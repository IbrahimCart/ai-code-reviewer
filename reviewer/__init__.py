"""AI GitHub Code Reviewer: reviews diffs with Claude Code running headless."""

__version__ = "0.1.0"


class ReviewerError(Exception):
    """A problem the user can fix (bad input, missing tool, failed model call)."""
