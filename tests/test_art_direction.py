import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.diversity import image_prompt, make_planning_prompt
from studio.store import Store
from tests.test_diversity import concept
from tests.test_pipeline import FakeProvider
from studio.engine import Engine


class ArtDirectionTests(unittest.TestCase):
    def test_guidance_updates_both_planner_and_previously_planned_image(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'direction.json'
            with patch('studio.diversity.ART_DIRECTION_PATH', path):
                path.write_text(json.dumps({'planning_rules': ['Ưu tiên cảnh sạch.'], 'image_rules': ['No exposed soil.']}))
                old = image_prompt({'prompt': 'A rural scene with a muddy bank.'})
                path.write_text(json.dumps({'planning_rules': ['Không có bùn đất; màu tươi tự nhiên.'], 'image_rules': ['No mud or dull colors; keep rural scenes diverse.']}))
                updated = image_prompt({'prompt': old})
                self.assertIn('Không có bùn đất', make_planning_prompt(3, []))
                self.assertNotIn('No exposed soil.', updated)
                self.assertIn('No mud or dull colors', updated)
                self.assertEqual(1, updated.count('[CURRENT_CONTENT_DIRECTION]'))
                self.assertEqual(updated, image_prompt({'prompt': updated}))
                self.assertIn('takes precedence', updated)

    def test_invalid_policy_stops_instead_of_silently_ignoring_feedback(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'direction.json'
            path.write_text('{bad json')
            with patch('studio.diversity.ART_DIRECTION_PATH', path):
                with self.assertRaisesRegex(ValueError, 'art-direction.json'):
                    image_prompt(concept())
                with self.assertRaisesRegex(ValueError, 'art-direction.json'):
                    make_planning_prompt(1, [])

    def test_generation_and_export_metadata_keep_actual_prompt_and_original_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            store.save_settings({'concurrency': 1})
            batch = store.create(1)
            planned = concept(prompt='Original rural scene with muddy water.')
            store.add_concepts(batch['id'], [planned])
            provider = FakeProvider()
            engine = Engine(store, lambda: provider)
            engine.start(batch['id']); engine.thread.join(10)
            self.assertFalse(engine.thread.is_alive())
            result = store.items(batch['id'])[0]
            self.assertEqual('completed', result['status'])
            self.assertEqual(provider.generate_calls[0], result['prompt'])
            self.assertEqual(planned['prompt'], result['planned_prompt'])
            self.assertIn('no mud', result['prompt'].lower())
            metadata = json.loads((Path(result['image_path']).parent / 'context.json').read_text())
            self.assertEqual(result['prompt'], metadata['prompt'])


if __name__ == '__main__':
    unittest.main()
