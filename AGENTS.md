# Antigravity Agent Rules for reMarkable AI

## Slash Command & Shorthand Handling

Whenever the user inputs any of the following slash commands or shorthand triggers, immediately execute the corresponding action via `run_command` without waiting or asking for clarification:

- **`/rm-list`**: Run `rm-ai list` and display the active tablet's notebooks in a clean Markdown table with names and UUIDs.
- **`/rm-read [name] [--page N]`**: Fetch and render the page using `rm-ai read "<name>" --page <N> --save temp_page.png`, inspect the rendered image using `view_file`, provide transcription and diagram analysis, and clean up `temp_page.png`.
- **`/rm-tasks [name]`**: Fetch the page, inspect the handwriting, and extract all to-do items into a `- [ ]` checklist.
- **`/rm-devices` / `/rm-device`**: Run `rm-ai devices` to display configured tablets and active device status.
- **`/rm-setup`**: Run `rm-ai setup` to refresh agent skills and slash commands.
