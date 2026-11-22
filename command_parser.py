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
Actions: move, take, drop, use, use_on, examine, search, interact, attack, check, look, self, help, quit, trial, unknown.
Use "look" with target "around" only for the whole room. Looking at, inspecting or examining an object is "examine".
For "take", "use", "examine", or "attack", target is the object name. For "check", target is "inventory".
Use "self" with target "appearance" when the player looks at or asks about their own appearance, body or outfit.
For "move", target is north, south, east, west, up or down. For "help" and "quit", use "commands" and "game".
When the player uses an object to do something to another object, preserve BOTH nouns, even if the attempt may fail.
Examples: "listen for hollow stone by tapping it with my staff" -> {"action":"use_on","tool":"staff","target":"stone wall"}
"read the book" -> {"action":"use","target":"book"}; "study the book's cover" -> {"action":"examine","target":"book"}.
Acting on yourself with an item is "use": "wrap the cloak around my shoulders" -> {"action":"use","target":"cloak"}.
Use "search" for a deliberate search of a named object, "drop" to set down a carried item.
Use "interact" with the subject as target for other intentional acts such as turning, pulling, bending or humming at something.
Interpret misspellings. Use the supplied scene to resolve references, including details in prose.
Reserve "attack" for an adversary. Keep missing objects as requested. Never invent a successful outcome.
If the scene supplies trial_intents, use action "trial" and the matching exact key only when the player actually
attempts that action. Questions, inspecting a cup, hypothetical actions, negations and tool experiments are never
consent to drink. If the player declines to drink and performs another action, interpret the action they perform.
If you cannot identify an action, return {"action":"unknown","target":""}."""

command_cache = {}
SIMPLE_COMMANDS = {
    "look": ("look", "around"),
    "look around": ("look", "around"),
    "look around the room": ("look", "around"),
    "remember": ("remember", "lessons"),
    "lessons": ("remember", "lessons"),
    "inventory": ("check", "inventory"),
    **{phrase: ("self", "appearance") for phrase in (
        "me", "myself", "self", "portrait", "look at me", "look at myself", "look at yourself", "examine me",
        "examine myself", "examine yourself", "x me", "x myself", "inspect myself", "look myself over",
        "what do i look like", "how do i look",
    )},
    "help": ("help", "commands"),
    "quit": ("quit", "game"),
    "exit": ("quit", "game"),
}


def direct_command(command, objects):
    """Resolve common unambiguous commands immediately."""
    text = " ".join("the" if word == "teh" else word for word in command.casefold().strip(" .!?").split())
    if text in SIMPLE_COMMANDS:
        return SIMPLE_COMMANDS[text]
    match = re.fullmatch(r"(?:go |move )?(north|south|east|west|up|down)", text)
    if match:
        return "move", match[1]
    if (re.match(r"(?:can (?:i|we) )?(?:make|craft|fashion|create|sew|fold|knot|tie|turn|use)\b", text)
            and re.search(r"\btapestr(?:y|ies)\b", text)
            and re.search(r"\b(?:make|craft|fashion|create|sew|into) (?:a |the )?"
                          r"(?:(?:makeshift|tapestry) )?(?:bag|sack|pouch)\b",
                          text)):
        return "craft_bag", "tapestry"
    for verb, pattern in (
        ("pack", r"(?:put|place|pack|store|stow) (.+?) (?:in|into|inside) (.+)"),
        ("unpack", r"(?:take|remove|retrieve|get) (.+?) (?:out of|from) (.+)"),
    ):
        match = re.fullmatch(pattern, text)
        if match:
            item, container = match.groups()
            bag = find_object(container, objects)
            if (bag and bag.contents is not None) or noun(container) in ("bag", "sack", "tapestry bag", "makeshift bag"):
                return verb, ToolUse(noun(item), noun(container))
    match = re.fullmatch(r"(?:look|check) (?:in|inside) (.+)", text)
    if match:
        bag = find_object(match[1], objects)
        if bag and bag.contents is not None:
            return "examine", bag.name
    match = re.fullmatch(r"(look at|examine|inspect|x|pick up|take|get|drop|search|use) (.+)", text)
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
        if verb in ("use", "drop", "search"):
            return verb, obj.name
        return "examine", obj.name
    return None


def parse_command(command, room=None, inventory=(), *, report=None):
    """Resolve ordinary verbs locally and ask OpenAI about free-form commands.

    Model interpretations can depend on the room, visible objects and held tools.
    The simulation checks the resulting intent before it changes any game state.
    """
    objects = [*inventory, *room.items, *room.scenery] if room else list(inventory)
    trial = room.trial if room else None
    if trial:
        intent = trial.interpret(" ".join(command.casefold().strip(" .!").split()))
        if intent:
            return "trial", intent

    direct = direct_command(command, objects)
    if direct:
        return direct

    context = ""
    if room:
        scene = {
            "room": room.name,
            "description": room.visible_text(),
            "exits": room.visible_exits,
            "items": [obj.name for obj in room.items],
            "scenery": [obj.name for obj in room.scenery],
            "carrying": [obj.name for obj in inventory],
        }
        if trial:
            scene["trial_intents"] = trial.intents
        context = "\nCurrent scene: " + json.dumps(scene)
    cache_key = (command, context)
    if cache_key in command_cache:
        if report is not None:
            report("[Cached AI] Reusing command interpretation.")
        return command_cache[cache_key]
    generated = complete(
        SYSTEM_PROMPT + context, command, max_tokens=100, json_output=True, purpose="command interpretation", report=report
    )
    if not generated:
        return None, None
    action, target = extract_action_target(generated)
    if action == "trial" and (not trial or target not in trial.intents):
        return None, None
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
            valid_actions = {
                "move", "take", "drop", "use", "attack", "check", "look", "examine", "search",
                "interact", "help", "quit", "trial", "self"
            }
            if isinstance(action, str) and action in valid_actions and isinstance(target, str) and target:
                return action, target.strip()
        return None, None
    except json.JSONDecodeError:
        return None, None
