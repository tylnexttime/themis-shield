"""Server-owned managed-hooks mode state, using the existing router's engine and stores.


This module is never imported by the hook CLI. No cloud inference is performed.
Hook policy is trusted server configuration, not a hook payload field.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
import hashlib
import os
from pathlib import Path
from themis_shield.paths import app_data_dir
import re
import stat
from threading import Lock, RLock

from themis_shield.chat_boundary import Surface
from themis_shield.cloud.openai_compat import canonical, strict_json, verify_json
from themis_shield.detectors.markings import detect_markings
from themis_shield.placeholders import SENSITIVE_LABELS, escape_literals
from themis_shield.register.client import JobStatus as S
from themis_shield.register.models import JobIntake, canonical_json
from themis_shield.restore import restore_text
from themis_shield.routing import Route, route_request
from themis_shield.types import Decision, Label

from .protocol import (
    DEFAULT_TOOLS, EXECUTABLE_TOOLS, LOCAL_MESSAGE, OUTPUT_TOOLS,
    bounded, prompt_block, session_id, tool_deny, tool_input, tool_output,
)


TEXT_SUFFIXES = frozenset({
    ".txt", ".md", ".csv", ".json", ".jsonl", ".py", ".toml", ".yaml", ".yml",
    ".log", ".ts", ".js", ".html", ".css",
})
MAX_FILE = 2 * 1024 * 1024
MAX_TREE_FILES = 128
MAX_TREE_BYTES = 8 * 1024 * 1024
TOKEN_PATTERN = re.compile(r"⟦[PCAIDSQ]:[0-9a-f]{6}⟧")


@dataclass(frozen=True)
class HookPolicy:
    # Direct Claude approval must not inherit a cloud-API approval.
    # Default is preparation-only: explicitly enable this synthetic destination.
    cloud_authorised: bool = False
    destination_id: str = "claude-direct-synthetic"
    model: str = "claude-code-approved-build"
    deployment_type: str = "claude-managed"
    allowed_tools: frozenset[str] = DEFAULT_TOOLS
    roots: tuple[Path, ...] = ()
    # Where de-identified prompt drafts are written; None = <app data>/hooks.
    drafts_dir: Path | None = None

    def __post_init__(self):
        if type(self.cloud_authorised) is not bool:
            raise ValueError("INVALID_HOOK_POLICY")
        if (
            not isinstance(self.allowed_tools, frozenset)
            or not self.allowed_tools <= DEFAULT_TOOLS
            or not isinstance(self.roots, tuple)
        ):
            raise ValueError("INVALID_HOOK_POLICY")
        if self.drafts_dir is not None and (
            not isinstance(self.drafts_dir, Path) or not self.drafts_dir.is_absolute()
        ):
            raise ValueError("INVALID_HOOK_POLICY")
        for alias in (self.destination_id, self.model, self.deployment_type):
            if not isinstance(alias, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", alias,
            ):
                raise ValueError("INVALID_HOOK_ALIAS")


@dataclass
class Session:
    lock: Lock = field(default_factory=Lock, repr=False)
    issued: set[str] = field(default_factory=set, repr=False)
    jobs: dict[str, str] = field(default_factory=dict, repr=False)
    active_job: str | None = None
    draft_number: int = 0
    local_only: bool = False
    stopped: bool = False


class ObjectBoundary:
    """Expose complete string leaves to Router._protect, with fixed JSON keys."""

    def __init__(self, value):
        self.body = deepcopy(value)
        self.surfaces = []

        def walk(item, path=()):
            if isinstance(item, str):
                self.surfaces.append(Surface(path, item))
            elif isinstance(item, list):
                for index, child in enumerate(item):
                    walk(child, (*path, index))
            elif isinstance(item, dict):
                for key, child in item.items():
                    # Fixed schema keys are inspected but never rewritten.
                    self.surfaces.append(Surface((*path, key), key, False))
                    walk(child, (*path, key))

        walk(self.body)
        if len(self.surfaces) > 512:
            raise ValueError("HOOK_SURFACE_BOUND")

    def final_body(self, maximum_tokens, model=None):
        return deepcopy(self.body)


class HookService:
    def __init__(self, router, policy):
        if router.use_case.synthetic_only is not True:
            raise PermissionError("SYNTHETIC_ONLY")
        self.router = router
        self.policy = policy
        self.lock = RLock()
        self.sessions = {}

    def key(self, identifier):
        return "claude:" + session_id(identifier)

    def _get(self, identifier, *, create):
        identifier = session_id(identifier)
        with self.lock:
            state = self.sessions.get(identifier)
            if state is None:
                if not create:
                    raise PermissionError("SESSION_UNKNOWN")
                if len(self.sessions) >= 256:
                    raise PermissionError("HOOK_SESSION_CAPACITY")
                state = Session()
                self.sessions[identifier] = state
            return identifier, state

    @contextmanager
    def session(self, identifier, *, create=False, allow_stopped=False):
        identifier, state = self._get(identifier, create=create)
        if not state.lock.acquire(timeout=1):
            raise PermissionError("SESSION_BUSY")
        try:
            if state.stopped and not allow_stopped:
                raise PermissionError("SESSION_STOPPED")
            yield identifier, state
        finally:
            state.lock.release()

    def _mapping(self, identifier, state):
        available = self.router.vault.mapping(self.key(identifier))
        if not state.issued <= available.keys():
            raise PermissionError("SESSION_MAPPING_UNAVAILABLE")
        return {token: available[token] for token in state.issued}

    def _mask_tokens(self, text, tokens):
        # Tokens are opaque protocol values, not detector input identities.
        return TOKEN_PATTERN.sub(
            lambda match: " " * len(match.group())
            if match.group() in tokens else match.group(),
            text,
        )

    def _strings(self, value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for item in value:
                yield from self._strings(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from self._strings(item)

    def _protect(self, identifier, state, value):
        """Use Router._protect, then rescan the exact replacement to convergence."""
        current = deepcopy(value)
        counts = Counter()
        tokens = set(state.issued)
        timings = {}
        decision = Decision.RELEASE
        for _ in range(3):
            boundary = ObjectBoundary(current)
            raw, mapping, sent, originals, result, measured, summary = (
                self.router._protect(boundary, self.key(identifier), set())
            )
            decision = (
                Decision.ASK
                if decision == Decision.ASK or result.decision == Decision.ASK
                else result.decision
            )
            timings = measured
            counts.update(summary)
            current = strict_json(raw)
            authorised = tokens | set(sent)

            def preserve(item):
                if isinstance(item, str):
                    # Only server-issued tokens may survive a resubmitted
                    # canonical draft. Other literal tokens stay escaped.
                    for token in authorised:
                        item = item.replace(escape_literals(token), token)
                    return item
                if isinstance(item, list):
                    return [preserve(child) for child in item]
                if isinstance(item, dict):
                    return {key: preserve(child) for key, child in item.items()}
                return item

            current = preserve(current)
            verify_json(canonical(current), originals, decode_arguments=False)
            scan_text = "\n\n".join(
                self._mask_tokens(item, authorised)
                for item in self._strings(current)
            )
            rescan = self.router._scan(scan_text)
            if rescan.decision == Decision.BLOCK:
                raise PermissionError("FINAL_SCAN_BLOCKED")
            if rescan.decision == Decision.ASK:
                decision = Decision.ASK
            if not rescan.findings:
                state.issued.update(authorised)
                return current, dict(counts), decision, timings
            tokens = authorised
        raise PermissionError("HOOK_NON_CONVERGENCE")

    def _job(self, identifier, state, clean, counts, decision, routing, timings):
        # Hash only the CLEAN package; never persist an original-value hash.
        package = {
            "schema_version": 1,
            "event": "UserPromptSubmit",
            "prompt": clean,
            "destination_id": self.policy.destination_id,
            "model": self.policy.model,
            "deployment_type": self.policy.deployment_type,
            "policy_version": self.router.profile.profile_version,
        }
        digest = hashlib.sha256(canonical(package)).hexdigest()
        existing = state.jobs.get(digest)
        if existing:
            return self.router._register_call("get_job", existing), digest
        if len(state.jobs) >= 256:
            raise PermissionError("HOOK_JOB_CAPACITY")
        candidate = JobIntake(
            "", self.router.clock(), S.LOGGED, Route.CLOUD,
            marking_class=routing.audit_fields.marking_class,
            destination_id=self.policy.destination_id,
            model=self.policy.model,
            deployment_type=self.policy.deployment_type,
            source_document_count=1, source_word_count=len(clean.split()),
            requester_identity=self.router.identity,
            client="CLAUDE_CODE", integration_mode="managed-hooks", synthetic=True,
            use_case_record_ref="SYNTHETIC-ENDORSEMENT",
            problem_summary_deid="Process the synthetic clean Claude prompt.",
            scope_json=(
                '{"schema_version":1,"release_profile":"claude-prompt-v1",'
                '"tool_profile":"claude-text-v1"}'
            ),
            gate_version="m6a-v1",
            policy_version=self.router.profile.profile_version,
        )
        job = self.router._register_call(
            "log_job", candidate, now=self.router.clock(),
        )
        state.jobs[digest] = job.job_id
        self.router._register_call(
            "request_stage1", job.job_id, now=self.router.clock(),
        )
        metadata = replace(
            candidate,
            job_id=job.job_id, created_utc=job.created_utc,
            manifest_hash=digest,
            pii_counts_json=canonical_json(counts),
            sensitive_categories_present=any(
                Label(label) in SENSITIVE_LABELS for label in counts
            ),
            confirmation_required=routing.mandatory_confirm or decision == Decision.ASK,
        )
        self.router._register_call(
            "set_package", metadata,
            timings={"tiers_ms": {str(key): value for key, value in timings.items()}},
            now=self.router.clock(),
        )
        return self.router._register_call("get_job", job.job_id), digest

    def _draft(self, identifier, state, clean):
        base = self.policy.drafts_dir if self.policy.drafts_dir is not None else app_data_dir() / "hooks"
        root = base / identifier
        if root.is_symlink() or any(parent.is_symlink() for parent in root.parents):
            raise PermissionError("DRAFT_STORAGE_UNSAFE")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # O_EXCL survives server restart without overwriting an earlier draft.
        for _ in range(1000):
            state.draft_number += 1
            path = root / f"prompt-{state.draft_number}.txt"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(path, flags, 0o600)
            except FileExistsError:
                continue
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(clean.encode("utf-8"))
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                path.unlink(missing_ok=True)
                raise
            return str(path)
        raise PermissionError("DRAFT_NUMBER_BOUND")

    def prompt(self, identifier, prompt):
        with self.session(identifier, create=True) as (identifier, state):
            routing = route_request(detect_markings(prompt), self.router.use_case)
            if state.local_only or routing.route == Route.LOCAL_ONLY:
                state.local_only = True
                return prompt_block(LOCAL_MESSAGE)
            clean_value, counts, decision, timings = self._protect(
                identifier, state, {"prompt": prompt},
            )
            clean = clean_value["prompt"]
            if counts:
                path = self._draft(identifier, state, clean)
                reason = (
                    "Prompt blocked. A de-identified version was written to "
                    + path + ". Open it and paste that instead."
                )
                verify_json(
                    canonical({"reason": reason}),
                    tuple(self._mapping(identifier, state).values()),
                    decode_arguments=False,
                )
                return prompt_block(reason)
            if (
                not self.policy.cloud_authorised
                or not self.router.use_case.approved_for_cloud
                or self.router.use_case.requires_in_region_inference
            ):
                return prompt_block("Direct Claude destination is not authorised.")
            job, digest = self._job(
                identifier, state, clean, counts, decision, routing, timings,
            )
            if job.status in {S.REFUSED, S.WITHDRAWN, S.EXPIRED, S.COMPLETED}:
                return prompt_block("This Claude job is closed; start a new session.")
            self.router._register_call(
                "finalize_stages", job.job_id, now=self.router.clock(),
            )
            row = self.router._register_call("get_details", job.job_id)
            if row["confirmation_required"] and row["sensitive_path"] is None:
                # Handling-marker/technical ASK may have confirmation without sensitive_path.
                # Authoritative current_approval below remains the final test.
                approval = self.router._register_call(
                    "current_approval", job.job_id, self.router.clock(),
                )
            else:
                approval = self.router._register_call(
                    "current_approval", job.job_id, self.router.clock(),
                )
            if approval is None:
                return prompt_block(
                    "held for operator approval " + job.job_id
                    + "; both stages and any required requester confirmation "
                    "must be complete. Re-submit afterwards."
                )
            with self.router.register.release_guard(
                job.job_id, digest, now=self.router.clock(),
            ):
                state.active_job = job.job_id
            return {}

    def _active(self, state):
        if state.local_only or not state.active_job:
            raise PermissionError("NO_ACTIVE_CLOUD_JOB")
        approval = self.router._register_call(
            "current_approval", state.active_job, self.router.clock(),
        )
        if approval is None:
            raise PermissionError("CLOUD_JOB_NOT_APPROVED")

    def _restore_input(self, identifier, state, value):
        mapping = self._mapping(identifier, state)

        def walk(item):
            if isinstance(item, str):
                restored = restore_text(item, mapping)
                if restored.report.unknown_count or restored.report.tolerant_count:
                    raise PermissionError("TOOL_TOKEN_NOT_STRICT")
                return restored.text
            if isinstance(item, list):
                return [walk(child) for child in item]
            if isinstance(item, dict):
                for key in item:
                    checked = restore_text(key, mapping)
                    if (
                        checked.text != key
                        or checked.report.unknown_count
                        or checked.report.tolerant_count
                    ):
                        raise PermissionError("TOKEN_IN_TOOL_KEY")
                return {key: walk(child) for key, child in item.items()}
            return item

        return walk(value)

    def _path(self, value):
        path = Path(value)
        if not path.is_absolute():
            raise PermissionError("ABSOLUTE_LOCAL_PATH_REQUIRED")
        if (
            value.startswith(("\\\\", "//"))
            or ".." in path.parts
            or any(":" in part for part in path.parts[1:])
        ):
            raise PermissionError("NONLOCAL_PATH")
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise PermissionError("LINKED_PATH")
        resolved = path.resolve(strict=True)
        roots = [Path(root).resolve(strict=True) for root in self.policy.roots]
        if not roots or not any(resolved.is_relative_to(root) for root in roots):
            raise PermissionError("PATH_OUTSIDE_LOCAL_ROOT")
        for component in (path, *path.parents):
            metadata = component.lstat()
            if getattr(metadata, "st_file_attributes", 0) & 0x400:
                raise PermissionError("REPARSE_POINT")
        return resolved

    def _file(self, path):
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > MAX_FILE
            or path.suffix.casefold() not in TEXT_SUFFIXES
        ):
            raise PermissionError("UNSUPPORTED_LOCAL_FILE")
        raw = path.read_bytes()
        if len(raw) > MAX_FILE or b"\x00" in raw:
            raise PermissionError("INCOMPLETE_TEXT_FILE")
        text = raw.decode("utf-8-sig")
        if route_request(
            detect_markings(text), self.router.use_case,
        ).route == Route.LOCAL_ONLY:
            raise PermissionError("MANDATORY_LOCAL_FILE")
        return len(raw)

    def _tree(self, path):
        if path.is_file():
            self._file(path)
            return
        files, byte_count = 0, 0
        for directory, directories, filenames in os.walk(path, followlinks=False):
            for name in directories:
                self._path(str(Path(directory) / name))
            for name in filenames:
                file = self._path(str(Path(directory) / name))
                files += 1
                byte_count += self._file(file)
                if files > MAX_TREE_FILES or byte_count > MAX_TREE_BYTES:
                    raise PermissionError("READ_SCOPE_BOUND")

    def pre_tool(self, identifier, tool, value):
        with self.session(identifier) as (identifier, state):
            # Deny before inspecting/restoring arguments for an unsafe tool.
            if tool not in self.policy.allowed_tools:
                return tool_deny("Tool is not on the local allow-list.")
            if tool not in EXECUTABLE_TOOLS:
                return tool_deny("Tool has no implemented, tested output profile.")
            try:
                self._active(state)
                restored = self._restore_input(identifier, state, value)
                tool_input(tool, restored)
                if tool == "Read":
                    self._file(self._path(restored["file_path"]))
                else:
                    scope = restored.get("path")
                    if scope is None:
                        if len(self.policy.roots) != 1:
                            raise PermissionError("EXPLICIT_TOOL_ROOT_REQUIRED")
                        scope = str(self.policy.roots[0])
                        restored["path"] = scope
                    if tool == "Glob":
                        pattern = restored["pattern"]
                        if (
                            Path(pattern).is_absolute()
                            or ".." in pattern.replace("\\", "/").split("/")
                            or ":" in pattern
                        ):
                            raise PermissionError("UNSAFE_GLOB")
                    # Grep can read many files; do not check only a named file.
                    self._tree(self._path(scope))
            except PermissionError as failure:
                if failure.args == ("MANDATORY_LOCAL_FILE",):
                    state.local_only = True
                    if state.active_job:
                        self.router._register_call(
                            "gate_refuse", state.active_job, now=self.router.clock(),
                        )
                    return tool_deny(LOCAL_MESSAGE)
                return tool_deny("Local tool action held: approval, path or schema check failed.")
            except (ValueError, UnicodeError, OSError):
                return tool_deny("Local tool action held: complete supported text is required.")
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": restored,
                },
            }

    def post_tool(self, identifier, tool, value):
        with self.session(identifier) as (identifier, state):
            if tool not in OUTPUT_TOOLS:
                return {
                    "decision": "block",
                    "reason": "Unexpected tool output: no supported replacement profile.",
                }
            self._active(state)
            tool_output(tool, value)
            for item in self._strings(value):
                if route_request(
                    detect_markings(item), self.router.use_case,
                ).route == Route.LOCAL_ONLY:
                    state.local_only = True
                    self.router._register_call(
                        "gate_refuse", state.active_job, now=self.router.clock(),
                    )
                    return {"decision": "block", "reason": LOCAL_MESSAGE}
            clean, _, _, _ = self._protect(identifier, state, value)
            tool_output(tool, clean)
            verify_json(
                canonical(clean),
                tuple(self._mapping(identifier, state).values()),
                decode_arguments=False,
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "updatedToolOutput": clean,
                },
            }

    def display(self, identifier, value):
        with self.session(identifier, allow_stopped=True) as (identifier, state):
            restored = restore_text(value, self._mapping(identifier, state))
            # Unknown tokens remain visible; never resolve another conversation.
            return {"displayContent": restored.text}

    def start(self, identifier):
        with self.session(identifier, create=True):
            return {}

    def stop(self, identifier):
        with self.session(
            identifier, create=True, allow_stopped=True,
        ) as (_, state):
            if state.stopped:
                return {}
            for identifier in set(state.jobs.values()):
                job = self.router._register_call("get_job", identifier)
                if job.status in {S.APPROVED, S.AUTO_ALLOWED_LOCAL}:
                    self.router._register_call(
                        "complete", identifier, now=self.router.clock(),
                    )
                elif job.status in {
                    S.LOGGED, S.AWAITING_OPERATOR, S.ESCALATED_REVIEW, S.HELD_OFFLINE,
                }:
                    self.router._register_call(
                        "withdraw", identifier,
                        requester_identity=self.router.identity,
                        now=self.router.clock(),
                    )
            state.active_job = None
            state.stopped = True
            return {}

    def confirm(self, identifier, job_id, manifest_hash):
        """Explicit local requester action; never called by a prompt hook."""
        with self.session(identifier) as (_, state):
            if state.jobs.get(manifest_hash) != job_id:
                raise PermissionError("CONFIRMATION_BINDING")
            row = self.router._register_call("get_details", job_id)
            self.router._register_call(
                "confirm_payload", job_id,
                requester_identity=self.router.identity,
                revision=row["approval_revision"], manifest_hash=manifest_hash,
                now=self.router.clock(),
            )
            self.router._register_call(
                "finalize_stages", job_id, now=self.router.clock(),
            )
            return {}
