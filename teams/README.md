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

From the arena directory, run a four-player match with all four default bots:

```sh
python3 runner.py
```

For a smaller match, use:

```sh
python3 runner.py --players 2
python3 runner.py --players 3
```

Use `--p1`, `--p2`, `--p3`, or `--p4` to replace a default bot with another
file; paths may also point to external team repositories. Matches are free-for-all
stock matches. At the runner's time limit, the unique player with the most
stocks wins; tied stocks are compared by lowest damage percentage. A tie for
first is reported as having no unique winner.

## Video replays

On macOS, the runner uses Dolphin's own frame dump to save an H.264 MP4 in
`replays/`: `round_1.mp4`, `round_2.mp4`, and so on. It records the game
itself rather than the desktop, at the game's frame rate. Existing recordings
are never overwritten. Videos are **silent** and are ignored by Git.

Before the event, install FFmpeg (`brew install ffmpeg`) and the project-local
Slippi Mainline beta with:

```sh
./install_dolphin.sh
```

The installer keeps this beta separate from your existing Slippi installation.
The beta uses Metal graphics by default on macOS. If you need to fall back to
OpenGL, run `python3 runner.py --gfx-backend OGL`.
To save videos somewhere else, use `python3 runner.py --replay-dir ~/melee-replays`.

Press Ctrl-C to stop a match early. The runner stops Dolphin and converts its
frame dump to MP4 before exiting. A separate supervisor also saves the recording
if the runner is forcibly killed. Because Dolphin is stopped abruptly, the last
few seconds may be lost. If saving fails, the original dumps remain in a hidden
`replays/.round_N.dolphin/` directory, with details in `replays/round_N.log`.
Recording needs free disk space.

The provided Dolphin installer and hardware encoder are for macOS. To run a
match without recording, use `python3 runner.py --no-record`.
