import io
import json
import unittest
from unittest import mock

import launch


class LauncherTests(unittest.TestCase):
    def test_existing_antigravity_server_is_recognized(self):
        response = io.BytesIO(json.dumps({'provider': 'antigravity', 'codex_available': True, 'antigravity_available': True}).encode())
        with mock.patch('launch.urllib.request.urlopen', return_value=response):
            self.assertTrue(launch.running())
