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
