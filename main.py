# main.py
import argparse
import os
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path

from dotenv import load_dotenv

from browser_game import play_in_browser
from game_session import GameSession
from game_world import world_map
from room_companion import open_companion


@contextmanager
def _quiet_typeahead():
    """Queue early keystrokes without echoing them over a pending turn's output."""
    if not sys.stdin.isatty():
        yield
        return
    try:
        import termios

        fd = sys.stdin.fileno()
        previous = termios.tcgetattr(fd)
    except (ImportError, OSError, ValueError):
        yield
        return

    quiet = previous.copy()
    quiet[3] &= ~termios.ECHO
    termios.tcsetattr(fd, termios.TCSANOW, quiet)
    try:
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, previous)


def main(*, visuals=False, browser=False):
    """Start a local game using the configured OpenAI credentials."""
    load_dotenv(Path(__file__).with_name(".env"))
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY in your environment or in the game's .env file to play.")

    if browser:
        play_in_browser(GameSession(world_map))
        return
    with open_companion() if visuals else nullcontext(None) as viewer:
        _play(viewer)


def _play(viewer):
    # Python's input() uses readline for editing, history and redisplaying queued input.
    if sys.stdin.isatty():
        try:
            import readline  # noqa: F401
        except ImportError:
            pass
    session = GameSession(world_map)
    print("Welcome to The Giant's Drink!")
    print("Type HELP for a list of commands.\n")
    print("[Procedural] Opening room.", flush=True)
    print(session.game.get_current_description(session.rooms))
    if viewer:
        session.show(viewer)

    while not session.ended:
        try:
            command = input("\n> ").strip()
        except EOFError:
            print("\nThanks for playing The Giant's Drink!")
            return
        if command:
            with _quiet_typeahead():
                session.turn(command, lambda event: print(event.text, flush=True))
                if viewer and not session.ended:
                    session.show(viewer)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description="Play The Giant's Drink.")
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument("--browser", action="store_true", help="Play in a local browser with room illustrations.")
    mode.add_argument("--visuals", action="store_true", help="Open a local browser companion with AI room illustrations.")
    main(**vars(cli.parse_args()))
