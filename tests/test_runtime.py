"""Runtime tests: real fake-server subprocesses plus fake resources.

Run from the repository root with:
    python -m unittest discover -s tests -p test_runtime.py -v
No GPU, downloads or real records.
"""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from themis_shield.inference import (
    InferenceRequest, NotEnoughMemory, RuntimeFailure, TargetUnavailable,
)
from themis_shield.inference.dgx_spark import DGXSparkTarget, DgxSparkTarget
from themis_shield.inference.llamacpp import LlamaCppTarget, tuning_args
from themis_shield.inference.marian import MarianTarget
from themis_shield.inference.strata import StrataTarget
from themis_shield.runtime.calibration import CalibrationStore, calibrate
from themis_shield.runtime.catalogue import BUILTINS, Catalogue, REFERENCE_LABEL
from themis_shield.runtime.lease import DeviceLease
from themis_shield.runtime.resources import DeviceIdentity, ResourceSnapshot
from themis_shield.runtime.scheduler import Scheduler

MESSAGES = [{"role": "user", "content": "Synthetic calibration unit."}]
SCHEMA = {
    "name": "pii", "strict": True,
    "schema": {
        "type": "object", "additionalProperties": False,
        "required": ["entities"],
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["label", "value"],
                    "properties": {
                        "label": {"type": "string", "enum": ["PERSON"]},
                        "value": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    },
}


def enough_resources() -> ResourceSnapshot:
    return ResourceSnapshot(8, 8, 30, 32, 2, 10, 64)


class FakeTarget:
    name = "laptop"
    pid = os.getpid()
    load_ms = 17.0

    def __init__(self, entry, events) -> None:
        self.entry = entry
        self.events = events
        self.resident = False
        self.last_timings = {}

    def start(self) -> None:
        self.events.append(("start", self.entry.id))
        self.resident = True

    def stop(self) -> None:
        self.events.append(("stop", self.entry.id))
        self.resident = False

    def health(self) -> bool:
        return self.resident

    def chat(self, messages, stream=False, response_format=None, max_tokens=512):
        self.last_timings = {
            "prompt_n": 1024, "prompt_per_second": 2000,
            "predicted_n": 128, "predicted_per_second": 50,
        }
        output = {
            "choices": [{
                "message": {"content": '{"entities":[]}'},
                "finish_reason": "stop",
            }],
            "timings": self.last_timings,
        }
        return iter([output]) if stream else output

    def chat_json(self, model_role, messages, schema, max_tokens=512):
        self.chat(messages)
        return {"entities": []}


class M3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.targets = []
        self.schedulers = []
        for entry in BUILTINS:
            (self.root / entry.file).write_bytes(b"SYNTHETIC-GGUF-STUB")

    def tearDown(self) -> None:
        for scheduler in reversed(self.schedulers):
            scheduler.close()
        for target in reversed(self.targets):
            target.stop()
        self.temp.cleanup()

    def target(self, *, extra=(), entry=None):
        target = LlamaCppTarget(
            entry or BUILTINS[0],
            models_dir=self.root,
            logs_dir=self.root / "logs",
            startup_timeout=5,
            retry_pause=0.01,
            stop_timeout=2,
            poll_interval=0.02,
            command_prefix=(
                sys.executable,
                str(Path(__file__).with_name("fake_llama_server.py")),
                *extra,
            ),
            timeout_seconds=2,
        )
        self.targets.append(target)
        return target

    def scheduler(self, reader=enough_resources, *, factory=None, idle=600):
        events = []
        targets = {}

        def default_factory(entry):
            target = FakeTarget(entry, events)
            targets[entry.id] = target
            return target

        scheduler = Scheduler(
            Catalogue(), factory or default_factory, reader,
            lease_path=self.root / f"lease-{len(self.schedulers)}.lock",
            idle_seconds=idle, memory_settle_seconds=0,
            queue_timeout=2,
        )
        self.schedulers.append(scheduler)
        return scheduler, events, targets

    def test_owned_start_stop_health_and_legacy_chat(self):
        target = self.target()
        self.assertFalse(target.health())
        target.start()
        self.assertTrue(target.health())
        pid = target.pid
        target.start()
        self.assertEqual(pid, target.pid)
        response = target.chat(InferenceRequest({"messages": MESSAGES}))
        self.assertIn("entities", response.text)
        self.assertEqual(50, response.timings["predicted_per_second"])
        target.stop()
        self.assertFalse(target.health())
        self.assertIsNone(target.pid)
        self.assertFalse(list((self.root / "logs").glob("key-*.txt")))

    def test_cuda_race_retries_exactly_once(self):
        marker = self.root / "attempts"
        target = self.target(extra=("--race-marker", str(marker)))
        target.start()
        self.assertEqual("2", marker.read_text())
        self.assertIn("CUDA_RACE_RETRY", target.log_path.read_text())
        self.assertTrue(target.health())

    def test_non_cuda_failure_is_not_retried(self):
        marker = self.root / "fatal"
        target = self.target(extra=("--fatal-marker", str(marker)))
        with self.assertRaises(TargetUnavailable):
            target.start()
        self.assertEqual("1", marker.read_text())
        self.assertFalse(target.health())

    def test_chat_json_and_no_prompt_log(self):
        target = self.target()
        target.start()
        result = target.chat_json("finder-fast", MESSAGES, SCHEMA, 128)
        self.assertEqual("Synthetic Citizen", result["entities"][0]["value"])
        target.stop()
        text = target.log_path.read_text()
        self.assertNotIn("SYNTHETIC-PROMPT-CANARY", text)
        self.assertNotIn("Synthetic Citizen", text)
        self.assertNotIn("Bearer", text)

    def test_json_errors_fail_closed(self):
        for case in ("invalid", "wrong-schema", "duplicate", "truncated"):
            with self.subTest(case=case):
                target = self.target(extra=("--output-case", case))
                target.start()
                with self.assertRaises(RuntimeFailure):
                    target.chat_json("finder-fast", MESSAGES, SCHEMA)
                target.stop()

    def test_protected_args_and_path_traversal(self):
        for args in (
            ("--host", "0.0.0.0"),
            ("--log-verbosity", "99"),
            ("-m", "unowned.gguf"),
            ("-t", "8", "-t", "16"),
        ):
            with self.assertRaises(ValueError):
                tuning_args(args, 16384)
        with self.assertRaises(ValueError):
            replace(BUILTINS[0], file="../escape.gguf")
        with self.assertRaises(ValueError):
            replace(BUILTINS[0], args=("-c", "8192"))

    def test_switch_unloads_when_vram_short(self):
        holder = {}

        def reader():
            targets = holder.get("targets", {})
            used = sum(
                target.entry.est_vram_gb
                for target in targets.values() if target.resident
            )
            return replace(enough_resources(), vram_free_gb=8 - used)

        scheduler, events, targets = self.scheduler(reader)
        holder["targets"] = targets
        with scheduler.acquire("finder-fast"):
            pass
        self.assertLess(reader().vram_free_gb, 7.1 + 0.5)
        with scheduler.acquire("finder-thorough"):
            self.assertFalse(targets["gemma4-e4b"].resident)
            self.assertTrue(targets["gemma4-26b-a4b"].resident)
        self.assertEqual([
            ("start", "gemma4-e4b"),
            ("stop", "gemma4-e4b"),
            ("start", "gemma4-26b-a4b"),
        ], events)
        self.assertEqual(
            ["gemma4-26b-a4b"], scheduler.status()["loaded_models"],
        )

    def test_switch_unloads_even_if_both_could_fit(self):
        scheduler, events, _ = self.scheduler(
            lambda: replace(enough_resources(), vram_total_gb=24, vram_free_gb=24),
        )
        with scheduler.acquire("finder-fast"):
            pass
        with scheduler.acquire("finder-thorough"):
            pass
        self.assertEqual(("stop", "gemma4-e4b"), events[1])

    def test_refuses_low_ram_and_unknown_qwen_footprint(self):
        scheduler, events, _ = self.scheduler(
            lambda: replace(enough_resources(), ram_available_gb=8),
        )
        with self.assertRaisesRegex(NotEnoughMemory, "not enough memory"):
            with scheduler.acquire("finder-fast"):
                self.fail("must not acquire")
        self.assertEqual([], events)
        self.assertEqual("not enough memory", scheduler.status()["status"])
        with self.assertRaisesRegex(TargetUnavailable, "budget unknown"):
            with scheduler.acquire("qwen36-35b-a3b"):
                self.fail("unknown budgets must hold")

    def test_missing_gpu_probe_and_low_commit_hold(self):
        for snapshot in (
            replace(enough_resources(), vram_free_gb=None, vram_total_gb=None),
            replace(enough_resources(), commit_used_gb=63),
        ):
            scheduler, events, _ = self.scheduler(lambda: snapshot)
            with self.assertRaises(RuntimeFailure):
                with scheduler.acquire("finder-fast"):
                    self.fail("must hold")
            self.assertEqual([], events)

    def test_fifo_queue_and_cancellation(self):
        scheduler, _, _ = self.scheduler()
        entered = threading.Event()
        release = threading.Event()
        order = []
        failures = []

        def first():
            try:
                with scheduler.acquire("finder-fast"):
                    entered.set()
                    release.wait(2)
                    order.append(1)
            except BaseException as exc:
                failures.append(exc)

        def second():
            try:
                with scheduler.acquire("finder-fast"):
                    order.append(2)
            except BaseException as exc:
                failures.append(exc)

        one = threading.Thread(target=first)
        two = threading.Thread(target=second)
        one.start()
        self.assertTrue(entered.wait(2))
        two.start()
        deadline = time.monotonic() + 1
        while scheduler.status()["queue"] != 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(1, scheduler.status()["queue"])
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(TargetUnavailable):
            with scheduler.acquire("finder-fast", cancel_event=cancelled):
                self.fail("cancelled")
        release.set()
        one.join(2)
        two.join(2)
        self.assertFalse(one.is_alive())
        self.assertFalse(two.is_alive())
        self.assertFalse(failures)
        self.assertEqual([1, 2], order)

    def test_stream_holds_lease_until_closed(self):
        scheduler, _, _ = self.scheduler()
        stream = scheduler.chat("answer", MESSAGES, stream=True)
        next(stream)
        self.assertTrue(scheduler.status()["inference_active"])
        stream.close()
        self.assertFalse(scheduler.status()["inference_active"])
        self.assertEqual(0, scheduler.status()["queue"])

    def test_idle_unload(self):
        scheduler, events, _ = self.scheduler(idle=0.05)
        with scheduler.acquire("finder-fast"):
            pass
        deadline = time.monotonic() + 2
        while scheduler.status()["loaded_models"] and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual([], scheduler.status()["loaded_models"])
        self.assertIn(("stop", "gemma4-e4b"), events)

    def test_calibration_and_labels_are_device_specific(self):
        scheduler, _, _ = self.scheduler()
        identity = DeviceIdentity(
            "Synthetic CPU alpha", "Synthetic GPU beta", 32 * 1024**3, 8 * 1024**3,
        )
        store = CalibrationStore(self.root / "calibration.json")
        with (
            patch("socket.gethostname", side_effect=AssertionError("hostname forbidden")),
            patch("platform.node", side_effect=AssertionError("node forbidden")),
        ):
            record = calibrate(
                scheduler, "gemma4-e4b", store, identity, sample_interval=0.01,
            )
        fingerprint = identity.fingerprint
        self.assertEqual(64, len(fingerprint))
        data = json.loads(store.path.read_text())
        self.assertEqual([fingerprint], list(data["devices"]))
        self.assertEqual(fingerprint, record["device_fingerprint"])
        text = store.path.read_text().lower()
        for forbidden in (
            "hostname", "username", "synthetic cpu alpha",
            "synthetic gpu beta", "synthetic unit",
        ):
            self.assertNotIn(forbidden, text)
        view = Catalogue().with_calibration(store, fingerprint)
        entry = view.get("gemma4-e4b")
        self.assertTrue(entry.measured)
        self.assertIn("measured on this device", entry.speed_label)
        self.assertEqual(REFERENCE_LABEL, entry.display_record()["footprint_label"])
        self.assertEqual(50, entry.expected_tok_s)
        self.assertEqual(
            REFERENCE_LABEL,
            view.get("gemma4-26b-a4b").speed_label,
        )
        other = DeviceIdentity("Other CPU", "Other GPU", 32 * 1024**3, 8 * 1024**3)
        self.assertFalse(
            Catalogue().with_calibration(store, other.fingerprint)
            .get("gemma4-e4b").measured
        )
        changed = Catalogue([replace(BUILTINS[0], args=("-ngl", "98"))])
        self.assertFalse(
            changed.with_calibration(store, fingerprint).get("gemma4-e4b").measured
        )

    def test_real_fake_server_calibration(self):
        def factory(entry):
            return self.target(entry=entry)

        scheduler, _, _ = self.scheduler(factory=factory)
        store = CalibrationStore(self.root / "real-fake-calibration.json")
        record = calibrate(
            scheduler, "gemma4-e4b", store,
            DeviceIdentity("Synthetic CPU", "Synthetic GPU", 32 * 1024**3, 8 * 1024**3),
            sample_interval=0.01,
        )
        self.assertEqual(2000, record["prompt_tok_s"])
        self.assertEqual(50, record["gen_tok_s"])
        self.assertGreater(record["load_seconds"], 0)
        self.assertEqual([], scheduler.status()["loaded_models"])

    def test_catalogue_patch_and_estimated_labels(self):
        path = self.root / "catalogue.json"
        path.write_text(json.dumps({
            "version": 1,
            "models": [{
                "id": "gemma4-e4b",
                "display_name": "Synthetic fast option",
                "file": "renamed.gguf",
            }],
        }))
        catalogue = Catalogue.load(path)
        self.assertEqual("Synthetic fast option", catalogue.get("gemma4-e4b").display_name)
        self.assertEqual(REFERENCE_LABEL, catalogue.get("gemma4-e4b").speed_label)
        self.assertEqual(len(BUILTINS), len(list(catalogue)))  # built-ins grow (Strata entry added)
        path.write_text(json.dumps({
            "version": 1,
            "models": [{"id": "gemma4-e4b", "measured": True}],
        }))
        with self.assertRaises(ValueError):
            Catalogue.load(path)

    def test_stubs_never_claim_available(self):
        self.assertIs(DGXSparkTarget, DgxSparkTarget)
        for target in (StrataTarget(), MarianTarget(), DgxSparkTarget()):
            self.assertFalse(target.health())
            with self.assertRaises(TargetUnavailable):
                target.start()
            with self.assertRaises(TargetUnavailable):
                target.chat_json("answer", MESSAGES, SCHEMA)

    def test_device_lease_excludes_second_owner(self):
        path = self.root / "exclusive.lock"
        lease = DeviceLease(path)
        try:
            with self.assertRaises(TargetUnavailable):
                DeviceLease(path)
        finally:
            lease.close()
        replacement = DeviceLease(path)
        replacement.close()


if __name__ == "__main__":
    unittest.main()
