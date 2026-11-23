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
from characters import DEFAULT, PRESETS, CharacterError, make
from game_session import GameSession
from game_session.journal import JournalTurn
from playthrough import render
from room_companion import open_companion
from saga.catalog import Catalog, StoryError, bundle_bytes, export_nomination, read_nomination
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


def main(*, visuals=False, browser=False, port=0, resume=False, load=None, saves=False, export=None, archive=None,
         replay=None, story_catalog=None, character=None, character_description=None):
    """Start, resume, list or export games. Playing uses the configured OpenAI credentials."""
    here = Path(__file__).parent
    root = here / "saves"
    if port and (not browser or not 1 <= port <= 65535):
        raise SystemExit("Use --browser with --port and a port number between 1 and 65535.")
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

        if replay and (resume or load):
            raise StoryError("Choose a replay edition when starting a new game. A resumed game keeps its edition.")
        resuming = resume or load is not None
        has_character = character is not None or character_description is not None
        if resuming and has_character:
            raise CharacterError("A resumed game keeps its character.")
        chosen = make(DEFAULT if character is None else character, character_description) if has_character else None
        edition, plans = Catalog(story_catalog).route(replay) if replay else ("", ())
        load_dotenv(here / ".env")
        if not os.getenv("OPENAI_API_KEY"):
            raise SystemExit("Set OPENAI_API_KEY in your environment or in the game's .env file to play.")
        if resuming:
            slot = SaveSlot.find(root, load)
            session, journal = slot.load()
        else:
            if chosen is None and not browser and sys.stdin.isatty():
                try:
                    chosen = _choose_character()
                except (EOFError, KeyboardInterrupt):
                    print("\nThanks for playing The Giant's Drink!")
                    return
            catalog_path = str(Path(story_catalog).resolve()) if story_catalog else None
            session = GameSession(edition=edition, plans=plans, catalog_path=catalog_path)
            if chosen is not None:
                session.game.character = chosen
            slot, journal = SaveSlot.new(root), []
    except (CharacterError, SaveError, StoryError) as exc:
        raise SystemExit(str(exc)) from None

    if browser:
        play_in_browser(session, journal=journal, store=slot, choose_character=not resuming and not has_character, port=port)
        return
    with open_companion(session.meter) if visuals else nullcontext(None) as viewer:
        _play(viewer, session, journal, slot)
    tokens = session.meter.snapshot()
    paintings = "illustration" if tokens["images"] == 1 else "illustrations"
    print(f"This game has used {tokens['total']:,} AI tokens: {tokens['text']:,} for text and "
          f"{tokens['image']:,} for {tokens['images']} {paintings}.")


def _edit_description(text):
    try:
        import readline
    except ImportError:
        readline = None
    # The libedit backend can expose the startup hook without running it.
    if readline is None or "libedit" in (readline.__doc__ or "") or not sys.stdout.isatty():
        print(text)
        return input("> ") or text
    readline.set_startup_hook(lambda: readline.insert_text(text))
    try:
        return input("> ")
    finally:
        readline.set_startup_hook()


def _choose_character():
    print("Who enters the castle?")
    choices = {str(index): preset for index, preset in enumerate(PRESETS, 1)}
    for index, preset in choices.items():
        name, description = PRESETS[preset]
        print(f"  {index}. {name} — {description}")
    options = " or ".join(choices)
    while True:
        choice = input(f"Choose {options} (Enter for 1): ").strip() or "1"
        if choice in choices:
            break
        print(f"Choose {options}.")
    preset = choices[choice]
    text = PRESETS[preset][1]
    print("Edit your description, then press Enter. Enter alone keeps it.")
    while True:
        try:
            return make(preset, _edit_description(text))
        except CharacterError as exc:
            print(exc)


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


def _nominate(session, slot):
    try:
        bundle = session.story_nomination()
        print(bundle_bytes(bundle).decode("utf-8"))
        print("Review the story text above for personal details before sharing it.")
        if input("Export this storyline for submission? [y/N] ").strip().casefold() in ("y", "yes"):
            path = export_nomination(bundle, slot.root / "nominations")
            print(f"Nomination exported to {path}. Send this file to the game maintainer for review.")
    except StoryError as exc:
        print(exc)
    except EOFError:
        print("Nomination canceled.")


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
    if session.saga.edition:
        print(f"Replaying {session.saga.edition}.")
    if slot:
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
        if command.casefold() == "nominate":
            _nominate(session, slot)
        elif command:
            turn = JournalTurn.start(len(journal) + 1, command, session.public_state())
            with _quiet_typeahead():
                turn.events = session.turn(command, lambda event: print(event.text, flush=True))
                if viewer and not session.ended:
                    session.show(viewer)
            journal.append(turn.record(art=session.turn_art(viewer) if viewer else None))
            if slot:
                try:
                    slot.save(session, journal)
                except Exception as exc:
                    print(f"[Save] Could not save this turn: {exc}", flush=True)


def _stories(argv, queue):
    parser = argparse.ArgumentParser(prog="main.py stories", description="List, review, and publish replay editions.")
    parser.add_argument("--catalog", help="Path to a SQLite catalog. Defaults to the one shipped with the game.")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="List published editions.")
    actions.add_parser("queue", help="List imported nominations.")
    receive = actions.add_parser("import", help="Check a nomination and put it in the local review queue.")
    receive.add_argument("file", type=Path)
    for command in ("review", "playtest", "publish"):
        action = actions.add_parser(command)
        action.add_argument("id", help="The nomination ID shown on import.")
        if command == "playtest":
            action.add_argument("--browser", action="store_true")
        if command == "publish":
            action.add_argument("edition", help="A lowercase ID such as lantern-marsh.")
            action.add_argument("revision", type=int)
            action.add_argument("--title", required=True)
    args = parser.parse_args(argv)
    catalog = Catalog(args.catalog)
    try:
        if args.action == "list":
            editions = catalog.editions() if catalog.path.exists() else []
            print("\n".join(f"{name}@{revision}  {title}" for name, revision, title in editions)
                  or "There are no published story editions yet.")
            return
        if args.action == "queue":
            print("\n".join(path.stem for path in sorted(queue.glob("*.json"))) or "The review queue is empty.")
            return
        if args.action == "import":
            bundle = read_nomination(args.file)
            path = export_nomination(bundle, queue)
            print(f"Queued {path.stem} for review.")
        else:
            if not re.fullmatch(r"[0-9a-f]{64}", args.id):
                raise StoryError("Use the nomination ID printed at import.")
            bundle = read_nomination(queue / f"{args.id}.json")
        if args.action in ("import", "review"):
            if args.action == "review":
                print(bundle_bytes(bundle).decode("utf-8"))
            print("Route " + " → ".join(plan["title"] for plan in bundle["arcs"]))
            if catalog.path.exists():
                for edition, exact in catalog.matching_routes(bundle["arcs"]):
                    print(f"{'Matches' if exact else 'Extends'} {edition}.")
        elif args.action == "playtest":
            load_dotenv(Path(__file__).with_name(".env"))
            session = GameSession.for_story_review(bundle["arcs"])
            if args.browser:
                play_in_browser(session)
            else:
                _play(None, session, [], None)
        elif args.action == "publish":
            edition = catalog.publish(bundle, args.edition, args.revision, args.title)
            print(f"Published {edition} in {catalog.path}.")
    except StoryError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    if sys.argv[1:2] == ["movie"]:
        from movie import run

        run(sys.argv[2:], Path(__file__).resolve().parent)
        raise SystemExit(0)
    if sys.argv[1:2] == ["stories"]:
        _stories(sys.argv[2:], Path(__file__).with_name("saves") / "story-review")
        raise SystemExit(0)
    cli = argparse.ArgumentParser(description="Play The Giant's Drink.",
                                  epilog="Use main.py movie --help for movie export and voice design.")
    cli.add_argument("--browser", action="store_true", help="Play in a local browser with illustrations and cutscenes.")
    cli.add_argument("--port", type=int, default=0, help="Reuse this local port for browser play (default: automatic).")
    cli.add_argument("--visuals", action="store_true", help="Open an illustrated companion for terminal play.")
    cli.add_argument("--replay", metavar="EDITION", help="Reuse a published story. Use ID@REVISION to pick a revision.")
    cli.add_argument("--story-catalog", metavar="PATH", help="Use an alternate published SQLite story catalog.")
    cli.add_argument("--character", choices=PRESETS, help="Choose a starting character for a new game.")
    cli.add_argument("--character-description", metavar="TEXT", help="Set your starting character's description.")
    saved = cli.add_mutually_exclusive_group()
    saved.add_argument("--continue", dest="resume", action="store_true", help="Resume your most recently played game.")
    saved.add_argument("--load", metavar="NAME", help="Resume a saved game by name.")
    saved.add_argument("--saves", action="store_true", help="List saved games.")
    saved.add_argument("--export", metavar="NAME", nargs="?", const="",
                       help="Write a shareable playthrough page for a saved game (default: the latest).")
    saved.add_argument("--archive", metavar="URL",
                       help="Save the journal of a game still running on an older build, then export it.")
    main(**vars(cli.parse_args()))
