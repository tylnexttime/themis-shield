"""Unavailable on-site DGX Spark boundary.

Raw text would leave the laptop, remaining on site only under a configured,
authorised boundary. Identity, authenticated TLS, access, retention and
system/contract authority remain prerequisites. No endpoint is contacted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import InferenceCapabilities, TargetUnavailable


@dataclass(frozen=True, slots=True)
class DgxSparkConfig:
    endpoint_alias: str | None = None
    authorised: bool = False


class DgxSparkTarget:
    name = "dgx-spark"
    capabilities = InferenceCapabilities(text_chat=False)
    pid = None
    load_ms = 0.0

    def __init__(self, config: DgxSparkConfig | None = None) -> None:
        self.config = config or DgxSparkConfig()
        self.last_timings: dict[str, Any] = {}

    def health(self) -> bool:
        return False

    def start(self) -> None:
        raise TargetUnavailable("DGX Spark unavailable: configure and authorise adapter")

    def stop(self) -> None:
        pass

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        self.start()

    def chat_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.start()
        raise AssertionError("unreachable")


# Original spelling retained.
DGXSparkTarget = DgxSparkTarget
