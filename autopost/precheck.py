"""Pre-upload checks, run by the hourly planner BEFORE slots are assigned (no download: uses the size,
length and width x height that Drive / the zip index / state/durations.json already give).

Hard error  -> that post is HELD (status "held", not scheduled or posted) and listed in the alert issue.
Warning     -> only listed in the daily checklist issue; the post goes ahead.
Once the package is fixed in Drive, the next run releases the hold and the planner gives it a slot.

Limits (official docs, Nov 2026):
  Instagram Reels (Graph API): 3 s - 15 min, <= 300 MB, caption <= 2200 chars, <= 30 hashtags
  Facebook Reels: 3 - 90 s, vertical 9:16
  YouTube: title <= 100, description <= 5000 (the bot cuts longer ones -> warning), tags <= 500 chars,
           no < or >; Shorts <= 3 min; hashtags: we keep <= 15 (YouTube ignores ALL of them above 60)
  Dailymotion: <= 4 GB, title <= 255, description <= 3000
"""
import re

from . import scheduler
from .drive import dims, duration_s

IG_MAX_MB, IG_MIN_S, IG_MAX_S = 300, 3, 900
FB_MIN_S, FB_MAX_S = 3, 90
SHORT_MAX_S = 180
DM_MAX_MB = 4000
YT_TITLE, YT_DESC, YT_TAGS, YT_HASHTAGS = 100, 5000, 500, 15
IG_CAPTION, IG_HASHTAGS = 2200, 30
DM_TITLE, DM_DESC = 255, 3000


def hashtags(text):
    return re.findall(r"(?<![\w&])#\w+", text or "")


def video_for(item, platform):
    return (item.get("video_wide") if platform == "youtube" else None) or item.get("video")


def meta(f):
    w, h = dims(f)
    try:
        mb = int(f.get("size") or 0) / 1e6
    except (TypeError, ValueError):
        mb = 0
    return {"mb": mb, "sec": duration_s(f), "w": w, "h": h}


def check(item, platform):
    """(errors, warnings) for one item on one platform."""
    errs, warns = [], []
    f = video_for(item, platform)
    t = item["text"].get(platform) or {}
    title, cap = t.get("title") or "", t.get("caption") or ""
    kind = scheduler.track(item, platform)
    if not f:
        return ["video file missing"], warns
    m = meta(f)
    if f.get("size") is not None and m["mb"] == 0:
        errs.append(f"video file {f.get('name')} is empty (0 bytes)")
    sec, mb = m["sec"], m["mb"]
    # size / length
    if platform == "instagram":
        if mb > IG_MAX_MB:
            errs.append(f"Instagram reels max {IG_MAX_MB} MB (file is {mb:.0f} MB)")
        if sec and not IG_MIN_S <= sec <= IG_MAX_S:
            errs.append(f"Instagram reels must be 3 s - 15 min (this is {sec:.0f} s)")
    if platform == "facebook" and sec and not FB_MIN_S <= sec <= FB_MAX_S:
        errs.append(f"Facebook reels must be 3-90 s (this is {sec:.0f} s)")
    if platform == "youtube" and kind == "reel" and sec > SHORT_MAX_S:
        errs.append(f"YouTube Shorts max 3 min (this is {sec:.0f} s)")
    if platform == "dailymotion" and mb > DM_MAX_MB:
        errs.append("Dailymotion max file size 4 GB")
    if not sec:
        warns.append("video length not known yet (Drive still processing) - not checked")
    # aspect vs track
    if m["w"] and m["h"]:
        portrait = m["h"] > m["w"]
        if kind == "reel" and not portrait:
            (errs if platform == "facebook" else warns).append(
                f"reel is {m['w']}x{m['h']} (not vertical 9:16)" + (" - Facebook Reels need 9:16" if platform == "facebook" else ""))
        if kind == "main" and portrait:
            warns.append(f"main video is vertical {m['w']}x{m['h']} (main track expects 16:9)")
    else:
        warns.append("video width x height not known yet - aspect not checked")
    # text
    if platform == "youtube":
        if len(title) > YT_TITLE:
            warns.append(f"YouTube title {len(title)} chars > {YT_TITLE} (will be cut)")
        if len(cap) > YT_DESC:
            warns.append(f"YouTube description {len(cap)} chars > {YT_DESC} (will be cut)")
        if re.search(r"[<>]", title + cap):
            errs.append("YouTube rejects < or > in title/description")
        if len(",".join(t.get("tags") or [])) > YT_TAGS:
            errs.append("YouTube tags longer than 500 chars total")
        n = len(hashtags(title + "\n" + cap))
        if n > YT_HASHTAGS:
            warns.append(f"{n} hashtags for YouTube (keep <= {YT_HASHTAGS}; above 60 YouTube ignores all)")
    if platform == "instagram":
        if len(cap) > IG_CAPTION:
            errs.append(f"Instagram caption {len(cap)} chars > {IG_CAPTION}")
        n = len(hashtags(cap))
        if n > IG_HASHTAGS:
            errs.append(f"{n} hashtags for Instagram (max {IG_HASHTAGS})")
    if platform == "dailymotion":
        if len(title) > DM_TITLE:
            warns.append(f"Dailymotion title {len(title)} chars > {DM_TITLE} (will be cut)")
        if len(cap) > DM_DESC:
            warns.append(f"Dailymotion description {len(cap)} chars > {DM_DESC} (will be cut)")
    return errs, warns


def apply(state, items):
    """Hold / release posts by the check result. Returns (notes, errors, warnings) where errors/warnings
    are lists of (item name, platform, message). Never touches a post already published or already
    uploaded to a platform (Facebook scheduled reel with an id)."""
    notes, all_e, all_w = [], [], []
    recs = state.setdefault("items", {})
    for item in items:
        rec = recs.get(item["id"]) or {}
        if rec.get("skip"):
            continue
        for p in scheduler.platforms_for(item):
            errs, warns = check(item, p)
            all_e += [(item["name"], p, e) for e in errs]
            all_w += [(item["name"], p, w) for w in warns]
            cur = rec.get(p) or {}
            if cur.get("status") == "published" or (cur.get("status") == "scheduled" and cur.get("id")):
                continue
            if errs:
                if cur.get("status") != "held":
                    notes.append(f"{item['name']} [{p}] HELD: {'; '.join(errs)}")
                cur.update({"status": "held", "check_errors": errs, "track": scheduler.track(item, p)})
                for k in ("slot", "slot_key", "fixed", "bump", "needs_reschedule"):
                    cur.pop(k, None)
                rec[p] = cur
                recs[item["id"]] = rec
            elif cur.get("status") == "held":
                for k in ("status", "check_errors"):
                    cur.pop(k, None)
                notes.append(f"{item['name']} [{p}] released (checks pass now)")
    return notes, all_e, all_w


def any_held(state):
    return any(isinstance(r, dict) and r.get("status") == "held"
               for rec in state.get("items", {}).values() if not rec.get("skip") for r in rec.values())
