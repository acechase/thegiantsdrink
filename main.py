# main.py
import os
import sys
from contextlib import contextmanager
from copy import deepcopy
from functools import partial
from pathlib import Path

from dotenv import load_dotenv

from adaptation_ai import AdaptationAI
from behavior_analyzer import BehaviorAnalyzer
from command_parser import parse_command
from content_generator import ContentGenerator
from game_world import world_map
from simulation_engine import GameState

HELP_TEXT = """Available commands:
  LOOK          - Look around the current room
  EXAMINE <object> - Inspect something here or in your inventory
  GO <direction> - Move through a listed exit
  TAKE <item>   - Pick up an item
  USE <item>    - Use an item from your inventory
  USE <item> ON <object> - Try a tool on something nearby
  ATTACK <target> - Attack something
  INVENTORY     - Check your inventory
  HELP          - Show this help message
  QUIT / EXIT   - Leave the game"""


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


def main():
    """Run the main game loop for The Giant's Drink text adventure.

    Initializes GameState, behavior analyzer, adaptation AI, and content generator.
    Processes player commands until the giant is defeated. Continuously adapts
    difficulty and world state based on player profile.


    """
    load_dotenv(Path(__file__).with_name(".env"))
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY in your environment or in the game's .env file to play.")

    rooms = deepcopy(world_map)
    # Python's input() uses readline for editing, history and redisplaying queued input.
    if sys.stdin.isatty():
        try:
            import readline  # noqa: F401
        except ImportError:
            pass
    game_state = GameState()
    behavior_analyzer = BehaviorAnalyzer()
    adaptation_ai = AdaptationAI()
    content_gen = ContentGenerator()
    report = partial(print, flush=True)

    print("Welcome to The Giant's Drink!")
    print("Type HELP for a list of commands.\n")
    report("[Procedural] Opening room.")
    print(game_state.get_current_description(rooms))

    while not game_state.giant_defeated:
        command = input("\n> ").strip()
        if not command:
            continue

        with _quiet_typeahead():
            previous_room = game_state.current_room
            room = rooms[previous_room]
            action, target = parse_command(command, room, game_state.inventory, report=report)

            if action:
                report(f"[Procedural] Resolving {action.replace('_', ' ')}.")
            else:
                report("[Procedural] Using the unknown-command response.")
            if action == "move" and target:
                result = game_state.move(target, rooms)
                if game_state.current_room != previous_room:
                    behavior_analyzer.update_profile("move")
            elif action == "take" and target:
                held = len(game_state.inventory)
                result = game_state.take_item(target, rooms)
                if len(game_state.inventory) > held:
                    behavior_analyzer.update_profile("take")
            elif action == "use" and target:
                difficulty = adaptation_ai.get_difficulty()
                result = game_state.use_item(target, difficulty)
                behavior_analyzer.update_profile("use")
                if game_state.book_knowledge:
                    behavior_analyzer.mark_book_read()
            elif action == "use_on":
                was_open = room.passage_open
                result = game_state.use_on(target.tool, target.subject, rooms, adaptation_ai.get_difficulty())
                if room.passage_open and not was_open:
                    behavior_analyzer.update_profile("discover")
            elif action == "attack" and target:
                difficulty = adaptation_ai.get_difficulty()
                result = game_state.attack(target, difficulty)
                behavior_analyzer.update_profile("attack")
            elif action == "check" and target == "inventory":
                result = (
                    "Inventory: " + ", ".join([item.name for item in game_state.inventory])
                    if game_state.inventory
                    else "Your inventory is empty."
                )
                behavior_analyzer.update_profile("check")
            elif action == "look" and target in ("around", "room"):
                result = game_state.get_current_description(rooms)
            elif action in ("examine", "look"):
                result = game_state.examine(target, rooms)
            elif action == "help":
                result = HELP_TEXT
            elif action == "quit":
                print("Thanks for playing The Giant's Drink!")
                return
            else:
                result = "I do not understand that command. Type HELP for a list of commands."

            profile = behavior_analyzer.get_profile()
            profile_summary = behavior_analyzer.get_profile_summary()
            adaptation_ai.adjust_difficulty(profile, profile_summary)
            adaptation_ai.adapt_world(rooms, profile, profile_summary)

            print(result)
            if game_state.current_room != previous_room:
                room = rooms[game_state.current_room]
                description = content_gen.generate_description(
                    room.name,
                    profile,
                    description=room.prose(),
                    items=[item.name for item in room.items],
                    profile_summary=profile_summary,
                    report=report,
                )
                print(game_state.get_current_description(rooms, description=description))

    print("\nCongratulations! You have defeated the giant and won the game!")


if __name__ == "__main__":
    main()
