"""Five-scenario acceptance rehearsal against a RUNNING local gate.

Embedded synthetic fixtures only. No upstream client or cloud
credential access. Run from the repository root:
    python demo/scenarios.py --base-url http://127.0.0.1:8742

Scenario 5 is interactive and stops the gate. Missing proof is a failure,
not a pass. Evidence contains checks and measurements, not prompts, answers,
gate tokens, HTTP authorisation headers or replacement maps.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from themis_shield.timezone import business_timezone
from themis_shield.paths import app_data_dir

import httpx

from themis_shield.cloud.openai_compat import strict_json
from themis_shield.runtime.resources import ResourceReader


ROOT = Path(__file__).resolve().parents[1]
JOB = re.compile(r"JOB-[0-9]{4}-[0-9]{4}")
TOKEN = re.compile(r"⟦[PCAIDSQ]:[0-9a-f]{6}⟧")
SAFE_CODE = re.compile(r"[A-Za-z0-9_.:/ -]{1,160}")
MODEL_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
MAX_RESPONSE = 16 * 1024 * 1024
MAX_CAPTURE = 10 * 1024 * 1024

CLEAN = "In two sentences, what is a synthetic test dataset used for?"
NAMES = (
    "Please draft a short, polite reminder to Mara Lindahl "
    "(customer C1234567, mara.lindahl@example.com) that her invoice "
    "is due on 30/10/2026."
)
RESTRICTED = (
    "RESTRICTED\nSynthetic board briefing on the Harbourline merger plan: "
    "meet 0900, decide 1400. Summarise in one line.\nRESTRICTED"
)
TWO_STAGE = "In one sentence, explain why synthetic rehearsal data is useful."
DOWN = "Explain the purpose of a synthetic gate-down rehearsal."
ORIGINALS = (
    "Mara Lindahl", "C1234567", "mara.lindahl@example.com", "Mara", "Lindahl",
)
SCENARIOS = (
    ("1", "Clean question", "held -> approved -> cloud-tokenised; cloud answer"),
    ("2", "Names", "placeholders only outbound; Mara restored locally"),
    ("3", "RESTRICTED", "local; RESTRICTED banner; no cloud payload or capture"),
    ("4", "Two-stage approval", "held -> stage 1 -> ready -> stage 2 -> re-send"),
    ("5", "Gate down", "connection refused; capture inventory unchanged; no send"),
)


class Failure(RuntimeError):
    """A fixed code; never echo a response body or exception arguments."""


def require(condition, code):
    if not condition:
        raise Failure(code)


def utc():
    return datetime.now(timezone.utc).isoformat()


def day_first(stamp):
    value = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return value.astimezone(business_timezone()).strftime(
        "%d/%m/%Y %H:%M:%S.%f %Z"
    )


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def unlinked(path):
    require(
        not path.is_symlink() and not any(p.is_symlink() for p in path.parents),
        "LINKED_PROOF_PATH",
    )


def absence(value, originals=ORIGINALS):
    """Check raw JSON AND decoded strings, including JSON-in-string content.

    Lexical fixture checks are not a detector recall or de-identification
    certification. In particular, they do not assess context-only identifiers.
    """
    raw = value if isinstance(value, str) else encoded(value).decode()
    require(
        all(original.casefold() not in raw.casefold() for original in originals),
        "ORIGINAL_IN_CLOUD_PROOF",
    )

    def walk(item, depth=0):
        require(depth <= 32, "PROOF_DEPTH")
        if isinstance(item, str):
            require(
                all(original.casefold() not in item.casefold()
                    for original in originals),
                "ORIGINAL_IN_CLOUD_PROOF",
            )
            if item.lstrip().startswith(("{", "[")):
                try:
                    nested = strict_json(item)
                except (ValueError, TypeError, UnicodeError):
                    return
                walk(nested, depth + 1)
        elif isinstance(item, list):
            for child in item:
                walk(child, depth + 1)
        elif isinstance(item, dict):
            for key, child in item.items():
                walk(key, depth + 1)
                walk(child, depth + 1)

    walk(strict_json(raw))


def payload(view):
    require(type(view) is dict, "INVALID_CLOUD_VIEW")
    require(
        type(view.get("payload")) is dict
        and isinstance(view.get("exact_json"), str),
        "INVALID_CLOUD_VIEW",
    )
    exact = view["exact_json"]
    require(strict_json(exact) == view["payload"], "CLOUD_VIEW_JSON_MISMATCH")
    return exact


def capture_records(directory, identifier):
    require(JOB.fullmatch(identifier) is not None, "INVALID_JOB_ID")
    path = directory / f"{identifier}.jsonl"
    unlinked(path)
    require(path.is_file(), "CAPTURE_MISSING")
    require(path.stat().st_size <= MAX_CAPTURE, "CAPTURE_BOUND")
    raw = path.read_bytes()
    require(raw.endswith(b"\n"), "CAPTURE_INCOMPLETE_LINE")
    rows = [strict_json(line) for line in raw.splitlines() if line.strip()]
    require(all(type(row) is dict for row in rows), "INVALID_CAPTURE")
    return path, raw, rows


def verify_capture(view, rows, staged_digest, *, names=False):
    exact = payload(view)
    outbound = [row for row in rows if row.get("kind") == "outbound"]
    require(len(outbound) == 1, "OUTBOUND_COUNT_NOT_ONE")
    record = outbound[0]
    require(record.get("verify_no_originals") is True, "VERIFY_RECEIPT_MISSING")
    require(record.get("exact_json") == exact, "API_CAPTURE_BYTES_DIFFER")
    require(record.get("payload") == view["payload"], "API_CAPTURE_PAYLOAD_DIFFER")
    require(
        record.get("manifest_hash") == sha(exact.encode()) == staged_digest,
        "PACKAGE_HASH_CHANGED",
    )
    require(
        view["payload"].get("model") == "example-cloud-model",
        "UNEXPECTED_CLOUD_MODEL",
    )
    answers = [row["chunk"] for row in rows
               if row.get("kind") == "answer-tokenised"]
    require(bool(answers), "TOKENISED_ANSWER_MISSING")
    require(view.get("answer") == answers, "API_CAPTURE_ANSWER_DIFFER")
    require(
        sum(row.get("kind") == "transport-complete" for row in rows) == 1,
        "CLOUD_TRANSPORT_NOT_COMPLETE",
    )
    if names:
        absence(exact)
        absence(record)
        require(TOKEN.search(exact) is not None, "PLACEHOLDERS_MISSING")
        # Also inspect reconstructed tokenised answer text across chunk splits.
        answer_text = "".join(
            choice.get("delta", {}).get("content") or ""
            for chunk in answers for choice in chunk.get("choices", [])
        )
        absence({"answer": answer_text})
    return {
        "outbound_records": 1,
        "payload_sha256": staged_digest,
        "exact_api_capture_match": True,
        "cloud_model": "example-cloud-model",
        "tokenised_answer_chunks": len(answers),
        "transport_complete": True,
        "fixture_originals_absent": True if names else None,
    }


def inventory(directory):
    unlinked(directory)
    require(directory.is_dir(), "CAPTURE_DIRECTORY_UNAVAILABLE")
    result = {}
    for path in sorted(directory.iterdir()):
        if path.suffix != ".jsonl":
            continue
        require(JOB.fullmatch(path.stem) is not None, "UNKNOWN_CAPTURE_FILENAME")
        unlinked(path)
        require(path.is_file() and path.stat().st_size <= MAX_CAPTURE,
                "CAPTURE_INVENTORY_UNREADABLE")
        raw = path.read_bytes()
        result[path.name] = {"bytes": len(raw), "sha256": sha(raw)}
    return result


def response_headers(headers):
    result = {}
    for key in ("x-themis-route", "x-themis-reason", "x-themis-job"):
        value = headers.get(key)
        if value:
            require(SAFE_CODE.fullmatch(value) is not None, "UNSAFE_HEADER")
            result[key] = value
    tiers = {}
    for tier in (1, 2, 3):
        value = headers.get(f"x-themis-tier-{tier}-ms")
        if value is not None:
            number = float(value)
            require(math.isfinite(number) and number >= 0, "INVALID_TIER_HEADER")
            tiers[str(tier)] = number
    result["tiers_ms"] = tiers
    return result


def parse_sse(raw):
    """Require a successful terminal event, complete framing and app metadata."""
    text = raw.decode("utf-8")
    data_lines, events = [], []
    done = False
    for line in text.splitlines():
        if not line:
            if data_lines:
                data = "\n".join(data_lines)
                data_lines = []
                require(not done, "EVENT_AFTER_DONE")
                if data == "[DONE]":
                    done = True
                else:
                    item = strict_json(data)
                    require(type(item) is dict and "error" not in item,
                            "CHAT_ERROR_EVENT")
                    events.append(item)
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
        elif line.startswith((": ", ":", "event:")):
            continue
        else:
            raise Failure("UNSUPPORTED_SSE_LINE")
    require(not data_lines and done, "SSE_NOT_COMPLETE")
    require(bool(events), "SSE_EMPTY")
    metadata = events[-1].get("themis")
    require(type(metadata) is dict, "FINAL_APP_METADATA_MISSING")
    answer = "".join(
        choice.get("delta", {}).get("content") or ""
        for event in events for choice in event.get("choices", [])
    )
    return answer, metadata


@dataclass
class Wire:
    status: int
    headers: dict
    raw: bytes
    began_utc: str
    ended_utc: str
    wall_seconds: float


def audit_timestamps(database, identifier):
    """Read only allow-listed, content-free audit events from the pilot store.

    API contract v1 does not expose stage event timestamps. Do not substitute
    client request times and call them server approval timestamps.
    """
    unlinked(database)
    require(database.is_file(), "REGISTER_TIMESTAMP_PROOF_UNAVAILABLE")
    connection = sqlite3.connect(
        database.resolve().as_uri() + "?mode=ro", uri=True, timeout=5,
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            """
            SELECT event,occurred_utc,approval_revision
            FROM audit WHERE job_id=? AND event IN (
                'STAGE1_REQUESTED','PACKAGE_READY','STAGE1_APPROVED',
                'STAGE2_APPROVED','SEND_AUTHORISED'
            ) ORDER BY audit_id
            """,
            (identifier,),
        ).fetchall()
    finally:
        connection.close()
    result = []
    for event, stamp, revision in rows:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        require(parsed.utcoffset() is not None, "INVALID_STAGE_TIMESTAMP")
        result.append({
            "event": event, "utc": stamp, "local": day_first(stamp),
            "approval_revision": revision,
        })
    for event in ("STAGE1_REQUESTED", "PACKAGE_READY", "STAGE1_APPROVED",
                  "STAGE2_APPROVED", "SEND_AUTHORISED"):
        require(any(row["event"] == event for row in result),
                "STAGE_TIMESTAMP_MISSING")
    revisions = {row["approval_revision"] for row in result}
    require(len(revisions) == 1, "MULTIPLE_PACKAGE_REVISIONS")
    stamps = {
        event: min(datetime.fromisoformat(row["utc"].replace("Z", "+00:00"))
                   for row in result if row["event"] == event)
        for event in ("STAGE1_REQUESTED", "PACKAGE_READY", "STAGE1_APPROVED",
                      "STAGE2_APPROVED", "SEND_AUTHORISED")
    }
    require(
        stamps["STAGE1_REQUESTED"] <= stamps["STAGE1_APPROVED"]
        and stamps["STAGE1_REQUESTED"] <= stamps["PACKAGE_READY"]
        and stamps["PACKAGE_READY"] <= stamps["STAGE2_APPROVED"]
        and max(stamps["STAGE1_APPROVED"], stamps["STAGE2_APPROVED"])
        <= stamps["SEND_AUTHORISED"],
        "STAGE_TIMESTAMP_ORDER_WRONG",
    )
    return result


class Demo:
    def __init__(self, args, token):
        self.args = args
        self.current = None
        self.security_abort = False
        self.run_id = secrets.token_hex(8)
        self.client = httpx.Client(
            base_url=args.base_url,
            headers={"Authorization": "Bearer " + token},
            trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(args.timeout, connect=5),
        )

    def exchange(self, method, path, value=None, *, timeout=None):
        began, start = utc(), time.perf_counter()
        status, headers, raw = None, {}, b""
        error = None
        try:
            options = {"json": value} if value is not None else {}
            if timeout is not None:
                options["timeout"] = timeout
            with self.client.stream(method, path, **options) as response:
                status = response.status_code
                headers = response_headers(response.headers)
                pieces, count = [], 0
                for part in response.iter_bytes():
                    count += len(part)
                    require(count <= MAX_RESPONSE, "HTTP_RESPONSE_BOUND")
                    require(time.perf_counter() - start <= self.args.timeout,
                            "HTTP_WALL_DEADLINE")
                    pieces.append(part)
                raw = b"".join(pieces)
            return Wire(
                status, headers, raw, began, utc(), time.perf_counter() - start,
            )
        except Exception as exc:
            # Class name only: no exception text, request, response or token.
            error = type(exc).__name__
            raise
        finally:
            if self.current is not None:
                self.current["operations"].append({
                    "method": method, "path": path, "began_utc": began,
                    "ended_utc": utc(),
                    "wall_seconds": time.perf_counter() - start,
                    "http_status": status, "headers": headers,
                    "transport_error_class": error,
                })

    def api(self, method, path, value=None):
        wire = self.exchange(method, path, value)
        require(wire.status == 200, "API_OPERATION_FAILED")
        result = strict_json(wire.raw)
        require(type(result) is dict, "API_OBJECT_REQUIRED")
        return result

    def state(self, identifier):
        require(JOB.fullmatch(identifier) is not None, "INVALID_JOB_ID")
        value = self.api("GET", f"/api/jobs/{identifier}")
        require(value.get("job_id") == identifier, "JOB_ID_MISMATCH")
        if value.get("route") == "local":
            # Local-only jobs carry no cloud approval stages. 
            require(all(value.get(name) is None for name in ("stage1", "package", "stage2")),
                    "LOCAL_JOB_HAS_CLOUD_STAGES")
        else:
            require(value.get("stage1") in {"requested", "approved", "refused"}
                    and value.get("package") in {"building", "ready", "failed"}
                    and value.get("stage2") in {"pending", "approved", "refused"},
                    "JOB_STAGE_FIELDS_MISSING")
        # Retain an allow-list, not arbitrary new API fields.
        safe = {key: value.get(key) for key in (
            "job_id", "route", "marking_class", "status", "stage1",
            "package", "stage2", "due", "expiry", "tier", "timings",
        )}
        self.current["stage_observations"].append({
            "observed_utc": utc(), "job": safe,
        })
        return value

    def no_file(self, identifier):
        path = self.args.capture_dir / f"{identifier}.jsonl"
        unlinked(path)
        require(not path.exists(), "UNEXPECTED_CLOUD_CAPTURE")

    def preview(self, text, intent):
        # The preview covers the whole conversation as "[role]" + newline + text turns, like the app;
        # these scenarios are single user turns.
        return self.api("POST", "/api/preview", {
            "text": "[user]\n" + text, "attachments": [], "intent": intent,
        })

    def chat(self, preview, text, number, *, confirm):
        require(not (text == RESTRICTED and confirm), "RESTRICTED_CONFIRM_FORBIDDEN")
        wire = self.exchange("POST", "/api/chat", {
            "preview_id": preview["preview_id"],
            "messages": [{"role": "user", "content": text}],
            "model": "themis/auto",
            "confirm_cloud": confirm,
            "conversation_id": f"demo-{self.run_id}-{number}",
        })
        require(wire.status == 200, "CHAT_HTTP_FAILURE")
        answer, metadata = parse_sse(wire.raw)
        route, identifier = metadata.get("route"), metadata.get("job_id")
        require(route in {"local", "cloud-tokenised", "held", "blocked"},
                "INVALID_ROUTE_METADATA")
        require(isinstance(identifier, str) and JOB.fullmatch(identifier),
                "CHAT_JOB_ID_MISSING")
        require(
            wire.headers.get("x-themis-route") == route
            and wire.headers.get("x-themis-job") == identifier
            and wire.headers.get("x-themis-reason") == metadata.get("reason"),
            "HEADER_METADATA_DISAGREEMENT",
        )
        if route in {"held", "cloud-tokenised"}:
            require(set(wire.headers["tiers_ms"]) == {"1", "2", "3"},
                    "FULL_STACK_TIER_HEADERS_MISSING")
        self.current.update({
            "actual": route, "route": route, "reason": metadata.get("reason"),
            "job_id": identifier, "tier_timings_ms": wire.headers["tiers_ms"],
            "answer_source": metadata.get("answer_source"),
        })
        self.current["chat_attempts"].append({
            "route": route, "reason": metadata.get("reason"),
            "job_id": identifier, "answer_source": metadata.get("answer_source"),
            "tiers_ms_from_headers": wire.headers["tiers_ms"],
            "tiers_ms_from_metadata": metadata.get("tiers_ms"),
            "wall_seconds": wire.wall_seconds,
            "began_utc": wire.began_utc, "ended_utc": wire.ended_utc,
            "answer_characters": len(answer),
        })
        return answer, metadata

    def approve(self, identifier, stage):
        print(f"Operator approval (synthetic rehearsal): "
              f"{identifier}, stage {stage}", flush=True)
        result = self.api("POST", f"/api/operator/{identifier}/approve", {
            "stage": stage, "reason": "ROUTINE_APPROVED",
        })
        require(result.get(f"stage{stage}") == "approved",
                "OPERATOR_STAGE_NOT_APPROVED")

    def cloud(self, number, text, *, names=False):
        require(not self.security_abort, "CLOUD_REHEARSAL_ABORTED")
        preview = self.preview(text, "cloud")
        require("cloud" in preview.get("route_options", []),
                "CLOUD_NOT_OFFERED")
        # A synthetic requester cloud choice, NOT operator approval.
        # Keep this boolean stable on re-send: it is part of job correlation.
        _, first = self.chat(preview, text, number, confirm=True)
        if first["route"] != "held":
            self.security_abort = True
            raise Failure("INITIAL_REQUEST_NOT_HELD")
        identifier = first["job_id"]
        self.no_file(identifier)
        row = self.state(identifier)
        require(row["stage1"] == "requested" and row["stage2"] == "pending",
                "INITIAL_STAGES_NOT_PENDING")

        # Inspect the prepared package BEFORE granting release approval.
        if row["package"] == "ready":
            staged = self.api("GET", f"/api/jobs/{identifier}/cloud-view")
            exact = payload(staged)
            if names:
                absence(exact)
                require(TOKEN.search(exact) is not None, "PLACEHOLDERS_MISSING")
        self.approve(identifier, 1)
        row = self.state(identifier)
        require(row["stage1"] == "approved" and row["stage2"] == "pending",
                "STAGE1_ALONE_CHANGED_STAGE2")
        self.no_file(identifier)
        ready_deadline = time.monotonic() + self.args.timeout
        while row["package"] == "building":
            require(time.monotonic() < ready_deadline, "PACKAGE_READY_TIMEOUT")
            time.sleep(0.5)
            row = self.state(identifier)
        require(row["package"] == "ready", "PACKAGE_NOT_READY")

        staged = self.api("GET", f"/api/jobs/{identifier}/cloud-view")
        exact = payload(staged)
        if names:
            absence(exact)
            require(TOKEN.search(exact) is not None, "PLACEHOLDERS_MISSING")
        staged_digest = sha(exact.encode())
        self.approve(identifier, 2)
        row = self.state(identifier)
        require(
            row["stage1"] == row["stage2"] == "approved"
            and row["package"] == "ready",
            "BOTH_APPROVALS_NOT_READY",
        )
        self.no_file(identifier)
        self.current["proof_lines"].append(
            "No capture after the hold, stage 1, or stage 2; "
            "operator actions did not send."
        )

        answer, final = self.chat(preview, text, number, confirm=True)
        require(final["job_id"] == identifier, "RESEND_CREATED_ANOTHER_JOB")
        require(final["route"] == "cloud-tokenised", "RESEND_NOT_CLOUD")
        require(final.get("answer_source") == "cloud", "ANSWER_SOURCE_NOT_CLOUD")
        require(bool(answer.strip()), "CLOUD_ANSWER_EMPTY")
        view = self.api("GET", f"/api/jobs/{identifier}/cloud-view")
        path, raw, records = capture_records(self.args.capture_dir, identifier)
        proof = verify_capture(view, records, staged_digest, names=names)
        self.current["capture_proof"] = {
            **proof, "filename": path.name, "file_sha256": sha(raw),
        }
        self.current["proof_lines"].append(
            f"{path.name}: exact staged/API/captured payload match; "
            "example-cloud-model; tokenised answer and transport-complete receipt."
        )
        if names:
            require("Mara" in answer, "GIVEN_NAME_NOT_RESTORED")
            require("Mara Lindahl" in answer, "FULL_NAME_NOT_RESTORED")
            require(type(final.get("restored")) is int and final["restored"] > 0,
                    "RESTORATION_RECEIPT_MISSING")
            self.current["proof_lines"].append(
                "All five fixture originals absent, case-insensitive, from "
                "raw and decoded outbound JSON; placeholders present. "
                "Local answer contains Mara Lindahl and Mara."
            )
        events = audit_timestamps(self.args.register_db, identifier)
        self.current["stage_timestamps"] = {
            "source": "read-only local SQLite audit; authoritative pilot events",
            "events": events,
        }
        self.current["proof_lines"].append(
            "Stage 1 request, package ready, both approvals and send-authorised "
            "timestamps recorded; same revision and valid order."
        )

    def protected(self):
        preview = self.preview(RESTRICTED, "auto")
        marking = preview.get("marking", {})
        require(marking.get("class") == "RESTRICTED", "RESTRICTED_NOT_DETECTED")
        require(preview.get("route_options") == ["local"], "RESTRICTED_CLOUD_OFFERED")
        banner = marking.get("banner", "")
        require(isinstance(banner, str) and banner.startswith("RESTRICTED:"),
                "RESTRICTED_BANNER_MISSING")
        print(banner, flush=True)
        self.current["protected_banner"] = banner
        answer, final = self.chat(preview, RESTRICTED, "3", confirm=False)
        require(final["route"] == "local", "RESTRICTED_NOT_LOCAL")
        require(final.get("answer_source") == "local", "RESTRICTED_SOURCE_NOT_LOCAL")
        require(bool(answer.strip()), "LOCAL_ANSWER_EMPTY")
        identifier = final["job_id"]
        row = self.state(identifier)
        require(row.get("marking_class") == "RESTRICTED", "JOB_MARKING_WRONG")
        require(row.get("route") == "local", "JOB_ROUTE_NOT_LOCAL")
        require(self.args.capture_dir.is_dir(), "CAPTURE_DIRECTORY_UNAVAILABLE")
        self.no_file(identifier)
        wire = self.exchange("GET", f"/api/jobs/{identifier}/cloud-view")
        if wire.status == 404:
            body = strict_json(wire.raw)
            require(body.get("error", {}).get("code") == "NOT_FOUND_OR_EXPIRED",
                    "UNEXPECTED_NO_PAYLOAD_RESPONSE")
            # The preceding successful GET establishes that this job exists.
            meaning = "no cloud payload (existing local job; capture unavailable)"
        else:
            require(wire.status == 200, "NO_CLOUD_PAYLOAD_NOT_PROVEN")
            body = strict_json(wire.raw)
            require(
                body.get("payload") is None
                and body.get("message") == "no cloud payload",
                "RESTRICTED_HAS_CLOUD_PAYLOAD",
            )
            meaning = "no cloud payload"
        self.current["proof_lines"] += [
            "RESTRICTED preview banner shown; only local offered; "
            "confirm_cloud was false.",
            f"{identifier}: no capture file; cloud-view: {meaning}.",
        ]
        self.current["tier_timing_note"] = (
            "Absent local-response tier headers are recorded as absent. "
            "The supplied local answer branch bypasses cloud PII tiers."
        )

    def stopped(self):
        if self.args.simulate_finder_down:
            print(
                "--simulate-finder-down: the supplied server has no verified "
                "test-only finder control endpoint. Using stopped-server "
                "rehearsal; no guessed admin endpoint is called.",
                flush=True,
            )
        print(
            "\nScenario 5: STOP the gate server with Ctrl+C in its server "
            "terminal. Wait for it to exit. Do not restart it yet. Close or "
            "pause the app, Kilo and other requesters for this rehearsal.\n"
            "Type STOPPED here after shutdown. This test makes one local "
            "/v1/chat/completions request and never uses a cloud fallback.",
            flush=True,
        )
        require(input("> ").strip() == "STOPPED", "STOPPED_SERVER_NOT_CONFIRMED")
        before = inventory(self.args.capture_dir)
        self.current["gate_down_capture_before"] = before
        require(before == self.current["capture_inventory_before_stop"],
                "CAPTURES_CHANGED_DURING_SHUTDOWN")
        try:
            self.exchange("POST", "/v1/chat/completions", {
                "model": "themis/cloud:example-cloud-model",
                "messages": [{"role": "user", "content": DOWN}],
                "stream": False,
            }, timeout=5)
        except httpx.ConnectError:
            self.current.update(
                actual="connection refused", route=None,
                reason="LOCAL_CONNECTION_REFUSED", job_id=None,
                answer_source=None, tier_timings_ms={},
            )
        else:
            raise Failure("STOPPED_GATE_ACCEPTED_CONNECTION")
        after = inventory(self.args.capture_dir)
        require(before == after, "CAPTURES_CHANGED_WHILE_GATE_DOWN")
        self.current["gate_down_capture_after"] = after
        self.current["proof_lines"] += [
            "One IPv4 loopback request failed to connect; proxies, redirects "
            "and inference retries disabled; no upstream client in this script.",
            "Capture filename/size/SHA-256 inventory unchanged across shutdown "
            "and the refused request: nothing sent by this request.",
            "No job ID, route headers or live runtime response exists while "
            "the gate is down; these are recorded as unavailable.",
        ]

    def snapshot(self, *, runtime_down_expected=False):
        result = {"observed_utc": utc(), "errors": []}
        try:
            memory = ResourceReader(self.args.gpu_index)().record()
            result["memory"] = memory
            if memory.get("gpu_error") is not None:
                result["errors"].append("GPU_MEASUREMENT_UNAVAILABLE")
        except Exception:
            result["memory"] = None
            result["errors"].append("RAM_VRAM_MEASUREMENT_UNAVAILABLE")
        try:
            runtime = self.api("GET", "/api/runtime")
            modes = runtime.get("loaded_modes")
            models = runtime.get("loaded_models")
            require(isinstance(models, list) and isinstance(modes, list),
                    "RUNTIME_MODEL_MODES_MISSING")
            require(
                all(isinstance(model, str) and MODEL_ID.fullmatch(model)
                    for model in models),
                "INVALID_RUNTIME_MODEL_ID",
            )
            require(
                all(type(item) is dict
                    and item.get("id") in models
                    and item.get("mode") in {"full", "mmap"} for item in modes)
                and {item["id"] for item in modes} == set(models),
                "RUNTIME_MODEL_MODE_UNKNOWN",
            )
            result["runtime"] = {
                "available": True, "loaded_models": models, "loaded_modes": modes,
                "queue": runtime.get("queue"), "status": runtime.get("status"),
                "loading": runtime.get("loading"),
                "inference_active": runtime.get("inference_active"),
            }
        except httpx.ConnectError:
            result["runtime"] = {
                "available": False, "reason": "LOCAL_CONNECTION_REFUSED",
                "loaded_models": None, "loaded_modes": None,
            }
            if not runtime_down_expected:
                result["errors"].append("RUNTIME_MEASUREMENT_UNAVAILABLE")
        except Exception:
            result["runtime"] = {"available": False}
            result["errors"].append("RUNTIME_MEASUREMENT_UNAVAILABLE")
        return result

    def preflight(self):
        health = self.api("GET", "/health")
        require(health.get("gate") == "ok" and health.get("synthetic_only") is True,
                "NOT_HEALTHY_SYNTHETIC_GATE")
        require(health.get("pilot_fixture") is False, "AUTOMATIC_APPROVAL_ENABLED")
        settings = self.api("GET", "/api/settings")
        require(settings.get("pilot_fixture_enabled") is False,
                "AUTOMATIC_APPROVAL_ENABLED")
        require(settings.get("operator_console_enabled") is True,
                "OPERATOR_CONSOLE_DISABLED")
        require(settings.get("local_target") == "laptop", "LAPTOP_TARGET_REQUIRED")
        queue = self.api("GET", "/api/operator/queue")
        require(queue.get("label") == "SYNTHETIC REHEARSAL",
                "REHEARSAL_LABEL_MISSING")
        models = self.api("GET", "/api/models")
        require(any(item.get("id") == "themis/cloud:example-cloud-model"
                    for item in models.get("cloud", [])), "APPROVED_CLOUD_MISSING")
        for identifier in ("gemma4-e4b", "gemma4-26b-a4b"):
            require(any(item.get("id") == identifier
                        and item.get("runtime") == "llamacpp"
                        and item.get("available") is True
                        for item in models.get("local", [])),
                    "REQUIRED_REAL_LOCAL_MODEL_UNAVAILABLE")
        unlinked(self.args.capture_dir)
        unlinked(self.args.register_db)
        return {
            "synthetic_only": True, "pilot_auto_approval": False,
            "operator_console": True, "local_target": "laptop",
            "finder_models": ["gemma4-e4b", "gemma4-26b-a4b"],
            "cloud_model": "themis/cloud:example-cloud-model",
        }

    def run_one(self, number, title, expected):
        row = {
            "scenario": number, "title": title, "expected": expected,
            "actual": "not completed", "pass": False,
            "route": None, "reason": None, "job_id": None,
            "tier_timings_ms": {}, "answer_source": None,
            "operations": [], "chat_attempts": [], "stage_observations": [],
            "proof_lines": [], "began_utc": utc(),
        }
        self.current = row
        began = time.perf_counter()
        print(f"\nScenario {number}: {title}", flush=True)
        row["before"] = self.snapshot()
        try:
            require(not row["before"]["errors"], "BEFORE_MEASUREMENT_INCOMPLETE")
            if number == "1":
                self.cloud(number, CLEAN)
            elif number == "2":
                self.cloud(number, NAMES, names=True)
            elif number == "3":
                self.protected()
            elif number == "4":
                self.cloud(number, TWO_STAGE)
            else:
                row["capture_inventory_before_stop"] = inventory(
                    self.args.capture_dir
                )
                self.stopped()
            row["pass"] = True
        except Failure as exc:
            code = str(exc)
            row["failure_code"] = code
            if code in {
                "ORIGINAL_IN_CLOUD_PROOF", "INITIAL_REQUEST_NOT_HELD",
                "UNEXPECTED_CLOUD_CAPTURE", "RESTRICTED_HAS_CLOUD_PAYLOAD",
                "RESTRICTED_NOT_LOCAL", "RESTRICTED_CLOUD_OFFERED",
            }:
                self.security_abort = True
            row["proof_lines"].append("FAILED: " + code)
        except (EOFError, KeyboardInterrupt):
            row["failure_code"] = "OPERATOR_REHEARSAL_INTERRUPTED"
        except Exception:
            row["failure_code"] = "DEMO_OR_REQUIRED_PROOF_UNAVAILABLE"
        finally:
            row["after"] = self.snapshot(runtime_down_expected=number == "5")
            if row["after"]["errors"]:
                row["pass"] = False
                row["proof_lines"].append("FAILED: AFTER_MEASUREMENT_INCOMPLETE")
            row["wall_seconds"] = time.perf_counter() - began
            row["ended_utc"] = utc()
            print("PASS" if row["pass"] else "FAIL", flush=True)
            self.current = None
        return row


def markdown(report):
    lines = [
        "# Five-scenario synthetic acceptance rehearsal", "",
        "Run: " + day_first(report["began_utc"]) + ".",
        "Result: **" + ("PASS" if report["pass"] else "FAIL") + "**.", "",
        "| Scenario | Expected | Actual | Result |",
        "|---|---|---|---|",
    ]
    for row in report["scenarios"]:
        lines.append(
            f'| {row["scenario"]}. {row["title"]} | {row["expected"]} | '
            f'{row["actual"]} | {"PASS" if row["pass"] else "FAIL"} |'
        )
    for row in report["scenarios"]:
        lines += [
            "", f'## {row["scenario"]}. {row["title"]}', "",
            f'Job: `{row["job_id"] or "unavailable"}`; '
            f'route: `{row["route"] or "unavailable"}`; '
            f'reason: `{row["reason"] or "unavailable"}`; '
            f'answer source: `{row["answer_source"] or "unavailable"}`.',
            f'Wall time, including operator/poll/proof/measurement work: '
            f'{row.get("wall_seconds", 0):.3f} s.',
        ]
        lines += ["- " + proof for proof in row["proof_lines"]]
        if row.get("failure_code"):
            lines.append("- Failure code: `" + row["failure_code"] + "`.")
        lines += [
            "",
            "| Chat attempt | Route | Reason | Job | Tier 1 ms | Tier 2 ms | Tier 3 ms | HTTP wall s |",
            "|---|---|---|---|---:|---:|---:|---:|",
        ]
        for index, attempt in enumerate(row["chat_attempts"], 1):
            timings = attempt["tiers_ms_from_headers"]
            cells = [
                f'{timings[str(tier)]:.3f}' if str(tier) in timings else "absent"
                for tier in (1, 2, 3)
            ]
            lines.append(
                f'| {index} | {attempt["route"]} | {attempt["reason"]} | '
                f'{attempt["job_id"]} | {" | ".join(cells)} | '
                f'{attempt["wall_seconds"]:.3f} |'
            )
        lines += ["", "Before/after measurements (memory units: GiB):", ""]
        lines += [
            "| Sample | Physical RAM free | Physical RAM used | Commit used | VRAM free | VRAM used | Loaded models/modes |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
        for phase in ("before", "after"):
            sample = row.get(phase, {})
            memory = sample.get("memory") or {}
            runtime = sample.get("runtime") or {}
            def fmt(key):
                value = memory.get(key)
                return f"{value:.3f}" if type(value) in (int, float) else "unavailable"
            used = (
                memory["vram_total_gb"] - memory["vram_free_gb"]
                if memory.get("vram_total_gb") is not None
                and memory.get("vram_free_gb") is not None else None
            )
            loaded = (
                ", ".join(f'{item["id"]}:{item["mode"]}'
                          for item in runtime.get("loaded_modes", [])) or "none"
                if runtime.get("available") else "runtime unavailable"
            )
            lines.append(
                f'| {phase} | {fmt("ram_available_gb")} | {fmt("ram_used_gb")} | '
                f'{fmt("commit_used_gb")} | {fmt("vram_free_gb")} | '
                f'{f"{used:.3f}" if used is not None else "unavailable"} | {loaded} |'
            )
        for event in row.get("stage_timestamps", {}).get("events", []):
            lines.append(
                f'- `{event["event"]}`: {event["local"]}; '
                f'revision {event["approval_revision"]}.'
            )
        if row.get("tier_timing_note"):
            lines.append(row["tier_timing_note"])
    lines += [
        "", "## Scope and limits", "",
        "- Synthetic fixture acceptance, not production assurance or recall certification.",
        "- RAM/VRAM are before/after system/GPU samples, not peaks or per-process attribution.",
        "- Runtime snapshots show models loaded at each sample, not every transient model.",
        "- Stage timestamps come from the local pilot audit; HTTP observation times are separate.",
        "- A capture is written before credential acquisition/send. Its presence alone does not prove a send.",
        "- A complete tokenised answer and transport receipt support the successful cloud round trip.",
        "- The stopped-server test covers front-door failure, not a live finder-only outage.",
        "- Negative-send evidence is scoped to this isolated rehearsal and the supplied capture-before-send connector.",
        "",
    ]
    return "\n".join(lines) + "\n"


def write_bundle(report, stamp):
    directory = ROOT / "runs" / "demo"
    directory.mkdir(parents=True, exist_ok=True)
    unlinked(directory)
    base = directory / ("DEMO-" + stamp)
    destinations = {
        base.with_suffix(".json"): encoded(report) + b"\n",
        base.with_suffix(".md"): markdown(report).encode("utf-8"),
    }
    require(not any(path.exists() for path in destinations), "EVIDENCE_NAME_EXISTS")
    for destination, raw in destinations.items():
        descriptor, temporary = tempfile.mkstemp(prefix=".demo-", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
    print(f"Evidence: runs/demo/{base.name}.json and .md", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8742")
    parser.add_argument("--capture-dir", type=Path)
    parser.add_argument("--register-db", type=Path)
    parser.add_argument("--gpu-index", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--simulate-finder-down", action="store_true")
    args = parser.parse_args(argv)
    report = {
        "schema_version": 1,
        "synthetic_only": True, "began_utc": utc(),
        "pass": False, "scenarios": [],
    }
    stamp = datetime.now(business_timezone()).strftime("%d-%m-%Y-%H%M")
    demo = None
    try:
        parsed = urlsplit(args.base_url)
        require(
            parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
            and parsed.port is not None and 1 <= parsed.port <= 65535
            and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment
            and parsed.path in {"", "/"},
            "IPV4_LOOPBACK_BASE_REQUIRED",
        )
        require(math.isfinite(args.timeout) and args.timeout > 0
                and args.gpu_index >= 0, "INVALID_MEASUREMENT_OPTIONS")
        local_root = app_data_dir()
        args.capture_dir = args.capture_dir or local_root / "cloud-payloads"
        args.register_db = args.register_db or local_root / "register.db"
        token_path = local_root / "gate-token"
        unlinked(token_path)
        require(token_path.is_file() and token_path.stat().st_size <= 4096,
                "LOCAL_TOKEN_UNAVAILABLE")
        token = token_path.read_text(encoding="ascii").strip()
        require(re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token) is not None,
                "LOCAL_TOKEN_INVALID")
        demo = Demo(args, token)
        report["preflight"] = demo.preflight()
        report["scenarios"] = [
            demo.run_one(number, title, expected)
            for number, title, expected in SCENARIOS
        ]
        report["pass"] = all(row["pass"] for row in report["scenarios"])
        report["security_abort"] = demo.security_abort
    except Exception:
        report["failure_code"] = "PREFLIGHT_OR_DEMO_SETUP_FAILED"
        report["scenarios"] = [
            {
                "scenario": number, "title": title, "expected": expected,
                "actual": "not run: preflight failed", "pass": False,
                "route": None, "reason": None, "job_id": None,
                "answer_source": None, "chat_attempts": [],
                "proof_lines": ["No acceptance result claimed."],
            }
            for number, title, expected in SCENARIOS
        ]
    finally:
        if demo is not None:
            demo.client.close()
    report["ended_utc"] = utc()
    try:
        write_bundle(report, stamp)
    except Exception:
        print("Evidence write refused or incomplete; no successful run claimed.")
        return 2
    print("Restart the gate after scenario 5 if required.")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
