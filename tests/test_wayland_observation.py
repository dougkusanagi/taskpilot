"""Regressões passivas: nunca abrem portal nem enviam input."""
import unittest
from unittest.mock import patch

import loop
import verification
from platform_backend import accessibility


class ObservationTests(unittest.TestCase):
    def test_missing_bus_never_launches_reader(self):
        with patch.dict('os.environ', {}, clear=True), patch('subprocess.run') as run:
            self.assertIn('error', accessibility.snapshot())
            run.assert_not_called()

    def test_invalid_bounds_never_become_click(self):
        action, note = loop._resolve_uia([dict(name='New Tab', type='Button', bounds=None)],
                                         'New Tab', None)
        self.assertIsNone(action)
        self.assertIn('bounds', note)

    def test_tab_requires_accessible_transition(self):
        before = ['TabItem:First']
        after = before + ['TabItem:Second']
        self.assertTrue(verification.confirm_effect('click', ui_before=before, ui_after=after)[0])
        self.assertFalse(verification.confirm_effect('click', ui_before=before, ui_after=before)[0])
        self.assertFalse(verification.confirm_effect('click')[0])

    def test_missing_text_is_not_confirmed(self):
        self.assertFalse(verification.confirm_effect('type', 'Olá, Ubuntu!', value='')[0])

    def test_unnamed_text_reaches_planner(self):
        self.assertIn('Olá, Ubuntu!', loop.format_ui_names([
            dict(name='', type='Edit', value='Olá, Ubuntu!')])[0])

    def test_opt_in_snapshot_and_diagnostic_are_forwarded(self):
        import uia
        data = dict(items=[dict(type='Edit', name='', value='conteúdo', bounds=None)],
                    title='Janela', focused='conteúdo')
        with patch('platform_backend.active', return_value=object()), \
                patch('platform_backend.accessibility.snapshot', return_value=data):
            items, title, rect = uia.active_window_snapshot()
            self.assertEqual(items, data['items'])
            self.assertEqual(title, 'Janela')
            self.assertIsNone(rect)
            self.assertEqual(uia.focused_value(), 'conteúdo')
            self.assertEqual(uia.LAST_DIAG['status'], 'ok')
        with patch('platform_backend.active', return_value=object()), \
                patch('platform_backend.accessibility.snapshot', return_value={'error': 'sem AT-SPI'}):
            self.assertEqual(uia.active_window_snapshot(), ([], '', None))
            self.assertEqual(uia.LAST_DIAG['status'], 'error')

    def test_text_confirmation_requires_entire_requested_text(self):
        from actions import Action
        requested = 'a' * 40 + 'fim'
        self.assertNotIn('text visible', loop.observe(Action(type='type', text=requested),
                                                    'janela', 'janela', 'a' * 40))

    def test_prompt_keeps_confirmed_target_and_effect(self):
        import planner
        prompt = planner.build_prompt('Clique em Criar e escreva teste', 'Janela', [], [],
                                      task_summary='E1=click Criar: new tab observed',
                                      last_result='click Criar: effect confirmed')
        self.assertIn('E1=click Criar', prompt)
        self.assertIn('click Criar: effect confirmed', prompt)
        self.assertNotIn('image_url', prompt)

    def test_timeout_is_honest(self):
        import subprocess
        with patch.dict('os.environ', {'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/private'}), \
                patch('subprocess.run', side_effect=subprocess.TimeoutExpired('reader', 1)):
            self.assertIn('error', accessibility.snapshot(1))

    def test_reader_keeps_deep_text_without_coordinates(self):
        import sys
        from types import SimpleNamespace

        from platform_backend.atspi_read import read

        class Node:
            def __init__(self, role, name='', value='', children=(), states=()):
                self.role, self.name, self.value = role, name, value
                self.children, self.states = children, states

            def get_child_count(self):
                return len(self.children)

            def get_child_at_index(self, i):
                return self.children[i]

            def get_state_set(self):
                return SimpleNamespace(contains=lambda state: state in self.states)

            def get_role_name(self):
                return self.role

            def get_name(self):
                return self.name

            def get_text_iface(self):
                return SimpleNamespace(get_text=lambda start, end: self.value[start:end],
                                       get_character_count=lambda: len(self.value))

        child = Node('text', value='Olá, Ubuntu!', states=('focused',))
        for _ in range(30):
            child = Node('panel', children=(child,))
        window = Node('frame', 'Editor', children=(child,), states=('active',))
        desktop = Node('desktop', children=(Node('app', children=(window,)),))
        atspi = SimpleNamespace(get_desktop=lambda _: desktop,
                                StateType=SimpleNamespace(ACTIVE='active', FOCUSED='focused'))
        with patch.dict(sys.modules, {'gi': SimpleNamespace(require_version=lambda *_: None),
                                     'gi.repository': SimpleNamespace(Atspi=atspi)}), \
                patch.dict('os.environ', {'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/private'}):
            result = read()
        self.assertEqual(result['focused'], 'Olá, Ubuntu!')
        self.assertEqual(result['items'][-1]['value'], 'Olá, Ubuntu!')
        self.assertIsNone(result['items'][-1]['bounds'])
        self.assertFalse(result['truncated'])
