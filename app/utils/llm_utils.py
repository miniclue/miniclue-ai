"""Utility functions for working with LiteLLM APIs (Responses API, Embeddings API, etc.)."""


def extract_text_from_response(response) -> str:
    """
    Extract text content from a LiteLLM Responses API response.

    The Responses API structure is:
    {
        "output": [{
            "type": "message",
            "content": [{
                "type": "output_text",
                "text": "..."
            }]
        }]
    }

    Args:
        response: The response object from litellm.aresponses() or litellm.responses()

    Returns:
        The extracted text string, or empty string if not found
    """
    if not response.output or len(response.output) == 0:
        return ""

    output_item = response.output[0]
    if (
        not hasattr(output_item, "content")
        or not output_item.content
        or len(output_item.content) == 0
    ):
        return ""

    content_item = output_item.content[0]
    if not hasattr(content_item, "text"):
        return ""

    return content_item.text
