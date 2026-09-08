# Antigravity Agent Rules for reMarkable AI

## Command & Shorthand Handling

Whenever the user inputs any of the following triggers (with or without a leading slash `/`), immediately execute the corresponding action via `run_command` without waiting or asking for clarification:

- **`rm-list`** or **`/rm-list`**: Run `rm-ai list` and display the active tablet's notebooks in a clean Markdown table with names and UUIDs.
- **`rm-read [name] [--page N]`** or **`/rm-read`**: Fetch and render the page using `rm-ai read "<name>" --page <N> --save temp_page.png`, inspect the rendered image using `view_file`, provide transcription and diagram analysis, and clean up `temp_page.png`.
- **`rm-tasks [name]`** or **`/rm-tasks`**: Fetch the page, inspect the handwriting, and extract all to-do items into a `- [ ]` checklist.
- **`rm-devices`** / **`rm-device`** or **`/rm-devices`**: Run `rm-ai devices` to display configured tablets and active device status.
- **`rm-export [name] [--page N]`** or **`/rm-export`**: Export the note to a Markdown file in `notes/` with embedded vector drawings in `notes/assets/`, full AI transcript, Mermaid diagram, and action items.
- **`rm-push <file> [--folder name]`** or **`/rm-push`**: Upload a PDF or Markdown document wirelessly to the tablet, automatically creating any missing remote folders.
- **`rm-clock [--pos pos] [--thickness N] [--frame] [--interval N]`** or **`/rm-clock`**: Launch the live digital clock drawn on the open page with the tablet's pen (runs until stopped; `--once` for a single stamp).
- **`rm-dashboard [--city name] [--mode standby|doc] [--live] [--suspend]`** or **`/rm-dashboard`**: Put the dashboard on the tablet: sleep screen by default (date, calendar, weather, Claude usage, battery), a notebook page with `--mode doc`, or a live page kept current by the pen with `--mode doc --live`.
- **`rm-draw <shape> [args]`** or **`/rm-draw`**: Inject live vector strokes directly into the tablet screen in real time.
- **`rm-setup`** or **`/rm-setup`**: Run `rm-ai setup` to refresh agent skills and commands.


