"""mode=unpost: delete already-posted copies of one package from every platform, then mark it skip.
Used when the owner posted the same video by hand. UNPOST_MATCH = text in the item name (case-insensitive)."""
import os
import requests
from . import config, state as st


def _yt(vid):
    from . import youtube
    r = requests.delete(f"{youtube.API}/videos", params={"id": vid},
                        headers={"Authorization": f"Bearer {youtube._auth()}"})
    if r.status_code >= 400:
        raise RuntimeError(f"YouTube delete {r.status_code}: {r.text[:300]}")


def _meta(mid, token):
    r = requests.delete(f"https://graph.facebook.com/{config.META_API}/{mid}", params={"access_token": token})
    if r.status_code >= 400:
        raise RuntimeError(f"Meta delete {r.status_code}: {r.text[:300]}")


def _dm(vid):
    from . import dailymotion as dm
    h = {"Authorization": f"Bearer {dm._auth()}"}
    r = requests.delete(f"{config.DM_BASE}/video/{vid}", headers=h)
    if r.status_code >= 400:
        r2 = requests.delete(f"{config.DM_BASE}/v2/videos/{vid}", headers=h)
        if r2.status_code >= 400:
            raise RuntimeError(f"Dailymotion delete {r.status_code}: {r.text[:200]} | v2 {r2.status_code}: {r2.text[:200]}")


def run(state, log):
    match = os.getenv("UNPOST_MATCH", "").strip().lower()
    if not match:
        log("unpost: UNPOST_MATCH is empty, nothing to do")
        return True
    ok = True
    for iid, rec in state.get("items", {}).items():
        if match not in rec.get("name", "").lower():
            continue
        for p in ("youtube", "instagram", "facebook", "dailymotion"):
            cur = rec.get(p)
            if not isinstance(cur, dict):
                continue
            mid = cur.get("id")
            if not mid or mid == "dry-run" or cur.get("status") == "deleted":
                log(f"unpost: {p} {rec['name']}: nothing posted (status {cur.get('status')})")
                continue
            try:
                if config.DRY_RUN:
                    log(f"unpost (dry): would delete {p} {mid}")
                    continue
                if p == "youtube":
                    _yt(mid)
                elif p == "instagram":
                    _meta(mid, config.IG_TOKEN)
                elif p == "facebook":
                    _meta(mid, config.FB_PAGE_TOKEN)
                else:
                    _dm(mid)
                cur["status"] = "deleted"
                log(f"unpost: {p} deleted {mid} ({rec['name']})")
            except Exception as e:
                ok = False
                cur["delete_error"] = str(e)[:300]
                log(f"unpost: {p} delete FAILED {mid}: {e}")
        if not config.DRY_RUN:
            rec["skip"] = True
    st.save(state)
    return ok
