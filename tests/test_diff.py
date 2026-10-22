import unittest
from pathlib import Path

from reviewer import diff

SAMPLE = (Path(__file__).parent.parent / "examples" / "sample.diff").read_text()


class ParseDiffTest(unittest.TestCase):
    def setUp(self):
        self.files = {f.path: f for f in diff.parse_diff(SAMPLE)}

    def test_finds_every_file(self):
        self.assertEqual(set(self.files), {"app/stats.py", "app/users.py", "package-lock.json"})

    def test_new_file_status_and_lines(self):
        stats = self.files["app/stats.py"]
        self.assertEqual(stats.status, "added")
        self.assertEqual(stats.added_lines, {1, 2, 3, 4, 5})

    def test_line_numbers_follow_hunk_headers(self):
        users = self.files["app/users.py"]
        self.assertIn(3, users.added_lines)  # API_KEY line
        self.assertIn(19, users.added_lines)  # SQL query line
        self.assertNotIn(12, users.added_lines)  # context line
        self.assertIn(12, users.visible_lines)
        self.assertNotIn(9, users.visible_lines)  # between hunks, never shown

    def test_removed_lines_have_no_number(self):
        lock = self.files["package-lock.json"]
        removed = [l for l in lock.lines if l.kind == "-"]
        self.assertEqual(len(removed), 1)
        self.assertIsNone(removed[0].new_lineno)

    def test_deleted_and_renamed_files(self):
        text = (
            "diff --git a/old.py b/old.py\ndeleted file mode 100644\n--- a/old.py\n+++ /dev/null\n"
            "@@ -1 +0,0 @@\n-x = 1\n"
            "diff --git a/a.py b/b.py\nsimilarity index 90%\nrename from a.py\nrename to b.py\n"
            "--- a/a.py\n+++ b/b.py\n@@ -1 +1 @@\n-y = 1\n+y = 2\n"
        )
        files = diff.parse_diff(text)
        self.assertEqual(files[0].status, "deleted")
        self.assertEqual((files[1].path, files[1].old_path, files[1].status), ("b.py", "a.py", "renamed"))

    def test_removed_line_that_looks_like_a_header(self):
        text = "diff --git a/x.sql b/x.sql\n--- a/x.sql\n+++ b/x.sql\n@@ -1,2 +1,1 @@\n--- a comment\n keep\n"
        f = diff.parse_diff(text)[0]
        self.assertEqual([l.kind for l in f.lines], ["@", "-", " "])
        self.assertEqual(f.visible_lines, {1})


class PrepareTest(unittest.TestCase):
    def test_skips_lockfile_and_numbers_lines(self):
        prepared = diff.prepare(SAMPLE)
        self.assertEqual([f.path for f in prepared.files], ["app/stats.py", "app/users.py"])
        self.assertIn(("package-lock.json", "generated or vendored file"), prepared.skipped)
        self.assertIn("+   19 |     query =", prepared.text)
        self.assertNotIn("lockfileVersion", prepared.text)

    def test_budget_drops_largest_file_first(self):
        small = len(diff.render_for_model(diff.parse_diff(SAMPLE)[0]))
        prepared = diff.prepare(SAMPLE, max_chars=small + 10)
        self.assertEqual([f.path for f in prepared.files], ["app/stats.py"])
        self.assertIn(("app/users.py", "over the size budget"), prepared.skipped)

    def test_empty_diff(self):
        prepared = diff.prepare("")
        self.assertEqual(prepared.files, [])


if __name__ == "__main__":
    unittest.main()
