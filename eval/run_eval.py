#!/usr/bin/env python3
"""Run themis-shield's real detection pipeline over an evaluation split, decide each case, and score it.

    python eval/run_eval.py --split dev --run-name dev-baseline-iq4 --runs-dir <outside the repo> \
        --runtime-settings <runtime settings JSON outside the repo> [--catalogue <catalogue patch JSON>]

What runs is what the gate runs: `detect_batch(texts, profile, targets, thorough=True)` with the rule packs, the
institution profile and the finder context given here, PII-Tracer on the CPU, then the local finders through the
gate's own runtime (llama.cpp's llama-server, one model resident at a time), union, propagation, allow-list and the
real policy (`themis_shield.policy.decide`). Nothing here reads gold spans or hard negatives before scoring.

Outputs (in <runs-dir>/<run-name>/, never inside the repository):
  predictions.jsonl  one line per case in score.py's format (gate labels; the label map is applied by the scorer)
  decisions.jsonl    decision, reason codes and stage statuses per case
  timings.jsonl      per-tier timings per case (kept apart so predictions and decisions reproduce byte for byte)
  llm_calls.jsonl    one line per finder call: model, case id, wall time, llama-server timings (no text)
  prompt-<model>.txt the finder messages with the case text replaced by <<TEXT>>, as sent
  config.json        arguments, git commit, case-file hashes, runtime settings, finder catalogue entries
  score.json / score.txt / summary.json   the scorer's report and the headline numbers
  run.log            progress

Case files: --cases FILE [FILE ...] runs those files instead of the split's bundled file (for example the v0.2
development set, dev.jsonl plus the retired v0.1 test.jsonl). A file holding cases marked "test" runs only as
--split test, under the guard below; the retired v0.1 test file is the one exception, pinned by its sha256.

Finder capture and replay: --capture-finders DIR writes every finder response (the decoded payload, or the error
kind), with the case id, the model, the prompt hash and the model identity (weights file, arguments, context).
--replay-finders DIR serves those responses instead of starting llama-server and refuses any call whose prompt hash
or model identity differs from the capture. Rules, PII-Tracer, union, propagation, allow-list and policy still run.

Gate versions: --gate-version v0.1 restores the evaluated v0.1 detection and policy (the negative control);
--detection-version, --policy-version and --quasi-id-ask set them one at a time. The default is v0.2.

The test split is guarded: it runs only from a clean tree whose HEAD carries the frozen tag named with
--frozen-tag, and never into a run directory that already holds a score (a crash may be resumed with --resume).
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
from dataclasses import replace
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for entry in (str(ROOT), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import label_map  # noqa: E402
import score as scorer  # noqa: E402

SENSITIVE_GOLD = ["HEALTH", "DISABILITY", "ETHNICITY", "SEXUALITY", "RELIGION", "CRIMINAL", "FAMILY_VIOLENCE", "UNION"]
SENSITIVE_LOOKALIKE_KINDS = (
    "fictional_character", "fiction_statement", "public_news", "committee", "initiative", "public_event",
    "policy_statement",
)
FINDER_IDS = ("gemma4-e4b", "gemma4-26b-a4b")
DEFAULT_CONTEXT = "a training and research institute"
DEFAULT_PROFILE = "harbourline-eval"
DEFAULT_RULE_PACKS = "core,au,intl"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------------------------------------------------
# Recording finder client: wraps the gate's own client, records timings per call, never stores text.
# --------------------------------------------------------------------------------------------------------------------
class RecordingClient:
    """Delegates to the gate's SequentialFinderClient; records one entry per finder call.

    The engine calls the resident target once per selected document, in input order, so the k-th call inside a
    residency belongs to the k-th document of the batch (every document is selected when detection is thorough).
    """

    def __init__(self, inner, sink=None, capture=None, replay=None):
        self.inner = inner
        self.sink = sink
        self.capture = capture  # FinderCapture: writes every finder response for later replay
        self.replay = replay  # FinderReplay: serves captured responses instead of calling a model
        self.case_ids: list[str] = []
        self.texts: list[str] = []
        self.calls: list[dict] = []
        self.templates: dict[str, set[str]] = {}
        self.first_messages: dict[str, list[dict]] = {}

    @contextmanager
    def residency(self, model_role):
        residency = getattr(self.inner, "residency", None)
        began = time.perf_counter()
        if residency is None:
            yield _RecordingTarget(self, self.inner, model_role, 0.0)
            return
        context = residency(model_role)
        try:
            target = context.__enter__()
        except Exception as error:
            # Record why a model could not be made resident (fixed scheduler messages, never document text).
            self.calls.append({"case_id": None, "model": model_role, "status": "residency_failed",
                               "error": f"{type(error).__name__}: {error}"[:200]})
            if self.sink is not None:
                self.sink.write(json.dumps(self.calls[-1]) + "\n")
                self.sink.flush()
            raise
        try:
            yield _RecordingTarget(self, target, model_role, (time.perf_counter() - began) * 1000)
        except BaseException as error:
            if not context.__exit__(type(error), error, error.__traceback__):
                raise
        else:
            context.__exit__(None, None, None)

    def chat_json(self, model_role, messages, schema, max_tokens):
        return _RecordingTarget(self, self.inner, model_role, 0.0).chat_json(model_role, messages, schema, max_tokens)


class _RecordingTarget:
    def __init__(self, owner: RecordingClient, target, model_role: str, acquire_ms: float):
        self.owner, self.target, self.model_role = owner, target, model_role
        self.index = 0
        self.acquire_ms = acquire_ms

    def chat_json(self, model_role, messages, schema, max_tokens):
        owner = self.owner
        index = self.index
        self.index += 1
        case_id = owner.case_ids[index] if index < len(owner.case_ids) else None
        text = owner.texts[index] if index < len(owner.texts) else None
        templated = [
            {"role": m["role"], "content": m["content"].replace(text, "<<TEXT>>") if text else m["content"]}
            for m in messages
        ]
        key = hashlib.sha256(json.dumps(templated, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        prefix = messages[:-1] if len(messages) > 1 else []
        prefix_sha = hashlib.sha256(json.dumps(prefix, ensure_ascii=False).encode()).hexdigest() if prefix else None
        owner.templates.setdefault(model_role, set()).add(key)
        owner.first_messages.setdefault(model_role, templated)
        prompt_sha = prompt_sha256(messages, schema, max_tokens)
        began = time.perf_counter()
        status = "ok"
        try:
            if owner.replay is not None:
                status = "replayed"
                return owner.replay.serve(case_id, model_role, prompt_sha)
            try:
                payload = self.target.chat_json(model_role, messages, schema, max_tokens)
            except Exception as error:
                if owner.capture is not None:
                    owner.capture.write(case_id, model_role, prompt_sha, error=error,
                                        timings=getattr(self.target, "last_timings", None))
                raise
            if owner.capture is not None:
                owner.capture.write(case_id, model_role, prompt_sha, payload=payload,
                                    timings=getattr(self.target, "last_timings", None))
            return payload
        except Exception as error:  # recorded, then re-raised for the engine's fail-closed handling
            status = type(error).__name__
            raise
        finally:
            record = {
                "case_id": case_id, "model": model_role, "status": status,
                "wall_ms": round((time.perf_counter() - began) * 1000, 1),
                "acquire_ms": round(self.acquire_ms, 1) if index == 0 else 0.0,
                "timings": dict(getattr(self.target, "last_timings", {}) or {}),
                "template_sha256": key, "system_prefix_sha256": prefix_sha, "prompt_sha256": prompt_sha,
                "text_chars": len(text) if text else None,
            }
            owner.calls.append(record)
            if owner.sink is not None:
                owner.sink.write(json.dumps(record) + "\n")
                owner.sink.flush()


# --------------------------------------------------------------------------------------------------------------------
# Finder capture and replay. Most v0.2 changes sit after the LLM finders and the finder prompt does not change, so a
# run can record every finder response once (on the GPU) and later runs can serve those responses instead of calling
# llama-server. Replay refuses a call whose prompt (messages, schema, max_tokens) or model identity differs from the
# capture, so a replayed run is the live run with only the post-finder code changed.
# --------------------------------------------------------------------------------------------------------------------
CAPTURE_FILE = "finders.jsonl"
CAPTURE_MANIFEST = "manifest.json"


class ReplayRefused(SystemExit):
    """A replayed call does not match its capture. Not an Exception: the engine must not turn it into a stage result."""


def prompt_sha256(messages, schema, max_tokens) -> str:
    """Everything a finder call depends on besides the model: the exact messages, schema and token limit."""
    body = json.dumps({"messages": messages, "schema": schema, "max_tokens": max_tokens},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def finder_identities(catalogue) -> dict:
    """Model identity per finder role: the weights file, launch arguments and context size from the catalogue."""
    out = {}
    for model_id in FINDER_IDS:
        entry = catalogue.get(model_id)
        out[model_id] = {"file": entry.file, "args": list(entry.args), "ctx": entry.ctx}
    return out


def _error_kind(error: BaseException) -> str:
    # The engine maps TimeoutError to TIMEOUT, these four to MALFORMED and anything else to FAILED.
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, (ValueError, TypeError, KeyError, IndexError)):
        return "malformed"
    return "failed"


class FinderCapture:
    """Appends one line per finder call: case id, model, prompt hash, model identity and the decoded response.

    The response is what the gate's client returned to the finder (the schema payload), which is the finder's whole
    input from the model. Contains document values: keep capture folders outside the repository.
    """

    def __init__(self, directory: Path, identities: dict, meta: dict):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        manifest_path = directory / CAPTURE_MANIFEST
        manifest = {"written_utc": now(), "identities": identities, **meta}
        if manifest_path.exists():
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            if previous.get("identities") != identities:
                raise SystemExit("capture folder holds responses from other model identities")
            manifest["resumed_from"] = previous.get("resumed_from", []) + [previous.get("written_utc")]
        manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        self.identities = identities
        self.sink = open(directory / CAPTURE_FILE, "a", encoding="utf-8", newline="\n")

    def write(self, case_id, model_role, prompt_sha, payload=None, error=None, timings=None) -> None:
        record = {"case_id": case_id, "model": model_role, "model_identity": self.identities.get(model_role),
                  "prompt_sha256": prompt_sha, "timings": dict(timings or {})}
        if error is None:
            record.update(outcome="ok", payload=payload)
        else:
            record.update(outcome="error", error_kind=_error_kind(error), error_type=type(error).__name__)
        self.sink.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self.sink.flush()

    def close(self) -> None:
        self.sink.close()


class FinderReplay:
    """Serves captured finder responses; refuses any call whose prompt hash or model identity differs."""

    def __init__(self, directory: Path, identities: dict):
        manifest_path = directory / CAPTURE_MANIFEST
        if not manifest_path.exists():
            raise SystemExit(f"no capture manifest in {directory}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("identities") != identities:
            raise ReplayRefused(
                f"model identities differ from the capture: captured {manifest.get('identities')}, now {identities}")
        self.manifest = manifest
        self.identities = identities
        self.records: dict[tuple[str, str], dict] = {}
        for line in (directory / CAPTURE_FILE).read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                # A resumed capture may hold a chunk twice (the crashed attempt and the rerun): the last one counts.
                self.records[(record["case_id"], record["model"])] = record
        self.served = 0

    def serve(self, case_id, model_role, prompt_sha):
        record = self.records.get((case_id, model_role))
        if record is None:
            raise ReplayRefused(f"no captured {model_role} response for case {case_id}")
        if record["model_identity"] != self.identities.get(model_role):
            raise ReplayRefused(f"model identity differs for {model_role} on case {case_id}")
        if record["prompt_sha256"] != prompt_sha:
            raise ReplayRefused(f"prompt hash differs for {model_role} on case {case_id}")
        self.served += 1
        if record["outcome"] == "ok":
            return json.loads(json.dumps(record["payload"]))  # a fresh copy each time
        kind = record["error_kind"]
        if kind == "timeout":
            raise TimeoutError("replayed-timeout")
        if kind == "malformed":
            raise ValueError("replayed-malformed")
        raise RuntimeError("replayed-failure")


# --------------------------------------------------------------------------------------------------------------------
# Case files. --cases replaces the split's bundled file(s). The v0.1 test file was retired to development data in
# v0.2 (it has been seen), so it may be scored as dev; any other case marked "test" needs the test split's guard.
# --------------------------------------------------------------------------------------------------------------------
RETIRED_TEST_SHA256 = {"f1fbcb4311eb7579c1f309c3b9595e55b7dc253d73d7c0592196e0be258db792"}


def load_cases(split: str, files: list[str] | None) -> tuple[list[dict], dict[str, str]]:
    paths = [Path(f) for f in files] if files else list(scorer.SPLIT_FILES[split])
    cases, hashes = [], {}
    for path in paths:
        digest = sha256_file(path)
        hashes[str(path)] = digest
        rows = scorer.load_jsonl(path)
        if split != "test" and digest not in RETIRED_TEST_SHA256 and any(r.get("split") == "test" for r in rows):
            raise SystemExit(f"{path} holds held-out (test) cases; run it with --split test under the test guard")
        cases.extend(rows)
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate case ids across the case files")
    return cases, hashes


# --------------------------------------------------------------------------------------------------------------------
def configure_detection(args) -> dict:
    """Apply the module-level detection settings the gate applies at start-up; return the institution profile."""
    from themis_shield.detectors import llm_finder
    from themis_shield.detectors.rules import set_rule_packs
    from themis_shield.profiles import load_profile_json

    packs = set_rule_packs([p.strip() for p in args.rule_packs.split(",") if p.strip()])
    institution = load_profile_json("institution", args.institution_profile, args.profiles_dir)
    if not isinstance(institution, dict):
        raise SystemExit("invalid institution profile")
    # The "do not list" items normally come from the same institution profile; --do-not-flag-from takes them from
    # another profile (used to reproduce the ported-prompt baseline exactly: v1 + harbourline-demo's list).
    source = institution
    if args.do_not_flag_from:
        source = load_profile_json("institution", args.do_not_flag_from, args.profiles_dir)
    finder = llm_finder.configure_finder(
        context=args.finder_context, prompt_version=args.prompt, do_not_flag=source.get("finder_do_not_flag"),
    )
    versions = configure_gate_versions(args)
    return {"rule_packs": list(packs), "institution": institution, "finder": finder, "versions": versions}


def configure_gate_versions(args) -> dict:
    """Detection and policy behaviour versions (v0.2 by default; v0.1 is the negative control)."""
    from themis_shield.detectors.settings import configure_detection_version
    from themis_shield.policy import configure_policy

    gate = getattr(args, "gate_version", None)
    detection = getattr(args, "detection_version", None) or gate
    policy_version = getattr(args, "policy_version", None) or gate
    quasi = getattr(args, "quasi_id_ask", None)
    return {
        "detection": configure_detection_version(detection),
        "policy": configure_policy(policy_version, quasi_id_ask=None if quasi is None else quasi == "on"),
    }


def finding_label(item) -> str:
    primary = item.primary_label.value
    rest = sorted(label.value for label in item.labels if label.value != primary)
    return label_map.combined_key([primary, *rest])


def guard_test_split(args, run_dir: Path) -> None:
    if args.split != "test":
        return
    if not args.frozen_tag:
        raise SystemExit("the test split runs only with --frozen-tag <tag on HEAD>")
    tags = git("tag", "--points-at", "HEAD").split()
    if args.frozen_tag not in tags:
        raise SystemExit(f"HEAD is not tagged {args.frozen_tag}")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("the test split runs only from a clean tree")
    if (run_dir / "score.json").exists():
        raise SystemExit("this test run already has a score; the test split is run once")
    if run_dir.exists() and any(run_dir.iterdir()) and not args.resume:
        raise SystemExit("run directory exists; use --resume only to continue after a crash")


def llama_servers() -> list[int]:
    import psutil
    found = []
    for process in psutil.process_iter(["name", "pid"]):
        name = (process.info.get("name") or "").lower()
        if name.startswith("llama-server"):
            found.append(process.info["pid"])
    return found


def process_cases(todo, profile, targets, client, use_case, run_dir: Path, chunk_size: int, log,
                  done_before: int = 0, total: int | None = None, allow_stage_failures: bool = False) -> None:
    """detect_batch per chunk, the real policy per case; append predictions and decisions after every chunk."""
    from themis_shield.detectors import detect_batch
    from themis_shield.policy import decide

    total = len(todo) if total is None else total
    with open(run_dir / "predictions.jsonl", "a", encoding="utf-8", newline="\n") as preds, \
            open(run_dir / "decisions.jsonl", "a", encoding="utf-8", newline="\n") as decs, \
            open(run_dir / "timings.jsonl", "a", encoding="utf-8", newline="\n") as times:
        for first in range(0, len(todo), chunk_size):
            chunk = todo[first:first + chunk_size]
            client.case_ids = [c["id"] for c in chunk]
            client.texts = [c["text"] for c in chunk]
            t0 = time.perf_counter()
            results = detect_batch([c["text"] for c in chunk], profile, targets, thorough=True)
            failed = sorted({stage for result in results for stage, report in result.stage_status.items()
                             if report.status.value == "failed"})
            if failed and not allow_stage_failures:
                # A FAILED stage is an infrastructure fault (a model that would not load, a server that died), not a
                # detection result. The gate would fail closed (BLOCK); the evaluation stops before writing this
                # chunk so the fault can be fixed and the run resumed. Malformed output and timeouts still count.
                log(f"STAGE FAILURE in chunk {first // chunk_size + 1}: {failed}; nothing written for this chunk")
                raise RuntimeError("finder stage failed (infrastructure); fix and rerun with --resume")
            for case, result in zip(chunk, results):
                decision = decide(result, use_case)
                if result.policy_result is not None and decision != result.policy_result:
                    raise RuntimeError("policy decision differs between engine and router paths")
                preds.write(json.dumps({
                    "id": case["id"],
                    # "stages" (which detectors produced the finding) is diagnostic; score.py ignores it.
                    "spans": [{"start": f.start, "end": f.end, "label": finding_label(f),
                               "stages": list(f.provenance)} for f in result.findings],
                    "decision": decision.decision.value,
                }, ensure_ascii=False) + "\n")
                decs.write(json.dumps({
                    "id": case["id"], "decision": decision.decision.value,
                    "reasons": [r.value for r in decision.reasons],
                    "tiers_run": list(result.tiers_run), "escalation_reasons": list(result.escalation_reasons),
                    "injection_signal": result.injection_signal, "doc_sensitivity": result.doc_sensitivity,
                    "findings": len(result.findings), "text_chars": len(case["text"]),
                    "stage_status": {
                        stage: {"status": r.status.value, "error_code": r.error_code, "findings": r.finding_count,
                                "persons": r.person_count, "values_not_found": r.values_not_found}
                        for stage, r in result.stage_status.items()
                    },
                }) + "\n")
                # Timings live in their own file so that predictions and decisions are reproducible byte for byte.
                times.write(json.dumps({
                    "id": case["id"], "timings_ms": {str(k): round(v, 1) for k, v in result.timings_ms.items()},
                }) + "\n")
            preds.flush()
            decs.flush()
            times.flush()
            log(f"chunk {first // chunk_size + 1}: {len(chunk)} cases in {time.perf_counter() - t0:.0f} s "
                f"({done_before + first + len(chunk)}/{total})")


def run_detection(args, run_dir: Path, cases: list[dict], log, case_hashes: dict | None = None) -> dict:
    from themis_shield.config import DeploymentProfile, UseCasePolicy
    from themis_shield.detectors import DetectionTargets
    from themis_shield.runtime import RuntimeSettings, build_runtime
    from themis_shield.runtime.catalogue import Catalogue
    from themis_shield.runtime.modes import SequentialFinderClient

    configured = configure_detection(args)
    use_case = UseCasePolicy("evaluation", (), requires_in_region_inference=False)
    profile = DeploymentProfile("evaluation", "1", (), (use_case,), institution=configured["institution"])

    settings = RuntimeSettings.load(Path(args.runtime_settings)) if args.runtime_settings else RuntimeSettings()
    if args.catalogue:
        settings = replace(settings, catalogue_file=Path(args.catalogue))
    catalogue = Catalogue.load(settings.catalogue_file)
    tracer_dir = Path(args.tracer_dir) if args.tracer_dir else settings.models_dir / "pii-tracer"
    identities = finder_identities(catalogue)

    config = {
        "written_utc": now(), "args": vars(args), "git_head": git("rev-parse", "HEAD"),
        "git_describe": git("describe", "--tags", "--always", "--dirty"),
        "git_dirty_files": git("status", "--porcelain", "--untracked-files=no").splitlines(),
        "python": sys.version, "platform": platform.platform(),
        "case_files_sha256": case_hashes or {p.name: sha256_file(p) for p in scorer.SPLIT_FILES[args.split]},
        "rule_packs": configured["rule_packs"], "finder_context": args.finder_context,
        "prompt_version": args.prompt, "institution_profile": args.institution_profile,
        "gate_versions": configured["versions"],
        "finder_settings": {k: (list(v) if isinstance(v, tuple) else v) for k, v in configured["finder"].items()},
        "runtime_settings": {name: str(getattr(settings, name)) for name in settings.__dataclass_fields__},
        "finders": identities,
        "tracer_dir": str(tracer_dir), "llm_max_tokens": args.max_tokens,
        "finder_mode": "replay" if args.replay_finders else ("live+capture" if args.capture_finders else "live"),
    }
    engine = Path(settings.engine_path)
    try:
        version = subprocess.run([str(engine), "--version"], capture_output=True, text=True, timeout=60)
        config["engine_version"] = (version.stdout + version.stderr).strip().splitlines()[-2:]
    except (OSError, subprocess.SubprocessError):
        config["engine_version"] = None
    config_path = run_dir / "config.json"
    if config_path.exists():
        previous = json.loads(config_path.read_text(encoding="utf-8"))
        config["resumed_from"] = previous.get("resumed_from", []) + [previous.get("written_utc")]
    config_path.write_text(json.dumps(config, indent=1), encoding="utf-8")

    pred_path = run_dir / "predictions.jsonl"
    done = set()
    if pred_path.exists():
        done = {json.loads(line)["id"] for line in pred_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    todo = [c for c in cases if c["id"] not in done]
    log(f"{len(cases)} cases, {len(done)} already done, {len(todo)} to run")

    replay = capture = runtime = None
    if args.replay_finders:
        replay = FinderReplay(Path(args.replay_finders), identities)
        log(f"replaying finder responses from {args.replay_finders} ({len(replay.records)} captured calls)")
    else:
        if args.capture_finders:
            capture = FinderCapture(Path(args.capture_finders), identities, {
                "prompt_version": args.prompt, "finder_context": args.finder_context,
                "institution_profile": args.institution_profile, "engine_version": config["engine_version"],
                "case_files_sha256": config["case_files_sha256"], "git_head": config["git_head"],
            })
        runtime = build_runtime(settings, catalogue)
        # Windows releases GPU memory some seconds after a model server exits; the scheduler re-reads free memory for
        # memory_settle_seconds (5 by default) before refusing a load. The harness allows longer so that a slow
        # release is not scored as a detection failure.
        runtime.memory_settle_seconds = args.memory_settle_seconds
    began = time.perf_counter()
    history = []
    try:
        with open(run_dir / "llm_calls.jsonl", "a", encoding="utf-8", newline="\n") as sink:
            inner = object() if replay is not None else SequentialFinderClient(runtime)
            client = RecordingClient(inner, sink, capture=capture, replay=replay)
            targets = DetectionTargets(
                llm_client=client, thorough=True, tracer_model_directory=str(tracer_dir),
                llm_max_tokens=args.max_tokens,
            )
            process_cases(todo, profile, targets, client, use_case, run_dir, args.chunk, log, len(done), len(cases),
                          allow_stage_failures=args.allow_stage_failures)
            if runtime is not None:
                history = runtime.status().get("timings", [])
    finally:
        if runtime is not None:
            runtime.close()
        if capture is not None:
            capture.close()
    leftover = llama_servers()
    for model_role, templates in client.templates.items():
        (run_dir / f"prompt-{model_role}.txt").write_text(
            "\n\n".join(f"### {m['role']}\n{m['content']}" for m in client.first_messages[model_role]),
            encoding="utf-8")
    return {
        "wall_seconds_this_session": round(time.perf_counter() - began, 1),
        "scheduler_history": history,
        "llama_server_left_running": leftover,
        "templates_per_model": {k: len(v) for k, v in client.templates.items()},
        "replayed_calls": replay.served if replay is not None else None,
    }


# --------------------------------------------------------------------------------------------------------------------
def summarise(rep: dict, decisions: list[dict], calls: list[dict], cases: list[dict],
              timings: list[dict] | None = None) -> dict:
    rows = rep["per_label"]

    def count(label, key):
        r = rows.get(label)
        return 0 if not r or r[key] is None else round(r[key] * r["gold"])

    sens_gold = sum(rows[L]["gold"] for L in SENSITIVE_GOLD if L in rows)
    sens_any = sum(count(L, "recall_any") for L in SENSITIVE_GOLD)
    sens_full = sum(count(L, "recall_full") for L in SENSITIVE_GOLD)
    n = len(decisions)
    dec_counts = {d: sum(1 for x in decisions if x["decision"] == d) for d in ("RELEASE", "ASK", "BLOCK")}
    reasons: dict[str, int] = {}
    for x in decisions:
        for r in x["reasons"]:
            reasons[r] = reasons.get(r, 0) + 1
    hn = rep["hard_negatives"]["by_kind"]
    look_total = sum(hn.get(k, {}).get("total", 0) for k in SENSITIVE_LOOKALIKE_KINDS)
    look_hit = sum(hn.get(k, {}).get("hit", 0) for k in SENSITIVE_LOOKALIKE_KINDS)
    clean_ids = {c["id"] for c in cases if not c["case_level"]["contains_pi"]}
    clean = [x for x in decisions if x["id"] in clean_ids]

    speeds = {}
    for model in FINDER_IDS:
        ok = [c for c in calls if c["model"] == model and c["status"] == "ok"]
        gen = [c["timings"].get("predicted_per_second") for c in ok if c["timings"].get("predicted_per_second")]
        pro = [c["timings"].get("prompt_per_second") for c in ok if c["timings"].get("prompt_per_second")]
        cached = [c["timings"].get("cache_n") for c in ok if c["timings"].get("cache_n") is not None]
        speeds[model] = {
            "calls": len([c for c in calls if c["model"] == model]), "ok": len(ok),
            "gen_tok_s_median": round(statistics.median(gen), 1) if gen else None,
            "prompt_tok_s_median": round(statistics.median(pro), 1) if pro else None,
            "gen_tokens_total": sum(c["timings"].get("predicted_n", 0) for c in ok),
            "prompt_tokens_processed_total": sum(c["timings"].get("prompt_n", 0) for c in ok),
            "prompt_tokens_cached_total": sum(cached) if cached else None,
            "seconds_per_call_median": round(statistics.median([c["wall_ms"] for c in ok]) / 1000, 2) if ok else None,
            "seconds_per_call_mean": round(statistics.mean([c["wall_ms"] for c in ok]) / 1000, 2) if ok else None,
        }
    # v0.1 runs kept timings inside decisions.jsonl; v0.2 runs keep them in timings.jsonl.
    per_doc = [sum(x["timings_ms"].values()) / 1000 for x in (timings or decisions) if "timings_ms" in x]
    o = rep["overall"]
    g = rep["gate"]
    return {
        "cases": n, "gold_spans": o["gold"],
        "R_full": o["recall_full"], "R_any": o["recall_any"], "R_typed": o["recall_typed"],
        "precision_any_label": o["precision_any_label"], "precision_typed": o["precision_typed"],
        "char_recall": o["char_recall"], "leaks_untouched": o["leak"], "partials": o["partial"],
        "sensitive_R_any": sens_any / sens_gold if sens_gold else None,
        "sensitive_R_full": sens_full / sens_gold if sens_gold else None,
        "per_label": {L: {"gold": r["gold"], "R_full": r["recall_full"], "R_any": r["recall_any"],
                          "R_typed": r["recall_typed"], "leak": r["leak"], "partial": r["partial"]}
                      for L, r in rows.items() if L in scorer.GOLD_LABELS},
        "released_leaks": g["released_leaks"], "released_leaks_by_label": g["released_leaks_by_label"],
        "injection_cases": g["injection_cases"], "injection_released_leaks": g["injection_released_leaks"],
        "decisions": dec_counts, "ask_rate": dec_counts["ASK"] / n if n else None,
        "block_rate": dec_counts["BLOCK"] / n if n else None, "reason_counts": dict(sorted(reasons.items())),
        "friction_on_clean_cases": g["friction_on_cases_without_pi"],
        "clean_cases": len(clean), "clean_case_decisions": {d: sum(1 for x in clean if x["decision"] == d)
                                                             for d in ("RELEASE", "ASK", "BLOCK")},
        "hard_negative_over_redaction": rep["hard_negatives"]["over_redaction_rate"],
        "hard_negatives_hit": rep["hard_negatives"]["hit"], "hard_negatives_total": rep["hard_negatives"]["total"],
        "sensitive_lookalike_over_redaction": look_hit / look_total if look_total else None,
        "sensitive_lookalikes_hit": look_hit, "sensitive_lookalikes_total": look_total,
        "case_level": {k: rep["case_level"][k] for k in ("contains_pi_recall", "contains_pi_false_positive_rate",
                                                          "any_sensitive_recall")},
        "speed": speeds,
        "seconds_per_document_median": round(statistics.median(per_doc), 2) if per_doc else None,
        "seconds_per_document_mean": round(statistics.mean(per_doc), 2) if per_doc else None,
        "stage_failures": sum(1 for x in decisions for s in x["stage_status"].values() if s["status"] != "ok"),
    }


def score_run(run_dir: Path, split: str, cases: list[dict], extra: dict | None = None) -> dict:
    preds = {}
    for line in (run_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            preds[row["id"]] = row
    keys = {span["label"] for row in preds.values() for span in row["spans"]}
    scorer.LABELSETS["themis"] = label_map.labelset_for(keys)
    rep = scorer.score(cases, preds, "themis", "RELEASE")
    (run_dir / "score.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        scorer.print_report(rep, show=40)
    (run_dir / "score.txt").write_text(buffer.getvalue(), encoding="utf-8")
    decisions = [json.loads(l) for l in (run_dir / "decisions.jsonl").read_text(encoding="utf-8").splitlines() if l]
    calls_path = run_dir / "llm_calls.jsonl"
    calls = [json.loads(l) for l in calls_path.read_text(encoding="utf-8").splitlines() if l] if calls_path.exists() else []
    timings_path = run_dir / "timings.jsonl"
    timings = ([json.loads(l) for l in timings_path.read_text(encoding="utf-8").splitlines() if l]
               if timings_path.exists() else None)
    summary = summarise(rep, decisions, calls, cases, timings)
    if extra:
        summary["run"] = extra
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    ap.add_argument("--run-name", required=True)
    ap.add_argument("--runs-dir", default=os.environ.get("THEMIS_EVAL_RUNS"),
                    help="where run folders go (outside the repository); default $THEMIS_EVAL_RUNS")
    ap.add_argument("--runtime-settings", default=None, help="runtime settings JSON (engine, models, data dir)")
    ap.add_argument("--catalogue", default=None, help="catalogue patch JSON (e.g. another 26B quantisation)")
    ap.add_argument("--tracer-dir", default=None, help="PII-Tracer directory (default <models_dir>/pii-tracer)")
    ap.add_argument("--profiles-dir", default=None, help="external profiles directory")
    ap.add_argument("--institution-profile", default=DEFAULT_PROFILE)
    ap.add_argument("--finder-context", default=DEFAULT_CONTEXT)
    ap.add_argument("--rule-packs", default=DEFAULT_RULE_PACKS)
    ap.add_argument("--prompt", default="v2", help="finder prompt version (v1 or v2)")
    ap.add_argument("--do-not-flag-from", default=None,
                    help="take finder_do_not_flag from this institution profile instead (baseline reproduction)")
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--chunk", type=int, default=40, help="documents per detect_batch call (checkpoint size)")
    ap.add_argument("--limit", type=int, default=None, help="first N cases only (smoke runs; never for results)")
    ap.add_argument("--ids", default=None, help="comma-separated case ids only (smoke runs)")
    ap.add_argument("--memory-settle-seconds", type=float, default=60,
                    help="how long the scheduler waits for GPU memory to be released before refusing a load")
    ap.add_argument("--allow-stage-failures", action="store_true",
                    help="score FAILED finder stages as the gate would (BLOCK) instead of stopping")
    ap.add_argument("--resume", action="store_true", help="continue a crashed run in the same folder")
    ap.add_argument("--frozen-tag", default=None, help="required for --split test: the tag on HEAD")
    ap.add_argument("--score-only", action="store_true", help="re-score an existing run folder")
    ap.add_argument("--cases", nargs="+", default=None,
                    help="case file(s) to run instead of the split's bundled file (e.g. a merged dev file)")
    ap.add_argument("--capture-finders", default=None,
                    help="write every finder response (with prompt hash and model identity) to this folder")
    ap.add_argument("--replay-finders", default=None,
                    help="serve finder responses from a capture folder instead of calling llama-server; refuses "
                         "any call whose prompt hash or model identity differs")
    ap.add_argument("--gate-version", choices=["v0.1", "v0.2"], default=None,
                    help="detection and policy behaviour together (v0.1 = the negative control)")
    ap.add_argument("--detection-version", choices=["v0.1", "v0.2"], default=None)
    ap.add_argument("--policy-version", choices=["v0.1", "v0.2"], default=None)
    ap.add_argument("--quasi-id-ask", choices=["on", "off"], default=None,
                    help="v0.2 policy: whether a found QUASI_ID holds the document for ASK (default: the policy's)")
    args = ap.parse_args(argv)
    if not args.runs_dir:
        ap.error("--runs-dir (or THEMIS_EVAL_RUNS) is required")
    runs_dir = Path(args.runs_dir).resolve()
    if runs_dir.is_relative_to(ROOT.resolve()):
        ap.error("run outputs must live outside the repository")
    if args.capture_finders and args.replay_finders:
        ap.error("--capture-finders and --replay-finders are exclusive")
    for folder in (args.capture_finders, args.replay_finders):
        if folder and Path(folder).resolve().is_relative_to(ROOT.resolve()):
            ap.error("finder captures must live outside the repository")
    run_dir = runs_dir / args.run_name
    cases, case_hashes = load_cases(args.split, args.cases)
    if args.ids:
        wanted = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    if args.limit:
        cases = cases[:args.limit]
    if args.score_only:
        print(json.dumps(score_run(run_dir, args.split, cases), indent=1)[:4000])
        return 0
    guard_test_split(args, run_dir)
    if run_dir.exists() and (run_dir / "predictions.jsonl").exists() and not args.resume:
        ap.error(f"{run_dir} already has predictions; pick a new --run-name or --resume")
    run_dir.mkdir(parents=True, exist_ok=True)
    log_file = open(run_dir / "run.log", "a", encoding="utf-8")

    def log(message):
        line = f"{now()} {message}"
        print(line, flush=True)
        log_file.write(line + "\n")
        log_file.flush()

    log(f"run {args.run_name} split={args.split} prompt={args.prompt} head={git('rev-parse', '--short', 'HEAD')}")
    started = time.perf_counter()
    extra = run_detection(args, run_dir, cases, log, case_hashes)
    extra["wall_seconds_total_this_session"] = round(time.perf_counter() - started, 1)
    summary = score_run(run_dir, args.split, cases, extra)
    log(f"R_full {summary['R_full']:.3f}  R_any {summary['R_any']:.3f}  precision {summary['precision_any_label']:.3f}"
        f"  released leaks {summary['released_leaks']}  decisions {summary['decisions']}"
        f"  llama-server left running: {extra['llama_server_left_running'] or 'none'}")
    log_file.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
