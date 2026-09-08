# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-module Python CLI (`rm_ai.py`, ~1700 lines) that drives a reMarkable tablet over local
Wi-Fi SSH: reads handwritten pages, renders them to PNG, pushes documents, injects live stylus
strokes, and paints an e-ink dashboard onto the sleep screen. There is no package directory —
`pyproject.toml` declares `py-modules = ["rm_ai"]` with entry points `rm-ai` / `rm_ai` → `rm_ai:main`
(`setup.py` is a two-line shim for the same thing).

## Commands

```bash
./install.sh                  # create .venv, then run install.py inside it (install.cmd on Windows)
python3 install.py            # install package + agent configs + run connection wizard
pip install -e .              # dev install only
rm-ai setup --skills-only     # re-copy slash commands/skills after editing them (no tablet needed)
./rm-ai <cmd>                 # run from the repo without installing (execs rm_ai.py directly)
```

`install.py` also writes `~/.local/bin/rm-ai` / `rm_ai` launchers that exec `python -m rm_ai` with
whichever interpreter ran the installer, so running it inside `.venv` pins the launchers to that
venv. `rm-ai.cmd` is a stale Windows launcher pointing at a different checkout; ignore it.

No linter config, no build step. `python3 test_rm_ai.py` runs the only offline checks (stylus
pacing, 7-segment geometry, the usage and weather reducers). Everything else is verified manually
against a live tablet: `rm-ai devices` (connectivity), `rm-ai list` (read path), `rm-ai clock --once`
(write path). `.venv/` (gitignored) is the project's own environment; the user's crontab runs
`.venv/bin/python rm_ai.py dashboard --mode standby` every 15 minutes from it.

Implemented subcommands — **this is the authoritative list**: `devices`/`device`, `add-device`,
`setup`/`setup-agent`/`install-skills`, `list`, `read`, `push`, `clock`, `draw`, `dashboard`/`dash`.
A global `--device/-d <id>` before the subcommand targets one tablet for a single invocation.

## Architecture

**Everything is SSH.** There is no daemon or agent on the tablet. Every operation is a
`subprocess` call to `ssh`/`scp` funnelled through `run_ssh()` and `get_ssh_base_opts()`, which
pins `UserKnownHostsFile` to `~/.config/remarkable-ai/known_hosts` so the tool never touches or
trips over the user's own `~/.ssh/known_hosts`. Host keys are `accept-new`, so a tablet whose key
changed (re-flash, IP reused) fails until its line is deleted from that file. Paramiko is used in
exactly one place — `install_ssh_key()` during first-time setup, with an OpenSSH fallback — and is
otherwise unused.

**Device resolution** (`get_active_host`): `--device` override → `active_device` in
`~/.config/remarkable-ai/config.json` → first configured device → launches `setup_wizard()`
interactively. Any new command must pass `getattr(args, "device", None)` down to `run_ssh`/`scp`,
or it silently talks to the wrong tablet. `cmd_read` already gets this wrong: its `.content` fetch
calls `run_ssh` without `host`, so under `--device` the page list comes from the active tablet.

**Tablet data model** (`REMOTE_PATH = /home/root/.local/share/remarkable/xochitl`) — a flat
directory keyed by UUID:
- `<uuid>.metadata` — `visibleName`, `parent`, `type`, `deleted`, `lastModified`, `lastOpenedPage`
- `<uuid>.content` — page ordering under `cPages.pages[].id` (older format: `pages[]`), `pageCount`
- `<uuid>/<page-uuid>.rm` — the vector strokes; `<uuid>.pdf` plus an empty `<uuid>.pagedata` for
  pushed documents

Folders are documents of `type: CollectionType`; `list_notebooks()` reads every `.metadata` in one
SSH round-trip and rebuilds the tree by walking `parent` chains. **Any write to xochitl requires
`systemctl restart xochitl`** for the library to notice it — `cmd_push` does it at the end, and
`ensure_remote_folder` relies on that same restart.

**Lookup semantics an agent depends on.** `read <name>` matches exact full-path or title first,
then substring, over notebooks sorted newest-modified first; no name → newest notebook; no
`--page` → the tablet's `lastOpenedPage`, out of range → page 1. `push` upserts: a document with
the same title in the same folder is updated in place under its existing UUID unless `--force-new`.
The Markdown→PDF path (reportlab) understands only `#`/`##`/`###` headings and blank lines; fence
markers are dropped and everything else, code included, becomes a paragraph.

**Rendering** (`render_rm_to_png`): `.rm` → svgwrite SVG → cairosvg PNG on a fixed 1404×1872
canvas. Two disjoint parsers behind a header sniff: v5 is a hand-rolled `struct` walk of the binary
layout; v6 delegates to `rmscene.read_blocks` and filters `SceneLineItemBlock`. Both discard brush
type, colour, width and pressure — every stroke is a 2.5px black polyline, so any fidelity change
must be made in both parsers. v6 coordinates are centered on the origin, so negative x triggers a
`+702` half-width shift — that offset is the whole coordinate correction and is the first thing to
check if pages render clipped or off-center. The v6 path prints `Debug:` lines to stdout.

**AI analysis is optional and secondary.** `analyze_with_ai()` calls google-genai
(`gemini-2.5-flash`) only when `GEMINI_API_KEY` is set; without it, it prints the rendered image
path and returns. That no-key path is the *normal* one for agent use: the agent runs
`rm-ai read ... --save temp_page.png` and looks at the image with its own vision, rather than
paying for a second model. The `google.genai` import sits above the key check, so `read` still
needs the package installed.

**Virtual Stylus** (`VirtualStylus`) writes raw 16-byte Linux `input_event` structs (`<IIHHi`)
into `dd of=/dev/input/event1` over one persistent SSH pipe — real hardware digitizer injection,
so strokes appear with no page reload. The digitizer axes are rotated relative to the display:
`ABS_Y` is horizontal (0..15725), `ABS_X` is vertical and inverted (0..20966). Those constants in
`display_to_digitizer()` are the calibration knob for any drawing that lands in the wrong place.
xochitl smooths and predicts pen motion over *time* and debounces tool changes, so `stroke()`
resamples the path to `STEP_PX` and sends one evdev frame per `FRAME_DT`, with a short hover before
touch-down and a `TOOL_SETTLE` pause on every pen↔eraser switch. Blasting a stroke in one write
stretches it ~15% and makes eraser strokes come out as pen lines — that was the original clock bug.
Speed is part of the look: the pencil lays down less ink the faster it moves, and xochitl's motion
prediction overruns turns and stroke ends more at speed (~4 px at 4 px/3 ms, ~11/20 px at 12 px/3 ms),
so `STEP_PX` stays at 4 and is not a free knob.
Every eraser stroke is followed by `ERASE_SETTLE` (0.15 s): erasing thick ink keeps xochitl busy and
a stroke sent meanwhile is dropped whole (measured: with no pause every second erase was lost).
Pens differ wildly from their nominal width — the pencil at pressure 4000 covers ~70px, halo
included — so `--ink-width` sizes the erase sweep separately from the `--pen-width` the bars are
built from; `--thickness` stacks passes on top of that. The only reliable way to know what landed
on the page is to read the `.rm` back after xochitl's autosave (irregular, 10–60 s).

`DigitalClock` sits on top: each digit is 7 segments, and every tick only toggles segments whose
state changed — pen to draw, eraser to remove — which is what keeps e-ink churn near zero.
`SevenSegmentDigit` builds bars of `--thickness` px from overlapping passes of the pen in use and
derives its corner gap and erase sweep from that ink width against the fixed ~17px medium eraser,
so erasing one segment never nicks a neighbour (`test_rm_ai.py` asserts this); bars too thick for
the digit size raise `ValueError`. The width of one pen line at the chosen `--pressure` is
measured by `detect_pen_width()` from the newest page's saved strokes (every stroke records pen,
pressure and drawn width), with `--pen-width` as the manual override. `--save-defaults` stores the clock flags under
`clock_defaults` in the config file; `cmd_clock` resolves flag > saved default > built-in. The
tick loop sleeps to the next wall-clock second, so drawing time cannot drift or skip seconds.

**Dashboard** (`render_dashboard_image` → `cmd_dashboard`): PIL renders a 1404×1872 grayscale
image locally. `--mode standby` scp's it to `/usr/share/remarkable/suspended.png`, backing the factory
image up to `suspended.png.original` once (`--restore` reads that); the tablet reads it only when it
falls asleep, so a cron job keeps it current. `--mode doc` routes through the same PDF upload path as
`push` (each push restarts xochitl). `--mode doc --live` pushes the template once (`margins=0`, so the
page maps 1:1 to the screen), waits for the document to be opened (`wait_for_open` watches
`lastOpened`; xochitl's restart restores the document *without* touching it, so the wait is capped),
then `run_live_dashboard()` draws on the page with the pen: every minute it sweeps the clock zone
(`sweep_path`, one eraser serpentine), hovers `START_SETTLE`, and writes HH:MM via `text_strokes()`
(the page's font filled with horizontal pen runs); usage rows are redone the same way when their
value or reset time changes; at midnight it re-pushes the template. Inputs: `fetch_weather()`
(Open-Meteo, city geocoded once into the config), `fetch_claude_subscription_usage()` (the `limits`
list of the usage endpoint behind Claude Code's `/usage`, read with the token in
`~/.claude/.credentials.json`), `fetch_claude_usage()` (Admin API, needs `ANTHROPIC_ADMIN_KEY`); the
pure `summarize_*` reducers are the tested parts. `--mode live` hands off to `DigitalClock`.

## Agent configuration lives in three places and is copied outward

- `.claude/commands/*.md` — Claude Code slash commands
- `.agents/skills/remarkable-ai/SKILL.md` — Antigravity/Gemini skill (**preferred source**)
- `skills/remarkable-ai/SKILL.md` — fallback, only used if `.agents/` is missing

`configure_ai_agents()` in `rm_ai.py` and `install.py` carry the same copy logic verbatim — change
both. They copy to `~/.claude/commands/` and `~/.gemini/config/skills/remarkable-ai/`, and under
WSL also into every `/mnt/c/Users/*/.claude` and `.gemini` they find. Edit the repo copy, then run
`rm-ai setup --skills-only`; editing the installed copy is lost on the next setup. The two
`SKILL.md` files have drifted — `.agents/` is the current one; `skills/` is stale. `AGENTS.md` and
`GEMINI.md` are near-identical duplicates of the same trigger table.

## Known drift between docs and code

- **`rm-ai export` does not exist.** `.claude/commands/rm-export.md`, README, and `AGENTS.md` all
  advertise it. `/rm-export` is really: `rm-ai read "<name>" --page N --save
  notes/assets/<name>.png`, then the agent writes the markdown to `notes/` itself
  (see `notes/D-Wave_page_5.md` for the expected shape: YAML frontmatter, embedded asset, Mermaid
  diagram, transcript, action items).
- **`COMMANDS.md` is an aspirational spec**, not a feature list — `rm-info`, `rm-search`, `rm-ask`,
  `rm-diagram`, `rm-daily`, `rm-lockscreen`, `rm-obsidian`, `rm-watch`, `rm-share` are unimplemented.
  `PLAN.md` is likewise a roadmap. Don't treat either as documentation of behavior.
- **`rm-ai list` does not return page counts** despite several docs claiming it does; it prints
  folder, title, and UUID.
- `dashboard --tasks-from` reads `visibleName` and `pages` keys that `list_notebooks()` never
  returns; the resulting KeyError is caught by a broad `except`, which prints a note and falls
  through to the placeholder tasks.

## Conventions

- Heavy deps (PIL, svgwrite, cairosvg, rmscene, reportlab, pypdf, google-genai, paramiko) are
  imported **inside** functions, not at module top, so the CLI stays fast to start and a missing
  optional dep only breaks the one command that needs it. Keep new imports lazy.
- User-facing output is emoji-prefixed status lines printed straight to stdout; commands return
  `None` and print errors rather than raising.
- Pages are 1-indexed on the CLI (`--page N`) and 0-indexed internally.

## Slash-command triggers

When the user types any of these (with or without a leading `/`), run it immediately without asking:
`rm-list`, `rm-read [name] [--page N]`, `rm-tasks [name]`, `rm-devices`, `rm-export [name]`,
`rm-push <file> [--folder name]`, `rm-dashboard [--mode standby|doc] [--suspend]`,
`rm-clock [--pos pos] [--duration N]`, `rm-draw <shape>`, `rm-setup`. For anything that renders a
page, save to `temp_page.png`, inspect the image, transcribe, then delete the temp file.
