"""Tiny GitHub Issues helper (uses the workflow's GITHUB_TOKEN). Every write is skipped (and printed)
when there is no token or when DRY_RUN=1, so trial / test runs never open issues."""
import requests

from . import config

API = "https://api.github.com"


def enabled():
    return bool(config.GITHUB_TOKEN and config.GITHUB_REPOSITORY) and not config.DRY_RUN


def _h():
    return {"Authorization": f"Bearer {config.GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}


def _url(path=""):
    return f"{API}/repos/{config.GITHUB_REPOSITORY}/issues{path}"


def _ok(r):
    if r.status_code >= 400:
        raise RuntimeError(f"GitHub {r.status_code}: {r.text[:200]}")
    return r.json() if r.text else {}


def open_issues(label):
    return [i for i in _ok(requests.get(_url(), headers=_h(), params={"state": "open", "labels": label, "per_page": 100}))
            if "pull_request" not in i]


def find_open(title, label):
    return next((i for i in open_issues(label) if i.get("title") == title), None)


def create(title, body, labels):
    return _ok(requests.post(_url(), headers=_h(), json={"title": title, "body": body, "labels": labels}))


def comment(number, body):
    return _ok(requests.post(_url(f"/{number}/comments"), headers=_h(), json={"body": body}))


def close(number, comment_body=None):
    if comment_body:
        comment(number, comment_body)
    return _ok(requests.patch(_url(f"/{number}"), headers=_h(), json={"state": "closed"}))


def upsert(title, body, label):
    """Comment on the open issue with this title, or open it. Returns the issue number (None when off)."""
    if not enabled():
        print(f"[github off] {title}\n{body}")
        return None
    i = find_open(title, label)
    if i:
        comment(i["number"], body)
        return i["number"]
    return create(title, body, [label])["number"]
