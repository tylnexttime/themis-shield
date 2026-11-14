"""Review FR-01 (14/11/2026): split G's secret-manager reference forms are the opt-in switch secret-config-references.

Under the default switches every line below is BLOCKed for ``secret``, as 0.4.0 BLOCKs it: the reviewer's twelve
constructed inputs (a random lower-case token dressed as a URI, a ``${NAME:a/b/c}`` lookup or a ``{{ secret: }}`` call)
and the three DD-27 inputs (a literal made of words in the same shapes). The last test records what the opt-in switch
still releases. The PII-Tracer stand-in is silent; the finder stand-in reports the value given, or nothing ("rules
alone"). Every value is invented.
"""

from __future__ import annotations

import pytest

from themis_shield.config import DeploymentProfile, UseCasePolicy
from themis_shield.detectors import DetectionTargets, detect_batch
from themis_shield.detectors.contracts import StageReport, StageResult, Status
from themis_shield.detectors.settings import (
    DEFAULT_DETECTION_FIXES, OPT_IN_DETECTION_FIXES, configure_detection_fixes, fix_on,
)
from themis_shield.types import Decision, ReasonCode

SWITCH = "secret-config-references"


class _SilentTracer:
    def detect(self, text):
        return StageResult((), StageReport("pii-tracer", Status.OK))


class _Finder:
    def __init__(self, value):
        self.value = value

    def chat_json(self, model_role, messages, schema, max_tokens):
        content = "\n".join(m["content"] for m in messages)
        found = [{"label": "SECRET", "value": self.value}] if self.value and self.value in content else []
        return {"entities": found}


def _gate(text, value):
    policy = UseCasePolicy("s", ("cloud",), approved_for_cloud=True, requires_in_region_inference=False)
    profile = DeploymentProfile("s", "1", (), (policy,))
    targets = DetectionTargets(tracer=_SilentTracer(), llm_client=_Finder(value))
    return detect_batch([text], profile, targets, thorough=True)[0].policy_result


# The reviewer's twelve (review-fable-final FR-01): a random lower-case token after a hyphen in a reference shape.
# ``None``: no finder entity, the gate's own rules alone.
RANDOM_TOKENS = [
    ('service:\n  api_key: "acme://prod/key-9f3k2zq7x1b8m4"\n', "acme://prod/key-9f3k2zq7x1b8m4"),
    ('db_password: "pg://main/db-a8f3k2z9q7x1m4w6"\n', "pg://main/db-a8f3k2z9q7x1m4w6"),
    ('db_password: "pg://main/db-a8f3k2z9q7x1m4w6"\n', None),
    ('password: "${DB_PASSWORD:a/b/c-9f3k2zq7x1b8m4}"\n', "${DB_PASSWORD:a/b/c-9f3k2zq7x1b8m4}"),
    ('password: "${DB_PASSWORD:a/b/c-9f3k2zq7x1b8m4}"\n', None),
    ('token: "{{ secret:live/sk-9f3k2zq7x1b8m4w6 }}"\n', "{{ secret:live/sk-9f3k2zq7x1b8m4w6 }}"),
    ('token: {{ secret:live/sk-9f3k2zq7x1b8m4w6 }}\n', "sk-9f3k2zq7x1b8m4w6"),
    ('token: {{ secret:live/sk-9f3k2zq7x1b8m4w6 }}\n', None),
    ('STRIPE_SECRET_KEY=stripe://live/sk-51h8f3k2z9q7x1m4w6\n', "stripe://live/sk-51h8f3k2z9q7x1m4w6"),
    ('STRIPE_SECRET_KEY=stripe://live/sk-51h8f3k2z9q7x1m4w6\n', None),
    ('api_token: "ghp://org/repo-9f3k2zq7x1b8m4w6a8f3k2z9q7x1m4w6aaaa"\n', None),
    ('api_token: "ghp://org/repo-9f3k2zq7x1b8m4w6a8f3k2z9q7x1m4w6aaaa"\n',
     "ghp://org/repo-9f3k2zq7x1b8m4w6a8f3k2z9q7x1m4w6aaaa"),
]
# DD-27: a literal made of words, in the same shapes.
WORD_LITERALS = [
    ('spring:\n  datasource:\n    password: "${DB_PASSWORD:correct/horse/battery}"\n', "${DB_PASSWORD:correct/horse/battery}"),
    ('service:\n  admin_password: "pw://winter/sunrise-42"\n', "pw://winter/sunrise-42"),
    ('broker_password: "sm://org/prod/broker-password"  # was: Tr0ub4dor&3xyz\n', "sm://org/prod/broker-password"),
]


def test_the_switch_is_opt_in_and_off_by_default():
    assert SWITCH in OPT_IN_DETECTION_FIXES and SWITCH not in DEFAULT_DETECTION_FIXES and not fix_on(SWITCH)
    assert fix_on("secret-config-lookalikes")  # 0.4.0's four look-alikes stay on


@pytest.mark.parametrize("text, value", RANDOM_TOKENS + WORD_LITERALS)
def test_every_fr01_and_dd27_input_blocks_under_the_defaults_as_in_040(text, value):
    result = _gate(text, value)
    assert result.decision == Decision.BLOCK and ReasonCode.SECRET in result.reasons


@pytest.mark.parametrize("text, value", RANDOM_TOKENS)
def test_with_the_opt_in_switch_a_random_token_still_blocks(text, value):
    configure_detection_fixes(list(DEFAULT_DETECTION_FIXES) + [SWITCH])
    result = _gate(text, value)
    assert result.decision == Decision.BLOCK and ReasonCode.SECRET in result.reasons


@pytest.mark.parametrize("text, value", WORD_LITERALS)
def test_what_the_opt_in_switch_still_releases(text, value):
    # On record, not endorsed: with secret-config-references on, a literal made of lower-case words in a reference
    # shape is released in clear (DD-27). This is why the switch is opt-in.
    configure_detection_fixes(list(DEFAULT_DETECTION_FIXES) + [SWITCH])
    assert _gate(text, value).decision == Decision.RELEASE
