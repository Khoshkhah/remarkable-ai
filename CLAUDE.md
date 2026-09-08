# Claude Code Instructions for reMarkable AI

This repository connects AI agents (Claude, Antigravity, Gemini) wirelessly to reMarkable tablets over local Wi-Fi SSH.

## Setup on a New Machine

1. Clone the repository:
   ```bash
   git clone https://github.com/Khoshkhah/remarkable-ai.git
   cd remarkable-ai
   ```

2. Run the one-step installer:
   ```bash
   python install.py
   ```
   Or install manually in an active virtual environment:
   ```bash
   pip install -e .
   ```

3. Ensure passwordless SSH access to the tablet:
   - Find tablet IP under: Settings > Help > Copyrights and licenses > General information (GPLv3).
   - Authorize your SSH key:
     ```bash
     ssh-copy-id root@<tablet-ip>
     ```
   - Verify connection:
     ```bash
     ssh root@<tablet-ip> "echo ok"
     ```

4. Register the tablet in reMarkable AI:
   ```bash
   rm-ai add-device
   ```
   Follow the prompts to enter a device alias (e.g. `rm2`) and the tablet IP address.

## Command & Shorthand Handling

Whenever the user inputs any of the following triggers (with or without a leading slash `/`), execute the corresponding action via bash without waiting:

- **`rm-list`** or **`/rm-list`**:
  Run `rm-ai list` and format the output into a clean Markdown table with notebook names and UUIDs.

- **`rm-read [name] [--page N]`** or **`/rm-read`**:
  Fetch and render the page:
  ```bash
  rm-ai read "<name>" --page <N> --save temp_page.png
  ```
  Inspect the image, transcribe the handwriting/diagrams, and clean up `temp_page.png`.

- **`rm-tasks [name]`** or **`/rm-tasks`**:
  Fetch the note and extract all handwritten tasks into a `- [ ]` checklist.

- **`rm-devices`** or **`/rm-devices`**:
  Run `rm-ai devices` to view configured tablets and active device status.

- **`rm-export [name] [--page N]`** or **`/rm-export`**:
  Export the note to a Markdown file in `notes/` with vector drawings in `notes/assets/`, AI transcript, and action items:
  ```bash
  rm-ai export "<name>"
  ```

- **`rm-push <file> [--folder name]`** or **`/rm-push`**:
  Upload a PDF or Markdown document wirelessly:
  ```bash
  rm-ai push "<file>" --folder "<folder>"
  ```

- **`rm-setup`** or **`/rm-setup`**:
  Refresh dependencies and agent commands:
  ```bash
  python install.py
  ```

## Architecture & File Layout

- `rm_ai.py`: Core CLI application, tablet SSH bridge, Lines v5/v6 parser, and vector renderer.
- `install.py`: Automated cross-platform installer for CLI and AI agent configurations.
- `pyproject.toml`: Package build specification and dependency definitions.
- `.claude/commands/`: Slash command definitions for Claude Code (`/rm-list`, `/rm-read`, `/rm-tasks`, etc.).
- `.agents/skills/remarkable-ai/`: Agent skill definition for Antigravity and Gemini.
- `notes/`: Target folder for exported notes and transcribed summaries.
- `~/.config/remarkable-ai/config.json`: Tablet profile storage (active device, IP addresses, aliases).
