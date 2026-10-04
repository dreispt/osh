# Osh demo recording for VHS (https://github.com/charmbracelet/vhs)
#
# Record from the repo root:  vhs docs/demo.tape  →  docs/demo.gif
# Then post-process to stay under the file-size hook limit
# (needs gifsicle — `sudo apt install gifsicle`):
#
#   gifsicle -O3 --lossy=150 --colors=40 --scale=0.55 \
#     docs/demo.gif -o docs/demo.gif
#
# The raw VHS render is ~6MB; the install log scroll is what costs —
# tweak sleeps, not content, if it regresses over the limit.
#
# The tape cds into ~/work/my-project, a scratch project holding a
# clone of the OCA `web` addons repo on the 19.0 branch. To recreate it:
#
#   mkdir -p ~/work/my-project && cd ~/work/my-project
#   git init && git checkout -b 19.0
#   git commit --allow-empty -m "Initial commit"
#   git clone --depth 1 -b 19.0 https://github.com/OCA/web web
#
# Prerequisites for recording: `osh` on PATH, a running Docker daemon,
# `odoo:19.0` and `postgres:16` images already pulled (otherwise the
# first init spends minutes pulling), port 8069 free (otherwise add
# `-p <port>` to the `osh odoo` command), and the project ALREADY
# docker-initialized with a `my-project-19.0` database that does NOT
# have `web_responsive` installed — so `osh odoo -i` installs just that
# module without asking to create the db. To set that up:
#
#   cd ~/work/my-project
#   osh docker init 19.0            # prompts: edition, proceed
#   osh odoo --stop-after-init      # prompts: create db? — answer c
#   osh addon uninstall web_responsive --yes   # needs osh-contrib
#   osh docker stop                 # tape shows init bringing the stack up
#
# The tape then re-runs `osh docker init` (idempotent, no prompts on an
# initialized project) and installs the module on the existing db.

Output docs/demo.gif

Set FontSize 11
Set Width 880
Set Height 520
Set Padding 8
Set Margin 0
Set Framerate 20
Set Theme "Dracula"
Set TypingSpeed 40ms
Set Shell "bash"

Hide
Type "cd /home/dreis/work/my-project && export PS1='$ ' && clear"
Enter
Sleep 800ms
Show

Type "# Initialize the Docker runtime"
Enter
Sleep 600ms
Type "osh docker init 19.0"
Enter
Sleep 1500ms
Enter
Sleep 4500ms

Type "# See the command it would run"
Enter
Sleep 600ms
Type "osh odoo --dry-run"
Enter
Sleep 2500ms

Type "# Install a module on the existing database"
Enter
Sleep 600ms
Type "osh odoo -i web_responsive --stop-after-init"
Enter
Sleep 16000ms

Type "# Drop into the Odoo shell"
Enter
Sleep 600ms
Type "osh odoo shell"
Enter
Sleep 5000ms
Type "env.user.name"
Enter
Sleep 2000ms
