# Running the event

## Before the event

- [ ] Install everything on the main machine:

  ```sh
  python3 -m venv .venv
  source .venv/bin/activate
  python3 -m pip install -r requirements.txt
  brew install ffmpeg
  ./install_dolphin.sh
  docker build -t smash-bots-sandbox -f sandbox/Dockerfile .
  ```

- [ ] Put the Melee ISO at `smash_bros_melee.iso`, or set its path in the admin
      settings.
- [ ] Enter the **Azure base URL**, **API key** and **model name** in the admin
      settings, then press **Test connection**. It tries plain answers,
      streaming, tool calls, reasoning and the safety filter.

- [ ] Do a full dry run, including a real match on the projector, then press
      **Reset event** in the admin page.

## On the day

1. Start the server: `python3 -m server`
2. Open the admin page on the main machine: <http://localhost:8001>
3. Start the tunnel, which exposes only the team site:

   ```sh
   cloudflared tunnel --url http://localhost:8000
   ```

4. Paste the tunnel's URL into **Public URL** in the admin settings. A quick
   tunnel's URL changes whenever cloudflared restarts; after that, paste the new
   URL and show the QR codes again.
5. Add the teams, giving each one a different character. Give each team's prompt
   writer their team's QR code.
6. Show **Helper chat QR** on a screen so that every kid can scan it.

## Before round 1

- Explain the game: under **Next round**, pick round 1's stage, tick the teams
  and press **Demo: nobody moves**. Every character stands still because nobody
  has told their bot what to do yet. Press **Stop match** when you are done.

## Each round

1. Under **Next round**, choose the prompt time in minutes and the stage, then
   press **Open next round**. **Show stage** puts the map on screen; the kids
   also see it on their phones. From round 2 on, each prompt writer is asked
   whether to keep improving their bot or to start clean.
2. Teams write their prompts. Use **+1 minute** or **Close round now** if needed.
3. When the time is up, the agents code. **Agents live** in the admin page shows
   every step in detail, and each team page shows a simple version.
4. Tick 2 to 4 teams and press **Start match**. It is played on the round's
   stage and lasts until only one player is left; with **Lives per player** at
   1, a player is out after their first knockout. The winner goes on the
   **Scoreboard**; **Show scoreboard** puts it on screen. **Stop match** ends a
   match with no winner. For a safety limit, set **Time limit** in the settings;
   when it runs out, the leader wins.

If an agent fails, the team's previous bot still fights. **Retry agent** runs it
again, and **Details** lets you roll a team back to any earlier version.
