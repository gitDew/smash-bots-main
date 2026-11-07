# Team bot contract

Each team owns only its own directory and may freely modify its `bot.py`.

A team bot must define:

```python
class Bot:
    def __init__(self, port: int):
        ...

    def get_character(self) -> melee.Character:
        return melee.Character.FOX

    def act(self, gamestate, controller) -> None:
        ...
```

`get_character()` is called once before the match starts. Change its return
value in `bot.py` to switch characters, for example:

```python
def get_character(self) -> melee.Character:
    return melee.Character.FALCO
```

The runner validates the enum value and handles character-select menus. Sheik
is supported; `NANA`, wireframe characters, Giga Bowser, Sandbag, and
`UNKNOWN_CHARACTER` are not independently selectable.

The shared `runner.py` owns Dolphin, `Console`, menu navigation, and the
controller objects. It calls `act()` once per game frame while that player's
state is present. The bot should only
make decisions and send input through the controller it receives; it should
not create another `Console` or call `console.step()`. Character selection is
also owned by the runner through `get_character()`.

The runner neutralizes each controller before calling `act()`, so a bot should
set the inputs it wants for the current frame. Two to four bot files are loaded
as separate modules, even when all are named `bot.py`. Use `self.port` to find
your player in `gamestate.players`; there may be up to three opponents, and
eliminated players may not appear in a frame.

From the arena directory, run the default two-player match with:

```sh
python3 runner.py
```

Add a third or fourth player by specifying their bot paths:

```sh
python3 runner.py --p3 teams/team_c/bot.py
python3 runner.py --p3 teams/team_c/bot.py --p4 teams/team_d/bot.py
```

`--p4` requires `--p3`. Use `--p1` and `--p2` to replace the default bots;
all paths may also point to external team repositories. Matches are free-for-all
stock matches. At the runner's time limit, the unique player with the most
stocks wins; tied stocks are compared by lowest damage percentage. A tie for
first is reported as having no unique winner.
