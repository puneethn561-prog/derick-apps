"""Instagram Reels + Facebook Page Reels via the Graph API (local file upload, no public URL needed)."""
import os
import time

import requests

from . import config

GRAPH = "https://graph.facebook.com"
RUPLOAD = "https://rupload.facebook.com"


def _check(r):
    if r.status_code >= 400:
        raise RuntimeError(f"Meta API {r.status_code}: {r.text[:500]}")
    return r.json()


# ---------------- Instagram ----------------
def ig_publish_reel(path, caption, share_to_feed=True, thumb_offset_ms=None):
    """Upload + publish now. Instagram's API has no scheduling, so call this at slot time."""
    v, tok, uid = config.META_API, config.IG_TOKEN, config.IG_USER_ID
    c = _check(requests.post(f"{GRAPH}/{v}/{uid}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption,
        "share_to_feed": str(share_to_feed).lower(), "access_token": tok,
        "is_ai_generated": "true" if config.AI_LABEL else "false",
        **({"thumb_offset": str(int(thumb_offset_ms))} if thumb_offset_ms else {})}))
    cid = c["id"]
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        _check(requests.post(f"{RUPLOAD}/ig-api-upload/{v}/{cid}", data=fh, headers={
            "Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(size)}))
    for _ in range(60):                       # wait up to ~10 min for processing
        s = _check(requests.get(f"{GRAPH}/{v}/{cid}", params={"fields": "status_code,status", "access_token": tok}))
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"IG processing failed: {s}")
        time.sleep(10)
    else:
        raise RuntimeError("IG processing timed out")
    p = _check(requests.post(f"{GRAPH}/{v}/{uid}/media_publish", data={"creation_id": cid, "access_token": tok}))
    return p["id"]


def ig_comment(media_id, text):
    r = _check(requests.post(f"{GRAPH}/{config.META_API}/{media_id}/comments",
                             data={"message": text, "access_token": config.IG_TOKEN}))
    return r["id"]


def ig_published_today_count():
    """Reels already live today (catches manual posts too), used to respect the 2/day cap."""
    r = _check(requests.get(f"{GRAPH}/{config.META_API}/{config.IG_USER_ID}/media", params={
        "fields": "id,media_product_type,timestamp", "limit": 25, "access_token": config.IG_TOKEN}))
    from datetime import datetime
    today = datetime.now(config.TZ).date()
    n = 0
    for m in r.get("data", []):
        ts = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z").astimezone(config.TZ)
        if ts.date() == today and m.get("media_product_type") == "REELS":
            n += 1
    return n


# ---------------- Facebook Page ----------------
def fb_upload_reel(path, description, publish_at=None, title=None):
    """Upload a Page reel. publish_at (aware datetime) -> natively SCHEDULED by Facebook."""
    v, tok, page = config.META_API, config.FB_PAGE_TOKEN, config.FB_PAGE_ID
    s = _check(requests.post(f"{GRAPH}/{v}/{page}/video_reels",
                             data={"upload_phase": "start", "access_token": tok}))
    vid = s["video_id"]
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        _check(requests.post(f"{RUPLOAD}/video-upload/{v}/{vid}", data=fh, headers={
            "Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(size)}))
    data = {"upload_phase": "finish", "video_id": vid, "description": description,
            "is_ai_generated": "true" if config.AI_LABEL else "false", "access_token": tok}
    if title:
        data["title"] = title
    if publish_at:
        data["video_state"] = "SCHEDULED"
        data["scheduled_publish_time"] = str(int(publish_at.timestamp()))
    else:
        data["video_state"] = "PUBLISHED"
    _check(requests.post(f"{GRAPH}/{v}/{page}/video_reels", data=data))
    return vid


def fb_reschedule(video_id, publish_at):
    _check(requests.post(f"{GRAPH}/{config.META_API}/{video_id}", data={
        "scheduled_publish_time": str(int(publish_at.timestamp())), "access_token": config.FB_PAGE_TOKEN}))


def fb_status(video_id):
    r = _check(requests.get(f"{GRAPH}/{config.META_API}/{video_id}", params={
        "fields": "status,scheduled_publish_time,published,permalink_url", "access_token": config.FB_PAGE_TOKEN}))
    return r


def fb_comment(video_id, text):
    r = _check(requests.post(f"{GRAPH}/{config.META_API}/{video_id}/comments",
                             data={"message": text, "access_token": config.FB_PAGE_TOKEN}))
    return r["id"]


def fb_set_thumbnail(video_id, image_path):
    with open(image_path, "rb") as fh:
        _check(requests.post(f"{GRAPH}/{config.META_API}/{video_id}/thumbnails",
                             data={"is_preferred": "true", "access_token": config.FB_PAGE_TOKEN},
                             files={"source": fh}))


# ---------------- simple view counts (weekly report, best effort) ----------------
def _first_value(j):
    d = (j.get("data") or [{}])[0]
    vals = d.get("values") or [{}]
    return int(vals[0].get("value") or d.get("total_value", {}).get("value") or 0)


def ig_views(media_id):
    """Instagram media insight `views` (needs instagram_manage_insights)."""
    return _first_value(_check(requests.get(f"{GRAPH}/{config.META_API}/{media_id}/insights",
                                            params={"metric": "views", "access_token": config.IG_TOKEN})))


def fb_reel_views(video_id):
    """Facebook Reel plays (`blue_reels_play_count`, needs read_insights)."""
    return _first_value(_check(requests.get(f"{GRAPH}/{config.META_API}/{video_id}/video_insights",
                                            params={"metric": "blue_reels_play_count",
                                                    "access_token": config.FB_PAGE_TOKEN})))
