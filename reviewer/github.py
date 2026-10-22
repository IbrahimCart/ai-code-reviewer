"""Plain code: post a checked review to a GitHub pull request through the gh CLI."""

from __future__ import annotations

import json
import re
import subprocess

from reviewer import ReviewerError
from reviewer.report import ICONS
from reviewer.validate import Review


def _gh(args: list[str], stdin: str | None = None) -> str:
    try:
        proc = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise ReviewerError("The GitHub CLI 'gh' is not installed. See https://cli.github.com.")
    if proc.returncode != 0:
        raise ReviewerError(f"gh {' '.join(args[:3])} failed:\n{proc.stderr.strip()}")
    return proc.stdout


def inline_comment(f) -> str:
    body = f"{ICONS[f.severity]} **{f.title}** ({f.severity} · {f.category})\n\n{f.explanation}"
    if f.suggestion:
        body += f"\n\n**Suggestion:** {f.suggestion}"
    return body


def post_review(pr: str, review: Review, body: str, repo: str | None = None) -> str:
    """Post one PR review: the summary as its body and each finding as an inline comment.

    The event is always COMMENT. The tool never approves or blocks a PR on its
    own; that stays a human decision.
    """
    view = ["pr", "view", pr, "--json", "number,url,headRefOid"]
    if repo:
        view += ["--repo", repo]
    info = json.loads(_gh(view))
    match = re.match(r"https://github\.com/([^/]+/[^/]+)/pull/\d+", info["url"])
    if not match:
        raise ReviewerError(f"Unexpected PR URL: {info['url']}")
    slug = match.group(1)

    payload = {
        "commit_id": info["headRefOid"],
        "event": "COMMENT",
        "body": body,
        "comments": [
            {"path": f.file, "line": f.line, "side": "RIGHT", "body": inline_comment(f)}
            for f in review.findings
        ],
    }
    endpoint = f"repos/{slug}/pulls/{info['number']}/reviews"
    try:
        _gh(["api", endpoint, "--method", "POST", "--input", "-"], stdin=json.dumps(payload))
    except ReviewerError:
        # GitHub rejects the whole review if any inline line is outside the diff.
        # Fall back to a single summary comment so the feedback still lands.
        payload["comments"] = []
        _gh(["api", endpoint, "--method", "POST", "--input", "-"], stdin=json.dumps(payload))
    return info["url"]
