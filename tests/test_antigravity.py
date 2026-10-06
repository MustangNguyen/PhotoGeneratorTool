import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from studio.providers import AntigravityProvider, SCHEMA, create_provider


class AntigravityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.provider = AntigravityProvider({'text_model': 'codex-only'}, self.root)
        self.provider.executable = '/mock/agy'

    def run_cli(self, result, **kwargs):
        child = mock.Mock(returncode=0)
        def spawn(args, **options):
            options['stdout'].write(json.dumps({'event': 'result', 'result': result}) + '\n')
            return child
        with mock.patch.object(self.provider, '_check_account_mode'), mock.patch('studio.providers.subprocess.Popen', side_effect=spawn) as process:
            answer = self.provider._exec('$imagegen\na prompt', self.root, **kwargs)
        self.last_child = child
        return answer, process

    def test_stream_protocol_and_account_environment(self):
        self.provider.settings['antigravity_text_model'] = 'context-only'
        with mock.patch.dict('os.environ', {'GEMINI_API_KEY': 'secret', 'AGY_ADC_AUTH': 'true'}):
            answer, process = self.run_cli({'status': 'SUCCESS', 'response': 'done'})
        self.assertEqual('done', answer)
        args = process.call_args.args[0]
        self.assertNotIn('--model', args)
        self.assertNotIn('--dangerously-skip-permissions', args)
        self.assertIn('--sandbox', args)
        self.assertEqual('accept-edits', args[args.index('--mode') + 1])
        self.assertNotIn('GEMINI_API_KEY', process.call_args.kwargs['env'])
        payload = json.loads(self.last_child.communicate.call_args.kwargs['input'])
        self.assertEqual({'event': 'user', 'message': {'content': 'a prompt'}}, payload)
        self.assertEqual(self.root, process.call_args.kwargs['cwd'])

    def test_schema_and_separate_model(self):
        self.provider.settings['antigravity_text_model'] = 'gemini-example'
        _, process = self.run_cli({'status': 'SUCCESS', 'response': '{"concepts":[]}'}, structured=True)
        args = process.call_args.args[0]
        self.assertEqual('gemini-example', args[args.index('--model') + 1])
        self.assertEqual(SCHEMA, json.loads((self.root / 'schema.json').read_text()))

    def test_prefers_structured_output_over_concatenated_agent_messages(self):
        answer, _ = self.run_cli({
            'status': 'SUCCESS',
            'response': '{"concepts":[]}\n{"concepts":[],"toolAction":"Finish"}',
            'structured_output': {'concepts': []},
        }, structured=True)
        self.assertEqual({'concepts': []}, json.loads(answer))

    def test_error_result_even_when_exit_zero(self):
        with self.assertRaisesRegex(RuntimeError, 'chưa hoàn tất'):
            self.run_cli({'status': 'ERROR', 'response': 'no quota'})

    def test_account_guard_rejects_paid_mode_and_accepts_sparse_false(self):
        settings = self.root / '.gemini/antigravity-cli/settings.json'
        settings.parent.mkdir(parents=True)
        with mock.patch('studio.providers.Path.home', return_value=self.root), mock.patch.dict('os.environ', {}, clear=True):
            for config in ({'useG1Credits': True}, {'useG1Credits': False, 'modelProvider': 'gemini'}):
                settings.write_text(json.dumps(config))
                with self.assertRaises(RuntimeError):
                    self.provider._check_account_mode()
            settings.write_text('{}')
            self.provider._check_account_mode()
            settings.write_text('{"useG1Credits":false}')
            self.provider._check_account_mode()

    def test_cancel_prevents_launch(self):
        self.provider.cancel()
        with mock.patch.object(self.provider, '_check_account_mode'), mock.patch('studio.providers.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'dừng trước'):
                self.provider._exec('test', self.root)
            spawn.assert_not_called()

    def test_missing_image_fails_without_retry(self):
        with mock.patch.object(self.provider, '_exec', return_value='no image') as execute:
            with self.assertRaisesRegex(RuntimeError, 'ID lượt tạo ảnh'):
                self.provider.generate('a cat', self.root)
            self.assertEqual(1, execute.call_count)

    def test_factory_selects_antigravity(self):
        store = mock.Mock(root=self.root)
        store.settings.return_value = {'provider': 'antigravity'}
        self.assertIsInstance(create_provider(store), AntigravityProvider)

    def test_generated_file_is_from_current_conversation(self):
        conversation = '11111111-1111-1111-1111-111111111111'
        artifacts = self.root / '.gemini/antigravity-cli/brain' / conversation
        artifacts.mkdir(parents=True)
        (artifacts / 'generated.jpg').write_bytes(b'new raster')
        (self.root / 'source.png').write_bytes(b'old')
        def generate(prompt, directory, **kwargs):
            self.provider.last_result = {'conversation_id': conversation}
            self.assertIn('Do not run shell commands', prompt)
            return 'saved'
        with mock.patch('studio.providers.Path.home', return_value=self.root), mock.patch.object(self.provider, '_exec', side_effect=generate):
            target = self.provider.generate('a cat', self.root)
        self.assertEqual(b'new raster', target.read_bytes())
        self.assertEqual('.jpg', target.suffix)
        self.assertEqual(self.root, target.parent.parent)
        self.assertEqual(b'old', (self.root / 'source.png').read_bytes())

    def test_rejects_unsafe_conversation_and_ambiguous_images(self):
        with self.assertRaisesRegex(RuntimeError, 'ID lượt tạo'):
            self.provider._collect_image({'conversation_id': '../old'}, self.root)
        conversation = '22222222-2222-2222-2222-222222222222'
        artifacts = self.root / '.gemini/antigravity-cli/brain' / conversation
        artifacts.mkdir(parents=True)
        (artifacts / 'one.jpg').write_bytes(b'one')
        (artifacts / 'two.png').write_bytes(b'two')
        with mock.patch('studio.providers.Path.home', return_value=self.root):
            with self.assertRaisesRegex(RuntimeError, 'có 2 ảnh'):
                self.provider._collect_image({'conversation_id': conversation}, self.root)

    def test_review_uses_plain_object_schema_without_mutating_shared_schema(self):
        from studio.review import REVIEW_SCHEMA
        with mock.patch.object(self.provider, '_exec', return_value='{"reviews":[]}') as execute:
            self.provider.review('one context')
        schema = execute.call_args.kwargs['output_schema']
        reviews = schema['properties']['reviews']
        self.assertEqual(1, reviews['minItems'])
        self.assertNotIn('anyOf', reviews['items'])
        self.assertEqual(['keep', 'reject', 'revise'], reviews['items']['properties']['decision']['enum'])
        self.assertIn('anyOf', REVIEW_SCHEMA['properties']['reviews']['items'])

    def test_timeout_cancels_process(self):
        process = mock.Mock()
        process.communicate.side_effect = subprocess.TimeoutExpired('agy', 1)
        with mock.patch.object(self.provider, '_check_account_mode'), mock.patch('studio.providers.subprocess.Popen', return_value=process), mock.patch.object(self.provider, 'cancel') as cancel:
            with self.assertRaisesRegex(RuntimeError, 'quá thời gian'):
                self.provider._exec('test', self.root, timeout=1)
            cancel.assert_called_once()
            self.assertIsNone(self.provider.process)
