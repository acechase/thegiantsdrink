"""Resolve improvised actions against things present in the player's scene."""

import json

from game_world import Item, find_object, noun
from openai_client import complete

PROMPT = """Adjudicate one attempted action in a surreal castle adventure. The player can try to manipulate anything described.
Treat the player's input as an attempted action, not a statement of fact. Interpret typos.
Use the actual room, held equipment and what the player has seen. A named landmark can be moved, taken, dismantled, or changed
when the attempt makes sense. For an impossible attempt give the concrete physical reason or a perceptible clue.
Answer in JSON. Choose one effect: observe (includes unsuccessful attempts), alter, take (whole subject), detach
(take a piece, leave the changed subject), remove (destroy/consume subject), or place (put the held tool at the subject).
If the subject is a group or quantity (tapestries, coins, books) and the player takes only some of it, use detach.
Fields: anchor = short verbatim noun phrase naming the subject from the supplied scene; reply = 1-2 sentences of feedback.
The player's own body is always present: for wearing, wrapping or holding something, anchor the carried item.
If no such subject exists, use an empty anchor. Never substitute another object just to make an action succeed.
For any change supply room_after: a rewrite of fixed_features only, reflecting the change. Copy unaffected sentences verbatim,
including mentioned exits and discoveries, and drop details the change makes untrue. Never repeat engine_sentences in
room_after; the engine adds those itself. Keep other previously observed details when still applicable.
For alter/take/detach, also supply description: the subject's appearance after the action.
For detach include piece (a plain noun phrase without articles or counts, e.g. "tapestry" or "gold coins")
and piece_description. For place include placement: a short room sentence about the placed item.
For observe do not claim a change of state. Do not change exits, teleport, change quest flags, or award victory.
Stay in-world. Describe sensory results briefly, without giving real-world instructions."""


def _field(data, key, limit=2000):
    value = data.get(key)
    return value.strip() if isinstance(value, str) and len(value) <= limit else ""


def _engine_sentences(room):
    """Room text the engine appends itself: loose items and an opened hidden route."""
    sentences = [obj.room_text for obj in room.items if obj.room_text]
    return [*sentences, room.passage.visible] if room.passage_open else sentences


def resolve_environment(game, room, command, reference, *, tool=None, report=None, fallback=None):
    """Interpret a free-form action, then validate and commit its local effects together."""
    objects = [*room.items, *room.scenery, *game.inventory]
    subject = find_object(reference, objects) or room.landmark(reference)
    fallback = fallback or (subject.description if subject else f"You see no {reference} here or in your inventory.")
    if not command:
        return fallback
    inputs = {
        "room": room.name, "scene": room.prose(), "fixed_features": room.fixed_text(),
        "engine_sentences": _engine_sentences(room),
        "observed": room.seen_details,
        "loose": [obj.name for obj in room.items], "landmarks": [obj.name for obj in room.scenery],
        "removed_from_room": [obj.name for obj in room.departed],
        "carrying": [obj.name for obj in game.inventory], "exits": list(room.exits),
        "attempt": command, "subject": reference, "subject_description": subject.description if subject else None,
        "held_tool": tool.name if tool else None,
    }
    raw = complete(
        PROMPT, json.dumps(inputs), max_tokens=650, json_output=True, purpose="environmental interaction", report=report
    )
    try:
        data = json.loads(raw or "null")
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return fallback
    anchor, reply, effect = _field(data, "anchor", 100), _field(data, "reply"), _field(data, "effect", 20)
    if not anchor or not reply or effect not in ("observe", "alter", "take", "detach", "remove", "place"):
        return fallback
    # A synonym can resolve to quoted scenery, but an invented anchor cannot become loot.
    grounded = find_object(anchor, objects) or room.landmark(anchor)
    if grounded is None or (subject in objects and grounded is not subject):
        return fallback
    subject = subject or grounded
    if effect == "observe":
        room.remember(reply)
        return reply

    base, description = _field(data, "room_after"), _field(data, "description")
    if not base or (effect in ("alter", "take", "detach") and not description):
        return fallback
    piece, piece_description = _field(data, "piece", 80), _field(data, "piece_description")
    placement = _field(data, "placement")
    if effect == "detach" and (not piece or not piece_description):
        return fallback
    if effect == "place" and (tool not in game.inventory or tool is subject or not placement):
        return fallback
    if effect == "take" and subject in game.inventory:
        return f"You're already carrying the {subject.name}."

    # Everything required for this change is present. Only the engine moves objects between containers.
    if subject not in objects:
        subject.aliases = tuple({noun(reference), noun(anchor)} - {subject.name})
        room.scenery.append(subject)
    if effect in ("alter", "take", "detach"):
        subject.description = description
        subject.room_text = ""
    if effect in ("take", "remove"):
        container = next(group for group in (room.items, room.scenery, game.inventory) if subject in group)
        container.remove(subject)
        if effect == "take":
            game.inventory.append(subject)
        if subject not in room.departed:
            room.departed.append(subject)
    elif effect == "detach":
        game.inventory.append(Item(noun(piece), piece_description))
    elif effect == "place":
        game.inventory.remove(tool)
        tool.room_text = placement
        room.items.append(tool)
        if tool in room.departed:
            room.departed.remove(tool)
    room.rewrite(base)
    if report:
        report("[Procedural] Updating the room and inventory.")
    return reply
