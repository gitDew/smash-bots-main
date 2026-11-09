# Event rules for team bots

These rules override anything in the libmelee documentation.

## The bot file

- A team's bot is the single file `/workspace/bot.py`. Other files are not kept.
- It must define:

```python
import melee

class Bot:
    def __init__(self, port: int):
        self.port = port

    def act(self, gamestate, controller) -> None:
        pass
```

- The host assigns each team a character, and no two teams share one. A bot
  cannot choose or change its character; anything in its code that tries to is
  ignored. A bot can read its own with `gamestate.players[self.port].character`.
- `act()` is called once per game frame (60 times per second) while the player
  is in the game. It should finish in well under 8 milliseconds. A bot that
  answers late plays a little behind; a bot that hangs stands still.
- Only Python's standard library, `melee` and `numpy` are available. Bots have
  no network and cannot read other teams' code.

## Seeing the game

- `gamestate.players` is a dictionary from port number to player state. It
  includes the bot itself (`gamestate.players[self.port]`) and every opponent.
- There can be one, two or three opponents, and an eliminated player may be
  missing from a frame, so always use `.get()` or check first.
- Do NOT use `gamestate.distance`. It only makes sense with two players.
  Work out distances from player positions instead.
- Matches are free-for-alls. There are no teams inside a match.

## Pressing buttons

- Use only the `controller` passed to `act()`. It controls this bot's player.
- All inputs are reset before every `act()` call, so set everything you want
  each frame. To hold a button, press it every frame.
- To press a button repeatedly, it must be released between presses: press on
  some frames and not on others.
- Available controller methods: `press_button`, `release_button`,
  `press_shoulder`, `tilt_analog`, `tilt_analog_unit`, `simple_press`,
  `release_all`.
- Do NOT create a `Console` or `Controller`, call `console.step()`, call
  `controller.flush()`, or handle menus. The runner does all of that.

## The match

- A match has two to four bots, on a random stage.
- The winner is whoever has the most stocks when the time runs out. Ties are
  broken by the lowest damage percentage.
- Stick values go from 0.0 to 1.0, and 0.5 is the middle.
