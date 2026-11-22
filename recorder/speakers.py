"""Help with naming the speakers of a meeting: how much each one said and when,
a few of their lines, seven seconds of their voice to listen to, and names
suggested from what was said (the recorder's summary) or from what a person's
agent knows (memory, wiki, calendar). Nothing is renamed without the person.
No voiceprints: a clip is cut from the meeting's own audio when someone asks
for it, and nothing about a voice is kept or compared across meetings."""
import re
import struct

from . import vault
from .render import words_to_segments
from .util import now_iso, read_json, write_json

CLIP_S = 7.0
WHO = ("recorder", "agent", "person")


def profile(transcript):
    """Per label: words, share of all words (%), turns, first time (s), three of
    their longest lines, and the clip: a window inside their longest turn."""
    words = [w for w in (transcript or {}).get("words", []) if (w.get("w") or "").strip()]
    total = len(words) or 1
    segs = words_to_segments(words)
    labels = (transcript or {}).get("speakers") or sorted({s["spk"] for s in segs})
    out = {}
    for label in labels:
        mine = [s for s in segs if s["spk"] == label]
        n = sum(1 for w in words if w.get("spk") == label)
        longest = sorted(mine, key=lambda s: s["e"] - s["s"], reverse=True)
        clip = None
        if longest:
            top = longest[0]
            start = top["s"] + min(0.3, (top["e"] - top["s"]) / 4)
            clip = {"s": round(start, 2), "e": round(min(top["e"], start + CLIP_S), 2)}
        out[label] = {"words": n, "share": round(100 * n / total), "turns": len(mine),
                      "first": round(mine[0]["s"], 1) if mine else None,
                      "lines": [_line(s["text"]) for s in sorted(longest[:3], key=lambda s: s["s"])],
                      "clip": clip}
    return out


def _line(text, n=140):
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " ..."


# "I'm Anna", "ich bin Anna": the only way a speaker's own line names the speaker
_SELF = re.compile(r"\b(i'?m|i am|my name is|this is|it'?s|call me|ich bin|ich hei(?:ss|ß)e|mein name ist|hier ist|hier spricht)\s+", re.I)


def plausible(label, name, why):
    """A name in a line a speaker says themselves belongs to someone they talk to,
    unless they introduce themselves with it: "[00:00:47] Speaker 1: okay Anthony,
    back to the document" does not make Speaker 1 Anthony."""
    m = re.match(r"\s*\[[0-9:]+\]\s*([^:]+):\s*(.*)", why or "", re.S)
    if not m or m.group(1).strip() != label:
        return True
    line, name = m.group(2).lower(), (name or "").lower().strip()
    return bool(name) and any(line[x.end():].startswith(name) for x in _SELF.finditer(line))


# A suggestion is a name and nothing else: an agent once suggested "Lewis
# (probably Luis) - tentative", which the app then offered for the name field.
# Doubts and reasons belong in "why".
_HEDGE = re.compile(r"\b(probably|maybe|perhaps|possibly|likely|tentative|unsure|unclear|unknown|guess|or|vermutlich|"
                    r"wahrscheinlich|eventuell|vielleicht|unklar|unbekannt|oder|speaker|sprecher)\b", re.I)


def plain_name(name):
    """The name if it is one ("Anna", "Anna Meier", "Jean-Luc"), else None."""
    name = " ".join(str(name or "").split()).strip("\"'\u201c\u201d\u2018\u2019 ")
    if not 2 <= len(name) <= 40 or len(name.split()) > 4 or not name[0].isalpha():
        return None
    if re.search(r"[()\[\]{}?!:;,/|<>@#\d]| - ", name) or _HEDGE.search(name):
        return None
    return name


def hints(folder):
    return read_json(folder / "speaker_hints.json", {}) or {}


def suggest(folder, labels, suggestions, by):
    """suggestions: {label: {name, why}}. One per author and label: a newer one
    replaces that author's older one; an empty name withdraws it. Returns all
    suggestions and the ones refused for not being a name, with why."""
    if by not in WHO:
        raise ValueError(f"by must be one of {WHO}")
    cur, refused = hints(folder), {}
    for label, s in (suggestions or {}).items():
        if label not in labels or not isinstance(s, dict):
            continue
        raw = " ".join(str(s.get("name") or "").split())
        name = plain_name(raw)
        if raw and not name:
            refused[label] = ("not a name: give only the name the person is called, like \"Anna\" or \"Anna Meier\"; "
                              "doubts and reasons go into why, and if you are not fairly sure, suggest nothing")
            continue
        keep = [x for x in cur.get(label, []) if x.get("by") != by]
        if name:
            keep.append({"name": name, "why": " ".join(str(s.get("why") or "").split())[:300], "by": by, "at": now_iso()})
        cur[label] = keep
    write_json(folder / "speaker_hints.json", cur)
    return cur, refused


def clip_wav(path, start, end):
    """Seconds start..end of a 16-bit PCM WAV (encrypted at rest or not) as a WAV of their own."""
    v = vault.VAULT
    head = b"".join(v.stream(path, 0, 4096))
    i, fmt, data = 12, None, None
    while i + 8 <= len(head):
        cid, size = head[i:i + 4], struct.unpack("<I", head[i + 4:i + 8])[0]
        if cid == b"fmt ":
            fmt = head[i + 8:i + 24]
        if cid == b"data":
            data = i + 8
            break
        i += 8 + size + (size & 1)
    if fmt is None or data is None:
        raise ValueError("not a PCM WAV")
    _, channels, rate, _, align, bits = struct.unpack("<HHIIHH", fmt)
    pcm = b"".join(v.stream(path, data + int(start * rate) * align, data + int(end * rate) * align))
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * align, align, bits) +
            b"data" + struct.pack("<I", len(pcm)) + pcm)
