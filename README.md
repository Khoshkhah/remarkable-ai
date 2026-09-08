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
python install.py
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
# Show configured tablets and select interactively:
rm-ai device
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
rm-ai device 2
rm-ai device rm-alt

# Switch back to primary:
rm-ai device 1
rm-ai device rm2
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

# Target specific tablet
rm-ai --device rm-alt read
```

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


---

### 3. Standalone CLI & Scripts
For standalone terminal use or custom scripts with Google Gemini / OpenAI:
```bash
export GEMINI_API_KEY="your-key-here"
rm-ai read "Notebook Name" --action summarize
```


## License
MIT © [khoshkhah](https://github.com/khoshkhah)
