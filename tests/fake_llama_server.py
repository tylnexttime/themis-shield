"""Tiny synthetic llama-server substitute; invoked as an owned subprocess.

No GPU, model loading or external libraries. Unknown production flags are
ignored, but host, port and API-key-file are honoured.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--api-key-file", type=Path, required=True)
    parser.add_argument("--race-marker", type=Path)
    parser.add_argument("--fatal-marker", type=Path)
    parser.add_argument("--output-case", default="valid")
    args, _ = parser.parse_known_args()
    marker = args.race_marker or args.fatal_marker
    if marker is not None:
        count = int(marker.read_text()) if marker.exists() else 0
        marker.write_text(str(count + 1))
        if args.fatal_marker is not None or count == 0:
            print(
                "CUDA error: shared object initialization failed"
                if args.race_marker else "synthetic unrelated startup failure",
                file=sys.stderr, flush=True,
            )
            raise SystemExit(3)
    key = args.api_key_file.read_text().strip()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *unused) -> None:
            pass

        def reply(self, code: int, value: dict) -> None:
            raw = json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path == "/health":
                self.reply(200, {"status": "ok"})
            else:
                self.reply(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.headers.get("Authorization") != f"Bearer {key}":
                self.reply(401, {"error": "unauthorised"})
                return
            if self.path != "/v1/chat/completions":
                self.reply(404, {"error": "not found"})
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["stream"] is False
            # Deliberately hostile logging canary: target must not persist it.
            print("SYNTHETIC-PROMPT-CANARY-NOT-A-PERSON", flush=True)
            content = '{"entities":[{"label":"PERSON","value":"Synthetic Citizen"}]}'
            finish = "stop"
            if args.output_case == "invalid":
                content = '{"entities":'
            elif args.output_case == "wrong-schema":
                content = '{"entities":42}'
            elif args.output_case == "duplicate":
                content = '{"entities":[],"entities":[]}'
            elif args.output_case == "truncated":
                finish = "length"
            elif args.output_case == "empty":
                # A valid, empty answer: what a finder returns when its template silently changed.
                content = '{"entities":[]}'
            elif args.output_case == "thought-channel":
                # A chat template that turns thinking on: the answer starts in a thought channel.
                content = '<|channel>thought\n<channel|>{"entities":[]}'
            elif args.output_case == "bare-list":
                # A bare empty list instead of the schema's object.
                content = "[]"
            elif args.output_case == "canary":
                # A healthy finder on the detector canary's fixed synthetic note: the name and the statement.
                document = body["messages"][-1]["content"]
                entities = []
                for label, value in (("PERSON", "Oriel Brackenbury"), ("HEALTH", "is on dialysis three mornings a week")):
                    if value in document:
                        entities.append({"label": label, "value": value})
                content = json.dumps({"entities": entities})
            self.reply(200, {
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish,
                }],
                "usage": {
                    "prompt_tokens": 1024, "completion_tokens": 128,
                    "total_tokens": 1152,
                },
                "timings": {
                    "prompt_n": 1024, "prompt_ms": 512,
                    "prompt_per_second": 2000,
                    "predicted_n": 128, "predicted_ms": 2560,
                    "predicted_per_second": 50,
                },
            })

    HTTPServer((args.host, args.port), Handler).serve_forever(poll_interval=0.05)


if __name__ == "__main__":
    main()
