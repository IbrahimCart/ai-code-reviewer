"""Command line entry point: wires the plain-code steps around the one model call."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reviewer import ReviewerError, diff, model, report, validate


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="ai-review",
        description="Review a git diff or GitHub pull request with Claude Code (claude -p).",
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--pr", help="GitHub PR number, URL or branch (uses gh)")
    src.add_argument("--base", help="review commits on HEAD since this ref, e.g. main or origin/main")
    src.add_argument("--staged", action="store_true", help="review staged changes")
    src.add_argument("--diff-file", help="review a diff file ('-' reads stdin)")
    p.add_argument("--repo", help="OWNER/NAME for --pr when not run inside the repo")
    p.add_argument("--post", action="store_true", help="post the review on the PR (needs --pr)")
    p.add_argument("--out", help="write the Markdown review to this file")
    p.add_argument("--json", dest="json_out", help="write the checked review as JSON to this file")
    p.add_argument("--fail-on", choices=["critical", "high", "medium", "low", "never"], default="never",
                   help="exit with code 1 if a finding is at or above this severity (default: never)")
    p.add_argument("--model", help="Claude model alias or name (default: Claude Code's default)")
    p.add_argument("--max-chars", type=int, default=60_000, help="diff size budget sent to the model")
    p.add_argument("--timeout", type=int, default=300, help="seconds to wait for Claude")
    p.add_argument("--dry-run", action="store_true", help="print the prompt and exit without calling Claude")
    args = p.parse_args(argv)
    if args.post and not args.pr:
        p.error("--post needs --pr")
    return args


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def load_diff(args: argparse.Namespace) -> tuple[str, str, str]:
    """Return (diff text, title, description) for the chosen source."""
    if args.pr:
        info = diff.pr_info(args.pr, args.repo)
        log(f"Fetching PR #{info['number']}: {info['title']}")
        return diff.pr_diff(args.pr, args.repo), info["title"], info.get("body") or ""
    if args.diff_file:
        text = sys.stdin.read() if args.diff_file == "-" else Path(args.diff_file).read_text()
        return text, "", ""
    if args.staged:
        log("Reviewing staged changes")
    elif args.base:
        log(f"Reviewing commits since {args.base}")
    else:
        log("Reviewing uncommitted changes against HEAD")
    return diff.git_diff(base=args.base, staged=args.staged), "", ""


def run(args: argparse.Namespace) -> int:
    text, title, description = load_diff(args)
    prepared = diff.prepare(text, max_chars=args.max_chars)
    for path, why in prepared.skipped:
        log(f"  skip {path}: {why}")
    if not prepared.files:
        log("Nothing to review.")
        return 0

    prompt = model.build_prompt(prepared.text, title, description, prepared.skipped)
    if args.dry_run:
        print(prompt)
        return 0

    log(f"Asking Claude to review {len(prepared.files)} file(s), {len(prepared.text):,} chars...")
    raw, meta = model.run_claude(prompt, model=args.model, timeout=args.timeout)

    checked = validate.check(raw, prepared.files)
    if checked.dropped:
        log(f"  dropped {len(checked.dropped)} finding(s) that failed checks")
    markdown = report.to_markdown(checked, meta, prepared.skipped, len(prepared.files))
    print(markdown)

    if args.out:
        Path(args.out).write_text(markdown)
        log(f"Wrote {args.out}")
    if args.json_out:
        Path(args.json_out).write_text(report.to_json(checked, meta, prepared.skipped))
        log(f"Wrote {args.json_out}")
    if args.post:
        from reviewer import github

        url = github.post_review(args.pr, checked, markdown, args.repo)
        log(f"Posted review on {url}")

    return 1 if validate.fails(checked, args.fail_on) else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except ReviewerError as e:
        log(f"error: {e}")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
