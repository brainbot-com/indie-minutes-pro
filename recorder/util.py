"""Small helpers shared by the service, the worker and the CLIs."""
import datetime as dt
import hashlib
import json
import re
import secrets
from pathlib import Path

from . import vault


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def now_iso():
    return utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def local_date(iso, tz="Europe/Berlin"):
    from zoneinfo import ZoneInfo
    t = parse_iso(iso) or utcnow()
    return t.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H%M")


def new_meeting_id(started=None):
    """Sortable and readable: 20261117-1530-a1b2 (UTC)."""
    t = started or utcnow()
    return t.astimezone(dt.timezone.utc).strftime("%Y%m%d-%H%M") + "-" + secrets.token_hex(2)


def new_id(prefix):
    return f"{prefix}_{secrets.token_hex(6)}"


def new_token():
    return "irt_" + secrets.token_urlsafe(32)


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def item_key(text):
    """Stable key for an action item, so reruns recognise items they have seen."""
    norm = re.sub(r"\W+", " ", (text or "").lower()).strip()
    return hashlib.sha1(norm.encode()).hexdigest()[:12]


def write_bytes(path, data):
    """Whole or not at all; encrypted when it is a meeting's file (vault.py)."""
    with vault.VAULT.writer(path) as f:
        f.write(data)


def read_bytes(path):
    return vault.VAULT.read(path)


def read_text(path):
    return read_bytes(path).decode()


def write_text(path, text):
    write_bytes(path, text.encode())


def write_json(path, obj):
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def read_json(path, default=None):
    try:
        return json.loads(read_text(path))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def fmt_ts(seconds):
    seconds = max(0, int(seconds or 0))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_clock(seconds):
    """A length as the apps show it: 47:12, or 1:46:43 from an hour on."""
    seconds = max(0, int(round(seconds or 0)))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_duration(seconds):
    minutes = round((seconds or 0) / 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


LANGUAGE_NAMES = {
    "de": "German", "en": "English", "fr": "French", "es": "Spanish", "it": "Italian",
    "nl": "Dutch", "pl": "Polish", "pt": "Portuguese", "sv": "Swedish", "da": "Danish",
}

_STOPWORDS = {
    "de": set("und der die das ist nicht ich wir sie es ein eine zu mit auf für auch dass wie was aber noch dann schon".split()),
    "en": set("the and is not we you it a an to with on for also that what but so this they have be".split()),
}


def guess_language(text, default="en"):
    """Cheap de/en guess from stopwords, used only when the engine reports no language."""
    words = re.findall(r"[a-zäöüß]+", (text or "").lower())[:3000]
    if not words:
        return default
    scores = {lang: sum(w in sw for w in words) for lang, sw in _STOPWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else default
