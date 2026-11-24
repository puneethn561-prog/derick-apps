"""Failure alerts -> one GitHub issue per day, label "autopost-alert" (GitHub emails the repo owner).

Anything that fails during a run (upload, comment, thumbnail/cover, a token/login, a pre-upload hard
error) is recorded with record(). At the end of the run flush() adds one comment with every failure to
"Autopost alert — <date>" (opened if needed). A run with no failures, and no item held by a pre-upload
error, closes the open alert issues automatically.
"""
import re
from datetime import datetime, timedelta

from . import config, gh

LABEL = "autopost-alert"
_run = []          # failures recorded during this run


def reset():
    _run.clear()


def pending():
    return list(_run)


def hint(platform, error):
    """Plain-language cause + fix for the errors we know."""
    e = str(error)
    low = e.lower()
    if "quotaexceeded" in low or ("quota" in low and platform == "youtube"):
        return "YouTube daily API quota used up -> post moved to the next free slot after the quota resets (13:30 IST)."
    if platform == "youtube" and ("invalid_grant" in low or "youtube auth" in low):
        return ("YouTube login failed (refresh token expired or revoked: invalid_grant). Make a new YT_REFRESH_TOKEN "
                "in OAuth Playground and update the repo secret. Tokens of an app in 'Testing' expire after 7 days.")
    if platform in ("facebook", "instagram") and (
            '"code":190' in e.replace(" ", "") or "error validating access token" in low
            or "session has expired" in low or ("oauthexception" in low and "expired" in low)):
        return "Meta token expired or invalid (code 190). Make a new long-lived Page token and update FB_PAGE_TOKEN / IG_TOKEN."
    if platform == "dailymotion" and "dailymotion auth" in low:
        return "Dailymotion login failed. Check DM_API_KEY / DM_API_SECRET (Private API key) in repo secrets."
    if re.search(r"\b(401|403)\b", e):
        return "Permission / login problem on this platform."
    return ""


def record(platform, item, what, error, state=None):
    """what: upload | comment | thumbnail | cover | auth | check | quota ..."""
    a = {"time": datetime.now(config.TZ).isoformat(timespec="minutes"), "platform": platform,
         "item": item, "what": what, "error": str(error)[:400], "hint": hint(platform, error)}
    _run.append(a)
    if state is not None:   # rolling 14-day history for the weekly report
        log = state.setdefault("alerts_log", [])
        log.append(a)
        cut = (datetime.now(config.TZ) - timedelta(days=14)).isoformat()
        state["alerts_log"] = [x for x in log if x["time"] >= cut][-200:]
    print(f"ALERT {platform} {what} {item}: {str(error)[:200]}", flush=True)
    return a


def body(alerts):
    rows = ["| time (IST) | platform | item | step | error | what to do |", "|---|---|---|---|---|---|"]
    for a in alerts:
        err = a["error"].replace("|", "/").replace("\n", " ")[:250]
        rows.append(f"| {a['time'][11:16]} | {a['platform']} | {a['item']} | {a['what']} | `{err}` | {a['hint']} |")
    return "\n".join(rows)


def title_for(day):
    return f"Autopost alert — {day:%d %b %Y}"


def flush(state=None, still_held=False):
    """Open/update today's alert issue, or close open ones after a clean run."""
    if _run:
        gh.upsert(title_for(datetime.now(config.TZ)), body(_run), LABEL)
        return "alerted"
    if still_held or not gh.enabled():
        return "kept"
    closed = 0
    for i in gh.open_issues(LABEL):
        gh.close(i["number"], f"Clean run at {datetime.now(config.TZ):%d %b %H:%M} IST — closing automatically.")
        closed += 1
    return f"closed {closed}"
