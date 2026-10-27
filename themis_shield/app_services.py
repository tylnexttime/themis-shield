"""Safe app projections, bounded preview custody and handover-mode snapshots."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import secrets
from threading import RLock
import time
import zipfile

from .cloud.openai_compat import strict_json
from .detectors.markings import (
    MarkingClass, active_marking_profile, detect_markings, marking_policy, summarise_markings,
)
from .handover import (
    HandoverManifest, ManifestFile, PeerCheck, canonical_manifest, manifest_hash, verify_snapshot,
)
from .placeholders import token_class_for
from .routing import Route, route_request
from .runtime.modes import entries
from .summary import TOKEN_PATTERN
from .types import Decision


def job_view(row):
    local = row["route"] == Route.LOCAL_ONLY.value
    return {
        "job_id": row["job_id"], "route": "local" if local else "cloud-tokenised",
        "marking_class": row["marking_class"], "counts": json.loads(row["pii_counts_json"]),
        "summary": row["problem_summary_deid"], "status": row["status"],
        "stage1": None if local else row["stage1"],
        "package": None if local else row["package"],
        "stage2": None if local else row["stage2"],
        "due": row["due_utc"], "expiry": row["expiry_utc"],
        "tier": row["tier"], "timings": json.loads(row["timings_json"]),
    }


def models(router):
    result = []
    try:
        snapshot = router.runtime.resource_reader()
    except Exception:
        snapshot = None
    for entry in router.runtime.catalogue:
        available = entry.runtime == "llamacpp"
        checker = getattr(router, "model_available", None)
        if checker is not None:
            available = bool(checker(entry))
        variants = []
        for variant in entries(entry):
            fits = False
            if available and snapshot is not None:
                try:
                    warm = (
                        router.runtime._model_id == variant.id
                        and getattr(router.runtime, "_mode", None) == variant.mode
                    )
                    router.runtime._check_capacity(variant, snapshot, warm=warm)
                    fits = True
                except Exception:
                    pass
            variants.append({
                "mode": variant.mode, "fits_now": fits,
                "expected_prompt_tok_s": variant.expected_prompt_tok_s,
                "expected_gen_tok_s": variant.expected_tok_s,
                "measured": variant.measured, "measured_label": variant.speed_label,
            })
        chosen = next((item for item in variants if item["fits_now"]), variants[0])
        speed = chosen["expected_prompt_tok_s"]
        result.append({
            "id": entry.id, "display_name": entry.display_name, "runtime": entry.runtime,
            "roles": list(entry.roles), "available": available,
            "unavailable_reason": None if available else "not configured",
            "variants": variants, "tokens_per_hour_estimate": speed * 3600 if speed is not None else None,
        })
    return {
        "local": result,
        "cloud": [{
            "id": router.cloud_model_id, "display_name": "Approved cloud model",
            "destination_id": router.cloud.settings.destination_id,
            "deployment_type": router.cloud.settings.deployment_type,
        }] if router.use_case.approved_for_cloud else [],
    }


def document_view(evidence, index):
    return {
        "document": index,
        "decision": evidence.decision.value if evidence.decision else "BLOCK",
        "reasons": [reason.value for reason in evidence.reasons],
        "counts_by_label": {label.value: count for label, count in evidence.redaction_counts.items()},
        "tiers": {
            str(tier): {"ms": evidence.timings_ms.get(tier, 0), "ran": tier in evidence.tiers_run}
            for tier in (1, 2, 3)
        },
        "stage_status": {
            name: {"status": report.status.value, "error_code": report.error_code}
            for name, report in evidence.stage_status.items()
        },
    }


@dataclass
class Preview:
    text: str = field(repr=False)
    attachments: tuple[str, ...] = field(repr=False)
    intent: str
    expires: float
    safe: dict
    size_bytes: int


class PreviewStore:
    def __init__(self, clock=time.monotonic):
        self.clock, self.lock, self.items = clock, RLock(), {}

    def _purge(self):
        for key, item in tuple(self.items.items()):
            if self.clock() >= item.expires:
                del self.items[key]

    def get(self, identifier):
        with self.lock:
            self._purge()
            if identifier not in self.items:
                raise KeyError("preview expired or unknown")
            return self.items[identifier]

    def create(self, router, value):
        if (
            set(value) != {"text", "attachments", "intent"}
            or not isinstance(value["text"], str)
            or value["intent"] not in {"auto", "local", "cloud"}
            or not isinstance(value["attachments"], list) or len(value["attachments"]) > 99
        ):
            raise ValueError("invalid preview")
        documents = [value["text"]]
        for attachment in value["attachments"]:
            if (
                type(attachment) is not dict or set(attachment) != {"name", "text"}
                or not isinstance(attachment["name"], str) or not isinstance(attachment["text"], str)
            ):
                raise ValueError("invalid attachment")
            documents.append(attachment["text"])
        byte_count = sum(len(text.encode("utf-8")) for text in documents)
        if byte_count > 2 * 1024 * 1024:
            raise ValueError("preview bound")
        marking = summarise_markings(tuple(detect_markings(text) for text in documents))
        route = route_request(marking, router.use_case)
        local_only = route.route == Route.LOCAL_ONLY or value["intent"] == "local"
        targets = replace(
            router.targets, thorough=not local_only, local_only=local_only,
            has_attachment=router.targets.has_attachment or len(documents) > 1,
        )
        evidence = router.batch_detector(
            documents, router.profile, targets, thorough=not local_only,
        )
        if len(evidence) != len(documents):
            raise ValueError("incomplete batch")
        counts, timings, replacements, reasons = Counter(), Counter(), [], []
        from .substitute import CATEGORY_STRENGTH
        for result in evidence:
            counts.update({label.value: count for label, count in result.redaction_counts.items()})
            timings.update(result.timings_ms)
            reasons.extend(reason.value for reason in result.reasons)
            groups = []
            for start, end, label in sorted(
                (finding.start, finding.end, finding.primary_label) for finding in result.findings
            ):
                if groups and start <= groups[-1][1]:
                    a, b, labels = groups[-1]
                    groups[-1] = a, max(b, end), labels | {label}
                else:
                    groups.append((start, end, {label}))
            for start, end, labels in groups:
                if any(label.value == "SECRET" for label in labels):
                    category, label = "BLOCK", "SECRET"
                else:
                    selected = max(labels, key=lambda item: (
                        CATEGORY_STRENGTH[token_class_for(item)], item.value,
                    ))
                    category, label = token_class_for(selected).value, selected.value
                replacements.append({"category": category, "label": label, "length": end - start})
        decision = (
            "BLOCK" if any(result.decision in {None, Decision.BLOCK} for result in evidence)
            else "ASK" if any(result.decision == Decision.ASK for result in evidence)
            else "RELEASE"
        )
        words = sum(len(text.split()) for text in documents)
        classification, reason = route.audit_fields.marking_class.name, route.reason_code.value
        label = active_marking_profile().label(route.audit_fields.marking_class)
        safe = {
            "marking": {
                "class": classification, "label": label, "reason_code": reason,
                "local_only": route.route == Route.LOCAL_ONLY,
                "banner": (
                    label + ": this stays on your computer and runs on the local model only"
                    if route.route == Route.LOCAL_ONLY else ""
                ),
            },
            "route_options": ["local"] + (
                ["cloud"] if route.audit_fields.cloud_offered and router.use_case.approved_for_cloud else []
            ),
            "route_reasons": {"local": reason, **(
                {"cloud": reason} if route.audit_fields.cloud_offered else {}
            )},
            "findings": {"counts_by_label": dict(counts), "replacements": replacements},
            "tiers": {
                str(tier): {
                    "ms": timings.get(tier, 0),
                    "ran": any(tier in result.tiers_run for result in evidence),
                } for tier in (1, 2, 3)
            },
            "documents": [document_view(result, index + 1) for index, result in enumerate(evidence)],
            "decision": decision, "reasons": list(dict.fromkeys(reasons)),
            "size": {
                "documents": len(documents), "words": words,
                "routine": len(documents) <= 5 and words <= 20_000,
            },
        }
        with self.lock:
            self._purge()
            if (
                len(self.items) >= 256
                or sum(item.size_bytes for item in self.items.values()) + byte_count > 32 * 1024 * 1024
            ):
                raise ValueError("preview store capacity")
            identifier = "pre-" + secrets.token_hex(16)
            safe["preview_id"] = identifier
            self.items[identifier] = Preview(
                value["text"], tuple(documents[1:]), value["intent"],
                self.clock() + 600, safe, byte_count,
            )
        return safe


def cloud_view(router, identifier):
    from .cloud.openai_compat import JOB_ID
    if not JOB_ID.fullmatch(identifier):
        raise KeyError("unknown job")
    router.register.get_details(identifier)
    path = router.cloud.settings.captures / f"{identifier}.jsonl"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
        raw = router._payloads.get(identifier)
        if raw is None:
            raise KeyError("capture unavailable")
        return {"payload": strict_json(raw), "exact_json": raw.decode(), "answer": []}
    outbound, answer = None, []
    for line in path.read_bytes().splitlines():
        record = strict_json(line)
        if record.get("kind") == "outbound":
            outbound = record
        elif record.get("kind") == "answer-tokenised":
            answer.append(record["chunk"])
    if outbound is None:
        raise KeyError("capture unavailable")
    return {"payload": outbound["payload"], "exact_json": outbound["exact_json"], "answer": answer}


class HandoverStore:
    def __init__(self):
        self.lock, self.items = RLock(), {}

    def _checks(self, router, files):
        texts = [
            TOKEN_PATTERN.sub(lambda match: " " * len(match.group()), raw.decode("utf-8"))
            for _, raw in sorted(files.items())
        ]
        canary = getattr(router, "canary", None)
        if canary is not None:
            canary.before_cloud_work(len(texts))
        results = router.batch_detector(
            texts, router.profile, replace(router.targets, thorough=True, local_only=False),
            thorough=True,
        )
        blocked = canary.block_reason() if canary is not None else None
        if blocked is not None:
            # A handover package is for cloud use: a failing detector canary blocks it like a cloud send.
            from .router import GateFailure
            raise GateFailure(blocked, 200)
        if len(results) != len(texts) or any(
            result.decision in {None, Decision.BLOCK} or result.findings for result in results
        ):
            raise PermissionError("handover clean recheck failed")
        return [document_view(result, index + 1) for index, result in enumerate(results)]

    def build(self, router, identifier):
        row = router.register.get_details(identifier)
        if row["route"] != Route.CLOUD.value or row["package"] != "ready":
            raise PermissionError("only a ready clean cloud package can be exported")
        raw = cloud_view(router, identifier)["exact_json"].encode("utf-8")
        if hashlib.sha256(raw).hexdigest() != row["manifest_hash"]:
            raise PermissionError("package binding changed")
        manifest = HandoverManifest(
            identifier, datetime.now(timezone.utc), MarkingClass[row["marking_class"]],
            Route.CLOUD, json.loads(row["pii_counts_json"]),
            bool(row["sensitive_categories_present"]),
            (ManifestFile("inputs/clean.json", hashlib.sha256(raw).hexdigest()),),
            row["problem_summary_deid"], PeerCheck(), row["gate_version"], row["policy_version"],
        )
        files = {"inputs/clean.json": raw}
        verify_snapshot(
            manifest, files,
            confirmed_manifest_hash=manifest_hash(manifest) if row["sensitive_path"] == "CLOUD_CONFIRMED" else None,
        )
        checks = self._checks(router, files)
        with self.lock:
            if len(self.items) >= 256 and identifier not in self.items:
                raise ValueError("handover capacity")
            self.items[identifier] = (manifest, files, row["manifest_hash"], checks, router)
        return self.manifest(identifier)

    def _get(self, identifier):
        if identifier not in self.items:
            raise KeyError("handover unavailable")
        return self.items[identifier]

    def manifest(self, identifier):
        with self.lock:
            manifest, _, source_hash, checks, _ = self._get(identifier)
            return {
                **strict_json(canonical_manifest(manifest)),
                "manifest_hash": manifest_hash(manifest),
                "clean_source_hash": source_hash, "documents": checks,
            }

    def peer_view(self, identifier):
        with self.lock:
            _, files, _, _, _ = self._get(identifier)
            return {"text": files["inputs/clean.json"].decode("utf-8")}

    def peer_check(self, identifier, checked, note):
        if type(checked) is not bool or not isinstance(note, str) or note not in {"", "CHECKED", "REVIEW_REQUIRED"}:
            raise ValueError("safe peer note code required")
        with self.lock:
            manifest, files, source_hash, checks, router = self._get(identifier)
            manifest = replace(manifest, peer_check=(
                PeerCheck(True, "synthetic-peer", datetime.now(timezone.utc)) if checked else PeerCheck()
            ))
            self.items[identifier] = manifest, files, source_hash, checks, router
            return self.manifest(identifier)

    def download(self, identifier):
        with self.lock:
            manifest, files, _, _, router = self._get(identifier)
            verify_snapshot(
                manifest, files,
                confirmed_manifest_hash=(
                    manifest_hash(manifest) if marking_policy().needs_confirm(manifest.marking_class) else None
                ),
            )
            self._checks(router, files)
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", canonical_manifest(manifest))
                for path, raw in sorted(files.items()):
                    archive.writestr(path, raw)
            return output.getvalue()


def capacity(router):
    path = getattr(router, "capacity_file", None)
    if path is None or not Path(path).is_file():
        return {
            "tiers": [{
                "tier": tier, "model_id": model, "prefill_tok_s": speed,
                "decode_tok_s": decode, "measured": False, "label": "estimated (reference device)",
            } for tier, model, speed, decode in (
                (1, "pii-tracer", None, None), (2, "gemma4-e4b", 2000, 50),
                (3, "gemma4-26b-a4b:full", 658, 23.6), (3, "gemma4-26b-a4b:mmap", 29, 7.3),
            )],
            "end_to_end_input_tokens_per_hour": None,
            "reference_job": {"documents": 20, "words": 15_000, "measured": False},
        }
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError("capacity evidence unavailable")
    value = strict_json(path.read_bytes())
    if value.get("synthetic_only") is not True or set(value) != {
        "schema_version", "synthetic_only", "measured_utc", "tiers",
        "end_to_end_input_tokens_per_hour", "reference_job",
    }:
        raise ValueError("invalid capacity evidence")
    return value
