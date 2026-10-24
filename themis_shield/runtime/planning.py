"""Launch planning: which arguments a llama.cpp entry is started with.

Entries without a planner use their catalogue ``args`` ("catalogue",
"static"). Entries with ``"planner": "marian"`` ask Marian, from saved
profiles, for the fastest measured configuration at the entry's context.
A usable plan replaces ``args`` and ``ctx`` (and adds allow-listed engine
flags for an alternative engine); anything else falls back to the
catalogue's arguments with the reason recorded.

Memory admission stays conservative: a plan's measured VRAM peak can raise
the entry's estimate, never lower it, and an entry without catalogue memory
estimates is still refused. If the planned configuration does not fit the
memory free right now, the scheduler falls back to the catalogue entry
(reason "plan-does-not-fit-now").
"""

from __future__ import annotations

from dataclasses import replace
import logging
from pathlib import Path

from ..inference.marian import MarianPlanner, PlanDecision
from .catalogue import ModelEntry

LOG = logging.getLogger("themis_shield.runtime")


def merge_engine_args(base: tuple[str, ...], override: tuple[str, ...]) -> tuple[str, ...]:
    """Flag/value pairs of ``base`` with flags in ``override`` replaced."""
    values = dict(zip(base[::2], base[1::2]))
    values.update(zip(override[::2], override[1::2]))
    return tuple(item for pair in values.items() for item in pair)


class LaunchPlanner:
    def __init__(self, planner: MarianPlanner | None, models_dir: Path) -> None:
        self.planner = planner
        self.models_dir = Path(models_dir)

    def __call__(self, entry: ModelEntry) -> tuple[ModelEntry, PlanDecision]:
        if entry.runtime != "llamacpp" or entry.planner != "marian":
            return entry, PlanDecision("catalogue", "static")
        if self.planner is None:
            return entry, PlanDecision("catalogue", "marian-not-installed")
        root = self.models_dir.resolve()
        model = (root / entry.file).resolve()
        if not model.is_relative_to(root) or not model.is_file():
            decision = PlanDecision("catalogue", "model-missing")
        else:
            decision = self.planner.plan(
                model, entry.ctx, allow_extra_args=bool(entry.engine_path),
            )
        if not decision.planned:
            LOG.info("launch args for %s: catalogue (%s)", entry.id, decision.reason)
            return entry, decision
        estimate = entry.est_vram_gb
        if estimate is not None and decision.vram_peak_gb is not None:
            estimate = max(estimate, decision.vram_peak_gb)
        try:
            planned = replace(
                entry,
                args=decision.args,
                ctx=decision.ctx,
                engine_args=merge_engine_args(entry.engine_args, decision.extra_args),
                est_vram_gb=estimate,
                variants=(),
            )
        except ValueError:
            decision = PlanDecision("catalogue", "plan-invalid")
            LOG.info("launch args for %s: catalogue (%s)", entry.id, decision.reason)
            return entry, decision
        LOG.info("launch args for %s: marian plan (ctx %s)", entry.id, decision.ctx)
        return planned, decision
