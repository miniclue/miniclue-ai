"""Posthog client utility for OpenAI LLM analytics."""

import logging
from typing import Optional
from posthog import Posthog
from posthog.ai.openai import AsyncOpenAI

from app.utils.config import Settings
from app.utils.model_provider_mapping import Provider

# Initialize settings
settings = Settings()

# Global Posthog client instance
_posthog_client: Optional[Posthog] = None


def get_posthog_client() -> Optional[Posthog]:
    """Get or initialize the Posthog client."""
    global _posthog_client

    if _posthog_client is None:
        if not settings.posthog_api_key:
            logging.warning(
                "Posthog API key not configured. Posthog logging will be disabled."
            )
            return None

        try:
            _posthog_client = Posthog(
                project_api_key=settings.posthog_api_key,
                host=settings.posthog_api_url,
            )
        except Exception as e:
            logging.error(f"Failed to initialize Posthog client: {e}")
            return None

    return _posthog_client


def get_base_url_for_provider(provider: Provider) -> str:
    """
    Get the base URL for a given provider.

    Args:
        provider: The provider name

    Returns:
        The base URL for the provider
    """
    provider_base_urls: dict[Provider, str] = {
        "openai": settings.openai_api_base_url,
        "gemini": settings.gemini_api_base_url,
        "anthropic": settings.anthropic_api_base_url,
        "xai": settings.xai_api_base_url,
        "deepseek": settings.deepseek_api_base_url,
    }
    return provider_base_urls.get(provider, settings.openai_api_base_url)


def get_openai_client(api_key: str, base_url: str | None = None) -> AsyncOpenAI:
    """
    Get an OpenAI client wrapped with Posthog for automatic LLM analytics.

    Args:
        api_key: API key for the provider
        base_url: Optional base URL. If not provided, uses OpenAI base URL from settings.

    Returns:
        AsyncOpenAI client with Posthog integration
    """
    posthog_client = get_posthog_client()

    if base_url is None:
        base_url = settings.openai_api_base_url

    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        posthog_client=posthog_client,  # Optional: if None, Posthog will use default client
    )


def shutdown_posthog() -> None:
    """Shutdown the Posthog client."""
    global _posthog_client
    if _posthog_client:
        try:
            _posthog_client.shutdown()
        except Exception as e:
            logging.error(f"Error shutting down Posthog client: {e}")
        finally:
            _posthog_client = None
