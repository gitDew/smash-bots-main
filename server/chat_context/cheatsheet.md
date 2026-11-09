# What your bot can see

Your bot looks at the game 60 times every second.

## Everyone in the match

Your bot can see **every** player, including all your opponents. For each one:

- `position.x` and `position.y`: where they are
- `percent`: their damage
- `stock`: lives left
- `character`: who they are playing
- `action`: what they are doing right now, like running or attacking
- `action_frame`: how far into that move they are
- `facing`: which way they are looking
- `on_ground` and `off_stage`: are they standing, or off the edge?
- `jumps_left`: jumps they have left
- `shield_strength`: how strong their shield is
- `invulnerable`: can they be hit right now?
- `hitstun_frames_left`: how long they are stuck after being hit
- `controller_state`: the buttons they are pressing

## The rest of the game

- `gamestate.players`: all the players, by port number
- `gamestate.stage`: which stage you are on
- `gamestate.frame`: how many frames have passed
- `gamestate.projectiles`: things flying around, like lasers and fireballs

## What your bot can do

Every frame it can move the sticks and press buttons:

- Main stick: walk, run, duck, jump, aim
- C stick: quick smash attacks
- A: attacks
- B: special moves
- X or Y: jump
- L or R: shield and dodge
- Z: grab

## What your bot cannot do

- See another team's code or plans
- Control anyone else's character
- Pick a different character: the host chooses those
- Use the internet
- Change the stage or the rules

## Good ideas to try

- Who is closest? Who has the most damage?
- What should my bot do when it falls off the edge?
- Should it attack, shield or run away when someone is close?
- What is your character best at?
