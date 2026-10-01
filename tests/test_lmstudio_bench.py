"""Ciclo gerenciado simulado; nunca inicia LM Studio real."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from evals import lmstudio_bench as managed
from evals import model_bench as bench


class TestManaged(unittest.TestCase):
    def test_inventory_and_invalid_keys(self):
        self.assertEqual(managed.inventory('[{"path":"vendor/a.gguf"}]')[0][0],
                         'vendor/a.gguf')
        for raw in ('{}', '[{"path":"--all"}]', '[{}]'):
            with self.assertRaises(ValueError):
                managed.inventory(raw)

    def test_dispatch_and_manual_mode_preserved(self):
        with patch.object(managed, 'main', return_value=42) as run:
            self.assertEqual(bench.main(['--lmstudio', '--list']), 42)
            run.assert_called_once_with(['--list'])

    def simulate(self, failure=False, interrupt=False):
        commands = []

        def command(self, *args):
            commands.append(args)
            if args[0] == 'ls':
                return json.dumps([{'path': 'minicpm-a'}, {'path': 'minicpm-b'},
                                   {'path': 'other'}])
            if failure and args[:2] == ('load', 'minicpm-a') and '--estimate-only' not in args:
                raise ValueError('out of memory')
            return '[]'

        def run(argv):
            if interrupt:
                return 130
            directory = Path(argv[argv.index('--out')+1])
            directory.mkdir()
            (directory / 'summary.json').write_text(json.dumps({'results': []}))
            return 0

        with tempfile.TemporaryDirectory() as temp, \
                patch.object(managed, 'find_lms', return_value='fake'), \
                patch.object(managed, 'ensure_server'), \
                patch.object(managed.Manager, 'command', command), \
                patch.object(bench, 'main', run):
            output = Path(temp) / 'out'
            code = managed.main(['--out', str(output), '--limit', '1'])
            metadata = json.loads((output / 'automation.json').read_text())
            with zipfile.ZipFile(output / 'resultado.zip') as bundle:
                self.assertIn('automation.json', bundle.namelist())
                self.assertIn('comparativo.md', bundle.namelist())
            before = (output / 'automation.json').read_text()
            self.assertEqual(managed.main(['--out', str(output)]), 2)
            self.assertEqual((output / 'automation.json').read_text(), before)
        return code, metadata, commands

    def test_two_models_sequential_fixed_context_auto_offload_and_cleanup(self):
        code, metadata, commands = self.simulate()
        self.assertEqual(code, 0)
        self.assertEqual(len(metadata['results']), 2)
        loads = [cmd for cmd in commands if cmd[0] == 'load' and '--estimate-only' not in cmd]
        self.assertEqual(len(loads), 2)
        self.assertTrue(all('8192' in cmd and '--gpu' not in cmd for cmd in loads))
        self.assertEqual(commands.count(('unload', '--all')), 2)
        self.assertIn(('unload', 'taskpilot-bench-1'), commands)

    def test_load_failure_recorded_and_next_model_runs(self):
        code, metadata, _ = self.simulate(failure=True)
        self.assertEqual(code, 2)
        self.assertIn('out of memory', metadata['results'][0]['error'])
        self.assertEqual(metadata['results'][1]['exit_code'], 0)

    def test_interrupt_cleans_up_and_preserves_report(self):
        code, metadata, commands = self.simulate(interrupt=True)
        self.assertEqual(code, 130)
        self.assertTrue(metadata['interrupted'])
        self.assertEqual(len(metadata['results']), 1)
        self.assertIn(('unload', 'taskpilot-bench-0'), commands)

    def test_server_start_only_on_connection_refused(self):
        import httpx
        original = httpx.Client
        calls = []

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ConnectError('refused')
            return httpx.Response(200, json={'data': []})

        def client(**kwargs):
            return original(transport=httpx.MockTransport(handler), **kwargs)

        with patch.object(managed.httpx, 'Client', client), \
                patch.object(managed.Manager, 'command', return_value='') as command:
            managed.ensure_server(managed.Manager('fake'), 'http://127.0.0.1:1234/v1')
            self.assertEqual(command.call_args_list[0].args, ('daemon', 'up'))
            self.assertEqual(command.call_args_list[1].args,
                             ('server', 'start', '--port', '1234', '--bind', '127.0.0.1'))
            command.reset_mock()
            managed.ensure_server(managed.Manager('fake'), 'http://127.0.0.1:1234/v1')
            command.assert_called_once_with('server', 'status')

    def test_command_uses_no_shell_and_records_exit(self):
        from unittest.mock import Mock
        process = Mock(returncode=0)
        process.communicate.return_value = ('output', '')
        with patch.object(managed.subprocess, 'Popen', return_value=process) as spawn:
            manager = managed.Manager('lms')
            self.assertEqual(manager.command('load', 'name with spaces'), 'output')
            self.assertFalse(spawn.call_args.kwargs['shell'])
            self.assertEqual(spawn.call_args.args[0], ['lms', 'load', 'name with spaces'])
            self.assertEqual(manager.events[0]['returncode'], 0)
