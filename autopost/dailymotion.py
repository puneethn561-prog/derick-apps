"""Dailymotion upload.

Default: API v2 with a Private API key (client_credentials) -> secrets DM_API_KEY / DM_API_SECRET.
Optional var DM_PROFILE_ID (else the first profile from GET /v2/me, or the one named DM_PROFILE_NAME).
Legacy fallback (password grant) only if DM_USERNAME + DM_PASSWORD are set.
Native scheduling is partner-only, so the bot posts at slot time.
"""
import os
import time

import requests

from . import config

V2_TOKEN_URL = "https://oauth2.dailymotion.com/v2/token"
V2_SCOPES = "video.manage account.read"
_tok = {"value": None, "exp": 0}


def _legacy():
    return bool(config.DM_USERNAME and config.DM_PASSWORD)


def _auth():
    if _tok["value"] and time.time() < _tok["exp"] - 60:
        return _tok["value"]
    if _legacy():
        r = requests.post(f"{config.DM_BASE}/oauth/token", data={
            "grant_type": "password", "client_id": config.DM_API_KEY, "client_secret": config.DM_API_SECRET,
            "username": config.DM_USERNAME, "password": config.DM_PASSWORD, "scope": "manage_videos"})
    else:
        r = requests.post(V2_TOKEN_URL, data={
            "grant_type": "client_credentials", "client_id": config.DM_API_KEY,
            "client_secret": config.DM_API_SECRET, "scope": V2_SCOPES})
    if r.status_code >= 400:
        raise RuntimeError(f"Dailymotion auth {r.status_code}: {r.text[:300]}")
    j = r.json()
    _tok["value"], _tok["exp"] = j["access_token"], time.time() + int(j.get("expires_in", 1800))
    return _tok["value"]


def _profile_id(h):
    pid = os.getenv("DM_PROFILE_ID", "")
    if pid:
        return pid
    me = requests.get(f"{config.DM_BASE}/v2/me", headers=h)
    if me.status_code >= 400:
        raise RuntimeError(f"Dailymotion /v2/me {me.status_code}: {me.text[:300]}")
    profiles = me.json().get("profiles", [])
    want = os.getenv("DM_PROFILE_NAME", "")
    for p in profiles:
        if not want or p.get("name") == want:
            return p["profile_id"]
    raise RuntimeError("Dailymotion: no profile found for this API key")


def _upload_file(path, upload_url):
    with open(path, "rb") as fh:
        r = requests.post(upload_url, files={"file": fh}, timeout=3600)
    if r.status_code >= 400:
        raise RuntimeError(f"Dailymotion upload {r.status_code}: {r.text[:300]}")
    return r.json()["url"]


def publish(path, title, description="", tags=None, channel="news", for_kids=False):
    h = {"Authorization": f"Bearer {_auth()}"}
    if _legacy():
        up = requests.get(f"{config.DM_BASE}/file/upload", headers=h).json()["upload_url"]
        data = {"url": _upload_file(path, up), "title": title[:255], "description": description[:3000],
                "channel": channel, "published": "true", "is_created_for_kids": str(for_kids).lower(),
                "stream_altered_with_ai": "true" if config.AI_LABEL else "false"}
        if tags:
            data["tags"] = ",".join(tags)[:150]
        v = requests.post(f"{config.DM_BASE}/me/videos", headers=h, data=data)
        if v.status_code >= 400:
            raise RuntimeError(f"Dailymotion create {v.status_code}: {v.text[:300]}")
        return v.json()["id"]

    sess = requests.post(f"{config.DM_BASE}/v2/files/upload_sessions", headers=h)
    if sess.status_code >= 400:
        raise RuntimeError(f"Dailymotion upload session {sess.status_code}: {sess.text[:300]}")
    file_url = _upload_file(path, sess.json()["upload_url"])
    body = {"title": title[:255], "description": description[:3000], "category": channel,
            "visibility": "public", "is_for_kids": bool(for_kids), "source": {"file_url": file_url}}
    if tags:
        body["tags"] = [t[:50] for t in tags][:20]
    pid = _profile_id(h)
    v = requests.post(f"{config.DM_BASE}/v2/profiles/{pid}/videos", headers={**h, "content-type": "application/json"}, json=body)
    if v.status_code >= 400:
        raise RuntimeError(f"Dailymotion create {v.status_code}: {v.text[:300]}")
    j = v.json()
    return j.get("video_id") or j.get("id")
