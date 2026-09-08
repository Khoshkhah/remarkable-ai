# remarkable-ai

> **Wireless AI Assistant, Multi-Device Manager, and Agent Slash Commands for reMarkable Tablets**

Connect your reMarkable tablets wirelessly to **Claude, Gemini, and ChatGPT** over local Wi-Fi. Transcribe cursive and multilingual handwriting, interpret hand-drawn architecture diagrams, extract to-do tasks, and sync with Obsidian—without cloud subscriptions or physical cables.

---

## Features

- **100% Wireless**: Communicates over local Wi-Fi via SSH key authentication.
- **Multi-Device Support**: Manage, switch between, and target multiple reMarkable tablets (e.g. Primary and Secondary) seamlessly.
- **v6 Vector Parsing**: Renders `.rm` lines files directly into crisp PNG and SVG vector images.
- **Multi-Model Vision**: Works seamlessly with Anthropic Claude 3.7/3.5 Sonnet, Google Gemini 2.5/2.0, and OpenAI GPT-4o.
- **Claude Code Slash Commands**: Use `/rm-read`, `/rm-tasks`, and `/rm-list` directly inside Claude Code.
- **Antigravity / Gemini Skills**: Native Agent Skill ready for autonomous agent execution.
- **Live e-ink dashboard and clock**: a sleep-screen dashboard with weather and Claude usage, and a clock drawn on the open page with the tablet's own pen.

## Quickstart Installation

### Option A: One-Step Automated Setup (Recommended)
```bash
# 1. Clone the repository
git clone https://github.com/Khoshkhah/remarkable-ai.git
cd remarkable-ai

# 2. Run the automated installer (installs package, CLI, and AI agent skills)
python3 install.py

# 3. Optional: connect or change tablet anytime (if not connected during install):
rm-ai setup
```

### Option B: Manual Virtualenv Setup
```bash
git clone https://github.com/Khoshkhah/remarkable-ai.git
cd remarkable-ai

python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e .

# Configure AI agents automatically:
rm-ai setup
```

---

## Multi-Device Configuration


`remarkable-ai` supports multiple tablets connected to your Wi-Fi network.

### 1. View & Interactively Select Tablet
```bash
# Show configured tablets and select interactively (alias: rm-ai device):
rm-ai devices
```
Output:
```text
Configured reMarkable Tablets:
  1. [ACTIVE] rm2        - reMarkable 2 (Primary) (IP: 192.168.18.18)
  2.          rm-alt     - reMarkable 2 (Secondary) (IP: N/A)

Select tablet number or name to activate (Enter to keep current): 2
Switched active tablet to: [rm-alt] reMarkable 2 (Secondary)
```

### 2. Direct Switch by Name or Number
```bash
# Switch to tablet by name or number:
rm-ai devices 2
rm-ai devices rm-alt

# Switch back to primary:
rm-ai devices 1
rm-ai devices rm2
```

### 3. Add a New Tablet
```bash
# Interactively register a new tablet:
rm-ai add-device

# Or with arguments:
rm-ai add-device --id rm-work --name "Work Tablet" --ip 192.168.18.25
```

### 4. Target a Tablet for a Single Command
You don't need to switch active devices if you just want to run one command on another tablet:
```bash
# List notebooks on your secondary tablet:
rm-ai --device rm-alt list

# Read notes from your secondary tablet:
rm-ai --device rm-alt read "Research"
```


---

## Setting Up a New reMarkable Tablet (Wi-Fi SSH)

To add any reMarkable tablet (reMarkable 1, 2, or Paper Pro) to `remarkable-ai`:

1. **Find your Wi-Fi IP and Root Password**:
   - On the tablet: Go to **Settings > Help > About** (or *Copyrights and licenses*).
   - Under **GPLv3 Compliance**, note the Wi-Fi IP (e.g. `192.168.18.19`) and password.
2. **Enable Wi-Fi SSH Daemon** (Firmware 3.14+ / 3.28+):
   - Connect once via USB or local network:
     ```bash
     mkdir -p /home/root/.config/remarkable
     touch /home/root/.config/remarkable/rm_enable_ssh_wifi_marker
     systemctl start dropbear-wlan.socket
     ```
3. **Install Passwordless SSH Key**:
   - Copy your public key to the tablet:
     ```bash
     cat ~/.ssh/id_ed25519.pub | ssh root@<TABLET_IP> "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
     ```
4. **Add to SSH Config (`~/.ssh/config`)**:
   ```text
   Host rm-alt
       HostName <TABLET_IP>
       User root
       IdentityFile ~/.ssh/id_ed25519
       StrictHostKeyChecking accept-new
   ```

---

## CLI Commands

```bash
# List all notebooks on your reMarkable wirelessly
rm-ai list

# Read and analyze the latest modified notebook with AI
rm-ai read

# Read a specific notebook and extract action items
rm-ai read "Meeting Notes" --action tasks

# Read a specific page
rm-ai read "D-Wave" --page 5 --action transcribe

# Push a PDF or Markdown document wirelessly (creates folder if missing)
rm-ai push report.pdf --folder "Work/Reports"
rm-ai push notes.md --folder "AI-Summaries" --title "Morning Brief"

# List configured tablets or switch active tablet
rm-ai devices
rm-ai devices 2

# Target specific tablet for a single command
rm-ai --device rm-alt read

# Sleep-screen dashboard: date, calendar, weather, Claude usage, battery (refreshes without touching the app)
rm-ai dashboard --city Burnaby            # the city is remembered; add --suspend to see it right away
rm-ai dashboard --restore                 # factory sleep screen back

# Dashboard as a notebook page, kept live by the pen: HH:MM every minute, usage rows when they change
rm-ai dashboard --mode doc --live         # pushes the page once (one reload), then draws on it
rm-ai dashboard --mode doc --live --no-push   # reuse the page already open on the tablet

# Optional extras on the dashboard
export ANTHROPIC_ADMIN_KEY="sk-ant-admin..."   # API spend/tokens line (Console > Settings > Admin keys; needs an organization)

# Live digital clock drawn on the open page with the virtual pen (no reload)
rm-ai clock                               # built-in defaults: center, large, pencil at pressure 4000, 28px bars, frame, every 2 s
rm-ai clock --pos center --duration 60 --clear
rm-ai clock --thickness 28 --frame --interval 2
rm-ai clock --pressure 4000               # the pencil gets darker and wider with pressure; the ballpoint 12 -> 17 px
rm-ai clock --pos center --size large --pressure 4000 --pen-width 12 --thickness 28 --frame --interval 2 --save-defaults

# Live vector stroke injection
rm-ai draw line --from 200,300 --to 800,300
rm-ai draw box --at 400,500 --size 300,200
```

---

## Dashboard

Three ways to put a dashboard on the tablet, all rendered by the PC from the same template
(date, month calendar with today marked, weather, Claude usage, battery, priorities):

- **Sleep screen** (`rm-ai dashboard`, the default `--mode standby`): the image becomes the tablet's
  sleep screen. It costs no battery and never touches the running app. The tablet paints it once, at
  the moment it falls asleep, and nothing on it can change until the tablet wakes and sleeps again,
  so it carries the date and an "Updated" stamp but no clock. Keep it fresh with a scheduled job, so
  that every new sleep shows current weather, usage and calendar:
  ```
  */15 * * * * cd ~/projects/remarkable-ai && .venv/bin/python rm_ai.py dashboard --mode standby >> ~/.config/remarkable-ai/dashboard.log 2>&1
  ```
- **Notebook page** (`--mode doc`): the same page as a PDF document you can write on. Every push
  restarts the tablet's app, so this is for occasional use.
- **Live page** (`--mode doc --live`): the page is pushed once as a template with a blank clock area
  and printed usage rows, and from then on the virtual pen keeps it current: the time is rewritten
  every minute in the page's own font, the usage rows whenever a value or reset time changes, all
  without reloads. At midnight a fresh template is pushed (one reload a day) for the date and
  calendar. `--no-push` reuses the page already open; `--usage-interval` sets the usage refresh in minutes.

What the dashboard shows and where it comes from:

| Block | Source | Needs |
| :--- | :--- | :--- |
| Weather: now, feels-like, wind, rain chance, today's high/low, sunrise/sunset, 5-day forecast | [Open-Meteo](https://open-meteo.com) | `--city` once (remembered) |
| Claude usage: session, week, week for the scoped model (e.g. Fable), with reset times | the usage behind Claude Code's `/usage`, via the login in `~/.claude/.credentials.json` | a Claude Code login |
| Claude API spend and tokens | Anthropic Usage & Cost Admin API | `ANTHROPIC_ADMIN_KEY` (organization accounts only) |
| Battery level | the tablet over SSH | – |
| Priorities | `--task "..."` (repeatable) | – |

---

## Live Clock (`rm-ai clock`)

A digital clock drawn on whatever page is open, with the tablet's own pen and eraser, so it updates
in place with no reload: only the segments that change are erased and redrawn.

- **Position and size**: `--pos top-right | top-left | center | bottom-right | X,Y`, `--size small |
  medium | large | xlarge`. The corner presets keep clear of the toolbar and the notebook menu.
- **Look**: `--thickness N` builds bars from overlapping passes (or `max`); `--pressure 0..4095`;
  `--frame` draws a rounded frame; `--format HH:MM:SS | HH:MM | MM:SS`.
- **Pen**: you pick the pen on the tablet. At start the clock draws one short test line and reads its
  width back from the page file to size its erase sweeps; `--pen-width` / `--ink-width` skip that.
- **Cadence**: `--interval 2` shows the real time every 2 seconds, `--duration`, `--once`, `--clear`.
- **Defaults**: built in for the pencil at full pressure (center, large, 28 px bars, frame, every 2 s);
  add `--save-defaults` to any invocation to make its options your defaults instead.

Things the tablet decides, not the tool: after "Erase all" the eraser stays the active tool and
every stroke erases, so tap the pen first; the pencil gets both darker and wider with pressure; the
page must stay open and the tablet awake.

## How the virtual pen works

Strokes are 16-byte Linux input events written straight into the tablet's Wacom digitizer over one
SSH pipe, so they appear like real handwriting without any reload. The tablet smooths and predicts
pen motion over time, so events are paced like a real pen (one frame every few milliseconds), the
pen hovers before touching, every eraser stroke is followed by a short pause (the tablet drops
strokes sent while it is still busy erasing), and pen speed is kept moderate because the pencil lays
down less ink when moved fast. What actually landed on a page is verified by reading the page's
stroke file back after the tablet's autosave; that loop is how all of the above was calibrated.

---

## AI Agents Integration Guide

The repository includes preconfigured agent integrations for Google Antigravity, Gemini, and Claude Code.

### Zero Configuration (Workspace Level)
- **Google Antigravity**: If you open this cloned repository in Antigravity IDE or run the Antigravity CLI, the workspace skill at `.agents/skills/remarkable-ai/SKILL.md` is detected automatically with zero manual steps.
- **Claude Code**: If you run Claude Code inside this cloned repository, the slash commands at `.claude/commands/` are loaded automatically.

### Automated Global Setup
To make reMarkable agent commands and skills available across your entire computer (in any project directory):
```bash
rm-ai setup
```
This single command automatically:
1. Installs the Antigravity / Gemini Skill to `~/.gemini/config/skills/remarkable-ai/`
2. Installs Claude Code slash commands (`/rm-list`, `/rm-read`, `/rm-tasks`) to `~/.claude/commands/`
3. Tests wireless SSH connectivity to your reMarkable tablet.

### How to Use With Each Agent

#### 1. Google Antigravity & Gemini Agents
The agent natively runs `rm-ai` in the background and views the rendered images using its built-in multimodal vision. You can prompt naturally or use slash-style prompts:
- "List my reMarkable notebooks."
- "Read page 5 of 'D-Wave' and transcribe the notes."
- "Extract my tasks from 'Project Planning'."
- "Switch active tablet to secondary reMarkable."

#### 2. Claude Code (Terminal Slash Commands)
Inside Claude Code, you can use dedicated slash commands:
| Slash Command | Action |
| :--- | :--- |
| `/rm-list` | Lists all notebooks with last modified dates and page counts |
| `/rm-read [name]` | Pulls the page, renders handwriting, and analyzes with Claude Vision |
| `/rm-tasks [name]` | Extracts all to-do items and creates an actionable checklist |
| `/rm-devices` | Lists configured tablets and displays active device status |
| `/rm-dashboard` | Turns tablet into fullscreen desk clock and productivity dashboard |
| `/rm-clock` | Runs real-time 7-segment clock on active page via Virtual Stylus |
| `/rm-push <file>` | Wirelessly uploads PDF or Markdown document to tablet |
| `/rm-export [name]` | Exports note with embedded vector SVG/PNG assets and transcript |
| `/rm-setup` | Runs interactive setup wizard and refreshes agent slash commands |


---

### 3. Standalone CLI & Scripts
For standalone terminal use or custom scripts with Google Gemini / OpenAI:
```bash
export GEMINI_API_KEY="your-key-here"
rm-ai read "Notebook Name" --action summarize
```


## License
MIT © [khoshkhah](https://github.com/khoshkhah)
