"""Entry point.  python -m autopost <mode>

modes:
  plan     hourly: read Drive, assign/repair slots, upload Facebook reels as natively SCHEDULED,
           post comments for videos that went live, keep the daily checklist issue.
  slot     at 07:15 / 17:45 / 19:45 IST: wait for the slot, publish queued Instagram + Dailymotion items.
  baseline mark everything already in Drive as skipped (fresh start). Runs automatically the first time.
"""
import sys
import time as _time
import traceback
from datetime import datetime, timedelta

from . import config, scheduler, state as st


def now():
    return datetime.now(config.TZ)


def log(msg):
    print(f"[{now():%Y-%m-%d %H:%M}] {msg}", flush=True)


def links_from_state(state):
    """Main-video links (for [YOUTUBE_LINK_EPx] placeholders) once they exist."""
    return state.get("links", {})


def text_for(item, platform, state):
    from .drive import fill_links
    t = dict(item["text"][platform])
    t["caption"] = fill_links(t["caption"], links_from_state(state))
    return t


# ---------------------------------------------------------------- publishing
def publish(drive, item, platform, rec, state, slot=None):
    from . import dailymotion, meta, youtube
    t = text_for(item, platform, state)
    if config.DRY_RUN:
        log(f"DRY_RUN would post {item['name']} -> {platform} at {slot}")
        return "dry-run"
    path = drive.download(item["video"])
    if platform == "instagram":
        return meta.ig_publish_reel(path, t["caption"])
    if platform == "facebook":
        vid = meta.fb_upload_reel(path, t["caption"], publish_at=slot, title=t.get("title"))
        cover = item["covers"].get("facebook")
        if cover:
            try:
                meta.fb_set_thumbnail(vid, drive.download(cover))
            except Exception as e:  # cover is nice-to-have
                log(f"fb cover failed: {e}")
        return vid
    if platform == "dailymotion":
        return dailymotion.publish(path, t["title"], t["caption"], t["tags"])
    if platform == "youtube":
        vid = youtube.upload(path, t["title"], t["caption"], t["tags"], short=item["kind"] == "reel")
        thumb = (item.get("thumbs") or [None])[0] if item["kind"] == "main" else item["covers"].get("youtube")
        if thumb:
            try:
                youtube.set_thumbnail(vid, drive.download(thumb))
            except Exception as e:
                log(f"yt thumbnail failed: {e}")
        if item["kind"] == "main":
            state.setdefault("links", {})["YOUTUBE_LINK_" + item["name"].split(" / ")[0][:20]] = f"https://youtu.be/{vid}"
        return vid
    raise ValueError(platform)


def post_comment(platform, media_id, text):
    from . import meta
    if config.DRY_RUN or not text:
        return "dry-run" if text else None
    if platform == "instagram":
        return meta.ig_comment(media_id, text)
    if platform == "facebook":
        return meta.fb_comment(media_id, text)
    if platform == "youtube":
        from . import youtube
        return youtube.comment(media_id, text)
    return None  # Dailymotion API has no comment posting


# ---------------------------------------------------------------- modes
def run_plan(drive, state):
    items = {i["id"]: i for i in drive.items()}
    if not state.get("baselined"):
        scheduler.baseline(state, items.values())
        log(f"fresh start: {len(items)} existing Drive items marked skip")
        return
    t = now()
    for n in scheduler.plan(state, list(items.values()), t):
        log("plan: " + n)

    for iid, rec in state["items"].items():
        item = items.get(iid)
        if not item or rec.get("skip"):
            continue
        fb = rec.get("facebook")
        if not fb:
            continue
        slot = datetime.fromisoformat(fb["slot"])
        try:
            if fb["status"] == "queued" and slot - t <= timedelta(days=config.FB_MAX_LEAD_DAYS):
                fb["id"] = publish(drive, item, "facebook", rec, state, slot=slot)
                fb["status"] = "scheduled"
                log(f"facebook scheduled {item['name']} for {slot:%d %b %H:%M}")
            elif fb.get("needs_reschedule") and not config.DRY_RUN:
                from . import meta
                meta.fb_reschedule(fb["id"], slot)
                fb["needs_reschedule"] = False
                log(f"facebook rescheduled {item['name']} -> {slot:%d %b %H:%M}")
            elif fb["status"] == "scheduled" and slot <= t:
                fb["status"] = "published"
        except Exception as e:
            fb["status"], fb["error"] = "failed", str(e)[:300]
            log(f"facebook FAILED {item['name']}: {e}")
    post_due_comments(items, state)


def run_slot(drive, state):
    items = {i["id"]: i for i in drive.items()}
    t = now()
    jobs = []
    for p in ("instagram", "dailymotion", "youtube"):
        for slot, iid in scheduler.due(state, p, t):
            if iid in items:
                jobs.append((slot, p, iid))
    if not jobs:
        log("nothing due")
        return
    first = min(j[0] for j in jobs)
    wait = (first - now()).total_seconds()
    if wait > 0:
        log(f"waiting {int(wait)}s for {first:%H:%M}")
        _time.sleep(wait)
    for slot, p, iid in sorted(jobs):
        rec, item = state["items"][iid], items[iid]
        cur = rec[p]
        try:
            cur["id"] = publish(drive, item, p, rec, state)
            cur["status"], cur["published_at"] = "published", now().isoformat()
            log(f"{p} published {item['name']}")
            if item["kind"] == "main" and p == "dailymotion" and cur["id"] != "dry-run":
                key = "DAILYMOTION_LINK_" + item["name"].split(" / ")[0][:20]
                state.setdefault("links", {})[key] = f"https://www.dailymotion.com/video/{cur['id']}"
        except Exception as e:
            cur["status"], cur["error"] = "failed", str(e)[:300]
            log(f"{p} FAILED {item['name']}: {e}")
        st.save(state)
    post_due_comments(items, state)


def post_due_comments(items, state):
    """After a video is live: post its pinned/first comment and add it to today's pin checklist."""
    t = now()
    todo = []
    for iid, rec in state["items"].items():
        item = items.get(iid)
        if not item or rec.get("skip"):
            continue
        for p in ("instagram", "facebook", "youtube"):
            cur = rec.get(p)
            if not cur or cur.get("comment_id") or cur.get("status") not in ("published", "scheduled"):
                continue
            if datetime.fromisoformat(cur["slot"]) > t - timedelta(minutes=3):
                continue
            text = item["text"].get(p, {}).get("pinned")
            try:
                cur["comment_id"] = post_comment(p, cur["id"], text) or "none"
                if cur["comment_id"] not in ("none",):
                    todo.append(f"- [ ] **{p}** — {item['name']} → long-press the comment and Pin")
                log(f"{p} comment posted on {item['name']}")
            except Exception as e:
                log(f"{p} comment FAILED {item['name']}: {e}")
    if todo:
        checklist(todo)


def checklist(lines):
    """Append to a daily GitHub issue so you can pin the comments in 1 minute."""
    import requests
    if not (config.GITHUB_TOKEN and config.GITHUB_REPOSITORY) or config.DRY_RUN:
        log("checklist:\n" + "\n".join(lines))
        return
    api = f"https://api.github.com/repos/{config.GITHUB_REPOSITORY}/issues"
    h = {"Authorization": f"Bearer {config.GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    title = f"Pin comments — {now():%d %b %Y}"
    existing = [i for i in requests.get(api, headers=h, params={"state": "open", "labels": "pin"}).json()
                if i.get("title") == title]
    body = "\n".join(lines)
    if existing:
        requests.post(f"{api}/{existing[0]['number']}/comments", headers=h, json={"body": body})
    else:
        requests.post(api, headers=h, json={"title": title, "body": body, "labels": ["pin"]})


def main(argv):
    mode = argv[1] if len(argv) > 1 else "plan"
    from .drive import Drive
    state = st.load()
    try:
        drive = Drive()
        if mode == "baseline":
            scheduler.baseline(state, drive.items())
        elif mode == "slot":
            run_slot(drive, state)
        else:
            run_plan(drive, state)
    except Exception:
        traceback.print_exc()
        st.save(state)
        return 1
    st.save(state)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
