"""Entry point.  python -m autopost <mode>

modes:
  plan     hourly: read Drive, pre-upload checks (hold bad posts), assign/repair slots, upload Facebook reels
           as natively SCHEDULED, post comments for videos that went live, keep the daily checklist issue.
  slot     06:45 / 13:45 / 17:45 IST: wait for the slot, publish queued Instagram / Dailymotion / YouTube items.
  baseline mark everything already in Drive as skipped (fresh start). Runs automatically the first time.
  check    read-only login check of every platform.     trial  walk a test folder, post nothing.
  audit    report-only check of every upcoming post against the slot rules (issue "autopost-schedule-audit").
  weekly   last 7 days summary as a GitHub issue (closed at once, so it just emails).
  preview  write state/preview.json: the next 7 days of planned posts (dry, posts nothing).
Failures of plan/slot runs go to the daily "Autopost alert" issue (label autopost-alert), closed after a clean run.
"""
import sys
import time as _time
import traceback
from datetime import datetime, timedelta

from . import alerts, config, quota, scheduler, state as st


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
    wide = item.get("video_wide") if platform == "youtube" else None
    path = drive.download(wide or item["video"])
    if platform == "instagram":
        return meta.ig_publish_reel(path, t["caption"], thumb_offset_ms=t.get("thumb_offset_ms") or (item["text"].get("youtube") or {}).get("thumb_offset_ms"))
    if platform == "facebook":
        vid = meta.fb_upload_reel(path, t["caption"], publish_at=slot, title=t.get("title"))
        cover = item["covers"].get("facebook")
        if cover:
            try:
                meta.fb_set_thumbnail(vid, drive.download(cover))
            except Exception as e:  # cover is nice-to-have: the reel stays up, but you get an alert
                alerts.record("facebook", item["name"], "cover", e, state)
        return vid
    if platform == "dailymotion":
        vid = dailymotion.publish(path, t["title"], t["caption"], t["tags"])
        thumb = item["covers"].get("dailymotion") or ((item.get("thumbs") or [None])[0] if item["kind"] == "main" else None)
        if thumb:   # no thumbnail in the kit -> skip silently (Dailymotion picks a frame)
            try:
                from .drive import probe
                img = drive.download(thumb)
                d = probe(img)
                dailymotion.set_thumbnail(vid, img, vertical=d["h"] > d["w"] > 0)
            except Exception as e:
                alerts.record("dailymotion", item["name"], "thumbnail", e, state)
        return vid
    if platform == "youtube":
        thumb = (item.get("thumbs") or [None])[0] if item["kind"] == "main" else item["covers"].get("youtube")
        t0 = now()
        ops = ["videos.insert"] + (["thumbnails.set"] if thumb else [])
        if not quota.fits(state, t0, *ops):
            raise quota.QuotaExceeded(f"YouTube quota guard: {quota.used(state, t0)}/{config.YT_DAILY_QUOTA} units used "
                                      f"today (Pacific), upload needs {sum(quota.COST[o] for o in ops)}")
        quota.charge(state, t0, "videos.insert")
        try:
            vid = youtube.upload(path, t["title"], t["caption"], t["tags"], short=item["kind"] == "reel" and not wide)
        except quota.QuotaExceeded:
            quota.exhaust(state, now())
            raise
        if thumb:
            try:
                quota.charge(state, now(), "thumbnails.set")
                youtube.set_thumbnail(vid, drive.download(thumb))
            except Exception as e:
                alerts.record("youtube", item["name"], "thumbnail", e, state)
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


def daily_auth_check(state):
    """Once a day (first plan run after 06:00 IST): read-only login check of every configured platform,
    so an expired token is reported before the next post fails."""
    t = now()
    if config.DRY_RUN or t.hour < 6 or state.get("auth_checked") == t.date().isoformat():
        return
    from . import check
    tests = [("facebook", check._fb, config.FB_PAGE_TOKEN), ("instagram", check._ig, config.IG_TOKEN),
             ("dailymotion", check._dm, config.DM_API_KEY), ("youtube", check._yt, config.YT_REFRESH_TOKEN)]
    for p, fn, configured in tests:
        if not configured:
            continue
        try:
            if p == "youtube":
                quota.charge(state, t, "list")
            fn()
        except Exception as e:
            alerts.record(p, "(login check)", "auth", e, state)
    state["auth_checked"] = t.date().isoformat()


def run_checks(state, items):
    """Pre-upload checks before planning. Hard errors hold the post (alert issue), warnings go to the daily
    checklist. Each problem is reported once, not every hour."""
    from . import precheck
    notes, errs, warns = precheck.apply(state, items)
    for n in notes:
        log("check: " + n)
    seen = set(state.get("check_seen", []))
    new_e = [x for x in errs if "E|" + "|".join(x) not in seen]
    new_w = [x for x in warns if "W|" + "|".join(x) not in seen]
    for name, p, e in new_e:
        alerts.record(p, name, "pre-upload check (post held)", e, state)
    if new_w:
        checklist(["**Pre-upload warnings** (post goes ahead):"] + [f"- [ ] **{p}** — {name}: {w}" for name, p, w in new_w])
    state["check_seen"] = sorted({"E|" + "|".join(x) for x in errs} | {"W|" + "|".join(x) for x in warns})


# ---------------------------------------------------------------- modes
def run_plan(drive, state):
    items = {i["id"]: i for i in drive.items()}
    if not state.get("baselined"):
        scheduler.baseline(state, items.values())
        log(f"fresh start: {len(items)} existing Drive items marked skip")
        return
    t = now()
    daily_auth_check(state)
    run_checks(state, list(items.values()))
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
            alerts.record("facebook", item["name"], "upload" if fb.get("status") != "scheduled" else "reschedule", e, state)
    st.save(state)
    run_slot(drive, state, only_fixed=True, items=items)
    post_due_comments(items, state)
    update_cleanup(state)


def update_cleanup(state):
    """state['cleanup']: Drive files/folders whose every post went out, with the last post time.
    A daily job (outside this repo, with the owner's Drive access) moves them to Trash 30 days later."""
    by_drive = {}
    for iid, rec in state.get("items", {}).items():
        did = iid.split("::")[0].split(":")[0]
        d = by_drive.setdefault(did, {"done": True, "last": None, "names": set()})
        d["names"].add(rec.get("name", "").split(" / ")[0])
        if rec.get("skip"):
            continue
        for p in config.REEL_PLATFORMS + config.MAIN_PLATFORMS:
            r = rec.get(p)
            if not isinstance(r, dict):
                continue
            if r.get("status") != "published":
                d["done"] = False
            elif r.get("slot") and (d["last"] is None or r["slot"] > d["last"]):
                d["last"] = r["slot"]
    state["cleanup"] = {did: {"name": ", ".join(sorted(d["names"])), "last_posted": d["last"],
                              "delete_after_days": config.DELETE_AFTER_DAYS}
                        for did, d in by_drive.items() if d["done"] and d["last"]}


def run_slot(drive, state, only_fixed=False, items=None):
    """Post what is due now. Standard slots run from the slot crons; fixed-time posts (any minute)
    are picked up by the hourly plan run, which runs at :50 and waits up to ~45 min for them."""
    items = items or {i["id"]: i for i in drive.items()}
    t = now()
    jobs = []
    for p in ("instagram", "dailymotion", "youtube"):
        for slot, iid in scheduler.due(state, p, t, before_min=45 if only_fixed else 25):
            if iid in items and (not only_fixed or state["items"][iid][p].get("fixed")):
                jobs.append((slot, p, iid))
    if not jobs:
        log("nothing due" + (" (fixed-time)" if only_fixed else ""))
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
        except quota.QuotaExceeded as e:
            new = scheduler.defer(state, iid, p, quota.next_reset(now()))
            log(f"{p} quota: {item['name']} moved to {new:%a %d %b %H:%M} ({e})")
            alerts.record(p, item["name"], f"quota -> moved to {new:%d %b %H:%M}", e, state)
        except Exception as e:
            cur["status"], cur["error"] = "failed", str(e)[:300]
            log(f"{p} FAILED {item['name']}: {e}")
            alerts.record(p, item["name"], "upload", e, state)
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
            if p == "youtube" and text and not config.DRY_RUN:
                if not quota.fits(state, t, "commentThreads.insert"):
                    log(f"youtube comment waits for quota: {item['name']}")
                    continue
                quota.charge(state, t, "commentThreads.insert")
            try:
                cur["comment_id"] = post_comment(p, cur["id"], text) or "none"
                if cur["comment_id"] not in ("none",):
                    todo.append(f"- [ ] **{p}** — {item['name']} → long-press the comment and Pin")
                log(f"{p} comment posted on {item['name']}")
            except Exception as e:
                log(f"{p} comment FAILED {item['name']}: {e}")
                alerts.record(p, item["name"], "comment", e, state)
    if todo:
        checklist(todo)


def checklist(lines):
    """Append to the daily checklist issue (comments to pin, pre-upload warnings)."""
    from . import gh
    try:
        gh.upsert(f"Daily checklist — {now():%d %b %Y}", "\n".join(lines), "pin")
    except Exception as e:
        log(f"checklist issue failed: {e}")


def run_audit(state):
    """Report only: nothing in state or on any platform is changed."""
    from . import audit, gh
    t = now()
    fb_live = {}
    if config.FB_PAGE_TOKEN:
        from . import meta
        for rec in state.get("items", {}).values():
            fb = rec.get("facebook")
            if isinstance(fb, dict) and fb.get("status") == "scheduled" and fb.get("id") and fb["id"] != "dry-run":
                try:
                    j = meta.fb_status(fb["id"])
                    ts = j.get("scheduled_publish_time")
                    if isinstance(ts, str) and not ts.isdigit():
                        fb_live[fb["id"]] = datetime.fromisoformat(ts.replace("+0000", "+00:00"))
                    elif ts:
                        fb_live[fb["id"]] = datetime.fromtimestamp(int(ts), config.TZ)
                    elif (j.get("status") or {}).get("video_status") != "ready" or not j.get("published"):
                        fb_live[fb["id"]] = None
                except Exception as e:
                    log(f"audit: Facebook status for {fb['id']} not readable: {e}")
    findings = audit.audit(state, t, fb_live)
    text = audit.report(findings, audit.upcoming(state, t), t)
    log("audit:\n" + text)
    if gh.enabled():
        title = "autopost-schedule-audit"
        i = gh.find_open(title, "autopost-audit")
        if i:
            gh.comment(i["number"], text)
            n = i["number"]
        else:
            intro = "Report-only checks of upcoming posts against the slot rules. Each audit run adds a comment.\n\n"
            n = gh.create(title, intro + text, ["autopost-audit"])["number"]
        log(f"audit posted to issue #{n}")
    return findings


def main(argv):
    mode = argv[1] if len(argv) > 1 else "plan"
    from .drive import Drive
    state = st.load()
    alerts.reset()
    if mode == "audit":
        run_audit(state)
        return 0
    if mode == "unpost":
        from . import unpost
        return 0 if unpost.run(state, log) else 1
    if mode == "weekly":
        from . import weekly
        weekly.run(state, now())
        st.save(state)
        return 0
    try:
        drive = Drive()
        if mode == "preview":
            from . import preview
            data = preview.build(state, drive.items(), now())
            path = preview.write(data)
            log(f"preview: {len(data['rows'])} planned post(s) in the next 7 days -> {path}")
            for r in data["rows"]:
                log(f"  {r['date']} {r['time_ist']} {r['platform']:<11} {r['kind']:<4} {r['status']:<9} {r['title'][:60]}")
            return 0
        if mode == "trial":
            from . import check, trial
            ok_login = check.run(drive)
            return 0 if (trial.run(drive, now()) and ok_login) else 1
        if mode == "check":
            from . import check
            return 0 if check.run(drive) else 1
        if mode == "baseline":
            scheduler.baseline(state, drive.items())
        elif mode == "slot":
            run_slot(drive, state)
        else:
            run_plan(drive, state)
    except Exception as e:
        traceback.print_exc()
        if mode in ("plan", "slot"):
            alerts.record("autopost", f"{mode} run", "crash", e, state)
            _flush(state)
        st.save(state)
        return 1
    if mode in ("plan", "slot"):
        _flush(state)
    st.save(state)
    return 0


def _flush(state):
    from . import precheck
    try:
        log("alerts: " + alerts.flush(state, still_held=precheck.any_held(state)))
    except Exception as e:
        log(f"alert issue failed: {e}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
