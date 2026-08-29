"""Team A's bot: press jump periodically."""

from __future__ import annotations

import melee


class Bot:
    def __init__(self, port: int):
        self.port = port
        self.next_jump_frame = 120
        self.previous_frame = -1

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
