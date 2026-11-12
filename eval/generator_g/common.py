"""Shared helpers of the split-G pipeline: paths, halves, model assignment (with recorded substitutions), settings,
status line, round logs. No key handling here (ali.py owns it)."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = r"C:\dev\themis-work\api-credit-2026-11-11\.venv\Scripts\python.exe"
REPORT = Path(r"C:\dev\themis-work\api-credit-2026-11-11\workers\w40-splitg-REPORT.md")
DENYLIST = r"C:\dev\themis-work\tools\origin-denylist.txt"

# half letter -> design fixed by Alder (author writes; labeller is blind and runs the pointer check)
HALVES = {
    "R": {"author": "kimi-k3", "labeller": "deepseek-v4-pro-0813", "seed": 20261112,
          "org_kind": "a regional port and freight authority"},
    "S": {"author": "qwen3.8-max-0902", "labeller": "kimi-k3.1-pro", "seed": 20261113,
          "org_kind": "a credit union (member-owned bank)"},
    "T": {"author": "glm-5.3", "labeller": "deepseek-v4.1-flash-1017", "seed": 20261114,
          "org_kind": "a municipal council"},
}
SUBSTITUTES = ["qwen3.8-flash", "deepseek-v4.1-flash", "qwen3.8-27b"]
FREE_QUOTA = {"kimi-k3": 857_000, "deepseek-v4-pro-0813": 1_000_000, "qwen3.8-max-0902": 808_000,
              "kimi-k3.1-pro": 1_000_000, "glm-5.3": 1_000_000, "deepseek-v4.1-flash-1017": 1_000_000}

DEFAULT_SETTINGS = {
    # thinking: True/False sets enable_thinking; None leaves the model default. extra: more request fields.
    "kimi-k3": {"thinking": False, "author_temperature": None}, "deepseek-v4-pro-0813": {"thinking": False},
    "qwen3.8-max-0902": {"thinking": False}, "kimi-k3.1-pro": {"thinking": False},
    "glm-5.3": {"thinking": None, "extra": {"reasoning_effort": "low"}},
    "deepseek-v4.1-flash-1017": {"thinking": False}, "qwen3.8-flash": {"thinking": False},
    "deepseek-v4.1-flash": {"thinking": False}, "qwen3.8-27b": {"thinking": False},
}
_LOCK = threading.Lock()

for _d in ("requests", "results", "state", "sealed", "profiles/institution", "inputs", "halves", "probe", "rounds"):
    (ROOT / _d).mkdir(parents=True, exist_ok=True)


def family(model: str) -> str:
    m = model.lower()
    for f in ("kimi", "deepseek", "qwen", "glm"):
        if m.startswith(f):
            return f
    return m.split("-")[0]


def settings(model: str) -> dict:
    s = dict(DEFAULT_SETTINGS.get(model, {"thinking": False}))
    p = ROOT / "settings.json"
    if p.exists():
        s.update(json.loads(p.read_text(encoding="utf-8")).get(model, {}))
    s.setdefault("stream", True)
    return s


# ------------------------------------------------------------------ files
def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def jline(obj) -> str:
    """One JSON line that line-based readers cannot split (U+0085, U+2028, U+2029 escaped)."""
    s = json.dumps(obj, ensure_ascii=False, separators=(", ", ": "))
    return s.replace("\u0085", "\\u0085").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def write_jsonl(path, rows) -> None:
    Path(path).write_text("".join(jline(r) + "\n" for r in rows), encoding="utf-8", newline="\n")


def read_jsonl(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").split("\n") if l.strip()]


def jload(path, default=None):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def jsave(path, obj) -> None:
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


# ------------------------------------------------------------------ models and substitutions
def _models_state() -> dict:
    return jload(ROOT / "state" / "models.json", {})


def current_models(letter: str) -> dict:
    st = _models_state()
    base = {"author": HALVES[letter]["author"], "labeller": HALVES[letter]["labeller"]}
    base.update(st.get(letter, {}))
    return base


def all_assigned() -> set[str]:
    return {m for L in HALVES for m in current_models(L).values()}


def substitute(letter: str, role: str, phase: str, reason: str, code: str) -> str:
    """Replace this half's model for `role` by the first of SUBSTITUTES that is not of the same family as the other
    model of the half and is not already doing another job. Records the substitution (never the key)."""
    with _LOCK:
        cur = current_models(letter)
        other = cur["labeller" if role == "author" else "author"]
        busy = all_assigned()
        for cand in SUBSTITUTES:
            if cand in busy or family(cand) == family(other):
                continue
            st = _models_state()
            st.setdefault(letter, {})[role] = cand
            jsave(ROOT / "state" / "models.json", st)
            log = jload(ROOT / "state" / "substitutions.json", [])
            log.append({"half": letter, "role": role, "phase": phase, "from": cur[role], "to": cand, "reason": reason,
                        "error_code": code, "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            jsave(ROOT / "state" / "substitutions.json", log)
            return cand
    raise SystemExit(f"no substitute available for half {letter} {role}")


def substitutions() -> list[dict]:
    return jload(ROOT / "state" / "substitutions.json", [])


# ------------------------------------------------------------------ one half
class Half:
    def __init__(self, letter: str):
        self.L = letter.upper()
        self.l = letter.lower()
        self.dir = ROOT / "halves" / self.l
        for d in ("", "work", "rounds"):
            (self.dir / d).mkdir(parents=True, exist_ok=True)
        self.cfg = HALVES[self.L]
        self.prefix = f"G{self.L}-"

    # paths
    plan = property(lambda s: s.dir / "plan.jsonl")
    plan_topup = property(lambda s: s.dir / "plan.topup.jsonl")
    profile_path = property(lambda s: s.dir / "profile.json")
    state_path = property(lambda s: s.dir / "work" / "cases_state.json")
    cases_path = property(lambda s: s.dir / "cases.jsonl")
    agreement_path = property(lambda s: ROOT / f"AGREEMENT-{s.L}.md")

    @property
    def models(self) -> dict:
        return current_models(self.L)

    def round_log(self, name: str, data: dict) -> None:
        """Counts only: one JSON file per round."""
        (self.dir / "rounds" / f"{name}.json").write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")

    def done_marker(self, name: str) -> Path:
        return self.dir / "work" / f"done-{name}.json"

    def is_done(self, name: str) -> bool:
        return self.done_marker(name).exists()

    def mark_done(self, name: str, info: dict | None = None) -> None:
        self.done_marker(name).write_text(json.dumps(info or {}, sort_keys=True), encoding="utf-8")


def status_line(text: str) -> None:
    with _LOCK:
        with open(ROOT / "STATUS.txt", "a", encoding="utf-8", newline="\n") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + text + "\n")