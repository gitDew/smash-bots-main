# Project Overview

This project is for an **offline event** where kids prompt an LLM to write
Super Smash Bros. Melee bots. The bots run against each other locally in
Dolphin/Slippi.

## Python

Always use python3 on the CLI.

## Runner

`runner.py` is the shared runner. It:

- Starts Dolphin through libmelee.
- Sets up the Melee menus and character select screens.
- Creates one controller per player, for two to four players.
- Calls each bot once per game frame.
- Stops after the match.

Team LLMs should **not modify `runner.py`**. Bot changes belong in the team's
`bot.py` file.

## Team bots

Each team has its own bot file:

- `teams/team_a/bot.py` — default player 1
- `teams/team_b/bot.py` — default player 2
- `teams/team_c/bot.py` — example player 3
- `teams/team_d/bot.py` — example player 4

The runner uses two players by default. Use `--p3` to add a third and `--p4`
to add a fourth (`--p4` requires `--p3`). All four `--p1`–`--p4` arguments
accept bot paths, so a team is not permanently tied to one port. For example:

```sh
python3 runner.py --p3 teams/team_c/bot.py --p4 teams/team_d/bot.py
```

Matches are free-for-alls, not a two-versus-two team mode.

A bot must define this interface:

```python
class Bot:
    def __init__(self, port: int):
        self.port = port

    def get_character(self) -> melee.Character:
        return melee.Character.FOX

    def act(self, gamestate, controller) -> None:
        pass
```

`runner.py` calls `get_character()` once before starting the match. It must
return a selectable `melee.Character` enum value. To switch characters, change
the returned value in the team's `bot.py`, for example:

```python
def get_character(self) -> melee.Character:
    return melee.Character.FALCO
```

Sheik is supported even though it shares Zelda's character-select slot. `NANA`,
wireframe characters, Giga Bowser, Sandbag, and `UNKNOWN_CHARACTER` are not
independently selectable and will be rejected by the runner.

`runner.py` creates one `Bot` object per team and calls `act()` once per
in-game frame while that player's state is present. The bot should read the
current game state and send inputs through the provided controller. It should
only control its own player.

The runner resets all controllers before each frame's calls to `act()`, so the
bot should set the buttons and stick positions it wants for that frame. It should
not create a console or controller, call `console.step()`, flush inputs, or
handle menus. Character selection is also handled by the runner; bots only
provide `get_character()`.

## libmelee

[libmelee](https://github.com/vladfi1/libmelee/) is the Python API connecting
these bots to Slippi Dolphin. It provides:

- `gamestate.players[port]` for player state. Use `self.port` for your own
  player; there can be one, two, or three opponents, and eliminated players
  may be absent from the current frame.
- State such as position, percent, action, `on_ground`, and `jumps_left`.
- Controller methods such as `press_button()`, `release_button()`, and
  `tilt_analog()`.
- Enums such as `melee.Button.BUTTON_A`, `BUTTON_X`, and `BUTTON_MAIN`.

Do not assume `gamestate.distance` represents all opponents in a multiplayer
match; calculate distances from player positions when needed.

The main documentation is at
<https://libmelee.readthedocs.io/en/latest/>. An offline snapshot is checked
into `docs/libmelee/`; start with [`docs/libmelee/README.md`](docs/libmelee/README.md)
for the local Markdown and HTML API references.

Keep bot code small, understandable, and offline. Do not add online play.
