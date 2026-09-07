---
name: remarkable-ai
description: Wirelessly inspects, reads, and analyzes handwritten notes, sketches, and diagrams from reMarkable 2 tablets over Wi-Fi, and pushes generated documents or e-ink dashboards onto the tablet.
---

# reMarkable AI Assistant Skill

This skill enables agents to communicate wirelessly with a reMarkable 2 tablet over local Wi-Fi.

## Capabilities:
1. **List Notebooks**: Discover all notebooks, folders, page counts, and last modified timestamps.
2. **Read & Transcribe Handwriting**: Pull `.rm` vector stroke files, render them into crisp PNG images, and use multimodal vision to read cursive, English, Persian/Farsi, and mathematical formulas.
3. **Extract Action Items**: Automatically scan notes for to-do items, tasks, and assignments.
4. **Push Documents**: Generate formatted PDFs and upload them wirelessly directly into the tablet library.

## Commands:
- `rm-ai list`: List all notebooks.
- `rm-ai read [NOTEBOOK_NAME] [--page N] [--action summarize|transcribe|tasks]`: Render page and analyze.
- `rm-ai export [NOTEBOOK_NAME]`: Export pages as PNG images.

## Architecture:
- Connection: SSH over Wi-Fi (`ssh rm2` or `ssh root@192.168.18.18`)
- Storage root: `/home/root/.local/share/remarkable/xochitl/`
- Vector parser: `rmscene` v6 lines parser with SVG/Cairo rendering.
