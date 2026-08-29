# Team bot contract

Each team owns only its own directory and may freely modify its `bot.py`.

A team bot must define:

```python
class Bot:
    def __init__(self, port: int):
        ...

    def act(self, gamestate, controller) -> None:
        ...
```

The shared `runner.py` owns Dolphin, `Console`, menu navigation, and the
controller objects. It calls `act()` once per game frame. The bot should only
make decisions and send input through the controller it receives; it should
not create another `Console` or call `console.step()`.

The runner neutralizes each controller before calling `act()`, so a bot should
set the inputs it wants for the current frame. The two bot files are loaded as
separate modules, even though both can be named `bot.py`.
