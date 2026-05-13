# Describe a b-roll clip

You are looking at a 4x4 contact sheet of frames sampled evenly from a single
b-roll clip. Each cell is labeled with its source timecode in seconds.

Produce a structured description so the clip can later be matched against
spoken dialog. Use the `describe_clip` tool to return:

- **subject** — concise noun phrase (e.g. "two people walking on beach")
- **motion** — camera/subject motion (e.g. "handheld tracking left", "static")
- **mood** — single adjective (e.g. "warm", "tense", "energetic")
- **tags** — 3–8 short keywords for retrieval
- **suggested_in_sec / suggested_out_sec** — the strongest 2-5s window from
  the timecodes shown on the sheet
- **description** — one sentence summary
