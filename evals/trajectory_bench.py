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
from guards import goal_ambiguity, goal_conflict
from planner import (
    PLAN_SCHEMA,
    PLAN_SYSTEM,
    MiniCPMPlanner,
    build_prompt,
    build_system,
    planner_json_schema,
    strip_type_prefix,
)
from recipes import recipes_for

MAX_STEPS = 12
MAX_INVALID = 2
MAX_SAME_ACTION = 3


def label(decision: dict) -> str:
    t = decision["type"]
    arg = next((decision[k] for k in ("app", "target", "text", "key", "keys", "instruction",
                                      "perception", "skill") if decision.get(k)), "")
    if t == "fill":
        arg = f"{decision.get('target', '')}={decision.get('text', '')}"
    return f"{t}({str(arg)[:60]})" if arg else t


class Veto(Exception):
    """Ação recusada pelo Python antes de qualquer efeito (como o RuntimeError do loop real)."""


class Sim:
    """Estado + regras de um cenário. Subclasses implementam `apply`."""
    goal = ""
    category = "trajetoria"
    ALIASES: dict[str, str] = {}

    def __init__(self):
        self.window = ""
        self.elements: list[str] = []
        self.evidence: dict[str, str] = {}  # chave interna -> descrição, na ordem de aquisição
        self.flags: set[str] = set()
        self.required: list[str] = []
        self.last = "Nenhuma ação anterior."
        self.answer = ""

    def pending(self):
        return [r for r in self.required if r not in self.evidence]

    def evidence_ids(self) -> dict[str, str]:
        """`E1`.. -> chave interna, na ordem em que as evidências foram confirmadas."""
        return {f"E{i + 1}": key for i, key in enumerate(self.evidence)}

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
            return self.vetted(want) if want in names else None
        if d["type"] == "visual_action":
            text = " " + " ".join(re.findall(r"\w+|[+=]", d["instruction"].casefold())) + " "
            hits = [n for n in names if f" {n} " in text]
            hits += [self.ALIASES[a] for a in self.ALIASES
                     if f" {a} " in text and self.ALIASES[a] in names]
            return self.vetted(max(hits, key=len)) if hits else None
        return None

    def vetted(self, name: str) -> str:
        """Mesmo veto do loop real: não adivinha entre itens que o pedido não distingue."""
        shown = [strip_type_prefix(e.split("=", 1)[0]) for e in self.elements]
        said = f"{self.goal} {self.answer}"  # a resposta do humano esclarece o pedido
        full = next((x for x in shown if x.casefold() == name), name)
        wanted = goal_conflict(said, full, shown)
        if wanted:
            raise Veto(f"o pedido (com a resposta do humano) indica {wanted[:3]}, não {name!r}; "
                       "clique no item indicado")
        rivals = goal_ambiguity(said, full, shown)
        if rivals:
            raise Veto(f"alvo ambíguo pelo pedido: {name!r} e {rivals[:3]} casam com o pedido e "
                       "ele não diz qual; pergunte ao humano com ask, não escolha")
        return name

    def handle_common(self, d: dict) -> str | None:
        """Ferramentas que valem em qualquer tela; None = deixa o cenário decidir."""
        if d["type"] == "perceive":
            spec = d.get("perception") or ""
            if spec.lower().startswith("wait:"):
                want = spec[5:].strip().casefold()
                if want and want in " ".join(self.visible_names() + [self.window.casefold()]):
                    return f"{spec[5:].strip()!r} apareceu na tela"
                return (f"{spec[5:].strip()!r} NÃO apareceu em 8 s; não repita wait: mude de "
                        "abordagem")
            return f"leitura: janela {self.window!r}; elementos {', '.join(self.elements)}"
        if d["type"] == "answer":
            return "answer registrado; isso não conclui a tarefa (use done com evidências)"
        if d["type"] == "sequence":
            steps = [x for x in (d.get("steps") or []) if isinstance(x, dict)]
            results = [self.apply(step) for step in steps]
            if not results or all(r.startswith("no visible effect") for r in results):
                return self.no_effect()
            return "; ".join(results)
        if d["type"] == "click_text":  # texto visível = mesmo alvo que o clique visual
            return self.apply({"type": "visual_action", "instruction": d.get("text") or ""})
        if d["type"] == "fill":
            return self.fill(strip_type_prefix(d.get("target") or "").casefold(),
                             d.get("text") or "")
        return None

    def fill(self, name: str, text: str) -> str:
        return self.no_effect("campo não encontrado")

    def apply(self, d: dict) -> str:  # pragma: no cover - contrato
        raise NotImplementedError

    def done(self, d: dict) -> tuple[bool, str]:
        if self.pending():
            return False, f"done recusado: pendências {self.pending()}"
        known = self.evidence_ids()
        given = {str(e).strip().upper() for e in (d.get("evidences") or [])}
        if not given or not given <= set(known):
            return False, "done recusado: evidências ausentes ou desconhecidas (cite E1, E2…)"
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

    def fill(self, name, text):
        if name == "editor" and not self.dialog:
            self.typed = text
            if "ola mundo" in self.typed.casefold():
                self.evidence["ev-texto"] = "texto digitado"
            self.elements = self.base_elements()
            return "Campo Editor preenchido."
        if name == "nome do arquivo" and self.dialog:
            self.fname = text
            self.elements = [f"Edit:Nome do arquivo={self.fname}", "Button:Salvar",
                             "Button:Cancelar"]
            return "Nome digitado."
        return self.no_effect("campo não encontrado")

    def confirm_save(self):
        if self.fname.strip().casefold() != "nota.txt":
            return "Aviso: nome do arquivo vazio ou diferente do pedido."
        self.dialog, self.window = False, "nota.txt - Notepad"
        self.elements = [f"Edit:Editor={self.typed}"]
        self.evidence["ev-arquivo"] = "arquivo salvo"
        return "Arquivo salvo."

    def open_dialog(self):
        self.dialog, self.menu, self.window = True, False, "Salvar como"
        self.elements = ["Edit:Nome do arquivo", "Button:Salvar", "Button:Cancelar"]
        return "Diálogo Salvar como aberto."

    def apply(self, d):
        common = self.handle_common(d)
        if common is not None:
            return common
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
        if t == "press_key" and d["key"].lower() == "enter" and self.dialog:
            return self.confirm_save()
        if t == "open_app" and d["app"] == "notepad":
            return "O Bloco de Notas já está ativo; nada mudou."
        if t == "save_as" and self.dialog:
            self.fname = (d.get("text") or "").strip()
            return self.confirm_save()
        if t == "save_as" and not self.dialog:
            name = (d.get("text") or "").strip()
            if name.casefold() != "nota.txt":
                self.window = f"{name} - Notepad"
                return f"Arquivo salvo como {name} (o pedido era nota.txt)."
            self.fname = name
            return self.confirm_save()
        if t == "type_text" and self.dialog:
            self.fname = d["text"]
            self.elements = [f"Edit:Nome do arquivo={self.fname}", "Button:Salvar",
                             "Button:Cancelar"]
            return "Nome digitado."
        name = self.clicked(d)
        if name == "arquivo" and not self.dialog:
            self.menu, self.elements = True, self.MENU
            return "Menu Arquivo aberto."
        if name in ("salvar", "salvar como") and self.menu:
            return self.open_dialog()
        if name == "salvar" and self.dialog:
            return self.confirm_save()
        if name in ("nome do arquivo", "editor"):
            return f"Campo {d.get('target') or name} focado."
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

    def fill(self, name, text):
        if name == "barra de endereço" and (self.tab or "barra de endereço" in
                                             " ".join(self.visible_names())):
            self.focused, self.typed = True, text
            self.elements = [f"Edit:Barra de endereço={text}", "Pane:Página"]
            return "URL digitada na barra."
        return self.no_effect("campo não encontrado")

    def new_tab(self):
        self.tab, self.focused, self.typed = True, True, ""
        self.window = "Nova guia - Google Chrome"
        self.elements = ["Edit:Barra de endereço", "Pane:Página"]
        self.evidence["ev-aba"] = "nova aba aberta"
        return "Nova aba aberta."

    def apply(self, d):
        common = self.handle_common(d)
        if common is not None:
            return common
        t = d["type"]
        if t == "hotkey" and keys_of(d) == "ctrl+t" and not self.tab:
            return self.new_tab()
        if self.clicked(d) == "nova guia" and not self.tab:
            return self.new_tab()
        if t == "hotkey" and keys_of(d) in ("ctrl+l", "f6", "alt+d"):
            self.focused = True
            return "Barra de endereço focada."
        if self.clicked(d) == "barra de endereço":
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
        common = self.handle_common(d)
        if common is not None:
            return common
        if d["type"] == "ask":
            self.asked = True
            self.answer = "Ana trabalho"
            return "Resposta do usuário: Ana trabalho"
        name = self.clicked(d)
        if name in ("ana pessoal", "ana trabalho"):
            if not self.asked:
                self.flags.add("chute")
                return "Perfil aberto, mas a escolha era ambígua e não foi confirmada."
            if name == "ana trabalho":
                self.window, self.elements = "Ana trabalho", ["Text:Bem-vinda, Ana"]
                self.evidence["ev-perfil"] = "perfil aberto"
                return "Perfil Ana trabalho aberto."
            return "Perfil errado aberto (o usuário pediu Ana trabalho)."
        return self.no_effect()

    def done(self, d):
        if "chute" in self.flags:
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
        common = self.handle_common(d)
        if common is not None:
            return common
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
    ALIASES = {"accept all": "aceitar todos", "accept": "aceitar todos",
               "accept cookies": "aceitar todos", "reject": "rejeitar",
               "buy now": "comprar agora", "buy": "comprar agora"}

    def __init__(self):
        super().__init__()
        self.window = "Loja Azul - Google Chrome"
        self.elements = ["Button:Aceitar todos", "Button:Rejeitar", "Button:Comprar agora"]
        self.required, self.banner = ["ev-cookies", "ev-compra"], True

    def apply(self, d):
        common = self.handle_common(d)
        if common is not None:
            return common
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


class FormFill(Sim):
    goal = "Preencha o formulário com nome Maria Souza e e-mail maria@exemplo.com e envie."
    category = "formulario"
    VALUES = {"nome": ("Maria Souza", "ev-nome"), "e-mail": ("maria@exemplo.com", "ev-email")}

    def __init__(self):
        super().__init__()
        self.window = "Cadastro - Google Chrome"
        self.fields = {"nome": "", "e-mail": ""}
        self.required = ["ev-nome", "ev-email", "ev-envio"]
        self.focus = ""
        self.refresh()

    def refresh(self):
        def shown(label: str, field: str) -> str:
            return f"Edit:{label}" + (f"={self.fields[field]}" if self.fields[field] else "")

        self.elements = [shown("Nome", "nome"), shown("E-mail", "e-mail"), "Button:Enviar"]

    def put(self, field: str, text: str) -> str:
        self.fields[field] = text
        want, key = self.VALUES[field]
        if text.strip().casefold() == want.casefold():
            self.evidence[key] = f"{field} preenchido"
        else:
            self.evidence.pop(key, None)
        self.refresh()
        return f"Campo {field} preenchido."

    def fill(self, name, text):
        if name not in self.fields:
            return self.no_effect("campo não encontrado")
        return self.put(name, text)

    def apply(self, d):
        common = self.handle_common(d)
        if common is not None:
            return common
        if d["type"] == "type_text":
            if not self.focus:
                return self.no_effect("nenhum campo de texto focado")
            return self.put(self.focus, d["text"])
        if d["type"] == "hotkey" and keys_of(d) == "ctrl+a":
            return "Conteúdo do campo selecionado." if self.focus else self.no_effect()
        name = self.clicked(d)
        if name in self.fields:
            self.focus = name
            return f"Campo {name} focado."
        if name == "enviar":
            if "ev-nome" in self.evidence and "ev-email" in self.evidence:
                self.window, self.elements = "Obrigado - Google Chrome", ["Text:Cadastro enviado"]
                self.evidence["ev-envio"] = "formulário enviado"
                return "Formulário enviado."
            return "Aviso: nome ou e-mail faltando ou incorreto."
        return self.no_effect()


class OverwriteSave(NotepadSave):
    goal = ("Digite 'ola mundo' no Bloco de Notas e salve como nota.txt, substituindo o arquivo "
            "que já existe.")
    category = "modal-confirmacao"

    def __init__(self):
        super().__init__()
        self.modal = False

    def confirm_save(self):
        if self.fname.strip().casefold() != "nota.txt":
            return super().confirm_save()
        self.modal, self.dialog = True, False
        self.window = "Confirmar Salvar como"
        self.elements = ["Text:nota.txt já existe. Deseja substituí-lo?", "Button:Sim",
                         "Button:Não"]
        return "O arquivo já existe: confirmação de substituição aberta."

    def apply(self, d):
        if not self.modal:
            return super().apply(d)
        common = self.handle_common(d)
        if common is not None:
            return common
        name = self.clicked(d)
        if name == "sim":
            self.modal, self.window = False, "nota.txt - Notepad"
            self.elements = [f"Edit:Editor={self.typed}"]
            self.evidence["ev-arquivo"] = "arquivo salvo (substituído)"
            return "Arquivo substituído e salvo."
        if name in ("não", "nao"):
            self.modal, self.dialog = False, True
            self.window = "Salvar como"
            self.elements = ["Edit:Nome do arquivo", "Button:Salvar", "Button:Cancelar"]
            return "Substituição recusada; diálogo Salvar como de volta."
        if d["type"] == "press_key" and d["key"].lower() == "enter":
            return self.apply({"type": "uia_click", "target": "Sim"})
        return self.no_effect("a confirmação de substituição bloqueia a janela")


class WrongWindow(Sim):
    goal = "Digite 'ola' no Bloco de Notas."
    category = "janela-errada"

    def __init__(self):
        super().__init__()
        self.window = "Calculadora"
        self.elements = ["Text:Exibição=0", "Button:1", "Button:2", "Button:Mais", "Button:Igual"]
        self.required = ["ev-texto"]

    def apply(self, d):
        common = self.handle_common(d)
        if common is not None:
            return common
        t = d["type"]
        if t == "open_app" and d["app"] == "notepad" and "Notepad" not in self.window:
            self.window, self.elements = "Sem título - Notepad", ["Edit:Editor"]
            return "Bloco de Notas aberto e em primeiro plano."
        if t == "focus_window":
            return self.no_effect("nenhuma janela com esse título está aberta")
        if t == "type_text":
            if "Notepad" in self.window:
                if "ola" in d["text"].casefold():
                    self.evidence["ev-texto"] = "texto digitado"
                self.elements = [f"Edit:Editor={d['text']}"]
                return "Texto digitado no editor."
            return self.no_effect("a Calculadora não aceita esse texto")
        return self.no_effect()


# --- HOLD-OUT: escritos DEPOIS de ajustar prompt/exemplos; nunca usados p/ ajustar nada ---
class SearchBox(Sim):
    goal = "Pesquise por gatos e abra o primeiro resultado."
    category = "holdout-busca"

    def __init__(self):
        super().__init__()
        self.window = "Buscador - Google Chrome"
        self.elements = ["Edit:Pesquisar", "Button:Buscar"]
        self.required = ["ev-busca", "ev-primeiro"]
        self.focus, self.query, self.results = False, "", False

    def run_query(self):
        if "gatos" not in self.query.casefold():
            return "Busca feita, mas não era por gatos."
        self.results = True
        self.window = "Resultados - Google Chrome"
        self.elements = ["Edit:Pesquisar=gatos", "Hyperlink:Gatos - Wikipédia",
                         "Hyperlink:Gatos fofos", "Hyperlink:Adote um gato"]
        self.evidence["ev-busca"] = "resultados de gatos na tela"
        return "Resultados exibidos."

    def fill(self, name, text):
        if name != "pesquisar":
            return self.no_effect("campo não encontrado")
        self.query, self.focus = text, True
        return "Campo Pesquisar preenchido."

    def apply(self, d):
        common = self.handle_common(d)
        if common is not None:
            return common
        if d["type"] == "type_text":
            if not self.focus:
                return self.no_effect("nenhum campo de texto focado")
            self.query = d["text"]
            return "Texto digitado."
        if d["type"] == "press_key" and d["key"].lower() == "enter" and self.query:
            return self.run_query()
        name = self.clicked(d)
        if name == "pesquisar":
            self.focus = True
            return "Campo Pesquisar focado."
        if name == "buscar" and self.query:
            return self.run_query()
        if self.results and name == "gatos - wikipédia":
            self.window, self.elements = "Gatos - Wikipédia", ["Text:Gato doméstico"]
            self.evidence["ev-primeiro"] = "primeiro resultado aberto"
            return "Primeiro resultado aberto."
        if self.results and name in ("gatos fofos", "adote um gato"):
            self.window, self.elements = name.title(), ["Text:Outra página"]
            return "Abriu um resultado que não é o primeiro."
        return self.no_effect()


class RenameFile(Sim):
    goal = "Renomeie relatorio.txt para final.txt."
    category = "holdout-renomear"

    def __init__(self):
        super().__init__()
        self.window = "Arquivos"
        self.names = ["relatorio.txt", "notas.txt"]
        self.selected, self.editing, self.typed = "", False, ""
        self.required = ["ev-renomeado"]
        self.refresh()

    def refresh(self):
        self.elements = [f"ListItem:{n}" for n in self.names] + ["Button:Renomear"]

    def commit(self):
        if self.selected == "relatorio.txt" and self.typed.strip().casefold() == "final.txt":
            self.names = ["final.txt" if n == "relatorio.txt" else n for n in self.names]
            self.evidence["ev-renomeado"] = "relatorio.txt agora é final.txt"
        else:
            self.names = [self.typed.strip() or n if n == self.selected else n
                          for n in self.names]
        self.editing, self.selected, self.typed = False, "", ""
        self.refresh()
        return "Renomeado."

    def apply(self, d):
        common = self.handle_common(d)
        if common is not None:
            return common
        t = d["type"]
        if self.editing:
            if t == "type_text":
                self.typed = d["text"]
                return "Novo nome digitado."
            if t == "press_key" and d["key"].lower() == "enter":
                return self.commit()
            if self.clicked(d) == "renomear" and self.typed:  # clicar de novo também confirma
                return self.commit()
            return self.no_effect("o nome está em edição")
        name = self.clicked(d)
        if name in self.names:
            self.selected = name
            return f"{name} selecionado."
        if (name == "renomear" or (t == "press_key" and d["key"].lower() == "f2")) \
                and self.selected:
            self.editing = True
            return "Nome em edição."
        if name == "renomear":
            return self.no_effect("nenhum arquivo selecionado")
        return self.no_effect()


class ToggleSetting(Sim):
    goal = "Ative o modo escuro e aplique."
    category = "holdout-configuracao"

    def __init__(self):
        super().__init__()
        self.window = "Configurações"
        self.on = {"modo escuro": False, "notificações": True}
        self.required = ["ev-modo", "ev-aplicado"]
        self.refresh()

    def refresh(self):
        def state(name: str) -> str:
            return "ligado" if self.on[name] else "desligado"

        self.elements = [f"CheckBox:Modo escuro={state('modo escuro')}",
                         f"CheckBox:Notificações={state('notificações')}", "Button:Aplicar"]

    def apply(self, d):
        common = self.handle_common(d)
        if common is not None:
            return common
        name = self.clicked(d)
        if name in self.on:
            self.on[name] = not self.on[name]
            self.refresh()
            if name == "modo escuro":
                if self.on[name]:
                    self.evidence["ev-modo"] = "modo escuro ligado"
                else:
                    self.evidence.pop("ev-modo", None)
            return f"{name} agora {'ligado' if self.on[name] else 'desligado'}."
        if name == "aplicar":
            if self.on["modo escuro"] and self.on["notificações"]:
                self.evidence["ev-aplicado"] = "configurações aplicadas"
                return "Configurações aplicadas."
            return "Aplicado, mas o resultado não é o pedido."
        return self.no_effect()


SCENARIOS = {cls.__name__: cls for cls in
             (NotepadSave, ChromeUrl, AmbiguousProfile, CalcSum, CookieBanner,
              FormFill, OverwriteSave, WrongWindow, SearchBox, RenameFile, ToggleSetting)}
TUNED = ("NotepadSave", "ChromeUrl", "AmbiguousProfile", "CalcSum", "CookieBanner", "FormFill",
         "OverwriteSave", "WrongWindow")  # os demais são HOLD-OUT (nunca usados p/ ajustar)


def load_cases() -> list[dict]:
    return [{"id": name, "category": cls.category, "goal": cls().goal}
            for name, cls in SCENARIOS.items()]


REQ_LABELS = {
    "ev-texto": "digitar o texto no editor", "ev-arquivo": "salvar o arquivo como nota.txt",
    "ev-aba": "abrir uma nova aba", "ev-pagina": "carregar example.org na nova aba",
    "ev-perfil": "abrir o perfil de Ana", "ev-resultado": "mostrar o resultado no visor",
    "ev-cookies": "aceitar os cookies", "ev-compra": "clicar em Comprar agora",
    "ev-nome": "preencher o nome Maria Souza", "ev-email": "preencher o e-mail",
    "ev-envio": "enviar o formulário", "ev-busca": "pesquisar por gatos",
    "ev-primeiro": "abrir o primeiro resultado",
    "ev-renomeado": "renomear relatorio.txt para final.txt",
    "ev-modo": "ativar o modo escuro", "ev-aplicado": "aplicar as configurações",
}


BENCH_ONLY_FEATURES = ("nochecklist",)


def step_messages(sim: Sim, history: list[str], features: tuple[str, ...] = (),
                  requirements: list[str] | None = None) -> list[dict]:
    """Prompt de um passo no formato do planner real: estado compacto com o checklist e
    "evidências confirmadas: E1=…". Checklist: `plan` = gerado pelo modelo (requisitos fixos, como
    o loop real); `nochecklist` = nenhum; padrão = pendências exatas do simulador (limite
    superior, o loop real não as tem)."""
    parts = []
    if "plan" in features:
        if requirements:
            parts.append("requisitos do pedido: " + "; ".join(
                f"({i}) {r[:80]}" for i, r in enumerate(requirements, 1)))
    elif "nochecklist" not in features:
        pend = [REQ_LABELS.get(k, k) for k in sim.pending()]
        if pend:
            parts.append("pendências: " + "; ".join(pend))
    ids = sim.evidence_ids()
    if ids:
        parts.append("evidências confirmadas: " + "; ".join(
            f"{eid}={sim.evidence[key]}" for eid, key in ids.items()))
    obs = sim.observation()
    recipes = recipes_for(sim.window) if "recipes" in features else ""
    user = build_prompt(sim.goal, obs["window"], obs["elements"], history,
                        task_summary=" | ".join(parts) or "(sem estado ainda)",
                        last_result="" if sim.last.startswith("Nenhuma ação") else sim.last,
                        recipes=recipes)
    return [{"role": "system", "content": build_system(features)},
            {"role": "user", "content": user}]


def make_run_case(features: tuple[str, ...] = ()):
    planner_feats = tuple(f for f in features if f not in BENCH_ONLY_FEATURES)
    probe = MiniCPMPlanner(features=planner_feats)

    def plan(client, url, model, settings, goal: str) -> list[str]:
        payload = bench.build_payload(
            model, [{"role": "system", "content": PLAN_SYSTEM}, {"role": "user", "content": goal}],
            settings, True, PLAN_SCHEMA)
        payload["max_tokens"] = min(payload["max_tokens"], 200)
        try:
            response = client.post(f"{url}/chat/completions", json=payload)
            response.raise_for_status()
            raw, _ = bench.parse_response(response.json()["choices"][0]["message"]["content"])
            return [str(x).strip()[:120] for x in raw.get("requirements", []) if str(x).strip()]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return []

    def step_schema(sim: Sim) -> dict:
        kwargs = probe.schema_kwargs(list(sim.elements))
        if "dynschema" in features:
            kwargs["evidence_ids"] = list(sim.evidence_ids())
        return planner_json_schema(**kwargs)

    def run_case(client: httpx.Client, url: str, model: str, case: dict, settings: dict) -> dict:
        sim = SCENARIOS[case["id"]]()
        requirements = plan(client, url, model, settings, sim.goal) if "plan" in features else None
        history: list[str] = []
        transcript, invalid, wasted, misdone, same = [], 0, 0, 0, 0
        started = time.perf_counter()
        row = {"passed": False, "format_valid": True, "pure_json": False, "decision": None,
               "error_kind": "semantic", "errors": [], "raw_response": None}
        tokens = 0
        for step in range(1, MAX_STEPS + 1):
            payload = bench.build_payload(
                model, step_messages(sim, history, features, requirements), settings, True,
                step_schema(sim) if planner_feats else None)
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
                try:
                    result = sim.apply(decision)
                except Veto as veto:
                    result = f"vetado: {veto}"
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
                   steps=len(transcript), wasted_actions=wasted, misdone=misdone,
                   invalid=invalid, transcript=transcript, usage={"completion_tokens": tokens},
                   final=sim.last)
        return row

    return run_case


run_case = make_run_case()  # sem recursos: prompt/schema de produção de base


def extra_summary(rows: list[dict]) -> dict:
    won = [r for r in rows if r.get("passed")]
    out = {"Cenários concluídos": f"{len(won)}/{len(rows)}"}
    if won:
        out["Passos nos concluídos (média)"] = round(sum(r["steps"] for r in won) / len(won), 1)
    out["Ações sem efeito (total)"] = sum(r.get("wasted_actions", 0) for r in rows)
    out["done prematuro (total)"] = sum(r.get("misdone", 0) for r in rows)
    out["Respostas inválidas (total)"] = sum(r.get("invalid", 0) for r in rows)
    return out


def trajectory_suite(features: tuple[str, ...] = ()) -> bench.Suite:
    feats = tuple(features)
    return bench.Suite(
        name="trajectory", title="Trajetórias multi-passo em simulador de desktop",
        scope=("Cenários determinísticos de vários passos; o modelo recebe o resultado de cada "
               "ação." + (f" Recursos do planner: {', '.join(feats)}." if feats else ""),
               f"Prompt real do planner; limite de {MAX_STEPS} passos, {MAX_INVALID} respostas "
               "inválidas e repetição 3x. A lista de pendências vem do simulador.",
               "É um simulador: não prova E2E nem uso do desktop real."),
        load=load_cases, messages=lambda case: [], evaluate=lambda c, d, ms: {},
        system=build_system(feats), quick=lambda cases: cases, use_schema=True,
        run_case=make_run_case(feats) if feats else run_case, extra_summary=extra_summary,
        fingerprint=lambda cases: json.dumps([c["id"] for c in cases]))


SUITE = trajectory_suite()
