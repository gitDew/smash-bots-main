"""Team B's bot: hold the main stick fully to the left."""

from __future__ import annotations

import melee


class Bot:
    def __init__(self, port: int):
        self.port = port

    def act(self, gamestate, controller) -> None:
        # The runner neutralizes the controller before calling us. Hold left
        # every frame so this bot keeps running in that direction.
        controller.tilt_analog(
            melee.Button.BUTTON_MAIN,
            0.0,  # left
            0.5,  # vertical neutral
        )
