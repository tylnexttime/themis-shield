"""Evaluation runner, label mapping and profile converter. No models, no server."""

from __future__ import annotations

import importlib.util
import json
from argparse import Namespace
from pathlib import Path
import sys

import pytest

from themis_shield.config import DeploymentProfile, UseCasePolicy
from themis_shield.detectors import DetectionTargets
from themis_shield.detectors.contracts import StageReport, StageResult, Status
from themis_shield.detectors.profile_adapter import adapt
from themis_shield.profiles import load_profile_json
from themis_shield.types import Label

EVAL = Path(__file__).resolve().parents[1] / "eval"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"eval_{name}", EVAL / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run_eval = _load("run_eval")
label_map = run_eval.label_map
scorer = run_eval.scorer
convert_profile = _load("convert_profile")


def test_every_gate_label_maps_to_known_gold_labels():
    for label in Label:
        assert label.value in label_map.GATE_TO_EVAL, label
    for gate, golds in label_map.GATE_TO_EVAL.items():
        assert golds and set(golds) <= set(scorer.GOLD_LABELS), gate


def test_every_gold_label_is_reachable_from_some_gate_label():
    reachable = {gold for golds in label_map.GATE_TO_EVAL.values() for gold in golds}
    assert reachable == set(scorer.GOLD_LABELS)


def test_unnamed_labels_are_documented_and_real():
    assert set(label_map.UNNAMED_BY_PROMPT_V1) <= set(scorer.GOLD_LABELS)


def test_combined_keys_stand_for_the_union_of_their_parts():
    table = label_map.labelset_for({"QUASI_ID|HEALTH", "PERSON"})
    assert table["QUASI_ID|HEALTH"] == ["QUASI_ID", "HEALTH"]
    assert table["PERSON"] == ["PERSON"]
    assert label_map.combined_key(["HEALTH", "QUASI_ID"]) == "HEALTH|QUASI_ID"
    with pytest.raises(ValueError):
        label_map.combined_key([])


def test_shipped_eval_profile_matches_a_fresh_conversion():
    assert convert_profile.TARGET.read_text(encoding="utf-8") == convert_profile.render()


def test_converted_profile_keeps_bare_site_names_out_of_the_allowlist():
    source = json.loads(convert_profile.SOURCE.read_text(encoding="utf-8"))
    facts = adapt(DeploymentProfile("eval", "1", (), (), institution=load_profile_json("institution", "harbourline-eval")))
    literals = set(facts["literals"])
    for site in source["sites"]:
        assert site["name"] not in literals  # suburbs where people live
        assert set(site["forms"]) <= literals
    assert set(source["committees"]) <= literals and set(source["initiatives"]) <= literals
    assert "personal_identifier_formats_issued" not in json.dumps(load_profile_json("institution", "harbourline-eval"))
    assert "enquiries" in facts["role_mailbox_local_parts"]


class FakeTracer:
    def detect(self, text):
        return StageResult((), StageReport("pii-tracer", Status.OK, doc_sensitivity=0.0))


class FakeFinder:
    """Returns the first word of the text as a PERSON; enough to exercise the whole path."""

    def __init__(self):
        self.calls = 0
        self.last_timings = {"prompt_n": 10, "predicted_n": 5, "predicted_per_second": 50.0}

    def chat_json(self, model_role, messages, schema, max_tokens):
        self.calls += 1
        text = messages[-1]["content"]
        body = text.split("<<<\n", 1)[1] if "<<<\n" in text else text
        word = body.split()[0].strip(":,.") or "x"
        return {"entities": [{"label": "PERSON", "value": word}]}


def test_runner_end_to_end_with_fakes(tmp_path):
    cases = scorer.load_gold("dev")[:4]
    finder = FakeFinder()
    client = run_eval.RecordingClient(finder)
    use_case = UseCasePolicy("evaluation", (), requires_in_region_inference=False)
    profile = DeploymentProfile("evaluation", "1", (), (use_case,),
                                institution=load_profile_json("institution", "harbourline-eval"))
    targets = DetectionTargets(tracer=FakeTracer(), llm_client=client, thorough=True)
    run_eval.process_cases(cases, profile, targets, client, use_case, tmp_path, 3, lambda m: None)
    preds = [json.loads(line) for line in (tmp_path / "predictions.jsonl").read_text().splitlines()]
    assert [p["id"] for p in preds] == [c["id"] for c in cases]
    assert all(p["decision"] in {"RELEASE", "ASK", "BLOCK"} for p in preds)
    assert finder.calls == 2 * len(cases)  # both finder tiers, every document (thorough)
    assert [c["case_id"] for c in client.calls[:3]] == [c["id"] for c in cases[:3]]
    summary = run_eval.score_run(tmp_path, "dev", cases)
    assert summary["cases"] == len(cases)
    assert 0 <= summary["R_full"] <= summary["R_any"] <= 1
    assert set(summary["decisions"]) == {"RELEASE", "ASK", "BLOCK"}
    assert (tmp_path / "score.txt").read_text().startswith("labelset=themis")


def test_recording_client_templates_hide_the_text():
    finder = FakeFinder()
    client = run_eval.RecordingClient(finder)
    client.case_ids, client.texts = ["c1"], ["Some secret text"]
    with client.residency("gemma4-e4b") as target:
        target.chat_json("gemma4-e4b", [{"role": "system", "content": "fixed"},
                                        {"role": "user", "content": "<<<\nSome secret text\n>>>"}], {}, 10)
    record = client.calls[0]
    assert record["case_id"] == "c1" and record["status"] == "ok"
    assert "Some secret text" not in json.dumps(record)
    assert "Some secret text" not in json.dumps(client.first_messages)
    assert record["system_prefix_sha256"]


def test_test_split_needs_the_frozen_tag(tmp_path):
    args = Namespace(split="test", frozen_tag=None, resume=False)
    with pytest.raises(SystemExit):
        run_eval.guard_test_split(args, tmp_path / "x")
    args = Namespace(split="test", frozen_tag="no-such-tag-on-head", resume=False)
    with pytest.raises(SystemExit):
        run_eval.guard_test_split(args, tmp_path / "x")
    run_eval.guard_test_split(Namespace(split="dev", frozen_tag=None, resume=False), tmp_path)


def test_runs_dir_inside_the_repository_is_refused():
    with pytest.raises(SystemExit):
        run_eval.main(["--split", "dev", "--run-name", "x", "--runs-dir", str(EVAL / "runs")])


class BrokenFinder(FakeFinder):
    def chat_json(self, model_role, messages, schema, max_tokens):
        raise RuntimeError("model would not load")


def test_runner_stops_on_infrastructure_stage_failure_without_writing(tmp_path):
    cases = scorer.load_gold("dev")[:2]
    client = run_eval.RecordingClient(BrokenFinder())
    use_case = UseCasePolicy("evaluation", (), requires_in_region_inference=False)
    profile = DeploymentProfile("evaluation", "1", (), (use_case,), institution={})
    targets = DetectionTargets(tracer=FakeTracer(), llm_client=client, thorough=True)
    with pytest.raises(RuntimeError):
        run_eval.process_cases(cases, profile, targets, client, use_case, tmp_path, 2, lambda m: None)
    assert (tmp_path / "predictions.jsonl").read_text() == ""
    # Scored as the gate would (BLOCK) only when asked to.
    run_eval.process_cases(cases, profile, targets, client, use_case, tmp_path, 2, lambda m: None,
                           allow_stage_failures=True)
    preds = [json.loads(line) for line in (tmp_path / "predictions.jsonl").read_text().splitlines()]
    assert [p["decision"] for p in preds] == ["BLOCK", "BLOCK"]


def test_recording_client_records_a_residency_failure():
    from contextlib import contextmanager

    class Inner:
        @contextmanager
        def residency(self, model_role):
            raise RuntimeError("not enough memory: available RAM below model budget")
            yield

    client = run_eval.RecordingClient(Inner())
    with pytest.raises(RuntimeError):
        with client.residency("gemma4-26b-a4b"):
            pass
    assert client.calls[0]["status"] == "residency_failed"
    assert "available RAM" in client.calls[0]["error"]


# --------------------------------------------------------------------------------------------------------------------
# v0.2: finder capture and replay, case files
# --------------------------------------------------------------------------------------------------------------------
IDENTITIES = {"gemma4-e4b": {"file": "e4b.gguf", "args": ["-ngl", "99"], "ctx": 16384},
              "gemma4-26b-a4b": {"file": "26b.gguf", "args": ["-ngl", "99", "-ncmoe", "18"], "ctx": 16384}}


def _process(tmp_path, cases, client, name):
    out = tmp_path / name
    out.mkdir()
    use_case = UseCasePolicy("evaluation", (), requires_in_region_inference=False)
    profile = DeploymentProfile("evaluation", "1", (), (use_case,),
                                institution=load_profile_json("institution", "harbourline-eval"))
    targets = DetectionTargets(tracer=FakeTracer(), llm_client=client, thorough=True)
    run_eval.process_cases(cases, profile, targets, client, use_case, out, 3, lambda m: None)
    return out


def test_capture_then_replay_is_byte_identical_and_needs_no_model(tmp_path):
    cases = scorer.load_gold("dev")[:5]
    capture = run_eval.FinderCapture(tmp_path / "cap", IDENTITIES, {"prompt_version": "v2"})
    live = _process(tmp_path, cases, run_eval.RecordingClient(FakeFinder(), capture=capture), "live")
    capture.close()
    records = [json.loads(line) for line in (tmp_path / "cap" / "finders.jsonl").read_text().splitlines()]
    assert len(records) == 2 * len(cases)
    assert all(r["prompt_sha256"] and r["model_identity"] == IDENTITIES[r["model"]] for r in records)
    replay = run_eval.FinderReplay(tmp_path / "cap", IDENTITIES)
    replayed = _process(tmp_path, cases, run_eval.RecordingClient(object(), replay=replay), "replay")
    for name in ("predictions.jsonl", "decisions.jsonl"):
        assert (live / name).read_bytes() == (replayed / name).read_bytes()
    assert replay.served == 2 * len(cases)
    assert (replayed / "timings.jsonl").exists()


def test_replay_refuses_other_prompts_models_and_missing_cases(tmp_path):
    cases = scorer.load_gold("dev")[:2]
    capture = run_eval.FinderCapture(tmp_path / "cap", IDENTITIES, {})
    _process(tmp_path, cases, run_eval.RecordingClient(FakeFinder(), capture=capture), "live")
    capture.close()
    other = {**IDENTITIES, "gemma4-26b-a4b": {**IDENTITIES["gemma4-26b-a4b"], "file": "other.gguf"}}
    with pytest.raises(run_eval.ReplayRefused):
        run_eval.FinderReplay(tmp_path / "cap", other)
    replay = run_eval.FinderReplay(tmp_path / "cap", IDENTITIES)
    with pytest.raises(run_eval.ReplayRefused):
        replay.serve(cases[0]["id"], "gemma4-e4b", "0" * 64)
    with pytest.raises(run_eval.ReplayRefused):
        replay.serve("no-such-case", "gemma4-e4b", "0" * 64)
    # A changed prompt (here: another finder context) is refused inside a run, not scored as a stage failure.
    from themis_shield.detectors import llm_finder
    before = llm_finder.configure_finder(context="a hospital")
    try:
        with pytest.raises(run_eval.ReplayRefused):
            _process(tmp_path, cases, run_eval.RecordingClient(object(), replay=replay), "changed")
    finally:
        llm_finder.configure_finder()
    assert before["context"] == "a hospital"


def test_replayed_errors_keep_their_stage_meaning(tmp_path):
    capture = run_eval.FinderCapture(tmp_path / "cap", IDENTITIES, {})
    capture.write("c1", "gemma4-e4b", "h1", error=TimeoutError())
    capture.write("c2", "gemma4-e4b", "h2", error=ValueError())
    capture.write("c3", "gemma4-e4b", "h3", error=RuntimeError())
    capture.close()
    replay = run_eval.FinderReplay(tmp_path / "cap", IDENTITIES)
    for case_id, digest, error in (("c1", "h1", TimeoutError), ("c2", "h2", ValueError), ("c3", "h3", RuntimeError)):
        with pytest.raises(error):
            replay.serve(case_id, "gemma4-e4b", digest)


def test_each_window_replays_its_own_answer_and_later_folders_win(tmp_path):
    # v0.4 finder-windows: several calls per case and model, told apart by prompt hash (DD-13).
    old = run_eval.FinderCapture(tmp_path / "old", IDENTITIES, {})
    old.write("c1", "gemma4-26b-a4b", "w1", payload={"entities": [{"label": "PERSON", "value": "old answer"}]})
    old.close()
    new = run_eval.FinderCapture(tmp_path / "new", IDENTITIES, {})
    new.write("c1", "gemma4-26b-a4b", "w1", payload={"entities": [{"label": "PERSON", "value": "window one"}]})
    new.write("c1", "gemma4-26b-a4b", "w2", payload={"entities": [{"label": "PERSON", "value": "window two"}]})
    new.close()
    replay = run_eval.FinderReplay([tmp_path / "old", tmp_path / "new"], IDENTITIES)
    assert replay.serve("c1", "gemma4-26b-a4b", "w1")["entities"][0]["value"] == "window one"
    assert replay.serve("c1", "gemma4-26b-a4b", "w2")["entities"][0]["value"] == "window two"
    with pytest.raises(run_eval.ReplayRefused, match="prompt hash differs"):
        replay.serve("c1", "gemma4-26b-a4b", "w3")


def test_case_files_and_the_held_out_guard(tmp_path):
    cases, hashes = run_eval.load_cases("dev", [str(EVAL / "cases" / "dev.jsonl"), str(EVAL / "cases" / "test.jsonl")])
    assert len(cases) == 521 and len(hashes) == 2  # the v0.1 test file is retired to dev
    held_out = tmp_path / "fresh.jsonl"
    held_out.write_text(json.dumps({"id": "X-1", "split": "test", "text": "x", "spans": []}) + "\n")
    with pytest.raises(SystemExit):
        run_eval.load_cases("dev", [str(held_out)])
    assert len(run_eval.load_cases("test", [str(held_out)])[0]) == 1
    with pytest.raises(SystemExit):
        run_eval.load_cases("dev", [str(EVAL / "cases" / "dev.jsonl")] * 2)


def test_capture_and_replay_folders_must_be_outside_the_repository(tmp_path):
    for flag in ("--capture-finders", "--replay-finders"):
        with pytest.raises(SystemExit):
            run_eval.main(["--split", "dev", "--run-name", "x", "--runs-dir", str(tmp_path), flag, str(EVAL / "cap")])


# --------------------------------------------------------------------------------------------------------------------
# v0.3: the detector canary at the start of every run, and per-stage timings
# --------------------------------------------------------------------------------------------------------------------
def test_timings_file_keeps_the_tier_keys_and_adds_stage_keys(tmp_path):
    out = _process(tmp_path, scorer.load_gold("dev")[:2], run_eval.RecordingClient(FakeFinder()), "timed")
    rows = [json.loads(line) for line in (out / "timings.jsonl").read_text().splitlines()]
    assert all(set(row) == {"id", "timings_ms", "stage_timings_ms"} for row in rows)
    assert set(rows[0]["timings_ms"]) == {"1", "2", "3"}
    assert {"rules", "pii-tracer", "propagate-tier1", "allowlist-tier1", "gemma4-e4b", "gemma4-26b-a4b",
            "union-final", "propagate-final", "allowlist-final", "gemma4-e4b:acquire"} <= set(rows[0]["stage_timings_ms"])


def _canary_targets():
    from test_canary import CanaryTracer
    return DetectionTargets(tracer=CanaryTracer(), thorough=True)


def test_eval_canary_live_and_replay(monkeypatch):
    from test_canary import CanaryFinder
    from themis_shield.detectors.rules import set_rule_packs
    set_rule_packs(("core", "au", "intl"))
    try:
        live = run_eval.eval_canary(_canary_targets(), CanaryFinder())
        assert live["passed"] and live["trigger"] == "eval"
        assert {c["detector"]: c["status"] for c in live["checks"]}["gemma4-26b-a4b"] == "pass"
        finder = CanaryFinder()
        replayed = run_eval.eval_canary(_canary_targets(), finder, replay=True)
        assert replayed["passed"] and finder.calls == []  # replay never calls a model
        assert [(c["detector"], c["error_code"]) for c in replayed["checks"] if c["status"] == "skipped"] == [
            ("gemma4-e4b", "replay"), ("gemma4-26b-a4b", "replay")]
        broken = run_eval.eval_canary(_canary_targets(), CanaryFinder("empty"))
        assert not broken["passed"]
    finally:
        set_rule_packs(None)


def test_a_failing_canary_stops_the_run_unless_allowed(tmp_path):
    from test_canary import CanaryFinder
    record = run_eval.eval_canary(_canary_targets(), CanaryFinder("empty"))
    config, path, lines = {"written_utc": "x"}, tmp_path / "config.json", []
    with pytest.raises(SystemExit):
        run_eval.apply_canary(record, config, path, lines.append)
    assert json.loads(path.read_text())["canary"] == record  # recorded before stopping
    assert "FAILED" in lines[-1]
    run_eval.apply_canary(record, config, path, lines.append, allow_failure=True)


def test_replayed_run_records_the_canary_in_config(tmp_path, monkeypatch):
    """End to end through main(): a replayed run starts with the canary (finders skipped) and records it."""
    from themis_shield.detectors import engine
    from themis_shield.runtime.catalogue import Catalogue
    from test_canary import CanaryTracer

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(engine, "tracer_for", lambda targets: CanaryTracer())
    from themis_shield.detectors import llm_finder
    cases = scorer.load_gold("dev")[:2]
    llm_finder.configure_finder(  # the prompt the run will use, so the capture's prompt hashes match
        context=run_eval.DEFAULT_CONTEXT, prompt_version="v2",
        do_not_flag=load_profile_json("institution", run_eval.DEFAULT_PROFILE).get("finder_do_not_flag"),
    )
    identities = run_eval.finder_identities(Catalogue.load(None))
    capture = run_eval.FinderCapture(tmp_path / "cap", identities, {})
    _process(tmp_path, cases, run_eval.RecordingClient(FakeFinder(), capture=capture), "live")
    capture.close()
    runs = tmp_path / "runs"
    argv = ["--split", "dev", "--run-name", "r", "--runs-dir", str(runs), "--ids", ",".join(c["id"] for c in cases),
            "--replay-finders", str(tmp_path / "cap")]
    try:
        assert run_eval.main(argv) == 0
        config = json.loads((runs / "r" / "config.json").read_text())
        assert config["canary"]["passed"] is True and config["finder_mode"] == "replay"
        assert [c["status"] for c in config["canary"]["checks"]] == ["pass", "pass", "pass", "skipped", "skipped"]
        assert "detector canary passed" in (runs / "r" / "run.log").read_text()
        # A tracer that has gone silent stops the next session before any case is processed.
        monkeypatch.setattr(engine, "tracer_for", lambda targets: CanaryTracer("empty"))
        with pytest.raises(SystemExit):
            run_eval.main(argv[:3] + ["r2"] + argv[4:])
        assert not (runs / "r2" / "predictions.jsonl").exists()
        assert json.loads((runs / "r2" / "config.json").read_text())["canary"]["passed"] is False
    finally:
        from themis_shield.detectors import llm_finder
        from themis_shield.detectors.rules import set_rule_packs
        llm_finder.configure_finder()
        set_rule_packs(None)
