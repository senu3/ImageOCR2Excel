from __future__ import annotations

from ImageOCR2Excel.models import DEFAULT_SET_DEFINITION, SetDefinition, find_set_definition


SET_PRESET_DEFAULT = "default"

EXAMPLE_SET_DEFINITIONS = (DEFAULT_SET_DEFINITION,)


def example_set_definition(preset: str) -> SetDefinition:
    """Return a bundled generic example, also used to read version-1 templates."""

    return find_set_definition(EXAMPLE_SET_DEFINITIONS, preset)

