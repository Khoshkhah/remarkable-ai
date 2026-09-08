---
description: Turn tablet into a dedicated fullscreen desk clock and productivity dashboard
---

Generate and push an executive E-ink dashboard to the tablet:
```bash
rm-ai dashboard $ARGUMENTS
```
Renders minimalist high-contrast digital clock, monthly calendar with today highlighted, live battery telemetry, daily habits, action items, and ruled notebook lines.
Options:
- `--mode standby` (default): Sets sleep screen (`/usr/share/remarkable/suspended.png`) for zero-battery desk clock display.
- `--mode doc`: Generates vector notebook and pushes to tablet documents for live note-taking.
- `--suspend`: Puts tablet to sleep immediately so the dashboard appears right away.
- `--restore`: Restores original reMarkable sleep screen.
