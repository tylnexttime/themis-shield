"""Write split G's adjudicated gold beside the sealed files, never editing them.

Usage (from the repository root): python eval/generator_g/adjudication/apply_adjudication.py
Reads eval/cases/test-g-<r|t>.jsonl (sealed, published) and the adjudicator's decisions.jsonl in this folder, applies
every change of every item whose verdict is gold_error (remove_span, relabel, reboundary, add_span, set_case_level),
checks each against the text, and writes eval/cases/test-g-<r|t>.adjudicated.jsonl (same format; a half with no change
is written byte-identical to its sealed file). Prints counts only."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CASES = ROOT / "eval" / "cases"
HERE = Path(__file__).resolve().parent
decisions = [json.loads(l) for l in (HERE / "decisions.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
changes = {}
for d in decisions:
    if d["verdict"] == "gold_error":
        changes.setdefault(d["case"], []).extend((d["item"], c) for c in d["changes"])
for half in "rt":
    src = CASES / f"test-g-{half}.jsonl"
    raw = src.read_text(encoding="utf-8")
    out, n = [], 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        todo = changes.get(case["id"], [])
        if not todo:
            out.append(line)
            continue
        text, spans = case["text"], case["spans"]
        for item, c in todo:
            key = (c["start"], c["end"], c.get("label"))
            match = [s for s in spans if (s["start"], s["end"], s["label"]) == key]
            if c["op"] == "add_span":
                assert not match and text[c["start"]:c["end"]] == c["value"], (item, "add_span does not fit")
                assert not any(s["start"] < c["end"] and c["start"] < s["end"] for s in spans), (item, "overlap")
                spans.append({"start": c["start"], "end": c["end"], "label": c["label"], "value": c["value"]})
            elif c["op"] == "set_case_level":
                case["case_level"] = {"contains_pi": c["contains_pi"], "sensitive": c.get("sensitive", [])}
            else:
                assert len(match) == 1, (item, "span not found", key)
                s = match[0]
                if c["op"] == "remove_span":
                    spans.remove(s)
                elif c["op"] == "relabel":
                    s["label"] = c["new_label"]
                elif c["op"] == "reboundary":
                    s["start"], s["end"] = c["new_start"], c["new_end"]
                    s["value"] = text[s["start"]:s["end"]]
            n += 1
        spans.sort(key=lambda s: (s["start"], s["end"]))
        out.append(json.dumps(case, ensure_ascii=False))
    dst = CASES / f"test-g-{half}.adjudicated.jsonl"
    dst.write_text(raw if n == 0 else "\n".join(out) + "\n", encoding="utf-8", newline="\n")
    print(f"half {half}: {n} changes applied -> {dst.name}")
