"""Vetting records (v0.3.1): a release is vetted with the gate's own pipeline, and the record holds counts, never
values. Synthetic data only."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from themis_shield import __version__, vetting
from themis_shield.vault import InMemoryVault
from themis_shield.vetting import (
    METHOD_BASELINES, ReleaseInfo, VettingError, assert_value_free, load_resolutions, method_baseline,
    read_release, render_markdown, vet_release,
)

from router_fakes import NOW, Finders, harness


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ReleaseInfo("DA-0042", "synthetic-roster", "R-2026-11-01", "Summarise roster coverage",
                      "cloud-deidentified", ("UC-0007",))
CONTACTS = "name,email,phone\nMara Lindahl,mara.lindahl@example.org,0412 345 678\n"
CLEAN = "The committee meets on the first Tuesday of each month to review the timetable."
MARKED = "SECRET\nThe synthetic plan for the night desk."
KEY = "api_key = 'sk-proj-" + "9f8e7d6c5b4a39281706f5e4d3c2b1a0'\n"
UNSURE = "Ravi Example leads the synthetic night desk."
VALUES = ("Mara Lindahl", "mara.lindahl@example.org", "0412 345 678", "Ravi Example")
NAMES = ("contacts-of-mara.csv", "clean.txt", "marked.txt", "key.txt", "image.dat", "unsure.txt")


class UnsureFinders(Finders):
    """Adds three names that are not in the text whenever the night desk is mentioned: ASK:unmatched-llm-values."""

    def chat_json(self, model_role, messages, schema, max_tokens):
        result = super().chat_json(model_role, messages, schema, max_tokens)
        if "night desk" in "\n".join(message["content"] for message in messages):
            result["entities"] += [{"label": "PERSON", "value": value}
                                   for value in ("Quill Harrowgate", "Senna Brightwater", "Oren Vale")]
        return result


def documents():
    return list(zip(NAMES, (CONTACTS, CLEAN, MARKED, KEY, None, UNSURE)))


def run(tmp_path, *, resolutions=None, docs=None, release=RELEASE, codebook=None):
    h = harness(tmp_path)
    h.router.targets = replace(h.router.targets, llm_client=UnsureFinders())
    out = tmp_path / "out"
    record = vet_release(h.router, docs or documents(), release, out, resolutions=resolutions,
                         codebook_vault=codebook, now=NOW)
    return h, out, record


def by_index(record):
    return {item["index"]: item for item in record["part3_run"]["documents"]}


def test_each_document_gets_the_gates_own_decision(tmp_path):
    _, out, record = run(tmp_path)
    docs = by_index(record)
    assert docs[1]["decision"] == "RELEASE" and docs[1]["derived"]
    assert docs[1]["findings_by_label"] == {"EMAIL": 1, "PERSON": 5, "PHONE": 1}
    assert docs[2]["decision"] == "RELEASE" and docs[2]["findings_by_label"] == {}
    # A local-only marking is decided before detection, as on the chat path, and never reaches the release.
    assert docs[3]["marking"] == "SECRET" and docs[3]["reasons"] == ["ROUTE_LOCAL_MARKING"] and not docs[3]["derived"]
    assert docs[4]["reasons"] == ["BLOCK:secret"] and not docs[4]["derived"]
    assert docs[5]["reasons"] == ["BLOCK:not-utf8-text"]
    assert docs[6]["decision"] == "ASK" and docs[6]["resolution"] is None and not docs[6]["derived"]
    assert record["part3_run"]["decisions"] == {"RELEASE": 2, "ASK": 1, "BLOCK": 3}
    assert sorted(p.name for p in (out / "release").iterdir()) == ["doc-0001.csv", "doc-0002.txt"]
    assert record["part2_classification"]["gate_held_local_only"] == 1


def test_derived_documents_are_de_identified_and_the_codebook_restores_them(tmp_path):
    codebook = InMemoryVault(clock=lambda: NOW)
    h, out, record = run(tmp_path, codebook=codebook)
    derived = (out / "release" / "doc-0001.csv").read_text(encoding="utf-8")
    assert not any(value in derived for value in VALUES)
    mapping = codebook.mapping(record["part5_codebook"]["handle"])
    restored = derived
    for token, value in mapping.items():
        restored = restored.replace(token, value)
    assert restored == CONTACTS
    # The chat vault is not the codebook: its four-hour idle expiry would make the release unrestorable.
    assert h.router.vault.mapping(record["part5_codebook"]["handle"]) == {}
    assert record["part5_codebook"]["entries"] == len(mapping)


def test_the_record_holds_counts_never_values_or_names(tmp_path):
    _, out, record = run(tmp_path)
    text = (out / "vetting-record.json").read_text(encoding="utf-8") + (out / "vetting-record.md").read_text(
        encoding="utf-8")
    for value in VALUES + NAMES + ("contacts-of-mara",):
        assert value.casefold() not in text.casefold(), value
    # File names live only in the local index, which stays with the codebook.
    index = json.loads((out / "local-only" / "document-index.json").read_text(encoding="utf-8"))
    assert index["documents"]["1"] == "contacts-of-mara.csv"


def test_unresolved_asks_leave_the_record_incomplete(tmp_path):
    _, _, record = run(tmp_path)
    assert record["status"] == "incomplete" and record["part3_run"]["unresolved_asks"] == [6]
    assert "approved" not in json.dumps(record).casefold()


@pytest.mark.parametrize("choice, derived", [("release", True), ("exclude", False)])
def test_a_persons_resolution_settles_an_ask(tmp_path, choice, derived):
    first = by_index(run(tmp_path / "a")[2])[6]["sha256"]
    resolution = {6: {"sha256": first, "resolution": choice, "by_role": "data custodian", "note": "checked"}}
    _, out, record = run(tmp_path / "b", resolutions=resolution)
    doc = by_index(record)[6]
    assert record["status"] == "ready-for-review"
    assert doc["resolution"] == {"resolution": choice, "by_role": "data custodian", "note": "checked"}
    assert doc["derived"] is derived and (out / "release" / "doc-0006.txt").exists() is derived


def test_a_resolution_for_other_content_is_ignored(tmp_path):
    resolution = {6: {"sha256": "0" * 64, "resolution": "release", "by_role": "data custodian", "note": ""}}
    _, _, record = run(tmp_path, resolutions=resolution)
    assert record["status"] == "incomplete" and by_index(record)[6]["resolution"] is None
    check = next(c for c in record["part6_inspection"]["automated"] if c["check"].startswith("ASK resolutions"))
    assert check["passed"] is False


def test_a_value_found_in_one_document_is_never_left_in_another(tmp_path):
    # Doc 2 names Mara with nothing around her that a detector reads as a name: the cross-check holds it.
    docs = [("a.csv", CONTACTS), ("b.txt", "Rota: lindahl@ is unrelated; Mara Lindahl was mentioned.")]

    class Blind(Finders):
        def chat_json(self, model_role, messages, schema, max_tokens):
            return {"entities": []}

    h = harness(tmp_path)
    h.router.targets = replace(h.router.targets, llm_client=Blind())
    record = vet_release(h.router, docs, RELEASE, tmp_path / "out", now=NOW)
    doc = by_index(record)[2]
    assert doc["decision"] == "BLOCK" and doc["reasons"][-1] == "BLOCK:original-in-output" and not doc["derived"]
    assert not (tmp_path / "out" / "release" / "doc-0002.txt").exists()
    check = next(c for c in record["part6_inspection"]["automated"] if c["check"].startswith("no protected value"))
    assert check["passed"] and "detection miss: documents 2" in check["detail"]


def test_a_record_that_would_carry_a_value_is_refused_and_nothing_is_written(tmp_path):
    release = replace(RELEASE, purpose="Check what Mara Lindahl wrote")
    with pytest.raises(VettingError, match="protected value"):
        run(tmp_path, release=release)
    assert not (tmp_path / "out").exists()


def test_the_output_folder_must_be_new_or_empty(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "old.txt").write_text("x", encoding="utf-8")
    with pytest.raises(VettingError, match="new or empty"):
        run(tmp_path)


def test_assert_value_free_ignores_values_too_short_to_search_for():
    assert_value_free("data custodian", ["Data"])
    with pytest.raises(VettingError):
        assert_value_free("purpose: ask MARA LINDAHL", ["Mara Lindahl"])


@pytest.mark.parametrize("change", [
    {"route": "anywhere"}, {"asset": "has space"}, {"release_id": ""}, {"purpose": ""}, {"purpose": "x" * 301},
    {"references": ("ok", "not ok")},
])
def test_release_metadata_is_validated(change):
    fields = {"asset": "DA-1", "object": "o", "release_id": "r", "purpose": "p", "route": "local-only",
              "references": ()}
    with pytest.raises(VettingError):
        ReleaseInfo(**{**fields, **change})


def test_method_baseline_is_the_published_result_and_says_when_it_is_inherited():
    base = method_baseline("0.3.0", typed_enabled=True)
    assert base["verdict"] == "FAIL" and base["released_leaks"] == 28 and base["clean_friction"] == 0.248
    assert base["inherited_from"] is None
    later = method_baseline("0.3.1")
    assert later["inherited_from"] == "0.3.0" and later["clean_friction"] == 0.304
    assert method_baseline("0.1.0")["available"] is False
    # The table quotes the published results file; keep them in step.
    results = (ROOT / METHOD_BASELINES["0.3.0"]["results"]).read_text(encoding="utf-8")
    for figure in ("28", "0.937", "0.911", "0.740", "0.304", "0.248", "404", "5,841"):
        assert figure in results, figure


def test_markdown_carries_the_seven_parts_and_leaves_peoples_parts_open(tmp_path):
    _, out, record = run(tmp_path)
    markdown = (out / "vetting-record.md").read_text(encoding="utf-8")
    assert markdown == render_markdown(record)
    for heading in ("## 1. Identity", "## 2. Classification", "## 3. De-identification run", "## 4. Does it work?",
                    "## 5. Codebook custody", "## 6. Inspection checklist", "## 7. Sign-offs"):
        assert heading in markdown
    assert "**FAIL**" in markdown and "_to be completed_" in markdown
    assert [s["who"] for s in record["part7_signoffs"]] == [None, None]


def test_read_release_marks_non_text_files_unreadable(tmp_path):
    (tmp_path / "rel" / "sub").mkdir(parents=True)
    (tmp_path / "rel" / "sub" / "a.txt").write_text(CLEAN, encoding="utf-8")
    (tmp_path / "rel" / "b.csv").write_bytes(b"\xff\xfe\x00bad")
    (tmp_path / "rel" / "c.png").write_bytes(b"\x89PNG")
    assert read_release(tmp_path / "rel") == [("b.csv", None), ("c.png", None), ("sub/a.txt", CLEAN)]
    with pytest.raises(VettingError):
        read_release(tmp_path / "missing")


def test_load_resolutions_validates_the_file(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"documents": {"6": {"sha256": "ab", "resolution": "maybe", "by_role": "x"}}}),
                    encoding="utf-8")
    with pytest.raises(VettingError):
        load_resolutions(path)
    path.write_text(json.dumps({"documents": {"6": {"sha256": "ab", "resolution": "exclude",
                                                    "by_role": "data custodian"}}}), encoding="utf-8")
    assert load_resolutions(path) == {6: {"sha256": "ab", "resolution": "exclude", "by_role": "data custodian",
                                          "note": ""}}


def test_cli_runs_end_to_end_and_returns_incomplete_for_open_asks(tmp_path, monkeypatch):
    import themis_shield.integration as integration
    import themis_shield.startup as startup

    h = harness(tmp_path)
    h.router.targets = replace(h.router.targets, llm_client=UnsureFinders())
    config = tmp_path / "local.toml"
    config.write_text("[gate]\nsynthetic_only = true\n", encoding="utf-8")
    monkeypatch.setattr(integration, "build_router", lambda path: h.router)
    monkeypatch.setattr(startup, "check_models", lambda router: None)
    monkeypatch.setattr(vetting, "_codebook_vault", lambda path, config: InMemoryVault(clock=lambda: NOW))
    folder = tmp_path / "rel"
    folder.mkdir()
    for name, text in documents():
        if text is not None:
            (folder / name).write_text(text, encoding="utf-8")
    argv = [str(folder), "--out", str(tmp_path / "out"), "--codebook", str(tmp_path / "codebook.db"),
            "--asset", "DA-0042", "--object", "synthetic-roster", "--release", "R-1", "--purpose", "Coverage",
            "--route", "cloud-deidentified", "--reference", "UC-0007", "--config", str(config)]
    assert vetting.main(argv) == 3
    record = json.loads((tmp_path / "out" / "vetting-record.json").read_text(encoding="utf-8"))
    assert record["part1_identity"]["references"] == ["UC-0007"]
    assert record["part3_run"]["gate"]["config_sha256"] and record["part3_run"]["gate"]["version"] == __version__
    (tmp_path / "codebook.db").write_text("", encoding="utf-8")
    assert vetting.main(argv[:2] + [str(tmp_path / "out2")] + argv[3:]) == 2   # the codebook file exists
