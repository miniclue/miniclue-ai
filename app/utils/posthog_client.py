from typing import Literal

from posthog import Posthog
from posthog.ai.openai import AsyncOpenAI
from app.utils.config import Settings

# Initialize settings
settings = Settings()

# Initialize PostHog client
posthog = Posthog(settings.posthog_api_key, host=settings.posthog_host)


def create_posthog_client(
    api_key: str, provider: Literal["openai", "gemini"] = "openai"
) -> AsyncOpenAI:
    """
    Creates a PostHog client with a user-specific API key for the specified provider.

    Args:
        api_key: User-specific API key (required).
        provider: The LLM provider, either "openai" or "gemini" (default: "openai").

    Returns:
        AsyncOpenAI client configured with PostHog integration
    """
    if not api_key:
        raise ValueError("API key is required")

    base_url = (
        settings.openai_api_base_url
        if provider == "openai"
        else settings.gemini_api_base_url
    )

    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        posthog_client=posthog,
    )
