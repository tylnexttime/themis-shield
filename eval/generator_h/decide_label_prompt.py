"""Decide ONCE, before labelling, from token counts only, whether both labellers get the full taxonomy.md or split G's
compact taxonomy: if the projected kimi-k3.1-pro total for half A with the full taxonomy exceeds 370,000 tokens, both get
the compact one. Inputs: the probes' token counts (probe/label-probe-sizes.json), half A's plan (planned characters per
case), kimi tokens already used (usage.jsonl). Writes state/label_prompt.json (numbers only).

    python decide_label_prompt.py
"""
import json

import common as C
import llm
from store import load_entries

sizes = json.loads((C.ROOT / "probe" / "label-probe-sizes.json").read_text(encoding="utf-8"))
k = "kimi-k3.1-pro"
doc, triv_full, triv_comp = sizes[f"{k}|full|doc"], sizes[f"{k}|full|trivial"], sizes[f"{k}|compact|trivial"]
ents = list(load_entries(C.Half("A")).values())
n = len(ents)
avg_chars = sum((e["chars"][0] + e["chars"][1]) / 2 for e in ents) / n
r_doc = (doc["prompt"] - triv_full["prompt"]) / doc["chars"]  # prompt tokens per document character
r_out = doc["completion"] / doc["chars"]  # completion tokens per document character
per_full = triv_full["prompt"] + (r_doc + r_out) * avg_chars
per_comp = triv_comp["prompt"] + (r_doc + r_out) * avg_chars
used = llm.totals()[0].get(k, 0)
proj_full, proj_comp = used + n * per_full, used + n * per_comp
mode = "compact" if proj_full > 370_000 else "full"
out = {"mode": mode, "rule": "compact if the projected kimi-k3.1-pro total for half A with the full taxonomy exceeds 370,000 tokens",
       "planned_cases_half_a": n, "avg_planned_chars": round(avg_chars), "kimi_tokens_used_so_far": used,
       "probe_prompt_tokens_full_taxonomy_trivial_text": triv_full["prompt"], "probe_prompt_tokens_compact_taxonomy_trivial_text": triv_comp["prompt"],
       "probe_prompt_tokens_per_doc_char": round(r_doc, 4), "probe_completion_tokens_per_doc_char": round(r_out, 4),
       "projected_tokens_per_case_full": round(per_full), "projected_tokens_per_case_compact": round(per_comp),
       "projected_total_full": round(proj_full), "projected_total_compact": round(proj_comp)}
C.jsave(C.ROOT / "state" / "label_prompt.json", out)
print(json.dumps(out))
