---
id: 2026-08-03-qwen-ladder-01
kind: experiment          # experiment | build
config: configs/2026-08-03-qwen-ladder-01.yaml   # omit this line if the work has no config
---

# Does extraction F1 degrade monotonically down the model-size ladder?

<!--
One file per experiment/build id. Append a new "## Session <date>" block each
working session — do not rewrite earlier blocks.

Prompts:      the human instructions, verbatim or lightly trimmed. Substantive
              ones only (what to do, what changed direction, what was ruled out).
Implemented:  3-5 sentences, written by Claude. A pointer-length summary, not a
              copy of the private note's ## Implementation section.
Commits:      short hashes + subjects that already exist when this file is
              committed. The trailing devlog commit does not list itself.

Public file: no secrets, no absolute cluster paths, no unpublished-result
specifics you would not put in a commit message.
-->

## Session 2026-08-03

### Prompts

> Set up the qwen size-ladder experiment from the design note: run extraction on
> pond across the three Qwen sizes, same seed, and report F1 per size.

> Use the reviewed ground truth, not the full set. Don't add a new runner —
> thread it through run_extraction.

### Implemented

Added `configs/2026-08-03-qwen-ladder-01.yaml` with the three model ids and
`dataset: pond` in `params`. `scripts/run_experiment.py` already covers the
`entry_point: extraction` shape, so no runner code changed. Rung 1 (config
parses, params map to `run_extraction` flags) and Rung 2 (one-doc smoke on the
smallest model) passed in-shell; full run submitted via `scripts/submit.sh`.

### Commits

- `a1b2c3d` qwen-ladder: add the experiment-contract config
- `e4f5a6b` qwen-ladder: pass reviewed-GT flag through run_extraction
