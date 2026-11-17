"""Engine interface and the transcript helpers both engines share.

An engine turns a 16 kHz mono WAV into words with start/end times and speaker
labels. Word dicts: {"w": text, "s": start, "e": end, "spk": raw speaker id}.
"""
import re
from collections import Counter

from ..render import words_to_segments


class EngineError(RuntimeError):
    pass


class Engine:
    name = "base"
    label = ""

    def transcribe(self, wav, workdir, language=None, log=print):
        """Return a transcript dict (see finish())."""
        raise NotImplementedError

    def info(self):
        return {"name": self.name, "label": self.label}


def _spk_num(label):
    m = re.search(r"(\d+)$", label)
    return int(m.group(1)) if m else 0


def relabel(words):
    """Rename raw speaker ids to "Speaker 1..N" in order of first appearance."""
    mapping = {}
    for w in words:
        raw = w.get("spk")
        if raw is None:
            continue
        if raw not in mapping:
            mapping[raw] = f"Speaker {len(mapping) + 1}"
        w["spk"] = mapping[raw]
    for w in words:
        w.setdefault("spk", "Speaker 1")
        if w["spk"] is None:
            w["spk"] = "Speaker 1"
    return words, mapping


def finish(words, *, language, duration, engine, notes=(), scope="meeting", timings=None,
           resources=None, extra=None):
    words = [w for w in words if (w.get("w") or "").strip()]
    words.sort(key=lambda w: (w["s"], w["e"]))
    words, _ = relabel(words)
    speakers = sorted({w["spk"] for w in words}, key=_spk_num)
    return {
        "version": 1,
        "language": language,
        "duration_s": round(duration, 3),
        "engine": engine,
        "speakers": speakers,
        "speaker_scope": scope,
        "notes": list(notes),
        "words": [{"w": w["w"], "s": round(w["s"], 3), "e": round(w["e"], 3), "spk": w["spk"],
                   **({"p": w["p"]} if w.get("p") is not None else {})} for w in words],
        "segments": words_to_segments(words),
        "timings": timings or {},
        "resources": resources or {},
        **(extra or {}),
    }


def assign_speakers(words, turns, max_gap=2.0):
    """Give each word the speaker of the diarization turn it overlaps most.
    turns: [(start, end, speaker)]. Words in gaps take the nearest turn within
    max_gap seconds, else the previous word's speaker."""
    turns = sorted(turns)
    prev = None
    j = 0
    for w in words:
        mid = (w["s"] + w["e"]) / 2
        while j < len(turns) and turns[j][1] < w["s"] - max_gap:
            j += 1
        best, best_ov, best_dist = None, 0.0, None
        for t in turns[j:]:
            if t[0] > w["e"] + max_gap:
                break
            ov = min(w["e"], t[1]) - max(w["s"], t[0])
            if ov > best_ov:
                best, best_ov = t[2], ov
            elif best_ov <= 0:
                dist = 0 if t[0] <= mid <= t[1] else min(abs(mid - t[0]), abs(mid - t[1]))
                if dist <= max_gap and (best_dist is None or dist < best_dist):
                    best, best_dist = t[2], dist
        w["spk"] = best if best is not None else prev
        prev = w["spk"]
    return words


def plan_windows(duration, max_len, overlap):
    """Windows of at most max_len seconds that overlap by `overlap` seconds."""
    if duration <= max_len:
        return [(0.0, duration)]
    windows, start = [], 0.0
    while True:
        end = min(duration, start + max_len)
        windows.append((start, end))
        if end >= duration:
            return windows
        start = end - overlap


def _norm(text):
    return re.sub(r"\W+", "", text.lower())


def merge_windows(parts):
    """Merge per-window results into one word list.

    parts: [(start, end, words)] with absolute word times and raw speaker ids
    that are only unique within their window. Speakers are linked across
    windows by matching the words both windows heard in their overlap; speakers
    that cannot be linked get a new label. Returns (words, links)."""
    merged, links, next_id = [], [], 0
    prev_words, prev_map = None, {}
    for k, (start, end, words) in enumerate(parts):
        words = [dict(w) for w in words]
        if k == 0:
            mapping = {}
            for w in words:
                if w["spk"] not in mapping:
                    mapping[w["spk"]] = f"g{next_id}"
                    next_id += 1
            for w in words:
                w["spk"] = mapping[w["spk"]]
            merged, prev_words, prev_end = words, words, end
            continue
        ov_start, ov_end = start, prev_end
        cut = (ov_start + ov_end) / 2
        prev_ov = [w for w in prev_words if w["s"] >= ov_start]
        pairs = Counter()
        for w in (w for w in words if w["s"] < ov_end):
            key = _norm(w["w"])
            cand = [p for p in prev_ov if abs(p["s"] - w["s"]) < 0.6 and _norm(p["w"]) == key]
            if cand:
                pairs[(cand[0]["spk"], w["spk"])] += 1
        mapping, used = {}, set()
        for (g, raw), n in pairs.most_common():
            if raw not in mapping and g not in used and n >= 3:
                mapping[raw] = g
                used.add(g)
        for w in words:
            if w["spk"] not in mapping:
                mapping[w["spk"]] = f"g{next_id}"
                next_id += 1
        for w in words:
            w["spk"] = mapping[w["spk"]]
        links.append({"window": k + 1, "start": round(start, 1), "matched_words": sum(pairs.values()),
                      "linked": sum(1 for raw, g in mapping.items() if g in used),
                      "new": sum(1 for raw, g in mapping.items() if g not in used)})
        merged = [w for w in merged if w["s"] < cut] + [w for w in words if w["s"] >= cut]
        prev_words, prev_end = words, end
    return merged, links


def segments_from_words(words):
    return words_to_segments(words)


def speaker_count(words):
    return len(Counter(w["spk"] for w in words))
