"""Team A's bot: press jump periodically."""

from __future__ import annotations

import melee


class Bot:
    def __init__(self, port: int):
        self.port = port
        self.next_jump_frame = 120
        self.previous_frame = -1

    def get_character(self) -> melee.Character:
        # Change this value to switch characters. Valid choices are:
        # MARIO, FOX, CPTFALCON, DK, KIRBY, BOWSER, LINK, SHEIK,
        # NESS, PEACH, POPO, PIKACHU, SAMUS, YOSHI, JIGGLYPUFF,
        # MEWTWO, LUIGI, MARTH, ZELDA, YLINK, DOC, FALCO, PICHU,
        # GAMEANDWATCH, GANONDORF, and ROY.
        # Use melee.Character.NAME, for example melee.Character.FALCO.
        # NANA, wireframe characters, GIGA_BOWSER, SANDBAG, and
        # UNKNOWN_CHARACTER are not valid independent selections.
        return melee.Character.FOX

    def act(self, gamestate, controller) -> None:
        # Game frames restart when a new match starts.
        if gamestate.frame < self.previous_frame:
            self.next_jump_frame = 120
        self.previous_frame = gamestate.frame

        player = gamestate.players.get(self.port)
        if player is None:
            return

        if (
            gamestate.frame >= self.next_jump_frame
            and player.on_ground
            and player.jumps_left > 1
        ):
            controller.press_button(melee.Button.BUTTON_X)
            print(f"Team A / P{self.port}: jump at frame {gamestate.frame}")
            self.next_jump_frame = gamestate.frame + 180
