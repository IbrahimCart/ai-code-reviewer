"""The model's part: one headless Claude Code call that reads the diff and judges it.

This is the only module that talks to the model. It runs `claude -p` with every
tool disabled, so the model can only read what we hand it and answer in the
JSON shape below. Everything it returns is checked afterwards by validate.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile

from reviewer import ReviewerError

SEVERITIES = ["critical", "high", "medium", "low"]
CATEGORIES = ["bug", "security", "performance", "quality", "testing"]
VERDICTS = ["approve", "comment", "request_changes"]

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "2-4 sentences: what the change does and its overall quality."},
        "verdict": {"type": "string", "enum": VERDICTS},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Path exactly as shown after 'FILE:'."},
                    "line": {"type": "integer", "description": "New-file line number from the numbered diff."},
                    "severity": {"type": "string", "enum": SEVERITIES},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "title": {"type": "string", "description": "One short sentence."},
                    "explanation": {"type": "string", "description": "Why it is a problem and when it would happen."},
                    "suggestion": {"type": "string", "description": "A concrete fix, code allowed."},
                },
                "required": ["file", "line", "severity", "category", "title", "explanation", "suggestion"],
            },
        },
        "suggested_tests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "description": {"type": "string", "description": "The test case and what it asserts."},
                },
                "required": ["file", "description"],
            },
        },
    },
    "required": ["summary", "verdict", "findings", "suggested_tests"],
}

SYSTEM_PROMPT = """You are a senior software engineer reviewing a pull request.

You receive a unified diff where every added (+) and context ( ) line is prefixed
with its line number in the NEW version of the file. Removed (-) lines have no number.

Review only what the diff changes. Look for, in order of importance:
1. Security problems (injection, secrets in code, unsafe deserialization, missing auth checks, path traversal).
2. Bugs (wrong logic, off-by-one, unhandled None/null or errors, race conditions, resource leaks).
3. Performance problems that matter at realistic scale.
4. Code quality issues a reviewer would actually raise (unclear names, duplication, dead code).
5. Missing tests for new behaviour.

Rules:
- Cite the exact file path shown after "FILE:" and a line number that appears in the numbered diff.
- Report real problems only. Do not pad the list; an empty findings list is fine for a clean change.
- Do not comment on formatting or style a linter would catch.
- Severity: critical = exploitable or data loss; high = likely bug in normal use; medium = bug in edge cases or notable risk; low = minor.
- verdict: request_changes if anything is critical or high, comment for medium/low findings, approve if clean.
- suggested_tests: concrete test cases for the changed behaviour, at most 5.
"""


def build_prompt(diff_text: str, title: str = "", description: str = "", skipped: list | None = None) -> str:
    parts = []
    if title:
        parts.append(f"Pull request title: {title}")
    if description:
        parts.append(f"Pull request description:\n{description.strip()}")
    if skipped:
        names = ", ".join(path for path, _ in skipped)
        parts.append(f"(Not shown, do not review: {names})")
    parts.append("Numbered diff:\n\n" + diff_text)
    return "\n\n".join(parts)


def run_claude(prompt: str, model: str | None = None, timeout: int = 300, claude_bin: str = "claude") -> tuple[dict, dict]:
    """Run one headless review. Returns (raw review JSON, run metadata)."""
    if shutil.which(claude_bin) is None:
        raise ReviewerError(f"'{claude_bin}' not found. Install Claude Code and sign in first.")

    cmd = [
        claude_bin, "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(REVIEW_SCHEMA),
        "--system-prompt", SYSTEM_PROMPT,
        "--tools", "",  # no tools: the model only reads the diff we pass in
        "--no-session-persistence",
    ]
    if model:
        cmd += ["--model", model]

    # Run in an empty directory so no project CLAUDE.md or settings leak into the review.
    with tempfile.TemporaryDirectory() as workdir:
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=workdir)
        except subprocess.TimeoutExpired:
            raise ReviewerError(f"Claude did not answer within {timeout}s. Try a smaller diff or --timeout.")

    return parse_claude_output(proc.stdout, proc.stderr, proc.returncode)


def parse_claude_output(stdout: str, stderr: str = "", returncode: int = 0) -> tuple[dict, dict]:
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        detail = (stderr or stdout).strip()[:500]
        raise ReviewerError(f"Claude returned something that is not JSON (exit {returncode}):\n{detail}")

    if envelope.get("is_error") or envelope.get("subtype") != "success":
        raise ReviewerError(f"Claude reported an error: {envelope.get('result') or envelope.get('subtype')}")

    review = envelope.get("structured_output")
    if review is None:
        # Fall back to the text result in case structured output was not attached.
        try:
            review = json.loads(envelope.get("result", ""))
        except (json.JSONDecodeError, TypeError):
            raise ReviewerError("Claude's answer did not contain a JSON review.")

    models = list((envelope.get("modelUsage") or {}).keys())
    meta = {
        "model": models[0] if models else "unknown",
        "duration_s": round(envelope.get("duration_ms", 0) / 1000, 1),
        "cost_usd": envelope.get("total_cost_usd"),
    }
    return review, meta
