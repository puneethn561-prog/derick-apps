"""Read-only credential check for every platform (posts nothing)."""
import requests

from . import config


def _line(name, fn):
    try:
        print(f"OK   {name}: {fn()}")
        return True
    except Exception as e:
        print(f"FAIL {name}: {str(e)[:300]}")
        return False


def _fb():
    r = requests.get(f"https://graph.facebook.com/{config.META_API}/{config.FB_PAGE_ID}",
                     params={"fields": "name", "access_token": config.FB_PAGE_TOKEN})
    if r.status_code >= 400:
        raise RuntimeError(r.text)
    return r.json().get("name")


def _ig():
    r = requests.get(f"https://graph.facebook.com/{config.META_API}/{config.IG_USER_ID}",
                     params={"fields": "username", "access_token": config.IG_TOKEN})
    if r.status_code >= 400:
        raise RuntimeError(r.text)
    return "@" + r.json().get("username", "?")


def _dm():
    from . import dailymotion
    tok = dailymotion._auth()
    h = {"Authorization": f"Bearer {tok}"}
    return "profile " + str(dailymotion._profile_id(h))


def _yt():
    from . import youtube
    tok = youtube._auth()
    r = requests.get(f"{youtube.API}/channels", params={"part": "snippet", "mine": "true"},
                     headers={"Authorization": f"Bearer {tok}"})
    if r.status_code >= 400:
        raise RuntimeError(r.text)
    items = r.json().get("items", [])
    return items[0]["snippet"]["title"] if items else "no channel"


def _drive(drive):
    return f"{len(drive.items())} items in upload folder"


def run(drive):
    ok = [_line("Drive", lambda: _drive(drive)), _line("Facebook", _fb), _line("Instagram", _ig),
          _line("Dailymotion", _dm), _line("YouTube", _yt)]
    return all(ok)
