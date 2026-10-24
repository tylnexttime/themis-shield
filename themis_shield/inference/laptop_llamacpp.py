"""Compatibility client for an independently managed loopback llama-server.

This does not launch, unload or switch an external server. Use LlamaCppTarget
and Scheduler for owned, resource-aware execution. Original names and model
profile constants are retained for existing imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .openai_local import OpenAILocalTarget

LLAMA_HOST = "127.0.0.1"
DEFAULT_LLAMA_PORT = 8080
REFERENCE_GPU_LAYERS = 99
MEASURED_26B_CONTEXT_TOKENS = 16384
MEASURED_26B_CPU_MOE_EXPERTS = 18


@dataclass(frozen=True, slots=True)
class ModelProfile:
    model_id: str
    quantisation: str
    context_tokens: int | None
    cpu_moe_experts: int | None
    gpu_layers: int = REFERENCE_GPU_LAYERS


MODEL_PROFILES: Mapping[str, ModelProfile] = MappingProxyType({
    "gemma4-e4b": ModelProfile("gemma4-e4b", "Q4_K_M", 16384, None),
    "gemma4-26b-a4b": ModelProfile(
        "gemma4-26b-a4b", "UD-IQ3_XXS", 16384, 18,
    ),
})


class LaptopLlamaCppTarget(OpenAILocalTarget):
    def __init__(
        self,
        port: int = DEFAULT_LLAMA_PORT,
        timeout_seconds: float = 120,
        maximum_response_bytes: int = 4 * 1024 * 1024,
        name: str = "laptop",
    ) -> None:
        if name != "laptop":
            raise ValueError("invalid laptop target name")
        self.port = port
        super().__init__(
            f"http://127.0.0.1:{port}", "local",
            name=name, timeout_seconds=timeout_seconds,
            maximum_response_bytes=maximum_response_bytes,
        )
