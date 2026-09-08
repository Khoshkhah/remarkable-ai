# Antigravity Agent Rules for reMarkable AI

## Command & Shorthand Handling

Whenever the user inputs any of the following triggers (with or without a leading slash `/`), immediately execute the corresponding action via `run_command` without waiting or asking for clarification:

- **`rm-list`** or **`/rm-list`**: Run `rm-ai list` and display the active tablet's notebooks in a clean Markdown table with names and UUIDs.
- **`rm-read [name] [--page N]`** or **`/rm-read`**: Fetch and render the page using `rm-ai read "<name>" --page <N> --save temp_page.png`, inspect the rendered image using `view_file`, provide transcription and diagram analysis, and clean up `temp_page.png`.
- **`rm-tasks [name]`** or **`/rm-tasks`**: Fetch the page, inspect the handwriting, and extract all to-do items into a `- [ ]` checklist.
- **`rm-devices`** / **`rm-device`** or **`/rm-devices`**: Run `rm-ai devices` to display configured tablets and active device status.
- **`rm-setup`** or **`/rm-setup`**: Run `rm-ai setup` to refresh agent skills and commands.
