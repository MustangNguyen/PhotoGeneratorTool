import json
import random
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from studio import axes
from studio.diversity import CATEGORIES, make_planning_prompt, pick_seeds, validate_concepts
from tests.test_diversity import concept


def catalogue(**changes):
    value = {
        'version': 1,
        'per_concept': {'min': 1, 'max': 2},
        'axes': [
            {'id': 'season', 'label': 'Mùa', 'values': ['xuân', 'hạ', 'thu', 'đông']},
            {'id': 'pet', 'label': 'Thú', 'exclude_categories': ['animal'], 'values': ['mèo', 'chó']},
        ],
    }
    value.update(changes)
    return value


class AxisTests(unittest.TestCase):
    def with_catalogue(self, data):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'axes.json'
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        patcher = patch('studio.axes.AXES_PATH', path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_repo_catalogue_is_valid_and_rich(self):
        loaded = axes.load_axes()
        self.assertGreaterEqual(len(loaded['axes']), 10)
        self.assertLessEqual(loaded['min'], loaded['max'])

    def test_each_slot_gets_a_subset_of_axes_within_bounds(self):
        self.with_catalogue(catalogue())
        result = axes.assign_axes([CATEGORIES[0]] * 50, [], random.Random(3))
        sizes = Counter(len(item) for item in result)
        self.assertEqual({1, 2}, set(sizes))

    def test_least_used_values_are_chosen_first(self):
        self.with_catalogue(catalogue(per_concept={'min': 1, 'max': 1}, axes=[
            {'id': 'season', 'label': 'Mùa', 'values': ['xuân', 'hạ', 'thu', 'đông']},
        ]))
        history = [{'axes': {'season': 'xuân'}}] * 3 + [{'axes': {'season': 'hạ'}}] * 2 + [{'axes': {'season': 'thu'}}]
        first = axes.assign_axes([CATEGORIES[0]] * 4, history, random.Random(1))
        self.assertEqual('đông', first[0]['season'])
        self.assertEqual(Counter({'đông': 2, 'thu': 1}), Counter(item['season'] for item in first[:3]))
        self.assertNotEqual('xuân', first[3]['season'])

    def test_category_exclusion_is_respected(self):
        self.with_catalogue(catalogue(per_concept={'min': 2, 'max': 2}))
        animal = 'Động vật dễ thương'
        result = axes.assign_axes([animal] * 20, [], random.Random(5))
        self.assertTrue(all('pet' not in item for item in result))

    def test_invalid_catalogue_fails_loudly(self):
        self.with_catalogue(catalogue(axes=[{'id': 'x', 'label': 'X', 'categories': ['moon'], 'values': ['a']}]))
        with self.assertRaises(ValueError):
            axes.load_axes()

    def test_exclude_rule_never_pairs_values(self):
        self.with_catalogue(catalogue(per_concept={'min': 2, 'max': 2}, rules=[
            {'type': 'exclude', 'if': {'axis': 'season', 'values': ['đông']}, 'then': {'axis': 'pet', 'values': ['mèo']}},
        ]))
        result = axes.assign_axes([CATEGORIES[0]] * 200, [], random.Random(7))
        self.assertFalse(any(item.get('season') == 'đông' and item.get('pet') == 'mèo' for item in result))
        self.assertTrue(any(item.get('season') == 'đông' for item in result))

    def test_require_rule_limits_partner_values(self):
        self.with_catalogue(catalogue(per_concept={'min': 2, 'max': 2}, rules=[
            {'type': 'require', 'if': {'axis': 'pet', 'values': ['chó']}, 'then': {'axis': 'season', 'values': ['hạ']}},
        ]))
        result = axes.assign_axes([CATEGORIES[0]] * 200, [], random.Random(8))
        self.assertTrue(all(item.get('season') == 'hạ' for item in result if item.get('pet') == 'chó' and 'season' in item))

    def test_category_rule_blocks_value_for_theme(self):
        self.with_catalogue(catalogue(per_concept={'min': 1, 'max': 1}, axes=[
            {'id': 'season', 'label': 'Mùa', 'values': ['xuân', 'đông']},
        ], rules=[
            {'type': 'exclude', 'if': {'axis': 'season', 'values': ['đông']}, 'then': {'category': ['coast']}},
        ]))
        result = axes.assign_axes(['Biển và nghỉ dưỡng ven biển'] * 30, [], random.Random(9))
        self.assertEqual({'xuân'}, {item['season'] for item in result})

    def test_rule_with_unknown_value_fails_loudly(self):
        self.with_catalogue(catalogue(rules=[
            {'type': 'exclude', 'if': {'axis': 'season', 'values': ['mưa']}, 'then': {'axis': 'pet'}},
        ]))
        with self.assertRaises(ValueError):
            axes.load_axes()

    def test_repo_rules_hold_for_many_random_slots(self):
        catalogue_rules = axes.load_axes()['rules']
        rng = random.Random(11)
        slots = [rng.choice(CATEGORIES) for _ in range(3000)]
        for category, item in zip(slots, axes.assign_axes(slots, [], rng)):
            self.assertTrue(axes.compatible(axes.final.CATEGORY_TO_THEME[category], item, catalogue_rules), (category, item))

    def test_axes_flow_from_slot_to_prompt_and_saved_concept(self):
        seeds = pick_seeds(6, [], rng=random.Random(2))
        self.assertTrue(all('axes' in seed for seed in seeds))
        prompt = make_planning_prompt(6, [], None, seeds)
        with_axes = next(seed for seed in seeds if seed['axes'])
        self.assertIn(axes.describe(with_axes['axes']), prompt)
        item = concept(main_subject='cello', seed_id=with_axes['id'])
        accepted, errors = validate_concepts({'concepts': [item]}, [], 1, seeds)
        self.assertEqual(with_axes['axes'], accepted[0]['axes'], errors)

    def test_required_axis_follows_group_shares(self):
        self.with_catalogue(catalogue(per_concept={'min': 0, 'max': 0}, axes=[
            {'id': 'shot', 'label': 'Kiểu ảnh', 'required': True, 'values': [
                {'value': 'gần', 'shares': {'food': 3}},
                {'value': 'xa', 'shares': {'food': 1, 'coast': 1}},
                {'value': 'phòng', 'shares': {'interior': 1}},
            ]},
        ]))
        food = axes.assign_axes(['Ẩm thực và bàn ăn'] * 40, [], random.Random(1))
        self.assertEqual(Counter({'gần': 30, 'xa': 10}), Counter(item['shot'] for item in food))
        coast = axes.assign_axes(['Biển và nghỉ dưỡng ven biển'] * 5, [], random.Random(1))
        self.assertEqual({'xa'}, {item['shot'] for item in coast})

    def test_repo_shot_types_keep_distant_views_rare(self):
        rng = random.Random(6)
        slots = [rng.choice(CATEGORIES) for _ in range(2000)]
        result = axes.assign_axes(slots, [], rng)
        self.assertTrue(all('shot' in item for item in result))
        vista = sum(axes.is_vista_shot(item['shot']) for item in result) / len(result)
        self.assertLess(vista, 0.2)

    def test_seed_caption_agrees_with_shot(self):
        seeds = pick_seeds(40, [], rng=random.Random(3))
        vista = __import__('studio.diversity', fromlist=['_VISTA_CAPTION'])._VISTA_CAPTION
        agree = sum(bool(vista.search(seed['caption'].lower())) == axes.is_vista_shot(seed['axes']['shot']) for seed in seeds)
        self.assertGreaterEqual(agree, 38)

    def test_slots_exist_without_final_index(self):
        with patch('studio.final.load_index', return_value=()):
            seeds = pick_seeds(4, [], rng=random.Random(1))
        self.assertEqual(4, len(seeds))
        self.assertTrue(all(seed['id'].startswith('S') and not seed['path'] for seed in seeds))


if __name__ == '__main__':
    unittest.main()
