"""Plan endless AI-generated story arcs that continue after the giant falls.

Each new arc is planned before the player enters it. Its branches contain a
sequence of tool-operated passages and a final obstacle. The engine validates
the whole route, including the rooms reachable at each puzzle stage.
"""

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field

from command_parser import ToolUse, direct_command
from game_world import DIRECTIONS, OPPOSITE, Challenge, HiddenPassage, Item, Room, key, noun
from openai_client import complete
from story_catalog import Catalog, StoryError, plan_id

CASTLE_HORIZON = (
    "Behind the empty throne, a crack has opened in the castle wall. Through it you glimpse a country that "
    "was not there before, as if the game itself has only just imagined it."
)

PROMPT = """You plan the next arc of an endless adventure that began in the castle of The Giant's Drink.
The world is a surreal, dreamlike game that quietly studies the player and reshapes itself in response.
Plan an arc of 8 to 12 rooms with branching exploration, two linked hidden passages, and a final obstacle.
- Begin where the supplied horizon promised. Keep continuity with earlier arcs, the chronicle and carried items.
- Read the player's profile and recent commands. Make this challenge test something their habits have not:
  if they rely on force, let force alone fail; if they are cautious, demand a bold act; if they hoard, ask for a gift.
- Begin with at least four connected rooms and a junction with three ways to explore, excluding the way back to the
  previous arc. Both passage landmarks should be accessible from the start, in different rooms. Put the first passage's
  tool in another accessible room. Behind the first passage place the second passage's distinct tool, so the player
  returns to explore the other branch. Behind the second passage put the final obstacle and its solution tools in
  different rooms. None of these rewards should have a route around the passage meant to protect them.
- Give each room a distinct purpose, landmark or discovery. Plant sensory clues that connect the tools with their
  landmarks across rooms, so players can infer what to try. Use short, recognizable names without ' on ' in them.
- Give 1 to 3 planned solutions to the final obstacle. Each uses a tool reached through the second passage.
  Clever unplanned solutions may also succeed at the final obstacle.
- The player can examine, take or change anything you describe, so describe concrete, tangible things.
  Mention each item only in its room_text, never in the room description: the engine shows room_text while it's there.
  Mention only exits that exist. Describe a closed passage through its landmark, not as an open exit.
  Make the barrier show a tangible danger and a subtle clue to surviving it.
  Put the barrier sentence in the challenge room's description.
- Escalate gradually. Stay original and in-world, with no talk of game mechanics.
Return only JSON with this shape:
{"title": str, "premise": "1-2 sentences shown on arrival, hinting at what must be overcome but not how",
 "start": room name,
 "rooms": [{"name": str, "description": "2-4 sentences", "exits": {"<direction>": room name},
            "items": [{"name": "short noun phrase", "description": str, "room_text": "sentence placing it in the room"}],
            "scenery": [{"name": "noun phrase used in the description", "description": str}]}],
 "passages": [{"room": room containing the landmark, "landmark": scenery name, "tool": local item name,
               "direction": direction from room to destination, "destination": room name,
               "reveal": "what happens when the tool opens it", "visible": "how the opened route looks"}],
 "challenge": {"room": room name, "obstacle": scenery name in that room, "blocks": direction it bars,
               "barrier": "sentence describing how it bars the way",
               "solutions": [{"tool": item name, "approach": "a few words",
                              "outcome": "1-2 sentences narrating, in the second person, what happens"}],
               "room_after": "the challenge room's description once the obstacle is overcome",
               "obstacle_after": "the obstacle's appearance once overcome"},
 "horizon": "1-2 sentences glimpsed beyond the obstacle once it is overcome; the next arc begins there"}
Directions are north, south, east, west, up and down. Room names must be new. The start room's reserved_start_exit
leads back the way the player came: do not use it. The challenge's blocks direction must not be another exit of
the challenge room. Give exactly two passages, in puzzle order. Omit both directions of each hidden passage from
rooms[].exits. Leave both of those directions free for the engine to connect on opening. Normal exits should allow
backtracking. Item names must be unique and must differ from carried items."""


class PlanError(ValueError):
    """A generated arc plan that cannot be played as written."""


@dataclass
class Arc:
    """A planned arc and the player's progress through it."""

    number: int
    title: str
    premise: str
    start: str
    horizon: str
    rooms: list
    challenge_room: str
    entered: bool = False
    plan: dict = None

    def banner(self):
        """Introduce the arc the first time the player arrives."""
        return f"── Arc {self.number}: {self.title} ──\n{self.premise}"


@dataclass
class Saga:
    """The chain of arcs that follows the castle, planned one arc at a time."""

    arcs: list = field(default_factory=list)
    chronicle: list = field(default_factory=list)
    gate: tuple = None  # (room name, direction) of a way onward still waiting for its arc
    horizon: str = ""
    edition: str = ""
    preferred_plans: tuple = ()
    catalog_path: str = None
    reviewing: bool = False

    @property
    def reached_arcs(self):
        """Return the contiguous route through the last entered arc."""
        return [arc for arc in self.arcs if arc.entered]

    @property
    def review_complete(self):
        """Tell whether a playtest has reached its final crossing."""
        return self.reviewing and len(self.arcs) == len(self.preferred_plans)

    @property
    def started(self):
        """Whether the castle has given way to generated arcs."""
        return bool(self.gate or self.arcs)

    def record(self, entry):
        """Remember a deed that later arcs can build on."""
        self.chronicle.append(entry)

    def begin(self, world, deed):
        """Turn the giant's defeat into the first way onward."""
        hall = world["Giant's Hall"]
        direction = next(d for d in ("north", "up", "west", "east", "down", "south") if d not in hall.exits)
        hall.rewrite(
            "A cavernous hall stretches before you, its ceiling lost in darkness high above. The throne of twisted "
            "iron stands empty; a drift of grey dust is all that remains of the giant. Behind the throne, a crack "
            f"splits the castle wall, and a cold light leaks through it from the {direction}."
        )
        hall.trial = None
        hall.scenery = [obj for obj in hall.scenery if obj.name not in ("left cup", "right cup", "silver tray")]
        hall.items.append(deepcopy(key))
        giant = next((thing for thing in hall.scenery if thing.name == "giant"), None)
        if giant:
            giant.description = "A drift of grey dust in the rough shape of a sleeping man."
        for item in hall.items:
            if item.name == "key":
                item.room_text = "A heavy key lies in the dust where the giant fell."
        self.record(f"The Castle: {deed}")
        self.open_way(hall, direction, CASTLE_HORIZON)
        return direction

    def open_way(self, room, direction, horizon):
        """Mark a way onward that the next arc will fill."""
        self.gate = (room.name, direction)
        self.horizon = horizon

    def arrive(self, room_name):
        """Return an arc's banner the first time the player reaches its start."""
        for arc in self.arcs:
            if arc.start == room_name and not arc.entered:
                arc.entered = True
                return arc.banner()
        return None

    def _attach(self, world, arc, rooms):
        source, direction = self.gate
        world.update(rooms)
        world[source].exits[direction] = arc.start
        world[arc.start].exits[OPPOSITE[direction]] = source
        self.arcs.append(arc)
        self.gate = None
        return arc

    def _reuse(self, world, game, direction, report):
        index = len(self.arcs)
        for plan in self._candidates(direction, report):
            try:
                arc, rooms = build_arc(json.dumps(plan), number=index + 1, reserved=OPPOSITE[direction], world=world,
                                       carried=game.carried_names(), allow_missing_tools=True)
            except PlanError:
                continue
            if report is not None:
                report("[Replay] Using a published arc." if not self.reviewing else "[Review] Using a nominated arc.")
            return self._attach(world, arc, rooms)
        return None

    def _candidates(self, direction, report):
        route = self.preferred_plans
        index = len(self.arcs)
        preferred = None
        if index < len(route) and all(plan_id(arc.plan) == plan_id(route[i]) for i, arc in enumerate(self.arcs)):
            preferred = route[index]
            yield preferred
        if self.reviewing:
            return
        parent = plan_id(self.arcs[-1].plan) if self.arcs else ""
        try:
            yield from Catalog(self.catalog_path).continuations(parent, direction, skip=preferred)
        except StoryError:
            if report is not None:
                report("[Replay] The published story catalog could not be read.")

    def plan_next(self, world, game, *, profile_summary="", report=None, attempts=2):
        """Plan and attach the next arc at the open gate. Return the new Arc, or None to retry later."""
        if not self.gate:
            return None
        _, direction = self.gate
        reserved = OPPOSITE[direction]
        if self.edition or self.reviewing:
            reused = self._reuse(world, game, direction, report)
            if reused or self.reviewing:
                return reused
        request = {
            "arc_number": len(self.arcs) + 1,
            "horizon": self.horizon,
            "reserved_start_exit": reserved,
            "player_profile": profile_summary or "unknown",
            "chronicle": self.chronicle[-8:],
            "earlier_arcs": [arc.title for arc in self.arcs],
            "recent_commands": game.history[-30:],
            "remembered_lessons": game.lessons[-8:],
            "carried": [{"name": item.name, "description": item.description} for item in game.inventory],
            "existing_room_names": sorted(world),
        }
        feedback = ""
        for _ in range(attempts):
            raw = complete(PROMPT, json.dumps(request) + feedback, max_tokens=9000, json_output=True,
                           purpose="planning the next arc", report=report, read_timeout=300)
            if raw is None:
                return None
            try:
                arc, rooms = build_arc(raw, number=len(self.arcs) + 1, reserved=reserved, world=world,
                                       carried=game.carried_names(), require_passages=True)
            except PlanError as exc:
                if report is not None:
                    report(f"[Procedural] Rejected the arc plan: {exc}")
                feedback = f"\nYour previous plan was rejected: {exc} Return a corrected plan."
                continue
            return self._attach(world, arc, rooms)
        return None


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


def build_arc(raw, *, number, reserved, world, carried=(), allow_missing_tools=False, require_passages=False):
    """Validate a plan and turn it into rooms. Raise PlanError with a fixable reason when it can't be played."""
    try:
        plan = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise PlanError("The plan was not valid JSON.") from exc
    if not isinstance(plan, dict):
        raise PlanError("The plan must be a JSON object.")
    expanded = require_passages or "passages" in plan
    minimum, maximum = (8, 12) if expanded else (3, 6)
    entries = plan.get("rooms")
    if not isinstance(entries, list) or not minimum <= len(entries) <= maximum:
        raise PlanError(f"An arc needs {minimum} to {maximum} rooms.")

    taken = {name.casefold() for name in world}
    rooms, item_names, placed = {}, {name.casefold() for name in carried}, {}
    for entry in entries:
        name = _text(entry, "name", 60)
        if name.casefold() in taken:
            raise PlanError(f"The room name {name!r} is already used.")
        taken.add(name.casefold())
        room = Room(name, _text(entry, "description"), _things(entry.get("items", []), "items", 4, placed=True),
                    scenery=_things(entry.get("scenery", []), "scenery", 6, placed=False))
        if expanded and not (room.items or room.scenery):
            raise PlanError(f"Give {name} an object or landmark the player can examine.")
        for item in room.items:
            if item.name in item_names:
                raise PlanError(f"The item name {item.name!r} is not unique.")
            item_names.add(item.name)
            placed[item.name] = room.name, item
        _place(room)
        room.generated = True
        room.arc = number
        room.planned_exits = entry.get("exits")
        rooms[name] = room

    start = plan.get("start")
    if not isinstance(start, str) or start not in rooms:
        raise PlanError("start must name one of the arc's rooms.")
    for room in rooms.values():
        exits = room.planned_exits
        del room.planned_exits
        if not isinstance(exits, dict):
            raise PlanError(f"{room.name} needs an exits object.")
        for direction, destination in exits.items():
            if (direction not in DIRECTIONS or not isinstance(destination, str)
                    or destination not in rooms or destination == room.name):
                raise PlanError(f"{room.name} has an invalid exit {direction!r} to {destination!r}.")
            room.exits[direction] = destination
    if reserved in rooms[start].exits:
        raise PlanError(f"The start room must leave its {reserved} exit free for the way back.")

    challenge = plan.get("challenge")
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
    tools = []
    for solution in solutions:
        tool = noun(_text(solution, "tool", 60))
        approach, outcome = _text(solution, "approach", 120), _text(solution, "outcome", 600)
        if expanded and tool not in placed:
            raise PlanError("Every final solution must use a tool found beyond the second passage.")
        if tool not in item_names:
            if allow_missing_tools:
                continue
            raise PlanError(f"The solution tool {tool!r} is not an item in this arc or a carried item.")
        tools.append((tool, approach, outcome))
    if not any(tool in placed for tool, _, _ in tools):
        raise PlanError("At least one solution must use an item placed in this arc.")
    barrier = _text(challenge, "barrier", 400)
    if barrier not in gate_room.description:
        gate_room.description = f"{gate_room.description} {barrier}"
    gate_room.challenge = Challenge(obstacle, blocks, barrier, tuple(tools),
                                    _text(challenge, "room_after"), _text(challenge, "obstacle_after", 600), checkpoint=start)

    reserved_exits = {(start, reserved), (gate_room.name, blocks)}
    passages = _build_passages(plan, rooms, placed, reserved_exits) if expanded else []

    # Fill in return routes the plan left implicit, without using reserved directions.
    for room in list(rooms.values()):
        for direction, destination in list(room.exits.items()):
            back, other = OPPOSITE[direction], rooms[destination]
            if back not in other.exits and (other.name, back) not in reserved_exits:
                other.exits[back] = room.name
    if expanded:
        _validate_passage_route(rooms, start, gate_room, passages, placed)
    elif _reachable({name: set(room.exits.values()) for name, room in rooms.items()}, start) != set(rooms):
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


def _reachable(links, origin):
    reached, frontier = {origin}, [origin]
    while frontier:
        for destination in links[frontier.pop()] - reached:
            reached.add(destination)
            frontier.append(destination)
    return reached


def _check_tool(rooms, placed, name, target_room, landmark):
    source, tool = placed[name]
    room = rooms[source]
    if (direct_command(f"take {name}", [*room.items, *room.scenery]) != ("take", name)
            or direct_command(f"use {name} on {landmark}", [tool, *target_room.scenery])
            != ("use_on", ToolUse(name, landmark))):
        raise PlanError(f"Use simple, distinct names for the tool {name!r} and its target {landmark!r}.")
    tool.puzzle_arc = room.arc


def _validate_passage_route(rooms, start, gate, passages, placed):
    links = {name: set(room.exits.values()) for name, room in rooms.items()}
    initial = _reachable(links, start)
    if len(initial) < 4 or not any(len(links[name]) >= 3 for name in initial):
        raise PlanError("Before opening a passage, make at least four rooms reachable and give one a three-way junction.")
    landmarks = {room.name for room in passages}
    if not landmarks.issubset(initial):
        raise PlanError("Both passage landmarks must be reachable from the start before either passage is opened.")
    if any(start not in _reachable(links, name) for name in initial):
        raise PlanError("Every room reached before the first passage must have a return route.")

    reached = initial
    available = initial
    for room in passages:
        passage = room.passage
        source, _ = placed[passage.tool]
        if source in landmarks or source not in available or passage.destination in reached:
            raise PlanError(f"The passage from {room.name} must open new rooms using a tool from the previous stage.")
        _check_tool(rooms, placed, passage.tool, room, passage.landmark)
        links[room.name].add(passage.destination)
        links[passage.destination].add(room.name)
        unlocked = _reachable(links, start)
        available, reached = unlocked - reached, unlocked
        if any(start not in _reachable(links, name) for name in reached):
            raise PlanError("Every room must have a return route at the stage when it becomes reachable.")

    if gate.name not in available or any(placed[tool][0] not in available - {gate.name}
                                         for tool, _, _ in gate.challenge.solutions):
        raise PlanError("Put the final obstacle and all of its solution tools in separate rooms beyond the second passage.")
    for tool, _, _ in gate.challenge.solutions:
        _check_tool(rooms, placed, tool, gate, gate.challenge.obstacle)
    if reached != set(rooms):
        raise PlanError("Every room must be reachable once both passages have opened.")


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
