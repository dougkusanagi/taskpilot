"""Bateria de trajetórias (suíte `trajectory` do model_bench): o modelo age por VÁRIOS passos.

Cada cenário é um simulador determinístico e minúsculo de uma tarefa de desktop (Bloco de
Notas, Chrome, calculadora, perfil ambíguo, banner de cookies). O modelo recebe o prompt real do
planner (`PLANNER_SYSTEM` + `build_prompt`) com histórico, último resultado e estado da tarefa,
escolhe uma ação por passo e o simulador devolve a nova observação. Sucesso = `done` aceito
com as pendências realmente cumpridas, dentro do limite de passos.

Mede o que a cena isolada não mede: convergência, repetição sem efeito, `done` prematuro e uso
de resultado. Não toca mouse/teclado/tela; o "desktop" é só este simulador.
"""
from __future__ import annotations

import json
import re
import time

import httpx

from evals import model_bench as bench
from planner import PLANNER_SYSTEM, build_prompt, strip_type_prefix

MAX_STEPS = 8
MAX_INVALID = 2
MAX_SAME_ACTION = 3


def label(decision: dict) -> str:
    t = decision["type"]
    arg = next((decision[k] for k in ("app", "target", "text", "key", "keys", "instruction",
                                      "perception", "skill") if decision.get(k)), "")
    return f"{t}({str(arg)[:60]})" if arg else t


class Sim:
    """Estado + regras de um cenário. Subclasses implementam `apply`."""
    goal = ""
    category = "trajetoria"
    ALIASES: dict[str, str] = {}

    def __init__(self):
        self.window = ""
        self.elements: list[str] = []
        self.evidence: dict[str, str] = {}
        self.required: list[str] = []
        self.last = "Nenhuma ação anterior."
        self.answer = ""

    def pending(self):
        return [r for r in self.required if r not in self.evidence]

    def observation(self):
        return {"window": self.window, "elements": list(self.elements)}

    def no_effect(self, why=""):
        return "no visible effect" + (f": {why}" if why else "")

    def visible_names(self) -> list[str]:
        return [strip_type_prefix(e.split("=", 1)[0]).casefold() for e in self.elements]

    def clicked(self, d: dict) -> str | None:
        """Elemento VISÍVEL que a ação clica: `uia_click` por nome, ou `visual_action` cuja
        instrução cita o nome do elemento (o app de produção permite os dois caminhos)."""
        names = self.visible_names()
        if d["type"] == "uia_click":
            want = name_of(d)
            want = self.ALIASES.get(want, want)
            return want if want in names else None
        if d["type"] == "visual_action":
            text = " " + " ".join(re.findall(r"\w+|[+=]", d["instruction"].casefold())) + " "
            hits = [n for n in names if f" {n} " in text]
            hits += [self.ALIASES[a] for a in self.ALIASES
                     if f" {a} " in text and self.ALIASES[a] in names]
            return max(hits, key=len) if hits else None
        return None

    def apply(self, d: dict) -> str:  # pragma: no cover - contrato
        raise NotImplementedError

    def done(self, d: dict) -> tuple[bool, str]:
        if self.pending():
            return False, f"done recusado: pendências {self.pending()}"
        given = set(d.get("evidences") or [])
        if not given or not given <= set(self.evidence):
            return False, "done recusado: evidências ausentes ou desconhecidas"
        return True, "ok"


def name_of(d: dict, key="target") -> str:
    return strip_type_prefix(d.get(key) or "").casefold()


def keys_of(d: dict) -> str:
    return (d.get("keys") or "").lower().replace(" ", "")


class NotepadSave(Sim):
    goal = "Digite 'ola mundo' no Bloco de Notas e salve como nota.txt."
    category = "salvar-arquivo"
    MENU = ["MenuItem:Novo", "MenuItem:Salvar", "MenuItem:Salvar como"]

    def __init__(self):
        super().__init__()
        self.window, self.elements = "Sem título - Notepad", ["Edit:Editor", "Menu:Arquivo"]
        self.required = ["ev-texto", "ev-arquivo"]
        self.dialog, self.menu, self.typed, self.fname = False, False, "", ""

    def base_elements(self):
        return [f"Edit:Editor={self.typed}" if self.typed else "Edit:Editor", "Menu:Arquivo"]

    def open_dialog(self):
        self.dialog, self.menu, self.window = True, False, "Salvar como"
        self.elements = ["Edit:Nome do arquivo", "Button:Salvar", "Button:Cancelar"]
        return "Diálogo Salvar como aberto."

    def apply(self, d):
        t = d["type"]
        if t == "type_text" and not self.dialog:
            self.typed += d["text"]
            if "ola mundo" in self.typed.casefold():
                self.evidence["ev-texto"] = "texto digitado"
            self.elements = self.base_elements()
            self.menu = False
            return "Texto digitado no editor."
        if t == "hotkey" and keys_of(d) == "ctrl+s" and not self.dialog:
            return self.open_dialog()
        if t == "type_text" and self.dialog:
            self.fname = d["text"]
            self.elements = [f"Edit:Nome do arquivo={self.fname}", "Button:Salvar",
                             "Button:Cancelar"]
            return "Nome digitado."
        name = self.clicked(d)
        if name == "arquivo" and not self.dialog:
            self.menu, self.elements = True, self.MENU
            return "Menu Arquivo aberto."
        if name == "salvar" and self.menu:
            return self.open_dialog()
        if name == "salvar" and self.dialog:
            if self.fname.strip().casefold() != "nota.txt":
                return "Aviso: nome do arquivo vazio ou diferente do pedido."
            self.dialog, self.window = False, "nota.txt - Notepad"
            self.elements = [f"Edit:Editor={self.typed}"]
            self.evidence["ev-arquivo"] = "arquivo salvo"
            return "Arquivo salvo."
        if name == "cancelar" and self.dialog:
            self.dialog, self.window = False, "Sem título - Notepad"
            self.elements = self.base_elements()
            return "Diálogo cancelado."
        return self.no_effect()


class ChromeUrl(Sim):
    goal = "Abra uma nova aba no Chrome e vá para example.org."
    category = "navegar-url"

    def __init__(self):
        super().__init__()
        self.window, self.elements = "Google - Google Chrome", ["Button:Nova guia", "Pane:Página"]
        self.required = ["ev-aba", "ev-pagina"]
        self.tab, self.focused, self.typed = False, False, ""

    def new_tab(self):
        self.tab, self.focused, self.typed = True, True, ""
        self.window = "Nova guia - Google Chrome"
        self.elements = ["Edit:Barra de endereço", "Pane:Página"]
        self.evidence["ev-aba"] = "nova aba aberta"
        return "Nova aba aberta."

    def apply(self, d):
        t = d["type"]
        if t == "hotkey" and keys_of(d) == "ctrl+t" and not self.tab:
            return self.new_tab()
        if self.clicked(d) == "nova guia" and not self.tab:
            return self.new_tab()
        if t == "hotkey" and keys_of(d) in ("ctrl+l", "f6", "alt+d"):
            self.focused = True
            return "Barra de endereço focada."
        if t == "type_text":
            if not self.focused:
                return self.no_effect("nenhum campo de texto focado")
            self.typed = d["text"]
            self.elements = [f"Edit:Barra de endereço={self.typed}", "Pane:Página"]
            return "URL digitada na barra."
        if t == "press_key" and d["key"].lower() == "enter" and self.typed:
            if "example.org" not in self.typed:
                return "Página carregada, mas não é o endereço pedido."
            self.window = "Example Domain - Google Chrome"
            self.elements = ["Hyperlink:Learn more", "Pane:Página"]
            self.focused = False
            if self.tab:
                self.evidence["ev-pagina"] = "example.org carregado na nova aba"
                return "Página carregada na nova aba: Example Domain."
            self.typed = ""
            return "Página carregada na aba atual (o pedido era numa nova aba)."
        if t == "open_app" and d["app"] == "chrome":
            return "Chrome já está ativo; nada mudou."
        return self.no_effect()


class AmbiguousProfile(Sim):
    goal = "Abra o perfil de Ana."
    category = "ambiguidade"

    def __init__(self):
        super().__init__()
        self.window, self.elements = "Perfis", ["Button:Ana pessoal", "Button:Ana trabalho"]
        self.required, self.asked = ["ev-perfil"], False

    def apply(self, d):
        if d["type"] == "ask":
            self.asked = True
            self.answer = "Ana trabalho"
            return "Resposta do usuário: Ana trabalho"
        name = self.clicked(d)
        if name in ("ana pessoal", "ana trabalho"):
            if not self.asked:
                self.evidence["_chute"] = "clique sem perguntar"
                return "Perfil aberto, mas a escolha era ambígua e não foi confirmada."
            if name == "ana trabalho":
                self.window, self.elements = "Ana trabalho", ["Text:Bem-vinda, Ana"]
                self.evidence["ev-perfil"] = "perfil aberto"
                return "Perfil Ana trabalho aberto."
            return "Perfil errado aberto (o usuário pediu Ana trabalho)."
        return self.no_effect()

    def done(self, d):
        if "_chute" in self.evidence:
            return False, "falha: abriu perfil sem esclarecer a ambiguidade"
        return super().done(d)


class CalcSum(Sim):
    goal = "Calcule 2 + 3 na calculadora."
    category = "multi-clique"
    ALIASES = {"+": "mais", "=": "igual", "plus": "mais", "soma": "mais", "somar": "mais",
               "adicionar": "mais", "equals": "igual", "c": "limpar", "clear": "limpar"}

    def __init__(self):
        super().__init__()
        self.window = "Calculadora"
        self.elements = ["Text:Exibição=0", "Button:1", "Button:2", "Button:3", "Button:Mais",
                         "Button:Igual", "Button:Limpar"]
        self.required, self.expr = ["ev-resultado"], ""

    def show(self):
        self.elements[0] = f"Text:Exibição={self.expr or '0'}"

    def feed(self, ch: str) -> bool:
        if ch in "123":
            self.expr += ch
        elif ch == "+" and self.expr and not self.expr.endswith("+"):
            self.expr += "+"
        elif ch in "=\n":
            try:
                total = sum(int(p) for p in self.expr.split("+") if p)
            except ValueError:
                return False
            self.expr = str(total)
            if self.expr == "5":
                self.evidence["ev-resultado"] = "visor mostra 5"
        else:
            return False
        return True

    def apply(self, d):
        if d["type"] == "type_text":  # a calculadora aceita teclado quando focada
            if all(self.feed(ch) for ch in d["text"].replace(" ", "")):
                self.show()
                return f"Visor: {self.expr}"
            return self.no_effect("caractere não suportado")
        if d["type"] == "press_key" and d["key"].lower() == "enter":
            self.feed("=")
            self.show()
            return f"Visor: {self.expr}"
        name = self.clicked(d)
        if name in ("1", "2", "3"):
            self.feed(name)
        elif name == "mais":
            self.feed("+")
        elif name == "igual":
            self.feed("=")
        elif name == "limpar":
            self.expr = ""
        else:
            return self.no_effect()
        self.show()
        return f"Visor: {self.expr or '0'}"


class CookieBanner(Sim):
    goal = "Aceite os cookies e depois clique em Comprar agora."
    category = "modal-bloqueio"

    def __init__(self):
        super().__init__()
        self.window = "Loja Azul - Google Chrome"
        self.elements = ["Button:Aceitar todos", "Button:Rejeitar", "Button:Comprar agora"]
        self.required, self.banner = ["ev-cookies", "ev-compra"], True

    def apply(self, d):
        name = self.clicked(d)
        if name == "comprar agora":
            if self.banner:
                return self.no_effect("o aviso de cookies cobre a página")
            self.evidence["ev-compra"] = "compra iniciada"
            return "Compra iniciada."
        if name == "aceitar todos" and self.banner:
            self.banner = False
            self.elements = ["Button:Comprar agora"]
            self.evidence["ev-cookies"] = "cookies aceitos"
            return "Cookies aceitos; aviso fechado."
        if name == "rejeitar" and self.banner:
            self.banner = False
            self.elements = ["Button:Comprar agora"]
            return "Cookies rejeitados (o pedido era aceitar)."
        return self.no_effect()


SCENARIOS = {cls.__name__: cls for cls in
             (NotepadSave, ChromeUrl, AmbiguousProfile, CalcSum, CookieBanner)}


def load_cases() -> list[dict]:
    return [{"id": name, "category": cls.category, "goal": cls().goal}
            for name, cls in SCENARIOS.items()]


def step_messages(sim: Sim, history: list[str]) -> list[dict]:
    pend = sim.pending()
    summary = ("Pending requirements: " + ", ".join(pend) if pend else "All requirements met.")
    summary += "\nAvailable evidence IDs: " + (", ".join(
        k for k in sim.evidence if not k.startswith("_")) or "(none yet)")
    obs = sim.observation()
    user = build_prompt(sim.goal, obs["window"], obs["elements"], history, task_summary=summary,
                        last_result="" if sim.last.startswith("Nenhuma ação") else sim.last)
    return [{"role": "system", "content": PLANNER_SYSTEM}, {"role": "user", "content": user}]


def run_case(client: httpx.Client, url: str, model: str, case: dict, settings: dict) -> dict:
    sim = SCENARIOS[case["id"]]()
    history: list[str] = []
    transcript, invalid, wasted, misdone, same = [], 0, 0, 0, 0
    started = time.perf_counter()
    row = {"passed": False, "format_valid": True, "pure_json": False, "decision": None,
           "error_kind": "semantic", "errors": [], "raw_response": None}
    tokens = 0
    for step in range(1, MAX_STEPS + 1):
        payload = bench.build_payload(model, step_messages(sim, history), settings, True)
        response = client.post(f"{url}/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()
        tokens += (data.get("usage") or {}).get("completion_tokens", 0) or 0
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            row.update(error_kind="truncated", errors=[f"passo {step}: saída truncada"])
            break
        try:
            raw, pure = bench.parse_response(choice["message"].get("content", ""))
            row["pure_json"] = pure or row["pure_json"]
            decision = bench.validate_decision(raw)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            invalid += 1
            transcript.append({"step": step, "invalid": str(exc)[:200]})
            sim.last = f"Invalid planner output: {str(exc)[:160]}"
            if invalid > MAX_INVALID:
                row.update(error_kind="format", format_valid=False,
                           errors=[f"passo {step}: {invalid} respostas inválidas"])
                break
            continue
        tag = label(decision)
        same = same + 1 if history and history[-1] == tag else 1
        history.append(tag)
        if decision["type"] == "done":
            ok, why = sim.done(decision)
            transcript.append({"step": step, "action": tag, "result": why})
            if ok:
                row["passed"], row["error_kind"] = True, ""
                break
            misdone += 1
            sim.last = why
            if why.startswith("falha"):
                row["errors"] = [why]
                break
        else:
            result = sim.apply(decision)
            wasted += result.startswith("no visible effect")
            sim.last = result
            transcript.append({"step": step, "action": tag, "result": result})
        if same >= MAX_SAME_ACTION:
            row["errors"] = [f"passo {step}: {tag} repetido {same}x"]
            break
    else:
        row["errors"] = [f"limite de {MAX_STEPS} passos sem concluir"]
    if not row["passed"] and not row["errors"]:
        row["errors"] = ["falhou"]
    row.update(elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
               steps=len(transcript), wasted_actions=wasted, misdone=misdone, invalid=invalid,
               transcript=transcript, usage={"completion_tokens": tokens}, final=sim.last)
    return row


def extra_summary(rows: list[dict]) -> dict:
    won = [r for r in rows if r.get("passed")]
    out = {"Cenários concluídos": f"{len(won)}/{len(rows)}"}
    if won:
        out["Passos nos concluídos (média)"] = round(sum(r["steps"] for r in won) / len(won), 1)
    out["Ações sem efeito (total)"] = sum(r.get("wasted_actions", 0) for r in rows)
    out["done prematuro (total)"] = sum(r.get("misdone", 0) for r in rows)
    out["Respostas inválidas (total)"] = sum(r.get("invalid", 0) for r in rows)
    return out


SUITE = bench.Suite(
    name="trajectory", title="Trajetórias multi-passo em simulador de desktop",
    scope=("Cenários determinísticos de vários passos; o modelo recebe o resultado de cada ação.",
           f"Prompt real do planner; limite de {MAX_STEPS} passos, {MAX_INVALID} respostas "
           "inválidas e repetição 3x.",
           "É um simulador: não prova E2E nem uso do desktop real."),
    load=load_cases, messages=lambda case: [], evaluate=lambda c, d, ms: {},
    system=PLANNER_SYSTEM, quick=lambda cases: cases, use_schema=True,
    run_case=run_case, extra_summary=extra_summary,
    fingerprint=lambda cases: json.dumps([c["id"] for c in cases]))
