# command_parser.py
import json

from openai_client import complete

SYSTEM_PROMPT = """You are a command parser for a text adventure game called The Giant's Drink.
The player explores a castle, collects items, and faces a giant.

Valid actions: move, take, use, attack, check, look, help, quit
Valid move targets: north, south, east, west, up, down
Valid check targets: inventory

Parse the player's natural language input into a JSON object with exactly two fields:
  "action": one of [move, take, use, attack, check, look, help, quit]
  "target": the target of the action (use "around" for look, "commands" for help, "game" for quit)

Examples:
- "go north" -> {"action": "move", "target": "north"}
- "take rusty sword" -> {"action": "take", "target": "rusty sword"}
- "drink potion" -> {"action": "use", "target": "potion"}
- "attack giant" -> {"action": "attack", "target": "giant"}
- "inventory" -> {"action": "check", "target": "inventory"}
- "look" -> {"action": "look", "target": "around"}
- "look around" -> {"action": "look", "target": "around"}
- "help" -> {"action": "help", "target": "commands"}
- "quit" -> {"action": "quit", "target": "game"}
- "exit" -> {"action": "quit", "target": "game"}

Respond with ONLY the JSON object, no other text."""

# Cache to store parsed results for performance
command_cache = {}


def parse_command(command):
    """Parse a player's natural language command using OpenAI.

    Caches results per input string to avoid redundant API calls for repeated
    inputs.  Falls back to ``(None, None)`` on network error or parse failure.

    Args:
        command (str): The player's raw input (e.g. "take the rusty sword").

    Returns:
        tuple[str, str] | tuple[None, None]: ``(action, target)`` on success,
            or ``(None, None)`` if parsing fails.
    """
    if command in command_cache:
        return command_cache[command]

    generated_text = complete(SYSTEM_PROMPT, command, max_tokens=100, json_output=True)
    if not generated_text:
        return None, None
    action, target = extract_action_target(generated_text)
    if action and target:
        command_cache[command] = (action, target)
    return action, target


def extract_action_target(text):
    """Extract action and target from the LLM's JSON response text.

    Args:
        text (str): Raw LLM output expected to contain a JSON object
            with ``action`` and ``target`` keys.

    Returns:
        tuple[str, str] | tuple[None, None]: ``(action, target)`` if valid,
            otherwise ``(None, None)``.
    """
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            data = json.loads(text[start:end])
            action = data.get("action")
            target = data.get("target")
            valid_actions = {"move", "take", "use", "attack", "check", "look", "help", "quit"}
            if isinstance(action, str) and action in valid_actions and isinstance(target, str) and target:
                return action, target
        return None, None
    except json.JSONDecodeError:
        return None, None
