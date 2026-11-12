"""Runner: bounded-concurrency execution of one phase's requests for one half, with the substitution rule and the token
guards, plus the usage summary written after every phase."""
from __future__ import annotations

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import ali
import common as C


def run_calls(h: C.Half, role: str, phase: str, jobs: list[dict], temp_key: str = "author_temperature",
              temp_default=0.7) -> tuple[dict, str | None]:
    """jobs: dicts with case_id, messages, max_tokens. Returns (results by case id, stop reason or None).
    A call whose result file already holds a response is not repeated. If the model is refused or out of quota, the
    half's `role` model is substituted (recorded) and the remaining jobs run on the substitute. A token guard stops
    the phase: whatever finished is returned with the stop reason."""
    results: dict[str, dict] = {}
    pending = []
    for j in jobs:
        prev = ali.load_result(h.L, phase, j["case_id"])
        if prev and prev.get("ok"):
            results[j["case_id"]] = prev
        else:
            pending.append(j)
    stop = None
    while pending and not stop:
        model = h.models[role]
        st = C.settings(model)
        temp = st.get(temp_key, temp_default)
        bad: Exception | None = None

        def work(job, model=model, st=st, temp=temp):
            return ali.call(half=h.L, phase=phase, case_id=job["case_id"], model=model, messages=job["messages"],
                            max_tokens=job["max_tokens"], temperature=temp, thinking=st["thinking"], stream=st["stream"],
                            extra=st.get("extra"))

        with ThreadPoolExecutor(max_workers=ali.MAX_INFLIGHT) as ex:
            futs = {ex.submit(work, j): j for j in pending}
            for fut in as_completed(futs):
                j = futs[fut]
                try:
                    results[j["case_id"]] = fut.result()
                except (ali.QuotaExhausted, ali.ModelRefused) as exc:
                    bad = bad or exc
                    for f in futs:
                        f.cancel()
                except ali.GuardStop as exc:
                    stop = stop or str(exc)
                    for f in futs:
                        f.cancel()
                except Exception as exc:  # noqa: BLE001  (never let a message carry the key)
                    results[j["case_id"]] = {"ok": False, "error": {"kind": "exception", "code": type(exc).__name__,
                                                                    "message": ali.scrub(exc)[:200]}, "content": ""}
        # a job that finished (even with a non-quota failure) is settled; the rest were cancelled or hit quota
        pending = [j for j in pending if j["case_id"] not in results]
        if bad is not None and not stop and pending:
            code = getattr(bad, "code", "")
            reason = "quota exhausted / access denied" if isinstance(bad, ali.QuotaExhausted) else "model code refused"
            new = C.substitute(h.L, role, phase, reason, str(code))
            C.status_line(f"SUBSTITUTION half {h.L} {role} {model} -> {new} ({reason}, {code}) in phase {phase}")
            continue
        break
    return results, stop


def usage_summary() -> dict:
    rows = ali.read_usage()
    per: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for r in rows:
        u = r.get("usage") or {}
        phase = r.get("phase", "?")
        d = per[r["model"]][phase]
        d["calls"] += 1
        d["http_ok"] += 1 if r.get("http_status") == 200 else 0
        p, c = u.get("prompt_tokens") or 0, u.get("completion_tokens") or 0
        t = u.get("total_tokens") or (p + c) or r.get("est_total_tokens") or 0
        d["prompt"] += p
        d["completion"] += c
        d["reasoning"] += (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        d["cached"] += (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        d["total"] += t
    out = {"models": {}, "all_total": 0}
    for m, phases in per.items():
        tot = defaultdict(int)
        for ph, d in phases.items():
            for k, v in d.items():
                tot[k] += v
        out["models"][m] = {"phases": {ph: dict(d) for ph, d in sorted(phases.items())}, "cumulative": dict(tot),
                            "free_quota_at_start": C.FREE_QUOTA.get(m)}
        out["all_total"] += tot["total"]
    return out


def write_usage_summary() -> dict:
    s = usage_summary()
    (C.ROOT / "usage-summary.json").write_text(json.dumps(s, indent=1, sort_keys=True), encoding="utf-8")
    lines = ["# Usage summary (Alibaba Model Studio; tokens as returned in `usage`)", "",
             "| model | phase | calls | prompt | completion | reasoning (in completion) | cached (in prompt) | total |",
             "|---|---|---|---|---|---|---|---|"]
    for m, d in sorted(s["models"].items()):
        for ph, x in d["phases"].items():
            lines.append(f"| {m} | {ph} | {x['calls']} | {x['prompt']} | {x['completion']} | {x['reasoning']} | {x['cached']} | {x['total']} |")
        c = d["cumulative"]
        q = d.get("free_quota_at_start")
        lines.append(f"| **{m}** | **cumulative** | {c['calls']} | {c['prompt']} | {c['completion']} | {c['reasoning']} | {c['cached']} | "
                     f"**{c['total']}**" + (f" (free quota at start {q:,})" if q else "") + " |")
    lines += ["", f"All models together: {s['all_total']:,} total tokens (guards: 2,000,000 per model, 8,000,000 all)."]
    (C.ROOT / "usage-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return s


def cumulative_line() -> str:
    s = usage_summary()
    return "tokens " + ", ".join(f"{m}={d['cumulative']['total']}" for m, d in sorted(s["models"].items()))