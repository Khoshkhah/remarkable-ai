---
description: Learn English from what you mark on the tablet: highlights and looped handwriting explained by Gemini, in Farsi-first lessons, one printed page each in the tablet's Vocabulary document
---

Run the vocabulary watcher (it runs until stopped):
```bash
rm-ai vocab $ARGUMENTS
```
Highlight a word or paragraph on a PDF/EPUB, or highlight/loop handwriting in a notebook. Each mark
becomes a lesson (Gemini, `GEMINI_API_KEY`, method in `vocab/teacher.md`): a markdown file and a printed
page in `vocab/lessons/`, shown on http://localhost:8765, appended to `--vault <file>` (Obsidian) if given,
and added as a page of the **Vocabulary** document in the tablet's app folder, which is rebuilt from all
lesson pages the next time nothing is open on the tablet (one reload).
Options: `--install` (push the document with all lessons so far now), `--no-tablet`,
`--no-explain` (capture only), `--teacher <file>`, `--dir`, `--port`.
