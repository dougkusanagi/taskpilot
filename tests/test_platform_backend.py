"""Camada de plataforma (protótipo Wayland): só o que é puro/stub. Nada abre sessão de portal."""
import sys
import unittest
from unittest import mock

import platform_backend
from platform_backend import wayland


class KeysymTests(unittest.TestCase):
    def test_ascii_e_latin1_sao_o_proprio_codepoint(self):
        self.assertEqual(wayland.keysym_for_char("a"), 0x61)
        self.assertEqual(wayland.keysym_for_char("A"), 0x41)
        self.assertEqual(wayland.keysym_for_char("á"), 0xE1)
        self.assertEqual(wayland.keysym_for_char("ç"), 0xE7)

    def test_fora_do_latin1_usa_faixa_unicode_do_x11(self):
        self.assertEqual(wayland.keysym_for_char("€"), 0x01000000 + 0x20AC)

    def test_quebra_de_linha_e_tab_viram_teclas(self):
        self.assertEqual(wayland.keysym_for_char("\n"), 0xFF0D)
        self.assertEqual(wayland.keysym_for_char("\t"), 0xFF09)

    def test_non_ascii_uses_unicode_entry(self):
        # Mutter descarta keysym fora do layout (28/10: "Olá" virou "Ol")
        self.assertEqual(wayland.key_events_for_char("a"), [(0x61, 1), (0x61, 0)])
        ev = wayland.key_events_for_char("á")
        self.assertEqual(ev[:3], [(0xFFE3, 1), (0xFFE1, 1), (ord("u"), 1)])
        typed = [chr(s) for s, st in ev[6:] if st == 1]
        self.assertEqual(typed, ["e", "1", " "])

    def test_teclas_nomeadas_e_erro_honesto(self):
        self.assertEqual(wayland.keysym_for_key("Ctrl"), 0xFFE3)
        self.assertEqual(wayland.keysym_for_key("f5"), 0xFFC2)
        self.assertEqual(wayland.keysym_for_key("l"), 0x6C)
        with self.assertRaises(ValueError):
            wayland.keysym_for_key("teclaquenaoexiste")


class WhitelistTests(unittest.TestCase):
    def test_nomes_do_planner_windows_mapeiam_para_apps_linux(self):
        self.assertEqual(wayland.LINUX_APPS["notepad"], "gnome-text-editor")
        self.assertEqual(wayland.LINUX_APPS["calc"], "gnome-calculator")
        self.assertEqual(wayland.LINUX_APPS["chrome"], "google-chrome")

    def test_app_fora_da_whitelist_recusa_sem_lancar_nada(self):
        be = wayland.WaylandBackend()
        with mock.patch("subprocess.Popen") as popen:
            with self.assertRaises(ValueError):
                be.open_app("rm -rf /")
            popen.assert_not_called()

    def test_foco_programatico_e_honestamente_false(self):
        self.assertFalse(wayland.WaylandBackend().focus_window("qualquer"))


class FocusRestoreTests(unittest.TestCase):
    """Foco após o diálogo do portal: só teclas, confirmado por AT-SPI (mocks, sem portal)."""

    def _backend(self, titles, devices=3):
        be = wayland.WaylandBackend()
        be.devices = devices
        sent = []
        be._call = lambda req, timeout=15.0: sent.append(req) or {"ok": True}
        seq = iter(titles)
        be._active_title = lambda: next(seq)
        return be, sent

    def test_sem_janela_ativa_restaura_com_super_esc_sem_clique(self):
        be, sent = self._backend(["", "Doc - Editor"])
        with mock.patch.object(wayland.time, "sleep"):
            note = be._restore_focus("Doc - Editor")
        self.assertIn("restaurado", note)
        self.assertNotIn("não", note)
        self.assertEqual([r["cmd"] for r in sent], ["keysym"] * 4)
        self.assertEqual([r["sym"] for r in sent[::2]], [0xFFEB, 0xFF1B])
        self.assertFalse(any(r["cmd"] in ("button", "move") for r in sent))

    def test_janela_ainda_ativa_nao_envia_nada(self):
        be, sent = self._backend(["Doc - Editor"])
        self.assertEqual(be._restore_focus("Doc - Editor"), "")
        self.assertEqual(sent, [])

    def test_sem_janela_antes_ou_sem_teclado_nao_age(self):
        be, sent = self._backend([])
        self.assertEqual(be._restore_focus(""), "")
        be, sent = self._backend([""], devices=2)
        self.assertEqual(be._restore_focus("Doc"), "")
        self.assertEqual(sent, [])

    def test_outra_janela_ativa_e_informada_como_falha(self):
        be, _ = self._backend(["", "Outra"])
        with mock.patch.object(wayland.time, "sleep"):
            note = be._restore_focus("Doc - Editor")
        self.assertIn("não restaurado", note)
        self.assertIn("Outra", note)

    def test_titulo_ativo_vem_do_atspi_e_erro_vira_vazio(self):
        with mock.patch("platform_backend.accessibility.snapshot",
                        return_value={"title": "Doc", "items": []}):
            self.assertEqual(wayland.WaylandBackend._active_title(), "Doc")
        with mock.patch("platform_backend.accessibility.snapshot",
                        return_value={"error": "janela ativa ausente"}):
            self.assertEqual(wayland.WaylandBackend._active_title(), "")


class UnicodeWarmupTests(unittest.TestCase):
    def test_so_a_primeira_entrada_unicode_espera_mais(self):
        be = wayland.WaylandBackend()
        be.devices = 3
        be._ensure = lambda: None
        be._call = lambda req, timeout=15.0: {"ok": True}
        with mock.patch.object(wayland.time, "sleep") as sleep:
            be.input.type_text("áá")
        waits = [c.args[0] for c in sleep.call_args_list]
        self.assertIn(wayland.FIRST_UNICODE_COMMIT_S, waits)
        self.assertEqual(waits.count(wayland.FIRST_UNICODE_COMMIT_S), 1)
        self.assertIn(wayland.UNICODE_COMMIT_S, waits)


class SeamTests(unittest.TestCase):
    def tearDown(self):
        platform_backend.disable()

    def test_sem_opt_in_nada_liga(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            self.assertIsNone(platform_backend.enable(""))
            self.assertIsNone(platform_backend.enable("default"))
            self.assertIsNone(platform_backend.active())

    def test_backend_desconhecido_falha_alto(self):
        with self.assertRaises(ValueError):
            platform_backend.enable("x11")

    def test_wrap_input_delega_ao_nativo_sem_backend_e_ao_backend_com_ele(self):
        nativo = mock.Mock()
        w = platform_backend.wrap_input(nativo)
        if sys.platform == "win32":
            self.assertIs(w, nativo)
            return
        w.click(1, 2)
        nativo.click.assert_called_once_with(1, 2)
        fake = mock.Mock()
        with mock.patch.object(platform_backend, "_ACTIVE", fake):
            w.click(3, 4)
        fake.input.click.assert_called_once_with(3, 4)
        nativo.click.assert_called_once()  # o nativo não recebeu a 2ª chamada

    def test_actions_usa_backend_para_digitar_e_limites_de_tela(self):
        import actions

        fake = mock.Mock()
        fake.screen_rect.return_value = (0, 0, 100, 50)
        with mock.patch.object(platform_backend, "_ACTIVE", fake):
            actions._type_unicode("olá")
            fake.input.type_text.assert_called_once_with("olá")
            self.assertEqual(actions._virtual_screen(), (0, 0, 100, 50))
            with self.assertRaises(ValueError):
                actions._check_coords(100, 10)  # fora do monitor compartilhado


class CliPlatformTests(unittest.TestCase):
    def test_locate_uses_platform_and_always_closes_it(self):
        import main

        calls = []
        with mock.patch.object(sys, "argv", ["main.py", "--platform", "wayland", "--locate", "OK"]), \
                mock.patch.object(main.cfgmod, "load", return_value={}), \
                mock.patch.object(platform_backend, "enable", side_effect=lambda p: calls.append(p)), \
                mock.patch.object(platform_backend, "disable", side_effect=lambda: calls.append("close")), \
                mock.patch.object(main, "locate_only", side_effect=lambda *a: calls.append("locate")):
            main.main()
        self.assertEqual(calls, ["wayland", "locate", "close"])

    def test_platform_closes_after_locate_error(self):
        import main

        with mock.patch.object(sys, "argv", ["main.py", "--platform", "wayland", "--locate", "OK"]), \
                mock.patch.object(main.cfgmod, "load", return_value={}), \
                mock.patch.object(platform_backend, "enable"), \
                mock.patch.object(platform_backend, "disable") as close, \
                mock.patch.object(main, "locate_only", side_effect=RuntimeError("captura falhou")):
            with self.assertRaisesRegex(RuntimeError, "captura falhou"):
                main.main()
            close.assert_called_once()

    def test_self_test_captures_with_selected_backend_and_closes(self):
        import main
        import obs
        import safety

        calls = []
        with mock.patch.object(sys, "argv", ["main.py", "--self-test", "--platform", "wayland"]), \
                mock.patch.object(main.cfgmod, "load", return_value={}), \
                mock.patch.object(platform_backend, "enable", side_effect=lambda p: calls.append(p)), \
                mock.patch.object(platform_backend, "disable", side_effect=lambda: calls.append("close")), \
                mock.patch.object(obs, "take_screenshot", side_effect=lambda: (calls.append("capture") or ("frame.png", (100, 80)))), \
                mock.patch.object(safety, "start"), mock.patch("builtins.print"):
            main.main()
        self.assertEqual(calls, ["wayland", "capture", "close"])

    def test_dry_run_does_not_enable_platform(self):
        import main

        with mock.patch.object(sys, "argv", ["main.py", "--dry-run", "--platform", "wayland", "--locate", "OK"]), \
                mock.patch.object(main.cfgmod, "load", return_value={}), \
                mock.patch.object(platform_backend, "enable") as enable, \
                mock.patch.object(platform_backend, "disable"), mock.patch.object(main, "locate_only"):
            main.main()
            enable.assert_not_called()


class BridgeInitializationTests(unittest.TestCase):
    def test_restore_token_respects_isolated_xdg_cache(self):
        import runpy
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict("os.environ", {"XDG_CACHE_HOME": tmp}), \
                mock.patch.dict(sys.modules, {"gi": mock.Mock(), "gi.repository": mock.Mock()}):
            module = runpy.run_path(str(Path(wayland.__file__).with_name("wayland_bridge.py")))
        self.assertEqual(module["TOKEN_FILE"], Path(tmp) / "taskpilot/wayland-restore-token")

    def test_pipeline_uses_the_fakesink_handoff_property(self):
        import runpy
        from pathlib import Path

        gst, gio, glib = mock.Mock(), mock.Mock(), mock.Mock()
        repository = mock.Mock(Gst=gst, Gio=gio, GLib=glib)
        with mock.patch.dict(sys.modules, {"gi": mock.Mock(), "gi.repository": repository}):
            module = runpy.run_path(str(Path(wayland.__file__).with_name("wayland_bridge.py")))
        bridge = module["Bridge"].__new__(module["Bridge"])
        bridge.bus = mock.Mock()
        reply, fds = mock.Mock(), mock.Mock()
        reply.unpack.return_value = (0,)
        fds.get.return_value = 7
        bridge.bus.call_with_unix_fd_list_sync.return_value = (reply, fds)
        bridge.session, bridge.node_id = "/session", 42
        bridge._open_pipeline()
        gst.init.assert_called_once_with(None)
        description = gst.parse_launch.call_args.args[0]
        self.assertIn("signal-handoffs=true", description)
        self.assertNotIn("signal-handoff=", description)


class PortalPermissionTests(unittest.TestCase):
    def test_capture_only_permission_never_sends_input(self):
        backend = mock.Mock(devices=0)
        input_api = wayland._Input(backend)
        for request in ({"cmd": "keysym", "sym": 97, "state": 1},
                        {"cmd": "button", "code": 272, "state": 1}):
            with self.assertRaisesRegex(RuntimeError, "Allow Remote Interaction"):
                input_api._c(request)
        backend._call.assert_not_called()

    def test_keyboard_and_pointer_permissions_are_checked_separately(self):
        for devices, allowed, denied in ((1, "keysym", "move"), (2, "move", "keysym")):
            backend = mock.Mock(devices=devices)
            input_api = wayland._Input(backend)
            input_api._c({"cmd": allowed})
            backend._call.assert_called_once_with({"cmd": allowed})
            with self.assertRaises(RuntimeError):
                input_api._c({"cmd": denied})

    def test_full_permission_allows_both_devices(self):
        backend = mock.Mock(devices=3)
        input_api = wayland._Input(backend)
        input_api._c({"cmd": "move"})
        input_api._c({"cmd": "keysym"})
        self.assertEqual(backend._call.call_count, 2)


if __name__ == "__main__":
    unittest.main()
