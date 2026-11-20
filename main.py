# main.py
import argparse
import json
import os
import re
import sys
import urllib.request
from contextlib import contextmanager, nullcontext
from pathlib import Path

from dotenv import load_dotenv

from browser_game import play_in_browser
from game_session import GameSession
from game_world import world_map
from playthrough import render
from room_companion import open_companion
from saves import SaveError, SaveSlot


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


def main(*, visuals=False, browser=False, resume=False, load=None, saves=False, export=None, archive=None):
    """Start, resume, list or export games. Playing uses the configured OpenAI credentials."""
    here = Path(__file__).parent
    root = here / "saves"
    try:
        if saves:
            slots = SaveSlot.all(root)
            print("\n".join(slot.summary() for slot in slots) if slots else "There are no saved games yet.")
            return
        if archive:
            export = _archive(root, archive).ident
        if export is not None:
            slot = SaveSlot.find(root, export or None)
            page = slot.path / "playthrough.html"
            page.write_text(render(slot.record(), here / ".room-art"), encoding="utf-8")
            print(f"Wrote the playthrough of {slot.ident} to {page}")
            return

        load_dotenv(here / ".env")
        if not os.getenv("OPENAI_API_KEY"):
            raise SystemExit("Set OPENAI_API_KEY in your environment or in the game's .env file to play.")
        if resume or load:
            slot = SaveSlot.find(root, load)
            session, journal = slot.load()
        else:
            slot, session, journal = SaveSlot.new(root), GameSession(world_map), []
    except SaveError as exc:
        raise SystemExit(str(exc)) from None

    if browser:
        play_in_browser(session, journal=journal, store=slot)
        return
    with open_companion(session.meter) if visuals else nullcontext(None) as viewer:
        _play(viewer, session, journal, slot)
    tokens = session.meter.snapshot()
    paintings = "illustration" if tokens["images"] == 1 else "illustrations"
    print(f"This game has used {tokens['total']:,} AI tokens: {tokens['text']:,} for text and "
          f"{tokens['image']:,} for {tokens['images']} {paintings}.")


def _archive(root, url):
    """Keep the journal of a game still running on an older build, as a view-only save."""
    url = url.rstrip("/")
    if not re.fullmatch(r"http://(?:127\.0\.0\.1|localhost):[0-9]{1,5}", url):
        raise SaveError("Give the address of a game running on this computer, such as http://127.0.0.1:64482")
    try:
        state = json.load(urllib.request.urlopen(url + "/state", timeout=10))
        journal, before = [], None
        while True:
            page = json.load(urllib.request.urlopen(url + "/journal" + (f"?before={before}" if before else ""),
                                                    timeout=10))
            journal = [*page["entries"][::-1], *journal]
            if not page["more"]:
                break
            before = page["entries"][-1]["turn"]
    except (OSError, ValueError, KeyError) as exc:
        raise SaveError(f"Could not read the game at {url}: {exc}") from None
    slot = SaveSlot.new(root)
    slot.write_record(state["room"], journal)
    print(f"Archived {len(journal)} turns as {slot.ident} (view only).")
    return slot


def _play(viewer, session, journal, slot):
    # Python's input() uses readline for editing, history and redisplaying queued input.
    if sys.stdin.isatty():
        try:
            import readline  # noqa: F401
        except ImportError:
            pass
    if journal:
        print(f"Welcome back to The Giant's Drink! Resuming {slot.ident} after turn {len(journal)}.")
    else:
        print("Welcome to The Giant's Drink!")
    print(f"Autosaving as {slot.ident}. Resume later with --continue or --load {slot.ident}.")
    print("Type HELP for a list of commands.\n")
    print("[Procedural] Opening room.", flush=True)
    print(session.game.get_current_description(session.rooms, description=session.description))
    if viewer:
        session.show(viewer)

    while not session.ended:
        try:
            command = input("\n> ").strip()
        except EOFError:
            print("\nThanks for playing The Giant's Drink!")
            return
        if command:
            place = session.public_state()
            with _quiet_typeahead():
                events = session.turn(command, lambda event: print(event.text, flush=True))
                if viewer and not session.ended:
                    session.show(viewer)
            art = viewer.snapshot() if viewer else {}
            journal.append({
                "turn": len(journal) + 1, "command": command, "place": place["name"], "chapter": place["chapter"],
                "events": [{"kind": event.kind, "text": event.text} for event in events if event.kind != "activity"],
                "art": {"key": art["key"], "kind": art["kind"]} if art.get("key") else None,
            })
            try:
                slot.save(session, journal)
            except Exception as exc:
                print(f"[Save] Could not save this turn: {exc}", flush=True)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description="Play The Giant's Drink.")
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument("--browser", action="store_true", help="Play in a local browser with room illustrations.")
    mode.add_argument("--visuals", action="store_true", help="Open a local browser companion with AI room illustrations.")
    saved = cli.add_mutually_exclusive_group()
    saved.add_argument("--continue", dest="resume", action="store_true", help="Resume your most recently played game.")
    saved.add_argument("--load", metavar="NAME", help="Resume a saved game by name.")
    saved.add_argument("--saves", action="store_true", help="List saved games.")
    saved.add_argument("--export", metavar="NAME", nargs="?", const="",
                       help="Write a shareable playthrough page for a saved game (default: the latest).")
    saved.add_argument("--archive", metavar="URL",
                       help="Save the journal of a game still running on an older build, then export it.")
    main(**vars(cli.parse_args()))
