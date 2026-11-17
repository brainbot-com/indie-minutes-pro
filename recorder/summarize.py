"""The recorder's own first summary. It is deliberately simple; the Agent Command
Center agent can later store a better version with its own context and memory."""
import json

from .llm import chat_json
from .render import words_to_segments
from .util import LANGUAGE_NAMES, fmt_ts, guess_language

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "tldr": {"type": "string"},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "owner": {"type": "string"},
                    "due": {"type": "string"},
                    "at": {"type": "string"},
                },
                "required": ["text", "owner", "due", "at"],
            },
        },
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "tldr", "decisions", "action_items", "open_questions"],
}

SYSTEM = """You write the minutes of a meeting from its transcript.

Rules:
- Write every value in {lang}, the language of the meeting.
- People appear as speaker labels such as "Speaker 2". Use exactly these labels, untranslated, whenever you refer to a person. Never guess real names.
- title: at most 8 words.
- tldr: 2 to 4 sentences on what the meeting was about and what came out of it.
- decisions: only what the participants actually agreed on.
- action_items: concrete tasks someone took on or was asked to do. owner is the responsible speaker label, or "" if unclear. due is the deadline exactly as it was said (for example "Friday" or "end of November"), or "" if none was said; never invent one. at is the timestamp [hh:mm:ss] of the line where it was agreed.
- open_questions: questions that were raised and not settled.
- Use empty lists when there is nothing. Add nothing that is not in the transcript."""

MERGE = """Below are the minutes of consecutive parts of ONE meeting, as JSON. Merge them into the minutes of the whole meeting: one title, one tldr for the whole meeting, and the decisions, action items and open questions of all parts without duplicates (drop questions a later part answered). Keep the speaker labels and all rules from before."""

PART_CHARS = 60_000      # about 15-20k tokens per part
SINGLE_CALL_CHARS = 120_000


def transcript_lines(transcript):
    segs = transcript.get("segments") or words_to_segments(transcript.get("words", []))
    return [f"[{fmt_ts(s['s'])}] {s['spk']}: {s['text']}" for s in segs]


def summarize(transcript, settings, log=print):
    lines = transcript_lines(transcript)
    text = "\n".join(lines)
    lang = transcript.get("language") or guess_language(text)
    system = SYSTEM.format(lang=LANGUAGE_NAMES.get(lang, lang))
    stats = []
    if not lines:
        result = {"title": "", "tldr": "", "decisions": [], "action_items": [], "open_questions": []}
    elif len(text) <= SINGLE_CALL_CHARS:
        result, st = chat_json(settings, system, "Transcript:\n\n" + text, SCHEMA)
        stats.append(st)
    else:
        parts, cur = [], []
        for line in lines:
            if cur and sum(len(x) for x in cur) + len(line) > PART_CHARS:
                parts.append(cur)
                cur = []
            cur.append(line)
        parts.append(cur)
        notes = []
        for i, part in enumerate(parts, 1):
            log(f"summary: part {i}/{len(parts)}")
            obj, st = chat_json(settings, system, f"Transcript, part {i} of {len(parts)}:\n\n" + "\n".join(part), SCHEMA)
            notes.append(obj)
            stats.append(st)
        result, st = chat_json(settings, system, MERGE + "\n\n" + json.dumps(notes, ensure_ascii=False), SCHEMA)
        stats.append(st)
    for a in result.get("action_items", []):
        for k in ("owner", "due", "at"):
            a[k] = (a.get(k) or "").strip()
    return {**result, "language": lang, "model": settings.llm_model, "llm_stats": stats}
