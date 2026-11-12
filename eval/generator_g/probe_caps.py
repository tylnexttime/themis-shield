"""Probe step 0d: which max_tokens values each model accepts (tiny prompt; a few tokens each)."""
import json
import ali
import common as C

for half, key in (("R", "author"), ("S", "author"), ("T", "author"), ("R", "labeller"), ("S", "labeller"), ("T", "labeller")):
    model = C.HALVES[half][key]
    st = C.settings(model)
    accepted = []
    for mt in (16000, 32000):
        try:
            r = ali.call(half=half, phase="probe", case_id=f"caps-{model}-{mt}", model=model, force=True, max_tokens=mt,
                         messages=[{"role": "user", "content": "Reply with the single word OK."}],
                         temperature=0 if model != "kimi-k3" else None, thinking=st["thinking"], stream=True, extra=st.get("extra"))
            accepted.append((mt, "ok" if r["ok"] else (r["error"] or {}).get("code")))
        except ali.AliError as exc:
            accepted.append((mt, type(exc).__name__))
    print(model, accepted)