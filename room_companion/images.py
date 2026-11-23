"""Request illustrations and account for their usage."""

import base64
import binascii
import os

import requests

from usage import record_image

DEFAULT_IMAGE_MODEL = "gpt-image-2.6-flare"


def generate_image(payload):
    """Return a JPEG from OpenAI or raise a player-readable error."""
    try:
        response = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json=payload,
            timeout=(15, 240),
        )
        response.raise_for_status()
        body = response.json()
        record_image(body.get("usage") if isinstance(body, dict) else None)
        data = base64.b64decode(body["data"][0]["b64_json"], validate=True)
        if not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
            raise ValueError("Invalid JPEG")
        return data
    except requests.HTTPError as exc:
        code = exc.response.status_code
        hint = {
            400: "Check the requested image model and its supported settings.",
            401: "Check your OpenAI API key.",
            403: "Check your project's access to the image model.",
            404: "Check OPENAI_IMAGE_MODEL.",
            429: "Check your OpenAI quota or try again later.",
        }.get(code, "Try again in a moment.")
        raise RuntimeError(f"Image request failed (HTTP {code}). {hint}") from exc
    except requests.RequestException as exc:
        raise RuntimeError("The image service could not be reached. Try again in a moment.") from exc
    except (KeyError, IndexError, ValueError, TypeError, binascii.Error) as exc:
        raise RuntimeError("The image service returned an unreadable image.") from exc
