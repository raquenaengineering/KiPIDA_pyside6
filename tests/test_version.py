import json
import re
import unittest
from pathlib import Path

from kipida import __version__
from kipida import version as v

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestVersion(unittest.TestCase):
    def test_plugin_json_matches_package_version(self):
        """plugin.json can't import __version__, so guard against drift."""
        manifest = json.loads((REPO_ROOT / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], __version__)

    def test_full_version_format(self):
        full = v.get_full_version()
        self.assertTrue(full == __version__ or re.fullmatch(re.escape(__version__) + r"\+g[0-9a-f]{7}", full), full)

    def test_window_title_mentions_edition_and_version(self):
        title = v.get_window_title()
        self.assertIn("PySide6", title)
        self.assertIn(f"v{v.get_full_version()}", title)

    def test_no_git_falls_back_to_release_version(self):
        self.assertIsNone(v._read_git_commit(REPO_ROOT / "does-not-exist"))

    def test_reads_loose_ref_packed_ref_and_detached_head(self):
        import tempfile
        sha = "0123456789abcdef0123456789abcdef01234567"
        with tempfile.TemporaryDirectory() as tmp:
            git = Path(tmp) / ".git"
            (git / "refs" / "heads").mkdir(parents=True)
            (git / "HEAD").write_text("ref: refs/heads/main\n")

            (git / "packed-refs").write_text(f"# pack-refs with: peeled\n{sha} refs/heads/main\n")
            self.assertEqual(v._read_git_commit(Path(tmp)), sha)

            loose = "fedcba9876543210fedcba9876543210fedcba98"
            (git / "refs" / "heads" / "main").write_text(loose + "\n")
            self.assertEqual(v._read_git_commit(Path(tmp)), loose)

            (git / "HEAD").write_text(sha + "\n")
            self.assertEqual(v._read_git_commit(Path(tmp)), sha)


if __name__ == "__main__":
    unittest.main()
