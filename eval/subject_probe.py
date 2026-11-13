"""Record the local model's answers to the subject check for the probe documents of ``tests/test_subject_check.py``.

The subject check (``[typed] subject_lift``) rests on a local model's answers, so its unit tests cannot show what the
real model says about the reviewer's sentences (second independent review, RC-01 and RC-02). This script asks the
gate's own typed model (the resident Gemma 4 26B, through the gate's runtime) every question of every probe document,
whatever a guard says (``subject_capture``), and writes the replies as a typed capture (``eval/typed_capture.py``):
prompt keys, the model identity and the top log-probabilities. No text is written. ``tests/test_subject_check.py``
replays the capture, so the suite checks the decisions the real model's answers lead to without a GPU.

The capture is tied to the question wording and the probe documents by the prompt keys: after changing either, run
this again (one live run; it takes the heavy-run lock's place, so run nothing else on the GPU).

    python eval/subject_probe.py --runtime-settings <runtime.json> --catalogue eval/config/catalogue-26b-iq3xxs.json \
        --out tests/fixtures/subject-check-capture
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "eval"), str(ROOT / "tests")]


class HeldRuntime:
    """One model session for the whole probe: every document's questions go to the same started back end."""

    def __init__(self, backend):
        self.backend = backend

    @contextmanager
    def session(self, settings):
        yield self.backend


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-settings", required=True)
    ap.add_argument("--catalogue", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    import test_subject_check as cases
    import typed_capture
    from themis_shield.detectors.typed import LlamaServerTypedRuntime, configure_typed, settings_record, typed_settings
    from themis_shield.runtime import RuntimeSettings, build_runtime
    from themis_shield.runtime.catalogue import Catalogue
    from themis_shield.runtime.modes import SequentialFinderClient

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    settings = replace(RuntimeSettings.load(Path(args.runtime_settings)), catalogue_file=Path(args.catalogue))
    catalogue = Catalogue.load(settings.catalogue_file)
    configure_typed(**cases.PROBE_SETTINGS, subject_capture=True)
    identity = typed_capture.typed_identity(catalogue, typed_settings().model_role)
    runtime = build_runtime(settings, catalogue)
    current: list[str] = []
    rows = []
    try:
        live = LlamaServerTypedRuntime(SequentialFinderClient(runtime))
        with live.session(typed_settings()) as backend:
            capturing = typed_capture.CapturingTypedRuntime(
                HeldRuntime(backend), out, identity, {"typed_settings": settings_record(), "probe": "subject-check"},
                lambda: current, None)
            for case_id, text, entities in cases.probe_documents():
                current[:] = [case_id]
                result = cases.run(text, capturing, entities)
                rows.append({"id": case_id, "decision": result.decision.value,
                             "reasons": [reason.value for reason in result.reasons],
                             "typed": list(result.policy_result.typed),
                             "answers": [answer.record() for answer in (result.typed.answers if result.typed else ())]})
            capturing.close()
    finally:
        runtime.close()
    for row in rows:
        marks = " ".join(
            f"{a['family']}={a['choice']}{'?' if a['unsure'] else ''}({a['margin']:.2f}{',' + a['guard'] if a.get('guard') else ''})"
            for a in row["answers"])
        print(f"{row['id']:22} {row['decision']:8} {','.join(row['typed']):40} {marks}")
    (out / "decisions.json").write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
