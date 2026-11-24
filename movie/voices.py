"""Gemini speech and prompted voice design for the movie exporter."""

import base64
import hashlib
import io
import json
import os
import re
import wave
from dataclasses import dataclass

import requests

from movie.screenplay import MovieError
from saves import write_atomic

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"
FLASH = "gemini-3.8-flash-tts"
FLASH_LITE = "gemini-3.8-flash-lite-tts"
DIRECTIONS = {
    "narrator": "An intimate first-person recollection, with a conversational pace and understated suspense.",
    "giant": "Quiet, measured authority. Speak clearly and let the final word land.",
}
SELF_DESCRIPTION = re.compile(r"(?:^|(?<=[.!?])\s+)(?:you are|you're|i am|i'm|my character is|the player is)\s+", re.I)
IDENTITY_PHRASE = re.compile(r"^(?:an?\s+|the\s+)?(?P<phrase>[\w\s-]+)", re.I)
IDENTITY_STOPS = {
    "and", "or", "with", "who", "whose", "wearing", "like", "not", "neither", "but", "of", "in", "from",
    "child", "person", "explorer", "adventurer",
}
NARRATOR_VOICES = {"girl": "Leda", "female": "Leda", "woman": "Despina",
                   "boy": "Algieba", "male": "Algieba", "man": "Algieba"}


class GeminiError(MovieError):
    """A failed Gemini response with its status for model selection."""

    def __init__(self, status, message, detail=""):
        """Keep the HTTP status and Gemini's reason without exposing authentication data."""
        super().__init__(message)
        self.status = status
        self.detail = detail


def _post(resource, payload):
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        raise MovieError("Set GEMINI_API_KEY in the project's .env file to generate voices.")
    try:
        response = requests.post(f"{GEMINI_URL}/{resource}", headers={"x-goog-api-key": key},
                                 json=payload, timeout=(12, 120))
    except requests.RequestException:
        raise MovieError("Could not reach Gemini. Check your connection, then re-run to reuse completed takes.") from None
    status = response.status_code
    if not response.ok:
        if status == 429:
            raise GeminiError(status, "Gemini reached the model's rate or daily limit. Re-run after the quota resets.")
        if status in (401, 403):
            raise GeminiError(status, f"Gemini denied access (HTTP {status}). Check your key and project permissions.")
        try:
            detail = str(response.json()["error"]["message"])[:200]
        except (ValueError, KeyError, TypeError):
            detail = ""
        advice = detail or "Check the model and configured voice IDs."
        raise GeminiError(status, f"Gemini {resource} returned HTTP {status}. {advice}", detail)
    try:
        body = response.json()
    except ValueError:
        raise MovieError("Gemini returned an invalid response.") from None
    if not isinstance(body, dict):
        raise MovieError("Gemini returned an invalid response.")
    return body


def _valid_wav(data):
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            frames = wav.getnframes()
            frame_size = wav.getnchannels() * wav.getsampwidth()
            return (wav.getnchannels() in (1, 2) and wav.getsampwidth() == 2
                    and 0 < frames <= wav.getframerate() * 90
                    and len(wav.readframes(frames)) == frames * frame_size)
    except (EOFError, wave.Error, ValueError):
        return False


def _wav(part):
    try:
        data = base64.b64decode(part["data"], validate=True)
    except (KeyError, TypeError, ValueError):
        data = b""
    if not _valid_wav(data):
        raise MovieError("Gemini did not return a usable WAV take.")
    return data


def _speech(model, voice, text, direction):
    passage = {"type": "text", "text": text}
    if direction:  # A designed voice already carries its character, so it may speak without a style note.
        passage["annotations"] = [{"type": "speech_metadata", "style": direction}]
    return {
        "model": model,
        "input": [{"type": "user_input", "content": [passage]}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voice}]},
    }


def _audio_part(body):
    try:
        parts = [part for step in body["steps"] if step["type"] == "model_output"
                 for part in step["content"] if part["type"] == "audio" and part.get("data")]
        return parts[-1]
    except (KeyError, IndexError, TypeError, AttributeError):
        raise MovieError("Gemini returned no speech for a passage. Try a shorter passage or a different voice.") from None


@dataclass(frozen=True)
class Cast:
    """Voice IDs and model choice for an export."""

    narrator: str = "Algieba"
    giant: str = "Algenib"
    model: str = "auto"
    narrator_direction: str = DIRECTIONS["narrator"]


def narrator_for(character):
    """Choose a prebuilt voice from the saved preset or an explicit self-description."""
    if not isinstance(character, dict):
        return Cast.narrator
    text = character.get("description")
    if isinstance(text, str):
        text = text.strip().replace("’", "'")
        descriptions = [text[match.end():] for match in SELF_DESCRIPTION.finditer(text)] or [text]
        for description in descriptions:
            phrase = IDENTITY_PHRASE.match(description)
            if not phrase:
                continue
            for word in phrase["phrase"].casefold().split()[:12]:
                if word in IDENTITY_STOPS:
                    break
                if word in NARRATOR_VOICES:
                    return NARRATOR_VOICES[word]
    return "Leda" if character.get("preset") == "girl" else Cast.narrator


class VoiceRenderer:
    """Cache takes by the exact Gemini request and fall back once on quota errors."""

    def __init__(self, cache, cast, report=print):
        """Construct a renderer that can also run entirely from cached takes."""
        self.cache = cache
        self.cast = cast
        self.report = report
        self.models = (FLASH, FLASH_LITE) if cast.model == "auto" else (cast.model,)
        self.limited = set()

    def take(self, scene):
        """Return the WAV path for the selected speaker and exact passage."""
        voice = getattr(self.cast, scene.speaker)
        if scene.speaker == "narrator":
            default = self.cast.narrator_direction
        else:  # A designed giant carries its own character, so it needs no default style note.
            default = "" if voice.startswith("voice_") else DIRECTIONS[scene.speaker]
        direction = scene.direction or default
        candidates = []
        for model in self.models:
            payload = _speech(model, voice, scene.text, direction)
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            path = self.cache / f"{digest}.wav"
            if path.is_file() and _valid_wav(path.read_bytes()):
                return path
            candidates.append((model, path, payload))
        self.cache.mkdir(parents=True, exist_ok=True)
        for model, path, payload in candidates:
            if model in self.limited:
                continue
            self.report(f"Voicing {scene.title} with {voice} on {model}.")
            try:
                audio = _wav(_audio_part(_post("interactions", payload)))
            except GeminiError as exc:
                if exc.status != 429 or len(self.models) == 1:
                    raise
                self.limited.add(model)
                if model == FLASH:
                    self.report("Flash TTS reached its quota. Trying Flash-Lite with the same voice.")
                continue
            write_atomic(path, audio)
            return path
        raise MovieError("Both Gemini speech models reached their quota. Completed takes are cached for the next run.")


def design_voice(name, description, language, gender=None):
    """Create a reusable prompted voice in the Gemini project and return its WAV preview if present."""
    if not 1 <= len(name.strip()) <= 80 or not 1 <= len(description.strip()) <= 800:
        raise MovieError("Supply a name up to 80 characters and a description up to 800 characters.")
    voice = {"model": FLASH, "type": "prompted", "display_name": name.strip(),
             "language_code": language, "prompted": {"input": description.strip()}}
    if gender:
        voice["gender"] = gender
    body = _post("voices", {"store": True, "voice": voice})
    ident = body.get("id") or body.get("name")
    if not isinstance(ident, str) or not ident.rsplit("/", 1)[-1].startswith("voice_"):
        raise MovieError("Gemini did not return an ID for the designed voice.")
    ident = ident.rsplit("/", 1)[-1]
    preview = body.get("sample_audio") or body.get("sampleAudio")
    try:
        audio = _wav(preview) if preview else None
    except MovieError:
        audio = None  # The saved voice remains usable even if a preview fails.
    return ident, audio
