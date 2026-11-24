"""Weekly report (mode "weekly", Sunday 20:00 IST): the last 7 days from state.

What went out per platform, failures (from the alert history + posts still failed/held), and views where
one cheap call gets them: YouTube videos.list statistics (1 quota unit for up to 50 videos), Dailymotion
public views_total, Instagram media insight `views`, Facebook Reel `blue_reels_play_count`. A view count
that can't be read shows "n/a" (never guessed). Posted as a GitHub issue that is closed straight away,
so it only lands in the owner's email.
"""
from datetime import datetime, timedelta

from . import config, gh

PLATFORMS = ("instagram", "facebook", "youtube", "dailymotion")


def collect(state, now, days=7):
    since = now - timedelta(days=days)
    posted = {p: [] for p in PLATFORMS}
    problems = []
    for iid, rec in state.get("items", {}).items():
        if rec.get("skip"):
            continue
        for p in PLATFORMS:
            c = rec.get(p)
            if not isinstance(c, dict):
                continue
            if c.get("status") == "published":
                when = datetime.fromisoformat(c.get("published_at") or c["slot"])
                if since <= when <= now:
                    posted[p].append({"item": rec.get("name", iid), "when": when, "id": c.get("id")})
            elif c.get("status") in ("failed", "held"):
                problems.append({"platform": p, "item": rec.get("name", iid), "what": c["status"],
                                 "error": c.get("error") or "; ".join(c.get("check_errors") or [])})
    for p in posted:
        posted[p].sort(key=lambda x: x["when"])
    alerts = [a for a in state.get("alerts_log", []) if a["time"] >= since.isoformat()]
    return {"since": since, "now": now, "posted": posted, "problems": problems, "alerts": alerts}


def fetch_views(posted, state=None):
    """{platform: {id: views}}; a platform whose call fails is left out (shown as n/a)."""
    from . import dailymotion, meta, quota, youtube
    out = {}
    ids = {p: [x["id"] for x in posted[p] if x.get("id") and x["id"] != "dry-run"] for p in posted}
    if ids["youtube"] and config.YT_REFRESH_TOKEN:
        try:
            out["youtube"] = youtube.stats(ids["youtube"])
            if state is not None:
                quota.charge(state, datetime.now(config.TZ), "list")
        except Exception as e:
            print(f"weekly: YouTube views n/a ({str(e)[:120]})")
    if ids["dailymotion"]:
        try:
            out["dailymotion"] = dailymotion.views(ids["dailymotion"])
        except Exception as e:
            print(f"weekly: Dailymotion views n/a ({str(e)[:120]})")
    for p, fn in (("instagram", meta.ig_views), ("facebook", meta.fb_reel_views)):
        got = {}
        for vid in ids[p]:
            try:
                got[vid] = fn(vid)
            except Exception as e:
                print(f"weekly: {p} views n/a for {vid} ({str(e)[:120]})")
        if got:
            out[p] = got
    return out


def render(data, views=None):
    views = views or {}
    s, n = data["since"], data["now"]
    total = sum(len(v) for v in data["posted"].values())
    lines = [f"**Autopost weekly report** — {s:%d %b} to {n:%d %b %Y} (IST)", "",
             f"{total} post(s) went out, {len(data['alerts'])} failure alert(s), "
             f"{len(data['problems'])} post(s) still failed/held.", ""]
    for p in PLATFORMS:
        lst = data["posted"][p]
        tv = [views.get(p, {}).get(x["id"]) for x in lst]
        known = [v for v in tv if v is not None]
        lines.append(f"### {p.capitalize()} — {len(lst)} posted"
                     + (f", {sum(known)} views" if known else ""))
        for x, v in zip(lst, tv):
            lines.append(f"- {x['when'].astimezone(config.TZ):%a %d %b %H:%M} — {x['item']} — "
                         + (f"{v} views" if v is not None else "views n/a"))
        if not lst:
            lines.append("- nothing")
        lines.append("")
    if data["alerts"]:
        lines.append("### Failures this week")
        for a in data["alerts"]:
            lines.append(f"- {a['time'][:16].replace('T', ' ')} — {a['platform']} {a['what']} — {a['item']}: "
                         f"`{a['error'][:160]}`")
        lines.append("")
    if data["problems"]:
        lines.append("### Still failed / held")
        for x in data["problems"]:
            lines.append(f"- {x['platform']} — {x['item']} — {x['what']}: {x['error'][:160]}")
    return "\n".join(lines).strip() + "\n"


def run(state, now):
    data = collect(state, now)
    text = render(data, fetch_views(data["posted"], state) if not config.DRY_RUN else {})
    print(text)
    if gh.enabled():
        i = gh.create(f"Weekly autopost report {now:%d %b %Y}", text, ["autopost-weekly"])
        gh.close(i["number"])
        print(f"weekly report issue #{i['number']} (closed)")
    return True
