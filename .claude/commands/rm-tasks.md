---
description: Extract action items and to-do tasks from your reMarkable 2 handwritten notes
argument: notebook_name (optional)
---

Execute the following workflow to extract tasks from the reMarkable tablet:

1. Run the task extractor:
   ```bash
   rm-ai read "$1" --action tasks
   ```
2. Parse the output and format as an actionable checklist:
   - [ ] High priority tasks
   - [ ] Standard tasks / followup items
   - [ ] Notes / context
