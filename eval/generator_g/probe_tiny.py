"""Phase probe, step 0a: one tiny request per model code (a few tokens) to confirm it is accepted. Not part of the set."""
import json
import sys

import ali

JOBS = [("R", "kimi-k3"), ("R", "deepseek-v4-pro-0813"), ("S", "qwen3.8-max-0902"), ("S", "kimi-k3.1-pro"),
        ("T", "glm-5.3"), ("T", "deepseek-v4.1-flash-1017")]
thinking = None if len(sys.argv) < 2 else (sys.argv[1] == "on")
tag = "tiny" if thinking is None else ("tiny-think" if thinking else "tiny-nothink")
for half, model in JOBS:
    try:
        r = ali.call(half=half, phase="probe", case_id=f"{tag}-{model}", model=model, force=True, max_tokens=32,
                     messages=[{"role": "user", "content": "Reply with the single word OK."}],
                     temperature=0, thinking=(False if thinking is None else thinking), stream=True)
        u = r["usage"] or {}
        print(model, "ok" if r["ok"] else "FAILED", r["http_status"], r["finish_reason"], "out_chars", len(r["content"]),
              "usage", json.dumps(u), "reasoning_chars", r.get("reasoning_chars"), "err", r["error"])
    except ali.AliError as exc:
        print(model, "REFUSED:", type(exc).__name__, str(exc))
