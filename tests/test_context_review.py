import copy
import json
import tempfile
import unittest
from pathlib import Path

from studio.engine import Engine
from studio.diversity import _IMAGE_RULES
from studio.review import apply_review, make_review_prompt
from studio.store import Store


def concept(number):
    return {
        "title": f"Đồ vật {number}",
        "category": "Nội thất và đời sống",
        "subject": f"Ấm trà gốm mẫu alpha{number}",
        "scene": f"Bàn gỗ trong phòng beta{number}",
        "story": f"Bộ trà được chuẩn bị cho buổi chiều gamma{number}",
        "composition": f"Ấm trà ở giữa, tách đặt lệch phía trước delta{number}",
        "palette": f"Kem ấm, lam nhạt và điểm nhấn epsilon{number}",
        "materials": f"Gốm men và gỗ zeta{number}",
        "key": f"alpha{number} teapot beta{number} room gamma{number}",
        "prompt": f"A realistic alpha{number} ceramic teapot in beta{number} room.",
    }


def keep(index, reason="Hợp lý"):
    return {"index": index, "decision": "keep", "reason": reason}


class ContextReviewTests(unittest.TestCase):
    def test_keep_preserves_all_original_fields_and_adds_audit(self):
        original = concept(1)
        before = copy.deepcopy(original)
        accepted, rejected = apply_review({"reviews": [keep(0)]}, [original], [])
        self.assertFalse(rejected)
        self.assertEqual(before, {field: accepted[0][field] for field in before})
        self.assertEqual({"decision": "keep", "reason": "Hợp lý"}, accepted[0]["context_review"])

    def test_revise_is_revalidated_and_reject_is_audited_in_feedback(self):
        revised = concept(9)
        accepted, rejected = apply_review({"reviews": [
            {"index": 0, "decision": "revise", "reason": "Làm cảnh rõ hơn", "revised": revised},
            {"index": 1, "decision": "reject", "reason": "Vật thể không có thật"},
        ]}, [concept(1), concept(2)], [])
        self.assertEqual(1, len(accepted))
        self.assertEqual("revise", accepted[0]["context_review"]["decision"])
        self.assertEqual("Đồ vật 1", accepted[0]["context_review"]["original"]["title"])
        self.assertIn("CURRENT_CONTENT_DIRECTION", accepted[0]["prompt"])
        self.assertIn("Vật thể không có thật", rejected[0])

    def test_revised_concept_cannot_duplicate_a_peer(self):
        duplicate = concept(2)
        accepted, rejected = apply_review({"reviews": [
            {"index": 0, "decision": "revise", "reason": "Sửa", "revised": duplicate},
            keep(1),
        ]}, [concept(1), concept(2)], [])
        self.assertEqual(["Đồ vật 2"], [item["title"] for item in accepted])
        self.assertTrue(any("gần trùng" in reason for reason in rejected))

    def test_malformed_duplicate_and_missing_indices_fail_closed(self):
        cases = [
            "not json",
            {"reviews": [keep(0), keep(0)]},
            {"reviews": [keep(0)]},
            {"reviews": [{"index": 0, "decision": "revise", "reason": "Sửa"}, keep(1)]},
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    apply_review(raw, [concept(1), concept(2)], [])

    def test_review_prompt_is_batched_and_strips_repeated_policy(self):
        item = concept(1)
        item["prompt"] += f" {_IMAGE_RULES} [CURRENT_CONTENT_DIRECTION] repeated direction [/CURRENT_CONTENT_DIRECTION]"
        prompt = make_review_prompt([item])
        self.assertNotIn(_IMAGE_RULES, prompt)
        self.assertNotIn("repeated direction", prompt)
        self.assertIn("CONTEXT CẦN DUYỆT", prompt)
        self.assertIn("prompt bắt buộc viết bằng tiếng Anh", prompt)

    def test_original_prompt_that_starts_with_portrait_spec_survives_compaction(self):
        item = concept(1)
        item["prompt"] = "Portrait 600x900 pixels, 2:3 aspect ratio. A real ceramic teapot on a stable table."
        prompt = make_review_prompt([item])
        self.assertIn(item["prompt"], prompt)

    def test_provider_review_error_blocks_before_store_or_generation(self):
        class Provider:
            def __init__(self):
                self.generated = 0

            def status(self):
                return {"ready": True, "text_ready": True, "message": "ready"}

            def plan(self, prompt):
                return {"concepts": [concept(1)]}

            def review(self, prompt):
                raise RuntimeError("review unavailable")

            def generate(self, prompt, directory):
                self.generated += 1
                raise AssertionError("must not generate")

        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary))
            provider = Provider()
            engine = Engine(store, lambda: provider)
            batch = store.create(1)
            engine.start(batch["id"])
            engine.thread.join(5)
            self.assertEqual("blocked", store.batch(batch["id"])["status"])
            self.assertEqual(0, store.batch(batch["id"])["planned"])
            self.assertEqual(0, provider.generated)

    def test_malformed_provider_review_blocks_before_store_or_generation(self):
        class Provider:
            generated = 0

            def status(self):
                return {"ready": True, "text_ready": True, "message": "ready"}

            def plan(self, prompt):
                return {"concepts": [concept(1)]}

            def review(self, prompt):
                return {"reviews": []}

            def generate(self, prompt, directory):
                self.generated += 1
                raise AssertionError("must not generate")

        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary))
            provider = Provider()
            engine = Engine(store, lambda: provider)
            batch = store.create(1)
            engine.start(batch["id"])
            engine.thread.join(5)
            self.assertEqual("blocked", store.batch(batch["id"])["status"])
            self.assertEqual(0, store.batch(batch["id"])["planned"])
            self.assertEqual(0, provider.generated)


if __name__ == "__main__":
    unittest.main()
