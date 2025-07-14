import json
from openai import OpenAI
from typing import List

from app.utils.config import Settings

settings = Settings()
client = OpenAI(api_key=settings.xai_api_key, base_url=settings.xai_api_base_url)


def generate_summary(slide_explanations: List[str]) -> tuple[str, str]:
    """Build prompt, call API, and parse summary from response."""
    with open("app/services/summary/prompt.md", "r", encoding="utf-8") as file:
        system_msg = file.read()

    explanations_str = "\n\n".join(
        f"[Slide {i}]\n{expl}" for i, expl in enumerate(slide_explanations, start=1)
    )

    user_prompt = f"""
You are creating a student-friendly **cheatsheet** for a university lecture.

Below are the **full per-slide explanations**, written by an AI professor. Each explanation covers the content of one lecture slide using Markdown and LaTeX.

---

## Slide Explanations
{explanations_str}

---

## Your Task

Use the above explanations to generate a **comprehensive yet concise cheatsheet** that helps a university student revise quickly and confidently.

### ✅ Format & Structure

1. **Key Takeaways**
   - Start with a section titled:
     ```markdown
     # Key Takeaways
     - ...
     ```
   - Include 5–15 short, clear bullet points that summarize the most important ideas across the entire lecture.

2. **Organized Content**
   - Structure the rest of the cheatsheet into logical sections and topics:
     - Use `##` for major topics or sections
     - Use `###` for key subtopics or concepts
   - You may infer section boundaries based on content (e.g., recurring themes or transitions).

3. **Clarity & Teaching Style**
   - Use **plain English** — short, simple, easy-to-understand sentences.
   - Clearly **explain technical terms** and acronyms.
   - Use **rhetorical questions** and **analogies** if they help understanding.
   - Use **examples** where appropriate.

4. **Math & Formatting**
   - Use **Markdown** to format the cheatsheet cleanly.
   - Use **LaTeX** for any mathematical formulas or equations (e.g., `$F = ma$`).

---

## Output Format

- Return a single, well-structured **Markdown cheatsheet**
- Do **not** include any JSON, commentary, or metadata
- Output should be ready for use in a Markdown editor or study app

---

Only return the Markdown cheatsheet.
"""

    response = client.chat.completions.create(
        model="grok-3-mini",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
    )

    summary = ""
    if response.choices and response.choices[0].message.content:
        summary = response.choices[0].message.content.strip()

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

    return summary, metadata_str


def mock_generate_summary(slide_explanations: List[str]) -> tuple[str, str]:
    """Mock: build the full prompt and return it as content without calling the LLM API."""
    with open("app/services/summary/prompt.md", "r", encoding="utf-8") as file:
        system_msg = file.read()

    explanations_str = "\n\n".join(
        f"[Slide {i}]\n{expl}" for i, expl in enumerate(slide_explanations, start=1)
    )

    user_prompt = f"""
You are creating a student-friendly **cheatsheet** for a university lecture.

Below are the **full per-slide explanations**, written by an AI professor. Each explanation covers the content of one lecture slide using Markdown and LaTeX.

---

## Slide Explanations
{explanations_str}

---

## Your Task

Use the above explanations to generate a **comprehensive yet concise cheatsheet** that helps a university student revise quickly and confidently.

### ✅ Format & Structure

1. **Key Takeaways**
   - Start with a section titled:
     ```markdown
     # Key Takeaways
     - ...
     ```
   - Include 5–15 short, clear bullet points that summarize the most important ideas across the entire lecture.

2. **Organized Content**
   - Structure the rest of the cheatsheet into logical sections and topics:
     - Use `##` for major topics or sections
     - Use `###` for key subtopics or concepts
   - You may infer section boundaries based on content (e.g., recurring themes or transitions).

3. **Clarity & Teaching Style**
   - Use **plain English** — short, simple, easy-to-understand sentences.
   - Clearly **explain technical terms** and acronyms.
   - Use **rhetorical questions** and **analogies** if they help understanding.
   - Use **examples** where appropriate.

4. **Math & Formatting**
   - Use **Markdown** to format the cheatsheet cleanly.
   - Use **LaTeX** for any mathematical formulas or equations (e.g., `$F = ma$`).

---

## Output Format

- Return a single, well-structured **Markdown cheatsheet**
- Do **not** include any JSON, commentary, or metadata
- Output should be ready for use in a Markdown editor or study app

---

Only return the Markdown cheatsheet.
"""

    summary_prompt = system_msg + "\n\n" + user_prompt
    metadata = {"mock": True}
    metadata_str = json.dumps(metadata)

    return summary_prompt, metadata_str
