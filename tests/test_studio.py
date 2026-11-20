from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from post_renderer import crop_aspect, valid_webcam, write_words
from studio import local_original, read_profile, save_profile


class SetupTest(unittest.TestCase):
    def test_profile_and_import_use_isolated_unicode_media_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photo = root / "foto.png"
            Image.new("RGB", (90, 130), "purple").save(photo)
            media = root / "Vídeos de ação"
            state = root / "local"
            cfg = save_profile(state, name="Testé", handle="@teste", avatar=photo,
                               media_dir=media, engine=root / "engine")
            self.assertEqual(read_profile(state), cfg)
            self.assertEqual(cfg["handle"], "@teste")
            self.assertTrue(Path(cfg["avatar"]).is_file())
            src = root / "Vídeo original.mp4"
            src.write_bytes(b"synthetic")
            dst = local_original(src, cfg)
            self.assertEqual(dst.read_bytes(), src.read_bytes())
            self.assertEqual(dst.parent, media)
            with self.assertRaises(FileExistsError):
                local_original(src, cfg)

    def test_languages_and_bounds(self):
        self.assertEqual(crop_aspect((0, 0, 1920, 1080), 1), (420, 0, 1080, 1080))
        self.assertIsNone(valid_webcam(None, 1920, 1080))
        with self.assertRaises(ValueError):
            valid_webcam("1900,0,90,90", 1920, 1080)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "words.json"
            output = folder / "captions.ass"
            source.write_text(
                '[{"text":"Olá","start":0.1,"end":0.4,"language":"pt"},'
                '{"text":"Hello","start":0.6,"end":0.9,"language":"en"}]', encoding="utf-8"
            )
            write_words(output, source, 1)
            captions = output.read_text(encoding="utf-8")
            self.assertIn("OLÁ", captions)
            self.assertNotIn("HELLO", captions)
