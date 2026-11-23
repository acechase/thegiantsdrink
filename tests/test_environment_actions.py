import pytest

from simulation_engine import GameState
from tests.support.cli import run_game
from tests.support.openai import OPENAI_URL, adjudication, sent_scene


class TestEnvironment:
    def test_piece_of_described_scenery_can_be_taken(self, openai_api, game, wmap):
        game.move("north", wmap)
        corridor = wmap["Corridor"]
        openai_api.post(
            OPENAI_URL,
            json=adjudication(
                effect="detach",
                anchor="tapestries",
                reply="You tear one tapestry from the wall.",
                room_after="A long passage. One tapestry is missing from the walls.",
                description="A gap shows where one tapestry hung.",
                piece="tapestry",
                piece_description="A rolled tapestry of feasting giants.",
            ),
        )
        assert game.investigate("tapestries", wmap, "tear down a tapestry") == "You tear one tapestry from the wall."
        assert [item.name for item in game.inventory] == ["tapestry"]
        assert corridor.prose() == "A long passage. One tapestry is missing from the walls."
        assert sent_scene(openai_api)["fixed_features"].startswith("A long passage stretches")

    def test_worn_items_follow_the_adjudicated_outcome(self, openai_api, game, wmap):
        game.move("north", wmap)
        fixed = wmap["Corridor"].fixed_text()
        cloak = "The tapestry hangs from your shoulders like a cloak."
        openai_api.post(OPENAI_URL, json=adjudication(
            effect="detach", anchor="tapestries", reply="You pull one down and swing it over your shoulders.",
            room_after=fixed, description="One tapestry is missing.", piece="tapestry",
            piece_description="Faded giants feast across the weave.", worn=cloak))
        openai_api.post(OPENAI_URL, json=adjudication(
            effect="observe", anchor="tapestry", reply="The weave smells of dust.", worn="off"))
        openai_api.post(OPENAI_URL, json=adjudication(
            effect="alter", anchor="tapestry", reply="You fold it over your arm.", room_after=fixed,
            description="A folded tapestry.", worn="off"))
        game.investigate("tapestries", wmap, "wear a tapestry as a cloak")
        assert game.worn == {"tapestry": cloak}
        assert game.examine("tapestry", wmap) == f"Faded giants feast across the weave. {cloak}"
        game.investigate("tapestry", wmap, "sniff my cloak")
        assert game.worn == {"tapestry": cloak} and sent_scene(openai_api, 1)["wearing"] == {"tapestry": cloak}
        game.investigate("tapestry", wmap, "fold the cloak over my arm")
        assert game.worn == {}

    def test_anchor_with_descriptive_words_still_grounds_on_the_object(self, openai_api, game, wmap):
        game.move("north", wmap)
        openai_api.post(OPENAI_URL, json=adjudication(
            effect="detach", anchor="Faded tapestries", reply="You drape one over your head like a hood.",
            room_after=wmap["Corridor"].fixed_text(), description="One is missing.", piece="tapestry",
            piece_description="A dusty tapestry.", worn="The tapestry hangs over your head like a hooded cloak."))
        assert game.investigate("tapestries", wmap, "wear a tapestry like a hooded cloak").startswith("You drape")
        assert game.worn == {"tapestry": "The tapestry hangs over your head like a hooded cloak."}

    def test_detail_mentioned_only_in_prose_can_be_taken(self, openai_api, game, wmap):
        for direction in ("north", "east"):
            game.move(direction, wmap)
        treasury = wmap["Treasure Room"]
        openai_api.post(
            OPENAI_URL,
            json=adjudication(
                effect="take",
                anchor="gold coins",
                reply="You scoop up the coins.",
                room_after="Faceted jewels lie across the floor around a marble pedestal.",
                description="A clinking handful of gold coins.",
            ),
        )
        assert game.take_item("gold coins", wmap, command="fill my pockets with gold coins") == "You scoop up the coins."
        assert [item.name for item in game.inventory] == ["gold coins"]
        assert "gold" not in treasury.prose().casefold()
        assert treasury.landmark("gold coins") is None

    def test_invented_or_substituted_subjects_are_rejected(self, openai_api, game, wmap):
        start = wmap["Starting Room"]
        before = start.prose()
        openai_api.post(OPENAI_URL, json=adjudication(effect="take", anchor="golden crown", reply="You take it.",
                                                      room_after="An empty room.", description="A crown."))
        openai_api.post(OPENAI_URL, json=adjudication(effect="take", anchor="candle", reply="You take the candle.",
                                                      room_after="An empty room.", description="A candle."))
        assert game.take_item("golden crown", wmap, command="take the golden crown") == "There is no such item here."
        assert game.investigate("shelf", wmap, "lift the shelf").startswith("A shallow stone ledge")
        assert game.inventory == [] and start.prose() == before

    def test_rewrite_absorbs_discoveries_without_repeating_sentences(self, openai_api, game, wmap):
        dungeon = wmap["Dungeon"]
        game.current_room = "Dungeon"
        assert "Seven tally marks" in game.investigate("stone walls", wmap, None, search=True)
        tally = dungeon.discoveries["stone walls"]
        helm = "An iron helm rests in the corner, forgotten by some earlier prisoner."
        openai_api.post(
            OPENAI_URL,
            json=adjudication(
                effect="alter",
                anchor="overhead grate",
                reply="The grate rattles loose.",
                room_after=f"Damp stone walls press close. The grate above hangs askew. {helm} {tally}",
                description="The grate hangs askew.",
            ),
        )
        assert game.investigate("overhead grate", wmap, "rattle the grate") == "The grate rattles loose."
        scene = dungeon.prose()
        assert scene.count("Seven tally marks") == 1 and scene.count("iron helm rests") == 1
        assert sent_scene(openai_api)["engine_sentences"][0] == helm
        assert game.examine("stone walls", wmap).count("Seven tally marks") == 1

    def test_tool_reveals_a_discovery_once(self, game, wmap):
        game.inventory.append(wmap["Starting Room"].items[0])
        game.current_room = "Dungeon"
        first = game.use_on("sword", "iron rings", wmap)
        assert "three different notes" in first and "tiny bell" in first
        assert game.use_on("sword", "iron rings", wmap) == "A tiny bell is stamped into the middle iron ring."
        assert wmap["Dungeon"].prose().count("tiny bell") == 1

    def test_drop_and_retake(self, game, wmap):
        game.take_item("sword", wmap)
        game.move("north", wmap)
        assert game.drop_item("sword", wmap) == "You set down the rusty sword."
        assert [item.name for item in wmap["Corridor"].items] == ["rusty sword"]
        assert game.drop_item("sword", wmap) == "You're not carrying the sword."
        assert game.take_item("sword", wmap) == "You take the rusty sword."

    def test_attack_accepts_a_described_giant(self, openai_api):
        inputs = ["take sword", "go north", "go north", "follow the banister", "go north", "swing at the sleeping giant"]
        replies = [None] * 5 + [("attack", "the sleeping giant")]
        output = run_game(openai_api, inputs, replies)
        assert "Life 2. You wake at the Giant's Hall." in output


def test_described_use_of_a_plain_item_is_judged(openai_api, game, wmap):
    game.take_item("sword", wmap)
    openai_api.post(OPENAI_URL, json=adjudication(effect="observe", anchor="rusty sword", reply="You test the edge."))
    assert game.use_item("sword", world_map=wmap, command="use the sword") == (
        "You're holding the rusty sword. What would you like to use it on?"
    )
    assert game.use_item("sword", world_map=wmap, command="run my thumb along the blade") == "You test the edge."
    assert len(openai_api.calls) == 1


def test_a_modifier_alone_never_names_a_different_thing():
    from game_world import Item, find_among, find_object

    chip, bell, sword = Item("chip of marble", "A chip."), Item("glass bell", "A bell."), Item("rusty sword", "A blade.")
    frames, marble = Item("empty picture frames", "Frames."), Item("marble", "A marble.")
    assert find_object("marble", [chip]) is None and find_object("glass", [bell]) is None
    assert find_object("chip", [chip]) is chip and find_object("the bell", [bell]) is bell
    assert find_object("sword", [sword]) is sword and find_object("picture frames", [frames]) is frames
    assert find_among("marble", [], [chip], [marble]) is marble  # An exact name anywhere beats the carried chip.
    assert find_among("bell", [Item("brass bell", "B.")], [bell]) .name == "brass bell"  # Nearer group wins a tie.


@pytest.fixture
def gallery():
    from game_world import Item, Room

    room = Room("Weather Gallery", "A long gallery holds a row of empty picture frames. A marble rolls down the "
                "floor and stops beside your foot.", exits={"west": "Rain Landing"},
                scenery=(Item("empty picture frames", "Each frame clouds over as you approach."),
                         Item("marble", "A glass marble with a tiny blue river inside.")))
    game = GameState()
    game.current_room = room.name
    game.inventory = [Item("chip of marble", "A white chip."), Item("glass bell", "A clear bell."),
                      Item("rusty sword", "Rust freckles the blade.")]
    return game, {room.name: room}


def test_taking_the_rolled_marble_does_not_grab_the_carried_chip(openai_api, gallery):
    game, rooms = gallery
    take = adjudication(effect="take", anchor="marble", reply="You pick up the marble.",
                        room_after="A long gallery holds a row of empty picture frames.", description="A glass marble.")
    openai_api.post(OPENAI_URL, json=take)
    assert game.take_item("marble", rooms, command="take marble") == "You pick up the marble."
    assert sent_scene(openai_api)["subject"] == "marble"
    game.drop_item("chip", rooms)
    game.drop_item("marble", rooms)
    assert game.take_item("marble", rooms, command="take marble") == "You take the marble."
    assert [item.name for item in rooms["Weather Gallery"].items] == ["chip of marble"]


def test_the_glass_in_a_frame_is_not_the_carried_glass_bell(openai_api, gallery):
    game, rooms = gallery
    openai_api.post(OPENAI_URL, json=adjudication(effect="observe", anchor="empty picture frames",
                                                  reply="The blade sinks into the clouding inside the frame like fog."))
    result = game.use_on("rusty sword", "glass", rooms, command="break the glass of one of the paintings using your sword")
    assert result == "The blade sinks into the clouding inside the frame like fog."
    assert sent_scene(openai_api)["subject"] == "glass" and sent_scene(openai_api)["held_tool"] == "rusty sword"
