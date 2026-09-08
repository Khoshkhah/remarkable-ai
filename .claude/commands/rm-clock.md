---
description: Start the live digital clock drawn on the open page with the tablet's virtual pen
---

Run the virtual stylus clock (it runs until stopped; use `--duration N` or `--once` for a bounded run):
```bash
rm-ai clock $ARGUMENTS
```
Options: `--pos top-right|top-left|center|bottom-right|X,Y`, `--size small|medium|large|xlarge`, `--thickness N|max`,
`--pressure 0..4095`, `--frame`, `--interval N` (seconds between updates), `--format HH:MM:SS|HH:MM|MM:SS`,
`--clear` (erase on exit), `--save-defaults` (make the given options the defaults),
`--install` (put the clock on the tablet itself: it runs whenever the "Clock" document is open, no PC needed),
`--uninstall`.
Before starting: a notebook page must be open and the pen (not the eraser or selection tool) selected on the tablet.
