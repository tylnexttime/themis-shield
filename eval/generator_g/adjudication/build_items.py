"""Build the adjudication scope for split G (published development data): every gold span 0.4.0's candidate arm
missed (no prediction overlaps it), every clean case (contains_pi false) the candidate held, and the blind
labeller's disagreements touching either. Writes items.jsonl (one item per line) and scope.txt (counts)."""
import json, sys
from pathlib import Path
sys.path.insert(0, "C:/dev/themis-work/split-g")
import agreement as A
OUT = Path("C:/dev/themis-work/adjudicate-g")
REPO = Path("C:/dev/themis-shield/eval/cases")
RUNS = Path("C:/dev/themis-work/split-g-run/runs")
RES = Path("C:/dev/themis-work/split-g/results")
ov = lambda a, b: a["start"] < b["end"] and b["start"] < a["end"]
items, n = [], {"missed": 0, "held_clean": 0, "lab_touching": 0}
for h in "rt":
    gold = [json.loads(l) for l in (REPO / f"test-g-{h}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    preds = {json.loads(l)["id"]: json.loads(l) for l in (RUNS / f"g-{h}-cand/predictions.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}
    decs = {json.loads(l)["id"]: json.loads(l) for l in (RUNS / f"g-{h}-cand/decisions.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}
    for c in gold:
        P = [p for p in preds[c["id"]].get("spans", []) if isinstance(p.get("start"), int)]
        d = decs[c["id"]]
        lf = RES / h / "label" / f"{c['id']}.json"
        L = A.labeller_spans(c["text"], json.loads(lf.read_text(encoding="utf-8"))["content"])["spans"] if lf.exists() else None
        lab_v = lambda s: {**s, "value": c["text"][s["start"]:s["end"]]}
        base = {"case": c["id"], "genre": c["genre"], "decision": d["decision"], "reasons": d.get("reasons", [])}
        for g in c["spans"]:
            if not any(ov(p, g) for p in P):
                touching = [lab_v(s) for s in (L or []) if ov(s, g)]
                items.append({**base, "type": "missed", "gold": g,
                              "labeller_overlapping": touching, "labeller_has_case": L is not None,
                              "context": c["text"][max(0, g["start"] - 400):g["end"] + 400]})
                n["missed"] += 1; n["lab_touching"] += len(touching)
        if not c["case_level"]["contains_pi"] and d["decision"] != "RELEASE":
            items.append({**base, "type": "held_clean",
                          "candidate_spans": [{"start": p["start"], "end": p["end"], "label": p.get("label"),
                                               "value": c["text"][p["start"]:p["end"]]} for p in P],
                          "labeller_spans": [lab_v(s) for s in (L or [])], "labeller_has_case": L is not None,
                          "hard_negatives": c.get("hard_negatives", []), "chars": len(c["text"])})
            n["held_clean"] += 1; n["lab_touching"] += len(L or [])
for i, it in enumerate(items, 1):
    it["item"] = f"I{i:03d}"
(OUT / "items.jsonl").write_text("".join(json.dumps(it, ensure_ascii=False) + "\n" for it in items), encoding="utf-8")
(OUT / "scope.txt").write_text(json.dumps(n) + "\n", encoding="utf-8")
print(n, "items", len(items))
