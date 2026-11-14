"""llm.py: the one call layer of split H, for both providers.

* Alibaba Cloud Model Studio (OpenAI-compatible chat completions, httpx, streamed): the key is read from the DPAPI vault
  inside this process (as tools/ali_probe.py does) and kept in memory only.
* OpenRouter: tools/openrouter_client.py (vault key in memory; zero data retention forced on every chat request by that
  client; its provider block is not copied or changed here). usage={"include": True} makes the response carry its cost.
* Neither key is ever printed, logged, written, put in an exception message, an environment variable or a command line;
  error text is scrubbed of both before it is stored. No request or response header is recorded anywhere.
* At most MAX_INFLIGHT requests in flight per model (a semaphore per model; gpt-oss-120b gets 2 per process because both
  halves run as separate processes and use it, so at most 4 are in flight in total). Retries with backoff on 429, 5xx and
  timeouts, at most MAX_TRIES tries per call.
* Guards, read from usage.jsonl before EVERY request (GuardStop): OpenRouter cost sum >= US$4.80; an Alibaba model at its
  token cap (total tokens, cached included; see common.ALI_CAPS), counting an estimate for the requests in flight. A model
  code that is not one of the five allowed codes is refused (NotAllowed).
* Every HTTP attempt appends one line to usage.jsonl; the request (messages, no headers) goes to requests/<half>/<phase>/
  and the response to results/<half>/<phase>/ (a re-run skips a call whose result file already holds a response).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

import httpx

import common as C

sys.path.insert(0, "C:/dev/themis-work/tools")
import openrouter_client as orc  # noqa: E402

BASE = "https://ws-WORKSPACE-ID.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
ROOT = Path(__file__).resolve().parent
USAGE = ROOT / "usage.jsonl"
MAX_INFLIGHT = 4
INFLIGHT = {"openai/gpt-oss-120b": 2}
MAX_TRIES = 4
TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=60.0, pool=300.0)
ALLOWED = set(C.ALI_CAPS) | set(C.OR_MODELS)


class AliError(Exception):
    """Base class; messages never contain a key."""


class GuardStop(AliError):
    """A guard refused the request."""


class NotAllowed(AliError):
    """A model code outside the allowed list."""


class QuotaExhausted(AliError):
    """The endpoint says this model is out of (free) budget or access is denied for billing."""

    def __init__(self, model, code, status):
        super().__init__(f"quota/billing: model={model} status={status} code={code}")
        self.model, self.code, self.status = model, code, status


class ModelRefused(AliError):
    """The endpoint does not accept this model code for this account (or no ZDR endpoint serves it on OpenRouter)."""

    def __init__(self, model, code, status):
        super().__init__(f"model refused: model={model} status={status} code={code}")
        self.model, self.code, self.status = model, code, status


# ---------------------------------------------------------------- key (memory only)
_KEY: str | None = None
_KEY_LOCK = threading.Lock()


def _key() -> str:
    global _KEY
    with _KEY_LOCK:
        if _KEY is None:
            k = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 os.path.expandvars(r"%USERPROFILE%\.claudetyl\secrets\get-secret.ps1"), "alibaba-modelstudio-key"],
                capture_output=True, text=True).stdout.strip()
            if not k:
                raise AliError("the vault returned no key")
            _KEY = k
    return _KEY


def scrub(s) -> str:
    s = str(s)
    for k in (_KEY, getattr(orc, "_KEY", None)):
        if k:
            s = s.replace(k, "***")
    return s


_CLIENT: httpx.Client | None = None
_CLIENT_LOCK = threading.Lock()


def _client() -> httpx.Client:
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = httpx.Client(timeout=TIMEOUT, limits=httpx.Limits(max_connections=64, max_keepalive_connections=16))
    return _CLIENT


_SEMS: dict[str, threading.BoundedSemaphore] = {}
_SEM_LOCK = threading.Lock()
_USAGE_LOCK = threading.Lock()
_INFLIGHT_EST: dict[str, int] = defaultdict(int)


def _sem(model: str) -> threading.BoundedSemaphore:
    with _SEM_LOCK:
        if model not in _SEMS:
            _SEMS[model] = threading.BoundedSemaphore(INFLIGHT.get(model, MAX_INFLIGHT))
        return _SEMS[model]


# ---------------------------------------------------------------- usage file and guards
def read_usage() -> list[dict]:
    if not USAGE.exists():
        return []
    rows = []
    with open(USAGE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def _row_tokens(r: dict) -> int:
    u = r.get("usage") or {}
    t = u.get("total_tokens")
    if t is None:
        t = (u.get("prompt_tokens") or 0) + (u.get("completion_tokens") or 0)
    if not t:
        t = r.get("est_total_tokens") or 0
    return int(t)


def totals() -> tuple[dict[str, int], int]:
    per: dict[str, int] = {}
    for r in read_usage():
        per[r["model"]] = per.get(r["model"], 0) + _row_tokens(r)
    return per, sum(per.values())


def row_cost(r: dict) -> float:
    return float((r.get("usage") or {}).get("cost") or r.get("cost") or 0)


def or_cost_total() -> float:
    return round(sum(row_cost(r) for r in read_usage() if r.get("provider") == "openrouter"), 6)


def guard(model: str, est: int = 0) -> None:
    if model not in ALLOWED:
        raise NotAllowed(f"model code not allowed in split H: {model}")
    if C.provider_of(model) == "openrouter":
        cost = or_cost_total()
        if cost >= C.OR_COST_STOP:
            raise GuardStop(f"guard: OpenRouter spend US${cost:.4f} has reached US${C.OR_COST_STOP:.2f}")
        return
    cap = C.ALI_CAPS[model]
    used = totals()[0].get(model, 0)
    with _SEM_LOCK:
        infl = _INFLIGHT_EST[model]
    if used >= cap or (infl and used + infl + est > cap):
        raise GuardStop(f"guard: model {model} is at {used:,} total tokens (+{infl:,} in flight, +{est:,} this request); cap {cap:,}")


def _append_usage(row: dict) -> None:
    line = json.dumps(row, ensure_ascii=False, separators=(", ", ": "))
    with _USAGE_LOCK:
        with open(USAGE, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- error classification
_QUOTA_CODES = ("freetieronly", "arrearage", "insufficient_quota", "quotaexceeded", "billing", "overdue",
                "accountoverdue", "insufficientbalance", "paymentrequired")
_QUOTA_MSG = ("free tier", "free quota", "good standing", "insufficient balance", "billing", "arrear", "out of quota",
              "quota has been exhausted", "exceeded your current quota", "run out of")
_MODEL_CODES = ("model_not_found", "modelnotfound", "model.accessdenied", "invalid_model", "model_not_exist",
                "modelnotexist", "model_not_supported")
_MODEL_MSG = ("model not found", "model does not exist", "model_not_found", "no such model", "does not exist or you do not",
              "model access denied", "not in the list", "model is not available", "unknown model")


def _err_fields(body: str) -> tuple[str, str]:
    code, msg = "", ""
    body = body.strip()
    if body.startswith("data:"):
        body = body[5:].strip()
    try:
        d = json.loads(body)
        e = d.get("error", d) if isinstance(d, dict) else {}
        if isinstance(e, dict):
            code = str(e.get("code") or e.get("type") or "")
            msg = str(e.get("message") or "")
        else:
            msg = str(e)
    except (json.JSONDecodeError, AttributeError):
        msg = body[:300]
    return code, msg


def classify(status: int, body: str) -> tuple[str, str, str]:
    """-> (kind, code, short message). kind in quota | model | retry | fatal."""
    code, msg = _err_fields(body)
    lc, lm = code.lower(), msg.lower()
    if any(c in lc for c in _QUOTA_CODES) or any(m in lm for m in _QUOTA_MSG):
        return "quota", code, msg
    if status in (401, 402):
        return "quota", code or str(status), msg
    if any(c in lc for c in _MODEL_CODES) or any(m in lm for m in _MODEL_MSG) or status == 404:
        return "model", code, msg
    if status == 403:
        return ("quota" if "access denied" in lm or "accessdenied" in lc else "fatal"), code, msg
    if status == 429 or (isinstance(status, int) and status >= 500) or status in (408, 409):
        return "retry", code, msg
    return "fatal", code, msg


# ---------------------------------------------------------------- the call
def _body(model, messages, max_tokens, temperature, thinking, stream, extra) -> dict:
    b: dict = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if temperature is not None:
        b["temperature"] = temperature
    if thinking is not None:
        b["enable_thinking"] = bool(thinking)
    if stream:
        b["stream"] = True
        b["stream_options"] = {"include_usage": True}
    if extra:
        b.update(extra)
    return b

def _one_attempt(body: dict, stream: bool) -> dict:
    """One HTTP attempt. Returns status, content, reasoning_chars, finish, usage, err_body. HTTP errors do not raise."""
    url = BASE + "/chat/completions"
    hdr = {"Authorization": "Bearer " + _key(), "Content-Type": "application/json"}
    out = {"status": None, "content": "", "reasoning_chars": 0, "finish": None, "usage": None, "err_body": None}
    c = _client()
    if not stream:
        r = c.post(url, headers=hdr, json=body)
        out["status"] = r.status_code
        if r.status_code != 200:
            out["err_body"] = r.text[:2000]
            return out
        d = r.json()
        ch = (d.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        out["content"] = msg.get("content") or ""
        out["reasoning_chars"] = len(msg.get("reasoning_content") or "")
        out["finish"] = ch.get("finish_reason")
        out["usage"] = d.get("usage")
        return out
    with c.stream("POST", url, headers=hdr, json=body) as r:
        out["status"] = r.status_code
        if r.status_code != 200:
            out["err_body"] = r.read().decode("utf-8", "replace")[:2000]
            return out
        parts: list[str] = []
        for line in r.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                o = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(o, dict) and o.get("error") and not o.get("choices"):
                out["status"] = 500
                out["err_body"] = json.dumps(o)[:2000]
                return out
            if o.get("usage"):
                out["usage"] = o["usage"]
            chs = o.get("choices") or []
            if chs:
                dl = chs[0].get("delta") or {}
                if dl.get("content"):
                    parts.append(dl["content"])
                if dl.get("reasoning_content"):
                    out["reasoning_chars"] += len(dl["reasoning_content"])
                if chs[0].get("finish_reason"):
                    out["finish"] = chs[0]["finish_reason"]
        out["content"] = "".join(parts)
    return out

def _paths(half: str, phase: str, case_id: str) -> tuple[Path, Path]:
    h = (half or "x").lower()
    rq, rs = ROOT / "requests" / h / phase, ROOT / "results" / h / phase
    rq.mkdir(parents=True, exist_ok=True)
    rs.mkdir(parents=True, exist_ok=True)
    return rq / f"{case_id}.json", rs / f"{case_id}.json"


def load_result(half: str, phase: str, case_id: str) -> dict | None:
    p = ROOT / "results" / (half or "x").lower() / phase / f"{case_id}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return None



# ---------------------------------------------------------------- OpenRouter attempt (through tools/openrouter_client.py)
def _or_attempt(model: str, messages: list[dict], max_tokens: int, temperature, extra) -> dict:
    out = {"status": None, "content": "", "reasoning_chars": 0, "finish": None, "usage": None, "err_body": None,
           "serving_provider": None}
    params: dict = {"max_tokens": max_tokens, "usage": {"include": True}}
    if temperature is not None:
        params["temperature"] = temperature
    if extra:
        params.update(extra)
    try:
        text, meta = orc.chat(model, messages, retries=1, **params)
    except Exception as exc:  # noqa: BLE001  (never let a message carry a key)
        msg = scrub(exc)
        m = re.search(r"HTTP Error (\d+)", msg)
        out["status"] = int(m.group(1)) if m else ("timeout" if "timed out" in msg.lower() else "transport")
        out["err_body"] = msg[:1000]
        return out
    out.update({"status": 200, "content": text or "", "finish": meta.get("finish_reason"), "usage": meta.get("usage"),
                "serving_provider": meta.get("provider")})
    return out


def _est_tokens(messages: list[dict], max_tokens: int) -> int:
    return int(sum(len(m["content"]) for m in messages) / 3) + min(max_tokens, 4000) // 2


def call(*, half: str, phase: str, case_id: str, model: str, messages: list[dict], max_tokens: int,
         temperature: float | None = None, thinking: bool | None = None, stream: bool = True,
         extra: dict | None = None, force: bool = False) -> dict:
    """One chat-completions call with retries. Returns the stored result dict (ok True/False). Raises GuardStop,
    NotAllowed, QuotaExhausted or ModelRefused (the caller decides what to do with them)."""
    if model not in ALLOWED:
        raise NotAllowed(f"model code not allowed in split H: {model}")
    provider = C.provider_of(model)
    rq_path, rs_path = _paths(half, phase, case_id)
    if not force:
        prev = load_result(half, phase, case_id)
        if prev and prev.get("ok"):
            prev["skipped"] = True
            return prev
    body = _body(model, messages, max_tokens, temperature, thinking, stream, extra)
    params = {"temperature": temperature, "max_tokens": max_tokens, "thinking": thinking, "stream": stream}
    if extra:
        params["extra"] = extra if provider == "openrouter" else sorted(extra)
    est = _est_tokens(messages, max_tokens)
    rq_path.write_text(json.dumps({"model": model, "provider": provider, "params": params, "messages": messages},
                                  ensure_ascii=False), encoding="utf-8")
    result = {"ok": False, "half": half, "phase": phase, "case_id": case_id, "model": model, "provider": provider,
              "params": params, "tries": 0, "http_status": None, "finish_reason": None, "content": "", "usage": None,
              "error": None}
    last_kind, last_code = None, ""
    for attempt in range(1, MAX_TRIES + 1):
        guard(model, est)
        t0 = time.time()
        row = {"time": _now(), "provider": provider, "half": half, "phase": phase, "case_id": case_id, "model": model,
               **params, "try": attempt, "http_status": None, "finish_reason": None, "usage": None}
        got = None
        with _sem(model):
            with _SEM_LOCK:
                _INFLIGHT_EST[model] += est
            try:
                got = (_or_attempt(model, messages, max_tokens, temperature, extra) if provider == "openrouter"
                       else _one_attempt(body, stream))
                row["http_status"] = got["status"]
            except (httpx.TimeoutException, httpx.TransportError, httpx.StreamError) as exc:
                row["http_status"] = "timeout" if isinstance(exc, httpx.TimeoutException) else "transport"
                row["error_type"] = type(exc).__name__
            finally:
                with _SEM_LOCK:
                    _INFLIGHT_EST[model] -= est
        row["seconds"] = round(time.time() - t0, 1)
        if got and got["status"] == 200:
            row["finish_reason"] = got["finish"]
            row["usage"] = got["usage"]
            row["reasoning_chars"] = got["reasoning_chars"]
            if provider == "openrouter":
                row["serving_provider"] = got.get("serving_provider")
                row["cost"] = (got["usage"] or {}).get("cost")
            if not got["usage"]:
                n_in = sum(len(m["content"]) for m in messages)
                row["est_total_tokens"] = int((n_in + len(got["content"])) / 3)
                row["usage_missing"] = True
            _append_usage(row)
            if got["finish"] is None and not got["content"]:
                last_kind, last_code = "retry", "empty_response"
                result["tries"] = attempt
                time.sleep(2 * attempt)
                continue
            result.update({"ok": True, "tries": attempt, "http_status": 200, "finish_reason": got["finish"],
                           "content": got["content"], "usage": got["usage"], "reasoning_chars": got["reasoning_chars"],
                           "seconds": row["seconds"], "error": None,
                           "serving_provider": got.get("serving_provider"), "cost": row.get("cost")})
            rs_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            return result
        # failure path
        if got:
            kind, code, msg = classify(got["status"], got["err_body"] or "")
            row["error_code"] = scrub(code)[:80]
            row["error_msg"] = scrub(msg)[:200]
        else:
            kind, code, msg = "retry", row.get("error_type", "transport"), ""
        _append_usage(row)
        last_kind, last_code = kind, scrub(code)
        result.update({"tries": attempt, "http_status": row["http_status"],
                       "error": {"kind": kind, "code": scrub(code)[:80], "message": scrub(msg)[:200]}})
        if kind == "quota":
            rs_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            raise QuotaExhausted(model, scrub(code), row["http_status"])
        if kind == "model":
            rs_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            raise ModelRefused(model, scrub(code), row["http_status"])
        if kind == "fatal":
            break
        time.sleep(min(60, (3 ** attempt) + random.random() * 3))
    result["error"] = result.get("error") or {"kind": last_kind, "code": last_code, "message": ""}
    rs_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def or_key_usage() -> float:
    """The key's total usage in US$ (the response carries no secret)."""
    return float(orc.key_info().get("usage"))


def list_models() -> list[str]:
    r = _client().get(BASE + "/models", headers={"Authorization": "Bearer " + _key()})
    if r.status_code != 200:
        raise AliError(f"models list status {r.status_code}")
    return sorted(m["id"] for m in r.json().get("data", []))


if __name__ == "__main__":
    per, tot = totals()
    print({"models": per, "all": tot, "openrouter_cost": or_cost_total()})
