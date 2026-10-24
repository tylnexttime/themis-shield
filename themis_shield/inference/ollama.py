"""Optional real Ollama client on http://127.0.0.1:11434/v1.

No pull/download API. The scheduler requires explicit exclusive-control
configuration because an independently used Ollama daemon can load other
models. This adapter unloads only its selected model, never unrelated models.
Process-private RAM is unavailable for this externally owned daemon.
"""

from __future__ import annotations

import time
from typing import Any

from . import TargetUnavailable
from .openai_local import OpenAILocalTarget


class OllamaTarget(OpenAILocalTarget):
    def __init__(
        self,
        model_id: str,
        *,
        exclusive_control: bool = False,
        keep_alive_seconds: int = 600,
        **options: Any,
    ) -> None:
        if keep_alive_seconds <= 0:
            raise ValueError("invalid Ollama keep-alive")
        self.exclusive_control = exclusive_control
        self.keep_alive_seconds = keep_alive_seconds
        super().__init__(
            "http://127.0.0.1:11434", model_id,
            name="ollama", health_path="/v1/models", **options,
        )

    def start(self) -> None:
        if not self.exclusive_control:
            raise TargetUnavailable("Ollama unavailable: exclusive control not configured")
        if not self.health():
            raise TargetUnavailable("configured Ollama model unavailable")
        began = time.monotonic()
        self._exchange("POST", "/api/generate", {
            "model": self.model_id, "prompt": "", "stream": False,
            "keep_alive": self.keep_alive_seconds,
        })
        self.load_ms = (time.monotonic() - began) * 1000

    def health(self) -> bool:
        try:
            result = self._exchange("GET", "/v1/models")
            return any(
                item.get("id") == self.model_id
                for item in result.get("data", [])
                if isinstance(item, dict)
            )
        except (RuntimeError, AttributeError, TypeError):
            return False

    def stop(self) -> None:
        if self.exclusive_control:
            self._exchange("POST", "/api/generate", {
                "model": self.model_id, "prompt": "", "stream": False,
                "keep_alive": 0,
            })
