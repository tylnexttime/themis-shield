"""Fake alternative llama-server: records argv, then serves like fake_llama_server.

The argv is written as JSON to the file named by THEMIS_FAKE_ENGINE_ARGV.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_llama_server  # noqa: E402

if __name__ == "__main__":
    target = os.environ.get("THEMIS_FAKE_ENGINE_ARGV")
    if target:
        Path(target).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
    fake_llama_server.main()
