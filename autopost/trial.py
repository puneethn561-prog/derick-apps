"""Trial run: walk every step for a package WITHOUT posting anything, and report mistakes.

Checks per item and platform: kind (reel / long video), the slot it would get, title / caption / tags
against each platform's limits, unresolved [PLACEHOLDER] links, cover / thumbnail present, and the video
file itself (downloads it, measures length, size and aspect with ffprobe).
"""
import json
import re
import subprocess
from datetime import timedelta

from . import config, scheduler

LIMITS = {  # chars
    "instagram": {"caption": 2200, "hashtags": 30},
    "facebook": {"caption": 63206, "title": 255},
    "youtube": {"title": 100, "caption": 5000, "tags_total": 500},
    "dailymotion": {"title": 255, "caption": 3000},
}


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration,size", "-of", "json", path],
                         capture_output=True, text=True, timeout=300).stdout
    j = json.loads(out or "{}")
    st = (j.get("streams") or [{}])[0]
    fm = j.get("format") or {}
    return {"w": int(st.get("width") or 0), "h": int(st.get("height") or 0),
            "sec": float(fm.get("duration") or 0), "mb": int(fm.get("size") or 0) / 1e6}


def check_text(platform, t, kind):
    errs, warns = [], []
    lim = LIMITS[platform]
    cap, title = t.get("caption") or "", t.get("title") or ""
    if not cap.strip():
        warns.append("caption/description empty")
    if len(cap) > lim.get("caption", 10 ** 9):
        errs.append(f"caption {len(cap)} chars > {lim['caption']}")
    if "title" in lim and len(title) > lim["title"]:
        errs.append(f"title {len(title)} chars > {lim['title']}")
    if platform == "youtube" and re.search(r"[<>]", title + cap):
        errs.append("YouTube rejects < or > in title/description")
    if platform == "youtube" and len(",".join(t.get("tags") or [])) > lim["tags_total"]:
        errs.append("YouTube tags longer than 500 chars total")
    if platform == "instagram" and len(re.findall(r"#\w+", cap)) > lim["hashtags"]:
        errs.append(f"more than {lim['hashtags']} hashtags")
    left = re.findall(r"\[[A-Z_0-9]+\]", cap)
    if left:
        warns.append(f"placeholder not filled yet: {', '.join(sorted(set(left)))} (filled after main video posts)")
    if not t.get("pinned") and platform != "dailymotion":
        warns.append("no pinned comment text")
    return errs, warns


def check_video(platform, kind, v, wide):
    errs, warns = [], []
    if not v["sec"]:
        return ["video unreadable (ffprobe found no duration)"], warns
    portrait = v["h"] > v["w"]
    if kind == "reel":
        if platform in ("instagram", "facebook", "dailymotion") and not portrait:
            warns.append(f"reel is {v['w']}x{v['h']}, not vertical 9:16")
        if platform == "facebook" and not 3 <= v["sec"] <= 90:
            errs.append(f"Facebook reels must be 3-90 s (this is {v['sec']:.0f} s)")
        if platform == "instagram" and not 3 <= v["sec"] <= 900:
            errs.append(f"Instagram reels must be 3 s-15 min (this is {v['sec']:.0f} s)")
        if platform == "youtube" and not wide and v["sec"] > 180:
            errs.append(f"YouTube Shorts max 3 min (this is {v['sec']:.0f} s)")
        if min(v["w"], v["h"]) < 540:
            warns.append(f"low resolution {v['w']}x{v['h']}")
    else:
        if portrait:
            warns.append(f"long video is vertical {v['w']}x{v['h']}")
    if platform == "dailymotion" and v["mb"] > 4000:
        errs.append("Dailymotion max file size 4 GB")
    return errs, warns


def run(drive, now):
    items = drive.items()
    print(f"TRIAL (nothing is posted). Found {len(items)} item(s).")
    if not items:
        print("ERROR no package found: folder needs 00_Text_<Platform>_*.md files plus videos named like 03_Reel1_13Nov_9x16_1080.mp4")
        return False
    state = {"baselined": True, "items": {}}
    notes = scheduler.plan(state, items, now)
    probes, total_err = {}, 0
    for it in items:
        rec = state["items"].get(it["id"], {})
        plats = scheduler.platforms_for(it)
        print(f"\n=== {it['name']}  [{'LONG video' if it['kind'] == 'main' else 'reel'}]  file: {it['video']['name']}"
              + (f"  + 16x9: {it['video_wide']['name']}" if it.get("video_wide") else ""))
        missing = [p for p in (config.MAIN_PLATFORMS if it["kind"] == "main" else config.REEL_PLATFORMS) if p not in it["text"]]
        if missing:
            print(f"  WARN no text section for: {', '.join(missing)} -> not posted there")
        for f in [it["video"]] + ([it["video_wide"]] if it.get("video_wide") else []):
            if f["id"] not in probes:
                try:
                    probes[f["id"]] = probe(drive.download(f))
                except Exception as e:
                    probes[f["id"]] = {"err": str(e)[:200]}
        for p in plats:
            t = it["text"][p]
            slot = (rec.get(p) or {}).get("slot")
            wide = it.get("video_wide") if p == "youtube" else None
            v = probes[(wide or it["video"])["id"]]
            errs, warns = check_text(p, t, it["kind"])
            if "err" in v:
                errs.append("video download failed: " + v["err"])
            else:
                e2, w2 = check_video(p, it["kind"], v, wide)
                errs += e2
                warns += w2
            if p == "facebook" and it["kind"] == "reel" and p not in it["covers"]:
                warns.append("no cover image for Facebook")
            if p == "instagram":
                off = t.get("thumb_offset_ms") or (it["text"].get("youtube") or {}).get("thumb_offset_ms")
                warns.append("Instagram API can't take a cover image file; "
                             + (f"cover = video frame at {off / 1000:.1f}s" if off else "cover = first frame"))
            if p == "dailymotion" and it["covers"].get("dailymotion"):
                warns.append("Dailymotion thumbnail image not set by bot (auto frame)")
            st = (t.get("settings") or "").lower()
            if config.AI_LABEL and ("not needed" in st and ("ai label" in st or "synthetic" in st)):
                warns.append("kit says no AI label, but your rule is AI label ON -> bot will set it ON")
            if p == "youtube" and it["kind"] == "main" and not it["thumbs"]:
                warns.append("no thumbnail")
            if p == "youtube" and it["kind"] == "reel" and not wide:
                note = "as Short"
            elif p == "youtube":
                note = "as normal video"
            else:
                note = ""
            vinfo = "" if "err" in v else f"{v['w']}x{v['h']} {v['sec']:.0f}s {v['mb']:.0f}MB"
            print(f"  {p:<11} {('at ' + slot[:16].replace('T', ' ')) if slot else 'NO SLOT'} {note}  | {vinfo} | title: {t['title'][:60]!r}")
            for e in errs:
                print(f"     ERROR {e}")
            for w in warns:
                print(f"     warn  {w}")
            total_err += len(errs)
    for n in notes:
        print("plan: " + n)
    print(f"\nTRIAL DONE: {total_err} error(s).")
    return total_err == 0
