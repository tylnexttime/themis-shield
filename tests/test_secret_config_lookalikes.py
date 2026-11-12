"""v0.5 friction fix secret-config-lookalikes: configuration shapes that are plainly not credentials.

Split F's clean configuration files were BLOCKed for ``secret`` on four shapes a rule can tell from a credential: a
template call to a secret manager, a URI under a key that says it is a reference, a path of words naming a directory or a
configuration file, and a camel-case key name. Every line below is invented. Each cause is tested end to end (the rules,
PII-Tracer and both finders all report the value, as the real run's detectors did) and against what must still BLOCK.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from themis_shield.detectors import secret_context, settings
from themis_shield.detectors.settings import (
    DEFAULT_DETECTION_FIXES, DETECTION_FIXES, SECRET_CONTEXT_FIXES, configure_detection_fixes, fix_on,
)
from themis_shield.types import Decision, Label, ReasonCode

from test_secret_references import LIVE_KEY, PADDED_KEY, _detect_secret, _detect_secret_names, _secret_kept

OFF = [name for name in DEFAULT_DETECTION_FIXES if name != "secret-config-lookalikes"]


def _released(text, value):
    result = _detect_secret(text, value)
    return result.decision == Decision.RELEASE and not any(Label.SECRET in f.labels for f in result.findings)


def _blocked(text, value):
    result = _detect_secret(text, value)
    return result.decision == Decision.BLOCK and ReasonCode.SECRET in result.reasons and _secret_kept(result, text, value)


# ----------------------------------------------------------------------------------------------------------------------
# The switch
# ----------------------------------------------------------------------------------------------------------------------
def test_the_switch_is_registered_documented_and_on_by_default():
    assert DETECTION_FIXES.index("secret-config-lookalikes") > DETECTION_FIXES.index("joined-terms")
    assert "secret-config-lookalikes" in DEFAULT_DETECTION_FIXES and fix_on("secret-config-lookalikes")
    assert "secret-config-lookalikes" in SECRET_CONTEXT_FIXES
    assert "secret-config-lookalikes" in settings.__doc__
    configure_detection_fixes(OFF)
    assert not fix_on("secret-config-lookalikes") and fix_on("secret-references")


# ----------------------------------------------------------------------------------------------------------------------
# Cause 1: a template call to a secret manager names a path (and a field); it is not the secret
# ----------------------------------------------------------------------------------------------------------------------
TEMPLATE_CALLS = [
    "{{ vault 'kv/mailer/relay#smtp_password' }}",
    "{{ vault('kv/ledger/db', 'password') }}",
    '{{ secret("kv/search/index", "api_key") }}',
    '{{- vault "kv/search/index" "api_key" -}}',
]


@pytest.mark.parametrize("call", TEMPLATE_CALLS)
def test_a_template_call_to_a_secret_manager_is_not_a_secret(call):
    text = "mailer:\n  smtp_password: \"" + call + "\"\n  retries: 3\n"
    assert _released(text, call)
    configure_detection_fixes(OFF)
    assert _blocked(text, call)  # switch off: the previous behaviour, byte for byte


@pytest.mark.parametrize("line", [
    "password: \"{{ vault 'kv/ledger/db' }}Xk9pL2mQ8\"",  # a literal attached to the call
    "password: \"{{ vault 'hunter22' }}\"",  # no path in the first argument: cannot tell a name from a value
    "password: \"{{ vault 'kv/ledger/db' | default('Xk9pL2mQ8') }}\"",  # a default is a literal value
    "password: \"{{ default 'kv/ledger/db' 'Xk9pL2mQ8' }}\"",  # not a secret manager's function
    "password: \"{{ vault('kv/ledger/db', 'password') }}\"  # old: " + LIVE_KEY,  # a literal key in a trailing comment
])
def test_a_template_call_with_a_literal_beside_it_still_blocks(line):
    text = "db:\n  " + line + "\n"
    value = LIVE_KEY if LIVE_KEY in text else line.split(": ", 1)[1].split("  #")[0].strip('"')
    assert _blocked(text, value)


def test_a_template_call_inside_a_connection_url_is_a_reference():
    line = "dsn: \"postgres://app:{{ vault('kv/app/db', 'password') }}@db.internal/app\""
    assert secret_context.references_only(line, 0, len(line))
    assert not secret_context.references_only(line, 0, len(line), False)  # the previous rule did not know the form


# ----------------------------------------------------------------------------------------------------------------------
# Cause 2: a URI of any non-web scheme, made only of words, under a key that says it is a reference
# ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("line, value", [
    ('  signingKeyRef: "keyhold://kv-stage/ci/builder#signing"', "keyhold://kv-stage/ci/builder#signing"),
    ("db_password_ref = 'keyhold://kv-stage/db/ledger'", "keyhold://kv-stage/db/ledger"),
    ("  tokenReference: orbit://vault-a/edge/syd-01#registration", "orbit://vault-a/edge/syd-01#registration"),
    ('  adminCredentialRef: "orbit://pki-stage/issue/public-edge"', "orbit://pki-stage/issue/public-edge"),
])
def test_a_reference_key_with_a_words_uri_is_not_a_secret(line, value):
    text = "stage:\n" + line + "\n  retries: 3\n"
    assert _released(text, value)
    configure_detection_fixes(OFF)
    assert _blocked(text, value)


@pytest.mark.parametrize("line, value", [
    # (a key that is neither a reference key nor a credential's name; "signing_key" is one since the split G friction fix)
    ('  connection: "keyhold://kv-stage/ci/builder#signing"', "keyhold://kv-stage/ci/builder#signing"),
    ('  tokenRef: "keyhold://svc:Xk9pL2mQ8@kv-stage/db"', "keyhold://svc:Xk9pL2mQ8@kv-stage/db"),  # a user and a password
    ('  tokenRef: "keyhold://kv-stage/db?token=Xk9pL2mQ8"', "keyhold://kv-stage/db?token=Xk9pL2mQ8"),  # a query
    ('  tokenRef: "keyhold://kv-stage/ci/Zk3p9QxL7vR2"', "keyhold://kv-stage/ci/Zk3p9QxL7vR2"),  # a piece that is no word
    ('  tokenRef: "keyhold://kv-stage/ci/builder"  # old: ' + LIVE_KEY, LIVE_KEY),  # a literal key in a trailing comment
    ('  href: "keyhold://kv-stage/ci/builder"', "keyhold://kv-stage/ci/builder"),  # "href" is not a reference key
])
def test_a_uri_that_could_carry_a_value_still_blocks(line, value):
    assert _blocked("stage:\n" + line + "\n", value)


def test_a_web_uri_is_never_a_reference_whatever_the_key_says():
    # (The older secret-url-components fix relabels a URL path segment as an identifier, so this is checked on the rule.)
    line = '  tokenRef: "https://hooks/services/abcdefghijklmnop"'
    assert not secret_context.references_only(line, 0, len(line))
    assert not secret_context.references_only(line.replace("https", "wss"), 0, len(line))
    assert secret_context.references_only(line.replace("https", "keyhold"), 0, len(line))


# ----------------------------------------------------------------------------------------------------------------------
# Cause 3: a path of lower-case words that is a directory or a configuration file
# ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, value", [
    ("# Credentials resolve at start-up from the vault (path prefix: kv/shop/prod/).\nretries: 3\n", "kv/shop/prod/"),
    ("#  checkout-service :: deploy/environments/staging.yaml\nretries: 3\n", "deploy/environments/staging"),
    ("#  checkout-service :: deploy/environments/production.jobs.yaml\nretries: 3\n", "deploy/environments/production"),
    ("# merged over config/base/defaults.overrides.json first\nretries: 3\n", "config/base/defaults"),
])
def test_a_directory_or_a_configuration_file_path_is_not_a_secret(text, value):
    assert _released(text, value)
    configure_detection_fixes(OFF)
    assert _blocked(text, value)


@pytest.mark.parametrize("text, value", [
    ("# run it from deploy/environments/production tonight\n", "deploy/environments/production"),  # neither
    ("password: deploy/environments/production\n", "deploy/environments/production"),  # a value, not a path
    ("# the token is kv/shop/prod/Xk9pL2mQ8vR\n", "kv/shop/prod/Xk9pL2mQ8vR"),  # a last piece that is not a word
    ("# the token is deploy/Zk3p9QxL7vR2/prod/\n", "deploy/Zk3p9QxL7vR2/prod/"),  # a piece that is not a word
    ("# the token is Xk9pL2mQ/kv/shop/prod/\n", "kv/shop/prod/"),  # the span starts inside a longer token
])
def test_a_path_that_is_not_plainly_a_directory_or_a_file_still_blocks(text, value):
    assert _blocked(text, value)


# ----------------------------------------------------------------------------------------------------------------------
# Cause 4: a camel-case identifier of words in the key of an assignment
# ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, value", [
    ("scaling:\n  targetMemoryUtilizationPercentage: 70\n  minReplicas: 2\n", "targetMemoryUtilizationPercentage"),
    ('  "maxUnavailableRolloutPercentage": 25,\n', "maxUnavailableRolloutPercentage"),
    ("  - maxSurgeDuringRolloutPercentage: 30\n", "maxSurgeDuringRolloutPercentage"),
])
def test_a_camel_case_key_name_is_not_a_secret(text, value):
    assert _released(text, value)
    configure_detection_fixes(OFF)
    assert _blocked(text, value)


@pytest.mark.parametrize("text, value", [
    ("password: targetMemoryUtilizationPercentage\n", "targetMemoryUtilizationPercentage"),  # the same shape as a value
    ("note = targetMemoryUtilizationPercentage\n", "targetMemoryUtilizationPercentage"),
    ("  targetMemoryUtilizationPercentage: 70  # old: " + LIVE_KEY + "\n", LIVE_KEY),  # a literal in a comment
    ("API_TOKEN: 5\n", "API_TOKEN"),  # a key alone that is not such an identifier (COR-001)
    (PADDED_KEY + "\n", PADDED_KEY),  # a lone padded base64 key (COR-001)
])
def test_the_same_shape_elsewhere_and_the_cor_001_cases_still_block(text, value):
    assert _blocked(text, value)


# ----------------------------------------------------------------------------------------------------------------------
# The span function and the gold it must never pass (the DD-16 lesson)
# ----------------------------------------------------------------------------------------------------------------------
def test_word_structure_boundaries():
    word_structure = secret_context.word_structure
    path = "  # see deploy/environments/staging.yaml here"
    start = path.index("deploy")
    assert word_structure(path, start, start + len("deploy/environments/staging"))
    assert word_structure(path, start, start + len("deploy/environments/staging.yaml"))
    assert not word_structure(path, start + 1, start + len("deploy/environments/staging"))  # starts inside a token
    assert not word_structure(path, start, start)  # empty
    assert not word_structure("# see Deploy/Environments/Staging.yaml here", 6, 6 + len("Deploy/Environments/Staging"))
    key = "  targetMemoryUtilizationPercentage: 70"
    assert word_structure(key, 2, 2 + len("targetMemoryUtilizationPercentage"))
    assert not word_structure(key, 2, 2 + len("targetMemory"))  # part of the key
    assert not word_structure(key, 2, len(key))  # key and value together
    assert not word_structure("  targetMemory: 70", 2, 2 + len("targetMemory"))  # two words: too short to tell
    assert not word_structure("  Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MDEy: 1", 2, 2 + 32)  # base64


def test_no_gold_secret_in_the_evaluation_sets_passes_the_new_rules():
    # Checked offline before the battery: every gold SECRET in eval/cases (split F's included) keeps its label under both
    # the span-level and the line-level forms of this switch.
    cases = Path(__file__).resolve().parents[1] / "eval" / "cases"
    passed, checked = [], 0
    for path in sorted(cases.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            for span in case["spans"]:
                if span["label"] == "SECRET":
                    checked += 1
                    start, end = span["start"], span["end"]
                    if secret_context.word_structure(case["text"], start, end) or secret_context.references_only(
                        case["text"], start, end, True
                    ):
                        passed.append((case["id"], case["text"][start:end]))
    assert checked > 100
    assert passed == []


def test_random_base64_is_not_a_word_path_or_a_camel_case_key():
    rng = random.Random(20261112)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    for _ in range(20000):
        token = "".join(rng.choice(alphabet) for _ in range(rng.randint(30, 44)))
        assert not secret_context.word_structure("# " + token, 2, 2 + len(token))
        assert not secret_context.word_structure(token + ": 1", 0, len(token))

# ----------------------------------------------------------------------------------------------------------------------
# Split G friction (development data): secret-manager references in clean configuration files. Every value below is
# invented. Three more reference forms, and the fragments of a reference that the finders return as SECRET.
# ----------------------------------------------------------------------------------------------------------------------
HIGH_ENTROPY_PATH = "kv/prod/9fQ2xL7vT0aB3mZ8"  # a path with a segment that is no word


def _refs(line, start=0, end=None, lookalikes=True):
    return secret_context.references_only(line, start, len(line) if end is None else end, lookalikes)


@pytest.mark.parametrize("line, value", [
    # ${NAME:path}: an upper-case name, a colon, a path of lower-case words
    ('  db_password: "${CRED_STORE:billing/prod/ledger/db-password}"', "${CRED_STORE:billing/prod/ledger/db-password}"),
    ("  gateway_token: ${KEYSAFE:orders/stage/gateway/token}", "${KEYSAFE:orders/stage/gateway/token}"),
    ("  api_key: '${APP_VAULT_2:stage/ops/webhook-key-01}'", "${APP_VAULT_2:stage/ops/webhook-key-01}"),
    # {{ vault:path#field }}: the template call written with a colon, quoted or not
    ('  api_token: "{{ vault:kv/billing#gateway-token }}"', "{{ vault:kv/billing#gateway-token }}"),
    ("  api_token: {{ vault:kv/billing#gateway-token }}", "{{ vault:kv/billing#gateway-token }}"),
    ("  webhook_secret: {{- ssm:ops/alerts/pager#signing_secret -}}", "{{- ssm:ops/alerts/pager#signing_secret -}}"),
    ('  db_password: "{{secret:kv/billing/ledger}}"', "{{secret:kv/billing/ledger}}"),
    # a URI of words under a key whose last word names a credential, not only under "...Ref"
    ('  cache_password: "sm://platform/stage/cache/admin-password"', "sm://platform/stage/cache/admin-password"),
    ("  ledger_db_password: ksv://billing/database/replica-password", "ksv://billing/database/replica-password"),
    ('  signing_key: "keyhold://kv-stage/ci/builder#signing"', "keyhold://kv-stage/ci/builder#signing"),
    ("  s3SecretKey: vlt://backups/stage/object-store/secret-key", "vlt://backups/stage/object-store/secret-key"),
    ('  "api_token": "sm://platform/stage/cache/api-token",', "sm://platform/stage/cache/api-token"),
])
def test_a_secret_manager_reference_in_a_configuration_file_is_not_a_secret(line, value):
    text = "stage:\n" + line + "\n  retries: 3\n"
    assert _released(text, value)
    configure_detection_fixes(OFF)
    assert _blocked(text, value)  # switch off: the previous behaviour


@pytest.mark.parametrize("line", [
    '  db_password: "${CRED_STORE:billing/prod/ledger/db-password}"',
    "  api_token: {{ vault:kv/billing#gateway-token }}",
    "  cache_password: sm://platform/stage/cache/admin-password",
])
def test_the_new_reference_forms_need_the_switch(line):
    assert _refs(line) and not _refs(line, lookalikes=False)


# The fragments the finders cut out of a reference, and the key of the assignment with its separator.
FRAGMENT_LINES = [
    ('  db_password: "${CRED_STORE:billing/prod/ledger/db-password}"', ['billing/prod/ledger/db-password}"',
                                                                      ":billing/prod", "CRED_STORE:billing/prod"]),
    ("  api_token: {{ vault:kv/billing#gateway-token }}", ["vault:kv/billing#gateway-token", "kv/billing#gateway",
                                                         "vault:kv/billing#gateway-token }}"]),
    ('  cache_password: "sm://platform/stage/cache/admin-password"', ["platform/stage/cache", "//platform/stage"]),
]


@pytest.mark.parametrize("line, fragments", FRAGMENT_LINES)
def test_a_fragment_wholly_inside_a_reference_is_not_a_secret(line, fragments):
    for fragment in fragments:
        start = line.index(fragment)
        assert _refs(line, start, start + len(fragment)), fragment
        assert not _refs(line, start, start + len(fragment), lookalikes=False), fragment
        text = "stage:\n" + line + "\n  retries: 3\n"
        assert _released(text, fragment)
        configure_detection_fixes(OFF)
        assert _blocked(text, fragment)
        configure_detection_fixes(None)  # back to the defaults for the next fragment


@pytest.mark.parametrize("line, key, separator", [
    ("  ledger_api_key: ksv://billing/integration/api-key", "ledger_api_key", ":"),
    ('  db_password: "${CRED_STORE:billing/prod/ledger/db-password}"', "db_password", ":"),
    ("  api_token: {{ vault:kv/billing#gateway-token }}", "api_token", ":"),
    ('  "api_token": "sm://platform/stage/cache/api-token",', 'api_token"', ":"),
    ("  ledger_api_key=ksv://billing/integration/api-key", "ledger_api_key", "="),
    ("  DEPLOY_TOKEN: ${{ secrets.DEPLOY_TOKEN }}", "DEPLOY_TOKEN", ":"),  # an older form: the key was already free
])
def test_the_key_of_a_reference_only_assignment_is_not_a_secret_with_or_without_its_separator(line, key, separator):
    start = line.index(key)
    with_separator = start + len(key) + len(separator)
    assert line[with_separator - 1] == separator
    assert _refs(line, start, start + len(key))
    assert _refs(line, start, with_separator)
    if line.lstrip().startswith('"'):
        assert _refs(line, start - 1, with_separator)  # the key's opening quote belongs to the key
    assert not _refs(line, start, with_separator, lookalikes=False)  # the switch gates it


@pytest.mark.parametrize("line", [
    "  ledger_api_key: ksv://billing/integration/api-key",
    '  db_password: "${CRED_STORE:billing/prod/ledger/db-password}"',
])
def test_the_key_with_its_colon_is_released_end_to_end_and_a_literal_beside_it_still_blocks(line):
    key = line.split(":")[0].strip() + ":"
    literal = "Xk9pL2mQ8vR4"
    text = "stage:\n" + line + "\n  smtp_secret: " + literal + "\n  retries: 3\n"
    result = _detect_secret_names(text, [key, literal])
    kept = [text[f.start:f.end] for f in result.findings if Label.SECRET in f.labels]
    assert result.decision == Decision.BLOCK and ReasonCode.SECRET in result.reasons
    assert literal in kept and not any(key in value for value in kept)
    only = _detect_secret_names("stage:\n" + line + "\n  retries: 3\n", [key])
    assert only.decision == Decision.RELEASE and not any(Label.SECRET in f.labels for f in only.findings)


LITERAL = "Xk9pL2mQ8vR4"  # synthetic literal password


@pytest.mark.parametrize("line, value", [
    # a literal value after the same keys
    ('  db_password: "' + LITERAL + '"', LITERAL),
    ("  cache_password: " + LITERAL, LITERAL),
    ('  api_key: "' + LITERAL + '"', LITERAL),
    ("  gateway_token = '" + LITERAL + "'", LITERAL),
    ("  signing_key: " + LIVE_KEY, LIVE_KEY),
    # a URI that carries a user and a password, or a piece that is no word
    ('  cache_password: "sm://svc:' + LITERAL + '@platform/stage/cache"', "sm://svc:" + LITERAL + "@platform/stage/cache"),
    ('  cache_password: "sm://platform/stage/cache?token=' + LITERAL + '"', "sm://platform/stage/cache?token=" + LITERAL),
    ('  cache_password: "sm://org/' + HIGH_ENTROPY_PATH + '"', "sm://org/" + HIGH_ENTROPY_PATH),
    ('  db_password: "${CRED_STORE:' + HIGH_ENTROPY_PATH + '}"', "${CRED_STORE:" + HIGH_ENTROPY_PATH + "}"),
    ('  api_token: "{{ vault:' + HIGH_ENTROPY_PATH + ' }}"', "{{ vault:" + HIGH_ENTROPY_PATH + " }}"),
    ('  api_token: "{{ vault:kv/prod#9fQ2xL7vT0aB3mZ8 }}"', "{{ vault:kv/prod#9fQ2xL7vT0aB3mZ8 }}"),
    # a reference with a literal beside it on the same line
    ('  db_password: "${CRED_STORE:billing/prod/ledger/db-password}' + LITERAL + '"', LITERAL),
    ("  db_password: ${CRED_STORE:billing/prod/ledger/db-password} " + LITERAL, LITERAL),
    ('  db_password: "${CRED_STORE:billing/prod/ledger/db-password}"  # was ' + LIVE_KEY, LIVE_KEY),
    ('  api_token: "{{ vault:kv/billing#gateway-token }}' + LITERAL + '"', LITERAL),
    ('  api_token: {{ vault:kv/billing#gateway-token }}  # was ' + LIVE_KEY, LIVE_KEY),
    ('  cache_password: "sm://platform/stage/cache/admin-password"  # was ' + LIVE_KEY, LIVE_KEY),
])
def test_a_literal_or_a_value_shaped_uri_after_the_same_keys_still_blocks(line, value):
    assert _blocked("stage:\n" + line + "\n  retries: 3\n", value)


@pytest.mark.parametrize("line", [
    '  password: "${DB_PASSWORD:hunter22}"',  # one word after the colon is Spring's default value, a literal
    '  password: "${DB_PASSWORD:admin/admin}"',  # a two-word default is a literal too: a path has three words
    '  password: "${DB_PASSWORD:-billing/prod/ledger}"',  # the shell's default
    '  password: "${CRED_STORE:Billing/Prod/Ledger}"',  # not lower-case words
    '  password: "${CRED_STORE:billing/prod/ledger?version=' + LITERAL + '}"',  # a query
    '  password: "${cred_store:billing/prod/ledger}"',  # not an upper-case name
    '  password: "${CRED_STORE:billing/prod/ledger/db-password}${CRED_STORE:billing/prod/ledger}"',  # two on one line
    '  password: "{{ vault:hunter22 }}"',  # no path
    '  password: "{{ vault:kv/billing#password | default(\'' + LITERAL + '\') }}"',  # a default is a literal
    '  password: "{{ user:kv/billing#password }}"',  # not a secret manager's function
    '  password: "{{ vault:kv/billing#password }}' + LITERAL + '"',  # a literal attached to the call
    '  password: "{{ vault:kv/billing#password }}{{ vault:kv/billing#other }}"',
    '  password: "{{ vault:kv/billing#password"',  # unterminated
    '  cache_password: "https://platform/stage/cache/admin-password"',  # a web scheme never counts
    '  cache_password: "wss://platform/stage/cache/admin-password"',
    '  cache_password: "FTP://platform/stage/cache/admin-password"',
    '  cache_password: "sm://svc:' + LITERAL + '@platform/stage/cache"',
    '  cache_password: "sm://platform.internal/stage/cache"',  # a host with dots
    '  cache_password: "sm://platform/stage/cache/Admin-Password"',  # not lower case
    '  cache_password: "sm://platform/stage/cache/pass2word"',  # letters run into digits
    '  cache_password: "sm://platform"',  # no path
    '  password_policy: "sm://platform/stage/cache/admin-password"',  # not a credential's name, not a reference key
    '  href: "keyhold://kv-stage/ci/builder"',
])
def test_the_new_reference_forms_are_as_narrow_as_the_old_ones(line):
    assert not _refs(line)


def test_a_reference_form_cannot_cover_a_span_that_leaves_it():
    ref = '  db_password: "${CRED_STORE:billing/prod/ledger/db-password}"'
    assert _refs(ref)
    commented = ref + "  # was " + LIVE_KEY
    assert not _refs(commented)  # through the comment
    assert not _refs(commented, commented.index("ledger"), len(commented))  # a fragment that reaches into it
    assert not _refs(commented, commented.index(LIVE_KEY), len(commented))
    assert _refs(commented, commented.index("ledger"), commented.index("  #"))  # the fragment short of it is not a secret
    assert not _refs("  db_password: " + LITERAL, 2, 2 + len("db_password:"))  # the key of a literal value keeps its label
    assert not _refs("  db_password: " + LITERAL, 2, len("  db_password: ") + len(LITERAL))
    assert not _refs("  db_password:", 2, len("  db_password:"))  # a value-less key (COR-001)
    assert not _refs("  db_password: # was " + LIVE_KEY, 2, len("  db_password:"))
    spaced = "  db_password : ${CRED_STORE:billing/prod/ledger}"
    assert not _refs(spaced, 2, spaced.index(":") + 1)  # the separator after a space is judged as before
    two = "  db_password: ${CRED_STORE:billing/prod/ledger}\n  smtp_password: " + LITERAL
    assert not secret_context.references_only(two, 2, len(two))  # a literal on the next line holds the span over both


def test_a_literal_on_another_line_still_holds_the_document_beside_the_new_forms():
    for ref in (
        '  db_password: "${CRED_STORE:billing/prod/ledger/db-password}"',
        "  api_token: {{ vault:kv/billing#gateway-token }}",
        "  cache_password: sm://platform/stage/cache/admin-password",
    ):
        text = "stage:\n" + ref + "\n  smtp_password: " + LITERAL + "\n  retries: 3\n"
        value = ref.split(": ", 1)[1].strip('"')
        result = _detect_secret_names(text, [value, LITERAL])
        kept = [text[f.start:f.end] for f in result.findings if Label.SECRET in f.labels]
        assert result.decision == Decision.BLOCK and ReasonCode.SECRET in result.reasons
        assert LITERAL in kept and value not in kept


def test_random_tokens_are_not_reference_paths():
    rng = random.Random(20261113)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    for _ in range(4000):
        token = "".join(rng.choice(alphabet) for _ in range(rng.randint(16, 40)))
        assert not _refs("  password: ${CRED_STORE:kv/prod/" + token + "}")
        assert not _refs("  password: {{ vault:kv/prod#" + token + " }}")
        assert not _refs("  password: sm://org/prod/" + token)
