"""v0.4 fix "finder-windows" (DD-05): a long document is read in windows, so a dense one cannot overflow the answer."""

from __future__ import annotations

import pytest

from themis_shield.detectors.engine import run_stage
from themis_shield.detectors.llm_finder import (
    FINDER_WINDOW_CHARS, FINDER_WINDOW_THRESHOLD, FinderModel, LLMFinder, finder_windows,
)
from themis_shield.detectors.contracts import Status
from themis_shield.detectors.settings import configure_detection_fixes
from themis_shield.types import Label


def table(rows):
    return "name,staff_no,dob\n" + "".join(f"Person{n:04d} Example,S{n:05d},1990-01-{n % 28 + 1:02d}\n"
                                          for n in range(rows))


class Recorder:
    """Lists every name the window shows; fails on request."""

    def __init__(self, fail_on=None):
        self.windows, self.fail_on = [], fail_on

    def chat_json(self, model_role, messages, schema, max_tokens):
        window = messages[-1]["content"]
        self.windows.append(window)
        if self.fail_on is not None and len(self.windows) == self.fail_on:
            raise TimeoutError("SYNTHETIC")
        names = sorted({line.split(",")[0] for line in window.splitlines() if line.startswith("Person")})
        return {"entities": [{"label": "PERSON", "value": name} for name in names]}


@pytest.fixture
def windows_on():
    configure_detection_fixes("all")
    yield
    configure_detection_fixes(None)


def test_a_short_document_is_one_window():
    text = table(10)
    assert len(text) <= FINDER_WINDOW_THRESHOLD and finder_windows(text) == [text]


def test_windows_cover_every_line_and_repeat_the_table_header():
    text = "Staff list, synthetic.\n\n" + table(400)
    windows = finder_windows(text)
    assert len(windows) > 1
    for window in windows[1:]:
        assert window.startswith("name,staff_no,dob\n")
        assert len(window) <= FINDER_WINDOW_CHARS + 2 * 60
    for line in text.splitlines(keepends=True):
        assert any(line in window for window in windows)


def test_a_line_longer_than_a_window_is_still_read():
    text = "x" * (FINDER_WINDOW_CHARS * 2) + "\n" + table(300)
    assert any(len(window) > FINDER_WINDOW_CHARS for window in finder_windows(text))


def test_every_windows_values_are_protected_everywhere(windows_on):
    text = table(400) + "\nPerson0001 Example signed off the list.\n"
    client = Recorder()
    result = LLMFinder(FinderModel.GEMMA_26B_A4B, client).detect(text)
    assert len(client.windows) > 1
    found = {text[f.start:f.end] for f in result.findings if Label.PERSON in f.labels}
    assert {f"Person{n:04d} Example" for n in range(400)} <= found
    # the value from window 1 is found in the closing sentence too
    assert sum(text[f.start:f.end] == "Person0001 Example" for f in result.findings) == 2


def test_one_failed_window_fails_the_stage_closed(windows_on):
    text = table(400)
    result = run_stage(text, "gemma4-26b-a4b",
                       lambda: LLMFinder(FinderModel.GEMMA_26B_A4B, Recorder(fail_on=2)).detect(text))
    assert result.report.status == Status.TIMEOUT and result.findings == ()


def test_on_by_default_and_off_with_the_switch():
    configure_detection_fixes(None)
    client = Recorder()
    LLMFinder(FinderModel.GEMMA_26B_A4B, client).detect(table(400))
    assert len(client.windows) > 1
    try:
        configure_detection_fixes("none")
        client = Recorder()
        LLMFinder(FinderModel.GEMMA_26B_A4B, client).detect(table(400))
        assert len(client.windows) == 1
    finally:
        configure_detection_fixes(None)
