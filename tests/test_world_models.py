import pickle

from game_world import Challenge, ChallengeSolution, Item, Room, fresh_world, starting_room, world_map


def test_fresh_world_isolates_the_castle_and_its_encounters():
    game = fresh_world()
    game[starting_room.name].items.clear()
    game["Giant's Hall"].trial.spilled.add("left")

    second = fresh_world()
    assert starting_room is world_map[starting_room.name]
    assert second[starting_room.name].items and starting_room.items
    assert second["Giant's Hall"].trial.spilled == set()


def test_copying_a_custom_world_keeps_shared_item_references_inside_the_copy():
    tool = Item("key", "An iron key.")
    template = {"A": Room("A", "", items=[tool]), "B": Room("B", "", scenery=[tool])}
    copy = fresh_world(template)
    assert copy["A"].items[0] is copy["B"].scenery[0]
    assert copy["A"].items[0] is not tool


def test_load_a_challenge_saved_with_plain_triples():
    triple = ("rope", "tie the rope", "The bridge holds.")
    challenge = Challenge("bridge", "east", "A gap.", (triple,), "The road is clear.", "A secured bridge.")
    challenge.solutions = (triple,)  # Field representation in a pre-refactor snapshot.
    restored = pickle.loads(pickle.dumps(challenge))

    solution = restored.solution_for("rope")
    assert isinstance(solution, ChallengeSolution)
    assert solution == triple
    assert solution.approach == "tie the rope"
    assert restored.outcome("rope") == solution.outcome
    assert restored.solution_for("sword") is None
