"""Pipeline state, per half: one record per case id in halves/<x>/work/cases_state.json (latest raw author text,
status, issues, pointer result, history)."""
from __future__ import annotations

import json

from common import Half, jload, read_jsonl


def load_profile(h: Half) -> dict:
    return json.loads(h.profile_path.read_text(encoding="utf-8"))


def load_entries(h: Half) -> dict[str, dict]:
    rows = read_jsonl(h.plan) + read_jsonl(h.plan_topup)
    return {e["id"]: e for e in rows}


def load_state(h: Half) -> dict:
    return jload(h.state_path, {})


def save_state(h: Half, st: dict) -> None:
    h.state_path.write_text(json.dumps(st, ensure_ascii=False, indent=0), encoding="utf-8")