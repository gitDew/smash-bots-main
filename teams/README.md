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
controller objects. It calls `act()` once per game frame. The bot should only
make decisions and send input through the controller it receives; it should
not create another `Console` or call `console.step()`. Character selection is
also owned by the runner through `get_character()`.

The runner neutralizes each controller before calling `act()`, so a bot should
set the inputs it wants for the current frame. The two bot files are loaded as
separate modules, even though both can be named `bot.py`.
