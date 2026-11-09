"""The fighters the host can give to a team."""

from __future__ import annotations

import melee

from runner import is_selectable_character


DISPLAY_NAMES = {
    "CPTFALCON": "Captain Falcon",
    "DK": "Donkey Kong",
    "DOC": "Dr. Mario",
    "GAMEANDWATCH": "Mr. Game & Watch",
    "POPO": "Ice Climbers",
    "YLINK": "Young Link",
}

# Internal name to display name, in alphabetical order for the admin menus.
CHARACTERS = dict(sorted(
    (
        (character.name, DISPLAY_NAMES.get(character.name, character.name.title()))
        for character in melee.Character if is_selectable_character(character)
    ),
    key=lambda item: item[1],
))
