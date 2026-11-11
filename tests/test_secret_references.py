"""v0.4 friction fix secret-references: a reference to where a secret lives is not a secret. Synthetic only."""

from __future__ import annotations

import pytest

from themis_shield.detectors import DetectionTargets, detect
from themis_shield.detectors.contracts import StageReport, StageResult, Status, finding
from themis_shield.detectors.secret_context import references_only, relabel_secrets
from themis_shield.types import Decision, Label, ReasonCode


@pytest.mark.parametrize("text", [
    "import os\n\nSECRET_KEY = os.environ['SECRET_KEY']",
    '    "password": "{{secrets.db_password}}"',
    "password = %(env:DB_PASSWORD)s",
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


def test_no_labelled_secret_in_the_evaluation_sets_reads_as_a_reference():
    # The first version passed 96 of these (30/10/2026) and the ratchet caught it only where nothing else covered them.
    # Every labelled SECRET must sit on a line that keeps its label, whatever any detector found.
    import json
    from pathlib import Path

    cases = Path(__file__).resolve().parents[1] / "eval" / "cases"
    passed, checked = [], 0
    for path in sorted(cases.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            for span in case["spans"]:
                if span["label"] == "SECRET":
                    checked += 1
                    if references_only(case["text"], span["start"], span["end"]):
                        passed.append((case["id"], case["text"][span["start"]:span["end"]]))
    assert checked > 100
    assert passed == []


# ----------------------------------------------------------------------------------------------------------------------
# COR-001 (independent review, 11/11/2026): a finding is a reference only where it sits in the reference, not on the line
# ----------------------------------------------------------------------------------------------------------------------
LIVE_KEY = "sk_" "live_" "51Hx9AbCdEfGhIjKlMnOpQrStUv"  # synthetic, built in parts
PADDED_KEY = "dGhpcy1pcy1h" "LXN5bnRoZXRpYy1r" "ZXktMTIzNDU2Nzg5MDE="  # synthetic base64, one "=", no "+" or "/"


class _Tracer:
    def __init__(self, findings):
        self.findings = tuple(findings)

    def detect(self, text):
        return StageResult(self.findings, StageReport("pii-tracer", Status.OK))


class _Finders:
    def __init__(self, value):
        self.value = value

    def chat_json(self, model_role, messages, schema, max_tokens):
        return {"entities": [{"label": "SECRET", "value": self.value}]}


def _detect_secret(text, value):
    """The rules, PII-Tracer and both finders all report ``value`` as a SECRET."""
    start = text.index(value)
    tracer = _Tracer([finding(start, start + len(value), Label.SECRET, "pii-tracer", 1)])
    return detect(text, {}, DetectionTargets(tracer=tracer, llm_client=_Finders(value), thorough=True))


def _secret_kept(result, text, value):
    return any(Label.SECRET in f.labels and value in text[f.start:f.end] for f in result.findings)


@pytest.mark.parametrize("text", [
    'API_KEY = os.environ["API_KEY"]  # old: ' + LIVE_KEY,
    "API_KEY = process.env.API_KEY  // old: " + LIVE_KEY,
    'API_KEY = os.environ["API_KEY"]  // rotated, was ' + LIVE_KEY + "\nDEBUG = 0",
    PADDED_KEY,
    "Rotate the deploy key tonight.\n" + PADDED_KEY + "\nThen restart the service.",
])
def test_a_real_key_beside_or_instead_of_a_reference_is_blocked(text):
    value = LIVE_KEY if LIVE_KEY in text else PADDED_KEY
    result = _detect_secret(text, value)
    assert result.decision == Decision.BLOCK and ReasonCode.SECRET in result.reasons
    assert _secret_kept(result, text, value)


def test_a_reference_with_a_harmless_comment_is_still_released():
    text = 'API_KEY = os.environ["API_KEY"]  # from the vault\nDEBUG = 0'
    result = _detect_secret(text, 'os.environ["API_KEY"')  # what the rules and finders cut out of the reference
    assert result.decision == Decision.RELEASE and not result.findings


def test_a_span_must_sit_in_the_reference_not_in_its_comment_or_key():
    line = 'API_TOKEN = os.environ["RECORDS_API_TOKEN"]  # from the vault'
    reference = line.index("os.environ")
    comment = line.index("#")
    assert references_only(line, reference, line.index("]") + 1)  # the reference itself
    assert references_only(line, 0, comment - 2)  # key and reference together
    assert not references_only(line, 0, len(line))  # the span reaches into the comment
    assert not references_only(line, comment, len(line))
    # The key alone, over a non-empty reference, is the name of a variable and no longer a secret (the friction fix below);
    # over a value-less key it still is (test_a_key_with_no_value_is_not_a_reference_for_a_span_over_the_key).
    assert references_only(line, 0, len("API_TOKEN"))
    slash = "API_KEY = process.env.API_KEY  // old: " + LIVE_KEY
    assert references_only(slash, 0, slash.index("  //"))
    assert not references_only(slash, slash.index(LIVE_KEY), len(slash))


def test_a_key_with_no_value_is_not_a_reference_for_a_span_over_the_key():
    assert not references_only(PADDED_KEY, 0, len(PADDED_KEY))
    text = "Rotate the deploy key tonight.\n" + PADDED_KEY + "\n"
    start = text.index(PADDED_KEY)
    assert not references_only(text, start, start + len(PADDED_KEY))
    assert not references_only("password:", 0, len("password:"))
    commented = "API_KEY =  # old: " + LIVE_KEY  # a comment never makes a value-free line a reference
    assert not references_only(commented, 0, len(commented))


# ----------------------------------------------------------------------------------------------------------------------
# COR-001 friction (development data): the NAME of a variable assigned a reference is not a secret value. After COR-001
# a finder answer that tagged only the key ("SECRET_KEY" in "SECRET_KEY = os.environ[...]") kept its label.
# ----------------------------------------------------------------------------------------------------------------------
RANDOM_KEY = "Xk9pL2mQ7vR4tY8wZ3nB6cD1fG5hJ0aS"  # synthetic: a long random-looking token used as a key name
PY_SETTINGS = 'import os\n\nDJANGO_SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")\nDEBUG = False\n'
CI_ENV = (
    "name: release\n"
    "on: push\n"
    "jobs:\n"
    "  publish:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - run: ./publish.sh\n"
    "        env:\n"
    "          PYPI_TOKEN: ${{ secrets.PYPI_TOKEN }}\n"
    "          DOCKER_PASSWORD: ${{ secrets.DOCKER_PASSWORD }}\n"
)


class _FindersMany:
    def __init__(self, values):
        self.values = values

    def chat_json(self, model_role, messages, schema, max_tokens):
        return {"entities": [{"label": "SECRET", "value": value} for value in self.values]}


def _detect_secret_names(text, names):
    """The tracer and both finders report each of ``names`` (and only those) as a SECRET."""
    found = [finding(text.index(n), text.index(n) + len(n), Label.SECRET, "pii-tracer", 1) for n in names]
    return detect(text, {}, DetectionTargets(tracer=_Tracer(found), llm_client=_FindersMany(names), thorough=True))


def _name_span(text, name):
    start = text.index(name)
    return start, start + len(name)


@pytest.mark.parametrize("text, names", [
    (PY_SETTINGS, ["DJANGO_SECRET_KEY"]),
    (CI_ENV, ["PYPI_TOKEN", "DOCKER_PASSWORD"]),
    ("SMTP_PASSWORD=${SMTP_PASSWORD}\nDEBUG=0\n", ["SMTP_PASSWORD"]),
])
def test_a_finder_answer_that_tags_only_the_name_of_a_reference_assignment_is_released(text, names):
    result = _detect_secret_names(text, names)
    assert result.decision == Decision.RELEASE and not result.findings


@pytest.mark.parametrize("text, name", [
    (PY_SETTINGS, "DJANGO_SECRET_KEY"),
    (CI_ENV, "PYPI_TOKEN"),
    (CI_ENV, "DOCKER_PASSWORD"),
    ('SECRET_KEY = os.environ["SECRET_KEY"]', "SECRET_KEY"),
    ("export DB_PASSWORD=${DB_PASSWORD}", "DB_PASSWORD"),
    ("  - REGISTRY_PASSWORD: ${{ secrets.REGISTRY_PASSWORD }}", "REGISTRY_PASSWORD"),
    ("const token = process.env.API_TOKEN;", "token"),
    ("  db.password: vault:secret/data/reports#db", "db.password"),
    ("  admin-password = var.admin_password", "admin-password"),
    ('API_KEY = os.environ["API_KEY"]  # old: ' + LIVE_KEY, "API_KEY"),  # the name; the comment is judged on its own
    ("DB_PASSWORD=${DB_PASSWORD}\nAPI_KEY=" + LIVE_KEY + "\n", "DB_PASSWORD"),  # the literal is on another line
])
def test_the_name_of_a_reference_assignment_is_not_a_secret(text, name):
    start, end = _name_span(text, name)
    assert references_only(text, start, end)
    assert references_only(text, start + 1, end - 1)  # cut out of the name


@pytest.mark.parametrize("line, name", [
    (RANDOM_KEY + ":", RANDOM_KEY),  # a value-less key: the name is all the line says
    (RANDOM_KEY + " =", RANDOM_KEY),
    (RANDOM_KEY + ":  # old", RANDOM_KEY),  # a comment never makes a value
    ("password:", "password"),
    ("API_KEY =  # old: " + LIVE_KEY, "API_KEY"),
    ("API_KEY = '" + LIVE_KEY + "'", "API_KEY"),  # a literal value: the line is not a reference at all
    ("API_KEY: " + LIVE_KEY, "API_KEY"),
    ("password = hunter22", "password"),
    ("API_KEY = os.getenv('API_KEY', 'sk-live-9f8e7d6c5b4a')", "API_KEY"),  # a default is a literal
    ("DB_PASSWORD=${DB_PASSWORD}\nAPI_KEY=" + LIVE_KEY + "\n", "API_KEY"),
])
def test_a_name_with_no_value_or_a_literal_value_stays_a_secret(line, name):
    start, end = _name_span(line, name)
    assert not references_only(line, start, end)


def test_a_lone_padded_key_and_a_random_key_name_stay_secrets():
    for line in (PADDED_KEY, RANDOM_KEY + ":", RANDOM_KEY + " ="):
        assert not references_only(line, 0, len(line))
    assert not references_only("Rotate it.\n" + PADDED_KEY + "\n", 11, 11 + len(PADDED_KEY))


def test_a_span_that_leaves_the_name_is_judged_as_before():
    line = 'API_TOKEN = os.environ["RECORDS_API_TOKEN"]  # from the vault'
    assert not references_only(line, 0, len("API_TOKEN ="))  # the name and its separator, short of the reference
    assert not references_only(line, len("API_TOKEN"), line.index("os.environ"))  # the separator alone
    assert not references_only(line, 0, len(line))  # the name through the comment
    assert references_only(line, 0, line.index("  #"))  # name and reference together (COR-001)
    literal = "API_TOKEN = " + LIVE_KEY
    assert not references_only(literal, 0, len(literal))  # name and a literal value together
    assert not references_only(literal, 0, literal.index(LIVE_KEY) + 5)
    commented = 'API_KEY = os.environ["API_KEY"]  # old: ' + LIVE_KEY
    assert not references_only(commented, 0, len(commented))  # name through a secret in the comment
    assert not references_only(commented, commented.index(LIVE_KEY), len(commented))


@pytest.mark.parametrize("text, value", [
    ("Rotated.\n" + RANDOM_KEY + ":\nDone.\n", RANDOM_KEY),
    ("Rotated.\n" + RANDOM_KEY + " =\nDone.\n", RANDOM_KEY),
    ("STRIPE_API_KEY = '" + LIVE_KEY + "'\n", LIVE_KEY),
    ("STRIPE_API_KEY: " + LIVE_KEY + "\n", LIVE_KEY),
    ('STRIPE_API_KEY = os.environ["STRIPE_API_KEY"]  # old: ' + LIVE_KEY + "\n", LIVE_KEY),
])
def test_a_key_with_no_value_or_a_literal_value_is_still_blocked_end_to_end(text, value):
    result = _detect_secret(text, value)
    assert result.decision == Decision.BLOCK and ReasonCode.SECRET in result.reasons
    assert _secret_kept(result, text, value)
