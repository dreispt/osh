# Osh demo recording for VHS (https://github.com/charmbracelet/vhs)
#
# Record from the repo root:  vhs docs/demo.tape  →  docs/demo.gif
# Then post-process to stay under the file-size hook limit
# (needs gifsicle — `sudo apt install gifsicle`):
#
#   gifsicle -O3 --lossy=120 --colors=64 --scale=0.4 \
#     docs/demo.gif -o docs/demo.gif
#
# The raw VHS render is ~6MB — the live `-i` install scroll is what
# costs. If it regresses over the 1MB hook limit, piping the install
# through `tail -20` cuts the size dramatically; otherwise tweak
# sleeps, not content.
#
# The tape cds into ~/work/my-project, a scratch project holding a
# clone of the OCA `web` addons repo on the 19.0 branch. To recreate it:
#
#   mkdir -p ~/work/my-project && cd ~/work/my-project
#   git init && git checkout -b 19.0
#   git commit --allow-empty -m "Initial commit"
#   git clone --depth 1 -b 19.0 https://github.com/OCA/web web
#
# Prerequisites for recording: `osh` on PATH, a local PostgreSQL server,
# and the project ALREADY venv-initialized with a `my-project-19.0`
# database that does NOT have `web_responsive` installed — so
# `osh odoo -i` installs just that module without asking to create the
# db. To set that up:
#
#   cd ~/work/my-project
#   osh init 19.0 --runtime=venv    # prompts: edition (first run), proceed
#   osh odoo --stop-after-init      # creates my-project-19.0 (prompt: c)
#   osh db uninstall web_responsive --yes   # needs osh-contrib
#
# A bare `osh init` asks which runtime to use only on the very first run
# (the answer is remembered; `--runtime=ask` asks again) — once a project
# is initialized it reports status instead. The tape opens on that
# status report, then re-runs the venv init (idempotent — re-init skips
# the pip reinstall when requirements are unchanged) and installs the
# module on the existing database.

Output docs/demo.gif

Set FontSize 20
Set Width 1600
Set Height 900
Set Padding 8
Set Margin 0
Set Framerate 20
Set Theme "Dracula"
Set TypingSpeed 40ms
Set Shell "bash"

Hide
Type "cd /home/dreis/work/my-project && export PS1='\[\e[38;5;220m\]$\[\e[0m\] ' OSH_URL_WATCH=0 && clear"
Enter
Sleep 800ms
Show

Type "# Initialize the venv runtime"
Enter
Sleep 600ms
Type "osh init 19.0 --runtime=venv"
Enter
Sleep 1500ms
Enter
Sleep 3000ms

Type "# A bare 'osh init' reports the project status"
Enter
Sleep 600ms
Type "osh init"
Enter
Sleep 2500ms

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
