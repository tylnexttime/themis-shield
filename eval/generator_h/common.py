"""Shared helpers of the split-H pipeline: paths, halves, model assignment (with recorded substitutions), settings,
status line, round logs. No key handling here (llm.py owns it)."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = r"C:/dev/themis-work/api-credit-2026-11-11/.venv/Scripts/python.exe"
REPORT = Path(r"C:/dev/themis-work/api-credit-2026-11-11/workers/w50-splith-REPORT.md")
DENYLIST = r"C:/dev/themis-work/tools/origin-denylist.txt"

# half letter -> design fixed by Alder in the frozen plan (author writes; labeller labels blind; pointer checks)
HALVES = {
    "A": {"author": "mistralai/mistral-large-2512", "labeller": "kimi-k3.1-pro", "pointer": "openai/gpt-oss-120b",
          "seed": 20261301, "org_kind": "a regional airline and airport operator"},
    "B": {"author": "deepseek-v4.1-flash", "labeller": "openai/gpt-oss-120b", "pointer": "openai/gpt-oss-120b",
          "seed": 20261302, "org_kind": "an independent school group"},
}
SUBSTITUTES = ["qwen3.8-flash"]  # spare author only (Alibaba); there is no spare labeller or pointer model
# Alibaba free quotas per exact model code: tokens (total, cached included) at which a request is refused
ALI_CAPS = {"deepseek-v4.1-flash": 980_000, "kimi-k3.1-pro": 390_000, "qwen3.8-flash": 800_000}
OR_MODELS = {"mistralai/mistral-large-2512", "openai/gpt-oss-120b"}
OR_COST_STOP, OR_COST_CAP = 4.80, 5.00
KEY_USAGE_AT_START_BEFORE_US = 21.3015  # at 21:45 UTC on 12/11 (before this job)
KEY_USAGE_LIMIT = 26.30
FREE_QUOTA = {"deepseek-v4.1-flash": 1_000_000, "kimi-k3.1-pro": 393_000, "qwen3.8-flash": None}


def provider_of(model: str) -> str:
    return "openrouter" if "/" in model else "alibaba"


DEFAULT_SETTINGS = {
    # thinking: True/False sets enable_thinking (Alibaba); None leaves the model default. extra: more request fields.
    "mistralai/mistral-large-2512": {"thinking": None, "stream": False, "author_temperature": 0.7},
    "openai/gpt-oss-120b": {"thinking": None, "stream": False, "extra": {"reasoning": {"effort": "low"}}},
    "kimi-k3.1-pro": {"thinking": False},
    "deepseek-v4.1-flash": {"thinking": False},
    "qwen3.8-flash": {"thinking": False},
}
_LOCK = threading.Lock()

for _d in ("requests", "results", "state", "sealed", "profiles/institution", "inputs", "halves", "probe", "rounds"):
    (ROOT / _d).mkdir(parents=True, exist_ok=True)


def family(model: str) -> str:
    m = model.lower().split("/")[-1]
    for f in ("kimi", "deepseek", "qwen", "glm", "mistral", "openai"):
        if m.startswith(f):
            return f
    return m.split("/")[-1].split("-")[0]


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
    base = {r: HALVES[letter][r] for r in ("author", "labeller", "pointer")}
    base.update(st.get(letter, {}))
    return base


def all_assigned() -> set[str]:
    return {m for L in HALVES for m in current_models(L).values()}


def substitute(letter: str, role: str, phase: str, reason: str, code: str) -> str:
    """Spare AUTHOR only: the half's author becomes qwen3.8-flash (recorded, never the key). There is no spare
    labeller or pointer model: SystemExit if asked for one."""
    if role != "author":
        raise SystemExit(f"no substitute for the {role} role (the plan has a spare author only)")
    with _LOCK:
        cur = current_models(letter)
        for cand in SUBSTITUTES:
            if cand == cur[role]:
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
        self.prefix = f"H{self.L}-"

    # paths
    plan = property(lambda s: s.dir / "plan.jsonl")
    plan_topup = property(lambda s: s.dir / "plan.topup.jsonl")
    plan_topup2 = property(lambda s: s.dir / "plan.topup2.jsonl")  # only after an explicit amendment (see run_half.py)
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