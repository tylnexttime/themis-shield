"""Finder prompt versions: v1 reproducibility, v2 prefix stability, no eval text, no sector terms."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import pytest

from themis_shield.detectors import DetectionTargets, detect
from themis_shield.detectors import llm_finder
from themis_shield.detectors.contracts import StageReport, StageResult, Status
from themis_shield.integration import configure_finder_prompt
from themis_shield.profiles import load_profile_json
from themis_shield.types import Label

CASES = Path(__file__).resolve().parents[1] / "eval" / "cases"
# sha256 of the v1 prompt (context "a training and research institute", document "SAMPLE TEXT") rendered with the
# current demo profile's do-not-flag list: it pins v1 rendering with that profile. The frozen evaluation's exact v1
# rendering stays reproducible from git tag eval-kit-v0.1-frozen. The organisation-specific "do not list" examples
# live in the demo profile, not in code.
V1_DEMO_SHA256 = "80f2cd57bf301c85141a21cfdce028946905d828cbdc8c47b2411d3b63b5ca44"
# Country- and organisation-specific words that must not appear in the generic prompt. The organisation-specific
# examples themselves are checked through the demo profile's list (so this file does not repeat them).
SECTOR_TERMS = ("usi", "tfn", "medicare", "abn", "australia", "australian", "harbourline", "student number")


@pytest.fixture(autouse=True)
def _reset_finder():
    llm_finder.configure_finder(context=llm_finder.DEFAULT_FINDER_CONTEXT, prompt_version=None, do_not_flag=None)
    yield
    llm_finder.configure_finder(context=llm_finder.DEFAULT_FINDER_CONTEXT, prompt_version=None, do_not_flag=None)


def _profile(name):
    return load_profile_json("institution", name)


def test_v1_with_the_demo_profile_renders_the_pinned_prompt():
    llm_finder.configure_finder(context="a training and research institute", prompt_version="v1",
                                do_not_flag=_profile("harbourline-demo")["finder_do_not_flag"])
    (message,) = llm_finder.render_messages("SAMPLE TEXT")
    assert hashlib.sha256(message["content"].encode()).hexdigest() == V1_DEMO_SHA256


def test_v2_system_prefix_is_identical_for_every_document():
    llm_finder.configure_finder(prompt_version="v2", do_not_flag=_profile("harbourline-eval")["finder_do_not_flag"])
    first = llm_finder.render_messages("Alpha document text.")
    second = llm_finder.render_messages("A completely different document, much longer than the first one.")
    assert first[0]["role"] == "system" and first[0] == second[0]
    assert "Alpha" not in first[0]["content"]
    assert first[1]["role"] == "user" and "Alpha document text." in first[1]["content"]
    # The user message's fixed head is also shared, so the cached prefix runs up to the document itself.
    head = llm_finder.V2_USER.split("{text}")[0]
    assert first[1]["content"].startswith(head) and second[1]["content"].startswith(head)
    assert first[0]["content"] == llm_finder.v2_system_prompt()


def test_v2_prefix_carries_no_sector_terms_without_a_profile():
    llm_finder.configure_finder(prompt_version="v2")
    text = llm_finder.v2_system_prompt().lower() + llm_finder.V2_USER.lower()
    words = set(re.findall(r"[\w-]+", text))
    for term in SECTOR_TERMS:
        assert (term not in words) if " " not in term else (term not in text), term
    # The demo profile's organisation-specific items (codes with digits, long acronyms) stay out of the generic prompt.
    sector = [item for item in _profile("harbourline-demo")["finder_do_not_flag"]
              if re.search(r"\d|\b[A-Z]{4,}\b", item)]
    assert len(sector) >= 2
    for item in sector:
        assert item.lower() not in text, item
        assert not set(re.findall(r"\b[A-Z]{4,}\b", item)) & {w.upper() for w in words}, item


def _ngrams(text, n=8):
    words = re.findall(r"\w+", text.lower())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def test_v2_prompt_shares_no_8_gram_with_any_evaluation_case():
    case_grams = set()
    for path in sorted(CASES.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                case_grams |= _ngrams(json.loads(line)["text"])
    assert case_grams
    rendered = [llm_finder.V2_USER.replace("{text}", " ")]
    for profile in (None, "harbourline-eval", "harbourline-demo"):
        llm_finder.configure_finder(
            context="a training and research institute", prompt_version="v2",
            do_not_flag=_profile(profile)["finder_do_not_flag"] if profile else None,
        )
        rendered.append(llm_finder.v2_system_prompt())
    shared = set().union(*(_ngrams(text) & case_grams for text in rendered))
    assert not shared, sorted(" ".join(gram) for gram in shared)[:5]


def test_v2_labels_are_native_labels_and_the_schema_matches():
    assert set(llm_finder.V2_LABELS) <= {label.value for label in Label}
    assert llm_finder.V2_SCHEMA["schema"]["properties"]["entities"]["items"]["properties"]["label"]["enum"] == list(
        llm_finder.V2_LABELS)
    assert set(llm_finder.V2_SENSITIVE_GUIDE) <= set(llm_finder.V2_LABELS)
    for examples, lookalikes in llm_finder.V2_SENSITIVE_GUIDE.values():
        assert 3 <= len(examples) <= 4 and len(lookalikes) == 2


def test_payload_validation_follows_the_prompt_version():
    payload = {"entities": [{"label": "UNION", "value": "x"}]}
    with pytest.raises(ValueError):
        llm_finder.validate_payload(payload)  # v1 labels
    assert llm_finder.validate_payload(payload, llm_finder.labels_for("v2"))


def test_finder_settings_are_validated():
    with pytest.raises(ValueError):
        llm_finder.set_finder_prompt("v9")
    for bad in ("one string", [], ["{format}"], ["ok", 3], ["line\nbreak"]):
        with pytest.raises(ValueError):
            llm_finder.set_finder_do_not_flag(bad)
    assert llm_finder.set_finder_do_not_flag(None) is None
    assert llm_finder.do_not_flag_items() == llm_finder.GENERIC_DO_NOT_FLAG


def test_integration_takes_do_not_flag_from_the_institution_profile():
    settings = configure_finder_prompt(
        {"detection": {"finder_prompt": "v2", "finder_context": "a test lab"}}, _profile("harbourline-eval"))
    assert settings["prompt_version"] == "v2" and settings["context"] == "a test lab"
    assert "Tidewater Building" in llm_finder.v2_system_prompt()
    settings = configure_finder_prompt({}, None)
    assert settings["prompt_version"] == llm_finder.DEFAULT_PROMPT_VERSION and settings["do_not_flag"] is None


class _Tracer:
    def detect(self, text):
        return StageResult((), StageReport("pii-tracer", Status.OK))


class _V2Client:
    def __init__(self):
        self.messages = []

    def chat_json(self, model_role, messages, schema, max_tokens):
        self.messages.append(messages)
        assert schema["schema"]["properties"]["entities"]["items"]["properties"]["label"]["enum"] == list(
            llm_finder.V2_LABELS)
        return {"entities": [{"label": "UNION", "value": "is the union rep"},
                             {"label": "PERSON", "value": "Ilse"}]}


def test_engine_runs_prompt_v2_end_to_end():
    llm_finder.configure_finder(prompt_version="v2")
    client = _V2Client()
    result = detect("Ilse is the union rep here.", {}, DetectionTargets(tracer=_Tracer(), llm_client=client,
                                                                          thorough=True))
    assert len(client.messages) == 2 and all(m[0]["role"] == "system" for m in client.messages)
    labels = {label for item in result.findings for label in item.labels}
    assert Label.UNION in labels and Label.PERSON in labels
    assert result.decision.value == "ASK"  # UNION is a sensitive label
