# command_parser.py
import json
import re
from typing import NamedTuple

from game_world import find_object, noun
from openai_client import complete


class ToolUse(NamedTuple):
    """A carried tool and the intended subject of an action."""

    tool: str
    subject: str


SYSTEM_PROMPT = """Interpret one player's command for a castle exploration game. Return only a JSON object.
Actions: move, take, use, use_on, examine, attack, check, look, help, quit, unknown.
Use "look" with target "around" only for the whole room. Looking at, inspecting or examining an object is "examine".
For "take", "use", "examine", or "attack", target is the object name. For "check", target is "inventory".
For "move", target is north, south, east, west, up or down. For "help" and "quit", use "commands" and "game".
When the player uses an object to do something to another object, preserve BOTH nouns, even if the attempt may fail.
Examples: "listen for hollow stone by tapping it with my staff" -> {"action":"use_on","tool":"staff","target":"stone wall"}
"read the book" -> {"action":"use","target":"book"}; "study the book's cover" -> {"action":"examine","target":"book"}.
Use the supplied scene to resolve references to objects. Keep missing objects as requested. Never invent a successful outcome.
If you cannot identify an action, return {"action":"unknown","target":""}."""

command_cache = {}
SIMPLE_COMMANDS = {
    "look": ("look", "around"),
    "look around": ("look", "around"),
    "inventory": ("check", "inventory"),
    "help": ("help", "commands"),
    "quit": ("quit", "game"),
    "exit": ("quit", "game"),
}


def _direct_command(command, objects):
    """Resolve common unambiguous commands immediately."""
    text = " ".join(command.casefold().strip(" .!?").split())
    if text in SIMPLE_COMMANDS:
        return SIMPLE_COMMANDS[text]
    match = re.fullmatch(r"(?:go |move )?(north|south|east|west|up|down)", text)
    if match:
        return "move", match[1]
    match = re.fullmatch(r"(look at|examine|inspect|x|pick up|take|get|use) (.+)", text)
    if not match:
        return None
    verb, reference = match.groups()
    if verb == "use" and " on " in reference:
        tool, subject = reference.split(" on ", 1)
        if find_object(tool, objects) and find_object(subject, objects):
            return "use_on", ToolUse(noun(tool), noun(subject))
    obj = find_object(reference, objects)
    if obj:
        if verb in ("pick up", "take", "get"):
            return "take", obj.name
        if verb == "use":
            return "use", obj.name
        return "examine", obj.name
    return None


def parse_command(command, room=None, inventory=()):
    """Resolve ordinary verbs locally and ask OpenAI about free-form commands.

    Model interpretations can depend on the room, visible objects and held tools.
    The simulation checks the resulting intent before it changes any game state.
    """
    objects = [*inventory, *room.items, *room.scenery] if room else list(inventory)
    direct = _direct_command(command, objects)
    if direct:
        return direct

    context = ""
    if room:
        scene = {
            "room": room.name,
            "description": room.prose(),
            "exits": list(room.exits),
            "items": [obj.name for obj in room.items],
            "scenery": [obj.name for obj in room.scenery],
            "carrying": [obj.name for obj in inventory],
        }
        context = "\nCurrent scene: " + json.dumps(scene)
    cache_key = (command, context)
    if cache_key in command_cache:
        return command_cache[cache_key]
    generated = complete(SYSTEM_PROMPT + context, command, max_tokens=100, json_output=True)
    if not generated:
        return None, None
    action, target = extract_action_target(generated)
    if action and target:
        command_cache[cache_key] = (action, target)
    return action, target


def extract_action_target(text):
    """Extract action and target from the LLM's JSON response text.

    Args:
        text (str): Raw LLM output expected to contain a JSON object
            with ``action`` and ``target`` keys.

    Returns:
        A pair containing an action and its target (a ToolUse for use_on),
        or (None, None) if the intent is invalid.
    """
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            data = json.loads(text[start:end])
            action = data.get("action")
            target = data.get("target")
            if action == "use_on":
                tool = data.get("tool")
                if isinstance(tool, str) and isinstance(target, str) and noun(tool) and noun(target):
                    return action, ToolUse(noun(tool), noun(target))
                return None, None
            valid_actions = {"move", "take", "use", "attack", "check", "look", "examine", "help", "quit"}
            if isinstance(action, str) and action in valid_actions and isinstance(target, str) and target:
                return action, target.strip()
        return None, None
    except json.JSONDecodeError:
        return None, None
