"""Schedule audit (mode "audit"): REPORT ONLY, changes nothing.

Reads every post that is still to come (queued / scheduled / held) from state, plus Facebook's own
scheduled_publish_time for reels the bot already uploaded as natively SCHEDULED, and lists anything that
breaks the slot rules:
  reels only at 07:00 / 18:00 IST, main videos only at 14:00 / 18:00 IST (fixed times from a package are allowed),
  no two videos at the same time on one platform, max one post per track slot (2 reels / day),
  a lone main video sits at 18:00 (14:00 only when there is a second main video that day),
  hand-filled RESERVED_SLOTS stay free, nothing queued in the past (missed), Facebook time == state time.
YouTube posts are uploaded at slot time by this bot (no publishAt), so for YouTube the state is the schedule.
"""
from datetime import datetime, timedelta

from . import config, scheduler

LIVE = ("queued", "scheduled")


def _fmt(dt):
    return dt.astimezone(config.TZ).strftime("%a %d %b %H:%M")


def audit(state, now, fb_live=None):
    """Return a list of findings {platform, item, slot, problem}. fb_live = {video_id: aware datetime | None}."""
    fb_live = fb_live or {}
    out = []
    recs = state.get("items", {})
    reserved = scheduler.reserved()
    plats = sorted({p for rec in recs.values() for p, v in rec.items() if isinstance(v, dict)})
    for p in plats:
        by_slot, by_key, main_days = {}, {}, {}
        for iid, rec in recs.items():
            c = rec.get(p)
            if rec.get("skip") or not isinstance(c, dict):
                continue
            name = rec.get("name", iid)
            if c.get("status") == "held":
                out.append({"platform": p, "item": name, "slot": "-", "problem": "held by pre-upload check: "
                            + "; ".join(c.get("check_errors") or [])})
                continue
            if c.get("status") not in LIVE or not c.get("slot"):
                continue
            slot = datetime.fromisoformat(c["slot"])
            if slot < now - timedelta(minutes=config.MISSED_AFTER_MIN):
                if c.get("status") == "queued":
                    out.append({"platform": p, "item": name, "slot": _fmt(slot), "problem": "missed (still queued, slot passed)"})
                continue
            kind = scheduler.rec_track(rec, p)
            row = {"platform": p, "item": name, "slot": _fmt(slot)}
            if not c.get("fixed") and not scheduler.is_valid_slot(slot, kind):
                want = "07:00/18:00" if kind == "reel" else "14:00/18:00"
                out.append({**row, "problem": f"{kind} at a wrong time (allowed {want} IST)"})
            if c["slot"] in reserved and not c.get("fixed"):
                out.append({**row, "problem": "sits in a hand-filled RESERVED slot"})
            by_slot.setdefault(c["slot"], []).append(name)
            key = (kind, c.get("slot_key") or c["slot"])
            by_key.setdefault(key, []).append((name, c["slot"]))
            if kind == "main":
                main_days.setdefault(slot.astimezone(config.TZ).date(), []).append((name, slot))
            if p == "facebook" and c.get("status") == "scheduled" and c.get("id") in fb_live:
                live = fb_live[c["id"]]
                if live is None:
                    out.append({**row, "problem": "Facebook shows no scheduled time for this reel"})
                elif abs((live - slot).total_seconds()) > 60:
                    out.append({**row, "problem": f"Facebook has it at {_fmt(live)}, state says {_fmt(slot)}"})
        for s, names in by_slot.items():
            if len(names) > 1:
                out.append({"platform": p, "item": " + ".join(sorted(names)), "slot": _fmt(datetime.fromisoformat(s)),
                            "problem": "clash: two videos at the same time"})
        for (kind, k), lst in by_key.items():
            if len(lst) > 1 and len({s for _, s in lst}) > 1:   # same exact time is already a "clash"
                out.append({"platform": p, "item": " + ".join(sorted(n for n, _ in lst)), "slot": _fmt(datetime.fromisoformat(k)),
                            "problem": f"two {kind} posts in one {kind} slot (daily cap)"})
        for day, lst in main_days.items():
            if len(lst) == 1 and lst[0][1].astimezone(config.TZ).hour == 14:
                out.append({"platform": p, "item": lst[0][0], "slot": _fmt(lst[0][1]),
                            "problem": "only main video that day but at 14:00 (should be 18:00)"})
            if len(lst) > 2:
                out.append({"platform": p, "item": " + ".join(n for n, _ in lst), "slot": f"{day:%a %d %b}",
                            "problem": "more than 2 main videos on one day"})
    return out


def upcoming(state, now):
    return sum(1 for rec in state.get("items", {}).values() if not rec.get("skip")
               for c in rec.values() if isinstance(c, dict) and c.get("status") in LIVE + ("held",))


def report(findings, n, now):
    head = f"**Schedule audit — {now:%d %b %Y %H:%M} IST** (report only, nothing changed). {n} upcoming post(s) checked."
    if not findings:
        return head + "\n\nAll upcoming posts match the slot rules. ✅"
    rows = ["| platform | item | slot (IST) | problem |", "|---|---|---|---|"]
    rows += [f"| {f['platform']} | {f['item']} | {f['slot']} | {f['problem']} |" for f in findings]
    return head + f"\n\n{len(findings)} problem(s):\n\n" + "\n".join(rows)
