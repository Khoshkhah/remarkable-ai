# 📝 remarkable-ai

> **Wireless AI Assistant, Multi-Agent Skills, and Slash Commands for reMarkable 2**

Connect your reMarkable 2 tablet wirelessly to **Gemini, Claude, and ChatGPT** over local Wi-Fi. Transcribe cursive and multilingual handwriting, interpret hand-drawn diagrams, extract to-do tasks, and sync with Obsidian—without cloud subscriptions or physical cables.

---

## ✨ Features

- 📶 **100% Wireless**: Communicates over local Wi-Fi via SSH key authentication.
- 🖋️ **v6 Vector Parsing**: Renders `.rm` lines files directly into crisp PNG and SVG vector images.
- 🤖 **Multi-Model Vision**: Works seamlessly with Google Gemini 2.5/2.0, Claude 3.7/3.5 Sonnet, and OpenAI GPT-4o.
- ⚡ **Claude Code Slash Commands**: Use `/rm-read`, `/rm-tasks`, and `/rm-list` directly inside Claude Code.
- 🧠 **Antigravity / Gemini Skills**: Native Agent Skill ready for autonomous agent execution.
- 📓 **Obsidian Ready**: Export notes and diagrams directly into Obsidian markdown vaults.

---

## 🚀 Quickstart

### 1. Requirements
- reMarkable 2 tablet connected to your local Wi-Fi.
- Passwordless SSH configured (`Host rm2`).
- Python 3.10+ (or WSL).

### 2. Installation
```bash
git clone https://github.com/khoshkhah/remarkable-ai.git
cd remarkable-ai
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

---

## 💻 CLI Commands

```bash
# List all notebooks on your reMarkable wirelessly
rm-ai list

# Read and analyze the latest modified notebook with AI
rm-ai read

# Read a specific notebook and extract action items
rm-ai read "Meeting Notes" --action tasks

# Read a specific page
rm-ai read "D-Wave" --page 2 --action transcribe
```

---

## ⚡ Slash Commands for Claude Code

This repository includes pre-built Claude Code commands in `.claude/commands/`:

| Slash Command | Action |
| :--- | :--- |
| **`/rm-list`** | Lists all notebooks with last modified dates and page counts |
| **`/rm-read [name]`** | Pulls the page, renders handwriting, and analyzes with Claude Vision |
| **`/rm-tasks [name]`** | Extracts all to-do items and creates an actionable checklist |

---

## 🧠 Antigravity / Gemini Skill

Defined in [`skills/remarkable-ai/SKILL.md`](skills/remarkable-ai/SKILL.md). When Antigravity or a Gemini Agent is active, it can autonomously interact with your tablet, discover notes, and summarize handwriting on demand.

---

## 📄 License
MIT © [khoshkhah](https://github.com/khoshkhah)
