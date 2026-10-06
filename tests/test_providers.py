import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from studio.providers import CodexProvider, OpenAIProvider


class CodexProviderTests(unittest.TestCase):
    def test_context_model_and_effort_do_not_change_image_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = CodexProvider({'text_model': 'gpt-6-luna'}, root)
            provider.executable = '/mock/codex'
            for structured in (True, False):
                def spawn(args, **kwargs):
                    (root / 'response.txt').write_text('{}')
                    return mock.Mock(returncode=0)
                with mock.patch('studio.providers.subprocess.Popen', side_effect=spawn) as popen:
                    provider._exec('test', root, structured=structured)
                args = popen.call_args.args[0]
                self.assertIn('model_reasoning_effort="medium"' if structured else 'model_reasoning_effort="low"', args)
                if structured:
                    self.assertEqual('gpt-6-luna', args[args.index('--model') + 1])
                else:
                    self.assertNotIn('--model', args)

    def test_generate_does_not_accept_stale_source_from_parent_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stale = root / "source.png"
            stale.write_bytes(b"old image from an earlier attempt")
            provider = CodexProvider({}, root / "jobs")
            called_directories = []

            def fake_exec(prompt, directory, **kwargs):
                called_directories.append(Path(directory))
                return "tool claimed success but wrote no image"

            with mock.patch.object(provider, "_exec", side_effect=fake_exec):
                with self.assertRaisesRegex(RuntimeError, "chưa lưu được ảnh"):
                    provider.generate("a harmless art brief", root)

            self.assertEqual(b"old image from an earlier attempt", stale.read_bytes())
            self.assertEqual(1, len(called_directories))
            self.assertEqual(root, called_directories[0].parent)
            self.assertTrue(called_directories[0].name.startswith("attempt-"))
            self.assertFalse((called_directories[0] / "source.png").exists())

    def test_cancel_prevents_late_process_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = CodexProvider({}, temporary)
            provider.executable = '/mock/codex'
            provider.cancel()
            with mock.patch('studio.providers.subprocess.Popen') as spawn:
                with self.assertRaisesRegex(RuntimeError, 'dừng trước'):
                    provider._exec('test', temporary)
                spawn.assert_not_called()

    def test_cancel_terminates_entire_process_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = CodexProvider({}, temporary)
            process = mock.Mock(pid=43210)
            process.poll.return_value = None
            provider.process = process

            with mock.patch("studio.providers.os.killpg") as killpg:
                provider.cancel()

            killpg.assert_called_once_with(43210, signal.SIGTERM)
            process.wait.assert_called_once_with(timeout=5)

    def test_cancel_escalates_process_group_after_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = CodexProvider({}, temporary)
            process = mock.Mock(pid=43211)
            process.poll.return_value = None
            process.wait.side_effect = [subprocess.TimeoutExpired("codex", 5), None]
            provider.process = process

            with mock.patch("studio.providers.os.killpg") as killpg:
                provider.cancel()

            self.assertEqual(
                [mock.call(43211, signal.SIGTERM), mock.call(43211, signal.SIGKILL)],
                killpg.call_args_list,
            )
            self.assertEqual([mock.call(timeout=5), mock.call(timeout=5)], process.wait.call_args_list)


class OpenAIProviderTests(unittest.TestCase):
    def test_missing_image_model_is_not_ready(self):
        provider = OpenAIProvider(
            {"text_model": "gpt-test", "image_model": ""},
            "sk-test-present",
        )
        status = provider.status()
        self.assertFalse(status["ready"])
        self.assertIn("model", status["message"].lower())


if __name__ == "__main__":
    unittest.main()
