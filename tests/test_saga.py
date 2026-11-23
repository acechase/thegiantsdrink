import json

import pytest
import responses

from game_world import fresh_world, world_map
from simulation_engine import GameState
from tests.support.cli import run_cli, run_game
from tests.support.openai import OPENAI_URL, adjudication, completion
from tests.support.stories import EXPLORE_ORCHARD, TO_THE_GIANT, arc_plan, expanded_arc_plan


def _arc_world():
    from saga import Saga

    rooms = fresh_world()
    game = GameState()
    game.current_room = "Giant's Hall"
    saga = Saga()
    saga.begin(rooms, "You attack the giant with the rusty sword and defeat him!")
    return rooms, game, saga


class TestArcs:
    def test_play_continues_through_planned_arcs(self, openai_api):
        inputs = [*TO_THE_GIANT, *EXPLORE_ORCHARD, "go north", "use silver bell on glass heron", "go north", "go south"]
        replies = [None] * len(inputs)
        plans = [expanded_arc_plan(), expanded_arc_plan("Second ", horizon="Further still, a lighthouse shines darkness.")]
        output = run_game(openai_api, inputs, replies, plans)
        assert "The giant’s bargain is broken." in output
        assert "A way north has opened." in output
        assert "── Arc 1: The Glass Orchard ──" in output and "Glass trees chime" in output
        assert "A glass heron stands across the northern path and will not move." in output
        assert "The heron answers the bell's note and steps aside." in output
        assert "a stair of cloud climbs toward a dark lighthouse" in output
        assert "── Arc 2: Second The Glass Orchard ──" in output
        assert "You move to the Still Pool." in output  # The way back still works.
        assert "Thanks for playing" in output
        planning = [json.loads(call.request.body) for call in openai_api.calls]
        planning = [json.loads(p["messages"][1]["content"]) for p in planning if "planning" in str(p) or
                    p["messages"][0]["content"].startswith("You plan the next arc")]
        assert [p["arc_number"] for p in planning] == [1, 2]
        assert planning[1]["horizon"] == "Beyond the pool, a stair of cloud climbs toward a dark lighthouse."
        assert planning[1]["chronicle"][-1].startswith("The Glass Orchard: The heron answers")
        assert "use silver bell on glass heron" in planning[1]["recent_commands"]
        assert "[Cached AI] Using the planned room description." in output

    def test_plan_builds_a_connected_arc_behind_the_gate(self, openai_api):
        rooms, game, saga = _arc_world()
        openai_api.post(OPENAI_URL, json=completion(json.dumps(expanded_arc_plan())))
        arc = saga.plan_next(rooms, game)
        assert arc.start == "Orchard Gate" and saga.gate is None
        assert rooms["Giant's Hall"].exits["north"] == "Orchard Gate"
        assert rooms["Orchard Gate"].exits == {
            "north": "Chiming Grove", "east": "Reed Bank", "west": "Weather Porch", "south": "Giant's Hall"
        }
        assert rooms["Still Pool"].exits == {"west": "Echo Vault"}
        assert rooms["Chiming Grove"].exits == {"south": "Orchard Gate"}
        assert "dust" in rooms["Giant's Hall"].prose() and "sits upon it" not in rooms["Giant's Hall"].prose()
        request = json.loads(json.loads(openai_api.calls[0].request.body)["messages"][1]["content"])
        assert request["reserved_start_exit"] == "south" and "Giant's Hall" in request["existing_room_names"]

    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            (lambda p: p["rooms"][0].update(name="Giant's Hall"), "already used"),
            (lambda p: p["rooms"][0]["exits"].update(south="Still Pool"), "south exit free"),
            (lambda p: p["rooms"][2]["exits"].update(north="Orchard Gate"), "not already an exit"),
            (lambda p: p["challenge"].update(obstacle="glass swan"), "scenery"),
            (lambda p: p["challenge"]["solutions"][0].update(tool="rusty sword"), "placed in this arc"),
            (lambda p: p["challenge"]["solutions"][0].update(tool="moonbeam"), "not an item"),
            (lambda p: p["rooms"][1].update(exits={}), "reachable"),
            (lambda p: p["rooms"].pop(), "3 to 6 rooms"),
        ],
    )
    def test_unplayable_plans_are_rejected(self, change, reason):
        from saga import PlanError, build_arc

        plan = arc_plan()
        change(plan)
        with pytest.raises(PlanError, match=reason):
            build_arc(json.dumps(plan), number=1, reserved="south", world=world_map, carried=["rusty sword"])

    def test_rejected_plan_is_retried_with_the_reason(self, openai_api):
        rooms, game, saga = _arc_world()
        broken = expanded_arc_plan()
        broken["challenge"]["obstacle"] = "glass swan"
        openai_api.post(OPENAI_URL, json=completion(json.dumps(broken)))
        openai_api.post(OPENAI_URL, json=completion(json.dumps(expanded_arc_plan())))
        assert saga.plan_next(rooms, game).title == "The Glass Orchard"
        assert "was rejected: challenge.obstacle" in json.loads(openai_api.calls[1].request.body)["messages"][1]["content"]

    def test_new_generation_retries_a_short_legacy_plan(self, openai_api):
        rooms, game, saga = _arc_world()
        openai_api.post(OPENAI_URL, json=completion(json.dumps(arc_plan())))
        openai_api.post(OPENAI_URL, json=completion(json.dumps(expanded_arc_plan())))
        arc = saga.plan_next(rooms, game)
        assert len(arc.rooms) == 8
        assert "8 to 12 rooms" in json.loads(openai_api.calls[1].request.body)["messages"][1]["content"]

    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            (lambda p: p["rooms"][0]["exits"].update(up="Echo Vault"), "must open new rooms"),
            (lambda p: p["rooms"][4]["exits"].update(north="Echo Vault"), "both ends"),
            (lambda p: p["rooms"][5]["items"].append(p["rooms"][6]["items"][0]), "not unique"),
            (lambda p: p["rooms"][5]["items"].append(p["rooms"][6]["items"].pop()), "tool from the previous stage"),
            (lambda p: p["challenge"]["solutions"][0].update(tool="reed wedge"), "beyond the second passage"),
        ],
    )
    def test_expanded_plan_rejects_shortcuts_and_conflicting_exits(self, change, reason):
        from saga import PlanError, build_arc

        plan = expanded_arc_plan()
        change(plan)
        with pytest.raises(PlanError, match=reason):
            build_arc(json.dumps(plan), number=1, reserved="south", world=world_map, require_passages=True)

    def test_fabricated_tool_and_improvisation_cannot_bypass_the_first_passage(self, openai_api):
        from game_world import Item
        from saga import build_arc

        _, rooms = build_arc(json.dumps(expanded_arc_plan()), number=1, reserved="south", world=world_map)
        game = GameState()
        game.current_room = "Chiming Grove"
        game.inventory.append(Item("reed wedge", "A crude imitation."))
        game.use_on("reed wedge", "glass shutter", rooms)
        openai_api.post(OPENAI_URL, json=adjudication(effect="remove", anchor="glass shutter",
                                                      reply="You tear off the shutter.", room_after="The shutter is gone."))
        game.investigate("glass shutter", rooms, "tear off the glass shutter")
        assert rooms["Chiming Grove"].exits == {"south": "Orchard Gate"}
        assert rooms["Chiming Grove"].scenery[0].name == "glass shutter"
        assert "west" not in rooms["Bell Foundry"].exits

    def test_unavailable_planner_leaves_mist_until_the_player_tries_again(self, openai_api):
        inputs = [*TO_THE_GIANT, "go north", "go north"]
        attempts = []

        def respond(request):
            payload = json.loads(request.body)
            if payload["messages"][0]["content"].startswith("You plan the next arc"):
                attempts.append(1)
                if len(attempts) <= 2:
                    return 503, {}, "{}"
                return 200, {}, json.dumps(completion(json.dumps(expanded_arc_plan())))
            if "response_format" in payload:
                return 200, {}, json.dumps(completion(json.dumps({"action": "attack", "target": "giant"})))
            return 200, {}, json.dumps(completion("You stand in a room."))

        openai_api.add_callback(responses.POST, OPENAI_URL, callback=respond, content_type="application/json")
        output = run_cli(inputs)
        assert "To the north there is only grey mist so far." in output
        assert "Only grey mist lies that way." in output
        assert "── Arc 1: The Glass Orchard ──" in output and len(attempts) == 3

    def test_unplanned_but_clever_solution_can_overcome_the_obstacle(self, openai_api):
        rooms, game, saga = _arc_world()
        openai_api.post(OPENAI_URL, json=completion(json.dumps(expanded_arc_plan())))
        saga.plan_next(rooms, game)
        game.current_room = "Still Pool"
        pool = rooms["Still Pool"]
        openai_api.post(OPENAI_URL, json=adjudication(effect="overcome", anchor="glass heron",
                                                      reply="You mimic the wind; the heron tilts aside.",
                                                      room_after="The pool is still. The way north is open.",
                                                      description="The heron leans aside."))
        assert game.move("north", rooms).startswith("A glass heron stands across the northern path and will not move.")
        result = game.investigate("glass heron", rooms, "whistle like the wind through glass leaves")
        assert result == "You mimic the wind; the heron tilts aside." and game.breakthrough is pool
        assert pool.prose() == "The pool is still. The way north is open."
        assert game.examine("glass heron", rooms) == "The heron leans aside."
        challenge = json.loads(json.loads(openai_api.calls[1].request.body)["messages"][1]["content"])["challenge"]
        assert challenge["planned_solutions"] == ["ring a matching note with the silver bell"]

    def test_obstacle_cannot_be_carried_off_or_overcome_through_something_else(self, openai_api):
        rooms, game, saga = _arc_world()
        openai_api.post(OPENAI_URL, json=completion(json.dumps(expanded_arc_plan())))
        saga.plan_next(rooms, game)
        game.current_room = "Still Pool"
        openai_api.post(OPENAI_URL, json=adjudication(effect="overcome", anchor="pool", reply="You swim past.",
                                                      room_after="The pool ripples.", description="Rippling."))
        openai_api.post(OPENAI_URL, json=adjudication(effect="take", anchor="glass heron", reply="You lift the heron.",
                                                      room_after="The pool is empty of herons.", description="Light."))
        before = rooms["Still Pool"].prose()
        assert game.investigate("pool", rooms, "swim around the heron").startswith("A pool lies perfectly still")
        assert game.breakthrough is None and rooms["Still Pool"].prose() == before
        assert game.investigate("glass heron", rooms, "pick up the heron") == "You lift the heron."
        assert game.breakthrough is rooms["Still Pool"] and game.inventory == []
        assert "glass heron" in [thing.name for thing in rooms["Still Pool"].scenery]

    def test_defeated_giant_stays_defeated(self, game, wmap):
        game.inventory.append(wmap["Starting Room"].items[0])
        game.current_room = "Giant's Hall"
        table = wmap[game.current_room].trial
        table.resolve("spill both", game, wmap)
        table.resolve("enter reflection", game, wmap)
        assert game.attack("giant") == "The giant is gone. Only a drift of grey dust remains."


def test_items_described_in_room_prose_move_to_their_own_sentence():
    from saga import build_arc

    plan = arc_plan()
    grove = plan["rooms"][1]
    grove["description"] = "Glass trees crowd close. A silver bell hangs from a low branch. A path leads east."
    grove["items"][0].pop("room_text")
    plan["rooms"][0]["description"] += " A silver bell's chime carries from somewhere ahead."
    arc, rooms = build_arc(json.dumps(plan), number=1, reserved="south", world=world_map)
    assert rooms["Chiming Grove"].description == "Glass trees crowd close. A path leads east."
    assert rooms["Chiming Grove"].items[0].room_text == "A silver bell hangs from a low branch."
    assert rooms["Chiming Grove"].prose().count("silver bell") == 1
