"""v0.4 friction fix secret-references: a reference to where a secret lives is not a secret. Synthetic only."""

from __future__ import annotations

import pytest

from themis_shield.detectors.contracts import finding
from themis_shield.detectors.secret_context import references_only, relabel_secrets
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
    "let token = process.env.TOKEN;",
    "from settings import config\n{\n  API_KEY = os.environ['API_KEY']\n}",
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
    "webhook_secret: secret:Xk9pL2mQ",  # a secret-manager prefix without a path is a value
    # The leaks that failed the first version's ratchet (30/10/2026): short values on lines that assign nothing.
    "  Temporary password: Dune!9301z",
    "Reissue card 5555 5555 5555 4444; temporary PIN 2970 to be changed at first use.",
    "Rosalind reports it as 7467 but says it stopped working this morning.",
    "Card used: 4242424242424242, expiry 10/29, CVV 428 (agent note: customer typed the CVV into chat).",
    '    assert rec.pin == "8151"',
    "  account thornba | initial password Ketch43@Qz",
    "$ curl -u ptupou:Ketch89!Wx https://api.example/people",
    "# the old password was hunter22",
    "const key = 'sk_live_51Hx9AbCdEf'",
])
def test_values_stay_secrets(text):
    assert not references_only(text, 0, len(text))


def test_a_reference_only_secret_finding_is_dropped_and_a_real_one_kept():
    text = "SECRET_KEY = os.environ['SECRET_KEY']\npassword = hunter22\n"
    ref = finding(0, text.index("\n"), Label.SECRET, "gemma4-26b-a4b", 3)
    real_start = text.index("password")
    real = finding(real_start, len(text) - 1, Label.SECRET, "rules", 1)
    out = relabel_secrets(text, (ref, real), frozenset({"secret-references"}))
    assert [text[f.start:f.end] for f in out] == ["password = hunter22"]
    assert relabel_secrets(text, (ref,), frozenset()) == (ref,)  # switch off: unchanged


def test_a_reference_keeps_its_other_labels():
    text = "OWNER_EMAIL = os.environ['OWNER_EMAIL']"
    both = finding(0, len(text), Label.SECRET, "gemma4-e4b", 2)
    from dataclasses import replace
    from themis_shield.detectors.contracts import FindingEvidence
    both = replace(both, labels=frozenset({Label.SECRET, Label.EMAIL}),
                   evidence=both.evidence + (FindingEvidence(Label.EMAIL, "gemma4-e4b", 2),))
    out = relabel_secrets(text, (both,), frozenset({"secret-references"}))
    assert len(out) == 1 and out[0].labels == frozenset({Label.EMAIL})
