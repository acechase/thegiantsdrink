import pytest


class TestMovement:
    def test_move_north_from_start(self, game, wmap):
        result = game.move("north", wmap)
        assert game.current_room == "Corridor"
        assert "Corridor" in result

    def test_move_invalid_direction(self, game, wmap):
        result = game.move("west", wmap)
        assert game.current_room == "Starting Room"
        assert "can't" in result.lower()

    def test_move_through_multiple_rooms(self, game, wmap):
        game.move("north", wmap)  # -> Corridor
        game.move("east", wmap)  # -> Treasure Room
        assert game.current_room == "Treasure Room"
        game.move("west", wmap)  # -> Corridor
        assert game.current_room == "Corridor"

    def test_move_to_locked_victory_chamber(self, game, wmap):
        game.current_room = "Throne Antechamber"
        result = game.move("north", wmap)
        assert game.current_room == "Throne Antechamber"
        assert "locked" in result.lower()

    def test_move_to_victory_chamber_when_unlocked(self, game, wmap):
        game.current_room = "Throne Antechamber"
        game.door_unlocked = True
        game.move("north", wmap)
        assert game.current_room == "Victory Chamber"


class TestInventory:
    def test_take_item(self, game, wmap):
        result = game.take_item("rusty sword", wmap)
        assert "rusty sword" in result.lower()
        assert len(game.inventory) == 1
        assert game.inventory[0].name == "rusty sword"

    def test_take_nonexistent_item(self, game, wmap):
        result = game.take_item("golden axe", wmap)
        assert "no such item" in result.lower()
        assert len(game.inventory) == 0

    def test_item_removed_from_room_after_take(self, game, wmap):
        game.take_item("rusty sword", wmap)
        assert [item.name for item in wmap["Starting Room"].items] == ["candle"]

    def test_take_item_in_treasure_room(self, game, wmap):
        game.current_room = "Treasure Room"
        result = game.take_item("magic potion", wmap)
        assert "magic potion" in result.lower()
        assert len(game.inventory) == 1


class TestItemUse:
    def test_probe_requires_a_carried_tool_and_local_target(self, game, wmap):
        start = wmap["Starting Room"]
        assert "carrying" in game.use_on("sword", "walls", wmap)
        assert "down" not in start.exits
        game.take_item("sword", wmap)
        assert "no telescope" in game.use_on("sword", "telescope", wmap)
        assert "Nothing happens" in game.use_on("sword", "shelf", wmap)
        game.move("north", wmap)
        assert "no iron rings" in game.use_on("sword", "iron rings", wmap)
        assert "down" not in start.exits

    def test_use_potion(self, game, wmap):
        game.current_room = "Treasure Room"
        game.take_item("magic potion", wmap)
        game.use_item("potion")
        assert game.potion_active is True
        assert len(game.inventory) == 0

    def test_use_book(self, game, wmap):
        game.current_room = "Library"
        game.take_item("ancient book", wmap)
        game.use_item("book")
        assert game.book_knowledge is True

    def test_use_key_in_antechamber(self, game, wmap):
        from saga import Saga

        Saga().begin(wmap, "You passed through the silver.")
        game.current_room = "Giant's Hall"
        game.take_item("key", wmap)
        game.current_room = "Throne Antechamber"
        game.use_item("key")
        assert game.door_unlocked is True

    def test_use_key_wrong_room(self, game, wmap):
        from saga import Saga

        Saga().begin(wmap, "You passed through the silver.")
        game.current_room = "Giant's Hall"
        game.take_item("key", wmap)
        game.current_room = "Library"
        game.use_item("key")
        assert game.door_unlocked is False

    def test_use_nonexistent_item(self, game, wmap):
        result = game.use_item("banana")
        assert "can't use" in result.lower()


class TestCombat:
    @pytest.mark.parametrize("difficulty,potion,book", [(0.5, False, False), (1.0, False, False),
                                                      (1.5, False, False), (1.5, True, False), (1.0, True, True)])
    def test_direct_attack_costs_a_life_even_with_equipment(self, game, wmap, difficulty, potion, book):
        game.take_item("rusty sword", wmap)
        game.current_room = "Giant's Hall"
        game.potion_active, game.book_knowledge = potion, book
        assert "You die." in game.attack("giant", difficulty, world_map=wmap)
        assert not game.giant_defeated and game.deaths == 1 and not game.potion_active
        assert game.book_knowledge == book and game.inventory[0].name == "rusty sword"

    def test_attack_nothing(self, game):
        assert "nothing to attack" in game.attack("giant").lower()
        assert game.deaths == 0

    def test_crystal_vial_offers_a_clue(self, game, wmap):
        game.current_room = "Hidden Study"
        game.take_item("crystal vial", wmap)
        game.current_room = "Giant's Hall"
        assert "upside down" in game.use_item("crystal vial")
        assert not game.giant_defeated and game.inventory[0].name == "crystal vial"
