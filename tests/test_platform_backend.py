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


if __name__ == "__main__":
    unittest.main()
