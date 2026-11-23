"""Scene models and the public entry points for room illustration."""

from importlib import import_module

from .scenes import (
    AVATAR_STYLE,
    CUTSCENE_STYLE,
    OUTFIT_RULES,
    PAINTING,
    PORTRAIT_STYLE,
    STYLE,
    Avatar,
    Cutscene,
    Portrait,
    Scene,
)

# Loading a saved scene needs only its data classes. Load the HTTP and rendering
# modules when a caller asks for the companion itself.
_RUNTIME_EXPORTS = {
    "DEFAULT_IMAGE_MODEL": ".images",
    "generate_image": ".images",
    "RoomCompanion": ".renderer",
    "HERE": ".server",
    "ViewerHandler": ".server",
    "launch_tab": ".server",
    "open_companion": ".server",
}

__all__ = [
    "AVATAR_STYLE", "CUTSCENE_STYLE", "OUTFIT_RULES", "PAINTING", "PORTRAIT_STYLE", "STYLE",
    "Avatar", "Cutscene", "Portrait", "Scene", *_RUNTIME_EXPORTS,
]


def __getattr__(name):
    """Resolve public runtime components while keeping scene imports lightweight."""
    if name not in _RUNTIME_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(_RUNTIME_EXPORTS[name], __name__), name)
    globals()[name] = value
    return value
