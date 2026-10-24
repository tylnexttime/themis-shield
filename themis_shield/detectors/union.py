"""Additive evidence union; never resolve overlaps by voting or priority.

"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Iterable

from .contracts import DetectionFinding, FindingEvidence


def union_findings(
    *groups: Iterable[DetectionFinding],
) -> tuple[DetectionFinding, ...]:
    """Coalesce identical intervals only, preserving every label and stage.

    Different overlapping intervals remain separate. M1 must substitute their
    geometric coverage without discarding any attached label or provenance.
    """
    by_interval = defaultdict(list)
    for group in groups:
        for item in group:
            by_interval[(item.start, item.end)].append(item)
    out = []
    for interval in sorted(by_interval):
        items = by_interval[interval]
        labels = frozenset(label for item in items for label in item.labels)
        scores: dict[tuple, FindingEvidence] = {}
        for item in items:
            for evidence in item.evidence:
                key = (evidence.label, evidence.stage, evidence.tier)
                previous = scores.get(key)
                if previous is None or (
                    evidence.score is not None
                    and (previous.score is None or evidence.score > previous.score)
                ):
                    scores[key] = evidence
        evidence = tuple(scores[key] for key in sorted(scores))
        primary = min(labels, key=lambda label: label.value)
        out.append(replace(
            items[0],
            labels=labels,
            primary_label=primary,
            detector_id=evidence[0].stage,
            checksum_valid=(
                True if any(item.checksum_valid is True for item in items)
                else items[0].checksum_valid
            ),
            evidence=evidence,
        ))
    return tuple(out)
