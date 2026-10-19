import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from studio import final, themes
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
    def test_categories_follow_new_themes(self):
        self.assertEqual(12, len(themes.THEMES))
        self.assertEqual([label for label, _ in themes.THEMES.values()], CATEGORIES)
        self.assertEqual(100, sum(CATEGORY_TARGETS.values()))
        hobby = themes.THEMES['hobby'][0]
        plan = category_plan(12, {})
        self.assertEqual(12, sum(plan.values()))
        self.assertEqual(max(plan.values()), plan[hobby])
        for count in (1, 5, 20, 37, 100):
            self.assertEqual(count, sum(category_plan(count, {}).values()))
        crowded = category_plan(5, {hobby: 50})
        self.assertEqual(5, sum(crowded.values()))
        self.assertNotIn(hobby, crowded)
        self.assertFalse(set(category_plan(20, {})) - set(CATEGORIES))

    def test_prompt_shows_new_targets_and_no_final_seeding(self):
        prompt = make_planning_prompt(6, [])
        self.assertIn('mục tiêu 10%', prompt)
        self.assertIn('GỢI Ý PHÂN BỔ 6 CONCEPT', prompt)
        self.assertNotIn('ẢNH MẪU FINAL', prompt)
        self.assertNotIn('ảnh mẫu', prompt)
        self.assertNotIn('tỷ lệ chủ đề của Final', prompt)
        self.assertIn('NỘI DUNG PHẢI MỚI', prompt)
        self.assertEqual(6, len(re.findall(r'^- S\d+ \[', prompt, re.MULTILINE)))
        self.assertIn('nắng ấm', prompt)

    def test_seeds_follow_category_plan_without_final_paths(self):
        from studio.diversity import pick_seeds
        seeds = pick_seeds(12, [])
        self.assertEqual([f'S{n}' for n in range(1, 13)], [seed['id'] for seed in seeds])
        plan = category_plan(12, {})
        for category, number in plan.items():
            self.assertEqual(number, sum(seed['category'] == category for seed in seeds))
        self.assertTrue(all('caption' not in seed and 'path' not in seed for seed in seeds))

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


    def test_theme_without_catalogue_subjects_still_gets_a_slot(self):
        from studio.diversity import pick_seeds
        with tempfile.TemporaryDirectory() as temp:
            catalogue = Path(temp) / 'subject-catalogue.json'
            catalogue.write_text(json.dumps({'version': 1, 'themes': {'music': [{'subject': 'harp', 'vi': 'đàn hạc'}]}}), encoding='utf-8')
            with patch('studio.diversity.CATALOGUE_PATH', catalogue), \
                    patch('studio.diversity.category_plan', return_value={themes.THEMES['wildlife'][0]: 1}):
                seeds = pick_seeds(1, [])
                prompt = make_planning_prompt(1, [], None, seeds)
        self.assertEqual('S1', seeds[0]['id'])
        self.assertNotIn('main_subject', seeds[0])
        self.assertIn('axes', seeds[0])
        self.assertIn('CHỦ THỂ: tự chọn trong nhóm', prompt)

    def test_catalogue_subject_drives_the_seed_and_skips_used_or_full_ones(self):
        from studio.diversity import pick_seeds
        with tempfile.TemporaryDirectory() as temp:
            catalogue = Path(temp) / 'subject-catalogue.json'
            catalogue.write_text(json.dumps({'version': 1, 'themes': {'wildlife': [
                {'subject': 'guinea pig', 'vi': 'chuột lang', 'family': 'small_pet'},
                {'subject': 'okapi', 'vi': 'hươu okapi'},
                {'subject': 'meerkat', 'vi': 'chồn đất'},
            ]}}), encoding='utf-8')
            # small_pet is full and okapi was used recently, so only meerkat is open.
            # Family quotas count only the current content era.
            history = [concept(main_subject='hamster', subject_family='small_pet', title='Chuột hamster', era=2),
                       concept(main_subject='bunny', subject_family='small_pet', title='Thỏ con', era=2),
                       concept(main_subject='okapi', title='Hươu okapi')]
            with patch('studio.diversity.CATALOGUE_PATH', catalogue), \
                    patch('studio.diversity.category_plan', return_value={themes.THEMES['wildlife'][0]: 1}):
                seeds = pick_seeds(1, history)
                prompt = make_planning_prompt(1, history, None, seeds)
        self.assertEqual('meerkat', seeds[0]['main_subject'])
        self.assertTrue(seeds[0]['catalogue'])
        self.assertIn('CHỦ THỂ: meerkat (chồn đất)', prompt)
        self.assertNotIn('ảnh mẫu', prompt)

    def test_catalogue_prefers_the_least_used_subject(self):
        from studio.diversity import pick_seeds
        with tempfile.TemporaryDirectory() as temp:
            catalogue = Path(temp) / 'subject-catalogue.json'
            catalogue.write_text(json.dumps({'version': 1, 'themes': {'farm': [
                {'subject': 'haystack'}, {'subject': 'scarecrow'},
            ]}}), encoding='utf-8')
            # haystack was used once long ago (outside the recent window, under the total cap), scarecrow never.
            # Neither belongs to a subject family, so the family caps cannot decide the order.
            filler = [concept(title=f'Mục {n}', key=f'filler {n}', main_subject=f'widget{n}') for n in range(520)]
            history = [concept(main_subject='haystack', title='Đống rơm')] + filler
            with patch('studio.diversity.CATALOGUE_PATH', catalogue), \
                    patch('studio.diversity.category_plan', return_value={themes.THEMES['farm'][0]: 2}):
                seeds = pick_seeds(2, history)
        self.assertEqual(['scarecrow', 'haystack'], [seed['main_subject'] for seed in seeds])


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
