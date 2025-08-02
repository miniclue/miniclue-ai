from posthog import Posthog
from posthog.ai.openai import AsyncOpenAI
from app.utils.config import Settings

# Initialize settings
settings = Settings()

# Initialize PostHog client
posthog = Posthog(settings.posthog_api_key, host=settings.posthog_host)

# Initialize PostHog OpenAI client
posthog_openai_client = AsyncOpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_api_base_url,
    posthog_client=posthog,
)

posthog_gemini_client = AsyncOpenAI(
    api_key=settings.gemini_api_key,
    base_url=settings.gemini_api_base_url,
    posthog_client=posthog,
)
