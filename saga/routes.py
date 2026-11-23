"""Check reachability and tool use for the playable stages of an arc."""

from command_parser import ToolUse, direct_command

from .models import PlanError


def castle_crossing(hall):
    """Choose the first unoccupied way out of the Giant’s Hall after the Cup."""
    return next(direction for direction in ("north", "up", "west", "east", "down", "south") if direction not in hall.exits)


def reachable(links, origin):
    """Return all locations accessible from the origin via the given directed links."""
    reached, frontier = {origin}, [origin]
    while frontier:
        for destination in links[frontier.pop()] - reached:
            reached.add(destination)
            frontier.append(destination)
    return reached


def _can_use_tool(room, tool, landmark, targets):
    take = direct_command(f"take {tool.name}", [*room.items, *room.scenery])
    use = direct_command(f"use {tool.name} on {landmark}", [tool, *targets])
    return take == ("take", tool.name) and use == ("use_on", ToolUse(tool.name, landmark))


def _check_tool(placed, name, target_room, landmark):
    room = placed[name].room
    tool = placed[name].item
    if not _can_use_tool(room, tool, landmark, target_room.scenery):
        raise PlanError(f"Use simple, distinct names for the tool {name!r} and its target {landmark!r}.")
    tool.puzzle_arc = room.arc


def validate_passage_route(rooms, start, gate, passages, placed):
    """Verify each stage opens the next tools and preserves the way back."""
    links = {name: set(room.exits.values()) for name, room in rooms.items()}
    initial = reachable(links, start)
    if len(initial) < 4 or not any(len(links[name]) >= 3 for name in initial):
        raise PlanError("Before opening a passage, make at least four rooms reachable and give one a three-way junction.")
    landmarks = {room.name for room in passages}
    if not landmarks.issubset(initial):
        raise PlanError("Both passage landmarks must be reachable from the start before either passage is opened.")
    if any(start not in reachable(links, name) for name in initial):
        raise PlanError("Every room reached before the first passage must have a return route.")

    reached = initial
    available = initial
    for room in passages:
        passage = room.passage
        source = placed[passage.tool].room.name
        if source in landmarks or source not in available or passage.destination in reached:
            raise PlanError(f"The passage from {room.name} must open new rooms using a tool from the previous stage.")
        _check_tool(placed, passage.tool, room, passage.landmark)
        links[room.name].add(passage.destination)
        links[passage.destination].add(room.name)
        unlocked = reachable(links, start)
        available, reached = unlocked - reached, unlocked
        if any(start not in reachable(links, name) for name in reached):
            raise PlanError("Every room must have a return route at the stage when it becomes reachable.")

    tool_rooms = {placed[solution.tool].room.name for solution in gate.challenge.solutions}
    if gate.name not in available or not tool_rooms.issubset(available - {gate.name}):
        raise PlanError("Put the final obstacle and all of its solution tools in separate rooms beyond the second passage.")
    for solution in gate.challenge.solutions:
        _check_tool(placed, solution.tool, gate, gate.challenge.obstacle)
    if reached != set(rooms):
        raise PlanError("Every room must be reachable once both passages have opened.")


def validate_legacy_crossing(rooms, gate):
    """Verify that a compact arc can be completed using a locally placed tool."""
    routes_back = {name: set() for name in rooms}
    for room in rooms.values():
        for destination in room.exits.values():
            routes_back[destination].add(room.name)
    if len(reachable(routes_back, gate.name)) != len(rooms):
        raise PlanError("Some rooms cannot reach the crossing.")

    challenge = gate.challenge
    targets = [*gate.scenery, *gate.items]
    for room in rooms.values():
        for item in room.items:
            if challenge.outcome(item.name) and _can_use_tool(room, item, challenge.obstacle, targets):
                return
    raise PlanError("The local solution needs a tool and obstacle that can be named in commands.")
