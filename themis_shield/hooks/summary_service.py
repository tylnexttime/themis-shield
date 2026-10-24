"""Managed-hooks mode integration with summaries from the finished tokenised prompt.

The existing hook state, substitution, rescan and release controls are inherited.
"""

from dataclasses import replace
import hashlib

from themis_shield.cloud.openai_compat import canonical
from themis_shield.placeholders import SENSITIVE_LABELS
from themis_shield.register.client import JobStatus as S
from themis_shield.register.models import JobIntake, canonical_json
from themis_shield.routing import Route
from themis_shield.summary import problem_summary
from themis_shield.types import Decision, Label

from .service import HookService


class SummarisingHookService(HookService):
    def _job(self, identifier, state, clean, counts, decision, routing, timings):
        package = {
            "schema_version": 1, "event": "UserPromptSubmit", "prompt": clean,
            "destination_id": self.policy.destination_id, "model": self.policy.model,
            "deployment_type": self.policy.deployment_type,
            "policy_version": self.router.profile.profile_version,
        }
        digest = hashlib.sha256(canonical(package)).hexdigest()
        existing = state.jobs.get(digest)
        if existing:
            return self.router._register_call("get_job", existing), digest
        if len(state.jobs) >= 256:
            raise PermissionError("HOOK_JOB_CAPACITY")
        candidate = JobIntake(
            "", self.router.clock(), S.LOGGED, Route.CLOUD,
            marking_class=routing.audit_fields.marking_class,
            destination_id=self.policy.destination_id, model=self.policy.model,
            deployment_type=self.policy.deployment_type,
            source_document_count=1, source_word_count=len(clean.split()),
            requester_identity=self.router.identity, client="CLAUDE_CODE",
            integration_mode="managed-hooks",
            synthetic=True, use_case_record_ref="SYNTHETIC-ENDORSEMENT",
            problem_summary_deid=problem_summary(
                clean, originals=tuple(self._mapping(identifier, state).values()),
            ),
            scope_json=(
                '{"schema_version":1,"release_profile":"claude-prompt-v1",'
                '"tool_profile":"claude-text-v1"}'
            ),
            gate_version="m6a-v1", policy_version=self.router.profile.profile_version,
        )
        job = self.router._register_call("log_job", candidate, now=self.router.clock())
        state.jobs[digest] = job.job_id
        self.router._register_call("request_stage1", job.job_id, now=self.router.clock())
        metadata = replace(
            candidate, job_id=job.job_id, created_utc=job.created_utc,
            manifest_hash=digest, pii_counts_json=canonical_json(counts),
            sensitive_categories_present=any(Label(label) in SENSITIVE_LABELS for label in counts),
            confirmation_required=routing.mandatory_confirm or decision == Decision.ASK,
        )
        self.router._register_call(
            "set_package", metadata,
            timings={"tiers_ms": {str(key): value for key, value in timings.items()}},
            now=self.router.clock(),
        )
        return self.router._register_call("get_job", job.job_id), digest
