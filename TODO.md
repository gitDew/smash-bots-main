# TODO

## 1. Create the repositories

- [ ] Keep the current repository as the main arena repository.
- [ ] Create a Team A GitHub repository containing Team A's `bot.py`.
- [ ] Create a Team B GitHub repository containing Team B's `bot.py`.
- [ ] Keep the team repositories separate from the main repository.
- [ ] Keep the team repositories limited to the team's bot file.

## 2. Configure GitHub access

- [ ] Create a fine-grained GitHub token for Team A.
- [ ] Give the token access only to the Team A repository.
- [ ] Grant only the permissions needed to clone, commit, and push.
- [ ] Create a separate token for Team B with access only to the Team B repository.
- [ ] Do not put the main repository credentials on either team laptop.
- [ ] Set an expiration date for the tokens.
- [ ] Revoke the tokens after the event.

## 3. Set up the master laptop

- [ ] Create a directory containing three sibling repositories:

  ```text
  smash-bots-event/
  ├── arena/             # main repository
  ├── team-a-bot/        # Team A repository
  └── team-b-bot/        # Team B repository
  ```

- [ ] Clone the main repository into `arena/`.
- [ ] Clone the Team A repository into `team-a-bot/`.
- [ ] Clone the Team B repository into `team-b-bot/`.
- [ ] Use the existing runner arguments to run the sibling bot files:

  ```text
  python arena/runner.py \\
    --p1 team-a-bot/bot.py \\
    --p2 team-b-bot/bot.py
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

- [ ] Use one generic Docker image for both teams.
- [ ] Create a Team A environment file containing:
  - [ ] Team ID.
  - [ ] Team A repository URL.
  - [ ] Team A branch.
  - [ ] Team A GitHub token.
  - [ ] LLM provider API token.
- [ ] Create an equivalent Team B environment file.
- [ ] Do not commit the environment files.
- [ ] Create `run-team-a` and `run-team-b` launch scripts.
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
- [ ] Test Team A submission with the Team A token.
- [ ] Test Team B submission with the Team B token.

## 8. Implement the master pull-and-launch script

- [ ] Pull the main repository.
- [ ] Pull the Team A repository.
- [ ] Pull the Team B repository.
- [ ] Run `runner.py` with the Team A and Team B bot paths.
- [ ] Add the Dolphin/ISO arguments needed on the master laptop.
- [ ] Add the runner safety timeout if desired.

## 9. Test the complete workflow

- [ ] Build the Docker image.
- [ ] Start the Team A container.
- [ ] Confirm Pi can use the reference documentation.
- [ ] Confirm Pi can write Team A's `bot.py`.
- [ ] Confirm Pi cannot use the disabled tools.
- [ ] Confirm `submit_bot()` creates and pushes a Team A commit.
- [ ] Repeat the process for Team B.
- [ ] Pull both repositories on the master laptop.
- [ ] Launch a match using the two submitted bot files.
