---
description: Learn English on the tablet: circle a word on the Words page and the tablet makes a Farsi-first lesson page in Vocabulary by itself; the PC watcher adds highlights from PDFs
---

Install once (needs `GEMINI_API_KEY`), or run the optional watcher:
```bash
rm-ai vocab $ARGUMENTS
```
`--install` puts the Words page, the Vocabulary document, the key, the method (`vocab/teacher.md`) and the
program on the tablet. On the tablet: open **Words**, write a word and a sentence, draw a loop around the
word; a check mark appears when the lesson is made, and Vocabulary is rebuilt (one printed page per lesson)
the next time nothing is open. The watcher (no flags) sends highlights on PDFs/EPUBs and marks over
handwriting in other notebooks to the tablet and mirrors every lesson into `vocab/lessons/*.md`,
http://localhost:8765 and `--vault <file>`. Other options: `--teacher <file>`, `--dir`, `--port`.
