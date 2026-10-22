import unittest
from pathlib import Path

from reviewer import diff, validate

SAMPLE = (Path(__file__).parent.parent / "examples" / "sample.diff").read_text()
FILES = diff.prepare(SAMPLE).files


def finding(**kw):
    base = {"file": "app/users.py", "line": 19, "severity": "critical", "category": "security",
            "title": "SQL injection", "explanation": "e", "suggestion": "s"}
    base.update(kw)
    return base


def review(*findings, verdict="request_changes", tests=()):
    return {"summary": "sum", "verdict": verdict, "findings": list(findings), "suggested_tests": list(tests)}


class CheckTest(unittest.TestCase):
    def test_keeps_a_grounded_finding(self):
        r = validate.check(review(finding()), FILES)
        self.assertEqual(len(r.findings), 1)
        self.assertTrue(r.findings[0].on_changed_line)
        self.assertEqual(r.dropped, [])

    def test_drops_file_not_in_diff(self):
        r = validate.check(review(finding(file="app/payments.py")), FILES)
        self.assertEqual(r.findings, [])
        self.assertEqual(r.dropped[0][1], "file is not in the reviewed diff")

    def test_drops_skipped_file(self):
        r = validate.check(review(finding(file="package-lock.json", line=1)), FILES)
        self.assertEqual(r.dropped[0][1], "file is not in the reviewed diff")

    def test_drops_line_far_from_the_diff(self):
        r = validate.check(review(finding(line=200)), FILES)
        self.assertIn("line 200 is not in the diff", r.dropped[0][1])

    def test_snaps_line_that_is_off_by_a_little(self):
        r = validate.check(review(finding(line=9)), FILES)  # 9 is not shown; 7 is the nearest shown line
        self.assertEqual(r.findings[0].line, 7)
        self.assertTrue(any("Moved" in n for n in r.notes))

    def test_accepts_path_prefixes_and_suffixes(self):
        r = validate.check(review(finding(file="./app/users.py"), finding(file="b/app/stats.py", line=5, title="Div by zero"),
                                  finding(file="users.py", line=30, title="Traversal")), FILES)
        self.assertEqual([f.file for f in r.findings], ["app/stats.py", "app/users.py", "app/users.py"])

    def test_drops_bad_enum_and_missing_fields(self):
        r = validate.check(review(finding(severity="urgent"), finding(category="style"), {"file": "app/users.py"}), FILES)
        self.assertEqual(r.findings, [])
        reasons = [why for _, why in r.dropped]
        self.assertIn("unknown severity 'urgent'", reasons)
        self.assertIn("unknown category 'style'", reasons)
        self.assertTrue(reasons[2].startswith("missing line"))

    def test_drops_duplicates(self):
        r = validate.check(review(finding(), finding(title=" sql injection ")), FILES)
        self.assertEqual(len(r.findings), 1)
        self.assertEqual(r.dropped[0][1], "duplicate")

    def test_sorts_by_severity(self):
        r = validate.check(review(finding(severity="low", line=31, title="a"), finding(severity="high", line=3, title="b")), FILES)
        self.assertEqual([f.severity for f in r.findings], ["high", "low"])

    def test_verdict_follows_checked_findings(self):
        # The model wanted changes, but its only finding was made up, so nothing is left to block on.
        r = validate.check(review(finding(file="nope.py")), FILES)
        self.assertEqual(r.verdict, "approve")
        self.assertTrue(any("using approve" in n for n in r.notes))

        r = validate.check(review(finding(), verdict="approve"), FILES)
        self.assertEqual(r.verdict, "request_changes")

        r = validate.check(review(finding(severity="medium"), verdict="nonsense"), FILES)
        self.assertEqual(r.verdict, "comment")

    def test_limits_suggested_tests(self):
        tests = [{"file": "t.py", "description": f"case {i}"} for i in range(7)] + [{"file": "t.py", "description": " "}]
        r = validate.check(review(tests=tests, verdict="approve"), FILES)
        self.assertEqual(len(r.suggested_tests), 5)

    def test_rejects_non_object(self):
        with self.assertRaises(ValueError):
            validate.check(["not", "a", "review"], FILES)


class FailsTest(unittest.TestCase):
    def test_threshold(self):
        r = validate.check(review(finding(severity="medium"), verdict="comment"), FILES)
        self.assertFalse(validate.fails(r, "high"))
        self.assertTrue(validate.fails(r, "medium"))
        self.assertTrue(validate.fails(r, "low"))
        self.assertFalse(validate.fails(r, "never"))


if __name__ == "__main__":
    unittest.main()
