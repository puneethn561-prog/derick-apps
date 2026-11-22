"""Dailymotion upload (REST API). Native scheduling (publish_date) is partner-only, so we post at slot time."""
import requests

from . import config

_token = None


def _auth():
    global _token
    if _token:
        return _token
    r = requests.post(f"{config.DM_BASE}/oauth/token", data={
        "grant_type": "password", "client_id": config.DM_API_KEY, "client_secret": config.DM_API_SECRET,
        "username": config.DM_USERNAME, "password": config.DM_PASSWORD, "scope": "manage_videos"})
    if r.status_code >= 400:
        raise RuntimeError(f"Dailymotion auth {r.status_code}: {r.text[:300]}")
    _token = r.json()["access_token"]
    return _token


def publish(path, title, description="", tags=None, channel="news", for_kids=False):
    h = {"Authorization": f"Bearer {_auth()}"}
    up = requests.get(f"{config.DM_BASE}/file/upload", headers=h).json()["upload_url"]
    with open(path, "rb") as fh:
        r = requests.post(up, files={"file": fh})
    if r.status_code >= 400:
        raise RuntimeError(f"Dailymotion upload {r.status_code}: {r.text[:300]}")
    data = {"url": r.json()["url"], "title": title[:255], "description": description[:3000],
            "channel": channel, "published": "true", "is_created_for_kids": str(for_kids).lower()}
    if tags:
        data["tags"] = ",".join(tags)[:150]
    v = requests.post(f"{config.DM_BASE}/me/videos", headers=h, data=data)
    if v.status_code >= 400:
        raise RuntimeError(f"Dailymotion create {v.status_code}: {v.text[:300]}")
    return v.json()["id"]
