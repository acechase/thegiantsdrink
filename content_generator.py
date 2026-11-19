# content_generator.py
from openai_client import complete

SYSTEM_PROMPT = """You are a narrator for a text adventure game called The Giant's Drink.
Generate atmospheric room descriptions in the style of classic Zork text adventures.
Keep descriptions to 2-3 sentences. Be vivid and evocative but concise.
Use second person ("You see...", "The air smells of...").
Incorporate the player's profile to subtly tailor the atmosphere. Describe only the supplied room and
currently present objects. Keep exits and discoveries consistent with the base description."""


class ContentGenerator:
    """LLM-powered room description generator with caching and static fallback.

    Uses OpenAI to produce atmospheric, Zork-style room descriptions
    tailored to the player's behavior profile. Caches generated descriptions
    per room and profile. Falls back to static descriptions when API
    credentials are missing or the request fails.

    Attributes:
        _cache: Descriptions keyed by room, profile and current scene contents.
    """

    def __init__(self):
        """Initialize ContentGenerator with an empty description cache."""
        self._cache = {}

    def generate_description(self, room_name, profile, description="", items=None, profile_summary=""):
        """Generate an atmospheric room description via OpenAI.

        Caches results per room and profile to avoid redundant API calls.
        Falls back to static description on network or parsing failure.

            room_name (str): Name of the room to describe.
            profile (dict): Player profile (for context, not currently used in prompt).
            description (str, optional): Base/fallback description. Defaults to ''.
            items (list, optional): List of item names present in room. Defaults to None.
            profile_summary (str, optional): Text summary of player style. Defaults to ''.

            str: Generated atmospheric description or fallback description.
        """
        cache_key = (room_name, profile_summary, description, tuple(items or ()))
        if cache_key in self._cache:
            return self._cache[cache_key]

        items_text = ", ".join(items) if items else "nothing of note"
        user_prompt = (
            f"Room: {room_name}\n"
            f"Base description: {description}\n"
            f"Items present: {items_text}\n"
            f"Player profile: {profile_summary or 'unknown'}\n\n"
            f"Generate a 2-3 sentence atmospheric description of this room "
            f"in Zork style."
        )

        generated = complete(SYSTEM_PROMPT, user_prompt, max_tokens=200)
        if not generated:
            return description or f"You are in the {room_name}."
        self._cache[cache_key] = generated
        return generated
