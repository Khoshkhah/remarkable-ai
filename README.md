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

```bash
# 1. Clone the repository
git clone https://github.com/Khoshkhah/remarkable-ai.git
cd remarkable-ai

# 2. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 3. Install the package
pip install -e .

# 4. Ready to use!
rm-ai list
```

---

## Multi-Device Configuration


`remarkable-ai` supports multiple tablets connected to your Wi-Fi network.

### 1. View Configured Tablets
```bash
rm-ai devices
```
Output:
```text
Configured reMarkable Tablets:
  [ACTIVE] rm2        - reMarkable 2 (Primary) (Host: rm2)
           rm-alt     - reMarkable 2 (Secondary) (Host: rm-alt)
```

### 2. Switch Active Tablet
```bash
# Switch to your secondary tablet:
rm-ai device rm-alt

# Switch back to your primary tablet:
rm-ai device rm2
```

### 3. Target a Tablet for a Single Command
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

# Target specific tablet
rm-ai --device rm-alt read
```

---

## Slash Commands for Claude Code

This repository includes pre-built Claude Code commands in `.claude/commands/`:

| Slash Command | Action |
| :--- | :--- |
| **`/rm-list`** | Lists all notebooks with last modified dates and page counts |
| **`/rm-read [name]`** | Pulls the page, renders handwriting, and analyzes with Claude Vision |
| **`/rm-tasks [name]`** | Extracts all to-do items and creates an actionable checklist |

---

## Antigravity / Gemini Skill

Defined in [`skills/remarkable-ai/SKILL.md`](skills/remarkable-ai/SKILL.md). When Antigravity or a Gemini Agent is active, it can autonomously interact with your tablet, discover notes, and summarize handwriting on demand.

---

## Full Command Specification & Architecture
For the complete catalog of commands (Obsidian sync, live presentation sharing, daily briefing generator, and second monitor setup), see [COMMANDS.md](COMMANDS.md) and [PLAN.md](PLAN.md).

---

## License
MIT © [khoshkhah](https://github.com/khoshkhah)
