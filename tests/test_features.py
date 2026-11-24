"""Tests for quota guard, defer, pre-upload checks, audit, alerts, weekly report and preview.
No network: every HTTP call is replaced by a fake."""
import os, sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from autopost import alerts, audit, config, gh, precheck, preview, quota, scheduler, weekly

TZ = config.TZ
PLATS = ("instagram", "facebook", "dailymotion", "youtube")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(config, "RESERVED_SLOTS", "")
    monkeypatch.setattr(config, "YT_DAILY_QUOTA", 10000)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "")
    alerts.reset()
    yield
    alerts.reset()


def _iso(*a):
    return datetime(*a, tzinfo=TZ).isoformat()


def _video(sec=30, w=1080, h=1920, size="10000000", name="v.mp4"):
    return {"id": name, "name": name, "size": size,
            "videoMediaMetadata": {"durationMillis": str(int(sec * 1000)), "width": w, "height": h}}


def _item(key="R1", order=1, kind="reel", video=None, text=None):
    return {"id": f"P{order}:{key}", "name": key, "key": key, "kind": kind, "order": order,
            "video": video or _video(), "video_wide": None, "covers": {}, "thumbs": [],
            "text": text or {p: {"title": f"{key} {p} title", "caption": "hello #a", "tags": ["x"]} for p in PLATS}}


# ---------------------------------------------------------------- quota
def test_quota_tracking_and_reset():
    st = {}
    now = datetime(2026, 11, 24, 10, 0, tzinfo=TZ)          # = 23 Nov 20:30 Pacific
    assert quota.pt_day(now) == "2026-11-23"
    assert quota.left(st, now) == 10000
    for _ in range(6):
        quota.charge(st, now, "videos.insert")
    assert quota.used(st, now) == 9600
    assert quota.fits(st, now, "thumbnails.set") and not quota.fits(st, now, "videos.insert")
    assert not quota.fits(st, now, "videos.insert", "thumbnails.set")
    quota.charge(st, now, "list", n=3)
    assert quota.used(st, now) == 9603
    reset = quota.next_reset(now)
    assert reset.astimezone(TZ) == datetime(2026, 11, 24, 13, 30, tzinfo=TZ)   # winter: 13:30 IST
    after = reset + timedelta(minutes=1)
    assert quota.used(st, after) == 0 and st["yt_quota"]["day"] == "2026-11-24"
    quota.exhaust(st, after)
    assert quota.left(st, after) == 0


def test_quota_error_detection_and_summer_reset():
    assert quota.is_quota_error('{"error":{"errors":[{"reason":"quotaExceeded"}]}}')
    assert quota.is_quota_error("dailyLimitExceeded")
    assert not quota.is_quota_error("backendError")
    summer = datetime(2026, 7, 1, 10, 0, tzinfo=TZ)
    assert quota.next_reset(summer).astimezone(TZ).strftime("%H:%M") == "12:30"


# ---------------------------------------------------------------- scheduler.defer / not_before
def _yt_state(slot, kind="main"):
    return {"baselined": True, "items": {"P1:M1": {"name": "M1", "kind": kind, "youtube": {
        "slot": slot, "status": "queued", "track": kind, "fixed": True, "error": "x"}}}}


def test_defer_moves_to_next_free_slot_after_reset(monkeypatch):
    monkeypatch.setattr(config, "MAIN_PLATFORMS", ["youtube"])
    st = _yt_state(_iso(2026, 12, 2, 14, 0))
    reset = datetime(2026, 12, 2, 13, 30, tzinfo=TZ)
    new = scheduler.defer(st, "P1:M1", "youtube", reset)
    cur = st["items"]["P1:M1"]["youtube"]
    assert new == datetime(2026, 12, 2, 18, 0, tzinfo=TZ)         # lone main prefers 18:00
    assert cur["status"] == "queued" and cur["deferred"] == "quota"
    assert cur["not_before"] == reset.isoformat()
    assert "fixed" not in cur and "error" not in cur


def test_defer_skips_taken_slot(monkeypatch):
    st = _yt_state(_iso(2026, 12, 2, 14, 0))
    st["items"]["P2:M2"] = {"name": "M2", "kind": "main", "youtube": {
        "slot": _iso(2026, 12, 2, 18, 0), "status": "queued", "track": "main"}}
    new = scheduler.defer(st, "P1:M1", "youtube", datetime(2026, 12, 2, 13, 30, tzinfo=TZ))
    assert new == datetime(2026, 12, 2, 14, 0, tzinfo=TZ)          # 18:00 taken -> 14:00 same day


def test_planner_respects_not_before(monkeypatch):
    monkeypatch.setattr(config, "MAIN_PLATFORMS", ["youtube"])
    monkeypatch.setattr(config, "REEL_PLATFORMS", ["youtube"])
    early = _iso(2026, 12, 2, 11, 0)                      # package asks for a fixed 11:00 post
    it = _item("M1", 1, "main", video=_video(600, 1920, 1080),
               text={"youtube": {"title": "t", "caption": "c", "tags": [], "schedule": early}})
    st = {"baselined": True, "items": {}}
    now = datetime(2026, 12, 2, 9, 0, tzinfo=TZ)
    scheduler.plan(st, [it], now)
    assert st["items"]["P1:M1"]["youtube"]["slot"] == early
    reset = datetime(2026, 12, 2, 13, 30, tzinfo=TZ)
    new = scheduler.defer(st, "P1:M1", "youtube", reset)
    scheduler.plan(st, [it], now + timedelta(minutes=30))
    cur = st["items"]["P1:M1"]["youtube"]
    assert cur["slot"] == new.isoformat() and datetime.fromisoformat(cur["slot"]) >= reset
    assert not cur.get("fixed")


# ---------------------------------------------------------------- precheck
def test_precheck_hold_and_release(monkeypatch):
    monkeypatch.setattr(config, "REEL_PLATFORMS", ["facebook", "instagram"])
    bad = _item("R1", 1, "reel", video=_video(sec=120))          # FB reels max 90 s
    st = {"baselined": True, "items": {}}
    notes, errs, warns = precheck.apply(st, [bad])
    fb = st["items"]["P1:R1"]["facebook"]
    assert fb["status"] == "held" and "3-90 s" in fb["check_errors"][0]
    assert any("HELD" in n for n in notes)
    assert ("R1", "facebook", fb["check_errors"][0]) in errs
    assert "instagram" not in st["items"]["P1:R1"]                # 120 s is fine on Instagram
    assert precheck.any_held(st)
    # the held post gets no slot from the planner
    scheduler.plan(st, [bad], datetime(2026, 12, 2, 9, 0, tzinfo=TZ))
    assert st["items"]["P1:R1"]["facebook"]["status"] == "held" and "slot" not in st["items"]["P1:R1"]["facebook"]
    # fixed in Drive -> released, then planned
    good = _item("R1", 1, "reel", video=_video(sec=30))
    notes, errs, _ = precheck.apply(st, [good])
    assert not errs and any("released" in n for n in notes)
    assert "status" not in st["items"]["P1:R1"]["facebook"] and not precheck.any_held(st)
    scheduler.plan(st, [good], datetime(2026, 12, 2, 9, 0, tzinfo=TZ))
    assert st["items"]["P1:R1"]["facebook"]["status"] == "queued" and st["items"]["P1:R1"]["facebook"]["slot"]


def test_precheck_rules():
    it = _item("R1", 1, "reel", video=_video(sec=30, w=1920, h=1080))
    e, w = precheck.check(it, "facebook")
    assert any("9:16" in x for x in e)                           # horizontal reel is an error on FB
    e, w = precheck.check(it, "instagram")
    assert not e and any("not vertical" in x for x in w)          # ...only a warning elsewhere
    it["text"]["youtube"]["caption"] = "a <b> c"
    e, _ = precheck.check(it, "youtube")
    assert any("< or >" in x for x in e)
    it["text"]["instagram"]["caption"] = " ".join(f"#t{i}" for i in range(31))
    e, _ = precheck.check(it, "instagram")
    assert any("31 hashtags" in x for x in e)
    e, _ = precheck.check(_item(video=_video(size="0")), "instagram")
    assert any("empty" in x for x in e)
    e, w = precheck.check(_item(video={"id": "v", "name": "v.mp4"}), "instagram")
    assert not e and any("length not known" in x for x in w)


# ---------------------------------------------------------------- audit
def test_audit_findings():
    now = datetime(2026, 12, 2, 9, 0, tzinfo=TZ)
    ig = lambda slot, status="queued", **k: {"slot": slot, "status": status, "track": "reel", **k}
    st = {"items": {
        "a": {"name": "WrongTime", "kind": "reel", "instagram": ig(_iso(2026, 12, 3, 10, 0))},
        "b": {"name": "ClashA", "kind": "reel", "instagram": ig(_iso(2026, 12, 4, 7, 0))},
        "c": {"name": "ClashB", "kind": "reel", "instagram": ig(_iso(2026, 12, 4, 7, 0))},
        "d": {"name": "Missed", "kind": "reel", "instagram": ig(_iso(2026, 12, 1, 18, 0))},
        "e": {"name": "Held", "kind": "reel", "instagram": {"status": "held", "check_errors": ["too long"]}},
        "f": {"name": "LoneMain", "kind": "main", "youtube": {"slot": _iso(2026, 12, 5, 14, 0), "status": "queued", "track": "main"}},
        "g": {"name": "FbOff", "kind": "reel", "facebook": ig(_iso(2026, 12, 6, 18, 0), "scheduled", id="v1")},
        "h": {"name": "Fixed", "kind": "reel", "instagram": ig(_iso(2026, 12, 7, 10, 15), fixed=True)},
        "i": {"name": "Skipped", "kind": "reel", "skip": True, "instagram": ig(_iso(2026, 12, 3, 9, 0))},
    }}
    fb_live = {"v1": datetime(2026, 12, 6, 19, 0, tzinfo=TZ)}
    f = audit.audit(st, now, fb_live)
    probs = {(x["item"], x["problem"].split(" (")[0].split(":")[0]) for x in f}
    assert ("WrongTime", "reel at a wrong time") in probs
    assert ("ClashA + ClashB", "clash") in probs
    assert ("Missed", "missed") in probs
    assert ("Held", "held by pre-upload check") in probs
    assert ("LoneMain", "only main video that day but at 14") in probs
    assert any(x["item"] == "FbOff" and "Facebook has it at" in x["problem"] for x in f)
    assert not any(x["item"] in ("Fixed", "Skipped") for x in f)
    assert audit.upcoming(st, now) == 8
    rep = audit.report(f, 8, now)
    assert "report only" in rep and f"{len(f)} problem(s)" in rep
    assert "All upcoming posts match" in audit.report([], 0, now)


def test_audit_reserved_and_fb_missing(monkeypatch):
    monkeypatch.setattr(config, "RESERVED_SLOTS", "2026-12-03@18:00")
    now = datetime(2026, 12, 2, 9, 0, tzinfo=TZ)
    st = {"items": {"a": {"name": "R", "kind": "reel", "instagram": {"slot": _iso(2026, 12, 3, 18, 0), "status": "queued"},
                          "facebook": {"slot": _iso(2026, 12, 4, 7, 0), "status": "scheduled", "id": "v9"}}}}
    f = audit.audit(st, now, {"v9": None})
    assert any("RESERVED" in x["problem"] for x in f)
    assert any("no scheduled time" in x["problem"] for x in f)


# ---------------------------------------------------------------- alerts
@pytest.mark.parametrize("platform,error,expect", [
    ("youtube", 'YouTube upload 403: {"reason": "quotaExceeded"}', "quota"),
    ("youtube", "YouTube auth 400: invalid_grant", "YT_REFRESH_TOKEN"),
    ("facebook", 'Meta API 400: {"error":{"code": 190, "message":"x"}}', "code 190"),
    ("instagram", "Error validating access token: Session has expired", "IG_TOKEN"),
    ("dailymotion", "Dailymotion auth 400: invalid_client", "DM_API_KEY"),
    ("dailymotion", "Dailymotion create 401: nope", "Permission"),
    ("facebook", "Meta API 500: boom", ""),
])
def test_alert_fix_hints(platform, error, expect):
    h = alerts.hint(platform, error)
    assert (expect in h) if expect else h == ""


def test_alert_record_body_and_flush(monkeypatch):
    st = {"alerts_log": [{"time": "2020-01-01T00:00+05:30", "platform": "x", "item": "old", "what": "w",
                          "error": "e", "hint": ""}]}
    a = alerts.record("youtube", "EP2 Main", "upload", "quotaExceeded | x", st)
    assert a["hint"] and alerts.pending() == [a]
    assert [x["item"] for x in st["alerts_log"]] == ["EP2 Main"]       # >14 days dropped
    assert "quotaExceeded / x" in alerts.body([a])                     # pipe escaped for the table
    calls = []
    monkeypatch.setattr(gh, "upsert", lambda t, b, l: calls.append((t, b, l)))
    assert alerts.flush(st) == "alerted"
    assert calls[0][0].startswith("Autopost alert — ") and calls[0][2] == "autopost-alert"
    alerts.reset()
    assert alerts.flush(st) == "kept"                                  # gh disabled -> nothing closed
    monkeypatch.setattr(gh, "enabled", lambda: True)
    monkeypatch.setattr(gh, "open_issues", lambda label: [{"number": 7}])
    closed = []
    monkeypatch.setattr(gh, "close", lambda n, body=None: closed.append(n))
    assert alerts.flush(st, still_held=True) == "kept"
    assert alerts.flush(st) == "closed 1" and closed == [7]


# ---------------------------------------------------------------- weekly
class _Resp:
    def __init__(self, j, code=200):
        self._j, self.status_code, self.text = j, code, "x"

    def json(self):
        return self._j


def test_weekly_collect_and_views(monkeypatch):
    now = datetime(2026, 11, 29, 20, 0, tzinfo=TZ)
    st = {"items": {
        "a": {"name": "Reel1", "youtube": {"status": "published", "slot": _iso(2026, 11, 27, 7, 0),
                                           "published_at": _iso(2026, 11, 27, 7, 1), "id": "yt1"},
              "dailymotion": {"status": "published", "slot": _iso(2026, 11, 27, 7, 0), "id": "dm1"},
              "instagram": {"status": "failed", "error": "boom"}},
        "b": {"name": "Old", "youtube": {"status": "published", "slot": _iso(2026, 11, 10, 7, 0), "id": "yt0"}},
        "c": {"name": "Held", "facebook": {"status": "held", "check_errors": ["too long"]}},
        "d": {"name": "Skip", "skip": True, "youtube": {"status": "published", "slot": _iso(2026, 11, 28, 7, 0)}},
    }, "alerts_log": [{"time": _iso(2026, 11, 28, 18, 0), "platform": "instagram", "item": "Reel1",
                       "what": "upload", "error": "boom", "hint": ""},
                      {"time": _iso(2026, 11, 1, 18, 0), "platform": "x", "item": "old", "what": "u", "error": "e", "hint": ""}]}
    d = weekly.collect(st, now)
    assert [x["item"] for x in d["posted"]["youtube"]] == ["Reel1"]
    assert [x["id"] for x in d["posted"]["dailymotion"]] == ["dm1"]
    assert {(p["platform"], p["what"]) for p in d["problems"]} == {("instagram", "failed"), ("facebook", "held")}
    assert len(d["alerts"]) == 1

    import requests
    from autopost import youtube
    seen = []

    def fake_get(url, params=None, headers=None, **k):
        seen.append(url)
        if "googleapis.com/youtube" in url:
            assert params["id"] == "yt1"
            return _Resp({"items": [{"id": "yt1", "statistics": {"viewCount": "120"}}]})
        if "dailymotion" in url:
            return _Resp({"list": [{"id": "dm1", "views_total": 33}]})
        return _Resp({}, 500)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(youtube, "_auth", lambda: "tok")
    monkeypatch.setattr(config, "YT_REFRESH_TOKEN", "r")
    v = weekly.fetch_views(d["posted"], st)
    assert v == {"youtube": {"yt1": 120}, "dailymotion": {"dm1": 33}}
    assert st["yt_quota"]["used"] == 1                                 # videos.list = 1 unit
    text = weekly.render(d, v)
    assert "2 post(s) went out" in text and "120 views" in text and "33 views" in text
    assert "### Instagram — 0 posted" in text and "### Still failed / held" in text


def test_weekly_views_failure_is_na(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp({}, 500))
    posted = {p: [] for p in weekly.PLATFORMS}
    posted["dailymotion"] = [{"item": "R", "when": datetime(2026, 11, 28, 7, tzinfo=TZ), "id": "dm1"}]
    v = weekly.fetch_views(posted)
    assert "dailymotion" not in v
    d = {"since": datetime(2026, 11, 22, tzinfo=TZ), "now": datetime(2026, 11, 29, tzinfo=TZ),
         "posted": posted, "problems": [], "alerts": []}
    assert "views n/a" in weekly.render(d, v)


# ---------------------------------------------------------------- preview
def test_preview_rows():
    now = datetime(2026, 12, 2, 9, 0, tzinfo=TZ)
    items = [_item("R1", 1, "reel")]
    st = {"items": {
        "P1:R1": {"name": "R1", "kind": "reel",
                  "instagram": {"slot": _iso(2026, 12, 3, 7, 0), "status": "queued", "track": "reel"},
                  "youtube": {"slot": _iso(2026, 12, 2, 18, 0), "status": "queued", "track": "reel", "deferred": "quota"},
                  "facebook": {"status": "held", "check_errors": ["too long"]},
                  "dailymotion": {"slot": _iso(2026, 12, 20, 7, 0), "status": "queued"}},
        "X": {"name": "NoItem", "kind": "main", "youtube": {"slot": _iso(2026, 12, 4, 14, 0), "status": "scheduled", "fixed": True}},
        "S": {"name": "Skip", "skip": True, "youtube": {"slot": _iso(2026, 12, 3, 18, 0), "status": "queued"}},
    }}
    r = preview.rows(st, items, now)
    assert [(x["date"], x["time_ist"], x["platform"]) for x in r] == [
        ("2026-12-02", "18:00", "youtube"), ("2026-12-03", "07:00", "instagram"),
        ("2026-12-04", "14:00", "youtube"), ("", "", "facebook")]
    assert r[0]["title"] == "R1 youtube title" and r[0]["note"] == "moved: YouTube quota"
    assert r[2]["title"] == "NoItem" and r[2]["note"] == "fixed time from package" and r[2]["kind"] == "main"
    assert r[3]["status"] == "held" and r[3]["note"] == "too long"


def test_preview_build_does_not_touch_state(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REEL_PLATFORMS", ["instagram", "facebook"])
    monkeypatch.setattr(config, "RESERVED_SLOTS", "2026-12-03@18:00")
    now = datetime(2026, 12, 2, 9, 0, tzinfo=TZ)
    st = {"baselined": True, "items": {}}
    data = preview.build(st, [_item("R1", 1, "reel")], now)
    assert st == {"baselined": True, "items": {}}                     # works on a copy
    assert {x["platform"] for x in data["rows"]} == {"instagram", "facebook"}
    assert all(x["time_ist"] in ("07:00", "18:00") for x in data["rows"])
    assert data["reserved"] == [{"date": "2026-12-03", "time_ist": "18:00", "platform": "all",
                                 "note": "filled by hand (RESERVED_SLOTS)"}]
    p = preview.write(data, str(tmp_path / "s" / "preview.json"))
    assert os.path.exists(p)
    assert preview.build({"items": {}}, [_item()], now)["planner_notes"][0].startswith("first run")
