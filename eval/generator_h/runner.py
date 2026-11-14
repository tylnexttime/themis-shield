"""Runner: bounded-concurrency execution of one phase's requests for one half (both providers), with the spare-author rule
and the guards, plus the usage summary (tokens per model and phase; US$ cost and serving providers for OpenRouter)
written after every phase."""
from __future__ import annotations

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import common as C
import llm


def run_calls(h: C.Half, role: str, phase: str, jobs: list[dict], temp_key: str = "author_temperature",
              temp_default=0.7) -> tuple[dict, str | None]:
    """jobs: dicts with case_id, messages, max_tokens (submitted in the given order). Returns (results by case id, stop
    reason or None). A call whose result file already holds a response is not repeated. If the half's AUTHOR model code is
    refused, the spare author (qwen3.8-flash) takes over (recorded) and the remaining jobs run on it. A guard stop, a quota
    error or a refusal of any other role stops the phase: whatever finished is returned with the stop reason."""
    results: dict[str, dict] = {}
    pending = []
    for j in jobs:
        prev = llm.load_result(h.L, phase, j["case_id"])
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
            return llm.call(half=h.L, phase=phase, case_id=job["case_id"], model=model, messages=job["messages"],
                            max_tokens=job["max_tokens"], temperature=temp, thinking=st["thinking"], stream=st["stream"],
                            extra=st.get("extra"))

        with ThreadPoolExecutor(max_workers=llm.MAX_INFLIGHT) as ex:
            futs = {ex.submit(work, j): j for j in pending}
            for fut in as_completed(futs):
                j = futs[fut]
                try:
                    results[j["case_id"]] = fut.result()
                except (llm.QuotaExhausted, llm.ModelRefused) as exc:
                    bad = bad or exc
                    for f in futs:
                        f.cancel()
                except (llm.GuardStop, llm.NotAllowed) as exc:
                    stop = stop or str(exc)
                    for f in futs:
                        f.cancel()
                except Exception as exc:  # noqa: BLE001  (never let a message carry a key)
                    results[j["case_id"]] = {"ok": False, "error": {"kind": "exception", "code": type(exc).__name__,
                                                                    "message": llm.scrub(exc)[:200]}, "content": ""}
        pending = [j for j in pending if j["case_id"] not in results]
        if bad is not None and not stop and pending:
            code = getattr(bad, "code", "")
            if isinstance(bad, llm.ModelRefused) and role == "author":
                new = C.substitute(h.L, role, phase, "model code refused", str(code))
                C.status_line(f"SUBSTITUTION half {h.L} {role} {model} -> {new} (model code refused, {code}) in phase {phase}")
                continue
            what = "quota exhausted / access denied" if isinstance(bad, llm.QuotaExhausted) else "model code refused"
            stop = f"{what} for {role} model {model} (code {code}) in phase {phase}; no substitute for this role"
        break
    return results, stop


def usage_summary() -> dict:
    rows = llm.read_usage()
    per: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    serving: dict[str, set] = defaultdict(set)
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
        if r.get("provider") == "openrouter":
            d["cost_usd"] += llm.row_cost(r)
            if r.get("serving_provider"):
                serving[r["model"]].add(r["serving_provider"])
    out = {"models": {}, "all_total": 0, "openrouter_cost_usd": 0.0}
    for m, phases in per.items():
        tot = defaultdict(float)
        for ph, d in phases.items():
            for k, v in d.items():
                tot[k] += v
        ints = lambda dd: {k: (round(v, 6) if k == "cost_usd" else int(v)) for k, v in dd.items()}  # noqa: E731
        out["models"][m] = {"provider": C.provider_of(m), "phases": {ph: ints(d) for ph, d in sorted(phases.items())},
                            "cumulative": ints(tot), "free_quota_at_start": C.FREE_QUOTA.get(m),
                            "serving_providers": sorted(serving.get(m, []))}
        out["all_total"] += int(tot["total"])
        out["openrouter_cost_usd"] += tot["cost_usd"]
    out["openrouter_cost_usd"] = round(out["openrouter_cost_usd"], 6)
    return out


def write_usage_summary() -> dict:
    s = usage_summary()
    (C.ROOT / "usage-summary.json").write_text(json.dumps(s, indent=1, sort_keys=True), encoding="utf-8")
    lines = ["# Usage summary (tokens as returned in `usage`; OpenRouter cost as reported)", "",
             "| model | phase | calls | prompt | completion | reasoning (in completion) | cached (in prompt) | total | US$ |",
             "|---|---|---|---|---|---|---|---|---|"]
    for m, d in sorted(s["models"].items()):
        for ph, x in d["phases"].items():
            usd = f"{x.get('cost_usd', 0):.4f}" if d["provider"] == "openrouter" else ""
            lines.append(f"| {m} | {ph} | {x['calls']} | {x['prompt']} | {x['completion']} | {x['reasoning']} | {x['cached']} | {x['total']} | {usd} |")
        c = d["cumulative"]
        q = d.get("free_quota_at_start")
        usd = f"**{c.get('cost_usd', 0):.4f}**" if d["provider"] == "openrouter" else ""
        extra = (f" (free quota at start {q:,})" if q else "") + (f" (serving: {', '.join(d['serving_providers'])})" if d["serving_providers"] else "")
        lines.append(f"| **{m}** | **cumulative** | {c['calls']} | {c['prompt']} | {c['completion']} | {c['reasoning']} | {c['cached']} | "
                     f"**{c['total']}**{extra} | {usd} |")
    lines += ["", f"All models together: {s['all_total']:,} total tokens. OpenRouter spend: US${s['openrouter_cost_usd']:.4f} "
              f"(guard: refuse at US$4.80, hard limit US$5.00)."]
    (C.ROOT / "usage-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return s


def cumulative_line() -> str:
    s = usage_summary()
    return ("tokens " + ", ".join(f"{m}={d['cumulative']['total']}" for m, d in sorted(s["models"].items()))
            + f" | OpenRouter US${s['openrouter_cost_usd']:.4f}")
