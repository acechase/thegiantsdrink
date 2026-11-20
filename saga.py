"""Plan endless AI-generated story arcs that continue after the giant falls.

Each arc is planned in full before the player enters it: a handful of connected
rooms, the items they hold, and one obstacle that bars the way onward together
with planned ways past it. The engine validates every plan, so a generated arc
is always connected and always solvable with what it provides. When the player
overcomes an arc's obstacle, the next arc is planned from that arc's horizon and
from how the player has played so far.
"""

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field

from game_world import Challenge, Item, Room, key, noun
from openai_client import complete

DIRECTIONS = ("north", "south", "east", "west", "up", "down")
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east", "up": "down", "down": "up"}
CASTLE_HORIZON = (
    "Behind the empty throne, a crack has opened in the castle wall. Through it you glimpse a country that "
    "was not there before, as if the game itself has only just imagined it."
)

PROMPT = """You plan the next arc of an endless adventure that began in the castle of The Giant's Drink.
The world is a surreal, dreamlike game that quietly studies the player and reshapes itself in response.
An arc is 3 to 6 connected rooms ending in one challenge: an obstacle that bars the way onward. Plan the entire arc now.
- Begin where the supplied horizon promised. Keep continuity with earlier arcs, the chronicle and carried items.
- Read the player's profile and recent commands. Make this challenge test something their habits have not:
  if they rely on force, let force alone fail; if they are cautious, demand a bold act; if they hoard, ask for a gift.
- Give 2 or 3 planned solutions that suit different temperaments. Each uses one tool: an item placed in this arc
  away from the obstacle, or something the player already carries. Clever unplanned solutions may also succeed in play.
- The player can examine, take or change anything you describe, so describe concrete, tangible things.
  Mention each item only in its room_text, never in the room description: the engine shows room_text while it's there.
  Mention only exits that exist. Make the barrier show a tangible danger and a subtle clue to surviving it.
  Put the barrier sentence in the challenge room's description.
- Escalate gradually. Stay original and in-world, with no talk of game mechanics.
Return only JSON with this shape:
{"title": str, "premise": "1-2 sentences shown on arrival, hinting at what must be overcome but not how",
 "start": room name,
 "rooms": [{"name": str, "description": "2-4 sentences", "exits": {"<direction>": room name},
            "items": [{"name": "short noun phrase", "description": str, "room_text": "sentence placing it in the room"}],
            "scenery": [{"name": "noun phrase used in the description", "description": str}]}],
 "challenge": {"room": room name, "obstacle": scenery name in that room, "blocks": direction it bars,
               "barrier": "sentence describing how it bars the way",
               "solutions": [{"tool": item name, "approach": "a few words",
                              "outcome": "1-2 sentences narrating, in the second person, what happens"}],
               "room_after": "the challenge room's description once the obstacle is overcome",
               "obstacle_after": "the obstacle's appearance once overcome"},
 "horizon": "1-2 sentences glimpsed beyond the obstacle once it is overcome; the next arc begins there"}
Directions are north, south, east, west, up and down. Room names must be new. The start room's reserved_start_exit
leads back the way the player came: do not use it. The challenge's blocks direction must not be another exit of
the challenge room. Item names must be unique and must differ from carried items."""


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

    def plan_next(self, world, game, *, profile_summary="", report=None, attempts=2):
        """Plan and attach the next arc at the open gate. Return the new Arc, or None to retry later."""
        if not self.gate:
            return None
        source, direction = self.gate
        reserved = OPPOSITE[direction]
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
            raw = complete(PROMPT, json.dumps(request) + feedback, max_tokens=3500, json_output=True,
                           purpose="planning the next arc", report=report, read_timeout=300)
            if raw is None:
                return None
            try:
                arc, rooms = build_arc(raw, number=len(self.arcs) + 1, reserved=reserved, world=world,
                                       carried=[item.name for item in game.inventory])
            except PlanError as exc:
                if report is not None:
                    report(f"[Procedural] Rejected the arc plan: {exc}")
                feedback = f"\nYour previous plan was rejected: {exc} Return a corrected plan."
                continue
            world.update(rooms)
            world[source].exits[direction] = arc.start
            world[arc.start].exits[reserved] = source
            self.arcs.append(arc)
            self.gate = None
            return arc
        return None


def _text(data, key, limit=1200):
    value = data.get(key) if isinstance(data, dict) else None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise PlanError(f"{key} must be a non-empty string under {limit} characters.")
    return " ".join(value.split())


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


def build_arc(raw, *, number, reserved, world, carried=()):
    """Validate a plan and turn it into rooms. Raise PlanError with a fixable reason when it can't be played."""
    try:
        plan = json.loads(raw)
    except ValueError as exc:
        raise PlanError("The plan was not valid JSON.") from exc
    if not isinstance(plan, dict):
        raise PlanError("The plan must be a JSON object.")
    entries = plan.get("rooms")
    if not isinstance(entries, list) or not 3 <= len(entries) <= 6:
        raise PlanError("An arc needs 3 to 6 rooms.")

    taken = {name.casefold() for name in world}
    rooms, item_names, placed = {}, {name.casefold() for name in carried}, set()
    for entry in entries:
        name = _text(entry, "name", 60)
        if name.casefold() in taken or name in rooms:
            raise PlanError(f"The room name {name!r} is already used.")
        room = Room(name, _text(entry, "description"), _things(entry.get("items", []), "items", 4, placed=True),
                    scenery=_things(entry.get("scenery", []), "scenery", 6, placed=False))
        for item in room.items:
            if item.name in item_names:
                raise PlanError(f"The item name {item.name!r} is not unique.")
            item_names.add(item.name)
            placed.add(item.name)
        _place(room)
        room.generated = True
        room.arc = number
        room.planned_exits = entry.get("exits")
        rooms[name] = room

    start = plan.get("start")
    if start not in rooms:
        raise PlanError("start must name one of the arc's rooms.")
    for room in rooms.values():
        exits = room.planned_exits
        del room.planned_exits
        if not isinstance(exits, dict):
            raise PlanError(f"{room.name} needs an exits object.")
        for direction, destination in exits.items():
            if direction not in DIRECTIONS or destination not in rooms or destination == room.name:
                raise PlanError(f"{room.name} has an invalid exit {direction!r} to {destination!r}.")
            room.exits[direction] = destination
    if reserved in rooms[start].exits:
        raise PlanError(f"The start room must leave its {reserved} exit free for the way back.")

    challenge = plan.get("challenge")
    if not isinstance(challenge, dict) or challenge.get("room") not in rooms:
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
        if tool not in item_names:
            raise PlanError(f"The solution tool {tool!r} is not an item in this arc or a carried item.")
        tools.append((tool, _text(solution, "approach", 120), _text(solution, "outcome", 600)))
    if not placed.intersection(tool for tool, _, _ in tools):
        raise PlanError("At least one solution must use an item placed in this arc.")
    barrier = _text(challenge, "barrier", 400)
    if barrier not in gate_room.description:
        gate_room.description = f"{gate_room.description} {barrier}"
    gate_room.challenge = Challenge(obstacle, blocks, barrier, tuple(tools),
                                    _text(challenge, "room_after"), _text(challenge, "obstacle_after", 600), checkpoint=start)

    # Fill in return routes the plan left implicit, without using reserved directions.
    for room in list(rooms.values()):
        for direction, destination in list(room.exits.items()):
            back, other = OPPOSITE[direction], rooms[destination]
            claimed = (other.name == start and back == reserved) or (other is gate_room and back == blocks)
            if back not in other.exits and not claimed:
                other.exits[back] = room.name
    reached, frontier = {start}, [start]
    while frontier:
        for destination in rooms[frontier.pop()].exits.values():
            if destination not in reached:
                reached.add(destination)
                frontier.append(destination)
    if reached != set(rooms):
        raise PlanError("Every room must be reachable from the start room.")

    arc = Arc(number, _text(plan, "title", 80), _text(plan, "premise", 500), start, _text(plan, "horizon", 500),
              list(rooms), gate_room.name)
    return arc, rooms
