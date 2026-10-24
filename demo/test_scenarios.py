"""Small offline falsifiers for acceptance proof parsing.

Run from the repository root:
    python -m unittest discover -s demo -p test_scenarios.py -v
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
import unittest

import scenarios as demo


def sample():
    exact = json.dumps({
        "model": "example-cloud-model",
        "messages": [{"role": "user", "content": "Reminder for ⟦P:abcdef⟧."}],
    }, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(exact.encode()).hexdigest()
    chunk = {"choices": [{
        "index": 0, "delta": {"content": "Dear ⟦P:abcdef⟧"},
        "finish_reason": "stop",
    }]}
    view = {
        "exact_json": exact, "payload": json.loads(exact), "answer": [chunk],
    }
    rows = [
        {
            "kind": "outbound", "verify_no_originals": True,
            "manifest_hash": digest,
            "exact_json": exact, "payload": json.loads(exact),
        },
        {"kind": "answer-tokenised", "chunk": chunk},
        {"kind": "transport-complete"},
    ]
    return view, rows, digest


class ProofTests(unittest.TestCase):
    def test_one_valid_proof(self):
        view, rows, digest = sample()
        result = demo.verify_capture(view, rows, digest, names=True)
        self.assertTrue(result["exact_api_capture_match"])

    def test_one_original_in_payload_proves_acceptance_wrong(self):
        view, rows, digest = sample()
        view["payload"]["messages"][0]["content"] = "Mara Lindahl"
        view["exact_json"] = demo.encoded(view["payload"]).decode()
        digest = demo.sha(view["exact_json"].encode())
        rows[0].update(
            payload=view["payload"], exact_json=view["exact_json"],
            manifest_hash=digest,
        )
        with self.assertRaises(demo.Failure):
            demo.verify_capture(view, rows, digest, names=True)

    def test_json_escape_does_not_hide_an_original(self):
        with self.assertRaises(demo.Failure):
            demo.absence(r'{"content":"\u004dara Lindahl"}')

    def test_json_inside_arguments_does_not_hide_an_original(self):
        body = {"arguments": r'{"name":"\u004dara"}'}
        with self.assertRaises(demo.Failure):
            demo.absence(body)

    def test_missing_transport_receipt_is_not_cloud_success(self):
        view, rows, digest = sample()
        with self.assertRaises(demo.Failure):
            demo.verify_capture(view, rows[:-1], digest)

    def test_one_changed_byte_invalidates_staged_package(self):
        view, rows, digest = sample()
        wrong = demo.sha((view["exact_json"] + " ").encode())
        with self.assertRaises(demo.Failure):
            demo.verify_capture(view, rows, wrong)

    def test_duplicate_outbound_is_not_one_send(self):
        view, rows, digest = sample()
        with self.assertRaises(demo.Failure):
            demo.verify_capture(view, rows + [rows[0]], digest)

    def test_split_original_in_tokenised_answer_is_rejected(self):
        view, rows, digest = sample()
        chunks = [
            {"choices": [{"delta": {"content": "Ma"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "ra"}, "finish_reason": "stop"}]},
        ]
        view["answer"] = chunks
        rows = [rows[0]] + [
            {"kind": "answer-tokenised", "chunk": chunk} for chunk in chunks
        ] + [rows[-1]]
        with self.assertRaises(demo.Failure):
            demo.verify_capture(view, rows, digest, names=True)

    def test_incomplete_sse_is_not_a_success(self):
        raw = b'data: {"choices":[],"themis":{"route":"held"}}\n\n'
        with self.assertRaises(demo.Failure):
            demo.parse_sse(raw)

    def test_error_event_even_with_done_fails(self):
        raw = b'data: {"error":{"code":"SYNTHETIC_FAILURE"}}\n\ndata: [DONE]\n\n'
        with self.assertRaises(demo.Failure):
            demo.parse_sse(raw)

    def test_final_app_metadata_required(self):
        raw = b'data: {"choices":[]}\n\ndata: [DONE]\n\n'
        with self.assertRaises(demo.Failure):
            demo.parse_sse(raw)

    def test_capture_inventory_detects_an_append(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "JOB-2026-0001.jsonl"
            path.write_bytes(b'{"kind":"outbound"}\n')
            before = demo.inventory(root)
            path.write_bytes(path.read_bytes() + b'{"kind":"transport-complete"}\n')
            self.assertNotEqual(before, demo.inventory(root))


if __name__ == "__main__":
    unittest.main()
