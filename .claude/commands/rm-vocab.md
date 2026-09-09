---
description: Learn English from what you mark on the tablet: highlights and looped handwriting explained by Gemini, in Farsi-first lessons, written on the Vocabulary page
---

Run the vocabulary watcher (it runs until stopped):
```bash
rm-ai vocab $ARGUMENTS
```
Highlight a word or paragraph on a PDF/EPUB, or highlight/loop handwriting in a notebook. Each mark
becomes a lesson (Gemini, `GEMINI_API_KEY`, method in `vocab/teacher.md`) on http://localhost:8765, in
the markdown note (`--vault <file>` for Obsidian) and, as a short card, on the tablet's Vocabulary page.
Options: `--install` (put the Vocabulary page and its program on the tablet first), `--no-tablet`,
`--no-explain` (capture only), `--teacher <file>`, `--dir`, `--port`.
