# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-module Python CLI (`rm_ai.py`, ~3700 lines) plus three C programs in `app/` (`rmclock.c`, `rmdash.c`, `rmvocab.c`, sharing `stylus.h` for the pen and `common.h` for HTTPS, JSON and deflate) for the tablet that drives a reMarkable tablet over local
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
`setup`/`setup-agent`/`install-skills`, `list`, `read`, `push`, `clock`, `draw`, `dashboard`/`dash`,
`chat`, `vocab`.
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
measured by `measure_pen_width()` from the page's saved strokes (every stroke records pen,
pressure and drawn width), with `--pen-width` as the manual override. `--save-defaults` stores the clock flags under
`clock_defaults` in the config file; `cmd_clock` resolves flag > saved default > built-in. The
tick loop sleeps to the next wall-clock second, so drawing time cannot drift or skip seconds.

**Tablet-resident clock** (`clock --install`, `app/`): the one thing that runs *on* the tablet.
`StrokeRecorder` is a `VirtualStylus` with zero pacing that keeps the bytes instead of sending them;
the clock document lives in the tablet's `CLOCK_FOLDER` ("app"; an existing document of that title is
moved there by editing its `.metadata` parent); `bake_clock_app()` records every stroke of a `DigitalClock` into one file per stroke
(`d<slot><seg>`/`e<slot><seg>`, `colon<i>`, `frame`), and `install_clock_app()` ships those, a
`config` (document uuid, page `.rm` path, slots, strftime format, interval), the PC's `/etc/localtime`
(the tablet runs on UTC) and the static ARMv7 binary `app/rmclock` to `/home/root/.local/share/rmclock`
with a systemd unit `rmclock.service`. `app/rmclock.c` replays the blobs with the same pacing constants
(keep them in sync with `VirtualStylus` by hand), yields to the real pen by reading the digitizer
back and filtering its own echo, and draws everything again when the page's `.rm` has not been
autosaved for 3 awake minutes, shrank to a fraction (`ink_wiped`, "Erase all") or did not keep its
size after a cycle (`ink_none`: the eraser is the selected tool, pen strokes erase); a closed page
left with the eraser selected gets the pen back (`give_back_the_pen`). **Before every stroke** `page_on_screen()` in
`stylus.h` requires two things: `LastOpen` in `xochitl.conf` names the document (xochitl clears it
to `@ByteArray()` on the home screen) *and* xochitl's journal says the document's worker is running
(`worker on <uuid> now running` / `now exiting`, tailed with `journalctl -u xochitl -f`; a
"translation" line marks a restart). `LastOpen` alone is not enough: for about a minute after
`systemctl restart xochitl` it still names the document while the home screen shows, and strokes
sent then tapped a library tile and drew on the user's notes. When the page leaves mid-draw the
program stops at once (`page_lost`) and redraws everything on the next open; the check runs on every
round of the retry loop (`page_gone()`), not once per stroke - waiting out the real pen can outlast the
page, and it did: strokes meant for the dashboard landed on a vocabulary page for three minutes. Installers stop the
services before any push, and `dashboard --install --no-push` reuses the page without a restart. Rebuild with
`app/build.sh` (Docker, `alpine` arm/v7 image, musl static); the binary is committed. It takes
`rmclock <dir> [xochitl.conf] [event device]` so a dry run on the PC against a plain file works.

**Restarting xochitl** always goes through `restart_xochitl()` (`stylus.h`, and `RESTART_XOCHITL` /
`restart_xochitl()` in `rm_ai.py`), never a bare `systemctl restart xochitl`: xochitl 3.28 segfaults in its
own teardown often enough to matter - measured on an idle tablet with nothing open, after its whole clean
shutdown had run - and systemd answers that with `OnFailure=remarkable-fail.service`, which on a device
with no pending firmware update does one thing, `systemctl reboot`. xochitl's own `Restart=on-failure`
brings it back without that, so the helper masks `remarkable-fail.service` around the restart and unmasks
it from a detached shell 25 s later. The 180 s wait the restart used to sit behind never prevented this
crash; it only made the restart rare.

It also needs a moment when the reload costs nothing:
`may_restart()` in `stylus.h` is the home screen *or* the screen off, in both cases with xochitl done
winding documents down: `workers_quiet()` counts the `worker on <uuid> now running`/`now exiting` lines and
waits `WORKER_SETTLE_S` (20 s) past the last exit. That signal replaced the age of `xochitl.conf`, whose
180 s never came true on a tablet in use (measured gaps between one document closing and the next opening:
2-72 s), which left the daily page and the Vocabulary document stuck for days.
The screen is the way in: xochitl logs `Changing display state from Normal to DeepSleep` and asks the
kernel to suspend ~12 s later, and stopping xochitl cancels that suspend, so the window is as long as
needed; whatever document is open is reloaded and restored by xochitl, unseen. `rmdash` therefore calls
`swap_daily_page()` in the open branch too - its page is parked open for days.

**Tablet-resident dashboard** (`dashboard --install`, `app/rmdash.c`, shares `app/stylus.h` with the
clock). The page: `render_dash_pages()` renders JPEG templates for the next `DASH_STOCK_DAYS` days
(`render_dashboard_image(live=True, pen_weather=True, now=day)`: everything printed except the weather
values); rmdash's `compose_page()` writes a one-page PDF (the JPEG as a DCTDecode XObject plus the
weather block as Helvetica text in the sleep screen's layout, `weather_block()`) and
`swap_daily_page()` puts it over the document's PDF and restarts xochitl at a moment the restart costs
nothing (`may_restart()`), on a new day or when the printed weather is over 6 h old; the first push is the
PC's full page. The pen strokes live in the `.rm` and survive the swap; the PC's standby cron tops the
template stock up (`top_up_dash_pages`, no reload). The sleep screen: `render_sleep_backgrounds()` renders
the standby template with `stamp=True` (header text, weather values, usage rows and the footer time
left blank) for the same days as `sleep/YYYY-MM-DD.rle` (`rle_encode`, value/count triples), and
`bake_font_atlases()` ships glyph bitmaps of `SLEEP_FONTS` (`f<size><b|r>.atlas`, PIL placement: left,
top, advance per glyph); rmdash's `compose_sleep()` loads the day's background, stamps the live values in
the standby layout plus an "AS OF" clock (`stamp_text`, coverage blend), and writes
`/usr/share/remarkable/suspended.png` with its own PNG writer (a fixed-Huffman deflate with run matches:
the root filesystem has ~10 MB free) after every fetch, at most every 5 min; the standby cron then only
tops the stocks up (`dash_app.sleep`).
`TABLET_DASH_LAYOUT` is the single source of the pen-owned zones (clock and the three usage rows; the
rows' reset times are pen-drawn too, 40 px), text origins and bars, written to the tablet as `layout`;
rmdash draws only zones whose sweep file exists. Glyphs are single strokes: `STROKE_FONT` is a designed
plotter-style font (lines and arcs on a 100-unit em, cap height 72, y down; `_arc()` angles are clockwise
on screen, 90 = bottom) that `stroke_glyph()` centres in the page font's advance width, so a thick pen
(the Marker) draws a bold digit in a second; `bake_dash_app()` records them at `GLYPH_BASE` with advance
widths in `glyphs`, each with an eraser twin (`e<size>_<code>`, `ebar<i>`: `erase_path()`, one
continuous eraser stroke zigzagging along the glyph's strokes at `ERASE_OFFSETS` ±12 px, sampled every
`ERASE_STEP_PX` 4 px, run 6 px past the ends). Measured: the app removes a point of a stroke only when
an eraser *sample* falls within a few px of the stroke's centreline (less for wide strokes: 16 px steps
left pieces of the Calligraphy pen's 50 px strokes, 14 px sweep lanes left slivers of a 27 px ballpoint),
so the sweeps use `SWEEP_LANE` 6 px. Updates, zone by zone: a changed
zone is swept whole (`sweep_<zone>.bin`), then `START_SETTLE_US` (4 s) in the air, then written whole
(`draw_zone`), and only then the next zone (the user's choice: erasing along the old strokes with the
eraser twins left pieces of the Calligraphy pen's 50 px strokes, and a digit-by-digit update cannot cope
with a partly erased time); the twins now only serve the stroke bookkeeping (`dry` in stylus.h). Data: Open-Meteo directly (in both the idle and the
active branch: the printed page and the sleep screen need it), Claude usage every 5 min either from a
`token` file (a Claude Code login made for the tablet in another `CLAUDE_CONFIG_DIR`; the program renews
it with `refresh_claude_token`'s request, and a login cannot be shared: renewal invalidates the other
holder at once; the token endpoint pretty-prints, so the JSON scanner skips blanks after a key) or from
the `usage` file the PC's standby run writes (`feed_dash_usage`, `dash_app.feed`). HTTPS is the tablet's
`openssl s_client -ign_eof` with HTTP/1.0 (no chunking). `state` remembers what is drawn (and
`strokes=`, the pen strokes drawn) against the page's `.rm` mtime so reopening an unchanged page draws
nothing. Ink checks: `drawn_total` counts every pen stroke drawn or erased (`blob_strokes()` per glyph
file); after each save later than the last cycle, `live_strokes()` (stylus.h, a minimal `.rm` v6 block
walk counting SceneLineItem blocks that still carry a value, verified against rmscene) is compared with
it and fewer strokes mean `resweep`: sweep every zone, `START_SETTLE`, draw everything (covers "Erase
all", a partial erase by hand, and pen strokes that erase because the eraser is the selected tool);
`ink_lost` (no autosave for 3 awake min) does the same. `give_back_the_pen()` rewrites
`"LastActiveTool": "eraser"` to `"primary"` in the document's `.content` while it is closed.
`RM_FIXTURES=<dir>` makes it read `usage.json`/`weather.json`/`token.json` instead of the network and skip
the xochitl restart for dry runs (`rmdash <dir> [xochitl.conf] [event device]`).

**Dashboard** (`render_dashboard_image` → `cmd_dashboard`): PIL renders a 1404×1872 grayscale
image locally. `--mode standby` scp's it to `/usr/share/remarkable/suspended.png`, backing the factory
image up to `suspended.png.original` once (`--restore` reads that); the tablet paints it once when it
falls asleep (Wi-Fi is off during sleep), so the image carries the date but no clock, and a cron job
keeps the *next* sleep current. An unreachable tablet is a one-line message, not a traceback. `--mode doc` routes through the same PDF upload path as
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

**Vocabulary** (`app/rmvocab.c`, `cmd_vocab`; English learning): the third tablet-resident program, no PC
needed. The learner writes a word and a sentence on the **Words** page (app folder; `push_chat_document`
with a hint, one page) and draws a loop around the word. rmvocab watches the newest `.rm` of that document
(`read_page`: a minimal v6 walk keeping the points, 14-byte v2 / 24-byte v1 records, x + 702), finds closed
loops (`is_loop`, the port of `is_box`) with ink mostly inside (`content_of`, ray casting), and asks Gemini
with two PNGs it encodes itself (the circled strokes at 2x, the whole page at 0.5x for the sentence), the
method in `teacher.md` and the last 12 phrases, through `common.h`'s `https` (90 s; `models=` tried in turn
on 503/429; the key is the `key` file, mode 600). The answer is plain text (`PHRASE:` / `CONTEXT:` / the
lesson). `render_lesson` prints it onto 1404×1872 gray pages with the DejaVu glyph atlases
`bake_lesson_atlases` bakes (`ATLU`: Unicode keys with the Arabic form in bits 21+; the joined forms are
rendered by PIL+raqm around zero-width joiners, the lam-alef ligatures under U+FEF5..FEFC; the 60 px title
font is Latin only): the tablet joins Farsi letters itself (`jclass`, `run_glyphs`) and orders each line with
a simplified bidi (`draw_visual`: letters and digits L, Arabic R, neutrals take equal neighbours' direction
else the paragraph's; a Farsi line, `is_rtl`, is right-aligned with runs placed right-to-left; brackets are
mirrored in R runs), wraps on spaces (`paragraph`) and paginates; glyphs the atlas lacks (emoji) are skipped.
Each page is one zlib stream `lessons/NNNN-<p>.z` beside `lessons/NNNN.txt` (phrase, context, source, blank,
lesson). `build_pdf` joins every page into the Vocabulary PDF (FlateDecode gray images) and `rebuild_if_due`,
as soon as the lesson count differs from `state`'s `pushed=` and the Vocabulary document itself is closed,
swaps it in with a fresh `.content` (page count), drops the document's `.rm` files (handwriting on lesson
pages does not survive), clears the Words page when every loop on it is done *and that page is closed*
(`done`: loop signatures; a loop erased by hand is forgotten; deleting the `.rm` under an open page is
undone by the copy xochitl holds) and touches `lastModified` - **with no restart**: the user works on while
the lessons arrive underneath. Whether xochitl re-reads a document from disk when it opens it, or trusts
the page list it cached, is not documented; `reload_if_stale` settles it per swap by reading the pageCount
back out of `.content` once xochitl has rewritten it (its own rewrite is the one carrying `cPages`). Equal
to what we wrote means xochitl picked the pages up by itself and nothing is ever reloaded; fewer means it
is showing a stale document, and only then does a restart follow, at a `may_restart()` moment. A check mark (`check.bin`) is drawn beside a loop
whose lesson is made, under `page_on_screen()` for the Words document. `inbox/<name>.txt` (+ `.png`) are
lookups the PC sends: the watcher (`cmd_vocab`) still reads highlights on PDFs/EPUBs (`read_highlights`) and
highlighter/loop marks over handwriting in other notebooks (`read_marked_ink`, `PenReader`; the Words page is
left to the tablet), sends them there, and mirrors every `lessons/NNNN.txt` back into
`vocab/lessons/NNNN-<phrase>.md` (`lesson_markdown`), the web page and `--vault`. `install_vocab_app` ships
the atlases, `check.bin`, `key`, `teacher.md`, `localtime`, `config` (doc= pdf= wdir= words=), the lessons
made on the PC in tablet form (`local_lessons_for_tablet`: PNG → zlib) and the service, keeping the tablet's
`lessons`, `done` and `inbox`. Loops around printed text are not supported (best-fit zoom crops pages; the
highlighter is the tool). `RM_FIXTURES=<dir>` answers Gemini from `gemini.json` and skips the restart
(`rmvocab <dir> [xochitl.conf] [event device]`; `journalctl -u rmvocab -f` on the tablet).

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
