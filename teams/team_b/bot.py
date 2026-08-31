"""Team B's bot: do nothing."""

from __future__ import annotations

import melee


class Bot:
    def __init__(self, port: int):
        self.port = port

    def get_character(self) -> melee.Character:
        # Change this value to switch characters. Valid choices are:
        # MARIO, FOX, CPTFALCON, DK, KIRBY, BOWSER, LINK, SHEIK,
        # NESS, PEACH, POPO, PIKACHU, SAMUS, YOSHI, JIGGLYPUFF,
        # MEWTWO, LUIGI, MARTH, ZELDA, YLINK, DOC, FALCO, PICHU,
        # GAMEANDWATCH, GANONDORF, and ROY.
        # Use melee.Character.NAME, for example melee.Character.FALCO.
        # NANA, wireframe characters, GIGA_BOWSER, SANDBAG, and
        # UNKNOWN_CHARACTER are not valid independent selections.
        return melee.Character.DK

    def act(self, gamestate, controller) -> None:
        pass
