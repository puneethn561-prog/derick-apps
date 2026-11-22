"""Pure scheduling logic (no network) so it can be unit-tested.

Reels: each platform gets at most one reel per slot -> max 2 reels a day per account (07:30, 18:00 IST).
Main videos: one per day at MAIN_SLOT (default 20:00 IST).
"""
from datetime import datetime, time, timedelta

from . import config

KIND_SLOTS = {"reel": config.REEL_SLOTS, "main": config.MAIN_SLOTS}


def slot_times(day, kind):
    return [datetime.combine(day, t, tzinfo=config.TZ) for t in KIND_SLOTS[kind]]


def is_valid_slot(dt, kind):
    local = dt.astimezone(config.TZ)
    return time(local.hour, local.minute) in KIND_SLOTS[kind] and local.second == 0


def next_free_slot(taken, after, kind, max_days=365):
    day = after.astimezone(config.TZ).date()
    for _ in range(max_days):
        for s in slot_times(day, kind):
            if s > after and s.isoformat() not in taken:
                return s
        day += timedelta(days=1)
    raise RuntimeError("no free slot in range")


def reserved():
    out = set()
    from datetime import date
    for part in filter(None, (x.strip() for x in config.RESERVED_SLOTS.split(";"))):
        rng, _, hm = part.partition("@")
        a, _, b = rng.partition("..")
        d, end = date.fromisoformat(a), date.fromisoformat(b or a)
        h, m = map(int, hm.split(":"))
        while d <= end:
            out.add(datetime.combine(d, time(h, m), tzinfo=config.TZ).isoformat())
            d += timedelta(days=1)
    return out


def taken_slots(state, platform, kind, exclude=None):
    out = reserved() if kind == "reel" else set()
    for iid, rec in state.get("items", {}).items():
        if iid == exclude or rec.get("kind") != kind:
            continue
        p = rec.get(platform)
        if p and p.get("slot") and p.get("status") in ("queued", "scheduled", "published"):
            out.add(p["slot"])
    return out


def platforms_for(item):
    allowed = config.REEL_PLATFORMS if item["kind"] == "reel" else config.MAIN_PLATFORMS
    return [p for p in allowed if p in item["text"]]


def plan(state, items, now):
    """Assign / repair slots and return change notes.

    new item -> next free slot | slot not on 07:30/18:00 -> moved (time mistake)
    two items in one slot -> later moved | queued but missed -> moved | failed -> retried
    """
    notes = []
    recs = state.setdefault("items", {})
    for item in sorted(items, key=lambda i: (i["id"].split(":")[0], i["order"])):
        rec = recs.setdefault(item["id"], {})
        rec.update({"name": item["name"], "kind": item["kind"]})
        if rec.get("skip"):
            continue
        kind = item["kind"]
        for p in platforms_for(item):
            cur = rec.get(p) or {}
            status = cur.get("status")
            if status == "published":
                continue
            slot = datetime.fromisoformat(cur["slot"]) if cur.get("slot") else None
            reason = None
            if slot is None:
                reason = "new"
            elif not is_valid_slot(slot, kind):
                reason = f"wrong time {slot.astimezone(config.TZ):%d %b %H:%M}"
            elif cur["slot"] in taken_slots(state, p, kind, exclude=item["id"]):
                reason = "slot clash"
            elif status == "queued" and slot < now - timedelta(minutes=config.MISSED_AFTER_MIN):
                reason = "missed"
            elif status == "failed":
                reason = "retry after failure"
            if not reason:
                continue
            lead = timedelta(minutes=config.FB_MIN_LEAD_MIN + 5 if p == "facebook" else 5)
            new = next_free_slot(taken_slots(state, p, kind, exclude=item["id"]), now + lead, kind)
            was_scheduled = status == "scheduled" and cur.get("id")
            cur.update({"slot": new.isoformat(), "status": "scheduled" if was_scheduled else "queued",
                        "needs_reschedule": bool(was_scheduled)})
            if status == "failed":
                cur.pop("id", None)
            rec[p] = cur
            notes.append(f"{item['name']} [{p}] -> {new:%a %d %b %H:%M} ({reason})")
    return notes


def baseline(state, items):
    """'Start fresh': mark everything currently in Drive as skipped so only new uploads get posted."""
    recs = state.setdefault("items", {})
    for item in items:
        recs.setdefault(item["id"], {"name": item["name"], "kind": item["kind"], "skip": True})
    state["baselined"] = True


def due(state, platform, now, before_min=25, late_min=60):
    out = []
    for iid, rec in state.get("items", {}).items():
        p = rec.get(platform)
        if p and p.get("status") == "queued":
            s = datetime.fromisoformat(p["slot"])
            if now - timedelta(minutes=late_min) <= s <= now + timedelta(minutes=before_min):
                out.append((s, iid))
    return sorted(out)
