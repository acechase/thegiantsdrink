import logging
import os
from typing import Callable, Optional

import requests

from usage import record_text

logger = logging.getLogger(__name__)
DEFAULT_MODEL = "gpt-5.4-mini"


def complete(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int,
    json_output: bool = False,
    purpose: str = "text generation",
    report: Optional[Callable[[str], None]] = None,
    read_timeout: Optional[float] = None,
) -> Optional[str]:
    """Request a text completion from OpenAI, returning None if it is unavailable."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    model = os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
    effort = os.getenv("OPENAI_REASONING_EFFORT", "").strip().lower()
    # The default model uses no reasoning by default. Other models can spend
    # completion tokens reasoning even when the caller leaves effort unset.
    allow_reasoning = effort != "none" and (bool(effort) or model != DEFAULT_MODEL)
    payload = {
        "model": model,
        "messages": [
            {"role": "developer", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_completion_tokens": max_tokens + (8192 if allow_reasoning else 0),
    }
    if effort:
        payload["reasoning_effort"] = effort
    if json_output:
        payload["response_format"] = {"type": "json_object"}

    if report is not None:
        report(f"[AI] Calling {model} for {purpose}...")
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=(15, read_timeout or (180 if allow_reasoning else 15)),
        )
        response.raise_for_status()
        body = response.json()
        record_text(body.get("usage") if isinstance(body, dict) else None)
        content = body["choices"][0]["message"]["content"]
    except requests.HTTPError as exc:
        logger.warning(
            "OpenAI request failed (HTTP %s). Check your key, model, reasoning effort and quota.", exc.response.status_code
        )
        return None
    except requests.RequestException as exc:
        logger.warning("OpenAI request failed (%s).", type(exc).__name__)
        return None
    except (KeyError, IndexError, TypeError, ValueError):
        logger.warning("OpenAI returned an unexpected completion.")
        return None

    if isinstance(content, str) and content.strip():
        return content.strip()
    return None
