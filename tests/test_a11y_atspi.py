"""Observação AT-SPI (backend Wayland) e o que o loop faz com ela. Tudo com árvore falsa: nada
abre sessão de portal nem lê o desktop real."""
import unittest
from types import SimpleNamespace
from unittest import mock

import uia
from platform_backend import a11y_atspi, wayland


class FakeStates:
    def __init__(self, names):
        self.names = set(names)

    def contains(self, state):
        return state in self.names


class FakeNode:
    def __init__(self, role="panel", name="", states=("SHOWING", "SENSITIVE"), text=None,
                 children=()):
        self.role, self.name, self.states, self.text = role, name, set(states), text
        self.children = list(children)

    def get_role_name(self):
        return self.role

    def get_name(self):
        return self.name

    def get_state_set(self):
        return FakeStates(self.states)

    def get_child_count(self):
        return len(self.children)

    def get_child_at_index(self, i):
        return self.children[i]


class FakeAtspi:
    """Só o que a11y_atspi usa do módulo Atspi."""

    StateType = SimpleNamespace(**{n: n for n in (
        "SHOWING", "ACTIVE", "FOCUSED", "EDITABLE", "ENABLED", "SENSITIVE", "CHECKED")})

    def __init__(self, apps):
        self.apps = apps
        FakeAtspi.Text = SimpleNamespace(
            get_character_count=lambda n: len(n.text) if n.text is not None else 0,
            get_text=lambda n, a, b: (n.text or "")[a:b])

    def get_desktop(self, _i):
        return FakeNode("desktop", children=self.apps)


def editor(text="ola mundo", active=True, focused=True, extra=()):
    field_states = ["SHOWING", "SENSITIVE", "EDITABLE"] + (["FOCUSED"] if focused else [])
    field = FakeNode("text", "", field_states, text=text)
    bar = FakeNode("panel", "", children=[
        FakeNode("push button", "Nova aba"),
        FakeNode("toggle button", "Menu principal"),
        FakeNode("label", "Rascunho"),
    ])
    # o campo fica fundo na árvore, como no libadwaita
    deep = field
    for _ in range(25):
        deep = FakeNode("panel", "", children=[deep])
    frame = FakeNode("frame", f"{text} (Rascunho) - Editor de Texto",
                     ["SHOWING"] + (["ACTIVE"] if active else []), children=[bar, deep, *extra])
    return FakeNode("application", "gnome-text-editor", [], children=[frame])


class SnapshotTests(unittest.TestCase):
    def snap(self, apps, **kw):
        return a11y_atspi.snapshot(atspi=FakeAtspi(apps), **kw)

    def test_le_titulo_elementos_e_campo_com_valor(self):
        s = self.snap([editor("ola mundo")])
        self.assertEqual(s["status"], "ok")
        self.assertEqual(s["title"], "ola mundo (Rascunho) - Editor de Texto")
        by_name = {i["name"]: i for i in s["items"]}
        self.assertEqual(by_name["Nova aba"]["type"], "Button")
        self.assertEqual(by_name["Rascunho"]["type"], "Text")
        field = by_name["campo de texto"]  # campo sem nome ganha rótulo, mas é achado
        self.assertEqual((field["type"], field["value"]), ("Edit", "ola mundo"))
        self.assertIn("focused", field["state"])
        self.assertEqual(s["focused"]["value"], "ola mundo")

    def test_nunca_inventa_coordenadas(self):
        s = self.snap([editor()])
        self.assertTrue(s["items"])
        for it in s["items"]:
            self.assertTrue(it["nobounds"])
            self.assertEqual(it["bounds"], [0, 0, 0, 0])

    def test_ignora_apps_de_infra_e_janelas_invisiveis(self):
        shell = FakeNode("application", "gnome-shell", [], children=[
            FakeNode("frame", "Main stage", ["SHOWING", "ACTIVE"])])
        portal = FakeNode("application", "xdg-desktop-portal-gnome", [], children=[
            FakeNode("frame", "Área de trabalho remota", ["SHOWING", "ACTIVE"])])
        hidden = FakeNode("application", "outro", [], children=[
            FakeNode("frame", "oculta", ["ACTIVE"])])  # sem SHOWING
        s = self.snap([shell, portal, hidden, editor()])
        self.assertEqual(s["app"], "gnome-text-editor")
        self.assertEqual([w[0] for w in s["windows"]], ["gnome-text-editor"])

    def test_sem_janela_de_usuario_diz_no_window(self):
        s = self.snap([FakeNode("application", "gnome-shell", [])])
        self.assertEqual((s["status"], s["items"], s["title"]), ("no_window", [], ""))

    def test_escolha_da_janela_ativa_declara_como_decidiu(self):
        a = editor("a", active=False, focused=False)
        b = editor("b", active=True, focused=False)
        self.assertEqual(self.snap([a, b])["how"], "active")
        self.assertEqual(self.snap([a, b])["title"].split()[0], "b")
        c = editor("c", active=False, focused=True)
        d = editor("d", active=False, focused=False)
        self.assertEqual(self.snap([c, d])["how"], "focused")
        self.assertEqual(self.snap([d, d])["how"], "latest")  # palpite, declarado
        self.assertEqual(self.snap([a])["how"], "only")

    def test_sem_estado_focused_o_unico_campo_editavel_e_foco_provavel(self):
        s = self.snap([editor("xyz", focused=False)])
        self.assertEqual(s["focus_how"], "guess")
        self.assertEqual(s["focused"]["value"], "xyz")

    def test_dois_campos_sem_foco_nao_adivinham(self):
        second = FakeNode("text", "", ["SHOWING", "SENSITIVE", "EDITABLE"], text="outro")
        s = self.snap([editor("a", focused=False, extra=[second])])
        self.assertIsNone(s["focused"])

    def test_valor_longo_e_truncado_e_valor_igual_ao_nome_some(self):
        s = self.snap([editor("x" * 500)])
        field = next(i for i in s["items"] if i["type"] == "Edit")
        self.assertTrue(field["value"].endswith("…"))
        self.assertLessEqual(len(field["value"]), a11y_atspi.VALUE_LEN + 1)

    def test_teto_de_elementos_marca_truncado(self):
        many = [FakeNode("push button", f"b{i}") for i in range(30)]
        frame = FakeNode("frame", "Janela", ["SHOWING", "ACTIVE"], children=many)
        s = self.snap([FakeNode("application", "app", [], children=[frame])], max_elements=10)
        self.assertEqual((s["status"], len(s["items"])), ("truncated", 10))

    def test_desabilitado_e_so_quando_nao_ha_enabled_nem_sensitive(self):
        frame = FakeNode("frame", "J", ["SHOWING", "ACTIVE"], children=[
            FakeNode("push button", "ligado", ["SHOWING", "SENSITIVE"]),
            FakeNode("push button", "desligado", ["SHOWING"])])
        s = self.snap([FakeNode("application", "app", [], children=[frame])])
        st = {i["name"]: i["state"] for i in s["items"]}
        self.assertNotIn("disabled", st["ligado"])
        self.assertIn("disabled", st["desligado"])

    def test_erro_vira_status_nunca_excecao(self):
        class Boom:
            StateType = FakeAtspi.StateType

            def get_desktop(self, _i):
                raise RuntimeError("sem barramento")

        s = a11y_atspi.snapshot(atspi=Boom())
        self.assertEqual(s["status"], "error")
        self.assertIn("sem barramento", s["error"])


class UiaSeamTests(unittest.TestCase):
    def test_snapshot_do_backend_alimenta_o_loop_e_o_diagnostico(self):
        backend = mock.Mock()
        backend.a11y_snapshot.return_value = {
            "status": "ok", "title": "Janela", "elapsed_ms": 12.0, "how": "active",
            "items": [{"id": 0, "name": "Salvar", "type": "Button", "bounds": [0, 0, 0, 0],
                       "nobounds": True, "value": "", "context": "", "state": ""}]}
        with mock.patch("platform_backend.active", return_value=backend):
            items, title, wrect = uia.active_window_snapshot()
        self.assertEqual((title, wrect, len(items)), ("Janela", None, 1))
        self.assertEqual(uia.LAST_DIAG["status"], "ok")
        self.assertEqual(uia.LAST_DIAG["source"], "atspi")

    def test_falha_do_backend_vira_diagnostico_de_erro(self):
        backend = mock.Mock()
        backend.a11y_snapshot.side_effect = RuntimeError("ponte caiu")
        with mock.patch("platform_backend.active", return_value=backend):
            items, title, _ = uia.active_window_snapshot()
        self.assertEqual((items, title), ([], ""))
        self.assertEqual(uia.LAST_DIAG["status"], "error")
        self.assertIn("ponte caiu", uia.LAST_DIAG["error"])

    def test_texto_e_tipo_do_foco_vem_do_backend(self):
        backend = mock.Mock()
        backend.focused_text.return_value = ("Edit", "ola mundo")
        with mock.patch("platform_backend.active", return_value=backend):
            self.assertEqual(uia.focused_value(), "ola mundo")
            self.assertEqual(uia.focused_control_type(), "Edit")


class PortalRulesTests(unittest.TestCase):
    def test_sem_interacao_remota_e_recusado_com_instrucao(self):
        from platform_backend import portal_rules as pr

        why = pr.devices_error(0, "/x/token")
        self.assertIn("Permitir interação remota", why)
        self.assertIn("/x/token", why)
        self.assertTrue(pr.devices_error(1, "t"))  # só teclado
        self.assertTrue(pr.devices_error(2, "t"))  # só ponteiro

    def test_teclado_e_ponteiro_passam(self):
        from platform_backend import portal_rules as pr

        self.assertEqual(pr.devices_error(3, "t"), "")
        self.assertEqual(pr.devices_error(7, "t"), "")  # + toque


class BackendClientTests(unittest.TestCase):
    def test_a11y_sobe_a_ponte_sem_abrir_sessao_do_portal(self):
        be = wayland.WaylandBackend()
        with mock.patch.object(be, "_spawn") as spawn, \
                mock.patch.object(be, "_call", return_value={"ok": True, "items": []}) as call:
            be.a11y_snapshot(max_elements=50, timeout=1.0)
        spawn.assert_called_once()
        self.assertEqual(call.call_args.args[0]["cmd"], "a11y")  # nunca "start"/consentimento

    def test_focused_text_devolve_tipo_e_valor_truncado(self):
        be = wayland.WaylandBackend()
        snap = {"focused": {"type": "Edit", "value": "a  b\n" + "c" * 200}}
        with mock.patch.object(be, "a11y_snapshot", return_value=snap):
            kind, val = be.focused_text(max_len=10)
        self.assertEqual(kind, "Edit")
        self.assertEqual(val, "a b cccccc…")

    def test_sessao_encerrada_pelo_usuario_aciona_o_stop_de_emergencia(self):
        be = wayland.WaylandBackend()
        be._proc = mock.Mock()
        be._proc.poll.return_value = None
        be._proc.stdout.readline.return_value = (
            '{"ok": false, "error": "RuntimeError: SESSION_CLOSED: o compartilhamento acabou"}\n')
        with mock.patch("safety.stop") as stop, self.assertRaisesRegex(RuntimeError,
                                                                      "SESSION_CLOSED"):
            be._call({"cmd": "frame", "path": "/tmp/x.png"})
        stop.assert_called_once()

    def test_outro_erro_da_ponte_nao_dispara_o_stop(self):
        be = wayland.WaylandBackend()
        be._proc = mock.Mock()
        be._proc.stdout.readline.return_value = '{"ok": false, "error": "outra coisa"}\n'
        with mock.patch("safety.stop") as stop, self.assertRaises(RuntimeError):
            be._call({"cmd": "frame", "path": "/tmp/x.png"})
        stop.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
