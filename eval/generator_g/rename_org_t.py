"""Split G amendment (Alder, 12/11/2026 03:20 UTC, option a): rename half T's one workplace metadata label that hits the
origin denylist to a neutral label, by script. Prints counts and hashes only: never a label, a pattern or a matched string.

    python rename_org_t.py          dry run: prove the hits are only in the `organisation` field of one workplace label
    python rename_org_t.py apply    write halves/t/work/org_label_override.json, rebuild, prove text+spans byte-identical

The override is applied by finalize.valid_cases (case-level `organisation` field only; the plan files, the requests and
the author's documents are untouched).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys

import common as C
import finalize as F
sys.path.insert(0, str(C.ROOT / "validator"))
import denylist_check as D  # noqa: E402

h = C.Half("T")
pats, bad = D.load_patterns(D.DEFAULT_DENYLIST)
assert pats and bad == 0, "denylist unusable"


def ts_hash(c: dict) -> str:
    return hashlib.sha256(json.dumps({"id": c["id"], "text": c["text"], "spans": c["spans"]}, ensure_ascii=False,
                                     sort_keys=True).encode("utf-8")).hexdigest()


def agg(hs: list[str]) -> str:
    return hashlib.sha256("\n".join(hs).encode()).hexdigest()


before_file = C.read_jsonl(h.cases_path)
ovr = h.dir / "work" / "org_label_override.json"
if ovr.exists():
    sys.exit("override already present: rename was applied; nothing to do")
fresh = F.valid_cases(h)
same = json.dumps(before_file, sort_keys=True, ensure_ascii=False) == json.dumps(fresh, sort_keys=True, ensure_ascii=False)
print(f"half T: {len(fresh)} cases; cases.jsonl on disk == rebuild from state: {same}")
assert same

hit_ids, labels, other_field_hits = [], set(), 0
for c in fresh:
    rest = {k: v for k, v in c.items() if k != "organisation"}
    other_field_hits += D.scan_case(rest, pats)
    if D.scan_text(c["organisation"], pats):
        hit_ids.append(c["id"])
        labels.add(c["organisation"])
print(f"cases with a hit in `organisation`: {len(hit_ids)}; distinct labels hit: {len(labels)}; hits in every other field: {other_field_hits}")
assert other_field_hits == 0 and len(labels) == 1
(label,) = labels
m = re.fullmatch(r"Other workplace (\d+): .+", label)
assert m, "hit label is not a workplace label"
k = m.group(1)
same_label = [c["id"] for c in fresh if c["organisation"] == label]
assert same_label == hit_ids
new = f"Other workplace {k}"
assert new not in {c["organisation"] for c in fresh} and not D.scan_text(new, pats)
print(f"the label hit is workplace {k}'s; it carries {len(same_label)} cases; new neutral label: '{new}'")
if sys.argv[1:] != ["apply"]:
    sys.exit(0)

ovr.write_text(json.dumps({"workplace": int(k), "label": new,
                           "why": "origin-denylist hit in the case metadata field `organisation` only; Alder's split G "
                                  "amendment (option a), 12/11/2026"}, indent=1), encoding="utf-8")
after = F.valid_cases(h)
assert [c["id"] for c in after] == [c["id"] for c in fresh]
hb, ha = [ts_hash(c) for c in fresh], [ts_hash(c) for c in after]
eq = sum(1 for x, y in zip(hb, ha) if x == y)
changed_fields = set()
changed = []
for b, a in zip(fresh, after):
    for key in set(b) | set(a):
        if b.get(key) != a.get(key):
            changed_fields.add(key)
            changed.append(b["id"])
deny_after = sum(D.scan_case(c, pats) for c in after)
print(f"text+spans hash identical in {eq}/{len(hb)} cases; aggregate before {agg(hb)} after {agg(ha)}")
print(f"fields changed: {sorted(changed_fields)} in {len(set(changed))} cases (== the hit cases: {sorted(set(changed)) == sorted(hit_ids)}); denylist hits after: {deny_after}")
assert eq == len(hb) and changed_fields <= {"organisation"} and sorted(set(changed)) == sorted(hit_ids) and deny_after == 0
F.write_cases(h.cases_path, after)
print("halves/t/cases.jsonl rewritten from state with the override; done")
