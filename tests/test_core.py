import os, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from autopost import config, scheduler
from autopost.drive import parse_platform_md, build_items, fill_links

HERE = os.path.dirname(__file__)
TZ = config.TZ

def texts():
    return {p: parse_platform_md(open(f"{HERE}/fixture_{p}.md", encoding="utf-8").read(), p)
            for p in ("instagram", "facebook", "dailymotion")}

FILES = [{"id": n, "name": n, "size": "1"} for n in [
    "01_Teaser_11Nov_9x16_1080.mp4", "01_Teaser_11Nov_16x9_4K.mp4", "02_Main_12Nov_4K.mp4",
    "03_Reel1_13Nov_9x16_1080.mp4", "03_Reel1_13Nov_Cover_fb_9x16.jpg", "04_Reel2_14Nov_9x16_1080.mp4",
    "05_Reel3_15Nov_9x16_1080.mp4", "02_Main_12Nov_Thumb_A.jpg"]]

def test_parse():
    t = texts()
    for p in t:
        assert {"Teaser", "Main", "Reel1", "Reel6"} <= set(t[p]), (p, t[p].keys())
        assert t[p]["Reel1"]["pinned"] and t[p]["Reel1"]["caption"]
    assert t["dailymotion"]["Reel1"]["tags"][0] == "Chivari Bussu"

def test_items():
    items = build_items("pkg", "EP2", FILES, texts())
    kinds = {i["key"]: i["kind"] for i in items}
    assert kinds == {"Teaser": "reel", "Main": "main", "Reel1": "reel", "Reel2": "reel", "Reel3": "reel"}
    assert items[0]["video"]["name"].startswith("01_Teaser_11Nov_9x16")
    assert "facebook" in [i for i in items if i["key"] == "Reel1"][0]["covers"]

def test_fill_links():
    s = "a\n▶️ EP2: [YOUTUBE_LINK_EP2 - paste]\nb"
    assert fill_links(s, {}) == "a\nb"
    assert "http://x" in fill_links(s, {"YOUTUBE_LINK_EP2": "http://x"})

def test_plan_reserved_and_two_per_day():
    now = datetime(2026, 11, 22, 10, 30, tzinfo=TZ)
    items = build_items("pkg", "EP2", FILES, texts())
    state = {"baselined": True}
    scheduler.plan(state, items, now)
    for p in ("instagram", "facebook", "dailymotion"):
        reels = sorted(r[p]["slot"] for r in state["items"].values() if r["kind"] == "reel" and p in r)
        hours = [datetime.fromisoformat(s) for s in reels]
        # until 29 Nov: only 07:30. 4 reels -> 23,24,25,26 Nov 07:30
        assert [h.strftime("%d %H:%M") for h in hours] == ["23 07:30", "24 07:30", "25 07:30", "26 07:30"], (p, hours)
        assert len(set(reels)) == len(reels)
    main = [r for r in state["items"].values() if r["kind"] == "main"][0]
    assert set(main) >= {"dailymotion"} and "instagram" not in main
    assert datetime.fromisoformat(main["dailymotion"]["slot"]).strftime("%d %H:%M") == "22 20:00"

def test_after_29_two_slots():
    now = datetime(2026, 11, 30, 6, 0, tzinfo=TZ)
    items = build_items("pkg", "EP2", FILES, texts())
    state = {"baselined": True}
    scheduler.plan(state, items, now)
    slots = sorted(datetime.fromisoformat(r["instagram"]["slot"]).strftime("%d %H:%M")
                   for r in state["items"].values() if r["kind"] == "reel")
    assert slots == ["30 07:30", "30 18:00", "01 07:30", "01 18:00"] or slots == sorted(["30 07:30", "30 18:00", "01 07:30", "01 18:00"])

def test_fix_wrong_time_and_missed():
    now = datetime(2026, 12, 5, 9, 0, tzinfo=TZ)
    items = build_items("pkg", "EP2", FILES, texts())[:1]
    state = {"baselined": True, "items": {items[0]["id"]: {"kind": "reel",
             "instagram": {"slot": datetime(2026, 12, 6, 9, 15, tzinfo=TZ).isoformat(), "status": "queued"},
             "dailymotion": {"slot": datetime(2026, 12, 4, 18, 0, tzinfo=TZ).isoformat(), "status": "queued"}}}}
    notes = scheduler.plan(state, items, now)
    r = state["items"][items[0]["id"]]
    assert datetime.fromisoformat(r["instagram"]["slot"]).strftime("%d %H:%M") == "05 18:00"
    assert datetime.fromisoformat(r["dailymotion"]["slot"]).strftime("%d %H:%M") == "05 18:00"
    assert any("wrong time" in n for n in notes) and any("missed" in n for n in notes)

def test_baseline_skips_existing():
    items = build_items("pkg", "EP2", FILES, texts())
    state = {}
    scheduler.baseline(state, items)
    scheduler.plan(state, items, datetime(2026, 11, 22, 11, tzinfo=TZ))
    assert all("instagram" not in r for r in state["items"].values())
