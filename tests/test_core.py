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

def test_parse_when():
    from autopost.drive import parse_when
    assert parse_when("2026-11-25 18:00").startswith("2026-11-25T18:00")
    assert parse_when("25-11-2026 6:00 PM").startswith("2026-11-25T18:00")
    assert parse_when("25 Nov 2026 9:30 am").startswith("2026-11-25T09:30")
    assert parse_when("tomorrow") is None

def test_fixed_time_bumps_other_post():
    now = datetime(2026, 11, 22, 10, 30, tzinfo=TZ)
    t = texts()
    items = build_items("pkg", "EP2", FILES, t)
    state = {"items": {}, "baselined": True}
    scheduler.plan(state, items, now)
    r1 = [i for i in items if i["key"] == "Reel1"][0]
    r3 = [i for i in items if i["key"] == "Reel3"][0]
    target = state["items"][r3["id"]]["instagram"]["slot"]
    tdt = datetime.fromisoformat(target)
    holder = [iid for iid, r in state["items"].items() if r.get("instagram", {}).get("slot") == target]
    assert holder and holder[0] != r1["id"]
    # now Reel1 asks for 30 Nov 10:00 (falls in the morning slot) on instagram only
    r1["text"]["instagram"] = dict(r1["text"]["instagram"], schedule=tdt.replace(hour=10, minute=0).isoformat())
    notes = scheduler.plan(state, items, now)
    ig = state["items"][r1["id"]]["instagram"]
    assert ig["fixed"] and ig["slot"].startswith(tdt.strftime("%Y-%m-%dT10:00")) and ig["slot_key"] == target
    moved = state["items"][holder[0]]["instagram"]["slot"]
    assert moved != target
    # per-day cap: nobody else on instagram on the 30 Nov morning slot
    same = [iid for iid, r in state["items"].items()
            if (r.get("instagram", {}).get("slot_key") or r.get("instagram", {}).get("slot")) == target]
    assert same == [r1["id"]], notes


def test_reel_16x9_goes_to_youtube_variant():
    from autopost import drive
    files=[{"id":"a","name":"03_Reel1_13Nov_9x16_1080.mp4"},{"id":"b","name":"03_Reel1_13Nov_16x9_1080.mp4"},
           {"id":"c","name":"04_Reel2_14Nov_16x9_1080.mp4"},{"id":"d","name":"02_Main_12Nov_4K.mp4"}]
    items={i["key"]:i for i in drive.build_items("p","P",files,{})}
    assert items["Reel1"]["kind"]=="reel" and items["Reel1"]["video"]["id"]=="a" and items["Reel1"]["video_wide"]["id"]=="b"
    assert items["Reel2"]["kind"]=="reel" and items["Reel2"]["video_wide"]["id"]=="c"
    assert items["Main"]["kind"]=="main" and items["Main"]["video_wide"] is None


def test_long_video_over_2min_is_main():
    from autopost import drive
    files = [
        {"id": "a", "name": "05_Story_20Nov_9x16_1080.mp4", "videoMediaMetadata": {"durationMillis": "185000"}},
        {"id": "b", "name": "06_Clip_21Nov_9x16_1080.mp4", "videoMediaMetadata": {"durationMillis": "59000"}},
    ]
    items = {i["key"]: i for i in drive.build_items("p", "P", files, {})}
    assert items["Story"]["kind"] == "main"
    assert items["Clip"]["kind"] == "reel"


def test_zip_package(tmp_path, monkeypatch):
    import io, zipfile, subprocess
    from autopost import drive, config
    vid = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=150", "-y", str(vid)], check=True)
    short = tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=5", "-y", str(short)], check=True)
    md = "## Story — Dailymotion\n**Title:** Long one\n\n## Clip — Dailymotion\n**Title:** Short one\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("EP9/00_Text_Dailymotion_titles.md", md)
        z.write(vid, "EP9/05_Story_20Nov_9x16_1080.mp4")
        z.write(short, "EP9/06_Clip_21Nov_9x16_1080.mp4")
        z.writestr("__MACOSX/EP9/._x", "junk")
    data = buf.getvalue()
    monkeypatch.setattr(drive, "DriveRangeFile", lambda svc, fid, size: io.BytesIO(data))
    monkeypatch.setattr(drive, "DURATIONS_PATH", str(tmp_path / "durations.json"))
    monkeypatch.setattr(config, "WORKDIR", str(tmp_path / "work"))
    d = drive.Drive.__new__(drive.Drive); d.svc = None
    items = {i["key"]: i for i in d._zip_package({"id": "Z", "name": "EP9.zip", "size": len(data)})}
    assert items["Story"]["kind"] == "main" and items["Clip"]["kind"] == "reel"
    assert items["Story"]["text"]["dailymotion"]["title"] == "Long one"
    assert drive.load_durations()["Z::EP9/05_Story_20Nov_9x16_1080.mp4"] >= 149000
    p = d.download(items["Clip"]["video"])
    assert open(p, "rb").read() == short.read_bytes()
