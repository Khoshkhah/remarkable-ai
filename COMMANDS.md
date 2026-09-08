# reMarkable AI Command & Slash-Command Specification

This document defines the complete catalog of commands available across:
- **Claude Code**: Native slash commands (e.g. `/rm-read`)
- **Gemini / Antigravity**: Agent Skill commands
- **Terminal CLI**: Direct command-line utility (`rm-ai <command>`)

---

## Category 0: Setup & Device Management

| Command | Usage | Description |
| :--- | :--- | :--- |
| **`python install.py`** | `python install.py` | One-step automated installer: sets up package, AI skills, and verifies wireless connectivity. |
| **`rm-ai setup`** | `rm-ai setup` | Automatically configures AI agent skills for Antigravity, Gemini, and Claude Code without manual copying. |
| **`rm-ai devices`** | `rm-ai devices [switch]` | Lists configured tablets and allows interactive or direct switching of the active tablet. |
| **`rm-ai add-device`** | `rm-ai add-device` | Registers a new tablet by ID, display name, and Wi-Fi IP address. |

---

## Category 1: Discovery & Navigation

| Command | Usage | Description |
| :--- | :--- | :--- |
| **`/rm-list`** | `/rm-list [folder]` | Lists all notebooks on your reMarkable, sorted by recently modified, showing page count and folder location. |
| **`/rm-info`** | `/rm-info [name]` | Shows detailed metadata for a notebook (total pages, created date, last opened page, format version). |
| **`/rm-search`** | `/rm-search <keyword>` | Searches across notebook titles and previously transcribed pages for matching terms. |

---

## Category 2: Note Reading & AI Analysis (Tablet -> AI)

| Command | Usage | Description |
| :--- | :--- | :--- |
| **`/rm-read`** | `/rm-read [name] [--page N]` | Pulls the notebook page wirelessly, renders the pen strokes, and provides a full transcript and executive summary. |
| **`/rm-tasks`** | `/rm-tasks [name]` | Scans handwritten notes and extracts all action items, assignments, or to-dos into an interactive Markdown checklist (`- [ ]`). |
| **`/rm-ask`** | `/rm-ask <question>` | Ask the AI any specific question about your latest sketch, diagram, or handwritten math/notes (e.g., *"/rm-ask what is the main bottleneck shown in the diagram?"*). |
| **`/rm-diagram`** | `/rm-diagram [name]` | Specifically analyzes hand-drawn architecture, mind maps, flowcharts, or tables and converts them into structured Mermaid diagrams or text tables. |

---

## Category 3: Bidirectional Content Delivery (AI -> Tablet)

| Command | Usage | Description |
| :--- | :--- | :--- |
| **`/rm-push`** | `/rm-push <file.pdf or text>` | Pushes a PDF document, markdown note, or article directly onto the reMarkable wirelessly and reloads the library. |
| **`/rm-daily`** | `/rm-daily` | Generates an AI morning briefing (calendar events, weather, daily priorities, news summary) and sends it as a new daily page on the tablet. |
| **`/rm-lockscreen`** | `/rm-lockscreen [image or prompt]` | Generates or sets a custom dynamic sleep screen / lockscreen (`suspended.png`) on your reMarkable. |

---

## Category 4: Obsidian Integration & Sync

| Command | Usage | Description |
| :--- | :--- | :--- |
| **`/rm-obsidian`** | `/rm-obsidian [name]` | Syncs handwritten notes directly into your Obsidian Vault: creates a Markdown note with AI transcript, embeds rendered PNG/SVG drawings, and links to your daily note. |
| **`/rm-export`** | `/rm-export [name] [--format pdf/png/svg]` | Exports a handwritten notebook into a clean vector PDF or high-resolution PNG image gallery on your PC. |

---

## Category 5: Live Utilities & Automation

| Command | Usage | Description |
| :--- | :--- | :--- |
| **`/rm-share`** | `/rm-share` | Initiates or checks live wireless screen sharing for webinars / Zoom. |
| **`/rm-watch`** | `/rm-watch [notebook]` | Background daemon mode: monitors your tablet over Wi-Fi. As soon as you write a new page, it automatically transcribes it and logs it into Obsidian. |

---

## Category 6: Virtual Stylus & Real-Time Screen Drawing

| Command | Usage | Description |
| :--- | :--- | :--- |
| **`rm-ai clock`** / **`/rm-clock`** | `rm-ai clock [--pos center] [--size large] [--once] [--duration N] [--clear]` | Live 7-segment digital clock via Virtual Stylus (/dev/input/event1). Features minimal-delta state machine, bold multi-pass segments, configurable sizes (small, medium, large, xlarge), and single-shot time stamp mode (--once). |
| **`rm-ai draw`** / **`/rm-draw`** | `rm-ai draw line --from X1,Y1 --to X2,Y2` | Injects live vector strokes directly into the active notebook screen in real time without page reload or tablet restart. |

---

## Category 7: Dedicated Fullscreen Clock & Productivity Dashboard

| Command | Usage | Description |
| :--- | :--- | :--- |
| **`rm-ai dashboard`** / **`/rm-dashboard`** | `rm-ai dashboard [--mode standby\|doc\|live] [--suspend] [--restore] [--quote "..."] [--task "..."]` | Turns the tablet into a dedicated executive desk clock and productivity dashboard. Displays giant digital clock, date, monthly calendar with today highlighted, live battery telemetry, daily habits, action items, and ruled handwriting notes. |
| **`rm-ai dashboard --mode standby`** | `rm-ai dashboard --mode standby [--suspend]` | Sets `/usr/share/remarkable/suspended.png` so the dashboard displays whenever the tablet sleeps with zero power consumption. Use `--suspend` to display immediately. |
| **`rm-ai dashboard --mode doc`** | `rm-ai dashboard --mode doc [--title "..."] [--folder "..."]` | Generates a 226 DPI vector notebook and uploads it wirelessly into tablet documents for live handwriting with the Marker stylus. |
| **`rm-ai dashboard --restore`** | `rm-ai dashboard --restore` | Restores the original factory reMarkable sleep screen from automatic backup. |

---


## Example Workflow in Claude or Gemini:

```text
User: /rm-list
AI: Found 96 notebooks. Most recent:
    1. D-Wave (16 pages, modified today)
    2. Naegele (1 page)
    3. Choices model (3 pages)

User: /rm-read D-Wave --page 5
AI: [Renders page 5 wirelessly]
    Transcription:
    - Persian: "معرفی -> دپارچ توپوگرافی کد برنامه"
    - Architecture Diagram: Optimization -> Theory -> Thesis
    - Bottom: "Optimization Annealing"
    Summary: High-level architectural breakdown of a simulated annealing optimization algorithm.

User: /rm-tasks D-Wave
AI: Extracted 2 action items:
    - [ ] Complete topography departure code implementation
    - [ ] Finalize theory section for thesis optimization comparison

User: /rm-obsidian D-Wave
AI: Synced D-Wave (16 pages) into your Obsidian vault at:
    Vault/reMarkable/D-Wave.md (with embedded vector images and transcripts).
```
