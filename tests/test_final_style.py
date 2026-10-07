import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from studio import final
from studio.diversity import CATEGORIES, image_prompt, CATEGORY_TARGETS, category_plan, make_planning_prompt, validate_concepts
from studio.engine import Engine
from studio.store import Store
from studio.style import METRICS, STYLE_PROFILE_PATH, load_profile, style_drift, style_stats
from tests.test_diversity import concept
from tests.test_pipeline import FakeProvider

ROOT = Path(__file__).resolve().parent.parent


def band(p5, p25, median, p75, p95):
    return {'p5': p5, 'p25': p25, 'median': median, 'p75': p75, 'p95': p95}


PROFILE = {'warn_score': 0.5, 'metrics': {
    'saturation': band(80, 110, 130, 150, 178),
    'brightness': band(140, 163, 179, 194, 213),
    'warmth': band(-12, 33, 59, 83, 109),
    'colorfulness': band(55, 73, 86, 101, 123),
    'contrast': band(40, 47, 52, 56, 63),
    'detail': band(28, 34, 39, 44, 50),
}}


class StyleProfileTests(unittest.TestCase):
    def test_committed_profile_and_index_describe_final(self):
        profile = load_profile(STYLE_PROFILE_PATH)
        self.assertEqual(set(METRICS), set(profile['metrics']))
        items = final.load_index(ROOT / 'final-index.json')
        self.assertEqual(profile['counts']['total'], len(items))
        self.assertTrue(all(item['theme'] in final.THEMES and item['caption'] for item in items))
        self.assertTrue(all(set(METRICS) <= set(item['stats']) for item in items))

    def test_typical_final_image_passes_and_muted_image_warns(self):
        typical = {key: values['median'] for key, values in PROFILE['metrics'].items()}
        self.assertIsNone(style_drift(typical, PROFILE))
        muted = dict(typical, saturation=60, warmth=-30, colorfulness=40)
        drift = style_drift(muted, PROFILE)
        self.assertIn('nhạt màu hơn', drift['message'])
        self.assertEqual({'saturation', 'warmth', 'colorfulness'}, {issue['metric'] for issue in drift['issues']})

    def test_slightly_outside_one_band_is_tolerated(self):
        typical = {key: values['median'] for key, values in PROFILE['metrics'].items()}
        self.assertIsNone(style_drift(dict(typical, saturation=75), PROFILE))

    def test_stats_separate_warm_vivid_from_grey(self):
        warm = style_stats(Image.new('RGB', (60, 90), (240, 150, 40)))
        grey = style_stats(Image.new('RGB', (60, 90), (128, 128, 128)))
        self.assertGreater(warm['saturation'], grey['saturation'])
        self.assertGreater(warm['warmth'], grey['warmth'])
        self.assertEqual(0, grey['saturation'])


class FinalPlanningTests(unittest.TestCase):
    def test_categories_follow_final_themes(self):
        self.assertEqual(len(final.THEMES), len(CATEGORIES))
        self.assertEqual(100, sum(CATEGORY_TARGETS.values()))
        plan = category_plan(12, {})
        self.assertEqual(12, sum(plan.values()))
        self.assertEqual(max(plan.values()), plan['Ẩm thực và bàn ăn'])
        crowded = category_plan(5, {'Ẩm thực và bàn ăn': 50})
        self.assertEqual(5, sum(crowded.values()))
        self.assertNotIn('Ẩm thực và bàn ăn', crowded)

    def test_prompt_shows_targets_plan_and_same_theme_examples(self):
        prompt = make_planning_prompt(6, [])
        self.assertIn('mục tiêu 22%', prompt)
        self.assertIn('GỢI Ý PHÂN BỔ 6 CONCEPT', prompt)
        self.assertIn('ẢNH MẪU FINAL CÙNG NHÓM', prompt)
        self.assertIn('nắng ấm', prompt)

    def test_title_repeating_a_final_caption_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            index = Path(temp) / 'final-index.json'
            index.write_text(json.dumps({'items': [
                {'path': 'Chap9/1-4x4.jpg', 'theme': 'food', 'caption': 'bánh flan caramel trên khăn vàng'},
            ]}), encoding='utf-8')
            with patch('studio.final.FINAL_INDEX_PATH', index):
                copied = concept(title='Bánh flan caramel trên khăn vàng buổi sáng', key='caramel flan breakfast')
                accepted, errors = validate_concepts({'concepts': [copied, concept()]}, [], 2)
        self.assertEqual(['Xưởng đóng đàn ven sông'], [item['title'] for item in accepted])
        self.assertTrue(any('ảnh mẫu Final' in error and 'Chap9/1-4x4.jpg' in error for error in errors))


class LegacyPromptTests(unittest.TestCase):
    def test_context_planned_with_old_rules_gets_only_final_style(self):
        old_rules = (
            'Portrait 600x900 pixels, 2:3 aspect ratio. Bright, crisp, realistic photography or photorealistic '
            'illustration. High-key natural daylight. Use one clear focal subject, a few large readable shape groups, '
            'calm breathing room, clear depth, and a restrained number of puzzle-friendly visual anchors. '
            'Avoid clutter and carpets of tiny repeated details.'
        )
        prompt = image_prompt({'prompt': 'A bakery counter with pastries. ' + old_rules})
        self.assertIn('A bakery counter with pastries', prompt)
        self.assertNotIn('calm breathing room', prompt)
        self.assertNotIn('realistic photography', prompt)
        self.assertEqual(1, prompt.count('Polished, high-detail illustration'))


class FinalEngineTests(unittest.TestCase):
    def test_generation_compares_pixels_with_final_and_warns_on_style(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            final_root = temp / 'Final'
            (final_root / 'Chap1').mkdir(parents=True)
            # Same flat colour as the first FakeProvider image, so the pixel check must match it.
            Image.new('RGB', (600, 900), (21, 90, 160)).save(final_root / 'Chap1' / '1-4x4.jpg')
            index = temp / 'final-index.json'
            index.write_text(json.dumps({'items': [
                {'path': 'Chap1/1-4x4.jpg', 'theme': 'facade', 'caption': 'tường xanh'},
            ]}), encoding='utf-8')
            store = Store(temp / 'data')
            store.save_settings({'concurrency': 1})
            batch = store.create(1)
            store.add_concepts(batch['id'], [concept()])
            with patch('studio.final.FINAL_INDEX_PATH', index):
                engine = Engine(store, FakeProvider, final_root=final_root)
                engine.start(batch['id']); engine.thread.join(10)
            result = store.items(batch['id'])[0]
            self.assertEqual('completed', result['status'])
            self.assertIn('Final/Chap1/1-4x4.jpg', result['similarity'])
            self.assertIn('Lệch phong cách Final', result['style_warning'])
            self.assertTrue((temp / 'data' / 'final-fingerprints.json').is_file())
            context = json.loads((Path(result['image_path']).parent / 'context.json').read_text())
            self.assertIn('saturation', context['style_stats'])


if __name__ == '__main__':
    unittest.main()
