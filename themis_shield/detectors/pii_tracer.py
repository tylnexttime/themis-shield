"""Lazy CPU PII-Tracer with complete token-window offset mapping.

Weights and audited custom code must be provisioned locally by the administrator.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from .contracts import (
    StageReport, StageResult, Status, finding, sensitivity, validate_findings,
)
from .labels import native_tracer_label
from ..paths import default_tracer_dir


TOKEN_WINDOW = 3800
TOKEN_STEP = 1900
MODEL_TOKEN_LIMIT = 4094
DEFAULT_MODEL_DIRECTORY = default_tracer_dir()
_LOAD_LOCK = Lock()
_PREDICT_LOCK = Lock()
_RESOURCE: tuple | None = None
_RESOURCE_KEY: tuple | None = None
_LOAD_FAILED = False


@dataclass(frozen=True, slots=True)
class PIITracerSpec:
    model_directory: Path = DEFAULT_MODEL_DIRECTORY
    artefact_manifest_id: str = ""
    cpu_threads: int = 8

    def __post_init__(self) -> None:
        if type(self.cpu_threads) is not int or self.cpu_threads < 1:
            raise ValueError("invalid-tracer-thread-count")


def token_windows(
    text: str, offsets: list | tuple
) -> tuple[tuple[int, int], ...]:
    """Use 3800-token windows stepping 1900, retaining document edges."""
    previous = 0
    for pair in offsets:
        if (
            len(pair) != 2
            or any(type(item) is not int for item in pair)
            or not 0 <= pair[0] <= pair[1] <= len(text)
            or pair[0] < previous
        ):
            raise ValueError("invalid-token-offsets")
        previous = pair[0]
    if len(offsets) <= TOKEN_WINDOW:
        return ((0, len(text)),)
    windows = []
    for first in range(0, len(offsets), TOKEN_STEP):
        last = min(first + TOKEN_WINDOW, len(offsets))
        start = 0 if first == 0 else offsets[first][0]
        end = len(text) if last == len(offsets) else offsets[last - 1][1]
        if start >= end:
            raise ValueError("invalid-token-window")
        windows.append((start, end))
        if last == len(offsets):
            break
    return tuple(windows)


def _load(spec: PIITracerSpec) -> tuple:
    global _RESOURCE, _RESOURCE_KEY, _LOAD_FAILED
    key = (
        str(spec.model_directory.resolve()),
        spec.cpu_threads,
        spec.artefact_manifest_id,
    )
    with _LOAD_LOCK:
        if _RESOURCE_KEY is not None and _RESOURCE_KEY != key:
            raise RuntimeError("tracer-process-configuration-conflict")
        if _RESOURCE is not None:
            return _RESOURCE
        if _LOAD_FAILED:
            raise RuntimeError("tracer-load-failed")
        _RESOURCE_KEY = key
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer

            torch.set_num_threads(spec.cpu_threads)
            tokenizer = AutoTokenizer.from_pretrained(
                spec.model_directory,
                local_files_only=True,
                trust_remote_code=True,
                use_fast=True,
            )
            model = AutoModel.from_pretrained(
                spec.model_directory,
                local_files_only=True,
                trust_remote_code=True,
                torch_dtype=torch.float32,
                use_safetensors=True,
            ).to("cpu").eval()
            _RESOURCE = (torch, tokenizer, model)
            return _RESOURCE
        except Exception:
            _LOAD_FAILED = True
            raise RuntimeError("tracer-load-failed") from None


@dataclass(frozen=True, slots=True)
class PIITracerDetector:
    spec: PIITracerSpec = PIITracerSpec()
    detector_id: str = "pii-tracer"

    def detect(self, text: str) -> StageResult:
        torch, tokenizer, model = _load(self.spec)
        findings, document_sensitivity = [], 0.0
        # Serialise access to audited custom predict code; it is not assumed
        # safe for concurrent calls. The runtime supplies process deadlines and cancellation.
        with _PREDICT_LOCK, torch.inference_mode():
            offsets = tokenizer(
                text, return_offsets_mapping=True, add_special_tokens=False
            )["offset_mapping"]
            for start, end in token_windows(text, offsets):
                chunk = text[start:end]
                count = len(tokenizer(
                    chunk, add_special_tokens=False
                )["input_ids"])
                if count > MODEL_TOKEN_LIMIT:
                    raise ValueError("retokenized-window-too-long")
                spans, score = model.predict(chunk, tokenizer=tokenizer)
                if hasattr(score, "item"):
                    score = score.item()
                document_sensitivity = max(
                    document_sensitivity, sensitivity(score)
                )
                if not isinstance(spans, (list, tuple)):
                    raise ValueError("invalid-tracer-spans")
                for item in spans:
                    record = (
                        item.__dict__ if hasattr(item, "__dict__") else dict(item)
                    )
                    a, b = record["start"], record["end"]
                    if (
                        type(a) is not int or type(b) is not int
                        or not 0 <= a < b <= len(chunk)
                    ):
                        raise ValueError("invalid-tracer-span")
                    confidence = record.get("score")
                    if confidence is not None:
                        if hasattr(confidence, "item"):
                            confidence = confidence.item()
                        confidence = sensitivity(confidence)
                    findings.append(finding(
                        start + a, start + b,
                        native_tracer_label(record["label"]),
                        self.detector_id, 1, confidence,
                    ))
        validate_findings(text, tuple(findings))
        # Raw window duplicates are deliberately retained in stage counts.
        return StageResult(
            tuple(findings),
            StageReport(
                self.detector_id, Status.OK, len(findings),
                sum(item.label.value == "PERSON" for item in findings),
                document_sensitivity,
            ),
        )
