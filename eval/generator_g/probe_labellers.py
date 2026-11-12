"""Probe step 0c: one probe text per labeller (the plain text of the half's author probe 1; not part of the split).
Runs the blind-labelling prompt and the pointer-check prompt; prints counts only."""
from __future__ import annotations

import json
import sys

import agreement as A
import ali
import common as C
import prompts
import validate as VAL
from markup import parse_markup


def main(half: str) -> int:
    half = half.upper()
    h = C.Half(half)
    model = h.models["labeller"]
    st = C.settings(model)
    # take the newest successful author probe 1 of this half
    cand = sorted((C.ROOT / "results" / half.lower() / "probe").glob(f"PROBE-{half}-1-*.json"))
    raw = None
    for p in reversed(cand):
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("ok"):
            raw = d["content"]
            break
    if raw is None:
        print("no author probe output for this half")
        return 2
    parsed = parse_markup(raw)
    plain = parsed["plain"]
    gold = [{"start": s["start"], "end": s["end"], "label": s["label"]} for s in parsed["spans"]]
    try:
        r = ali.call(half=half, phase="probe", case_id=f"LABEL-{half}", model=model, force=True,
                     messages=[{"role": "system", "content": prompts.taxonomy_text()},
                               {"role": "user", "content": prompts.label_user(plain)}],
                     max_tokens=int(st.get("label_max_tokens", 6000)), temperature=0, thinking=st["thinking"],
                     stream=st["stream"], extra=st.get("extra"))
    except ali.AliError as exc:
        print(model, "STOP", type(exc).__name__, str(exc))
        return 2
    u = r["usage"] or {}
    print("label", model, "ok" if r["ok"] else "FAILED", r["http_status"], r["finish_reason"], "prompt", u.get("prompt_tokens"),
          "completion", u.get("completion_tokens"), "reasoning", (u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
          "secs", r.get("seconds"), "err", r["error"])
    if r["ok"]:
        d = A.labeller_spans(plain, r["content"])
        ex = {(s["start"], s["end"], s["label"]) for s in d["spans"]}
        g = {(s["start"], s["end"], s["label"]) for s in gold}
        print("   labeller spans", len(d["spans"]), "gold spans", len(g), "exact matches", len(ex & g), "drift", d["drift"],
              "dropped", d["dropped"], "markup errors", d["errors"])
    try:
        r2 = ali.call(half=half, phase="probe", case_id=f"POINTER-{half}", model=model, force=True,
                      messages=[{"role": "system", "content": prompts.POINTER_SYSTEM},
                                {"role": "user", "content": prompts.pointer_user(plain)}],
                      max_tokens=int(st.get("pointer_max_tokens", 400)), temperature=0, thinking=st["thinking"],
                      stream=st["stream"], extra=st.get("extra"))
    except ali.AliError as exc:
        print(model, "STOP", type(exc).__name__, str(exc))
        return 2
    u = r2["usage"] or {}
    import pipeline as checks
    flag = checks.parse_answer(r2["content"]) if r2["ok"] else None
    print("pointer", model, "ok" if r2["ok"] else "FAILED", r2["finish_reason"], "prompt", u.get("prompt_tokens"), "completion",
          u.get("completion_tokens"), "parsed flag", None if flag is None else flag[0], "reason_chars", None if flag is None else len(flag[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))