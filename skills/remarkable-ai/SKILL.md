---
name: remarkable-ai
description: Wirelessly inspects, reads, and analyzes handwritten notes, sketches, and diagrams from reMarkable 2 tablets over Wi-Fi, and pushes generated documents or e-ink dashboards onto the tablet.
---

# reMarkable AI Assistant Skill

This skill enables agents to communicate wirelessly with a reMarkable 2 tablet over local Wi-Fi.

## How to Fulfill User Requests:

### 1. Listing Notebooks:
When the user asks to list, find, or search notebooks:
- Run: `rm-ai list`
- Present the notebook titles, UUIDs, and page counts in a clean table.

### 2. Reading and Analyzing Notes / Handwriting / Diagrams:
When the user asks to read, transcribe, summarize, or analyze a page from any notebook:
1. Identify the notebook name and page number (defaults to latest opened page if omitted).
2. Fetch and render the page to a temporary image:
   `rm-ai read "<notebook_name>" --page <page_num> --save temp_page.png`
3. Inspect the rendered image using `view_file` on `temp_page.png`.
4. Use your multimodal vision to:
   - Accurately transcribe all handwritten English, Persian/Farsi, or other multilingual text.
   - Explain drawings, architectures, graphs, mind maps, and mathematical formulas.
   - Extract actionable to-dos and next steps.
5. Delete `temp_page.png` after inspection.

### 3. Tablet & Device Management:
- To switch active tablets: `rm-ai devices [number_or_name]`
- To register a new tablet: `rm-ai add-device`
- To target a specific tablet for a single command: `rm-ai --device <name> list`

## Architecture:
- Connection: SSH over Wi-Fi (`ssh rm2` or `ssh root@<IP>`)
- Storage root: `/home/root/.local/share/remarkable/xochitl/`
- Vector parser: `rmscene` v6 lines parser with SVG/Cairo rendering.
