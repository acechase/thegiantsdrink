"""Instructions and wire format for generated story arcs."""

PROMPT = """You plan the next arc of an endless adventure that began in the castle of The Giant's Drink.
The world is a surreal, dreamlike game that quietly studies the player and reshapes itself in response.
Plan an arc of 8 to 12 rooms with branching exploration, two linked hidden passages, and a final obstacle.
- Begin where the supplied horizon promised. Keep continuity with earlier arcs, the chronicle and carried items.
- Read the player's profile and recent commands. Make this challenge test something their habits have not:
  if they rely on force, let force alone fail; if they are cautious, demand a bold act; if they hoard, ask for a gift.
- Begin with at least four connected rooms and a junction with three ways to explore, excluding the way back to the
  previous arc. Both passage landmarks should be accessible from the start, in different rooms. Put the first passage's
  tool in another accessible room. Behind the first passage place the second passage's distinct tool, so the player
  returns to explore the other branch. Behind the second passage put the final obstacle and its solution tools in
  different rooms. None of these rewards should have a route around the passage meant to protect them.
- Give each room a distinct purpose, landmark or discovery. Plant sensory clues that connect the tools with their
  landmarks across rooms, so players can infer what to try. Use short, recognizable names without ' on ' in them.
- Give 1 to 3 planned solutions to the final obstacle. Each uses a tool reached through the second passage.
  Clever unplanned solutions may also succeed at the final obstacle.
- The player can examine, take or change anything you describe, so describe concrete, tangible things.
  Mention each item only in its room_text, never in the room description: the engine shows room_text while it's there.
  Mention only exits that exist. Describe a closed passage through its landmark, not as an open exit.
  Make the barrier show a tangible danger and a subtle clue to surviving it.
  Put the barrier sentence in the challenge room's description.
- Escalate gradually. Stay original and in-world, with no talk of game mechanics.
Return only JSON with this shape:
{"title": str, "premise": "1-2 sentences shown on arrival, hinting at what must be overcome but not how",
 "start": room name,
 "rooms": [{"name": str, "description": "2-4 sentences", "exits": {"<direction>": room name},
            "items": [{"name": "short noun phrase", "description": str, "room_text": "sentence placing it in the room"}],
            "scenery": [{"name": "noun phrase used in the description", "description": str}]}],
 "passages": [{"room": room containing the landmark, "landmark": scenery name, "tool": local item name,
               "direction": direction from room to destination, "destination": room name,
               "reveal": "what happens when the tool opens it", "visible": "how the opened route looks"}],
 "challenge": {"room": room name, "obstacle": scenery name in that room, "blocks": direction it bars,
               "barrier": "sentence describing how it bars the way",
               "solutions": [{"tool": item name, "approach": "a few words",
                              "outcome": "1-2 sentences narrating, in the second person, what happens"}],
               "room_after": "the challenge room's description once the obstacle is overcome",
               "obstacle_after": "the obstacle's appearance once overcome"},
 "horizon": "1-2 sentences glimpsed beyond the obstacle once it is overcome; the next arc begins there"}
Directions are north, south, east, west, up and down. Room names must be new. The start room's reserved_start_exit
leads back the way the player came: do not use it. The challenge's blocks direction must not be another exit of
the challenge room. Give exactly two passages, in puzzle order. Omit both directions of each hidden passage from
rooms[].exits. Leave both of those directions free for the engine to connect on opening. Normal exits should allow
backtracking. Item names must be unique and must differ from carried items."""
