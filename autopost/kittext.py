"""Read an upload kit from ANY text-like file: .txt .md .pdf .docx .odt .rtf .html .json .csv or a Google Doc.

Two layouts are understood:
  1. the PDF 'UPLOAD KIT' layout (kitpdf.py: '1. YOUTUBE SHORTS' sections + TITLE/DESCRIPTION/TAGS fields)
  2. a free layout: platform headings (e.g. '━━ YOUTUBE SHORTS ━━', '## Instagram', 'FACEBOOK REELS:')
     with 'Title:', 'Description:' / 'Caption:', 'Tags:' / 'Keywords:', 'Hashtags:', 'Pinned comment:' fields.
     If the file has a 'COPY-PASTE' part, the text is read from there; a 'PINNED COMMENTS' part gives the pins.
     'Upload time: 24-11-2026, 3:50 PM IST' / 'Schedule: ...' / 'Post date: ...' sets a fixed time.
"""
import io
import json
import re
import zipfile

from . import kitpdf

MEDIA_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".jpg", ".jpeg", ".png", ".webp", ".gif",
             ".mp3", ".wav", ".m4a", ".zip", ".srt", ".vtt")
GDOC = "application/vnd.google-apps.document"
PLATS = {"youtube": "youtube", "yt": "youtube", "shorts": "youtube", "instagram": "instagram", "ig": "instagram",
         "facebook": "facebook", "fb": "facebook", "dailymotion": "dailymotion", "dm": "dailymotion"}


def is_candidate(f):
    """A file that might hold a kit: anything that is not media, not a zip, not a 00_Text_ file."""
    n = f["name"].lower()
    if f.get("mimeType") == GDOC:
        return True
    if n.startswith("00_text_") or n.startswith(".") or n.endswith(MEDIA_EXT):
        return False
    return "." in n


def to_text(name, data):
    n = name.lower()
    if n.endswith(".pdf") or data[:4] == b"%PDF":
        return kitpdf.pdf_text(data)
    if n.endswith((".docx", ".odt")) or data[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            xml = z.read("word/document.xml" if "word/document.xml" in z.namelist() else "content.xml").decode("utf-8", "ignore")
            xml = re.sub(r"</w:p>|<text:p[^>]*/>|</text:p>|<w:br/>|</text:h>", "\n", xml)
            return _unescape(re.sub(r"<[^>]+>", "", xml))
        except Exception:
            return ""
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            t = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if n.endswith(".rtf"):
        t = re.sub(r"\\par[d]?", "\n", t)
        t = re.sub(r"\\[a-z]+-?\d* ?|[{}]", "", t)
    elif n.endswith((".html", ".htm")):
        t = re.sub(r"<(br|/p|/div|/h\d|/li)[^>]*>", "\n", t, flags=re.I)
        t = _unescape(re.sub(r"<[^>]+>", "", t))
    elif n.endswith(".json"):
        try:
            return _json_kit_text(json.loads(t))
        except Exception:
            pass
    return t


def _unescape(t):
    import html
    return html.unescape(t)


def _json_kit_text(d):
    """{'youtube': {'title':..,'description':..,'tags':[..]}, ...} -> free-layout text."""
    out = ["UPLOAD KIT"]
    for k, v in (d.get("platforms", d) if isinstance(d, dict) else {}).items():
        p = PLATS.get(str(k).lower())
        if not p or not isinstance(v, dict):
            continue
        out.append(f"== {p.upper()} ==")
        for f, val in v.items():
            if isinstance(val, list):
                val = ", ".join(map(str, val))
            out.append(f"{f}:\n{val}")
    if isinstance(d, dict) and d.get("schedule"):
        out.insert(1, f"Schedule: {d['schedule']}")
    return "\n".join(out)


# ------------------------------------------------------------------ free layout
HEAD_RE = re.compile(
    r"^\s*(?:[#=━─\-*_>|\d.)\s]*)\s*(youtube|instagram|facebook|dailymotion)\b([^\n]{0,60})$", re.I | re.M)
FIELD_RE = re.compile(
    r"^\s*[*_#\-]*\s*(backup title|title|first line|description|caption|text|keywords\s*/\s*tags|keywords|tags|"
    r"hashtags|pinned comment|category)\s*[*_]*\s*:\s*(.*)$", re.I)
TIME_RE = re.compile(r"(?:upload\s*time|schedule[d]?(?:\s*(?:for|at|time))?|post\s*(?:date|time|at)[^:\n]*|publish\s*(?:date|time|at)?)"
                     r"[^:\n]{0,40}:\s*([^\n]+)", re.I)


def _is_head(line):
    m = HEAD_RE.match(line)
    if not m or re.match(r"^\s*#\w", line):
        return None
    rest = m.group(2)
    # a heading is short and has no sentence after the name (no 'Title:' etc.)
    if ":" in rest.strip(" ━─=#*-()")[:-1] or len(line.strip()) > 70:
        return None
    if FIELD_RE.match(line):
        return None
    return PLATS[m.group(1).lower()]


def _blocks(text):
    out, cur, buf = [], None, []
    for line in text.splitlines():
        p = _is_head(line)
        if p or re.match(r"^\s*={5,}|^\s*(PINNED COMMENTS|ALL PLATFORMS|COPY-PASTE)", line, re.I):
            if cur:
                out.append((cur, "\n".join(buf)))
            cur, buf = p, []
            continue
        if cur:
            buf.append(line)
    if cur:
        out.append((cur, "\n".join(buf)))
    return out


def _fields(body):
    fields, name, buf = {}, None, []

    def flush():
        if name:
            fields.setdefault(name, "\n".join(buf).strip())
    for line in body.splitlines():
        m = FIELD_RE.match(line)
        if m:
            flush()
            name = re.sub(r"\s+", " ", m.group(1).lower())
            name = {"keywords / tags": "tags", "keywords": "tags", "first line": "title", "text": "description",
                    "caption": "description"}.get(name, name)
            buf = [m.group(2)] if m.group(2).strip() else []
            continue
        if name:
            buf.append(line)
    flush()
    return fields


def _clean(t):
    keep, extra = [], []
    for line in t.splitlines():
        s = line.strip()
        m = re.match(r"^\((?:add at the end|add)\s*:\s*(.+)\)$", s, re.I)
        if m:
            extra.append(m.group(1).strip())
            continue
        if s.startswith("(") and s.endswith(")"):
            continue
        keep.append(line.rstrip())
    return "\n".join(keep).strip(), extra


def parse_free(text):
    low = text
    m = re.search(r"^[\s=━#*\-]*COPY-PASTE[^\n]*$", text, re.I | re.M)
    copy = text[m.end():] if m else text
    pm = re.search(r"^[\s=━#*\-]*PINNED COMMENTS[^\n]*$", copy, re.I | re.M)
    pins_part = copy[pm.end():] if pm else ""
    copy_part = copy[:pm.start()] if pm else copy
    pins = {}
    for p, body in _blocks(pins_part):
        b, _ = _clean(body)
        if b:
            pins.setdefault(p, b)
    tm = TIME_RE.search(text)
    from .drive import parse_when
    when = parse_when(tm.group(1)) if tm else None
    name_m = re.search(r"^\s*([^\n]*?)\s*[—\-–]*\s*UPLOAD KIT", low, re.I | re.M)
    kit = {"_name": (name_m.group(1).strip(" —-–") if name_m else "")[:80]}
    for p, body in _blocks(copy_part):
        f = _fields(body)
        if not f:
            continue
        cap, extra = _clean(f.get("description", ""))
        title, _ = _clean(f.get("title", ""))
        title = " ".join(title.split())
        tags = [t.strip() for t in " ".join(_clean(f.get("tags", ""))[0].split()).split(",") if t.strip()]
        hashtags = " ".join(_clean(f.get("hashtags", ""))[0].split())
        if hashtags and hashtags not in cap:
            cap = (cap + "\n\n" + hashtags).strip()
        _, extra2 = _clean(body)
        for e in extra + extra2:
            if e.lower() not in cap.lower():
                cap = (cap + "\n\n" + e).strip()
        pin, _ = _clean(f.get("pinned comment", ""))
        if p in kit:
            continue
        kit[p] = {"title": title or (cap.splitlines()[0] if cap else kit["_name"]), "caption": cap, "tags": tags,
                  "pinned": pins.get(p) or pin, "schedule": when, "thumb_offset_ms": None, "settings": ""}
    return kit


def parse(text):
    """Return a kit dict (as kitpdf.parse) or None when the text is not a kit."""
    if not text or not text.strip():
        return None
    if kitpdf.is_kit(text):
        k = kitpdf.parse(text)
        if any(not p.startswith("_") for p in k):
            return k
    k = parse_free(text)
    return k if any(not p.startswith("_") for p in k) else None
