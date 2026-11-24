"""YouTube Data API quota guard (pure, unit-tested).

Google resets the daily quota at midnight Pacific time (13:30 IST in winter, 12:30 IST in summer).
state["yt_quota"] = {"day": "<Pacific date>", "used": <estimated units>}. Costs are Google's published
unit costs: videos.insert 1600, thumbnails.set 50, commentThreads.insert 50, any list call 1.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import config

PT = ZoneInfo("America/Los_Angeles")
COST = {"videos.insert": 1600, "thumbnails.set": 50, "commentThreads.insert": 50, "list": 1}


class QuotaExceeded(RuntimeError):
    """Raised before an upload that would go over the daily limit, or when YouTube answers quotaExceeded."""


def pt_day(now):
    return now.astimezone(PT).date().isoformat()


def next_reset(now):
    """The next midnight Pacific, as an aware datetime."""
    d = now.astimezone(PT).date() + timedelta(days=1)
    return datetime.combine(d, time(0, 0), tzinfo=PT)


def _bucket(state, now):
    q = state.setdefault("yt_quota", {})
    if q.get("day") != pt_day(now):
        q.clear()
        q.update({"day": pt_day(now), "used": 0})
    return q


def used(state, now):
    return _bucket(state, now)["used"]


def left(state, now):
    return max(0, config.YT_DAILY_QUOTA - used(state, now))


def fits(state, now, *ops):
    return sum(COST[o] for o in ops) <= left(state, now)


def charge(state, now, op, n=1):
    q = _bucket(state, now)
    q["used"] += COST[op] * n
    return q["used"]


def exhaust(state, now):
    """YouTube said quotaExceeded: treat today's quota as gone."""
    _bucket(state, now)["used"] = max(used(state, now), config.YT_DAILY_QUOTA)


def is_quota_error(text):
    t = str(text)
    return "quotaExceeded" in t or "dailyLimitExceeded" in t
