"""Plain code: fetch a diff, parse it, filter it and number its lines for the model.

Nothing in this module calls the model. It decides *what* the model gets to see.
"""

from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass, field

from reviewer import ReviewerError

# Files that are generated, vendored or binary. Reviewing them wastes tokens
# and produces noise, so they are never sent to the model.
SKIP_PATTERNS = [
    "*.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Cargo.lock", "go.sum", "*.min.js", "*.min.css", "*.map",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.ico", "*.pdf", "*.zip", "*.svg",
    "dist/*", "build/*", "node_modules/*", "vendor/*",
]


@dataclass
class DiffLine:
    kind: str  # "+" added, "-" removed, " " context, "@" hunk header
    new_lineno: int | None  # line number in the new file (None for removed lines and headers)
    text: str


@dataclass
class FileDiff:
    path: str
    status: str = "modified"  # added | modified | deleted | renamed
    old_path: str | None = None
    binary: bool = False
    lines: list[DiffLine] = field(default_factory=list)

    @property
    def added_lines(self) -> set[int]:
        return {l.new_lineno for l in self.lines if l.kind == "+" and l.new_lineno}

    @property
    def visible_lines(self) -> set[int]:
        """New-file line numbers the model was shown (added and context)."""
        return {l.new_lineno for l in self.lines if l.kind in "+ " and l.new_lineno}

    @property
    def change_count(self) -> int:
        return sum(1 for l in self.lines if l.kind in "+-")


def _run(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise ReviewerError(f"'{cmd[0]}' is not installed or not on PATH.")
    if proc.returncode != 0:
        raise ReviewerError(f"`{' '.join(cmd)}` failed:\n{proc.stderr.strip()}")
    return proc.stdout


def git_diff(base: str | None = None, staged: bool = False) -> str:
    """Local changes: staged, against a base ref (base...HEAD), or uncommitted work."""
    cmd = ["git", "diff", "--no-color", "--no-ext-diff"]
    if staged:
        cmd.append("--cached")
    elif base:
        cmd.append(f"{base}...HEAD")
    else:
        cmd.append("HEAD")
    return _run(cmd)


def pr_diff(pr: str, repo: str | None = None) -> str:
    cmd = ["gh", "pr", "diff", pr, "--color", "never"]
    if repo:
        cmd += ["--repo", repo]
    return _run(cmd)


def pr_info(pr: str, repo: str | None = None) -> dict:
    import json

    cmd = ["gh", "pr", "view", pr, "--json", "number,title,body,url"]
    if repo:
        cmd += ["--repo", repo]
    return json.loads(_run(cmd))


def _strip_prefix(path: str) -> str:
    return path[2:] if path[:2] in ("a/", "b/") else path


def parse_diff(text: str) -> list[FileDiff]:
    """Parse a unified diff (git format) into files with new-file line numbers."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    in_hunk = False
    new_line = 0

    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            parts = raw.split(" b/", 1)
            path = parts[1] if len(parts) == 2 else raw.split()[-1]
            current = FileDiff(path=_strip_prefix(path))
            files.append(current)
            in_hunk = False
            continue
        if current is None:
            continue

        if not in_hunk:
            if raw.startswith("new file mode"):
                current.status = "added"
            elif raw.startswith("deleted file mode"):
                current.status = "deleted"
            elif raw.startswith("rename from "):
                current.old_path = raw[len("rename from "):]
                current.status = "renamed"
            elif raw.startswith("rename to "):
                current.path = raw[len("rename to "):]
            elif raw.startswith("Binary files") or raw.startswith("GIT binary patch"):
                current.binary = True
            elif raw.startswith("+++ ") and not raw.startswith("+++ /dev/null"):
                current.path = _strip_prefix(raw[4:].strip())

        if raw.startswith("@@"):
            in_hunk = True
            # @@ -a,b +c,d @@ optional section heading
            try:
                new_part = raw.split("+", 1)[1].split(" ", 1)[0]
                new_line = int(new_part.split(",")[0])
            except (IndexError, ValueError):
                raise ReviewerError(f"Could not parse hunk header: {raw!r}")
            current.lines.append(DiffLine("@", None, raw))
            continue

        if not in_hunk:
            continue
        if raw.startswith("+"):
            current.lines.append(DiffLine("+", new_line, raw[1:]))
            new_line += 1
        elif raw.startswith("-"):
            current.lines.append(DiffLine("-", None, raw[1:]))
        elif raw.startswith(" ") or raw == "":
            current.lines.append(DiffLine(" ", new_line, raw[1:]))
            new_line += 1
        # "\ No newline at end of file" and anything else is ignored

    return files


def should_skip(f: FileDiff) -> str | None:
    """Return the reason a file is left out of the review, or None to keep it."""
    if f.binary:
        return "binary file"
    if f.status == "deleted":
        return "file deleted"
    for pattern in SKIP_PATTERNS:
        if fnmatch.fnmatch(f.path, pattern) or fnmatch.fnmatch(f.path.rsplit("/", 1)[-1], pattern):
            return "generated or vendored file"
    if f.change_count == 0:
        return "no line changes"
    return None


def render_for_model(f: FileDiff) -> str:
    """Show a file's diff with explicit new-file line numbers the model can cite."""
    out = [f"### FILE: {f.path} ({f.status})"]
    for l in f.lines:
        if l.kind == "@":
            out.append(l.text)
        elif l.kind == "-":
            out.append(f"-      | {l.text}")
        else:
            out.append(f"{l.kind}{l.new_lineno:>5} | {l.text}")
    return "\n".join(out)


@dataclass
class PreparedDiff:
    files: list[FileDiff]  # files actually sent to the model
    text: str  # numbered diff text for the prompt
    skipped: list[tuple[str, str]]  # (path, reason) for files left out


def prepare(text: str, max_chars: int = 60_000) -> PreparedDiff:
    """Filter files and fit the diff into a character budget, smallest files first."""
    files = parse_diff(text)
    skipped: list[tuple[str, str]] = []
    keep: list[FileDiff] = []
    for f in files:
        reason = should_skip(f)
        if reason:
            skipped.append((f.path, reason))
        else:
            keep.append(f)

    # Smallest first, so one huge file can't crowd out many small ones.
    chunks: list[tuple[FileDiff, str]] = []
    used = 0
    for f in sorted(keep, key=lambda f: f.change_count):
        chunk = render_for_model(f)
        if used + len(chunk) > max_chars:
            skipped.append((f.path, "over the size budget"))
            continue
        chunks.append((f, chunk))
        used += len(chunk)

    # Present files to the model in their original diff order.
    order = {f.path: i for i, f in enumerate(files)}
    chunks.sort(key=lambda c: order[c[0].path])
    return PreparedDiff(
        files=[f for f, _ in chunks],
        text="\n\n".join(c for _, c in chunks),
        skipped=skipped,
    )
