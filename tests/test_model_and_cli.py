import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from reviewer import ReviewerError, cli, model

SAMPLE_PATH = str(Path(__file__).parent.parent / "examples" / "sample.diff")


def envelope(**kw):
    base = {"type": "result", "subtype": "success", "is_error": False, "duration_ms": 4200,
            "total_cost_usd": 0.01, "modelUsage": {"claude-test": {}}, "result": ""}
    base.update(kw)
    return json.dumps(base)


class ParseClaudeOutputTest(unittest.TestCase):
    def test_reads_structured_output(self):
        review, meta = model.parse_claude_output(envelope(structured_output={"summary": "x"}))
        self.assertEqual(review, {"summary": "x"})
        self.assertEqual(meta, {"model": "claude-test", "duration_s": 4.2, "cost_usd": 0.01})

    def test_falls_back_to_result_text(self):
        review, _ = model.parse_claude_output(envelope(result='{"summary": "y"}'))
        self.assertEqual(review, {"summary": "y"})

    def test_errors(self):
        with self.assertRaises(ReviewerError):
            model.parse_claude_output("not json", "boom", 1)
        with self.assertRaises(ReviewerError):
            model.parse_claude_output(envelope(is_error=True, result="rate limited"))
        with self.assertRaises(ReviewerError):
            model.parse_claude_output(envelope(result="plain prose"))

    def test_prompt_mentions_skipped_files(self):
        prompt = model.build_prompt("DIFF", "Add search", "body", [("package-lock.json", "generated")])
        self.assertIn("Pull request title: Add search", prompt)
        self.assertIn("do not review: package-lock.json", prompt)
        self.assertTrue(prompt.endswith("DIFF"))


FAKE_REVIEW = {
    "summary": "Adds search.",
    "verdict": "request_changes",
    "findings": [
        {"file": "app/users.py", "line": 19, "severity": "critical", "category": "security",
         "title": "SQL injection", "explanation": "Concatenated input.", "suggestion": "Use parameters."},
        {"file": "app/ghost.py", "line": 1, "severity": "high", "category": "bug",
         "title": "Made up", "explanation": "", "suggestion": ""},
    ],
    "suggested_tests": [{"file": "tests/test_users.py", "description": "search with a quote"}],
}


class CliTest(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    @mock.patch("reviewer.model.run_claude", return_value=(FAKE_REVIEW, {"model": "claude-test", "duration_s": 1.0, "cost_usd": None}))
    def test_end_to_end_with_fake_model(self, run_claude):
        with tempfile.TemporaryDirectory() as tmp:
            out_json = Path(tmp) / "r.json"
            code, out, err = self.run_cli("--diff-file", SAMPLE_PATH, "--fail-on", "high", "--json", str(out_json))
            data = json.loads(out_json.read_text())

        self.assertEqual(code, 1)  # a critical finding is above the threshold
        self.assertIn("SQL injection", out)
        self.assertIn("`app/users.py:19`", out)
        self.assertNotIn("Made up", out.split("Review checks")[0])  # dropped before the findings list
        self.assertIn("file is not in the reviewed diff", out)
        self.assertIn("skip package-lock.json", err)
        self.assertEqual(len(data["findings"]), 1)
        self.assertEqual(data["dropped"][0]["reason"], "file is not in the reviewed diff")
        # The model only ever saw the numbered diff, never the lockfile.
        prompt = run_claude.call_args.args[0]
        self.assertIn("+   19 |", prompt)
        self.assertNotIn("lockfileVersion", prompt)

    @mock.patch("reviewer.model.run_claude")
    def test_dry_run_does_not_call_model(self, run_claude):
        code, out, _ = self.run_cli("--diff-file", SAMPLE_PATH, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("### FILE: app/users.py", out)
        run_claude.assert_not_called()

    @mock.patch("reviewer.model.run_claude", side_effect=ReviewerError("Claude reported an error: overloaded"))
    def test_model_failure_exits_2(self, _):
        code, _, err = self.run_cli("--diff-file", SAMPLE_PATH)
        self.assertEqual(code, 2)
        self.assertIn("overloaded", err)

    def test_post_requires_pr(self):
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            cli.parse_args(["--post"])


if __name__ == "__main__":
    unittest.main()
