"""Model to provider mapping utility."""

from typing import Literal

Provider = Literal["openai", "gemini", "anthropic", "xai", "deepseek"]

# Mapping from model ID to provider
# This mapping should be kept in sync with the curatedModelCatalog in the Go backend
MODEL_TO_PROVIDER_MAP: dict[str, Provider] = {
    # OpenAI models
    "gpt-5.2": "openai",
    "gpt-5.1": "openai",
    "gpt-5.1-chat-latest": "openai",
    "gpt-5": "openai",
    "gpt-5-chat-latest": "openai",
    "gpt-5-mini": "openai",
    "gpt-5-nano": "openai",
    "gpt-4.1": "openai",
    "gpt-4.1-mini": "openai",
    "gpt-4.1-nano": "openai",
    "gpt-4o": "openai",
    "gpt-4o-mini": "openai",
    # Gemini models
    "gemini-3-flash-preview": "gemini",
    "gemini-3-pro-preview": "gemini",
    "gemini-2.5-pro": "gemini",
    "gemini-2.5-flash": "gemini",
    "gemini-2.5-flash-lite": "gemini",
    "gemini-embedding-001": "gemini",
    # Anthropic models
    "claude-sonnet-4-5": "anthropic",
    "claude-haiku-4-5": "anthropic",
    # xAI models
    "grok-4-1-fast-reasoning": "xai",
    "grok-4-1-fast-non-reasoning": "xai",
    # DeepSeek models
    "deepseek-chat": "deepseek",
    "deepseek-reasoner": "deepseek",
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
