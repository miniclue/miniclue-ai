import logging
import uuid
from typing import Optional

from app.utils.config import Settings
from app.utils.posthog_client import posthog_openai_client

# Initialize settings
settings = Settings()


async def generate_summary(
    explanations: list[str],
    lecture_id: str,
    customer_identifier: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> tuple[str, dict]:
    """
    Generates a comprehensive lecture summary using an AI model.

    Returns:
        A tuple containing the summary string and a metadata dictionary.
    """

    # Load system prompt
    try:

        with open("app/services/summary/prompt.md", "r", encoding="utf-8") as f:
            system_prompt = f.read()
    except FileNotFoundError:
        logging.error("Summary prompt file not found.")
        raise

    # Format the explanations into a numbered list for the prompt
    formatted_explanations = "\n".join(
        f"Slide {i}: {exp}" for i, exp in enumerate(explanations, 1)
    )

    # Fill the prompt template
    user_message_content = [
        {
            "type": "input_text",
            "text": formatted_explanations,
        }
    ]

    try:
        response = await posthog_openai_client.responses.create(
            model=settings.summary_model,
            instructions=system_prompt,
            input=[{"role": "user", "content": user_message_content}],
            reasoning={"effort": "minimal"},
            text={"verbosity": "low"},
            posthog_distinct_id=customer_identifier,
            posthog_trace_id=lecture_id,
            posthog_properties={
                "service": "summary",
                "lecture_id": lecture_id,
                "explanations_count": len(explanations),
                "customer_name": name,
                "customer_email": email,
            },
        )
        summary = response.output_text

        metadata = {
            "model": response.model,
            "usage": response.usage.model_dump() if response.usage else None,
            "response_id": response.id,
        }

        if summary:
            return summary.strip(), metadata
        else:
            logging.warning("The AI model returned an empty summary.")
            return "Error: The AI model returned an empty summary.", metadata

    except Exception as e:
        logging.error(
            f"An error occurred while calling the OpenAI API: {e}", exc_info=True
        )
        raise


def mock_generate_summary(
    explanations: list[str],
    lecture_id: str,
) -> tuple[str, dict]:
    """
    Returns a mock summary result containing the full prompt that would have
    been sent to the AI model, following the pattern from other services.
    """

    # Load system prompt
    try:
        with open("app/services/summary/prompt.md", "r", encoding="utf-8") as f:
            prompt_template = f.read()
    except FileNotFoundError:
        logging.error("Summary prompt file not found for mock generation.")
        prompt_template = "[System Prompt Not Found]"

    # Format the explanations into a numbered list for the prompt
    formatted_explanations = "\n".join(
        f"{i}. {exp}" for i, exp in enumerate(explanations, 1)
    )

    # Fill the prompt template
    full_prompt_for_debug = prompt_template.format(explanations=formatted_explanations)

    summary = f"""
---
# MOCK SUMMARY
---
This is a mock response. If this were a real request, the following prompt would be sent to the AI model:
---
{full_prompt_for_debug}
"""

    metadata = {
        "model": "mock-summary-model",
        "usage": {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
        "response_id": f"mock_response_{uuid.uuid4()}",
        "mock": True,
    }

    metadata.update(
        {
            "environment": settings.app_env,
            "service": "summary",
            "lecture_id": lecture_id,
        }
    )
    return summary, metadata
