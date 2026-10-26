"""Synthetic fixtures for the v0.3 catalogue tests. No real model, register service or cloud.

Builds on router_fakes: the real SQLite register and router, fake detectors and a fake cloud endpoint that can also
play the trip-wire (it answers the trip-wire's fixed instruction with a configured reply).
"""

from __future__ import annotations

from datetime import timedelta
import re

import httpx

from themis_shield.catalogues.false_positives import FalsePositiveCatalogue
from themis_shield.catalogues.gate import FalsePositiveGate, KnownMissSweep
from themis_shield.catalogues.known_miss import KnownMissRegister
from themis_shield.catalogues.tripwire import INSTRUCTION, Tripwire
from themis_shield.cloud import ApiKeyCredential, CloudClient, CloudSettings
from themis_shield.cloud.openai_compat import canonical, strict_json
from themis_shield.detectors.contracts import StageReport, StageResult, Status, finding
from themis_shield.protectors import PassphraseProtector

from router_fakes import NOW, FakeCloud, harness


PASSPHRASE = "synthetic-passphrase-not-real-01"
# A high-entropy synthetic reference no detector in the fakes finds (the miss the register learns).
MISSED = "QX7Z-K9PW-4M2R-T8VB"


class PhraseTracer:
    """A PII-Tracer stand-in that tags fixed synthetic phrases as PERSON with a confident score."""

    def __init__(self, phrases=(), label="PERSON", score=0.97):
        self.phrases, self.label, self.score = tuple(phrases), label, score

    def detect(self, text):
        found = tuple(
            finding(match.start(), match.end(), self.label, "pii-tracer", 1, self.score)
            for phrase in self.phrases for match in re.finditer(re.escape(phrase), text)
        )
        return StageResult(found, StageReport(
            "pii-tracer", Status.OK, finding_count=len(found),
            person_count=len(found) if self.label == "PERSON" else 0,
        ))


class TripwireCloud(FakeCloud):
    """The usual fake answer for task turns; ``reply`` (raw content) for the trip-wire's fixed instruction."""

    def __init__(self, reply='{"residual": [], "stop": false}', **kwargs):
        super().__init__(**kwargs)
        self.reply = reply
        self.tripwire_requests = []

    def __call__(self, request):
        body = strict_json(request.content)
        first = body["messages"][0]
        if first.get("role") == "system" and first.get("content") == INSTRUCTION:
            self.requests.append(body)
            self.tripwire_requests.append(body)
            reply = self.reply(body) if callable(self.reply) else self.reply
            events = [
                {"choices": [{"index": 0, "delta": {"content": reply}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            raw = b"".join(b"data: " + canonical(event) + b"\n\n" for event in events) + b"data: [DONE]\n\n"
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=raw)
        return super().__call__(request)


def with_cloud(h, tmp_path, fake):
    """Swap the harness cloud for one served by ``fake`` (same settings)."""
    h.cloud.close()
    h.cloud = CloudClient(
        CloudSettings(
            base_url="https://example.invalid/v1", model="example-cloud-model",
            api_key_env="SYNTHETIC_CLOUD_KEY", capture_dir=tmp_path / "captures",
        ),
        credential=ApiKeyCredential(
            "SYNTHETIC_CLOUD_KEY", environ={"SYNTHETIC_CLOUD_KEY": "SYNTHETIC_BEARER_NOT_REAL"},
        ),
        transport=httpx.MockTransport(fake),
    )
    h.router.cloud = h.cloud
    h.fake = fake
    return h


def protector():
    return PassphraseProtector(PASSPHRASE)


def known_miss(tmp_path, *, clock=lambda: NOW, with_protector=True):
    return KnownMissRegister(
        tmp_path / "known-miss.db", protector=protector() if with_protector else None,
        clock=clock, create=True,
    )


def sweeping_harness(tmp_path, register=None, *, max_age_days=7, tracer=None):
    h = harness(tmp_path)
    if tracer is not None:
        h.router.targets = _replace_tracer(h.router.targets, tracer)
    register = register if register is not None else known_miss(tmp_path)
    h.router.known_miss = KnownMissSweep(register, timedelta(days=max_age_days))
    h.kmr = register
    return h


def _replace_tracer(targets, tracer):
    from dataclasses import replace
    return replace(targets, tracer=tracer)


def catalogue_harness(tmp_path, *, phrases=("Harbour Committee",), scope="default"):
    h = harness(tmp_path)
    h.router.targets = _replace_tracer(h.router.targets, PhraseTracer(phrases))
    h.fpc = FalsePositiveCatalogue(tmp_path / "false-positives.db", clock=lambda: NOW, create=True)
    h.router.fp_catalogue = FalsePositiveGate(h.fpc, scope)
    return h


def tripwire_harness(tmp_path, reply='{"residual": [], "stop": false}', **harness_kwargs):
    h = harness(tmp_path, **harness_kwargs)
    with_cloud(h, tmp_path, TripwireCloud(reply))
    h.router.tripwire = Tripwire(512)
    return h


def plain_harness(tmp_path, *, phrases=()):
    h = harness(tmp_path)
    if phrases:
        h.router.targets = _replace_tracer(h.router.targets, PhraseTracer(phrases))
    return h
