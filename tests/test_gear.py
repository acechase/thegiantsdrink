import json

import pytest

from game_session import GameSession
from game_world import Item
from game_world.gear import walk_items
from saves import SaveSlot
from tests.support.openai import scripted


def outcome(session, command):
    return session.turn(command)[-1].text


def test_crafted_pouch_stays_with_belt_across_drop_and_resume(monkeypatch, tmp_path):
    scripted(monkeypatch, [])
    session = GameSession()
    cloak, key, belt = [Item(name, name) for name in ("cloak", "key", "leather belt")]
    session.game.inventory.extend([cloak, key, belt])
    original = session.public_state()["gear"]
    outcome(session, "fashion a pouch from my cloak")
    outcome(session, "put the key inside the pouch")
    outcome(session, "clip the pouch to my belt")
    assert belt.attachments == [cloak] and cloak.contents == [key]
    assert session.game.carried_names() == ["leather belt", "pouch", "key"]
    assert "pouch [key]" in outcome(session, "inventory")
    assert "Attached: pouch." in outcome(session, "examine belt")
    assert "Inside: key." in outcome(session, "look inside the pouch")
    assert "pouch is attached" in outcome(session, "look at myself")
    outcome(session, "drop belt")
    assert session.game.inventory == [] and belt in session.room.items
    slot = SaveSlot.new(tmp_path)
    slot.save(session, [])
    restored, _ = slot.load()
    outcome(restored, "take belt")
    tree = restored.public_state()["gear"]
    assert [tree[0]["id"], tree[0]["attachments"][0]["id"], tree[0]["attachments"][0]["contents"][0]["id"]] == [
        original[2]["id"], original[0]["id"], original[1]["id"]]
    outcome(restored, "take key from the pouch")
    outcome(restored, "detach pouch from belt")
    assert restored.public_state()["inventory"] == ["leather belt", "key", "pouch"]
    assert len(set(walk_items(restored.game.inventory))) == 3


def test_same_name_gear_uses_distinct_instances_for_commands_and_wearing(monkeypatch):
    scripted(monkeypatch, [])
    session = GameSession()
    p1, p2 = (Item("pouch", s) for s in ("red silk", "blue silk"))
    ring = Item("ring", "A ring.")
    session.game.inventory.extend([p1, p2, ring])
    p1.worn = "The red pouch hangs at your waist."
    gear = session.public_state()["gear"]
    one, two = gear[0]["label"], gear[1]["label"]
    assert one != two and gear[0]["worn"] and not gear[1]["worn"]
    clarification = outcome(session, "put ring in pouch")
    assert one in clarification and two in clarification
    assert ring in session.game.inventory
    outcome(session, f"put ring in {two}")
    assert p1.contents == [] and p2.contents == [ring]
    assert "blue silk" in outcome(session, f"examine {two}")
    outcome(session, f"drop {two}")
    assert session.game.wearing() == [(p1, p1.worn)]
    outcome(session, f"take {two}")
    outcome(session, f"take ring from {two}")
    assert session.game.inventory.count(ring) == 1 and not p2.contents


def test_mixed_cycles_wrong_hosts_and_sides_leave_items_in_place(monkeypatch):
    scripted(monkeypatch, [])
    session = GameSession()
    pack, pouch, clip = [Item(name, name) for name in ("backpack", "pouch", "clip")]
    session.game.inventory.extend([pack, pouch, clip])
    outcome(session, "put pouch inside backpack")
    outcome(session, "attach clip to backpack")
    before = session.public_state()["gear"]
    for command in ("put backpack in pouch", "attach backpack to backpack", "put clip in pouch",
                    "take clip out of backpack", "unclip pouch from backpack", "attach backpack to pouch"):
        outcome(session, command)
        assert session.public_state()["gear"] == before, command
    outcome(session, "take clip from backpack")
    assert session.game.inventory == [pack, clip] and pack.contents == [pouch]


@pytest.mark.parametrize("craft", [None, {"name": "sling", "storage": "yes", "attachment_points": True},
                                   {"name": "sling", "storage": False, "attachment_points": False}])
def test_uncommittable_craft_cannot_report_success(monkeypatch, craft):
    session = GameSession()
    bark = Item("bark", "A sheet of bark.")
    session.game.inventory.append(bark)
    room_before = session.room.prose()
    scripted(monkeypatch, [json.dumps(dict(effect="alter", anchor="bark", room_after="Changed room.",
                                            description="A carrier.", craft=craft, reply="You make a basket."))])
    assert "haven't managed" in outcome(session, "fashion a basket from bark")
    assert bark.name == "bark" and bark.contents is None and session.room.prose() == room_before


def test_improvised_gear_can_gain_both_capabilities_without_losing_children(monkeypatch):
    session = GameSession()
    case = Item("reed case", "A case.")
    seed = Item("seed", "A seed.")
    case.contents.append(seed)
    session.game.inventory.append(case)
    fixed = session.room.fixed_text()
    reply = dict(effect="alter", anchor="reed case", room_after=fixed, description="A case with a woven loop.",
                 craft={"name": "reed case", "storage": True, "attachment_points": True}, reply="You weave a loop.")
    scripted(monkeypatch, [json.dumps(reply)])
    assert outcome(session, "sew a loop onto my reed case") == reply["reply"]
    assert case.contents == [seed] and case.attachments == []
    outcome(session, "take seed from case")
    outcome(session, "attach seed to case")
    assert case.attachments == [seed]


def test_unusual_phrasing_can_resolve_to_engine_inventory_actions(monkeypatch):
    session = GameSession()
    holder, charm = Item("leather belt", "A belt."), Item("charm", "A charm.")
    session.game.inventory.extend([charm, holder])
    scripted(monkeypatch, ['{"action":"attach","tool":"charm","target":"belt"}'])
    outcome(session, "could the charm dangle at my belt?")
    assert holder.attachments == [charm]


def test_craft_preserves_full_container_or_refashions_it_once_empty(monkeypatch, tmp_path):
    session = GameSession()
    case, pin = Item("case", "A case."), Item("pin", "A pin.")
    case.contents.append(pin)
    session.game.inventory.append(case)
    reply = json.dumps(dict(effect="alter", anchor="case", room_after=session.room.fixed_text(),
                            description="A small rack.", reply="You make a rack.",
                            craft={"name": "rack", "storage": False, "attachment_points": True}))
    scripted(monkeypatch, [reply, reply])
    assert "Empty the case" in outcome(session, "make a rack from case")
    assert case.name == "case" and case.description == "A case." and case.contents == [pin]
    outcome(session, "take pin from case")
    outcome(session, "make a rack from case")
    assert case.contents is None and case.attachments == [] and case.name == "rack"
    slot = SaveSlot.new(tmp_path)
    slot.save(session, [])
    restored, _ = slot.load()
    assert restored.public_state()["gear"][0]["contents"] is None


def test_newly_crafted_basket_and_same_name_room_items(monkeypatch):
    scripted(monkeypatch, [])
    session = GameSession()
    reeds, other = Item("reeds", "Fresh reeds."), Item("basket", "A willow basket.")
    session.game.inventory.append(reeds)
    session.room.items.append(other)
    outcome(session, "weave the reeds into a basket")
    state = session.public_state()
    assert state["gear"][0]["label"] == reeds.reference and other.reference in state["items"]
    outcome(session, "take candle")
    outcome(session, f"put candle in {other.reference}")
    assert other.contents[0].name == "candle" and not reeds.contents
    outcome(session, f"take {other.reference}")
    assert session.game.inventory[-1] is other


def test_craft_needs_the_capability_the_player_requested(monkeypatch):
    session = GameSession()
    holder = Item("leather case", "A case.")
    session.game.inventory.append(holder)
    scripted(monkeypatch, [json.dumps(dict(effect="alter", anchor="leather case", description="Loops on the case.",
                                           room_after="Changed room.", reply="You add the loops.",
                                           craft={"name": "leather case", "storage": True, "attachment_points": False}))])
    assert "haven't managed" in outcome(session, "sew loops onto the case")
    assert holder.attachments is None and holder.description == "A case."
