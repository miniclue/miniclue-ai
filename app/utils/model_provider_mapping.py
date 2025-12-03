"""Model to provider mapping utility."""

from typing import Literal

Provider = Literal["openai", "gemini", "anthropic", "xai", "deepseek"]

# Mapping from model ID to provider
MODEL_TO_PROVIDER_MAP: dict[str, Provider] = {
    "gpt-4o-mini": "openai",
    "gemini-2.5-flash-lite": "gemini",
    "claude-3-5-sonnet": "anthropic",
    "grok-4-1-fast-non-reasoning": "xai",
    "deepseek-chat": "deepseek",
}


def get_provider_for_model(model_id: str) -> Provider | None:
    """
    Get the provider for a given model ID.

    Args:
        model_id: The model ID (e.g., "gpt-4o-mini")

    Returns:
        The provider name or None if model is not found
    """
    return MODEL_TO_PROVIDER_MAP.get(model_id)
