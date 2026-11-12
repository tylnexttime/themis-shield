"""ali.py: the one place that talks to the Alibaba Cloud Model Studio endpoint (OpenAI-compatible chat completions).

* The key is read from the DPAPI vault inside this process (as tools/ali_probe.py does) and kept only in memory. It is
  never printed, logged, written, put in an exception message, an environment variable or a command line. Error text
  is scrubbed of it before it is stored. No request or response header is recorded anywhere.
* At most MAX_INFLIGHT requests in flight per model code (a semaphore per model).
* Retries with backoff on 429 (rate limits), 5xx and timeouts, at most MAX_TRIES tries per call.
* Token guards, read from usage.jsonl before EVERY request: a model above PER_MODEL_CAP total tokens, or all models
  together above ALL_CAP, are refused (GuardStop).
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
import threading
import time
from pathlib import Path

import httpx

BASE = "https://ws-WORKSPACE-ID.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
ROOT = Path(__file__).resolve().parent
USAGE = ROOT / "usage.jsonl"
PER_MODEL_CAP = 2_000_000
ALL_CAP = 8_000_000
MAX_INFLIGHT = 4
MAX_TRIES = 4
TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=60.0, pool=300.0)


class AliError(Exception):
    """Base class; messages never contain the key."""


class GuardStop(AliError):
    """A token guard refused the request."""


class QuotaExhausted(AliError):
    """The endpoint says this model is out of (free) budget or access is denied for billing."""

    def __init__(self, model, code, status):
        super().__init__(f"quota/billing: model={model} status={status} code={code}")
        self.model, self.code, self.status = model, code, status


class ModelRefused(AliError):
    """The endpoint does not accept this model code for this account."""

    def __init__(self, model, code, status):
        super().__init__(f"model refused: model={model} status={status} code={code}")
        self.model, self.code, self.status = model, code, status


class CallFailed(AliError):
    """All tries failed for a reason that is not quota or model refusal (error code kept, never the key)."""


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
    return s.replace(_KEY, "***") if _KEY else s


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


def _sem(model: str) -> threading.BoundedSemaphore:
    with _SEM_LOCK:
        if model not in _SEMS:
            _SEMS[model] = threading.BoundedSemaphore(MAX_INFLIGHT)
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


def guard(model: str) -> None:
    per, tot = totals()
    if per.get(model, 0) >= PER_MODEL_CAP:
        raise GuardStop(f"guard: model {model} has passed {PER_MODEL_CAP:,} total tokens ({per[model]:,})")
    if tot >= ALL_CAP:
        raise GuardStop(f"guard: all models together have passed {ALL_CAP:,} total tokens ({tot:,})")


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


def call(*, half: str, phase: str, case_id: str, model: str, messages: list[dict], max_tokens: int,
         temperature: float | None = None, thinking: bool | None = None, stream: bool = True,
         extra: dict | None = None, force: bool = False) -> dict:
    """One chat-completions call with retries. Returns the stored result dict (ok True/False). Raises GuardStop,
    QuotaExhausted or ModelRefused (the caller decides what to do with them)."""
    rq_path, rs_path = _paths(half, phase, case_id)
    if not force:
        prev = load_result(half, phase, case_id)
        if prev and prev.get("ok"):
            prev["skipped"] = True
            return prev
    body = _body(model, messages, max_tokens, temperature, thinking, stream, extra)
    params = {"temperature": temperature, "max_tokens": max_tokens, "thinking": thinking, "stream": stream}
    if extra:
        params["extra"] = sorted(extra)
    rq_path.write_text(json.dumps({"model": model, "params": params, "messages": messages}, ensure_ascii=False),
                       encoding="utf-8")
    result = {"ok": False, "half": half, "phase": phase, "case_id": case_id, "model": model, "params": params,
              "tries": 0, "http_status": None, "finish_reason": None, "content": "", "usage": None, "error": None}
    last_kind, last_code = None, ""
    for attempt in range(1, MAX_TRIES + 1):
        guard(model)
        t0 = time.time()
        row = {"time": _now(), "half": half, "phase": phase, "case_id": case_id, "model": model, **params,
               "try": attempt, "http_status": None, "finish_reason": None, "usage": None}
        got = None
        with _sem(model):
            try:
                got = _one_attempt(body, stream)
                row["http_status"] = got["status"]
            except (httpx.TimeoutException, httpx.TransportError, httpx.StreamError) as exc:
                row["http_status"] = "timeout" if isinstance(exc, httpx.TimeoutException) else "transport"
                row["error_type"] = type(exc).__name__
        row["seconds"] = round(time.time() - t0, 1)
        if got and got["status"] == 200:
            row["finish_reason"] = got["finish"]
            row["usage"] = got["usage"]
            row["reasoning_chars"] = got["reasoning_chars"]
            if not got["usage"]:
                n_in = sum(len(m["content"]) for m in messages)
                row["est_total_tokens"] = int((n_in + len(got["content"])) / 3)
                row["usage_missing"] = True
            _append_usage(row)
            if got["finish"] is None and not got["content"]:
                last_kind, last_code = "retry", "empty_stream"
                result["tries"] = attempt
                time.sleep(2 * attempt)
                continue
            result.update({"ok": True, "tries": attempt, "http_status": 200, "finish_reason": got["finish"],
                           "content": got["content"], "usage": got["usage"], "reasoning_chars": got["reasoning_chars"],
                           "seconds": row["seconds"], "error": None})
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


def list_models() -> list[str]:
    r = _client().get(BASE + "/models", headers={"Authorization": "Bearer " + _key()})
    if r.status_code != 200:
        raise AliError(f"models list status {r.status_code}")
    return sorted(m["id"] for m in r.json().get("data", []))


if __name__ == "__main__":
    per, tot = totals()
    print({"models": per, "all": tot})
