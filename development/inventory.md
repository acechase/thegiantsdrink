# Crafted storage and attachments

The player should be able to fashion a pouch from a cloak, put a key inside it, attach the pouch to a belt, and drop the belt. Picking the belt up after a save and reload should recover those same three objects. The tapestry backpack and its existing commands must keep working.

| Step | Implementation | Acceptance check |
| --- | --- | --- |
| 1. Model the equipment | Extend the existing item with an optional list of attached items alongside its optional contents. Give each encountered object a permanent number. Keep the worn description on the individual object. | An empty pouch can accept an item. A belt can accept an attachment. A backpack can do both. Two identical pouches have distinct references. |
| 2. Commit crafting | Resolve straightforward cloth bags and woven baskets locally. For an improvised design, ground the action in an available material and validate a structured result giving its name and capabilities. Apply the change to the source object before publishing success. | Sewing a loop onto a case preserves the seed already inside. An incomplete proposal leaves the material, room and inventory intact. |
| 3. Move whole items | Use one transfer operation for stowing, retrieving, fastening and unfastening. Resolve the source and host, check capability and ancestry, then move the existing object and clear its worn state. | Dropping a parent carries its children with it. A failed transfer, ambiguous name or attempt to make a loop leaves all possessions in place. |
| 4. Present the inventory | Build one inventory tree for the terminal and browser. Show inside and outside separately. Provide direct browser controls and qualify matching names with their item numbers. Include external attachments in character descriptions. | Either pouch can be selected independently. A key is retrievable from a pouch while the pouch stays attached to a belt. |
| 5. Preserve saves and progression | Read earlier signed snapshots, migrate name-based worn state and keep existing container lists. Continue the tapestry repair for the older save that recorded crafting in prose. Include all descendants when reserving gear names for future story arcs. | An older game resumes in the same room with the same journal, possessions and worn gear. New item numbers and relationships survive a second save and reload. |
| 6. Verify and roll out | Run targeted state-transition tests, the full suite and lint. Exercise the drawer on a disposable browser game. Back up the latest committed live turn, resume its signed save at the original address, then inspect the room, journal and inventory. | Live verification adds zero turns. An existing browser tab can refresh to obtain the new drawer. |

An inventory transfer moves one loose carried item into or onto a reachable host. Retrieval moves a direct child back into the player's hands. A carried host and a host set down in the current room both remain reachable. A player can use the qualified name whenever they want to select a particular instance.

## Extending the inventory

An `Item` has two independent fields, `contents` and `attachments`. For each, `None` means the item lacks that capability and `[]` means it supports the capability and is currently empty. Use `is not None` to test a capability. Use a truth check to see whether there are children to move or protect.

For an authored item, pass `storage` and `attachment_points` to the `Item` constructor when the ordinary defaults for its name do not fit. For a new crafting rule, pass the finished form to `Item.refashion`. This preserves the item number and earlier names and checks that each existing child will still have a valid location. An improvised craft supplies `name`, `storage`, and `attachment_points` in the adjudicator's `craft` object. Validate those fields before applying the room changes or reporting success.

Route new packing or fastening verbs through `game_world.gear.transfer`. Use `walk_items` when a rule concerns everything the player possesses, and `inventory_tree` for interface views. The browser reads `room.gear`, whose nodes carry an item number, command label, worn flag, and both child lists. The qualified label, such as `pouch #7`, can be passed back as a command argument.

Run the inventory and save regression tests together when changing item identity or capabilities.

```sh
python -m pytest -q tests/test_gear.py tests/test_saves.py
```
