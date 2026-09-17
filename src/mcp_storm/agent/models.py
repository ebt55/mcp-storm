"""Model factory + prereg constants (BUILD-GUIDE §3.6, §5.1).

REASONING_EFFORT is a prereg constant: completion tokens include reasoning, so
the effort level must be pinned and declared. Value verified at the model-smoke
step (paid, after the prereg lock); until then it is provisional.
"""

from __future__ import annotations

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

# Prereg constant -- freeze in prereg/PREREGISTRATION.md at the Oct 9 lock.
REASONING_EFFORT = "low"

MODEL_NAMES = ("openai:gpt-5-nano", "openai:gpt-5-mini")


def get_model(name: str) -> BaseChatModel:
    """Init a chat model by name (e.g. 'openai:gpt-5-nano') with the pinned
    reasoning effort. Offline tests pass their own FakeChatModel; this function
    is for paid runs only."""
    if name not in MODEL_NAMES:
        raise ValueError(f"unknown model {name!r}; expected one of {MODEL_NAMES}")
    model = init_chat_model(name)
    try:
        return model.bind(reasoning_effort=REASONING_EFFORT)
    except (TypeError, ValueError):
        # provider rejected the kwarg at bind time -- surface at smoke test
        return model
