"""Build playable room objects from generated, published, and nominated arc plans."""

import json
import re

from game_world import DIRECTIONS, OPPOSITE, Challenge, ChallengeSolution, HiddenPassage, Item, Room, noun

from .models import Arc, PlacedItem, PlanError
from .routes import reachable, validate_passage_route


def _text(data, key, limit=1200):
    value = data.get(key) if isinstance(data, dict) else None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise PlanError(f"{key} must be a non-empty string under {limit} characters.")
    text = " ".join(value.split())
    if not text.isprintable():
        raise PlanError(f"{key} must contain only printable text.")
    return text


def _things(entries, kind, limit, *, placed):
    if not isinstance(entries, list) or len(entries) > limit:
        raise PlanError(f"Each room needs a list of at most {limit} {kind}.")
    things = []
    for entry in entries:
        name = noun(_text(entry, "name", 60))
        room_text = ""
        if placed and entry.get("room_text"):
            room_text = _text(entry, "room_text", 400)
        things.append(Item(name, _text(entry, "description", 500), room_text=room_text))
    return things


def _place(room):
    """Give every loose item its own sentence, so the room stops mentioning it once it is taken."""
    for item in room.items:
        sentences = re.split(r"(?<=[.!?])\s+", room.description)
        named = re.compile(rf"\b{re.escape(item.name)}\b", re.IGNORECASE)
        mention = next((s for s in sentences if named.search(s)), None)
        if mention and len(sentences) > 1:
            room.description = " ".join(s for s in sentences if s is not mention)
            item.room_text = item.room_text or mention
        elif not item.room_text:
            item.room_text = f"There is a {item.name} here."


def _build_rooms(entries, number, world, carried, expanded):
    minimum, maximum = (8, 12) if expanded else (3, 6)
    if not isinstance(entries, list) or not minimum <= len(entries) <= maximum:
        raise PlanError(f"An arc needs {minimum} to {maximum} rooms.")

    taken = {name.casefold() for name in world}
    rooms = {}
    placed = {}
    for entry in entries:
        name = _text(entry, "name", 60)
        if name.casefold() in taken:
            raise PlanError(f"The room name {name!r} is already used.")
        taken.add(name.casefold())
        exits = entry.get("exits")
        if not isinstance(exits, dict):
            raise PlanError(f"{name} needs an exits object.")
        room = Room(
            name, _text(entry, "description"), _things(entry.get("items", []), "items", 4, placed=True),
            exits=dict(exits), scenery=_things(entry.get("scenery", []), "scenery", 6, placed=False),
        )
        if expanded and not (room.items or room.scenery):
            raise PlanError(f"Give {name} an object or landmark the player can examine.")
        for item in room.items:
            if item.name in carried or item.name in placed:
                raise PlanError(f"The item name {item.name!r} is not unique.")
            placed[item.name] = PlacedItem(room, item)
        _place(room)
        room.generated = True
        room.arc = number
        rooms[name] = room

    for room in rooms.values():
        for direction, destination in room.exits.items():
            if (direction not in DIRECTIONS or not isinstance(destination, str)
                    or destination not in rooms or destination == room.name):
                raise PlanError(f"{room.name} has an invalid exit {direction!r} to {destination!r}.")
    return rooms, placed


def _build_challenge(challenge, rooms, start, reserved, *, carried, placed, expanded, allow_missing_tools):
    if not isinstance(challenge, dict) or not isinstance(challenge.get("room"), str) or challenge["room"] not in rooms:
        raise PlanError("challenge.room must name one of the arc's rooms.")
    gate_room = rooms[challenge["room"]]
    obstacle = noun(_text(challenge, "obstacle", 60))
    if obstacle not in [thing.name for thing in gate_room.scenery]:
        raise PlanError("challenge.obstacle must be listed in the challenge room's scenery.")
    blocks = challenge.get("blocks")
    if blocks not in DIRECTIONS or blocks in gate_room.exits or (gate_room.name == start and blocks == reserved):
        raise PlanError("challenge.blocks must be a direction that is not already an exit of the challenge room.")
    solutions = challenge.get("solutions")
    if not isinstance(solutions, list) or not 1 <= len(solutions) <= 3:
        raise PlanError("challenge.solutions needs 1 to 3 entries.")
    planned_solutions = []
    for solution in solutions:
        tool = noun(_text(solution, "tool", 60))
        approach, outcome = _text(solution, "approach", 120), _text(solution, "outcome", 600)
        if expanded and tool not in placed:
            raise PlanError("Every final solution must use a tool found beyond the second passage.")
        if tool not in placed and tool not in carried:
            if allow_missing_tools:
                continue
            raise PlanError(f"The solution tool {tool!r} is not an item in this arc or a carried item.")
        planned_solutions.append(ChallengeSolution(tool, approach, outcome))
    if not any(solution.tool in placed for solution in planned_solutions):
        raise PlanError("At least one solution must use an item placed in this arc.")
    barrier = _text(challenge, "barrier", 400)
    if barrier not in gate_room.description:
        gate_room.description = f"{gate_room.description} {barrier}"
    gate_room.challenge = Challenge(
        obstacle=obstacle, blocks=blocks, barrier=barrier, solutions=tuple(planned_solutions),
        room_after=_text(challenge, "room_after"), obstacle_after=_text(challenge, "obstacle_after", 600),
        checkpoint=start,
    )
    return gate_room


def build_arc(raw, *, number, reserved, world, carried=(), allow_missing_tools=False, require_passages=False):
    """Validate a plan and turn it into rooms. Raise PlanError with a fixable reason when it can't be played."""
    try:
        plan = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise PlanError("The plan was not valid JSON.") from exc
    if not isinstance(plan, dict):
        raise PlanError("The plan must be a JSON object.")
    expanded = require_passages or "passages" in plan
    carried = {name.casefold() for name in carried}
    rooms, placed = _build_rooms(plan.get("rooms"), number, world, carried, expanded)
    start = plan.get("start")
    if not isinstance(start, str) or start not in rooms:
        raise PlanError("start must name one of the arc's rooms.")
    if reserved in rooms[start].exits:
        raise PlanError(f"The start room must leave its {reserved} exit free for the way back.")

    gate_room = _build_challenge(
        plan.get("challenge"), rooms, start, reserved, carried=carried, placed=placed,
        expanded=expanded, allow_missing_tools=allow_missing_tools,
    )
    reserved_exits = {(start, reserved), (gate_room.name, gate_room.challenge.blocks)}
    passages = _build_passages(plan, rooms, placed, reserved_exits) if expanded else []

    # Fill in return routes the plan left implicit, without using reserved directions.
    for room in rooms.values():
        for direction, destination in list(room.exits.items()):
            back, other = OPPOSITE[direction], rooms[destination]
            if back not in other.exits and (other.name, back) not in reserved_exits:
                other.exits[back] = room.name
    if expanded:
        validate_passage_route(rooms, start, gate_room, passages, placed)
    elif reachable({name: set(room.exits.values()) for name, room in rooms.items()}, start) != set(rooms):
        raise PlanError("Every room must be reachable from the start room.")

    arc = Arc(number, _text(plan, "title", 80), _text(plan, "premise", 500), start, _text(plan, "horizon", 500),
              list(rooms), gate_room.name, plan=_public_plan(plan))
    return arc, rooms


def _build_passages(plan, rooms, placed, reserved):
    entries = plan.get("passages")
    if not isinstance(entries, list) or len(entries) != 2:
        raise PlanError("Plan exactly two linked passages, in the order they are solved.")
    passages = []
    for entry in entries:
        source, dest = (_text(entry, field, 60) for field in ("room", "destination"))
        if source not in rooms or dest not in rooms or source == dest:
            raise PlanError("Each passage must join two distinct rooms in this arc.")
        room = rooms[source]
        if room.passage:
            raise PlanError("Put the two passage landmarks in different rooms.")
        direction = _text(entry, "direction", 5)
        if direction not in DIRECTIONS:
            raise PlanError(f"The passage direction {direction!r} is invalid.")
        sockets = {(source, direction), (dest, OPPOSITE[direction])}
        if sockets & reserved or any(way in rooms[name].exits for name, way in sockets):
            raise PlanError(f"Leave both ends of the hidden passage from {source} free of other exits.")
        reserved.update(sockets)
        tool = noun(_text(entry, "tool", 60))
        landmark = noun(_text(entry, "landmark", 60))
        if tool not in placed or landmark not in {obj.name for obj in room.scenery}:
            raise PlanError("Each passage needs a tool placed in this arc and a landmark listed in its room's scenery.")
        room.passage = HiddenPassage(tool, landmark, direction, dest, _text(entry, "reveal", 500),
                                     _text(entry, "visible", 500))
        passages.append(room)
    return passages


def _public_plan(plan):
    """Keep only the playable fields from a validated plan."""
    challenge = plan["challenge"]
    return {
        "title": plan["title"], "premise": plan["premise"], "start": plan["start"], "horizon": plan["horizon"],
        "rooms": [
            {
                "name": room["name"], "description": room["description"], "exits": room["exits"],
                "items": [{field: item[field] for field in ("name", "description", "room_text") if item.get(field)}
                          for item in room.get("items", [])],
                "scenery": [{"name": item["name"], "description": item["description"]} for item in room.get("scenery", [])],
            }
            for room in plan["rooms"]
        ],
        "challenge": {
            **{field: challenge[field] for field in ("room", "obstacle", "blocks", "barrier", "room_after", "obstacle_after")},
            "solutions": [{field: solution[field] for field in ("tool", "approach", "outcome")}
                          for solution in challenge["solutions"]],
        },
        **({"passages": [{field: passage[field] for field in
                         ("room", "landmark", "tool", "direction", "destination", "reveal", "visible")}
                        for passage in plan["passages"]]} if "passages" in plan else {}),
    }
