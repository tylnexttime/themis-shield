"""v0.4 fix "detectors-detection-view" (DD-06): PII-Tracer and the LLM finders read the detection view too."""

from __future__ import annotations

from themis_shield.detectors.contracts import StageReport, StageResult, Status, finding, on_detection_view
from themis_shield.detectors.llm_finder import FinderModel, LLMFinder
from themis_shield.detectors.settings import configure_detection_fixes
from themis_shield.types import Label

HIDDEN = "Reviewed by Ma\u200dra Lind\u00adahl on site."  # a zero-width joiner and a soft hyphen inside the name


class NameFinder:
    """Finds the name only when it is written without invisible characters, as a model reading the view would."""

    def __init__(self):
        self.seen = []

    def chat_json(self, model_role, messages, schema, max_tokens):
        content = messages[-1]["content"]
        self.seen.append(content)
        return {"entities": [{"label": "PERSON", "value": "Mara Lindahl"}] if "Mara Lindahl" in content else []}


def tracer(text):
    at = text.find("Mara Lindahl")
    found = (finding(at, at + len("Mara Lindahl"), Label.PERSON, "pii-tracer", 1),) if at >= 0 else ()
    return StageResult(found, StageReport("pii-tracer", Status.OK, len(found)))


def test_off_the_raw_text_hides_the_name():
    try:
        configure_detection_fixes("none")
        assert on_detection_view(HIDDEN, tracer).findings == ()
    finally:
        configure_detection_fixes(None)


def test_on_by_default():
    configure_detection_fixes(None)
    assert len(on_detection_view(HIDDEN, tracer).findings) == 1


def test_on_the_view_finds_it_and_maps_back_over_the_invisible_characters():
    configure_detection_fixes("all")
    try:
        result = on_detection_view(HIDDEN, tracer)
        (item,) = result.findings
        assert HIDDEN[item.start:item.end] == "Ma\u200dra Lind\u00adahl"
        client = NameFinder()
        found = LLMFinder(FinderModel.E4B, client).detect(HIDDEN).findings
        assert "\u200d" not in client.seen[0]
        assert [HIDDEN[f.start:f.end] for f in found] == ["Ma\u200dra Lind\u00adahl"]
    finally:
        configure_detection_fixes(None)


def test_text_the_view_leaves_unchanged_is_read_as_it_is():
    configure_detection_fixes("all")
    try:
        calls = []
        on_detection_view("Plain text.", lambda text: calls.append(text) or tracer(text))
        assert calls == ["Plain text."]
    finally:
        configure_detection_fixes(None)
