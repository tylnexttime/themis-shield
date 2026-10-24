"""Live back-end smoke tests (marked slow; skipped unless --run-slow).

They need a free GPU and are meant to be run by hand:

- Strata: start Strata yourself, then set THEMIS_STRATA_URL (for example
  http://127.0.0.1:8095/v1) and optionally THEMIS_STRATA_MODEL (default
  qwen3.8-flash-next-gyro-s) and THEMIS_STRATA_API_KEY_ENV (the NAME of the
  variable holding the key, if Strata was started with one). The test only
  attaches and sends one synthetic prompt; it never starts or stops Strata.
- Marian: install Marian (https://github.com/tylnexttime/marian) so ``marian`` is importable, and set
  THEMIS_MARIAN_MODEL to a GGUF file that has a saved profile; optionally
  THEMIS_MARIAN_PROFILES (profiles directory), THEMIS_MARIAN_NEED (context,
  default 16384) and THEMIS_MARIAN_OBJECTIVE (tg or pp). The test only plans
  from saved profiles: it never profiles, loads a model or starts a server.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from themis_shield.inference.llamacpp import tuning_args
from themis_shield.inference.marian import MarianPlanner
from themis_shield.inference.strata import StrataConfig, StrataTarget

pytestmark = pytest.mark.slow


def test_live_strata_attach_and_answer():
    url = os.environ.get("THEMIS_STRATA_URL")
    if not url:
        pytest.skip("set THEMIS_STRATA_URL to a running Strata endpoint")
    target = StrataTarget(StrataConfig(
        endpoint=url,
        model_id=os.environ.get("THEMIS_STRATA_MODEL", "qwen3.8-flash-next-gyro-s"),
        api_key_env=os.environ.get("THEMIS_STRATA_API_KEY_ENV", "STRATA_API_KEY"),
    ))
    facts = target.probe()
    print("strata probe:", json.dumps(facts))
    target.start()
    print("strata metrics:", json.dumps(target.metrics()))
    output = target.chat(
        [{"role": "user", "content": "Synthetic smoke test. Reply with the single word: ready."}],
        max_tokens=1024,                               # raised to the thinking floor
    )
    message = output["choices"][0]["message"]
    print("strata finish:", output["choices"][0]["finish_reason"], "usage:", output.get("usage"))
    assert set(message) == {"role", "content"}         # no reasoning_content passes through
    assert output["choices"][0]["finish_reason"] == "stop"
    assert message["content"].strip()
    target.stop()
    assert target.health()                             # detaching left Strata running


def test_live_marian_plan_from_saved_profiles():
    planner = MarianPlanner(
        Path(os.environ["THEMIS_MARIAN_PROFILES"]) if os.environ.get("THEMIS_MARIAN_PROFILES") else None,
        os.environ.get("THEMIS_MARIAN_OBJECTIVE", "tg"),
    )
    installed, reason = planner.available()
    if not installed:
        pytest.skip(f"Marian not importable ({reason})")
    print("marian status:", json.dumps(planner.status()))
    model = os.environ.get("THEMIS_MARIAN_MODEL")
    if not model:
        pytest.skip("set THEMIS_MARIAN_MODEL to a profiled GGUF file")
    need = int(os.environ.get("THEMIS_MARIAN_NEED", "16384"))
    decision = planner.plan(Path(model), need)
    print("marian decision:", json.dumps(decision.record()))
    assert decision.reason in {"planned", "no-profile", "no-fit", "no-profiles-dir"}
    if decision.planned:
        assert decision.ctx >= need
        tuning_args(decision.args, decision.ctx)
