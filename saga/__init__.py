"""Plan endless AI-generated story arcs that continue after the giant falls.

Each new arc is planned before the player enters it. Its branches contain a
sequence of tool-operated passages and a final obstacle. The engine validates
the whole route, including the rooms reachable at each puzzle stage.
"""

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional

from game_world import OPPOSITE, key
from openai_client import complete

from .catalog import Catalog, StoryError, plan_id
from .models import Arc, PlanError
from .plans import build_arc
from .prompt import PROMPT
from .routes import castle_crossing

CASTLE_HORIZON = (
    "Behind the empty throne, a crack has opened in the castle wall. Through it you glimpse a country that "
    "was not there before, as if the game itself has only just imagined it."
)


@dataclass
class Saga:
    """The chain of arcs that follows the castle, planned one arc at a time."""

    arcs: list[Arc] = field(default_factory=list)
    chronicle: list[str] = field(default_factory=list)
    gate: Optional[tuple[str, str]] = None  # (room name, direction) of a way onward still waiting for its arc
    horizon: str = ""
    edition: str = ""
    preferred_plans: tuple[dict, ...] = ()
    catalog_path: Optional[str] = None
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
        direction = castle_crossing(hall)
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
