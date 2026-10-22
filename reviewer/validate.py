"""Plain code: check the model's review against the real diff before anyone sees it.

The model can misnumber lines, name files that are not in the change, repeat
itself or pick a verdict that contradicts its own findings. Each check here
catches one of those, and every dropped or adjusted item is reported, so a
failure can be traced to the model or to this code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from reviewer.diff import FileDiff
from reviewer.model import CATEGORIES, SEVERITIES, VERDICTS

# How far a cited line may be from a line the model was shown before it is
# treated as made up. Models often cite the line just above or below.
LINE_TOLERANCE = 2


@dataclass
class Finding:
    file: str
    line: int
    severity: str
    category: str
    title: str
    explanation: str
    suggestion: str
    on_changed_line: bool = True


@dataclass
class Review:
    summary: str
    verdict: str
    findings: list[Finding]
    suggested_tests: list[dict]
    dropped: list[tuple[dict, str]] = field(default_factory=list)  # (raw finding, reason)
    notes: list[str] = field(default_factory=list)  # adjustments made by the checks

    def count(self, severity: str) -> int:
        return sum(1 for f in self.findings if f.severity == severity)


def _norm_path(path: str) -> str:
    path = path.strip()
    while path.startswith("./"):
        path = path[2:]
    return path[2:] if path[:2] in ("a/", "b/") else path


def _nearest(line: int, candidates: set[int]) -> int | None:
    best = min(candidates, key=lambda c: abs(c - line), default=None)
    if best is not None and abs(best - line) <= LINE_TOLERANCE:
        return best
    return None


def check(raw: dict, files: list[FileDiff]) -> Review:
    """Turn the model's raw JSON into a Review that only cites real lines."""
    if not isinstance(raw, dict):
        raise ValueError("review is not a JSON object")

    by_path = {f.path: f for f in files}
    notes: list[str] = []
    dropped: list[tuple[dict, str]] = []
    findings: list[Finding] = []
    seen: set[tuple[str, int, str]] = set()

    for item in raw.get("findings") or []:
        if not isinstance(item, dict):
            dropped.append(({"value": item}, "not an object"))
            continue
        missing = [k for k in ("file", "line", "severity", "category", "title") if not item.get(k) and item.get(k) != 0]
        if missing:
            dropped.append((item, f"missing {', '.join(missing)}"))
            continue
        if item["severity"] not in SEVERITIES:
            dropped.append((item, f"unknown severity {item['severity']!r}"))
            continue
        if item["category"] not in CATEGORIES:
            dropped.append((item, f"unknown category {item['category']!r}"))
            continue

        path = _norm_path(str(item["file"]))
        f = by_path.get(path)
        if f is None:
            # Accept a unique suffix match ("app.py" for "src/app.py").
            matches = [p for p in by_path if p.endswith("/" + path)]
            f = by_path[matches[0]] if len(matches) == 1 else None
        if f is None:
            dropped.append((item, "file is not in the reviewed diff"))
            continue

        try:
            line = int(item["line"])
        except (TypeError, ValueError):
            dropped.append((item, "line is not a number"))
            continue
        if line not in f.visible_lines:
            snapped = _nearest(line, f.visible_lines)
            if snapped is None:
                dropped.append((item, f"line {line} is not in the diff for {f.path}"))
                continue
            notes.append(f"Moved '{item['title']}' from line {line} to {snapped} ({f.path}).")
            line = snapped

        key = (f.path, line, str(item["title"]).strip().lower())
        if key in seen:
            dropped.append((item, "duplicate"))
            continue
        seen.add(key)

        findings.append(Finding(
            file=f.path,
            line=line,
            severity=item["severity"],
            category=item["category"],
            title=str(item["title"]).strip(),
            explanation=str(item.get("explanation", "")).strip(),
            suggestion=str(item.get("suggestion", "")).strip(),
            on_changed_line=line in f.added_lines,
        ))

    findings.sort(key=lambda x: (SEVERITIES.index(x.severity), x.file, x.line))

    # The verdict must agree with the findings that survived the checks.
    verdict = raw.get("verdict")
    expected = expected_verdict(findings)
    if verdict not in VERDICTS:
        notes.append(f"Model gave no valid verdict ({verdict!r}); set to {expected}.")
        verdict = expected
    elif verdict != expected:
        notes.append(f"Model said {verdict}, but its checked findings call for {expected}; using {expected}.")
        verdict = expected

    tests = []
    for t in raw.get("suggested_tests") or []:
        if isinstance(t, dict) and str(t.get("description", "")).strip():
            tests.append({"file": str(t.get("file", "")).strip(), "description": str(t["description"]).strip()})
    if len(tests) > 5:
        notes.append(f"Kept 5 of {len(tests)} suggested tests.")
        tests = tests[:5]

    summary = str(raw.get("summary") or "").strip() or "(The model gave no summary.)"
    return Review(summary, verdict, findings, tests, dropped, notes)


def expected_verdict(findings: list[Finding]) -> str:
    severities = {f.severity for f in findings}
    if severities & {"critical", "high"}:
        return "request_changes"
    if findings:
        return "comment"
    return "approve"


def fails(review: Review, threshold: str) -> bool:
    """True if any finding is at or above the threshold severity ('never' disables)."""
    if threshold == "never":
        return False
    limit = SEVERITIES.index(threshold)
    return any(SEVERITIES.index(f.severity) <= limit for f in review.findings)
