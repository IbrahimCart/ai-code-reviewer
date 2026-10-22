# Project Log

What I planned, what I built and what changed, for TCSS 490 Artifact 3 (*AI Inside It*).

## 2026-10-22: Plan

**Goal:** a code reviewer where a model does real work every run: it reads a diff and decides what is wrong with it.

**Constraint from the assignment:** my Claude access covers Claude Code, not the paid Claude API. So instead of calling the API, the app runs `claude -p` headless as one pipeline step. This also means no API key to store.

**Split between model and code (decided up front):**
- Model: read the numbered diff and return findings (file, line, severity, category, explanation, fix) and suggested tests as JSON.
- Code: everything around that call. It fetches and filters the diff, numbers the lines, checks the model's answer against the real diff, picks the verdict and exit code, and posts to GitHub.

**First plan was a GitHub Action.** I dropped it because an Action would need an API key in CI. A local CLI plus an optional `pre-push` hook gives the same workflow on my subscription.

## 2026-10-22: Build

- `diff.py`: unified diff parser that tracks new-file line numbers, skips lockfiles, binaries and vendored files, and enforces a size budget (smallest files first).
- `model.py`: one `claude -p` call with `--tools ""` (no tools), `--json-schema` for structured output, a reviewer system prompt, and a run in an empty temp directory so no project `CLAUDE.md` gets into the review.
- `validate.py`: checks the model's answer before anyone sees it:
  - files that are not in the diff → dropped
  - lines the model never saw → dropped, or snapped if off by 1 or 2
  - bad severity or category values, missing fields, duplicates → dropped
  - verdict recomputed from the findings that survive
- `report.py` / `github.py`: Markdown and JSON reports, and a PR review with inline comments posted through `gh api`. It is always posted as a `COMMENT` review, so the tool never approves or blocks a PR by itself.
- `hooks/pre-push`: blocks a push on high or critical findings, and lets the push through if the reviewer itself fails.

## 2026-10-22: First real run

I made a sample change with 7 bugs planted on purpose (`examples/sample.diff`): SQL injection, path traversal, a hardcoded secret, an off-by-one in paging, division by zero, and two resource leaks. Claude found all 7 in about 14 seconds, with correct line numbers and useful fixes. The lockfile in the same change was skipped as intended.

**Change:** the first sample used a fake `sk_live_...` key, and GitHub's secret scanning would likely block the push. I replaced it with an obviously fake value.

## 2026-10-22: Tests

30 `unittest` tests with the model mocked: diff parsing (renames, deletes, a removed SQL comment that looks like a `---` header), the size budget, every validation rule, parsing Claude's JSON output, and a CLI run from start to finish.

**Bug found while testing:** the path cleanup used `lstrip("./")`, which would have turned `.github/workflows/ci.yml` into `github/workflows/ci.yml`. I changed it to strip only a leading `./` prefix.

## What I learned

- Keeping the model's part to one call with no tools made the system easy to reason about. When a review is wrong, the "Review checks" section shows whether the model made something up or my code dropped something.
- Structured output (`--json-schema`) helps, but it isn't enough on its own. The model can return valid JSON that cites a line it never saw, and GitHub rejects the whole review when one inline comment is outside the diff.
