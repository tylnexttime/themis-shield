"""Capture and replay of typed-decision (T1.5) calls for the evaluation runner, like the finder capture.

--capture-typed DIR writes every prefill call: the prompt key (sha256 of the messages, the answer prefix, the option
letters and the recipe version), the case id, the family, the option order, the model identity and the reply (the
top log-probabilities of the next token, or the error kind). The label token ids found at start-up go into the
manifest. --replay-typed DIR serves those replies instead of calling llama-server and refuses (ReplayRefused) any call
whose prompt key is not in the capture, and a capture made with another model identity. The read-out (temperature,
margin, label-mass floor, order agreement) and the policy run again on every replay, so they can be tuned without
the GPU. Captures hold document text windows: keep them outside the repository.
"""
from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import json
from pathlib import Path
import time

from themis_shield.detectors.typed import RECIPE_VERSION, prompt_key

TYPED_FILE = "typed.jsonl"
TYPED_MANIFEST = "manifest.json"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def typed_identity(catalogue, model_role: str) -> dict:
    entry = catalogue.get(model_role)
    return {"model": model_role, "file": entry.file, "args": list(entry.args), "ctx": entry.ctx,
            "recipe": RECIPE_VERSION}


class _Recorder:
    """Shared bookkeeping: which case and family a call belongs to, and the per-call log (no text)."""

    def __init__(self, case_ids, sink):
        self.case_ids = case_ids  # the runner's list for the current chunk (indexed by document position)
        self.sink = sink
        self.document = None
        self.family = None
        self.order = None

    def note(self, family, order_index):
        self.family, self.order = family, order_index

    def case_id(self):
        ids = self.case_ids()
        return ids[self.document] if self.document is not None and self.document < len(ids) else None

    def log(self, record):
        if self.sink is not None:
            self.sink.write(json.dumps(record) + "\n")
            self.sink.flush()


class CapturingBackend:
    kind = "prefill"

    def __init__(self, inner, recorder: _Recorder, out, identity):
        self.inner, self.recorder, self.out, self.identity = inner, recorder, out, identity
        self.label_ids = inner.label_ids

    def note_question(self, family, order_index):
        self.recorder.note(family, order_index)

    def score(self, messages, letters):
        key = prompt_key(messages, letters)
        began = time.perf_counter()
        record = {"key": key, "case_id": self.recorder.case_id(), "family": self.recorder.family,
                  "order": self.recorder.order, "model_identity": self.identity}
        try:
            reply = self.inner.score(messages, letters)
        except Exception as error:
            record.update(outcome="error", error_kind="malformed" if isinstance(error, (ValueError, TypeError, KeyError,
                                                                                         IndexError)) else "failed",
                          error_type=type(error).__name__)
            self.out.write(json.dumps(record, sort_keys=True) + "\n")
            self.out.flush()
            self.recorder.log({**{k: record[k] for k in ("key", "case_id", "family", "order")},
                               "status": record["error_kind"], "wall_ms": round((time.perf_counter() - began) * 1000, 1)})
            raise
        record.update(outcome="ok", top=reply["top"], timings=reply.get("timings", {}))
        self.out.write(json.dumps(record, sort_keys=True) + "\n")
        self.out.flush()
        self.recorder.log({**{k: record[k] for k in ("key", "case_id", "family", "order")}, "status": "ok",
                           "wall_ms": round((time.perf_counter() - began) * 1000, 1),
                           "timings": reply.get("timings", {})})
        return reply


class CapturingTypedRuntime:
    """Live typed calls through the gate's own runtime; every reply written to the capture folder (when given) and
    every call logged (no text) to the run's typed_calls.jsonl."""

    def __init__(self, inner_runtime, directory: Path | None, identity: dict, meta: dict, case_ids, sink):
        self.inner = inner_runtime
        self.directory = directory
        self.identity = identity
        self.meta = meta
        self.recorder = _Recorder(case_ids, sink)
        self.previous = None
        self.written = False
        self.out = _NullSink()
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)
            manifest_path = directory / TYPED_MANIFEST
            if manifest_path.exists():
                self.previous = json.loads(manifest_path.read_text(encoding="utf-8"))
                if self.previous.get("identity") != identity:
                    raise SystemExit("typed capture folder holds replies from another model identity")
            self.out = open(directory / TYPED_FILE, "a", encoding="utf-8", newline="\n")

    def begin_document(self, index):
        self.recorder.document = index

    @contextmanager
    def session(self, settings):
        with self.inner.session(settings) as backend:
            label_ids = {letter: list(ids) for letter, ids in backend.label_ids.items()}
            if self.previous is not None and self.previous.get("label_ids") != label_ids:
                raise SystemExit("typed capture folder holds replies read with other label token ids")
            if self.directory is not None and not self.written:
                manifest = {"written_utc": now(), "identity": self.identity, "label_ids": label_ids, **self.meta}
                if self.previous is not None:
                    manifest["resumed_from"] = (self.previous.get("resumed_from", [])
                                                + [self.previous.get("written_utc")])
                (self.directory / TYPED_MANIFEST).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
                self.written = True
            yield CapturingBackend(backend, self.recorder, self.out, self.identity)

    def close(self):
        self.out.close()


class _NullSink:
    def write(self, _):
        return 0

    def flush(self):
        return None

    def close(self):
        return None


class ReplayBackend:
    kind = "prefill"

    def __init__(self, owner, label_ids):
        self.owner, self.label_ids = owner, label_ids
        self.records, self.recorder, self.refused = owner.records, owner.recorder, owner.refused

    def note_question(self, family, order_index):
        self.recorder.note(family, order_index)

    def score(self, messages, letters):
        key = prompt_key(messages, letters)
        record = self.records.get(key)
        if record is None:
            raise self.refused(f"no captured typed reply for a {self.recorder.family} question "
                               f"(case {self.recorder.case_id()})")
        self.owner.served += 1
        self.recorder.log({"key": key, "case_id": self.recorder.case_id(), "family": self.recorder.family,
                           "order": self.recorder.order, "status": "replayed", "wall_ms": 0.0,
                           "timings": record.get("timings", {})})
        if record["outcome"] == "ok":
            return {"top": [list(pair) for pair in record["top"]], "timings": dict(record.get("timings", {}))}
        if record.get("error_kind") == "malformed":
            raise ValueError("replayed-malformed")
        raise RuntimeError("replayed-failure")


class ReplayTypedRuntime:
    def __init__(self, directory: Path, identity: dict, case_ids, sink, refused):
        manifest_path = directory / TYPED_MANIFEST
        if not manifest_path.exists():
            raise SystemExit(f"no typed capture manifest in {directory}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("identity") != identity:
            raise refused(f"typed model identity differs from the capture: captured {manifest.get('identity')}, "
                          f"now {identity}")
        self.manifest = manifest
        self.records = {}
        for line in (directory / TYPED_FILE).read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                if record.get("model_identity") != identity:
                    raise refused("a typed capture record has another model identity")
                self.records[record["key"]] = record  # a resumed capture: the last reply counts
        self.recorder = _Recorder(case_ids, sink)
        self.refused = refused
        self.served = 0

    def begin_document(self, index):
        self.recorder.document = index

    @contextmanager
    def session(self, settings):
        label_ids = {letter: tuple(ids) for letter, ids in self.manifest["label_ids"].items()}
        yield ReplayBackend(self, label_ids)

    def close(self):
        return None
