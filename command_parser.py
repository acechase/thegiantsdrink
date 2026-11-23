# command_parser.py
import json
import re
from typing import NamedTuple

from game_world import noun
from game_world.gear import ATTACHMENT_FEATURES, ATTACHMENT_FORMS, STORAGE_FEATURES, STORAGE_FORMS, walk_items
from game_world.names import TAPESTRY_BAG_NAMES, matching_objects
from openai_client import complete


class ToolUse(NamedTuple):
    """A carried tool and the intended subject of an action."""

    tool: str
    subject: str


SYSTEM_PROMPT = """Interpret one player's command for a castle exploration game. Return only a JSON object.
Actions: move, take, drop, use, use_on, craft, pack, unpack, attach, unfasten, examine, search, interact,
attack, check, look, self, help, quit, trial, unknown.
Use "look" with target "around" only for the whole room. Looking at, inspecting or examining an object is "examine".
For "take", "use", "examine", or "attack", target is the object name. For "check", target is "inventory".
Use "self" with target "appearance" when the player looks at or asks about their own appearance, body or outfit.
For "move", target is north, south, east, west, up or down. For "help" and "quit", use "commands" and "game".
When the player uses an object to do something to another object, preserve BOTH nouns, even if the attempt may fail.
For inventory moves use pack, unpack, attach or unfasten with tool = item and target = host item.
For fashioning storage or attachment points use craft with tool = source material or item being modified,
target = the proposed result (e.g. "basket" or "belt loop"). Use interact for other improvised alterations.
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
CRAFT_FORMS = STORAGE_FORMS | ATTACHMENT_FORMS | STORAGE_FEATURES | ATTACHMENT_FEATURES | {"carrier", "container", "holder"}
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


def craft_command(text):
    """Recognize a proposed form and its source; feasibility belongs to the simulation."""
    text = re.sub(r"^can (?:i|we) ", "", text)
    match = re.fullmatch(r"use (.+?) to (?:make|craft|fashion|create|sew) (.+)", text)
    if match:
        material, product = match.groups()
    else:
        match = re.fullmatch(r"(?:make|craft|fashion|create|stitch|sew|weave|fold|tie|braid|knot) (.+?) "
                             r"(?:out of|from|onto|on) (.+)", text)
        if match:
            product, material = match.groups()
        else:
            match = re.fullmatch(r"(?:turn|make|craft|fashion|sew|weave|fold|braid|knot|tie) (.+?) into (.+)", text)
            if not match:
                return None
            material, product = match.groups()
    product = noun(product.split(" for ", 1)[0])
    material = noun(material)
    if material.split(" #", 1)[0] in ("tapestry", "tapestries") and product in TAPESTRY_BAG_NAMES:
        return "craft_bag", material
    if product.rsplit(" ", 1)[-1] in CRAFT_FORMS:
        return "craft", ToolUse(material, product)
    return None


def direct_command(command, objects, inventory=()):
    """Resolve common unambiguous commands immediately."""
    text = " ".join("the" if word == "teh" else word for word in command.casefold().strip(" .!?").split())
    if text in SIMPLE_COMMANDS:
        return SIMPLE_COMMANDS[text]
    match = re.fullmatch(r"(?:go |move )?(north|south|east|west|up|down)", text)
    if match:
        return "move", match[1]
    crafting = craft_command(text)
    if crafting:
        return crafting
    for verb, pattern in (
        ("pack", r"(?:put|place|pack|store|stow) (.+?) (?:in|into|inside) (.+)"),
        ("unpack", r"(?:take|remove|retrieve|get) (.+?) out of (.+)"),
        ("retrieve", r"(?:take|remove|retrieve|get) (.+?) from (.+)"),
        ("attach", r"(?:attach|clip|fasten|hang|strap|tie|hook|buckle|lash) (.+?) (?:to|onto|on|from) (.+)"),
        ("unfasten", r"(?:detach|unclip|unfasten|unhook|untie|unstrap|take) (.+?) (?:off of|off|from) (.+)"),
    ):
        match = re.fullmatch(pattern, text)
        if match:
            item, container = match.groups()
            hosts = matching_objects(container, objects)
            if (any(host.contents is not None or host.attachments is not None or host in inventory for host in hosts)
                    or noun(container).rsplit(" ", 1)[-1] in CRAFT_FORMS):
                return verb, ToolUse(noun(item), noun(container))
    match = re.fullmatch(r"(?:look|check) (?:in|inside) (.+)", text)
    if match:
        if any(bag.contents is not None for bag in matching_objects(match[1], objects)):
            return "examine", noun(match[1])
    match = re.fullmatch(r"(look at|examine|inspect|x|pick up|take|get|drop|search|use) (.+)", text)
    if not match:
        return None
    verb, reference = match.groups()
    if verb == "use" and " on " in reference:
        tool, subject = reference.split(" on ", 1)
        if matching_objects(tool, objects) and matching_objects(subject, objects):
            return "use_on", ToolUse(noun(tool), noun(subject))
    if matching_objects(reference, objects):
        if verb in ("pick up", "take", "get"):
            return "take", noun(reference)
        if verb in ("use", "drop", "search"):
            return verb, noun(reference)
        return "examine", noun(reference)
    return None


def parse_command(command, room=None, inventory=(), *, report=None):
    """Resolve ordinary verbs locally and ask OpenAI about free-form commands.

    Model interpretations can depend on the room, visible objects and held tools.
    The simulation checks the resulting intent before it changes any game state.
    """
    objects = list(walk_items([*inventory, *room.items, *room.scenery] if room else inventory))
    trial = room.trial if room else None
    if trial:
        intent = trial.interpret(" ".join(command.casefold().strip(" .!").split()))
        if intent:
            return "trial", intent

    direct = direct_command(command, objects, inventory)
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
            "gear": [{"item": obj.name, "inside": [i.name for i in obj.contents or ()],
                      "attached": [i.name for i in obj.attachments or ()]} for obj in objects
                     if obj.contents is not None or obj.attachments is not None],
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
            if action in ("use_on", "craft", "pack", "unpack", "attach", "unfasten"):
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
