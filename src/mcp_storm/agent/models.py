"""Model factory + prereg constants (BUILD-GUIDE §3.6, §5.1).

Preregistered matrix models: openai:gpt-5-nano (cheap) + openai:gpt-5-mini
(mid). OpenRouter value-flash arms (owner request, 2026-09-18) are
EXPLORATORY: dose-0 + malformed slices only, never scored against prereg
claims (declared in predictions.json).

reasoning_effort is a prereg constant for OpenAI models (completion tokens
include reasoning). Verified live at the model smoke (2026-09-18).
"""

from __future__ import annotations

import os

from langchain_core.language_models import BaseChatModel

from mcp_storm.envtools import load_dotenv

# Prereg constant -- frozen in prereg/PREREGISTRATION.md at the lock.
REASONING_EFFORT = "low"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

MODEL_REGISTRY: dict[str, dict] = {
    "openai:gpt-5-nano": {"provider": "openai", "model_id": "gpt-5-nano", "role": "prereg"},
    "openai:gpt-5-mini": {"provider": "openai", "model_id": "gpt-5-mini", "role": "prereg"},
    # exploratory value-flash arms (OpenRouter), added before the lock
    "or:deepseek-v4-flash": {
        "provider": "openrouter", "model_id": "deepseek/deepseek-v4-flash",
        "role": "exploratory"},
    "or:glm-5.3-flash": {
        "provider": "openrouter", "model_id": "z-ai/glm-5.3-flash",
        "role": "exploratory"},
}

MODEL_NAMES = tuple(MODEL_REGISTRY)
PREREG_MODELS = tuple(k for k, v in MODEL_REGISTRY.items() if v["role"] == "prereg")


def get_model(name: str) -> BaseChatModel:
    load_dotenv()
    if name not in MODEL_REGISTRY:
        raise ValueError(f"unknown model {name!r}; expected one of {MODEL_NAMES}")
    entry = MODEL_REGISTRY[name]
    if entry["provider"] == "openai":
        from langchain.chat_models import init_chat_model

        model = init_chat_model(f"openai:{entry['model_id']}")
        try:
            return model.bind(reasoning_effort=REASONING_EFFORT)
        except (TypeError, ValueError):
            return model
    if entry["provider"] == "openrouter":
        from langchain_openai import ChatOpenAI

        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set (.env or environment)")
        return ChatOpenAI(
            model=entry["model_id"],
            base_url=OPENROUTER_BASE_URL,
            api_key=api_key,
            temperature=0,
        )
    raise ValueError(f"unknown provider {entry['provider']!r}")
