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

### 3. Dashboard:
When the user asks for a desk clock, standby dashboard, weather, Claude usage or a daily planner:
- Run: `rm-ai dashboard --city <city>` (sleep-screen dashboard: date, calendar, weather, Claude usage, battery; no reload)
- Run: `rm-ai dashboard --suspend` (put the tablet to sleep so it shows right away)
- Run: `rm-ai dashboard --mode doc --live` (a notebook page kept current by the pen: HH:MM every minute, usage rows as they change)

### 4. Real-Time Digital Clock & Live Vector Drawing:
- Clock on the open page: `rm-ai clock` (saved defaults) or e.g. `rm-ai clock --pos center --thickness 28 --frame --interval 2`
- Single time stamp: `rm-ai clock --once`
- Inject vector strokes: `rm-ai draw line --from X1,Y1 --to X2,Y2`
- A notebook page must be open with the pen selected; after "Erase all" the eraser stays selected and strokes erase.

### 5. Pushing Documents:
- Upload PDF or Markdown document: `rm-ai push file.pdf --folder "Work"`

### 6. Tablet & Device Management:
- To switch active tablets: `rm-ai devices [number_or_name]`
- To register or setup a tablet: `rm-ai setup` or `rm-ai add-device`
- To target a specific tablet for a single command: `rm-ai --device <name> list`

## Architecture:
- Connection: SSH over Wi-Fi (`root@<IP>`) with isolated known_hosts
- Storage root: `/home/root/.local/share/remarkable/xochitl/`
- Vector parser: `rmscene` v6 lines parser with SVG/Cairo rendering.

