# TODO

## 1. Create the repositories

- [ ] Keep the current repository as the main arena repository.
- [ ] Create separate Team A, B, C, and D GitHub repositories, each containing
      that team's `bot.py`.
- [ ] Keep the team repositories separate from the main repository.
- [ ] Keep the team repositories limited to the team's bot file.

## 2. Configure GitHub access

- [ ] Create a separate fine-grained GitHub token for each of Teams A–D.
- [ ] Give each token access only to its team's repository, with only the
      permissions needed to clone and push.
- [ ] Do not put the main repository credentials on the team laptops.
- [ ] Set an expiration date for the tokens.
- [ ] Revoke the tokens after the event.

## 3. Set up the master laptop

- [ ] Create a directory containing the arena and four sibling team repositories:

  ```text
  smash-bots-event/
  ├── arena/
  ├── team-a-bot/
  ├── team-b-bot/
  ├── team-c-bot/
  └── team-d-bot/
  ```

- [ ] Clone the arena and each team's repository into those directories.
- [ ] Use the runner arguments to run two to four sibling bot files, for example:

  ```sh
  python3 arena/runner.py \
    --p1 team-a-bot/bot.py \
    --p2 team-b-bot/bot.py \
    --p3 team-c-bot/bot.py \
    --p4 team-d-bot/bot.py
  ```

- [ ] Do not use nested repositories or submodules.

## 4. Build the Docker image

- [ ] Create a Docker build context for the team agent.
- [ ] Add a `Dockerfile`.
- [ ] Install Pi.
- [ ] Install Git.
- [ ] Install the dependencies needed by the Pi extension and bot repository.
- [ ] Add the custom Pi extension.
- [ ] Add `AGENTS.md`.
- [ ] Add the libmelee and other reference documentation under `docs/`.
- [ ] Add the fixed startup and submit scripts.
- [ ] Do not include GitHub tokens or LLM API tokens in the image.
- [ ] Configure the image to start Pi in the container terminal.
- [ ] Build and test the image locally.

## 5. Create the team launch configuration

- [ ] Use one generic Docker image for all four teams.
- [ ] Create one environment file per team containing its ID, repository URL,
      branch, GitHub token, and LLM provider API token.
- [ ] Do not commit the environment files.
- [ ] Create launchers for all four teams on Windows and Linux.
- [ ] Have each launcher start the image with its environment file.
- [ ] Use ephemeral container storage initially.
- [ ] Have the container clone the configured team repository at startup.

## 6. Implement the restricted Pi extension

- [ ] Load only the admin-installed extension from the image.
- [ ] Disable Pi's general-purpose tools that are not needed.
- [ ] Do not expose `bash`, arbitrary shell commands, or general Git tools to
  the LLM.
- [ ] Add `save_bot()` that writes only the configured team's `bot.py`.
- [ ] Do not allow `save_bot()` to receive an arbitrary file path.
- [ ] Add `submit_bot()` with no repository, branch, path, or command arguments.
- [ ] Make `submit_bot()` run only the predefined Git commit and push workflow.
- [ ] Return only the submission result and commit hash from `submit_bot()`.
- [ ] Do not expose web-fetch/search tools or related skills.
- [ ] Keep context loading enabled for the image-provided `AGENTS.md`.
- [ ] Provide a read capability for the files under `docs/` if the LLM needs to
  inspect the reference documentation.

## 7. Implement team submission

- [ ] Make the submit workflow read only the team's `bot.py`.
- [ ] Stage only `bot.py`; do not use `git add -A`.
- [ ] Commit with a generated team-specific message.
- [ ] Push only to the configured team repository and branch.
- [ ] Keep the GitHub token available only at runtime.
- [ ] Test each team's submission with its own token.

## 8. Implement the master pull-and-launch script

- [ ] Pull the main repository.
- [ ] Pull each team's repository.
- [ ] Run `runner.py` with two to four selected bot paths.
- [ ] Add the Dolphin/ISO arguments needed on the master laptop.

## 9. Test the complete workflow

- [ ] Build the Docker image.
- [ ] Start each team container and confirm Pi can use the reference docs and
      write and submit only that team's `bot.py`.
- [ ] Confirm Pi cannot use the disabled tools.
- [ ] Pull all four repositories on the master laptop.
- [ ] Test two-, three-, and four-player matches using submitted bot files.
