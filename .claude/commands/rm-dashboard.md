---
description: Put a dashboard on the tablet: sleep screen (default), a notebook page, or a live page kept current by the pen
---

Run the dashboard:
```bash
rm-ai dashboard $ARGUMENTS
```
Renders the date, month calendar with today marked, weather (Open-Meteo, `--city` once), Claude usage
(session / week / week Fable with reset times, from the Claude Code login), battery, and priorities (`--task`).
Modes:
- `--mode standby` (default): sets the sleep screen (`/usr/share/remarkable/suspended.png`); no reload, zero battery.
- `--mode doc`: pushes the page as a document you can write on (restarts the tablet's app).
- `--mode doc --live`: pushes the page once, then keeps drawing HH:MM every minute and the usage rows on it with the pen; `--no-push` reuses the open page.
- `--restore`: factory sleep screen back.
