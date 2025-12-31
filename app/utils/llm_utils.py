"""Utility functions for working with LLM providers."""

import logging
from uuid import UUID
from typing import Dict, Any

from app.utils.config import Settings
from app.utils.secret_manager import (
    get_user_api_key,
    SecretNotFoundError,
    InvalidAPIKeyError,
)
from app.utils.model_provider_mapping import get_provider_for_model, Provider
from app.utils.posthog_client import (
    get_base_url_for_provider,
    get_openai_client,
    get_gemini_client,
)


# Initialize settings
settings = Settings()


def extract_metadata(response: Any) -> Dict[str, Any]:
    """Extracts common metadata from LLM response (OpenAI/Gemini)."""
    model = getattr(response, "model", "")
    response_id = getattr(response, "id", "")

    usage_obj = getattr(response, "usage", None)
    usage = None
    if usage_obj:
        if hasattr(usage_obj, "model_dump"):
            usage = usage_obj.model_dump()
        else:
            usage = {
                "prompt_tokens": getattr(usage_obj, "prompt_tokens", None)
                or getattr(usage_obj, "prompt_token_count", None),
                "completion_tokens": getattr(usage_obj, "completion_tokens", None),
                "total_tokens": getattr(usage_obj, "total_tokens", None)
                or getattr(usage_obj, "total_token_count", None),
            }

    metadata = {
        "model": model,
        "usage": usage,
    }
    if response_id:
        metadata["response_id"] = response_id

    return metadata


def is_authentication_error(error: Exception) -> bool:
    """Checks if the error is related to authentication/invalid API key."""
    error_str = str(error).lower()
    auth_indicators = ["authentication", "unauthorized", "invalid api key", "401"]
    return any(indicator in error_str for indicator in auth_indicators)


def extract_text_from_response(response: Any) -> str:
    """
    Extract text content from an OpenAI Chat Completions API response.
    """
    if (
        not hasattr(response, "choices")
        or not response.choices
        or len(response.choices) == 0
    ):
        return ""

    choice = response.choices[0]
    if not hasattr(choice, "message"):
        return ""

    message = choice.message
    if not hasattr(message, "content") or not message.content:
        return ""

    return message.content


async def get_llm_context(
    user_id: str | UUID, model_id: str, is_embedding: bool = False
) -> tuple[Any, Provider]:
    """
    Fetches API key and creates a client for a given model.
    For embeddings with Gemini, returns a PostHog-wrapped Gemini client.
    Otherwise, returns a PostHog-wrapped OpenAI client.

    Args:
        user_id: The user ID to fetch the key for
        model_id: The model ID to get the client for
        is_embedding: Whether the client is for embedding generation

    Returns:
        A tuple of (client, provider)

    Raises:
        ValueError: If the provider is unknown for the given model
        InvalidAPIKeyError: If the API key is missing or inaccessible
    """
    provider = get_provider_for_model(model_id)
    if provider is None:
        raise ValueError(f"Unknown model provider for: {model_id}")

    try:
        api_key = get_user_api_key(str(user_id), provider=provider)
    except SecretNotFoundError:
        logging.error(f"{provider} API key not found for user {user_id}")
        raise InvalidAPIKeyError(
            f"User {provider} API key not found. Please configure your API key in settings."
        )
    except Exception as e:
        logging.error(f"Failed to fetch {provider} API key for {user_id}: {e}")
        raise InvalidAPIKeyError(f"Failed to access API key: {str(e)}")

    base_url = get_base_url_for_provider(provider)

    if provider == "gemini" and is_embedding:
        client = get_gemini_client(api_key)
    else:
        client = get_openai_client(api_key, base_url=base_url)

    return client, provider
