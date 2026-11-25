"""Pure scheduling logic (no network) so it can be unit-tested.

Reels (9:16): at most one per slot -> max 2 a day per account, 07:00 and 18:00 IST.
Main videos (16:9 or > 2 min, YouTube + Dailymotion only): own track. One that day -> 18:00;
two -> 14:00 (earlier package first) and 18:00.
Two videos at the same date+time on the same platform (any track) -> the later one is pushed to its next slot.
"""
from datetime import datetime, time, timedelta

from . import config

KIND_SLOTS = {"reel": config.REEL_SLOTS, "main": config.MAIN_SLOTS}


def slot_times(day, kind, prefer=False):
    ts = KIND_SLOTS[kind]
    if prefer and kind == "main":     # fill 18:00 first, 14:00 only for a second main video that day
        ts = sorted(ts, key=lambda t: t != config.MAIN_PREFERRED)
    return [datetime.combine(day, t, tzinfo=config.TZ) for t in ts]


def track(item, platform):
    """Which schedule a post follows: a reel's 16:9 YouTube version is a normal video -> main track."""
    if item["kind"] == "reel" and platform == "youtube" and item.get("video_wide"):
        return "main"
    return item["kind"]


def slot_key(dt, kind):
    """The standard slot a fixed-time post occupies, so the 2-reels/day (1 main/day) cap still holds.
    Reels: before 13:00 -> 07:00 slot, else 18:00. Main: before 16:00 -> 14:00 slot, else 18:00."""
    local = dt.astimezone(config.TZ)
    slots = slot_times(local.date(), kind)
    if len(slots) > 1:
        return slots[0] if local.hour < (13 if kind == "reel" else 16) else slots[1]
    return slots[0]


def is_valid_slot(dt, kind):
    local = dt.astimezone(config.TZ)
    return time(local.hour, local.minute) in KIND_SLOTS[kind] and local.second == 0


def next_free_slot(taken, after, kind, max_days=365):
    day = after.astimezone(config.TZ).date()
    for _ in range(max_days):
        for s in slot_times(day, kind, prefer=True):
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


def rec_track(rec, platform):
    p = rec.get(platform) or {}
    return p.get("track") or rec.get("kind")


def taken_slots(state, platform, kind, exclude=None):
    """Slots this track can't use on this platform: its own posts' slots, plus any other video on the
    same platform at that exact time, plus the hand-filled RESERVED_SLOTS."""
    out = reserved()
    for iid, rec in state.get("items", {}).items():
        if iid == exclude:
            continue
        p = rec.get(platform)
        if not (p and p.get("slot") and p.get("status") in ("queued", "scheduled", "published")):
            continue
        if rec_track(rec, platform) == kind:
            out.add(p.get("slot_key") or p["slot"])
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
    ordered = sorted(items, key=lambda i: (i["id"].split(":")[0], i["order"]))
    # pass 1: posts with a date+time given in the package keep that exact time; whoever sat there is bumped
    for item in ordered:
        rec = recs.setdefault(item["id"], {})
        rec.update({"name": item["name"], "kind": item["kind"]})
        if rec.get("skip"):
            continue
        for p in platforms_for(item):
            if p == "youtube" and not config.YT_ENABLED and not (rec.get(p) or {}).get("id"):
                rec[p] = {"status": "off", "track": track(item, p), "note": "YouTube off until API audit"}
                continue
            if (rec.get(p) or {}).get("status") == "off":
                continue
            want = item["text"][p].get("schedule")
            cur = rec.get(p) or {}
            if not want or cur.get("status") in ("published", "held"):
                continue
            wdt = datetime.fromisoformat(want)
            if cur.get("not_before") and wdt < datetime.fromisoformat(cur["not_before"]):
                continue   # moved off its fixed time (YouTube quota) -> keep the new slot
            if wdt < now - timedelta(minutes=config.MISSED_AFTER_MIN):
                if cur.get("fixed"):
                    cur.pop("fixed", None)
                    cur.pop("slot_key", None)
                    cur["slot"] = want  # let pass 2 treat it as missed
                    rec[p] = cur
                continue
            tk = track(item, p)
            key = slot_key(wdt, tk).isoformat()
            if cur.get("slot") == want and cur.get("fixed"):
                continue
            for oid, orec in recs.items():
                o = orec.get(p)
                if (oid != item["id"] and o and not o.get("fixed") and o.get("status") in ("queued", "scheduled")
                        and ((rec_track(orec, p) == tk and (o.get("slot_key") or o.get("slot")) == key)
                             or o.get("slot") == want)):
                    o["bump"] = True
            was_scheduled = cur.get("status") == "scheduled" and cur.get("id")
            cur.update({"slot": want, "slot_key": key, "fixed": True, "track": tk,
                        "status": "scheduled" if was_scheduled else "queued",
                        "needs_reschedule": bool(was_scheduled)})
            if cur.get("status") != "scheduled":
                cur.pop("error", None)
            rec[p] = cur
            notes.append(f"{item['name']} [{p}] -> {wdt:%a %d %b %H:%M} (fixed time from package)")
    for item in ordered:
        rec = recs.setdefault(item["id"], {})
        rec.update({"name": item["name"], "kind": item["kind"]})
        if rec.get("skip"):
            continue
        for p in platforms_for(item):
            if p == "youtube" and not config.YT_ENABLED and not (rec.get(p) or {}).get("id"):
                rec[p] = {"status": "off", "track": track(item, p), "note": "YouTube off until API audit"}
                continue
            if (rec.get(p) or {}).get("status") == "off":
                continue
            kind = track(item, p)
            cur = rec.get(p) or {}
            cur["track"] = kind
            status = cur.get("status")
            if status in ("published", "held") or (cur.get("fixed") and status != "failed"):
                rec[p] = cur
                continue
            slot = datetime.fromisoformat(cur["slot"]) if cur.get("slot") else None
            reason = None
            if slot is None:
                reason = "new"
            elif cur.pop("bump", False):
                reason = "pushed by a fixed-time post"
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
            after = now + lead
            if cur.get("not_before"):
                after = max(after, datetime.fromisoformat(cur["not_before"]))
            new = next_free_slot(taken_slots(state, p, kind, exclude=item["id"]), after, kind)
            was_scheduled = status == "scheduled" and cur.get("id")
            cur.pop("slot_key", None)
            cur.pop("fixed", None)
            cur.update({"slot": new.isoformat(), "status": "scheduled" if was_scheduled else "queued",
                        "needs_reschedule": bool(was_scheduled)})
            if status == "failed":
                cur.pop("id", None)
            rec[p] = cur
            notes.append(f"{item['name']} [{p}] -> {new:%a %d %b %H:%M} ({reason})")
    notes += _tidy_main(state, ordered, now)
    return notes


def _movable(c, now, p):
    lead = timedelta(minutes=config.FB_MIN_LEAD_MIN + 5 if p == "facebook" else 15)
    return (c and not c.get("fixed") and c.get("status") in ("queued", "scheduled")
            and datetime.fromisoformat(c["slot"]) > now + lead)


def _set(c, dt):
    was = c.get("status") == "scheduled" and c.get("id")
    c.update({"slot": dt.isoformat(), "needs_reschedule": bool(was) or c.get("needs_reschedule", False)})


def _tidy_main(state, ordered, now):
    """Main track, per platform and day: a lone main video sits at 18:00; with two, the earlier
    package takes 14:00 and the later one 18:00."""
    notes, recs = [], state.get("items", {})
    rank = {it["id"]: n for n, it in enumerate(ordered)}
    plats = set(config.MAIN_PLATFORMS) | {"youtube"}
    for p in plats:
        days = {}
        for iid, rec in recs.items():
            c = rec.get(p)
            if rec.get("skip") or not c or not c.get("slot") or rec_track(rec, p) != "main":
                continue
            if c.get("status") not in ("queued", "scheduled", "published"):
                continue
            d = datetime.fromisoformat(c["slot"]).astimezone(config.TZ)
            days.setdefault(d.date(), []).append((iid, c))
        for day, lst in days.items():
            t14, t18 = slot_times(day, "main")
            if len(lst) == 1:
                iid, c = lst[0]
                if c["slot"] == t14.isoformat() and _movable(c, now, p) and \
                        t18.isoformat() not in taken_slots(state, p, "main", exclude=iid):
                    _set(c, t18)
                    notes.append(f"{recs[iid]['name']} [{p}] -> {t18:%a %d %b %H:%M} (only main video that day)")
            elif len(lst) == 2:
                (a, ca), (b, cb) = sorted(lst, key=lambda x: rank.get(x[0], 99))
                if ca["slot"] == t18.isoformat() and cb["slot"] == t14.isoformat() \
                        and _movable(ca, now, p) and _movable(cb, now, p):
                    _set(ca, t14)
                    _set(cb, t18)
                    notes.append(f"{recs[a]['name']} [{p}] -> {t14:%a %d %b %H:%M} (first main video that day)")
                    notes.append(f"{recs[b]['name']} [{p}] -> {t18:%a %d %b %H:%M} (second main video that day)")
    return notes


def defer(state, iid, platform, after, reason="quota"):
    """Move one post to the next free slot of its track after `after` (e.g. the YouTube quota reset).
    It keeps that slot: the planner won't pull it back before `not_before`."""
    cur = state["items"][iid][platform]
    kind = rec_track(state["items"][iid], platform)
    new = next_free_slot(taken_slots(state, platform, kind, exclude=iid), after, kind)
    for k in ("slot_key", "fixed", "error"):
        cur.pop(k, None)
    cur.update({"slot": new.isoformat(), "status": "queued", "not_before": after.isoformat(), "deferred": reason})
    return new


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
