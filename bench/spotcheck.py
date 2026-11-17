"""Spot check: action items from our summary next to the action sentences of
AMI's human abstractive summary, for three meetings. The box's LLM marks which
human actions our list covers (a rough recall, not a metric to quote alone).

    python -m bench.spotcheck [--engine A-iq4] [--set ami-sdm] [--meetings ES2004a,IS1009b,TS3003c]

Writes data/bench/spotcheck.md and spotcheck.json."""
import argparse
import json
from pathlib import Path

from recorder.config import Settings
from recorder.llm import chat_json
from recorder.render import build_actions
from recorder.summarize import summarize

ROOT = Path("/data/bench")
JUDGE = {"type": "object", "properties": {"covered": {"type": "array", "items": {"type": "boolean"}},
                                          "supported": {"type": "array", "items": {"type": "boolean"}}},
         "required": ["covered", "supported"]}
JUDGE_SYSTEM = """You compare two lists of action items from the same meeting.
"human" was written by an annotator, "ours" by a system. For each human item, say whether some item of ours
asks for the same thing (covered). For each item of ours, say whether it matches something the human list
asks for (supported). Wording may differ; owners are labelled differently and do not matter."""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="A-iq4")
    ap.add_argument("--set", default="ami-sdm")
    ap.add_argument("--meetings", default="ES2004a,IS1009b,TS3003c")
    a = ap.parse_args(argv)
    s = Settings()
    md, out = [f"# Action items: ours ({a.engine}, {a.set}) vs AMI's human summaries", ""], []
    for m in a.meetings.split(","):
        run = ROOT / "runs" / a.engine / a.set / m
        meta = json.loads((ROOT / "datasets" / a.set / m / "meta.json").read_text())
        human = (meta.get("abstractive") or {}).get("actions") or []
        cached = run / "summary.json"
        summary = json.loads(cached.read_text()) if cached.exists() else summarize(json.loads((run / "transcript.json").read_text()), s)
        cached.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
        ours = [x["text"] for x in build_actions(m, summary, {})["items"]]
        judge, _ = chat_json(s, JUDGE_SYSTEM, json.dumps({"human": human, "ours": ours}), JUDGE, timeout=600)
        covered, supported = judge.get("covered", []), judge.get("supported", [])
        out.append({"meeting": m, "human": human, "ours": ours, "covered": covered, "supported": supported})
        md += [f"## {m}", "", f"AMI human actions ({sum(covered)}/{len(human)} covered by ours):", ""]
        md += [f"- {'covered' if i < len(covered) and covered[i] else 'missed'}: {h}" for i, h in enumerate(human)] or ["- (none)"]
        md += ["", f"Our action items ({sum(supported)}/{len(ours)} match a human one):", ""]
        md += [f"- {'match' if i < len(supported) and supported[i] else 'extra'}: {o}" for i, o in enumerate(ours)] or ["- (none)"]
        md.append("")
    (ROOT / "spotcheck.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    (ROOT / "spotcheck.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
