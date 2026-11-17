"""Pure rendering: transcript.md, summary.md and actions.json from the stored JSON,
with the speaker names the user set. Nothing here touches the disk."""
import re
from zoneinfo import ZoneInfo

from .util import fmt_duration, fmt_ts, item_key, parse_iso

LABEL_RE = re.compile(r"\b(?:Speaker|Sprecher(?:in)?)\s+(\d+)\b")

HEADINGS = {
    "en": {"tldr": "TL;DR", "decisions": "Decisions", "actions": "Action items",
           "questions": "Open questions", "action": "Action", "owner": "Owner", "due": "Due",
           "none": "None recorded.", "speakers": "speakers", "summary": "Summary",
           "transcript": "Transcript", "version": "version"},
    "de": {"tldr": "Kurzfassung", "decisions": "Entscheidungen", "actions": "Aufgaben",
           "questions": "Offene Fragen", "action": "Aufgabe", "owner": "Verantwortlich",
           "due": "Fällig", "none": "Keine erfasst.", "speakers": "Sprecher",
           "summary": "Zusammenfassung", "transcript": "Transkript", "version": "Version"},
}


def headings(lang):
    return HEADINGS.get((lang or "en")[:2], HEADINGS["en"])


def display_name(label, names):
    name = (names or {}).get(label, "").strip()
    return name or label


def apply_names(text, names):
    """Replace "Speaker 2" (or the German "Sprecher 2") with the name the user set."""
    if not text or not names:
        return text

    def repl(m):
        name = (names.get(f"Speaker {m.group(1)}") or "").strip()
        return name or m.group(0)

    return LABEL_RE.sub(repl, text)


def local_time(iso, tz="Europe/Berlin"):
    t = parse_iso(iso)
    if not t:
        return ""
    return t.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M")


def meeting_line(meta, n_speakers, lang, tz):
    h = headings(lang)
    parts = [local_time(meta.get("started_at") or meta.get("created_at"), tz),
             fmt_duration(meta.get("duration_s") or 0)]
    if n_speakers:
        parts.append(f"{n_speakers} {h['speakers']}")
    if lang:
        parts.append(lang)
    return " · ".join(p for p in parts if p)


def words_to_segments(words, max_gap=1.5, max_len=40.0):
    """Group consecutive words of one speaker into readable segments."""
    segs = []
    for w in words:
        text = (w.get("w") or "").strip()
        if not text:
            continue
        cur = segs[-1] if segs else None
        if (cur and cur["spk"] == w.get("spk") and w["s"] - cur["e"] <= max_gap
                and w["e"] - cur["s"] <= max_len):
            cur["text"] += ("" if _no_space_before(text) else " ") + text
            cur["e"] = max(cur["e"], w["e"])
        else:
            segs.append({"spk": w.get("spk") or "Speaker 1", "s": w["s"], "e": w["e"], "text": text})
    return segs


def _no_space_before(token):
    return token[:1] in ",.;:!?%)" or token.startswith("'")


def render_transcript_md(transcript, names, meta, tz="Europe/Berlin"):
    lang = transcript.get("language") or meta.get("language") or "en"
    h = headings(lang)
    segs = transcript.get("segments") or words_to_segments(transcript.get("words", []))
    title = meta.get("title") or meta.get("id")
    out = [f"# {h['transcript']}: {title}", "",
           meeting_line(meta, len(transcript.get("speakers", [])), lang, tz)
           + f" · {engine_label(transcript.get('engine'))}", ""]
    for note in transcript.get("notes", []):
        out += [f"> {note}", ""]
    turn = None
    for s in segs:
        if s["spk"] != turn:
            out += [f"**{display_name(s['spk'], names)}** · {fmt_ts(s['s'])}", ""]
            turn = s["spk"]
        out += [s["text"], ""]
    return "\n".join(out).rstrip() + "\n"


def engine_label(engine):
    if not engine:
        return ""
    return engine.get("label") or engine.get("name", "")


def build_actions(meeting_id, summary, names):
    items = []
    for a in summary.get("action_items", []):
        text = (a.get("text") or "").strip()
        if not text:
            continue
        owner_label = (a.get("owner") or "").strip() or None
        items.append({
            "key": item_key(text),
            "text": apply_names(text, names),
            "text_raw": text,
            "owner": apply_names(owner_label, names) if owner_label else None,
            "owner_speaker": owner_label if owner_label and LABEL_RE.fullmatch(owner_label) else None,
            "due": (a.get("due") or "").strip() or None,
            "at": (a.get("at") or "").strip() or None,
        })
    return {"meeting_id": meeting_id, "summary_version": summary.get("version"),
            "language": summary.get("language"), "items": items}


def render_summary_md(summary, names, meta, tz="Europe/Berlin", link=None):
    lang = summary.get("language") or meta.get("language") or "en"
    h = headings(lang)
    title = apply_names(summary.get("title") or meta.get("title") or meta.get("id"), names)
    byline = (f"{meeting_line(meta, meta.get('speakers') or 0, lang, tz)} · "
              f"{h['summary']} {h['version']} {summary.get('version', 1)} ({summary.get('author', 'recorder')})")
    out = [f"# {title}", "", f"_{byline}_", ""]
    if link:
        out += [link, ""]
    if summary.get("markdown"):
        # Written by an agent: keep its structure, only apply the names.
        out.append(apply_names(summary["markdown"].strip(), names))
        return "\n".join(out).rstrip() + "\n"
    out += [f"## {h['tldr']}", "", apply_names(summary.get("tldr") or h["none"], names), ""]
    out += [f"## {h['decisions']}", ""]
    out += [f"- {apply_names(d, names)}" for d in summary.get("decisions", [])] or [h["none"]]
    out += ["", f"## {h['actions']}", ""]
    actions = build_actions(meta.get("id"), summary, names)["items"]
    if actions:
        out += [f"| # | {h['action']} | {h['owner']} | {h['due']} |", "|---|---|---|---|"]
        for i, a in enumerate(actions, 1):
            cells = [a["text"], a["owner"] or "", a["due"] or ""]
            out.append(f"| {i} | " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    else:
        out.append(h["none"])
    out += ["", f"## {h['questions']}", ""]
    out += [f"- {apply_names(q, names)}" for q in summary.get("open_questions", [])] or [h["none"]]
    return "\n".join(out).rstrip() + "\n"

