"""YouTube upload (Data API v3) with an OAuth refresh token.

Secrets: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN (scopes youtube.upload + youtube.force-ssl).
Note: until the Google Cloud project passes the YouTube API audit, Google locks API uploads to Private.
"""
import json
import os
import time

import requests

from . import config
from .quota import QuotaExceeded, is_quota_error

_tok = {"value": None, "exp": 0}


def _fail(what, r):
    """Raise QuotaExceeded for a quota error (the caller moves the post), else RuntimeError."""
    msg = f"YouTube {what} {r.status_code}: {r.text[:300]}"
    raise (QuotaExceeded if is_quota_error(r.text) else RuntimeError)(msg)
API = "https://www.googleapis.com/youtube/v3"


def _auth():
    if _tok["value"] and time.time() < _tok["exp"] - 60:
        return _tok["value"]
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": config.YT_CLIENT_ID, "client_secret": config.YT_CLIENT_SECRET,
        "refresh_token": config.YT_REFRESH_TOKEN, "grant_type": "refresh_token"})
    if r.status_code >= 400:
        raise RuntimeError(f"YouTube auth {r.status_code}: {r.text[:300]}")
    j = r.json()
    _tok["value"], _tok["exp"] = j["access_token"], time.time() + int(j.get("expires_in", 3600))
    return _tok["value"]


def upload(path, title, description="", tags=None, short=False, publish_at=None):
    title = title[:100]
    if short and "#shorts" not in (title + description).lower():
        description = (description + "\n\n#Shorts").strip()
    status = {"privacyStatus": "public", "selfDeclaredMadeForKids": False,
              "containsSyntheticMedia": bool(config.AI_LABEL)}
    if publish_at:  # native scheduling: must be private + publishAt (UTC ISO)
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at.astimezone().isoformat()
    body = {"snippet": {"title": title, "description": description[:5000],
                        "tags": (tags or [])[:30], "categoryId": "24", "defaultLanguage": "te"},
            "status": status}
    h = {"Authorization": f"Bearer {_auth()}"}
    size = os.path.getsize(path)
    init = requests.post(f"{API.replace('/youtube/v3', '/upload/youtube/v3')}/videos",
                         params={"uploadType": "resumable", "part": "snippet,status"},
                         headers={**h, "Content-Type": "application/json; charset=UTF-8",
                                  "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                         data=json.dumps(body))
    if init.status_code >= 400:
        _fail("init", init)
    with open(path, "rb") as fh:
        r = requests.put(init.headers["Location"], headers={**h, "Content-Type": "video/mp4"}, data=fh, timeout=7200)
    if r.status_code >= 400:
        _fail("upload", r)
    return r.json()["id"]


def set_thumbnail(video_id, image_path):
    with open(image_path, "rb") as fh:
        r = requests.post("https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
                          params={"videoId": video_id}, headers={"Authorization": f"Bearer {_auth()}",
                                                                 "Content-Type": "image/jpeg"}, data=fh)
    if r.status_code >= 400:
        _fail("thumbnail", r)


def comment(video_id, text):
    body = {"snippet": {"videoId": video_id, "topLevelComment": {"snippet": {"textOriginal": text}}}}
    r = requests.post(f"{API}/commentThreads", params={"part": "snippet"},
                      headers={"Authorization": f"Bearer {_auth()}"}, json=body)
    if r.status_code >= 400:
        _fail("comment", r)
    return r.json()["id"]


def stats(video_ids):
    """{video_id: viewCount} for up to 50 ids in one videos.list call (1 quota unit)."""
    if not video_ids:
        return {}
    r = requests.get(f"{API}/videos", params={"part": "statistics", "id": ",".join(video_ids[:50])},
                     headers={"Authorization": f"Bearer {_auth()}"})
    if r.status_code >= 400:
        _fail("stats", r)
    return {v["id"]: int(v.get("statistics", {}).get("viewCount", 0)) for v in r.json().get("items", [])}
