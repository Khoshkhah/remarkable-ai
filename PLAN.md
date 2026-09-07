# reMarkable 2 Wireless Hub & AI Integration Plan

Transforming the reMarkable 2 into a completely wireless productivity workstation: live presentation mirror for webinars/Zoom, an AI-powered note assistant, bidirectional document/dashboard delivery, and a secondary e-ink monitor.

---

## System Architecture Overview

```
                        +-----------------------------------------+
                        |           reMarkable 2 (Linux)          |
                        |      wlan0: 192.168.18.18 (SSH :22)     |
                        |  /home/root/.local/share/.../xochitl/   |
                        |     Frame buffer: /dev/fb0              |
                        +--------------------+--------------------+
                                             |
                                 (Wireless LAN / SSH)
                                             |
            +--------------------------------+--------------------------------+
            |                                                                 |
            v                                                                 v
+-------------------------------+                         +-----------------------------------+
|       Windows Host PC         |                         |      WSL2 Environment / Linux     |
| - Passwordless SSH Key        |                         | - Python AI Agent Pipeline        |
| - Live Presentation (rmview)  |                         | - Note Parser (rmscene / rmcl)    |
| - Zoom / Webinar Screen Share |                         | - Vision LLM Integration (Gemini) |
| - Second Monitor Display Host |                         | - Automated Sync & Push Jobs      |
+-------------------------------+                         +-----------------------------------+
```

---

## Phase 1: Wireless Connection Foundation (Prerequisite)
Goal: Ensure zero-friction, passwordless communication between your Windows PC, WSL, and the reMarkable tablet over Wi-Fi.

### Tasks:
1. **Windows SSH Key Setup**:
   - Generate an SSH keypair on Windows (if not already present).
   - Copy the public key to `/home/root/.ssh/authorized_keys` on the reMarkable.
   - Configure `~/.ssh/config` alias (`Host rm2`).
2. **WSL SSH Key Setup (Second PC / Local WSL)**:
   - Repeat key copy from WSL to reMarkable for automated headless scripts.
3. **Reboot Persistence Verification**:
   - Verify that `/home/root/.ssh/authorized_keys` and the Wi-Fi marker `/home/root/.config/remarkable/rm_enable_ssh_wifi_marker` survive tablet reboots.

---

## Phase 2: Live Screen Sharing for Zoom & Webinars
Goal: Real-time, low-latency wireless mirroring of your pen strokes to your PC desktop, suitable for sharing in Zoom, Teams, Google Meet, or recording in OBS.

### Tasks:
1. **Tool Selection & Setup**:
   - Install **rmview** (clean desktop window, orientation detection, smooth vector rendering).
   - Alternatively configure **reStream** (direct framebuffer pipeline to VLC/OBS).
2. **Webinar / Zoom Integration**:
   - Test screen mirroring over Wi-Fi.
   - Verify window capture in Zoom / Meet without screen tearing or latency.
   - Configure hotkeys / landscape vs. portrait orientation switching.

---

## Phase 3: AI Agent Note Reader (Tablet -> AI -> Action)
Goal: An AI agent running on your PC/WSL that wirelessly inspects handwritten notes, decodes pen strokes/diagrams, and performs automated actions (summarization, task extraction, answering questions).

### Tasks:
1. **Note Sync & Parsing Engine**:
   - Build a Python watcher script using `rmscene` / `paramiko` to monitor `/home/root/.local/share/remarkable/xochitl/`.
   - Map document UUIDs to readable notebook titles via `.metadata`.
   - Convert `.rm` vector stroke files to high-resolution PNG / SVG images.
2. **Multimodal AI Integration**:
   - Send rendered handwritten note images to a Vision LLM (e.g., Gemini 1.5 / 2.0 Flash/Pro).
   - Prompts for:
     - OCR & clean text transcription.
     - Extraction of action items (`[ ] Task`).
     - Mind map / diagram comprehension.
     - Summarization and auto-tagging.
3. **Automated Triggers**:
   - CLI or daemon: "Check for newly modified notes in notebook *Ideas* and output a digest".

---

## Phase 4: AI Agent Page Generator (AI -> Tablet)
Goal: The AI generates content (daily agenda, AI research briefs, flashcards, weather/news dashboard) and pushes it wirelessly directly onto the reMarkable.

### Tasks:
1. **PDF Generation & Ingestion**:
   - Python script generates formatted PDFs (sized for reMarkable 2: 1872 x 1404).
   - Upload PDF into the `xochitl` directory with proper `.content`, `.metadata`, and UUID structure.
   - Signal `xochitl` to refresh its document library without rebooting the tablet.
2. **Ambient E-Ink Screen (Sleep Screen / Lock Screen)**:
   - Create daily dynamic lockscreens (`suspended.png`) containing daily calendar events, weather, or custom AI daily briefing.
   - Wirelessly push and update the sleep screen.

---

## Phase 5: Wireless Second Monitor
Goal: Turn the reMarkable 2 into an ambient E-ink second monitor for reading documentation, monitoring logs, or writing code without eye strain.

### Tasks:
1. **Architecture Options**:
   - **Option A (VNSee)**: Run a lightweight VNC client natively on the reMarkable 2; stream a virtual desktop from the PC.
   - **Option B (Virtual Framebuffer / rmk)**: Stream designated terminal windows or markdown docs to the e-ink display.
2. **PC Virtual Display Configuration**:
   - Create a virtual display output on Windows/Linux matching reMarkable resolution (1872x1404).
   - Route code documentation, dashboards, or Slack to the tablet.

---

## Phase 6: Obsidian Integration (Two-Way Sync & Note Ingestion)
Goal: Seamlessly connect reMarkable handwritten notes, sketches, and annotations directly into your Obsidian Markdown vault.

### Tasks:
1. **Handwritten Notes to Obsidian Markdown**:
   - Direct local sync pipeline pulling updated notebooks over SSH into your Obsidian vault (`/reMarkable/` folder).
   - Render vector pages to crisp SVG / PNG images embedded directly in Obsidian notes.
   - Run AI OCR to generate searchable Markdown text, headers, and tag metadata alongside the drawings.
2. **Obsidian Community Plugins & Local Bridge**:
   - Explore community plugins (like *Obsidian reMarkable Sync* or our custom automated local script) that operate completely offline/without cloud.
3. **Obsidian to reMarkable (Push Reading Material)**:
   - One-click script to push any Obsidian Markdown note or canvas as a formatted PDF onto the reMarkable for distraction-free reading and markup.

---

## Verification Plan

### Phase 1 Verification:
- Running `ssh root@192.168.18.18` from Windows and WSL connects without prompting for a password.
- Rebooting the tablet preserves passwordless access.

### Phase 2 Verification:
- `rmview` opens on Windows, connects to `192.168.18.18` over Wi-Fi, and displays pen strokes in real time.
- Window is successfully captured and broadcast in Zoom/Teams.

### Phase 3 Verification:
- Run a test Python script that pulls the latest modified notebook, converts page 1 to an image, and prints a structured summary generated by an AI model.

### Phase 4 Verification:
- Agent generates a sample "AI Morning Briefing" PDF, transfers it over Wi-Fi, and it opens natively in the reMarkable notebook list.

### Phase 5 Verification:
- Display pipeline streams a live desktop window or documentation page to the reMarkable screen.
