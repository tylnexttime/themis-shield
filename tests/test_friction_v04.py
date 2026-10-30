"""v0.4 friction fixes: secret references and implausible PII-Tracer evidence. Synthetic only."""

from __future__ import annotations

import pytest

from themis_shield.detectors.contracts import finding
from themis_shield.detectors.secret_context import references_only, relabel_secrets
from themis_shield.detectors.tracer_sanity import implausible, mark_implausible_tracer_values
from themis_shield.policy import contrary_tracer_findings
from themis_shield.types import Label


@pytest.mark.parametrize("text", [
    "import os\n\nSECRET_KEY = os.environ['SECRET_KEY']",
    '    "password": "{{secrets.db_password}}"',
    "password = %(env:DB_PASSWORD)s",
    'API_TOKEN = os.environ["RECORDS_API_TOKEN"]  # from the vault',
    "DB_PASSWORD=${DB_PASSWORD}\nAPI_KEY=<your-api-key>\nSMTP_PASSWORD=REPLACE_ME",
    'variable "db_password" {',
    "  administrator_login_password = var.db_password",
    "  url: postgres://svc_reports:{{ lookup('vault', 'secret/data/reports', 'password') }}@db.example",
    "  webhook_secret: vault:secret/data/reports#webhook",
    "      DEPLOY_TOKEN: ${{ secrets.DEPLOY_TOKEN }}",
    "const key = process.env.API_KEY",
])
def test_references_are_not_secrets(text):
    assert references_only(text, 0, len(text))


@pytest.mark.parametrize("text", [
    "password = hunter22",
    "DB_PASSWORD=Xk9pL2mQ",
    "token ghp_ABCDEFabcdef1234567890xyz",
    "API_KEY = os.getenv('API_KEY', 'sk-live-9f8e7d6c5b4a')",  # a default is a literal value
    "password: correcthorsebatterystaple",
    "  url: postgres://svc:Tr0ub4dor3@db.example",
    "DB_PASSWORD=${DB_PASSWORD}\nAPI_KEY=sk_live_51Hx9AbCdEf",  # one real value on a touched line keeps it
])
def test_literal_values_stay_secrets(text):
    assert not references_only(text, 0, len(text))


def test_a_reference_only_secret_finding_is_dropped_and_a_real_one_kept():
    text = "SECRET_KEY = os.environ['SECRET_KEY']\npassword = hunter22\n"
    ref = finding(0, text.index("\n"), Label.SECRET, "gemma4-26b-a4b", 3)
    real_start = text.index("password")
    real = finding(real_start, len(text) - 1, Label.SECRET, "rules", 1)
    out = relabel_secrets(text, (ref, real), frozenset({"secret-references"}))
    assert [text[f.start:f.end] for f in out] == ["password = hunter22"]
    assert relabel_secrets(text, (ref,), frozenset()) == (ref,)  # switch off: unchanged


TEXT = ("Notice: the depot opens on 10 May 2024 at Kestrel Reservoir. Born 3 March 1990 at 12 Harbour Way. "
        "Amount 186.50, see registry. Chair\nname,dob\nMara,1990-03-03\n")


def tracer(value, label):
    start = TEXT.index(value)
    return finding(start, start + len(value), label, "pii-tracer", 1)


@pytest.mark.parametrize("value, label, expected", [
    ("10 May 2024", Label.DATE_OF_BIRTH, True),
    ("3 March 1990", Label.DATE_OF_BIRTH, False),   # birth context in the same sentence
    ("1990-03-03", Label.DATE_OF_BIRTH, False),     # a table column headed "dob"
    ("Kestrel Reservoir", Label.ADDRESS, True),
    ("12 Harbour Way", Label.ADDRESS, False),
    ("186.50", Label.STUDENT_ID, True),
    ("registry", Label.URL_PERSONAL, True),
    ("Chair", Label.PERSON, True),
    ("Mara", Label.PERSON, False),
])
def test_implausible_tracer_values(value, label, expected):
    assert implausible(TEXT, tracer(value, label)) is expected


def test_flagged_values_stay_substituted_but_stop_holding():
    dates = tracer("10 May 2024", Label.DATE_OF_BIRTH)
    marked = mark_implausible_tracer_values(TEXT, (dates,))
    assert marked[0].implausible_tracer_value and marked[0].span == dates.span

    class Evidence:
        findings = marked

    assert contrary_tracer_findings(Evidence) == []


def test_a_value_a_finder_also_found_is_never_flagged():
    start = TEXT.index("10 May 2024")
    both = finding(start, start + 11, Label.DATE_OF_BIRTH, "pii-tracer", 1)
    from dataclasses import replace
    from themis_shield.detectors.contracts import FindingEvidence
    both = replace(both, evidence=both.evidence + (FindingEvidence(Label.DATE_OF_BIRTH, "gemma4-e4b", 2),))
    assert not mark_implausible_tracer_values(TEXT, (both,))[0].implausible_tracer_value
