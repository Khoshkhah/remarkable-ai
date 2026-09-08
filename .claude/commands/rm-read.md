---
description: Read and analyze handwritten notes from your reMarkable 2 tablet over Wi-Fi
argument: notebook_name (optional)
---

Execute the following workflow to read the user's reMarkable notes:

1. If no notebook name is specified, read the latest modified notebook:
   ```bash
   rm-ai read "$1"
   ```
2. Inspect the rendered page image generated in the temporary folder or pass it to vision.
3. Provide:
   - Complete accurate transcription of text (including English and Persian/Farsi).
   - Explanation of all diagrams, boxes, arrows, or math formulas.
   - List of extracted tasks or next steps.
