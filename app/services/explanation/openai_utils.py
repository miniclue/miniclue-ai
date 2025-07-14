import json
from openai import OpenAI

from app.utils.config import Settings


settings = Settings()
client = OpenAI(api_key=settings.xai_api_key, base_url=settings.xai_api_base_url)


def generate_explanation(
    slide_number: int,
    context_recap: list[str],
    previous_one_liner: str,
    full_text: str,
    related_concepts: list[str],
    ocr_texts: list[str],
    alt_texts: list[str],
) -> tuple[str, str, str, str]:
    """Build prompt, call API, and parse explanation from response."""
    with open("app/services/explanation/prompt.md", "r", encoding="utf-8") as file:
        system_msg = file.read()

    # Prepare formatted sections
    text_block = full_text.strip()
    # Use raw OCR text blocks for code fences
    ocr_block = "\n".join(ocr_texts)
    alt_bullets = "\n".join(f"- {line}" for line in alt_texts)
    context_bullets = "\n".join(f"- {line}" for line in context_recap)
    # Build related concepts bullets
    related_bullets = "\n\n".join(f"- {line}" for line in related_concepts)

    user_prompt = f"""
## Slide Information
**Slide Number**: {slide_number}

**Text Content**:
```text
{text_block}
```

**Image OCR Text**:
```text
{ocr_block}
```

**Image Alt Text**:
{alt_bullets}

## Previous Slide
**Previous Slide's One-Liner**:
{previous_one_liner}

## Context Recap
**Last 2–3 Slides' One-Liners**:
{context_bullets}

## Related Concepts
**From Earlier Slides (via RAG)**:
{related_bullets}

## Your Task
- Determine the slide type: "cover", "header", or "content".
- Explain the slide using the following guidelines:
    - A smooth transition from the previous slide's one-liner.
    - A clear high-level summary followed by in-depth explanation (Minto Pyramid).
    - Plain English, avoiding or clearly explaining jargon.
    - Analogies and rhetorical questions to aid understanding.
    - Markdown formatting for clarity.
    - LaTeX syntax for any formulas or equations.
    - Emojis to visually support key points.
    - Only content present in this slide.

## Output Format
Return your answer as a valid JSON object with the following fields:
```json
  "slide_type": "cover" | "header" | "content",
  "one_liner": "Key takeaway here (≤ 25 words).",
  "content": "Full explanation here in Markdown and LaTeX."
```
Only return the JSON. Do not include any additional text or explanation.
    """

    response = client.chat.completions.create(
        model="grok-3-mini",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_prompt},
        ],
        temperature=1,
    )
    content_str = response.choices[0].message.content or ""

    try:
        data = json.loads(content_str)
    except json.JSONDecodeError:
        # Retry by escaping backslashes to handle LaTeX or other backslash sequences
        sanitized = content_str.replace("\\", "\\\\")
        try:
            data = json.loads(sanitized)
        except json.JSONDecodeError:
            raise ValueError(
                f"Failed to parse explanation JSON even after sanitizing: {content_str}"
            )

    one_liner = data.get("one_liner", "")
    content = data.get("content", "")
    slide_type = data.get("slide_type", "")

    metadata = {
        "response_id": response.id,
        "object": response.object,
        "created": response.created,
        "model": response.model,
        "finish_reason": response.choices[0].finish_reason,
        "usage": (
            {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
            if response.usage
            else None
        ),
    }
    metadata_str = json.dumps(metadata)

    return slide_type, one_liner, content, metadata_str


def mock_generate_explanation(
    slide_number: int,
    context_recap: list[str],
    previous_one_liner: str,
    full_text: str,
    related_concepts: list[str],
    ocr_texts: list[str],
    alt_texts: list[str],
) -> tuple[str, str, str, str]:
    """Mock: build the full prompt and return it as content without calling the LLM API."""
    # Load system instructions
    with open("app/services/explanation/prompt.md", "r", encoding="utf-8") as file:
        system_msg = file.read()

    # Prepare formatted sections
    text_block = full_text.strip()
    # Use raw OCR text blocks for code fences
    ocr_block = "\n".join(ocr_texts)
    alt_bullets = "\n".join(f"- {line}" for line in alt_texts)
    context_bullets = "\n".join(f"- {line}" for line in context_recap)
    # Build related concepts bullets
    related_bullets = "\n\n".join(f"- {line}" for line in related_concepts)
    # Build user prompt exactly as in generate_explanation
    user_prompt = f"""
## Slide Information
**Slide Number**: {slide_number}

**Text Content**:
```text
{text_block}
```

**Image OCR Text**:
```text
{ocr_block}
```

**Image Alt Text**:
{alt_bullets}

## Previous Slide
**Previous Slide's One-Liner**:
{previous_one_liner}

## Context Recap
**Last 2–3 Slides' One-Liners**:
{context_bullets}

## Related Concepts
**From Earlier Slides (via RAG)**:
{related_bullets}

## Your Task
- Determine the slide type: "cover", "header", or "content".
- Explain the slide using the following guidelines:
    - A smooth transition from the previous slide's one-liner.
    - A clear high-level summary followed by in-depth explanation (Minto Pyramid).
    - Plain English, avoiding or clearly explaining jargon.
    - Analogies and rhetorical questions to aid understanding.
    - Markdown formatting for clarity.
    - LaTeX syntax for any formulas or equations.
    - Emojis to visually support key points.
    - Only content present in this slide.

## Output Format
Return your answer as a valid JSON object with the following fields:
```json
  "slide_type": "cover" | "header" | "content",
  "one_liner": "Key takeaway here (≤ 25 words).",
  "content": "Full explanation here in Markdown and LaTeX."
```
Only return the JSON. Do not include any additional text or explanation.
    """

    # Combine system and user messages as the full prompt
    combined_prompt = system_msg + "\n\n" + user_prompt

    # Return mock values
    one_liner = "prompt"
    metadata = {"mock": True}
    metadata_str = json.dumps(metadata)
    slide_type = "mock"
    return slide_type, one_liner, combined_prompt, metadata_str
