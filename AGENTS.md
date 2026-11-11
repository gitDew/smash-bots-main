# Project Overview

This project is for an **offline event** where kids prompt an LLM to write
Super Smash Bros. Melee bots. The bots run against each other locally in
Dolphin/Slippi.

## Python

Always use python3 on the CLI.

## Event system

`TODO.md` explains how to run the event. The parts are:

- `server/` (`python3 -m server`): team site on port 8000, the only part exposed
  through the tunnel, and admin on port 8001, reachable only from this machine.
  Round rules are enforced in `server/event.py`.
- Each team has a `/t/<uuid>` link for its one prompt writer. One separate
  `/c/<uuid>` link is the helper chat for any kid; it has no access to teams.
- `server/agent.py`: the coding agent. Its loop runs on the host and calls Azure
  OpenAI; every tool runs through `docker exec` in the team's container.
- `server/chat.py`: the helper chat. It has no tools and returns only text.
- `sandbox/`: the image for the per-team containers, which have no network. Bots
  also run there during matches: the runner sends game state in and gets back
  only validated input commands (`container_bot.py`). After changing anything
  in `sandbox/`, rebuild the image; the server then replaces outdated team
  containers by itself and keeps their bots.

Team code must never be imported or run on the host during the event.

## Runner

`runner.py` is the match runner, started by the server for each match. It:

- Starts Dolphin through libmelee.
- Sets up the Melee menus and character select screens.
- Starts each bot in its team's container and creates one controller per player,
  for two to four players.
- Sends each game frame to the bots and applies their validated inputs.
- Stops after the match.

It takes each container and its character in player order, and the stage with
`--stage`. A character without a container has no bot and stands still; the
admin page's demo match uses only those. Matches are free-for-alls, not a
two-versus-two team mode.

```sh
python3 runner.py smash-team-1=FOX smash-team-2=MARTH --stage BATTLEFIELD
python3 runner.py FOX MARTH --stage BATTLEFIELD --no-record   # nobody moves
```

## Stages

The host picks each round's prompt time and stage when opening it. The stages
are listed in `server/stages.py`, which also draws the map the kids see on
their phones from libmelee's own stage numbers.

The macOS arena uses the project-local Slippi Mainline beta; install it with
`./install_dolphin.sh`.

## Replays

The runner uses Dolphin's own frame dump to save a silent H.264 MP4 in
`replays/` as `round_1.mp4`, `round_2.mp4`, and so on. It needs FFmpeg and free
disk space, and existing recordings are never overwritten. If saving fails, the
original dumps stay in `replays/.round_N.dolphin/` with details in
`replays/round_N.log`.

## Team bots

Each team's bot is the single file `/workspace/bot.py` in its container. New
teams start from `server/starter_bot.py`. The host assigns each team a different
character in the admin page; bot code cannot choose one. The bot rules are in
`server/chat_context/rules.md`; the coding agent and the helper chat both read
that file, so change the rules there.

## libmelee

[libmelee](https://github.com/vladfi1/libmelee/) is the Python API connecting
these bots to Slippi Dolphin. The main documentation is at
<https://libmelee.readthedocs.io/en/latest/>. An offline Markdown snapshot of the
pages this project uses is in `docs/libmelee/`.

Keep bot code small, understandable, and offline. Do not add online play.
