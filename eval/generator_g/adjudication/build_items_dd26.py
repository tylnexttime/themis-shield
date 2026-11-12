"""Second adjudication batch (DD-26): the five split G cases where 0.4.0's negative-statements, unique-roles and
quasi-cells switches break their guards. Writes items-dd26.jsonl (items I044-I048)."""
import json, sys
from pathlib import Path
sys.path.insert(0, "C:/dev/themis-nov13")
from themis_shield.detectors.statements import negative_statement_spans
from themis_shield.detectors.propagate import unique_role_spans, quasi_cell_spans
from themis_shield.detectors.contracts import DetectionFinding  # noqa: F401
sys.path.insert(0, "C:/dev/themis-nov13/tests")
import test_quasi_cells as tq
from themis_shield.detectors.labels import Label
REPO = Path("C:/dev/themis-nov13/eval/cases")
items = []
for h in "rt":
    for line in (REPO / f"test-g-{h}.adjudicated.jsonl").read_text(encoding="utf-8").splitlines():
        c = json.loads(line); t = c["text"]
        ctx = lambda a, b: t[max(0, a - 500):b + 300]
        for it in negative_statement_spans(t):
            a, b = it.start, it.end
            gold = [s for s in c["spans"] if s["start"] < b and a < s["end"]]
            if not any(s["label"] == it.primary_label.value for s in gold):
                items.append({"case": c["id"], "switch": "negative-statements", "switch_span": {"start": a, "end": b,
                              "label": it.primary_label.value, "value": t[a:b]}, "gold_overlapping": gold,
                              "contains_pi": c["case_level"]["contains_pi"], "context": ctx(a, b)})
        for it in unique_role_spans(t):
            a, b = it.start, it.end
            gold = [s for s in c["spans"] if s["start"] < b and a < s["end"]]
            if not any(s["label"] == "QUASI_ID" for s in gold):
                items.append({"case": c["id"], "switch": "unique-roles", "switch_span": {"start": a, "end": b,
                              "label": "QUASI_ID", "value": t[a:b]}, "gold_overlapping": gold,
                              "contains_pi": c["case_level"]["contains_pi"], "context": ctx(a, b)})
        golds = [s for s in c["spans"] if s["label"] == "QUASI_ID" and len(s["value"]) >= 8]
        halves = tuple(tq.finding(s["start"], s["start"] + len(s["value"]) // 2, Label.QUASI_ID, "llm", 1) for s in golds)
        for it in quasi_cell_spans(t, halves):
            a, b = it.start, it.end
            hn = [n for n in c["hard_negatives"] if n["start"] < b and a < n["end"]]
            if hn:
                items.append({"case": c["id"], "switch": "quasi-cells", "switch_span": {"start": a, "end": b,
                              "label": "QUASI_ID", "value": t[a:b]}, "hard_negatives_overlapping": hn,
                              "gold_overlapping": [s for s in c["spans"] if s["start"] < b and a < s["end"]],
                              "contains_pi": c["case_level"]["contains_pi"], "context": ctx(a, b)})
for i, it in enumerate(items, 44):
    it["item"] = f"I{i:03d}"; it["type"] = "dd26"
Path("C:/dev/themis-work/adjudicate-g/items-dd26.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in items), encoding="utf-8")
print(len(items), [(x["item"], x["case"], x["switch"]) for x in items])
