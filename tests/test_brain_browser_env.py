import json
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from evals.brain.agent_contract import Decision
from evals.brain.browser_env import BrowserEnv
from evals.brain.fixtures import CATALOG

CATALOG_DATA = json.loads(CATALOG.read_text())


def observation(oid, elements=(), text=""):
    return {
        "version": "0",
        "id": oid,
        "url": "http://localhost:1/webmail",
        "elements": list(elements),
        "text": text,
        "coverage": "DOM podado",
        "next": "",
    }


def evidence(ref, excerpt):
    return {"ref": ref, "url": "http://localhost:1/webmail", "excerpt": excerpt}


class FakeExtension:
    """Responde no formato real da extensão (content.js/worker.js)."""

    def __init__(self):
        self.calls, self.serial = [], 0

    def __call__(self, action, confirmed=False, tab=None):
        self.calls.append({**action, "_confirmed": confirmed, "_tab": tab})
        self.serial += 1
        oid = f"u{self.serial}"
        tool = action["tool"]
        if tool == "read_page":
            button = {
                "ref": f"{oid}:e0",
                "role": "button",
                "name": "Enviar",
                "value": "",
                "sensitive": False,
            }
            field = {
                "ref": f"{oid}:e1",
                "role": "textbox",
                "name": "Rascunho",
                "value": "",
                "sensitive": False,
            }
            return {
                "observation": observation(oid, [button, field]),
                "result": {
                    "version": "0",
                    "sent": True,
                    "confirmed": True,
                    "evidence": [evidence(f"r:{oid}", "[]")],
                    "delta": "",
                    "error": "",
                },
            }
        if tool == "get_page_text":
            return {
                "observation": observation(oid, text="Texto da página"),
                "result": {
                    "version": "0",
                    "sent": True,
                    "confirmed": True,
                    "evidence": [evidence(f"r:{oid}", "Texto da página")],
                    "delta": "",
                    "error": "",
                },
            }
        if tool == "form_input":
            return {
                "result": {
                    "version": "0",
                    "sent": True,
                    "confirmed": True,
                    "evidence": [evidence("r:campo", action["text"])],
                    "delta": "{}",
                    "error": "",
                }
            }
        if tool == "click":
            if action["ref"].endswith("e0") and not confirmed:
                return {"error": "confirmation_required"}
            return {
                "result": {
                    "version": "0",
                    "sent": True,
                    "confirmed": False,
                    "evidence": [],
                    "delta": "",
                    "error": "effect_requires_read",
                }
            }
        if tool == "tabs_list":
            return {
                "observation": observation(oid, text="7 | http://localhost:1 | Webmail"),
                "result": {
                    "version": "0",
                    "sent": True,
                    "confirmed": True,
                    "evidence": [evidence(f"r:{oid}", "7 | http://localhost:1 | Webmail")],
                    "delta": "",
                    "error": "",
                },
            }
        if tool == "tabs_create":
            if "roubo" in action["url"]:
                return {"error": "destination_not_granted"}
            return {"sent": True, "confirmed": True, "url": action["url"], "tab": 99}
        if tool == "tabs_select":
            return {
                "sent": True,
                "confirmed": True,
                "url": "http://localhost:1/x",
                "tab": int(action["tab"]),
            }
        if tool == "tabs_close":
            if action["tab"] == "7":
                return {"error": "not_agent_tab"}
            return {"sent": True, "confirmed": True, "url": "", "tab": int(action["tab"])}
        if tool == "navigate":
            if "roubo" in action["url"]:
                return {"error": "destination_not_granted"}
            return {"sent": True, "confirmed": True, "url": action["url"]}
        return {"error": "unsupported_tool"}


class StateServer(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"draft": "x", "recipient": "José", "sent": []}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class BrowserEnvTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), StateServer)
        Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.extension = FakeExtension()
        self.env = BrowserEnv(
            self.extension,
            "webmail",
            "http://localhost:1",
            CATALOG_DATA,
            state_url=f"http://127.0.0.1:{self.server.server_port}/state",
            settle=0,
        )

    def act(self, **action):
        return self.env.execute(Decision.validate_python(action))

    def test_entry_page_is_recorded_as_evidence_but_action_refreshes_are_not(self):
        self.assertEqual(self.env.current.id, "u1")
        self.assertEqual([e.ref for e in self.env.evidence], ["r:u1"])
        ref = self.env.current.elements[1].ref
        self.act(tool="form_input", ref=ref, text="x")  # refresh pós-ação não vira leitura
        self.assertEqual([e.ref for e in self.env.evidence], ["r:u1", "r:campo"])
        self.act(tool="navigate", url="http://localhost:1/loja")  # página nova: leitura de entrada
        self.assertEqual(len(self.env.evidence), 3)

    def test_old_cursor_is_refused_with_the_valid_one_and_never_reaches_the_extension(self):
        before = len(self.extension.calls)
        result = self.act(tool="read_page", filter="all", scope="", next="velho")
        self.assertEqual(result.error, "stale_cursor")
        self.assertIn("última página", result.delta)
        self.assertEqual(len(self.extension.calls), before)

    def test_reads_record_evidence_and_replace_the_observation(self):
        result = self.act(tool="get_page_text", scope="", next="")
        self.assertTrue(result.confirmed)
        self.assertEqual(self.env.current.text, "Texto da página")
        self.assertEqual(self.env.evidence[-1].excerpt, "Texto da página")

    def test_form_input_keeps_field_evidence_and_refreshes_refs(self):
        ref = self.env.current.elements[1].ref
        result = self.act(tool="form_input", ref=ref, text="José, confirmo")
        self.assertTrue(result.confirmed)
        self.assertEqual(self.env.evidence[-1].excerpt, "José, confirmo")
        self.assertNotEqual(self.env.current.elements[1].ref, ref)

    def test_click_is_not_a_confirmed_effect_and_is_not_an_error(self):
        ref = self.env.current.elements[1].ref
        result = self.act(tool="click", ref=ref)
        self.assertTrue(result.sent)
        self.assertFalse(result.confirmed)
        self.assertEqual(result.error, "")
        self.assertIn("releia", result.delta)

    def test_policy_vetoes_are_returned_and_counted(self):
        ref = self.env.current.elements[0].ref
        result = self.act(tool="click", ref=ref)
        self.assertEqual(result.error, "confirmation_required")
        result = self.act(tool="navigate", url="https://roubo.invalid")
        self.assertEqual(result.error, "destination_not_granted")
        self.assertEqual(
            [v["error"] for v in self.env.vetoes],
            ["confirmation_required", "destination_not_granted"],
        )

    def make_env(self, confirm):
        return BrowserEnv(
            self.extension, "webmail", "http://localhost:1", CATALOG_DATA, settle=0, confirm=confirm
        )

    def test_human_confirmation_resends_only_when_approved(self):
        asked = []
        env = self.make_env(lambda info: asked.append(info) or True)
        result = env.execute(
            Decision.validate_python({"tool": "click", "ref": env.current.elements[0].ref})
        )
        self.assertEqual(result.error, "")
        self.assertEqual(asked[0]["target"], "button: Enviar")
        self.assertTrue([c for c in self.extension.calls if c["tool"] == "click"][-1]["_confirmed"])
        self.assertEqual(env.confirmations, [{"target": "button: Enviar", "approved": True}])
        self.assertEqual(env.vetoes, [])

    def test_denied_confirmation_stays_a_veto_and_never_sets_the_flag(self):
        env = self.make_env(lambda info: False)
        result = env.execute(
            Decision.validate_python({"tool": "click", "ref": env.current.elements[0].ref})
        )
        self.assertEqual(result.error, "confirmation_required")
        self.assertFalse(any(c["_confirmed"] for c in self.extension.calls))
        self.assertEqual(env.vetoes[0]["error"], "confirmation_required")

    def test_without_a_callback_nothing_is_ever_confirmed(self):
        result = self.act(tool="click", ref=self.env.current.elements[0].ref)
        self.assertEqual(result.error, "confirmation_required")
        self.assertFalse(any(c["_confirmed"] for c in self.extension.calls))

    def test_stale_or_invented_refs_never_reach_the_extension(self):
        before = len(self.extension.calls)
        with self.assertRaises(ValueError):
            self.act(tool="click", ref="inventado")
        self.assertEqual(len(self.extension.calls), before)

    def test_done_resolves_refs_and_rejects_invented_ones(self):
        with self.assertRaises(ValueError):
            self.act(tool="done", answer="ok", evidence=["r:inventada"])
        self.act(tool="get_page_text", scope="", next="")
        recorded = self.env.evidence[0].ref
        self.assertTrue(self.act(tool="done", answer="ok", evidence=[recorded]).confirmed)
        # Citar um elemento da leitura vale como citar a leitura; o registro inteiro é anexado.
        reading = recorded.removeprefix("r:")
        self.act(tool="done", answer="ok", evidence=[f"{reading}:e2"])
        self.assertEqual(self.env.final_evidence, self.env.evidence)

    def test_done_without_any_reading_is_a_false_done(self):
        with self.assertRaises(ValueError):
            self.act(tool="done", answer="ok", evidence=["r:qualquer"])
        self.assertEqual(self.env.final_evidence, [])

    def test_stale_ref_is_renewed_only_for_a_unique_role_and_name(self):
        old = self.env.current.elements[1].ref  # textbox "Rascunho"
        self.env.refresh()  # nova observação: a ref antiga venceu
        fresh = self.env.current.elements[1].ref
        self.assertNotEqual(old, fresh)
        result = self.act(tool="form_input", ref=old, text="José, confirmo")
        self.assertTrue(result.confirmed)
        sent = [c for c in self.extension.calls if c["tool"] == "form_input"][-1]
        self.assertEqual(sent["ref"], fresh)
        self.assertEqual(self.env.renewals[0]["target"], "textbox: Rascunho")

    def test_ambiguous_stale_ref_is_an_error_result_not_a_guess(self):
        old = self.env.current.elements[1].ref
        self.env.refresh()
        twin = self.env.current.elements[1].model_copy(update={"ref": "dup:e9"})
        self.env.current.elements.append(twin)  # dois "textbox: Rascunho": não dá para escolher
        before = len(self.extension.calls)
        result = self.act(tool="form_input", ref=old, text="x")
        self.assertEqual(result.error, "stale_ref")
        self.assertEqual(self.env.renewals, [])
        self.assertEqual(len(self.extension.calls), before)

    def test_invented_ref_is_still_rejected_not_renewed(self):
        with self.assertRaises(ValueError):
            self.act(tool="click", ref="nunca-vista:e1")

    def test_describe_names_the_target_for_the_history(self):
        action = Decision.validate_python(
            {"tool": "click", "ref": self.env.current.elements[0].ref}
        )
        self.assertEqual(self.env.describe(action), "button: Enviar")

    def test_tabs_list_is_evidence_but_keeps_the_page_observation(self):
        page = self.env.current
        result = self.act(tool="tabs_list")
        self.assertIn("Webmail", result.delta)
        self.assertIs(self.env.current, page)
        self.assertIn("Webmail", self.env.evidence[-1].excerpt)

    def test_working_tab_follows_create_select_and_close(self):
        self.assertIsNone(self.env.tab)
        self.act(tool="tabs_create", url="http://localhost:1/loja")
        self.assertEqual(self.env.tab, 99)
        self.assertEqual(self.extension.calls[-1]["_tab"], 99)  # releitura já na aba nova
        self.act(tool="tabs_select", tab="5")
        self.assertEqual(self.env.tab, 5)
        self.act(tool="tabs_close", tab="5")
        self.assertIsNone(self.env.tab)  # volta à aba inicial
        self.assertIsNone(self.extension.calls[-1]["_tab"])

    def test_tab_policy_errors_are_vetoes_not_exceptions(self):
        self.assertEqual(
            self.act(tool="tabs_create", url="https://roubo.invalid").error,
            "destination_not_granted",
        )
        self.assertEqual(self.act(tool="tabs_close", tab="7").error, "not_agent_tab")
        self.assertIn("not_agent_tab", [v["error"] for v in self.env.vetoes])

    def test_unsupported_tools_are_not_faked(self):
        self.assertEqual(
            self.act(tool="scroll_to", ref=self.env.current.elements[0].ref).error,
            "unsupported_tool",
        )

    def test_snapshot_reads_the_independent_http_state(self):
        self.assertEqual(self.env.snapshot()["draft"], "x")

    def test_error_reply_becomes_stub_observation(self):
        def blocked(action):
            return {"error": "human_required", "reason": "captcha"}

        env = BrowserEnv(blocked, "captcha", "http://localhost:1", CATALOG_DATA, settle=0)
        self.assertIn("human_required", env.current.coverage)
        self.assertEqual(env.current.elements, [])


if __name__ == "__main__":
    unittest.main()
