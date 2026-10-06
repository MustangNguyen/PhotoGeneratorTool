import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import studio.diversity as diversity


def _catalogue(*motifs):
    return {"version": 1, "motifs": list(motifs)}


def _motif(label="ghế bên cửa sổ", groups=None):
    return {
        "id": "chair-window",
        "label": label,
        "evidence": ["Final/example.jpg"],
        "groups": groups
        or [
            {"fields": ["subject"], "phrases": ["ghế mây", "rattan chair"]},
            {"fields": ["scene", "composition"], "phrases": ["bên cửa sổ", "by the window"]},
        ],
    }


class SampleMotifTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "content-motifs.json"
        self.path.write_text(json.dumps(_catalogue(_motif()), ensure_ascii=False), encoding="utf-8")
        self.path_patch = patch.object(diversity, "CONTENT_MOTIFS_PATH", self.path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.temp.cleanup()

    def test_all_groups_are_required_and_each_group_uses_or_phrases(self):
        self.assertIn(
            "ghế bên cửa sổ",
            diversity._motifs({"subject": "Một rattan chair", "scene": "Quiet room by the window"}),
        )
        self.assertNotIn(
            "ghế bên cửa sổ",
            diversity._motifs({"subject": "Một ghế mây", "scene": "Phòng không có ô cửa"}),
        )

    def test_phrase_in_background_field_does_not_satisfy_subject_group(self):
        self.path.write_text(
            json.dumps(
                _catalogue(
                    _motif(
                        label="kính trên sân thượng",
                        groups=[
                            {"fields": ["subject"], "phrases": ["telescope"]},
                            {"fields": ["scene"], "phrases": ["rooftop"]},
                        ],
                    )
                )
            ),
            encoding="utf-8",
        )
        labels = diversity._motifs(
            {"subject": "A ceramic teapot", "scene": "A rooftop with a telescope far in the background"}
        )
        self.assertNotIn("kính trên sân thượng", labels)

    def test_english_and_vietnamese_phrase_variants_match(self):
        english = diversity._motifs({"subject": "A rattan chair", "scene": "Reading nook by the window"})
        vietnamese = diversity._motifs({"subject": "Một ghế mây", "scene": "Góc đọc bên cửa sổ"})
        self.assertIn("ghế bên cửa sổ", english)
        self.assertIn("ghế bên cửa sổ", vietnamese)

    def test_malformed_catalogue_raises_clear_value_error(self):
        broken = _motif(groups=[{"fields": ["prompt"], "phrases": ["chair"]}])
        self.path.write_text(json.dumps(_catalogue(broken)), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fields không được phép"):
            diversity._motifs({"prompt": "chair"})

    def test_same_size_file_change_is_hot_reloaded(self):
        first = _catalogue(
            _motif(label="motif one", groups=[{"fields": ["subject"], "phrases": ["alpha"]}])
        )
        second = _catalogue(
            _motif(label="motif two", groups=[{"fields": ["subject"], "phrases": ["alpha"]}])
        )
        first_text = json.dumps(first, separators=(",", ":"))
        second_text = json.dumps(second, separators=(",", ":"))
        self.assertEqual(len(first_text), len(second_text))
        self.path.write_text(first_text, encoding="utf-8")
        original_stat = self.path.stat()
        self.assertIn("motif one", diversity._motifs({"subject": "alpha"}))

        self.path.write_text(second_text, encoding="utf-8")
        os.utime(self.path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 1_000_000))
        self.assertEqual(original_stat.st_size, self.path.stat().st_size)
        labels = diversity._motifs({"subject": "alpha"})
        self.assertIn("motif two", labels)
        self.assertNotIn("motif one", labels)

    def test_evidence_does_not_count_as_used_without_history(self):
        self.assertEqual("- (chưa nhận diện mô-típ mạnh nào)", diversity._motif_history_summary([]))
        self.assertNotIn("ghế bên cửa sổ", diversity._motifs({"subject": "unrelated object"}))


if __name__ == "__main__":
    unittest.main()
