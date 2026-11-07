"""Team C's starter bot: do nothing."""

from __future__ import annotations

import melee


class Bot:
    def __init__(self, port: int):
        self.port = port

    def get_character(self) -> melee.Character:
        return melee.Character.MARTH

    def act(self, gamestate, controller) -> None:
        pass
