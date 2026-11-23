"""Parser for the 'UPLOAD KIT' PDF format (What If Labs):

  zip / folder:  <Kit>.pdf  +  <video>.mp4  (+ optional <video>_16x9.mp4)  +  thumb*.jpg
  PDF sections:  '1. YOUTUBE SHORTS' / 'YOUTUBE', 'INSTAGRAM REELS', 'FACEBOOK REELS', 'DAILYMOTION'
  fields:        TITLE, TITLE / FIRST LINE, DESCRIPTION, CAPTION, TAGS, PINNED COMMENT, then 'Settings'
  optional:      'Post date/time: 25-11-2026 at 6:00 PM IST' or 'Schedule: 2026-11-25 18:00' in a section
"""
import io
import re

SECTION_RE = re.compile(r"^\s*\d+\.\s*(YOUTUBE|INSTAGRAM|FACEBOOK|DAILYMOTION)\b[^\n]*$", re.M)
FIELD_RE = re.compile(r"^(TITLE(?: / FIRST LINE)?|DESCRIPTION|CAPTION|TAGS|PINNED COMMENT|ON-SCREEN TEXT[^\n]*|Settings|Upload steps[^\n]*|Good to know)\b[^\n]*$", re.M)
FOOTER_RE = re.compile(r"^.*\|\s*Upload kit\s*$\n?(^\d+\s*$\n?)?", re.M)


def pdf_text(data):
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)


def is_kit(text):
    return bool(re.search(r"UPLOAD KIT", text, re.I)) and bool(SECTION_RE.search(text))


def unwrap(block):
    """PDF text breaks lines inside sentences; glue those back, keep real line breaks."""
    out = []
    for line in [l.strip() for l in block.strip().splitlines()]:
        if not line:
            continue
        if out and not out[-1].endswith((".", "!", "?", ":", "…")) and not out[-1].startswith("#") \
                and not line.startswith("#") and (line[0].islower() or line[0].isdigit() or out[-1].endswith(",")):
            out[-1] += " " + line
        else:
            out.append(line)
    return "\n".join(out)


def parse(text):
    from .drive import parse_when
    text = FOOTER_RE.sub("", text)
    heads = list(SECTION_RE.finditer(text))
    title_m = re.search(r"UPLOAD KIT\s*\n(.+?)\n(?:YouTube|Instagram|Facebook|Dailymotion)", text, re.S | re.I)
    kit = {"_name": " ".join(title_m.group(1).split()) if title_m else ""}
    for i, h in enumerate(heads):
        plat = h.group(1).lower()
        body = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        fields, marks = {}, list(FIELD_RE.finditer(body))
        for j, m in enumerate(marks):
            name = m.group(1).split(" /")[0].upper()
            val = body[m.end(): marks[j + 1].start() if j + 1 < len(marks) else len(body)]
            fields.setdefault(name, val)
        settings = fields.get("SETTINGS", "")
        caption = unwrap(fields.get("DESCRIPTION") or fields.get("CAPTION") or "")
        title = unwrap(fields.get("TITLE", "")).replace("\n", " ")
        tags = [t.strip() for t in " ".join(fields.get("TAGS", "").split()).split(",") if t.strip()]
        when = re.search(r"(?:Schedule|Post\s*(?:date|time|at)[^:\n]*)\s*:\s*(.+)", body, re.I)
        offs = re.search(r"about\s+(\d+(?:\.\d+)?)\s*s\b", body)
        kit[plat] = {
            "title": title or (caption.splitlines()[0] if caption else kit["_name"]),
            "caption": caption, "tags": tags,
            "pinned": unwrap(fields.get("PINNED COMMENT", "")),
            "schedule": parse_when(when.group(1)) if when else None,
            "thumb_offset_ms": int(float(offs.group(1)) * 1000) if offs else None,
            "settings": " ".join(settings.split())[:600],
        }
    return kit
