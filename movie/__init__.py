"""Command-line workflow for adapting a saved playthrough into a movie."""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from movie.screenplay import MovieError, draft, journal, read_screenplay
from movie.casting import cast_narrator
from movie.voices import FLASH, FLASH_LITE, Cast, VoiceRenderer, design_voice
from saves import SaveError, SaveSlot, write_atomic


def _path(path, suffix):
    if path.suffix.lower() != suffix:
        raise MovieError(f"Use a {suffix} file: {path}")
    return path.resolve()


def _export(args, root, art_dir):
    if args.save:
        slot = SaveSlot.find(root, args.save)
    else:
        slots = SaveSlot.all(root)
        if not slots:
            raise MovieError("There are no saved games to export.")
        slot = slots[0]
    record = slot.record()
    turns = journal(record)
    script = _path(args.screenplay or slot.path / "screenplay.json", ".json")
    if script == (slot.path / "game.json").resolve():
        raise MovieError("Choose a screenplay path separate from the saved journal.")
    if args.music and not args.music.is_file():
        raise MovieError(f"Could not find the music file: {args.music}")
    filename = "movie-preview.mp4" if args.silent else "movie.mp4"
    output = _path(args.output or slot.path / filename, ".mp4")
    if args.music and args.music.resolve() == output:
        raise MovieError("Choose different paths for the music and movie.")
    if script.exists() and not args.redraft:
        try:
            data = json.loads(script.read_text(encoding="utf-8"))
        except ValueError:
            raise MovieError(f"The screenplay at {script} is invalid JSON.") from None
        print(f"Using the screenplay at {script}")
    else:
        data = draft(turns, art_dir, record.get("character"))
        script.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(script, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        print(f"Wrote the screenplay to {script}")
    screenplay = read_screenplay(data, turns)
    if args.script_only:
        return
    try:
        from movie.render import render_movie
    except ModuleNotFoundError as exc:
        if exc.name != "PIL":
            raise
        raise MovieError("Install the movie dependency with python -m pip install -r requirements-movie.txt.") from None
    voices = None
    if not args.silent:
        defaults = Cast()
        model = args.speech_model or os.getenv("MOVIE_GEMINI_MODEL") or defaults.model
        if model not in ("auto", FLASH, FLASH_LITE):
            raise MovieError("Set the Gemini model to auto, gemini-3.8-flash-tts, or gemini-3.8-flash-lite-tts.")
        narrator = cast_narrator(record.get("character"), slot.path,
                                 args.narrator_voice or os.getenv("MOVIE_NARRATOR_VOICE"))
        cast = Cast(narrator.voice, args.giant_voice or os.getenv("MOVIE_GIANT_VOICE") or defaults.giant,
                    model, narrator.direction)
        voices = VoiceRenderer(slot.path / ".movie-audio", cast)
    render_movie(screenplay, turns, art_dir, output, voices=voices, music=args.music)
    print(f"Wrote the movie to {output}")


def _design(args, root):
    preview = _path(args.preview, ".wav") if args.preview else None
    ident, audio = design_voice(args.name, args.description, args.language, args.gender)
    print(f"Designed {args.name}: {ident}")
    if audio:
        preview = preview or root / "voice-previews" / f"{ident}.wav"
        preview.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(preview, audio)
        print(f"Wrote the voice preview to {preview}")


def run(argv, here):
    """Parse and execute the movie subcommands without starting or resuming gameplay."""
    parser = argparse.ArgumentParser(prog="main.py movie", description="Make a movie from a saved playthrough.")
    sub = parser.add_subparsers(dest="action", required=True)
    export = sub.add_parser("export", help="Draft or render a playthrough movie.")
    export.add_argument("save", nargs="?", help="Save name. Defaults to the most recently played or archived save.")
    export.add_argument("--screenplay", type=Path, help="Path to the editable screenplay JSON.")
    export.add_argument("--script-only", action="store_true", help="Write or validate the screenplay without rendering.")
    export.add_argument("--redraft", action="store_true", help="Replace the current screenplay with a fresh AI draft.")
    export.add_argument("--silent", action="store_true", help="Render with captions and without generating speech.")
    export.add_argument("--output", type=Path, help="Path to the rendered MP4.")
    export.add_argument("--music", type=Path, help="Optional music bed to play under the speech.")
    export.add_argument("--narrator-voice",
                        help="Override the narrator cast from the saved character with a Gemini voice name or ID.")
    export.add_argument("--giant-voice", help="Gemini voice name or designed voice ID for the giant.")
    export.add_argument("--speech-model", choices=("auto", FLASH, FLASH_LITE), help="Gemini speech model. Defaults to auto.")
    design = sub.add_parser("design-voice", help="Create a Gemini voice from a text description.")
    design.add_argument("name", help="Name to save in the Gemini project.")
    design.add_argument("description", help="Describe the voice's timbre, accent, and general delivery.")
    design.add_argument("--language", default="en-US", help="Language code, such as en-GB.")
    design.add_argument("--gender", choices=("male", "female"), help="Optional voice-design gender.")
    design.add_argument("--preview", type=Path, help="Where to save Gemini's WAV preview.")
    args = parser.parse_args(argv)
    load_dotenv(here / ".env")
    root = here / "saves"
    try:
        if args.action == "export":
            _export(args, root, here / ".room-art")
        else:
            _design(args, root)
    except (MovieError, SaveError, OSError) as exc:
        raise SystemExit(str(exc)) from None
