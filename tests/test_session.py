import json

import pytest

from game_session import GameSession


def test_arrival_prose_survives_inspection_then_updates_with_room(monkeypatch):
    session = GameSession()
    monkeypatch.setattr(session.content, "generate_description", lambda *a, **k: "A potion gleams on the pedestal.")
    session.turn("go north")
    received = []
    events = session.turn("go east", received.append)
    assert received == events
    assert events[-1].kind == "arrival" and "A potion gleams" in events[-1].text
    assert session.public_state()["description"] == "A potion gleams on the pedestal."
    session.turn("examine potion")
    assert session.public_state()["description"] == "A potion gleams on the pedestal."
    session.turn("take potion")
    assert "potion" not in session.public_state()["description"]
    assert session.public_state()["inventory"] == ["magic potion"]


def test_sessions_own_separate_worlds_and_end_on_quit():
    first, second = GameSession(), GameSession()
    first.turn("take candle")
    assert "candle" in second.public_state()["items"]
    assert first.turn("quit")[-1].text.startswith("Thanks")
    assert first.public_state()["ended"]
    assert not first.turn("go north")


@pytest.fixture
def castle(monkeypatch):
    session = GameSession()
    monkeypatch.setattr(session.content, "generate_description", lambda *args, **kw: kw["description"])
    return session


def test_stairs_teach_a_repeatable_route_and_spend_potion_once(castle):
    for command in ("north", "east", "take potion", "use potion", "west", "north"):
        castle.turn(command)
    assert "stays level" in castle.public_state()["description"]
    assert "last grip" in castle.turn("up")[-1].text
    assert castle.game.deaths == 0 and not castle.game.potion_active
    for life in (2, 3):
        events = [evt for evt in castle.turn("up") if evt.kind != "activity"]
        assert [evt.kind for evt in events] == ["death", "lesson", "respawn", "arrival"]
        state = castle.public_state()
        assert state["life"] == life and state["name"] == "Great Staircase" and not state["ended"]
        assert state["lessons"] == [events[1].text]
        assert castle.turn("remember")[-1].text == events[1].text
    castle.turn("follow the banister")
    assert castle.public_state()["name"] == "Upper Landing"
    castle.turn("down")
    castle.turn("up")
    assert castle.public_state()["name"] == "Upper Landing" and castle.game.deaths == 2


def test_table_deaths_reset_the_cups_and_keep_world_changes(castle):
    for command in ("take sword", "use sword on wall", "north", "north", "follow the banister", "north"):
        castle.turn(command)
    routes = [("Giant's Hall", ()), ("Upper Landing", ("north",)), ("Great Staircase", ("up", "north")),
              ("Starting Room", ("north", "north", "up", "north")), ("Starting Room", ())]
    for i, (checkpoint, route) in enumerate(routes):
        side, other = ("left", "right") if i % 2 == 0 else ("right", "left")
        castle.turn("tip the " + other + " cup")
        assert castle.public_state()["offers"] == [f"Drink from the {side} cup"]
        assert "empty" in castle.turn("drink " + other)[-1].text
        assert castle.game.deaths == i
        death = [evt for evt in castle.turn("drink " + side) if evt.kind == "death"]
        assert len(death) == 1
        state = castle.public_state()
        assert state["name"] == checkpoint and state["life"] == i + 2 and state["inventory"] == ["rusty sword"]
        assert not state["ended"] and not castle.saga.started
        for command in route:
            castle.turn(command)
        if route or i == 0:
            assert len(castle.public_state()["offers"]) == 2
    state = castle.public_state()
    assert "down" in state["exits"] and "rusty sword" not in state["items"]
    assert len(state["lessons"]) == 2 and "white" in state["lessons"][0] and "black" in state["lessons"][1]


def test_table_can_be_inspected_and_broken_one_cup_at_a_time(castle, monkeypatch):
    monkeypatch.setattr("saga.complete", lambda *args, **kw: None)
    for command in ("north", "north", "follow the banister", "north", "examine left cup", "examine right cup",
                    "look at the tray", "inventory", "refuse", "enter reflection", "knock over the left cup"):
        castle.turn(command)
    assert "cold silver" in castle.turn("enter reflection")[-1].text
    assert "empty glass" in castle.turn("spill left")[-1].text
    assert not castle.game.giant_defeated and castle.public_state()["items"] == ()
    # An improvised command must be able to reach the same authored rule as a literal command.
    monkeypatch.setattr("command_parser.complete", lambda *args, **kw: '{"action":"trial","target":"spill right"}')
    castle.turn("Sweep the remaining glass onto its side with my elbow")
    assert castle.public_state()["offers"] == [] and "swings open" in castle.public_state()["description"]
    events = castle.turn("step through the reflected eye")
    assert [evt.kind for evt in events].count("milestone") == 1
    assert castle.saga.started and castle.game.deaths == 0 and "key" in castle.public_state()["items"]
    assert "left cup" not in castle.public_state()["scenery"]


class Viewer:
    def __init__(self):
        self.shown = []

    def show(self, room, *, description=None):
        self.shown.append(("room", room.name))

    def show_portrait(self, portrait):
        self.shown.append(("portrait", portrait.place))


def scripted(monkeypatch, replies):
    """Answer parser and adjudicator calls in order, failing loudly on an unexpected request."""
    replies = iter(replies)

    def complete(*args, **kw):
        return next(replies)

    monkeypatch.setattr("command_parser.complete", complete)
    monkeypatch.setattr("environment_actions.complete", complete)


def test_portrait_shows_what_the_player_has_on_until_the_next_turn(castle, monkeypatch):
    for command in ("take sword", "use sword on wall", "down", "take helm", "use helm", "take chain"):
        castle.turn(command)
    dungeon = castle.room.fixed_text()
    scripted(monkeypatch, [
        '{"action":"use","target":"iron chain"}',
        json.dumps({"effect": "alter", "anchor": "iron chain", "reply": "You loop the chain around your neck.",
                    "room_after": dungeon, "description": "A heavy chain, now looped twice.",
                    "worn": "The iron chain loops around your neck like a necklace."}),
    ])
    castle.turn("wear the chain like a necklace")
    viewer = Viewer()
    text = castle.turn("look at yourself")[-1].text
    assert text == ("You look yourself over. The iron helm sits low over your brow. "
                    "The iron chain loops around your neck like a necklace. You carry the rusty sword.")
    castle.show(viewer)
    prompt = castle.portrait.request("model")["prompt"]
    assert "like a necklace" in prompt and "rusty sword" in prompt and "Room behind the figure: Dungeon" in prompt
    assert castle.public_state()["worn"] == ["iron helm", "iron chain"]
    assert castle.turn("inventory")[-1].text == "Inventory: rusty sword, iron helm (worn), iron chain (worn)"
    castle.show(viewer)
    assert viewer.shown == [("portrait", "Dungeon"), ("room", "Dungeon")] and castle.portrait is None

    scripted(monkeypatch, ['{"action":"take","target":"iron helm"}'])
    assert castle.turn("take off the helm")[-1].text == "You take off the iron helm and carry it."
    castle.turn("drop chain")
    castle.turn("take chain")
    assert castle.public_state()["worn"] == []
    assert castle.turn("me")[-1].text == ("You look yourself over. You have nothing from the castle on. "
                                          "You carry the rusty sword, the iron helm and the iron chain.")


def test_trail_names_the_last_four_arrivals_including_a_respawn(castle):
    for command in ("north", "north", "up", "follow the banister", "north"):
        castle.turn(command)
    assert castle.public_state()["trail"] == [
        {"way": "north", "room": "Great Staircase"}, {"way": "woke", "room": "Great Staircase"},
        {"way": "up", "room": "Upper Landing"}, {"way": "north", "room": "Giant's Hall"},
    ]
    castle.turn("south")
    assert castle.public_state()["trail"][-1] == {"way": "south", "room": "Upper Landing"}
    assert len(castle.game.trail) == 6


def test_cutscene_captures_the_equipment_and_room_before_a_discovery(castle):
    for command in ("take sword", "use sword on wall", "down", "take helm", "use helm", "up", "north", "north"):
        castle.turn(command)
    previous = castle.cutscene
    castle.turn("up")
    assert castle.cutscene is previous  # A death is not a successful technique.
    castle.turn("follow the banister")
    assert castle.room.name == "Upper Landing"
    scene = castle.cutscene
    prompt = scene.request("model")["prompt"]
    assert "Setting: Great Staircase" in prompt and "stays level" in prompt
    assert "Worn: iron helm" in prompt and "rusty sword" in prompt
    assert "Action in progress: Edge up along the banister" in prompt
    for command in ("drop sword", "down", "follow the banister"):
        castle.turn(command)
    assert castle.cutscene is scene and scene.request("model")["prompt"] == prompt


def test_improvised_cutscene_requires_an_effect_and_ignores_paraphrased_repeats(castle, monkeypatch):
    castle.turn("north")
    interpret = '{"action":"interact","target":"tapestries"}'
    scripted(monkeypatch, [
        interpret, '{"effect":"observe","anchor":"tapestries","reply":"The weave ripples."}',
        interpret, '{"effect":"alter","anchor":"tapestries","reply":"You fold the fabric above the arch.",'
        '"room_after":"The corridor is clear.","description":"The fabric is folded."}',
        interpret, '{"effect":"alter","anchor":"tapestries","reply":"You tuck a loose edge in the fold.",'
        '"room_after":"The folds sit tight.","description":"The fabric is tucked in."}',
    ])
    castle.turn("test the wall hangings")
    assert castle.cutscene is None
    castle.turn("fold the tapestries above the arch")
    scene = castle.cutscene
    assert scene.action == "fold the tapestries above the arch"
    assert scene.description == "You fold the fabric above the arch."
    castle.turn("tidy the folded hangings")
    assert castle.cutscene is scene and len(castle.discovered_actions) == 1


def test_crafting_a_bag_and_packing_gear_is_procedural(castle, monkeypatch):
    from game_world import Item

    def no_model(*args, **kwargs):
        pytest.fail("Called a model for a bag command")

    monkeypatch.setattr("command_parser.complete", no_model)
    monkeypatch.setattr("environment_actions.complete", no_model)
    castle.game.inventory.append(Item("tapestry", "A length of sturdy cloth."))
    castle.turn("take candle")
    castle.turn("use the tapestry to make a bag for holding all our small items that we're carrying")
    assert castle.cutscene is not None and castle.public_state()["inventory"] == ["tapestry bag", "candle"]
    assert "Keep containers separate." in castle.turn("put bag in bag")[-1].text
    castle.turn("put candle in bag")
    castle.turn("drop bag")
    castle.turn("take tapestry bag")
    assert castle.public_state()["containers"] == {"tapestry bag": ["candle"]}
    castle.turn("take candle out of bag")
    assert castle.public_state()["inventory"] == ["tapestry bag", "candle"]
