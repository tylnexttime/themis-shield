"""Pipeline state: one record per case id in work/cases_state.json (latest raw author text, status, issues)."""
from __future__ import annotations

import json

from common import ROOT, read_jsonl

WORK = ROOT / "work"
WORK.mkdir(exist_ok=True)
STATE_PATH = WORK / "cases_state.json"


def load_profile() -> dict:
    return json.loads((ROOT / "profile.json").read_text(encoding="utf-8"))


def load_entries() -> dict[str, dict]:
    rows = read_jsonl(ROOT / "plan.jsonl") + read_jsonl(ROOT / "plan.topup.jsonl")
    return {e["id"]: e for e in rows}


def load_state() -> dict:
    return json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}


def save_state(st: dict) -> None:
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=0), encoding="utf-8")