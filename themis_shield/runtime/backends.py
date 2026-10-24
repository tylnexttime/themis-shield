"""Content-free status of the optional back ends, for ``themis runtime status``.

- Strata: per configured endpoint, whether it is reachable, a Strata server,
  loaded and serving the configured model; whether it is attached (and so
  counted as holding GPU memory); and a few engine figures from ``/metrics``.
- Marian: whether the planner is installed, whether profiles were found, and
  for each planned entry the decision a load would use right now.
- Alternative engines: whether each entry's ``engine_path`` exists.

No prompts, outputs, keys or file paths are reported. The Strata endpoint is
a loopback address and is shown.
"""

from __future__ import annotations

import os
from typing import Any

from ..inference import RuntimeFailure
from .catalogue import Catalogue
from .occupancy import strata_holders
from .planning import LaunchPlanner


def backend_status(settings: Any, catalogue: Catalogue) -> dict[str, Any]:
    strata: list[dict[str, Any]] = []
    for holder in strata_holders(catalogue, api_key_env=settings.strata_api_key_env):
        target = holder.target
        facts = target.probe()
        attached = bool(facts["service_ok"] and facts["loaded"])
        strata.append({
            "entries": holder.label.split("+"),
            "endpoint": target.base_url + "/v1",
            "served_model": target.config.model_id,
            **facts,
            "attached": attached,
            "vram_counted_gb": holder.reserve_gb if attached else 0.0,
            "vram_reservation_gb": holder.reserve_gb,
            "api_key_env": target.config.api_key_env,
            "api_key_set": bool(
                target.config.api_key_env and os.environ.get(target.config.api_key_env)
            ),
            "metrics": target.metrics() if facts["service_ok"] else {},
        })
    unconfigured = [
        entry.id for entry in catalogue
        if entry.runtime == "strata" and not entry.endpoint
    ]
    planned = [entry for entry in catalogue if entry.planner == "marian"]
    planner = settings.marian_planner()
    marian: dict[str, Any] = {**planner.status(), "entries": []}
    if planned:
        launch = LaunchPlanner(planner, settings.models_dir)
        for entry in planned:
            try:
                _, decision = launch(entry)
                marian["entries"].append({"id": entry.id, "need_ctx": entry.ctx, **decision.record()})
            except (RuntimeFailure, ValueError, OSError):
                marian["entries"].append({"id": entry.id, "args_source": "catalogue", "reason": "planner-error"})
    engines = []
    for entry in catalogue:
        if entry.engine_path:
            try:
                found = settings.engine_for(entry).is_file()
            except (RuntimeFailure, OSError):
                found = False
            engines.append({
                "id": entry.id, "engine_found": found,
                "engine_args": list(entry.engine_args),
            })
    return {
        "strata": strata,
        "strata_unconfigured": unconfigured,
        "marian": marian,
        "alternative_engines": engines,
    }
