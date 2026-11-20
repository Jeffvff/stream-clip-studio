from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from updates import blob_id, check_update

OLD, NEW = "1" * 40, "2" * 40


class UpdateTest(unittest.TestCase):
    def test_new_revision_warns_instead_of_changing_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / ".studio-revision"
            marker.write_text(OLD, encoding="ascii")
            output = io.StringIO()
            with patch("updates.github_get", side_effect=[{"sha": NEW}, {"status": "ahead"}]), redirect_stderr(output):
                self.assertEqual(check_update(root), 0)
            self.assertIn("atualização disponível", output.getvalue())
            self.assertIn(NEW, output.getvalue())
            self.assertEqual(marker.read_text(encoding="ascii"), OLD)

    def test_zip_copy_matches_repository_even_with_windows_line_endings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_bytes(b"hello\r\n")
            commit = {"sha": NEW, "commit": {"tree": {"sha": "3" * 40}}}
            tree = {"truncated": False, "tree": [{"type": "blob", "path": "README.md", "sha": blob_id(b"hello\n")}]}
            output = io.StringIO()
            with patch("updates.github_get", side_effect=[commit, tree]), redirect_stdout(output):
                self.assertEqual(check_update(root, verbose=True), 0)
            self.assertIn("está atualizado", output.getvalue())

    def test_offline_mode_leaves_normal_usage_quiet(self):
        output = io.StringIO()
        with patch("updates.github_get", side_effect=OSError("offline")), redirect_stderr(output):
            self.assertEqual(check_update(Path.cwd()), 1)
        self.assertEqual(output.getvalue(), "")
