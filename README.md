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
- **Obsidian Ready**: Export notes and diagrams directly into Obsidian markdown vaults.

## Quickstart Installation

### Option A: One-Step Automated Setup (Recommended)
```bash
# 1. Clone the repository
git clone https://github.com/Khoshkhah/remarkable-ai.git
cd remarkable-ai

# 2. Run the automated installer (installs package, CLI, and AI agent skills)
python3 install.py

# 3. Connect your tablet wirelessly:
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

# Dedicated Fullscreen Clock & Productivity Dashboard (0-Power Standby)
rm-ai dashboard
rm-ai dashboard --suspend

# Push interactive dashboard notebook for physical note-taking
rm-ai dashboard --mode doc --title "Daily Dashboard"

# Live 7-segment digital clock via Virtual Stylus (zero reload / zero restart)
rm-ai clock --pos top-right
rm-ai clock --pos center --duration 60 --clear

# Live vector stroke injection
rm-ai draw line --from 200,300 --to 800,300
rm-ai draw box --at 400,500 --size 300,200
```

---

## Dedicated Fullscreen Clock & Productivity Dashboard

Turn your reMarkable 2 into a dedicated minimalist executive desk display:

- **Zero-Power Standby Display (`--mode standby`)**:
  Updates `/usr/share/remarkable/suspended.png` with a clean Swiss typography clock, full-month calendar with today highlighted, live battery telemetry read from the tablet kernel, daily habits, action items, and notebook ruled lines. When the tablet is asleep, it holds this high-contrast display indefinitely with zero battery drain.
  ```bash
  # Generate and push dashboard to sleep screen:
  rm-ai dashboard

  # Push and put tablet to sleep immediately:
  rm-ai dashboard --suspend

  # Restore original factory sleep screen:
  rm-ai dashboard --restore
  ```

- **Interactive Notebook Document (`--mode doc`)**:
  Generates a 226 DPI vector document and uploads it wirelessly into tablet documents as "Daily Dashboard". You can open it and write notes, check off checkboxes, or sketch directly with your physical Marker stylus:
  ```bash
  rm-ai dashboard --mode doc --title "Daily Dashboard"
  ```

---

## Virtual Stylus & Minimal-Delta Clock Engine

`remarkable-ai` provides direct hardware event injection into `/dev/input/event1` (`Wacom I2C Digitizer`):

- **Real-Time Drawing**: Emulates physical stylus pressure, coordinates, and contact directly into the Linux input subsystem. The active page renders strokes instantly with zero page reload or tablet restart.
- **7-Segment Minimal-Delta State Machine**: When running the digital clock, each digit is broken down into 7 discrete physical segments (A through G). On every second tick, only the segments that change state are toggled (virtual pen to turn on, virtual eraser to turn off).
- **Zero Screen Churn**: Segments that remain unchanged are never touched, achieving the absolute mathematical minimum number of changes per second on the e-ink screen.

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
