"""Shared helpers for the split-F pipeline: paths, client, ledger booking, cost guard, batch submit/wait/collect.

The API key is never read, printed or logged here: anthropic.Anthropic() picks it up from the environment itself.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = r"C:\dev\themis-work\api-credit-2026-11-11\.venv\Scripts\python.exe"
LEDGER = r"C:\dev\themis-work\api-credit-2026-11-11\tools\ledger.py"
REPORT = Path(r"C:\dev\themis-work\api-credit-2026-11-11\workers\w20-splitf-REPORT.md")
BUDGET_USD = 200.0
AUTHOR_MODEL = "claude-opus-5-5"
CHECK_MODEL = "claude-sonnet-5-5"
# (input, output, cache read) USD per million tokens, list prices as used by the ledger; batch = 50 %
PRICES = {"claude-opus-5-5": (4.00, 20.00, 0.20), "claude-sonnet-5-5": (2.00, 10.00, 0.10)}
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

for _d in ("requests", "results", "state", "rounds", "sealed", "profiles/institution", "inputs"):
    (ROOT / _d).mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------ files
def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def jline(obj) -> str:
    """One JSON line that line-based readers cannot split (U+0085, U+2028, U+2029 escaped)."""
    s = json.dumps(obj, ensure_ascii=False, separators=(", ", ": "))
    return s.replace("\u0085", "\\u0085").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def write_jsonl(path, rows) -> None:
    Path(path).write_text("".join(jline(r) + "\n" for r in rows), encoding="utf-8", newline="\n")


def read_jsonl(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").split("\n") if l.strip()]


def load_state(name: str) -> dict:
    p = ROOT / "state" / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_state(name: str, st: dict) -> None:
    (ROOT / "state" / f"{name}.json").write_text(json.dumps(st, indent=1), encoding="utf-8")


def log_round(name: str, data: dict) -> None:
    """Counts only: one JSON file per round in rounds/."""
    (ROOT / "rounds" / f"{name}.json").write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")


# ------------------------------------------------------------------ client and ledger
def client():
    import anthropic
    return anthropic.Anthropic(base_url="https://api.anthropic.com", max_retries=3)


def ledger_total() -> float:
    r = subprocess.run([PY, LEDGER, "total"], capture_output=True, text=True)
    return float(r.stdout.strip().split()[-1].lstrip("$"))


def sum_usage(usages) -> dict:
    return {k: int(sum((u.get(k) or 0) for u in usages)) for k in USAGE_KEYS}


def book(name: str, model: str, effort: str, purpose: str, usage_path, batch: bool) -> None:
    cmd = [PY, LEDGER, "api", name, model, effort, purpose, str(usage_path)] + (["--batch"] if batch else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    tail = (r.stdout.strip().splitlines() or [""])[-1][:160]
    print(f"ledger {name}: rc={r.returncode} {tail}")
    if r.returncode != 0:
        print((r.stderr or "")[-300:])
        raise SystemExit(5)


def count_tokens_zero_booking_note(n: int) -> None:
    st = load_state("count-tokens")
    st["calls"] = st.get("calls", 0) + n
    save_state("count-tokens", st)


def estimate_and_guard(name: str, model: str, params: list[dict], expected_out: list[int], batch: bool = True) -> dict:
    """count_tokens on the longest request, times the count; output at most the planned length; refuse over budget."""
    c = client()
    rep = max(params, key=lambda p: len(json.dumps(p["messages"], ensure_ascii=False)))
    tok = c.messages.count_tokens(model=model, system=rep["system"], messages=rep["messages"]).input_tokens
    count_tokens_zero_booking_note(1)
    pin, pout, _ = PRICES[model]
    f = 0.5 if batch else 1.0
    in_cost = len(params) * tok * pin / 1e6 * f
    out_cost = sum(expected_out) * pout / 1e6 * f
    est = {"name": name, "n": len(params), "input_tokens_rep": tok, "expected_output_tokens": int(sum(expected_out)),
           "input_usd": round(in_cost, 3), "output_usd": round(out_cost, 3), "estimate_usd": round(in_cost + out_cost, 3)}
    est["ledger_total_usd"] = ledger_total()
    est["would_total_usd"] = round(est["ledger_total_usd"] + est["estimate_usd"], 3)
    save_state(f"{name}.estimate", est)
    print(f"estimate {name}: n={est['n']} in/req={tok} est=${est['estimate_usd']} ledger=${est['ledger_total_usd']} "
          f"-> ${est['would_total_usd']} (cap ${BUDGET_USD})")
    if est["would_total_usd"] > BUDGET_USD:
        print("STOP: estimate plus ledger total exceeds the cap; not submitting")
        raise SystemExit(4)
    return est


# ------------------------------------------------------------------ batches
def submit(name: str, requests: list[dict], model: str, effort: str, purpose: str, record: str | None = None) -> str:
    st = load_state(name)
    if st.get("batch_id"):
        print(f"{name}: already submitted as {st['batch_id']}")
        return st["batch_id"]
    write_jsonl(ROOT / "requests" / f"{record or name}.jsonl", requests)
    b = client().messages.batches.create(requests=requests)
    st = {"name": name, "batch_id": b.id, "n": len(requests), "model": model, "effort": effort, "purpose": purpose,
          "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "booked": False}
    save_state(name, st)
    print(f"{name}: submitted {b.id} ({len(requests)} requests)")
    return b.id


def _row(entry) -> dict:
    r = entry.result
    row = {"custom_id": entry.custom_id, "type": r.type}
    if r.type == "succeeded":
        m = r.message
        row["text"] = "".join(b.text for b in m.content if getattr(b, "type", "") == "text")
        row["stop_reason"] = m.stop_reason
        row["usage"] = {k: int(getattr(m.usage, k, 0) or 0) for k in USAGE_KEYS}
    elif r.type == "errored":
        row["error"] = r.error.model_dump()
    return row


def collect(name: str) -> dict:
    st = load_state(name)
    rows = None
    for attempt in range(5):  # the result stream can drop mid-download; it can be fetched again
        try:
            cl = client()  # keep a reference: a garbage-collected client closes its sockets mid-stream
            rows = [_row(e) for e in cl.messages.batches.results(st["batch_id"])]
            break
        except Exception as exc:  # noqa: BLE001
            print(f"{name}: result download attempt {attempt + 1} failed ({type(exc).__name__}); retrying")
            time.sleep(10 * (attempt + 1))
    if rows is None:
        raise SystemExit("could not download results")
    write_jsonl(ROOT / "results" / f"{name}.jsonl", rows)
    ok = [r for r in rows if r["type"] == "succeeded"]
    bad = len(rows) - len(ok)
    usage = sum_usage([r["usage"] for r in ok])
    usage["note"] = f"{st['batch_id']}, {len(ok)} succeeded, {bad} errored"
    upath = ROOT / "requests" / f"{name}.usage.json"
    upath.write_text(json.dumps(usage), encoding="utf-8")
    if not st.get("booked"):
        book(name, st["model"], st["effort"], st["purpose"], upath, batch=True)
        st["booked"] = True
    trunc = sum(1 for r in ok if r.get("stop_reason") == "max_tokens")
    st.update({"collected": True, "succeeded": len(ok), "errored": bad, "truncated": trunc, "usage": usage})
    save_state(name, st)
    print(f"{name}: {len(ok)} succeeded, {bad} errored, {trunc} hit max_tokens; usage {usage['input_tokens']}/"
          f"{usage['output_tokens']}/{usage['cache_creation_input_tokens']}/{usage['cache_read_input_tokens']}")
    if bad > 0.10 * max(1, st["n"]):
        print(f"STOP: more than 10% errored in {name}")
        save_state(name, {**st, "halt": True})
        raise SystemExit(3)
    return st


def wait(name: str, max_wait: int = 540, poll: int = 60) -> bool:
    st = load_state(name)
    if st.get("collected"):
        print(f"{name}: already collected")
        return True
    c = client()
    t0 = time.time()
    while True:
        b = c.messages.batches.retrieve(st["batch_id"])
        rc = b.request_counts
        print(f"{name} {b.processing_status}: processing={rc.processing} succeeded={rc.succeeded} errored={rc.errored} "
              f"canceled={rc.canceled} expired={rc.expired}", flush=True)
        if b.processing_status == "ended":
            collect(name)
            return True
        if time.time() - t0 + poll > max_wait:
            return False
        time.sleep(poll)


def batch_request(custom_id: str, model: str, system_text: str, user_text: str, max_tokens: int, effort: str,
                  cache_system: bool = True, thinking: bool = True) -> dict:
    sysblock = {"type": "text", "text": system_text}
    if cache_system:
        sysblock["cache_control"] = {"type": "ephemeral"}
    params = {"model": model, "max_tokens": max_tokens, "system": [sysblock],
              "messages": [{"role": "user", "content": user_text}], "output_config": {"effort": effort}}
    if thinking:
        params["thinking"] = {"type": "adaptive"}
    return {"custom_id": custom_id, "params": params}


if __name__ == "__main__":
    print("ledger total", ledger_total())