# devlog

A per-build / per-experiment record of **what was asked for** and **what was
built**, for AI-assisted development in this repo.

Each file pairs the human-written prompts that drove a piece of work with a short
summary of what landed and the commits that carry it. This is the public, curated
disclosure record. The full rationale, hypotheses, staged-gate results, and
assessments live in the private research-notes repo (`notes/`, not committed here).

## One file per contract id

`devlog/<id>.md`, where `<id>` is the contract id (`YYYY-MM-DD-slug-NN`) — the same
string used by `configs/<id>.yaml`, the private build/experiment note, and the run
directory. See `notes/hub/conventions.md`.

A `devlog/` file is only created for an `<id>` that also exists as a build or
experiment note. Work that is not a registered build or experiment (one-off
refactors, dependency bumps, bug fixes) is covered by its commit message and the
`Claude-Session:` trailer — it does not get a `devlog/` file.

## What goes in a file

Frontmatter, then one `## Session <date>` block per working session (never rewrite
earlier blocks):

- **Prompts** — the instructions the human gave, verbatim or lightly trimmed.
  Substantive ones only; drop typo-fixes and bare approvals.
- **Implemented** — 3–5 sentences by Claude: what changed, which entry points, which
  configs, which ladder rungs passed. A pointer-length summary, not a copy of the
  private note.
- **Commits** — the short hashes and subjects for that session's commits.

## Workflow

`/devlog <id>` at the end of a `/develop` session does all of it: writes or appends
this file, commits the session's implementation code, records the commit hashes here
and in the private note, then commits this file on its own
(`devlog: <id> session <date>`). Because that is a trailing commit, every hash it
lists already exists and it does not list itself.

## This is a public file

Same review bar as a commit message. No secrets or API keys, no absolute cluster
paths (repo-relative only), no unpublished-result specifics. No session id. If a
prompt contained something that shouldn't be public, paraphrase it and say so.
