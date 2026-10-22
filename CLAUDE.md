# CLAUDE.md

AI GitHub Code Reviewer: a Python CLI (stdlib only) that reviews git diffs and GitHub PRs by running Claude Code headless (`claude -p`). This is TCSS 490 Artifact 3 for Ibrahim Cartan.

## Layout

- `reviewer/diff.py`: fetch (git/gh), parse, filter and number diffs. No model calls.
- `reviewer/model.py`: the only model call: `claude -p --tools "" --json-schema ...`. Keep it the only one.
- `reviewer/validate.py`: checks model output against the diff (files, lines, enums, duplicates, verdict).
- `reviewer/report.py`, `reviewer/github.py`: Markdown/JSON output and posting PR reviews via `gh api`.
- `reviewer/cli.py`: argparse entry point (`python3 -m reviewer` / `ai-review`).
- `examples/sample.diff`: a change with 7 planted bugs, used by the tests and the demo.

## Rules

- Use the Claude Code CLI, never the Anthropic API (the course covers Claude Code only).
- The model gets no tools and only sees the numbered diff. New decisions should go in plain code unless they truly need judgement.
- Every model output passes through `validate.check`. Add a test for any new check.
- Never post approve or request-changes reviews. Always post `COMMENT`.
- Run `python3 -m unittest` before committing. Add an entry to `LOG.md` for each meaningful change.
