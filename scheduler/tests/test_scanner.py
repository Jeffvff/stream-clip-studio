import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from video_scanner import scan


class ScannerTest(unittest.TestCase):
    def test_unicode_order_recursion_and_dedup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sub = root / "Áudios e vídeos"
            sub.mkdir()
            (sub / "Ônibus.MOV").write_bytes(b"one")
            (root / "Água.webm").write_bytes(b"two")
            (root / "z-cópia.mp4").write_bytes(b"one")
            (root / "instruções.txt").write_bytes(b"skip")
            items, rejected = scan(root, recursive=True, order="custom",
                                   custom=["Água.webm", "Áudios e vídeos/Ônibus.MOV", "z-cópia.mp4"], ffprobe=None)
            self.assertEqual([v.path.name for v in items], ["Água.webm", "Ônibus.MOV"])
            self.assertEqual(len(rejected), 1)

    def test_windows_creation_order_and_empty_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.mp4").write_bytes(b"one")
            (root / "b.webm").touch()
            items, rejected = scan(root, recursive=False, order="creation", custom=[], ffprobe=None)
            self.assertEqual([v.path.name for v in items], ["a.mp4"])
            self.assertIn("vazio", rejected[0]["reason"])
