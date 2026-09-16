"""First-run configuration must be reproducible without the author's private state."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from writing_memory.rsih_setup import setup, diagnose, locate_binary
from writing_memory.util import read_json


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'state'
        self.binary = Path(self.tmp.name) / 'rsih'
        self.binary.write_text('#!/bin/sh\nexit 0\n')
        self.binary.chmod(0o700)
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.which = patch('writing_memory.rsih_setup.shutil.which', return_value='/usr/bin/git')
        self.which.start()
        self.addCleanup(self.which.stop)
        self.run = patch('writing_memory.rsih_setup.subprocess.run', return_value=
                         subprocess.CompletedProcess([], 0, '0.1.0', ''))
        self.mock_run = self.run.start()
        self.addCleanup(self.run.stop)

    def test_fresh_install_and_diagnostic_do_not_expose_key(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'fixture-private-token'}):
            result = setup(self.state, self.binary)
        self.assertTrue(result['credential_ready'])
        self.assertFalse(result['model_request_made'])
        self.assertEqual(locate_binary(self.state), self.binary.resolve())
        self.assertEqual((self.state / 'credentials.json').stat().st_mode & 0o777, 0o600)
        self.assertTrue(diagnose(self.state)['ready'])
        self.assertNotIn('fixture-private-token', json.dumps(result) + json.dumps(diagnose(self.state)))
        self.assertNotIn('fixture-private-token', (self.state / 'agent/models.json').read_text())
        self.assertTrue((self.state / 'genomes/writing-demo/contracts/instructions.dev.md').exists())
        self.assertEqual(self.mock_run.call_count, 2)

    def test_repeat_preserves_personal_rules_provider_and_key(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'original-fixture'}):
            setup(self.state, self.binary)
        rule = self.state / 'genomes/writing-demo/components/instructions.json'
        rule.write_text('{"personal":"keep"}')
        models = self.state / 'agent/models.json'
        config = read_json(models)
        config['providers']['deepseek-study']['baseUrl'] = 'https://example.invalid'
        models.write_text(json.dumps(config))
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'new-fixture'}):
            setup(self.state)
        self.assertEqual(read_json(self.state / 'credentials.json')['DEEPSEEK_API_KEY'], 'original-fixture')
        self.assertEqual(rule.read_text(), '{"personal":"keep"}')
        self.assertEqual(read_json(models), config)

    def test_replace_is_explicit_and_skip_key_is_offline(self):
        setup(self.state, self.binary, skip_key=True)
        self.assertFalse((self.state / 'credentials.json').exists())
        self.assertFalse(diagnose(self.state)['ready'])
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'replacement-fixture'}):
            setup(self.state, replace_key=True)
        self.assertTrue(diagnose(self.state)['ready'])
        with self.assertRaises(ValueError):
            setup(self.state, skip_key=True, replace_key=True)

    def test_missing_binary_does_not_create_state(self):
        with self.assertRaisesRegex(ValueError, '找不到'):
            setup(self.state, Path(self.tmp.name) / 'missing')
        self.assertFalse(self.state.exists())

    def test_noninteractive_input_does_not_echo_secret(self):
        with patch('sys.stdin.isatty', return_value=False), patch('getpass.getpass') as prompt:
            with self.assertRaisesRegex(ValueError, '交互式终端'):
                setup(self.state, self.binary)
            prompt.assert_not_called()
        # A partially completed setup can be resumed.
        setup(self.state, self.binary, skip_key=True)
        self.assertFalse((self.state / '.setup-in-progress').exists())

    def test_failed_genome_validation_does_not_save_credentials(self):
        self.mock_run.side_effect = [subprocess.CompletedProcess([], 0, 'version', ''),
                                     subprocess.CompletedProcess([], 1, '', 'invalid genome')]
        with self.assertRaisesRegex(ValueError, 'Genome'):
            setup(self.state, self.binary)
        self.assertFalse((self.state / 'credentials.json').exists())

    def test_refuses_unknown_nonempty_directory(self):
        self.state.mkdir()
        (self.state / 'unrelated.txt').write_text('keep')
        with self.assertRaisesRegex(ValueError, '已有其他内容'):
            setup(self.state, self.binary)
        self.assertEqual((self.state / 'unrelated.txt').read_text(), 'keep')
