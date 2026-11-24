"""Schedule preview (mode "preview", dry: posts nothing, changes nothing in state.json).

Runs the real pre-upload checks + planner on a COPY of the state with the current Drive packages and
writes state/preview.json: every post planned in the next 7 days (date, time IST, platform, title,
kind reel/main, status) plus the hand-filled reserved slots in that window.
"""
import copy
import json
import os
from datetime import datetime, timedelta

from . import config, precheck, scheduler


def rows(state, items, now, days=7):
    by_id = {i["id"]: i for i in items}
    end = now + timedelta(days=days)
    out = []
    for iid, rec in state.get("items", {}).items():
        if rec.get("skip"):
            continue
        item = by_id.get(iid)
        for p, c in rec.items():
            if not isinstance(c, dict):
                continue
            title = ((item or {}).get("text", {}).get(p) or {}).get("title") or rec.get("name", iid)
            base = {"platform": p, "title": title, "item": rec.get("name", iid),
                    "kind": scheduler.rec_track(rec, p), "status": c.get("status") or "planned"}
            if c.get("status") == "held":
                out.append({"date": "", "time_ist": "", **base, "note": "; ".join(c.get("check_errors") or [])})
                continue
            if not c.get("slot"):
                continue
            s = datetime.fromisoformat(c["slot"]).astimezone(config.TZ)
            if not (now - timedelta(minutes=config.MISSED_AFTER_MIN) <= s <= end):
                continue
            note = "fixed time from package" if c.get("fixed") else (
                "moved: YouTube quota" if c.get("deferred") == "quota" else "")
            out.append({"date": s.strftime("%Y-%m-%d"), "time_ist": s.strftime("%H:%M"), **base, "note": note})
    out.sort(key=lambda r: (r["date"] or "9999", r["time_ist"], r["platform"]))
    return out


def reserved_rows(now, days=7):
    end = now + timedelta(days=days)
    out = []
    for iso in sorted(scheduler.reserved()):
        s = datetime.fromisoformat(iso).astimezone(config.TZ)
        if now <= s <= end:
            out.append({"date": s.strftime("%Y-%m-%d"), "time_ist": s.strftime("%H:%M"),
                        "platform": "all", "note": "filled by hand (RESERVED_SLOTS)"})
    return out


def build(state, items, now, days=7):
    sim = copy.deepcopy(state)
    notes = []
    if not sim.get("baselined"):
        scheduler.baseline(sim, items)
        notes.append("first run would mark everything currently in Drive as skip (fresh start)")
    n, _, _ = precheck.apply(sim, items)
    notes += n + scheduler.plan(sim, items, now)
    return {"generated_at": now.isoformat(timespec="minutes"), "timezone": str(config.TZ), "days": days,
            "rows": rows(sim, items, now, days), "reserved": reserved_rows(now, days), "planner_notes": notes}


def write(data, path=None):
    path = path or config.PREVIEW_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    return path
