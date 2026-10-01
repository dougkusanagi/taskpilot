"""Loop observe -> decide -> act -> verify -> repeat (2 modelos).

Arquitetura:
  MiniCPM5-2B = pensar (só texto compacto; nunca recebe screenshots,
                nunca emite coordenadas)
  Vocaela-2-500M = enxergar (screenshot + instrução curta -> ação visual 0..1)
  Python = executar, OBSERVAR e VETAR — nunca escolher a ação.

Ordem de decisão (barata primeiro), todas vindas do planner:
  1. native tool      (open/focus/type decididos pelo planner)
  2. UI Automation    (uia_click por NOME; Vocaela nunca é chamado à toa)
  3. Vocaela          (SÓ quando o elemento não está na accessibility tree)

Guard-rails determinísticos (Python veta/observa, não escolhe):
  - bootstrap da janela do app pedido (open_app é ferramenta; um 1B não
    abre processo) — roda ANTES de gastar uma chamada ao planner;
  - guarda anti-janela-errada e anti-repetição: devolvem `last_error` ao
    planner em vez de executar;
  - veto de coordenada fora da tela/janela (actions/_resolve_uia).

Sem modelos online = erro honesto, sem fallback programático.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import psutil

import safety
import server
import state as statemod
import uia
import verification as verif
from actions import execute
from obs import capture_for_vision
from planner import MiniCPMPlanner, PlannerDecision, strip_type_prefix
from schemas import Action, Decision
from uia import active_window_snapshot, focused_value
from vocaela import VocaelaAdapter, visual_to_action

LOG = Path("run.jsonl")

CALC_WORDS = ("calculadora", "calculator")
MAX_RETRIES = 3  # erros de decisão/tool consecutivos antes de "stuck"

_JUNK_TYPES = {"window", "titlebar", "menubar"}
_CHROME_PREFIXES = (
    "minimizar ",
    "maximizar ",
    "restaurar ",
    "fechar ",
    "minimize ",
    "maximize ",
    "restore ",
    "close ",
)
# número-por-extenso PT+EN (UIA da calculadora Win expõe "Sete", não "7")
_DIGIT_WORDS = {
    "0": ("zero",),
    "1": ("um", "one"),
    "2": ("dois", "two"),
    "3": ("tres", "três", "three"),
    "4": ("quatro", "four"),
    "5": ("cinco", "five"),
    "6": ("seis", "six"),
    "7": ("sete", "seven"),
    "8": ("oito", "eight"),
    "9": ("nove", "nine"),
}
_WORD_DIGIT = {w: d for d, ws in _DIGIT_WORDS.items() for w in ws}

# §5.5: controles interativos primeiro (no Edge os 40 primeiros da árvore
# são quase só chrome; o conteúdo web ficava fora do prompt).
_INTERACTIVE_TYPES = {
    "button",
    "edit",
    "hyperlink",
    "menuitem",
    "listitem",
    "tabitem",
    "checkbox",
    "radiobutton",
    "combobox",
    "spinner",
    "splitbutton",
    "treeitem",
    "thumb",
    "slider",
}


def format_ui_names(items: list[dict], limit: int = 40) -> list[str]:
    """`tipo:nome` p/ o planner, interativos primeiro (puro, testável).

    Ex.: "Button:7", "Edit:Pesquisar". Sem coordenadas (o planner não vê
    a tela). Nomes >60 chars são os mesmos que `_resolve_uia` ignora.
    R2: sufixo `="valor"` quando o elemento expõe texto (preço, campo).
    """

    def label(it: dict) -> str | None:
        name = (it.get("name") or "").strip()
        if not name:
            return None
        ctype = (it.get("type") or "").strip() or "?"
        base = f"{ctype}:{name[:60]}"
        val = " ".join(str(it.get("value") or "").split())
        if val:
            base += f'="{val[:40]}"'
        return base

    ordered = sorted(
        items,
        key=lambda it: 0 if (it.get("type") or "").strip().lower() in _INTERACTIVE_TYPES else 1,
    )
    out: list[str] = []
    for it in ordered:
        lab = label(it)
        if lab:
            out.append(lab)
        if len(out) >= limit:
            break
    return out


def _log(obj: dict) -> None:
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _has_any(ins: str, words: tuple[str, ...]) -> bool:
    ins = ins.lower()
    return any(w in ins for w in words)


# --- guard: bootstrap do app pedido (ferramenta determinística, sem IA) --------
def _browser_want(low: str) -> tuple[str, str, str] | None:
    """(open_target, app_word, focus_hint) do browser pedido, ou None.

    Puro, testável. Chrome/Edge/Brave entram no bootstrap como notepad/calc:
    o 1B sozinho abre processo errado (focou o PowerShell) e nunca chega ao
    `open_app` — o run de 19/09 provou.
    """
    import re

    if "brave" in low:
        return ("brave", "brave", "brave")
    if "msedge" in low or "microsoft edge" in low or re.search(r"\bedge\b", low):
        return ("msedge", "edge", "edge||msedge")
    if "chrome" in low:
        return ("chrome", "chrome", "chrome")
    return None


def _app_bootstrap(
    instruction: str, step: int, active_lower: str, dry_run: bool = False
) -> Action | None:
    """Garante a JANELA certa aberta antes das decisões por modelo.

    open_app é ferramenta (o MiniCPM não abre processo), por isso estes steps
    fixos existem e rodam ANTES do planner: ele já recebe a janela certa no
    estado, e a partir daí tudo é IA. Só abrir app + confirmar foco.
    """
    low = instruction.lower()
    browser = _browser_want(low)
    if browser is not None:
        want, app_word, hint = browser
    else:
        want = (
            "notepad.exe"
            if any(w in low for w in ("notepad", "bloco de notas"))
            else "calc.exe"
            if _has_any(low, CALC_WORDS)
            else None
        )
        hint = "bloco de notas||notepad" if want == "notepad.exe" else "calculadora||calculator"
        app_word = "notepad" if want == "notepad.exe" else "calcul" if want else ""
    if want is None:
        return None  # resto: planner resolve (focus/visual)
    if app_word and app_word in active_lower:
        return None  # janela certa já ativa: mão p/ os modelos
    if dry_run:
        # Dry-run F0: bloqueia TODOS os efeitos, inclusive foco/abertura.
        return Action(type="wait", ms=300)
    from tools import focus_window

    if browser is not None:
        # Focus-first: usuário quase sempre já tem janela do browser aberta
        # (no perfil dele). Abrir nova cai no seletor de perfil — adivinhar
        # entre perfis de pessoas diferentes é sensível; focar a existente
        # evita o picker. Só abre se não houver nenhuma.
        if focus_window(hint, timeout=2.0):
            return Action(type="wait", ms=300)
        if step == 0:
            return Action(type="open", target=want)
        focus_window(hint, timeout=3.0)
        return Action(type="wait", ms=300)
    if step == 0:
        return Action(type="open", target=want)
    focus_window(hint, timeout=3.0)
    return Action(type="wait", ms=300)


# --- UIA por nome ------------------------------------------------------------
def _resolve_uia(
    items: list[dict], target: str, wrect: tuple | None, state_title: str = ""
) -> tuple[Action | None, str]:
    """Como `_resolve_uia_exact`, tolerando o prefixo de tipo (`Button:Salvar` -> `Salvar`).

    O nome literal tem prioridade; só em miss simples tenta sem o prefixo. Ambíguo continua
    ambíguo (nunca adivinha).
    """
    result = _resolve_uia_exact(items, target, wrect, state_title)
    if result[0] is None and not result[1]:
        bare = strip_type_prefix(target)
        if bare != (target or "").strip():
            return _resolve_uia_exact(items, bare, wrect, state_title)
    return result


def _resolve_uia_exact(
    items: list[dict], target: str, wrect: tuple | None, state_title: str = ""
) -> tuple[Action | None, str]:
    """Encontra elemento pelo NOME na janela ativa -> (click no centro, "").

    R2: alvo ambíguo NUNCA resolve por primeiro match — retorna
    (None, "ambíguo: ...") p/ o planner desambiguar (contexto ou visão).
    (None, "") = miss simples. Nunca clica fora da janela ativa, em chrome
    (minimizar/fechar) nem na própria janela.
    """
    want = (target or "").strip().lower()
    if not want:
        return None, ""
    # "7" casa "Sete" e vice-versa
    wants = {want}
    if want in _DIGIT_WORDS:
        wants |= set(_DIGIT_WORDS[want])
    if want in _WORD_DIGIT:
        wants.add(_WORD_DIGIT[want])

    def _tier(nl: str) -> int:
        if nl in wants:
            return 0
        for w in wants:
            if nl.startswith(w):
                return 1
            if w in nl:
                return 2
        return -1

    tiers: dict[int, list[dict]] = {0: [], 1: [], 2: []}
    for it in items:
        name = (it.get("name") or "").strip()
        if not name or len(name) > 60:
            continue
        nl = name.lower()
        if nl.startswith(_CHROME_PREFIXES):
            continue
        if (it.get("type") or "").lower() in _JUNK_TYPES:
            continue
        if state_title and nl == state_title.lower():
            continue
        t = _tier(nl)
        if t >= 0:
            tiers[t].append(it)
    cands: list[dict] = tiers[0] or tiers[1] or tiers[2]
    if not cands:
        return None, ""
    seen: dict[tuple, dict] = {}
    for c in cands:
        b = tuple(c.get("bounds") or [0, 0, 0, 0])
        seen.setdefault(b, c)
    if len(seen) > 1:
        # Mesmo nome em lugares distintos: adivinhar é clicar no escuro.
        labels = "; ".join(
            f"{c.get('type', '?')}:{(c.get('name') or '')[:30]}"
            f"@{c.get('context', '') or '?'}"
            for _, c in sorted(seen.items())[:4]
        )
        return None, (
            f"alvo ambíguo: {len(seen)} candidatos p/ {target!r} "
            f"({labels}); uia_click exige nome único — desambigue pelo "
            "contexto entre parênteses ou use visual_action"
        )
    hit = next(iter(seen.values()))
    b = hit.get("bounds") or [0, 0, 0, 0]
    cx, cy = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
    if wrect is not None and not (wrect[0] <= cx <= wrect[2] and wrect[1] <= cy <= wrect[3]):
        return None, ""  # rect fantasma fora da janela -> trata como miss
    return Action(type="click", x=cx, y=cy), ""


def _planner_to_action(dec: PlannerDecision) -> Action | None:
    """Mapeia decisão nativa do planner -> Action. uia_click/visual voltam None
    (resolvidos à parte). ValueError da whitelist sobe ao chamador."""
    t = dec.type
    if t == "open_app":
        return Action(type="open", target=dec.app or "")
    if t == "focus_window":
        return Action(type="focus", target=dec.target or "")
    if t == "type_text":
        return Action(type="type", text=dec.text or "")
    if t == "press_key":
        return Action(type="hotkey", key=dec.key or "enter")
    if t == "hotkey":
        return Action(type="hotkey", key=dec.keys or "")
    if t == "wait":
        return Action(type="wait", ms=dec.ms or 1000)
    if t == "answer":
        return Action(type="answer", text=dec.text or "")
    if t == "done":
        return Action(type="done")
    if t == "ask":
        return Action(type="ask", text=dec.text or "")
    if t == "use_skill":
        # F5: skill executa à parte; Action placeholder sem efeito físico.
        return Action(type="wait", ms=0)
    if t == "sequence":
        # F6: cai no ramo dedicado abaixo (validação de 1..3 primitivas);
        # None aqui é o que o torna alcançável (regressão 19/09: voltar
        # wait(0) executava o placeholder sem validar os steps).
        return None
    if t == "perceive":
        # R1: cai no ramo dedicado abaixo (só leitura, sem input físico).
        return None
    return None  # uia_click, visual_action


_SEQUENCE_PRIMITIVES = ("hotkey", "type_text", "press_key", "wait")


def _steps_label(acts: list) -> str:
    """Resumo curto de primitivas p/ logs e anti-repetição (puro, testável)."""
    bits = []
    for a in acts:
        if isinstance(a, dict):
            t = str(a.get("type", "?"))
            v = a.get("keys") or a.get("key") or a.get("text") or a.get("ms") or ""
            bits.append(f"{t}({str(v)[:30]})")
        else:
            v = getattr(a, "key", None) or getattr(a, "text", None) or ""
            if getattr(a, "type", "") == "wait":
                v = getattr(a, "ms", "")
            bits.append(f"{getattr(a, 'type', '?')}({str(v)[:30]})")
    return "+".join(bits)[:120]


def _sequence_to_actions(dec: PlannerDecision) -> list[Action]:
    """Valida a sequência do modelo (F6, puro, testável).

    Até 3 primitivas de teclado/espera, com pré-condições explícitas.
    Cliques que navegam e arrastes complexos exigem nova observação
    (nada de plano longo cego).
    """
    steps = dec.steps or []
    if not 1 <= len(steps) <= 3:
        raise RuntimeError(f"sequence precisa de 1..3 primitivas, veio {len(steps)}")
    out: list[Action] = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            raise RuntimeError(f"sequence[{i}] malformado: {s!r}"[:150])
        t = str(s.get("type", ""))
        if t not in _SEQUENCE_PRIMITIVES:
            raise RuntimeError(
                f"sequence[{i}] {t!r} proibido: só "
                "hotkey/type_text/press_key/wait (cliques/drags exigem "
                "nova observação)"
            )
        if t == "press_key" and "keys" in s and s.get("keys"):
            raise RuntimeError(
                f"sequence[{i}] misturou press_key com keys "
                f"({str(s.get('keys'))[:30]!r}); combinação vai em hotkey "
                "com keys, tecla simples em press_key com key"
            )
        if t == "hotkey" and not str(s.get("keys", "")).strip() and str(s.get("key", "")).strip():
            # Alias tolerado e inequívoco: hotkey com só "key".
            s = {**s, "keys": str(s.get("key", ""))}
        if t == "hotkey" and not str(s.get("keys", "")).strip():
            raise RuntimeError(f"sequence[{i}] hotkey precisa de keys")
        if t == "type_text" and not str(s.get("text", "")).strip():
            raise RuntimeError(f"sequence[{i}] type_text precisa de text")
        if t == "press_key" and not str(s.get("key", "")).strip():
            raise RuntimeError(f"sequence[{i}] press_key precisa de key")
        if t == "hotkey":
            out.append(Action(type="hotkey", key=str(s["keys"])[:60]))
        elif t == "type_text":
            out.append(Action(type="type", text=str(s["text"])[:500]))
        elif t == "press_key":
            out.append(Action(type="hotkey", key=str(s["key"])[:30]))
        else:
            out.append(Action(type="wait", ms=max(0, int(s.get("ms", 500)))))
    return out


# --- percepção read-only R1/R2 ------------------------------------------------
# O planner textual nunca vê pixels; `perceive` é como ele pede releitura
# SEM clicar/digitar. UIA + campo focado + expansão de ramo + OCR local
# (texto da captura; indisponível = fato honesto, sem backend no ambiente).
# Fatos voltam como observação (dados), nunca como evidência confirmada
# nem como instrução.
PERCEPTION_SPECS = ("uia_refresh", "read_focused", "expand:<nome>", "ocr")
PERCEPTION_BUDGET_DEFAULT = 6


def _parse_perception(spec: str) -> tuple[str, str]:
    """(base, arg) da spec de percepção. Erro cita as formas válidas."""
    s = (spec or "").strip()
    low = s.lower()
    if low in ("uia_refresh", "read_focused", "ocr"):
        return low, ""
    if low.startswith("expand:"):
        arg = s[len("expand:"):].strip()
        if arg:
            return "expand", arg
    raise RuntimeError(
        f"perceive({spec!r}) inválido: use uia_refresh, read_focused, "
        "ocr ou expand:<nome visível na árvore>"
    )


def _check_perception_budget(ctx: dict, cfg: dict) -> None:
    """Teto separado p/ percepções (F3/R1): excesso vira erro ao modelo."""
    max_p = int(cfg.get("perception", {}).get("max_perceptions", PERCEPTION_BUDGET_DEFAULT))
    if int(ctx.get("n_perceptions", 0)) >= max_p:
        raise RuntimeError(
            f"perceive recusado: limite de {max_p} releituras por run; "
            "aja com o observável (cliques/teclado), não releia."
        )


def _run_perception(
    spec: str,
    ctx: dict,
    cfg: dict,
    tm: dict,
    snapshot_fn=None,
    focused_fn=None,
    expand_fn=None,
    capture_fn=None,
    ocr_fn=None,
) -> str:
    """Releitura read-only: NENHUM input físico. Retorna fatos p/ o prompt."""
    del cfg
    base, arg = _parse_perception(spec)
    spec_norm = f"{base}:{arg}" if arg else base
    from schemas import new_id as _new_id

    obs_id = _new_id("obs")
    if base == "ocr":
        # R2 fatia 3: texto da captura atual via OCR local (ex.: preço fora
        # da árvore). Sem backend = fato honesto "indisponível", nunca erro
        # fatal nem evidência. Nenhum clique/tecla aqui.
        cap = capture_fn or capture_for_vision
        img, _origin, _full = cap()
        read = ocr_fn or __import__("ocr").read
        try:
            res = read(img)
        except Exception as e:
            res = {"ok": False, "text": "", "backend": "error",
                   "reason": f"OCR falhou: {e}"[:200]}
        if res.get("ok"):
            facts = (
                f"perceive(ocr @{obs_id}): backend={res.get('backend', '?')}; "
                f"texto={str(res.get('text', ''))[:300]!r}"
            )
        else:
            facts = (
                f"perceive(ocr @{obs_id}): OCR indisponível "
                f"(backend={res.get('backend', '?')}: "
                f"{str(res.get('reason', ''))[:150]}); use UIA/visão"
            )
    elif base == "read_focused":
        _items, title, _w = (snapshot_fn or active_window_snapshot)()
        read = (focused_fn or focused_value)()
        facts = (
            f"perceive(read_focused @{obs_id}): window={title[:80]!r}; "
            f"focused={(read[:120] if read else '(vazio/ilegível)')!r}"
        )
    elif base == "expand":
        exp = expand_fn or uia.expand_subtree
        items, title, note = exp(arg)
        if note and not items:
            raise RuntimeError(note)
        names = format_ui_names(items, limit=15)
        facts = (
            f"perceive(expand:{arg} @{obs_id}): window={title[:80]!r}; "
            f"ramo={'; '.join(names) or '(sem descendentes nomeados)'}"
            + (f"; nota: {note[:120]}" if note else "")
        )
    else:
        snap = snapshot_fn or active_window_snapshot
        items, title, _wrect = snap()
        names = format_ui_names(items)
        facts = (
            f"perceive(uia_refresh @{obs_id}): window={title[:80]!r}; "
            f"ui={'; '.join(names[:15]) or '(sem elementos expostos)'}"
        )
    tm["perception"] = {"spec": spec_norm, "observation_id": obs_id, "facts": facts[:500]}
    try:
        feats = ctx.setdefault("perception_facts", [])
        feats.append(facts[:500])
        del feats[:-5]
    except Exception:
        pass
    return facts


def _skills_catalog(cfg: dict) -> str:
    """Catálogo compacto F5 (orçamento; vazio = skills desligadas)."""
    if cfg.get("skills", {}).get("enabled", True) is False:
        return ""
    try:
        import skills as _sk

        cat = _sk.catalog_text(budget=int(cfg.get("skills", {}).get("catalog_budget", 8)))
    except Exception:
        cat = ""
    try:
        import prefs as _prefs

        line = _prefs.context_line()
        if line:
            cat = (cat + "\n" + line) if cat else line
    except Exception:
        pass
    return cat


_PICKER_HINTS = ("quem está usando", "who's using", "who is using", "modo visitante")


def detect_chrome_picker(title: str, names: list[str]) -> bool:
    """Seletor de perfil do browser aberto? Puro, testável.

    Escolher perfil = escolher dados de alguém: nunca adivinhar (ask ou
    preferência lembrada em prefs.json).
    """
    t = (title or "").lower()
    if "chrome" not in t and "edge" not in t and "brave" not in t:
        return False
    blob = " | ".join((n or "").lower() for n in names)
    return any(h in blob for h in _PICKER_HINTS)


def _short(dec: PlannerDecision) -> str:
    t = dec.type
    arg = (
        dec.app
        or dec.target
        or dec.text
        or dec.key
        or dec.keys
        or dec.instruction
        or dec.skill
        or dec.perception
        or ""
    )
    if dec.type == "use_skill" and getattr(dec, "args", None):
        arg = f"{dec.skill}{dec.args}"
    if len(arg) > 42:
        arg = arg[:42] + "..."
    return f"{t}({arg})" if arg else t


def _load_skill_dec(dec: PlannerDecision) -> tuple[dict, dict, dict]:
    """Valida use_skill do modelo: (manifest, args, loaded). Erro -> ValueError."""
    import skills as _sk

    loaded = _sk.load_skill(dec.skill or "")
    man = loaded["manifest"]
    args = dict(dec.args or {})
    _sk.validate_skill_args(man, args)
    return man, args, loaded


def _ask_planner(
    planner,
    instruction: str,
    title: str,
    names: list[str],
    ctx: dict,
    last_error: str,
    cfg: dict,
    skill_context: str = "",
) -> tuple[PlannerDecision, float]:
    """Uma pergunta ao planner, com catálogo (e contexto da skill) F5."""
    task_summary = ""
    try:
        task_state = ctx.get("task_state")
        if task_state is not None:
            task_summary = statemod.compact(task_state)
    except Exception:
        task_summary = ""
    last_result = str(ctx.get("last_result", ""))[:600]
    # Escalada opcional (planner.escalate_thinking): raciocínio só quando há erro/repetição
    # pendente, onde vale pagar a latência. Desligado por padrão até medir no loop real.
    extra = {"think": True} if last_error and cfg.get("planner", {}).get(
        "escalate_thinking") else {}
    try:
        return planner.next_action(
            goal=instruction,
            window=title,
            ui_names=names,
            history=ctx.get("hist_labels", []),
            last_error=last_error,
            skills_catalog=_skills_catalog(cfg),
            skill_context=skill_context,
            task_summary=task_summary,
            last_result=last_result,
            **extra,
        )
    except TypeError:
        # Compat: planner fake/mock sem os kwargs novos.
        try:
            return planner.next_action(
                goal=instruction,
                window=title,
                ui_names=names,
                history=ctx.get("hist_labels", []),
                last_error=last_error,
                skills_catalog=_skills_catalog(cfg),
                skill_context=skill_context,
            )
        except TypeError:
            return planner.next_action(
                goal=instruction,
                window=title,
                ui_names=names,
                history=ctx.get("hist_labels", []),
                last_error=last_error,
            )


# --- decide: fluxo principal (planner) ----------------------------------------
def _decide_planner(
    instruction: str,
    step: int,
    ctx: dict,
    cfg: dict,
    planner: MiniCPMPlanner,
    vocaela: VocaelaAdapter,
) -> tuple[Decision, dict]:
    t: dict = {
        "planner_ms": 0.0,
        "uia_ms": 0.0,
        "screenshot_ms": 0.0,
        "vision_ms": 0.0,
        "vision_calls": 0,
        "planner_calls": 0,
    }
    t0 = time.perf_counter()
    items, title, wrect = active_window_snapshot()
    t["uia_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    t["uia_title"] = title
    t["uia_count"] = len(items)
    try:
        # R2: diagnóstico do snapshot (ok/provider_empty/truncated/
        # timeout_partial/no_window/error). Mocks de teste não preenchem.
        t["uia_diag"] = dict(getattr(uia, "LAST_DIAG", None) or {"source": "unavailable"})
    except Exception:
        t["uia_diag"] = {"source": "unavailable"}
    names = format_ui_names(items)
    # R1: observação ≠ evidência. Observação vira ID rastreável (prompt/
    # correção de efeito); só efeito CONFIRMADO entra em task_state.evidences
    # (ver run(): confirm_effect). Ação enviada ou janela vista nunca prova
    # objetivo cumprido.
    from schemas import new_id as _new_id

    obs_id = _new_id("obs")
    t["observation_id"] = obs_id
    ctx["last_observation_id"] = obs_id
    ctx["last_observation"] = (
        f"{obs_id}: window={title[:120]!r}; "
        f"ui={'; '.join(names[:12]) or '(sem elementos expostos)'}"
    )

    # guard 1: bootstrap ANTES do planner (senão a decisão do 1B é descartada
    # e a latência, paga à toa).
    boot = _app_bootstrap(
        instruction, step, (title or "").lower(), dry_run=bool(ctx.get("dry_run"))
    )
    if boot is not None:
        return Decision(
            action=boot, source="planner", confidence=None, reason="bootstrap janela do app"
        ), t

    last_error = ctx.get("last_error", "")
    skill_docs: dict = ctx.setdefault("skill_docs", {})
    t["ui_names"] = names  # p/ ask persistir preferência + log do contexto
    # Picker de perfil com preferência lembrada: executa o lembrado (dado do
    # HUMANO versionado, não decisão do modelo). Sem preferência: o planner
    # decide (regra do prompt: ask uma vez, sem adivinhar entre pessoas).
    if detect_chrome_picker(title, names):
        t["picker"] = True
        try:
            import prefs as _prefs

            want_profile = _prefs.get("browser_profile")
        except Exception:
            want_profile = ""
        if want_profile:
            hit, _amb = _resolve_uia(items, want_profile, wrect, state_title=title)
            if hit is not None:
                return Decision(
                    action=hit,
                    source="uia",
                    confidence=None,
                    reason=f"perfil lembrado ({want_profile})",
                ), t
            # Ambíguo ou miss com preferência: não adivinhar entre perfis —
            # cai no ask abaixo com as opções visíveis.
        choices = []
        for item in items:
            label = str(item.get("name", "")).strip()
            kind = str(item.get("type", "")).lower()
            if (
                label
                and kind in ("button", "listitem", "hyperlink")
                and "visitante" not in label.lower()
            ):
                choices.append(label)
        unique = list(dict.fromkeys(choices))[:4]
        shown = ", ".join(unique) if unique else "o perfil desejado"
        return Decision(
            action=Action(type="ask", text=(f"Qual perfil do Chrome devo usar? {shown}")),
            source="planner",
            confidence=None,
            kind="question",
            expected_effect="resposta humana vira observação",
            reason="seletor de perfil do Chrome exige escolha humana",
        ), t
    dec: PlannerDecision | None = None
    pms = 0.0
    try:
        for _q in range(2):  # 1ª pergunta + 1 re-query com skill GUI em contexto
            active_doc = ""
            try:
                ts0 = ctx.get("task_state")
                active = (getattr(ts0, "active_skill", "") or "") if ts0 else ""
                if active and active in skill_docs:
                    active_doc = str(skill_docs[active])[:2000]
            except Exception:
                active_doc = ""
            dec, pms = _ask_planner(
                planner, instruction, title, names, ctx, last_error, cfg, skill_context=active_doc
            )
            t["planner_ms"] = round(t["planner_ms"] + pms, 1)
            t["planner_calls"] += 1
            frame_id = str(getattr(planner, "last_frame_id", "") or "")
            if frame_id:
                # R1: frame disponível ≠ fato confirmado. O ID fica rastreável
                # (observation_ref/frame_ref); evidência exige confirmação.
                t["frame_id"] = frame_id
                ctx["last_frame_id"] = frame_id
            if dec.type != "use_skill":
                break
            try:
                man, _args, loaded = _load_skill_dec(dec)
            except ValueError as e:
                raise RuntimeError(str(e))
            if man.get("mode") != "GUI":
                break  # CLI: segue p/ execução à parte no run()
            name = man.get("name", "")
            if name in skill_docs:
                raise RuntimeError(
                    f"skill {name} já está ativa; aja COM ela (hotkey, type, "
                    "uia_click), não reative."
                )
            # GUI: só orienta — carrega referências e pergunta DE NOVO com a
            # skill em contexto (nunca placeholder wait(0) executável).
            skill_docs[name] = str(loaded.get("doc", ""))[:2000]
            try:
                ts1 = ctx.get("task_state")
                if ts1 is not None:
                    ts1.active_skill = name
            except Exception:
                pass
            t["skill_loaded"] = name
            last_error = ""  # re-query limpa: a skill é o contexto novo
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"planner falhou: {e}")
    assert dec is not None
    t["planner_decision"] = dec.model_dump()
    ctx["last_error"] = ""
    # Métricas parciais sobrevivem ao veto: guardas abaixo levantam
    # RuntimeError APÓS a chamada ao planner; sem isso o run registra
    # planner_calls=0 mesmo pagando segundos de inferência (run U1 20/09:
    # 174s/4 chamadas invisíveis). Referência viva: o run() consome no except.
    ctx["_partial_tm"] = t

    # Reabrir um browser ativo pode criar o seletor de perfis.
    browser = _browser_want(instruction)
    if browser and (
        dec.type == "open_app"
        or (dec.type == "focus_window" and browser[1] in (dec.target or "").lower())
    ):
        app, app_word, _hint = browser
        active = (title or "").lower()
        if app_word in active:
            raise RuntimeError(
                f"{app} ja esta ativo em {title!r}; a parte 'abrir o browser' "
                "esta CUMPRIDA, avance p/ o proximo sub-objetivo. Nao abra/"
                "focalize de novo. Aja DENTRO da janela (hotkey ctrl+t/ctrl+l, "
                "type_text, press_key enter, uia_click por NOME visivel, "
                "visual_action p/ o que nao esta na lista, sequence de ate 3 "
                "primitivas). Se a pagina mostra intersticial/captcha/"
                "'Continue shopping', clique p/ dispensar primeiro (faz parte "
                "de chegar ao site)."
            )

    # guard 2b: anti-cópia do exemplo — "Google" é o EXEMPLO do spec, não o
    # alvo (run 19/09: focus("Google") casava qualquer aba do Chrome e o 1B
    # repetia). Só vale se "google" estiver no pedido.
    if dec.type == "focus_window":
        tgt = (dec.target or "").strip().lower()
        if tgt == "google" and "google" not in instruction.lower():
            raise RuntimeError(
                'focus("Google") recusado: "Google" é o EXEMPLO do spec, não '
                "o alvo. Use open_app/focus_window com o app do pedido "
                "(chrome/edge/brave) ou aja DENTRO da janela (ctrl+l, "
                "type, cliques)."
            )
        if tgt in ("...", "...texto...") or (dec.text or "") in ("...",):
            raise RuntimeError(
                "alvo/texto é o placeholder do spec; use o conteúdo real do "
                "pedido, da janela ou dos UI elements."
            )

    # guard 2: anti-janela-errada — sem foco no alvo, planner deve focar/abrir;
    # se insistir em agir, devolve como last_error em vez de executar.
    if (
        _has_any(instruction, CALC_WORDS)
        and "calcul" not in title.lower()
        and dec.type in ("type_text", "uia_click", "visual_action", "press_key", "hotkey")
    ):
        raise RuntimeError(
            f"janela ativa é {title!r}, não a calculadora; use focus_window ou open_app primeiro."
        )

    if ctx.get("no_vision") and dec.type == "visual_action":
        raise RuntimeError("visão desabilitada (no_vision); use uia_click, type_text ou teclado.")

    # `done` exige referências exatas a evidências confirmadas. Uma ação
    # enviada ou uma nota no histórico nunca prova que o objetivo foi cumprido.
    if dec.type == "done":
        import verification as _vf

        ts = ctx.get("task_state")
        evs = list(getattr(ts, "evidences", []) or []) if ts is not None else []
        ok, msg = _vf.done_evidence_ok(list(dec.evidences or []), evs)
        if not ok:
            raise RuntimeError(f"'done' recusado: {msg}")
    # F3: memória — atualização compacta validada (fato != hipótese).
    # R1: proposta registrada separada da aceita; concluídas sem
    # evidence_refs válidas são ignoradas (alegação não vira fato).
    if dec.task_update:
        try:
            ts = ctx.get("task_state")
            if ts is not None:
                proposed = dict(dec.task_update)
                before_done = list(getattr(ts, "done_items", []) or [])
                statemod.apply_update(ts, dec.task_update)
                t["task_update_proposed"] = proposed
                t["task_update_accepted_done"] = [
                    d for d in getattr(ts, "done_items", []) if d not in before_done
                ]
                prop_done = (
                    proposed.get("done_items") or proposed.get("concluidas") or proposed.get("done")
                )
                if isinstance(prop_done, list) and prop_done and not t["task_update_accepted_done"]:
                    t["task_update_rejected"] = "done_items sem evidence_refs válidas"
        except Exception:
            pass

    try:
        native = _planner_to_action(dec)
    except ValueError as e:  # app fora da whitelist
        raise RuntimeError(str(e))
    if native is not None:
        if dec.type == "use_skill":
            # F5: skill escolhida pelo modelo; CLI executa à parte no run().
            # GUI nunca chega aqui como placeholder: o re-query acima já a
            # carregou em contexto; se o planner insistir, é para AGIR.
            try:
                man, args, _loaded = _load_skill_dec(dec)
            except ValueError as e:
                raise RuntimeError(str(e))
            if man.get("mode") == "GUI":
                raise RuntimeError(
                    f"skill {man.get('name')} já carregada; aja COM ela "
                    "(hotkey, type, uia_click, visual_action ou sequence), "
                    "não reative."
                )
            return Decision(
                action=native,
                source="planner",
                confidence=None,
                kind="skill",
                skill=man.get("name", ""),
                skill_args=args,
                expected_effect=f"skill {man.get('name')}: {man.get('verify', '')}",
                observation_ref=t.get("observation_id", ""),
                reason=f"planner use_skill({man.get('name')})",
            ), t
        if dec.type == "ask":
            # R1: pergunta é kind próprio (nunca ação física).
            return Decision(
                action=native,
                source="planner",
                confidence=None,
                kind="question",
                expected_effect="resposta humana vira observação",
                observation_ref=t.get("observation_id", ""),
                reason=f"planner {_short(dec)}",
            ), t
        if dec.type == "done":
            # R1: conclusão é kind próprio (evidências já vetadas acima).
            return Decision(
                action=native,
                source="planner",
                confidence=None,
                kind="finish",
                expected_effect="conclusão com evidências confirmadas",
                observation_ref=t.get("observation_id", ""),
                reason=f"planner {_short(dec)}",
            ), t
        return Decision(
            action=native, source="planner", confidence=None, reason=f"planner {_short(dec)}"
        ), t

    if dec.type == "sequence":
        # F6: sequência curta do modelo; guardas entre primitivas no run().
        try:
            acts = _sequence_to_actions(dec)
        except RuntimeError as e:
            raise RuntimeError(str(e))
        for i, act in enumerate(acts):
            text = (act.text or "").strip().lower()
            if act.type == "type" and (text.startswith("http://") or text.startswith("https://")):
                previous = acts[i - 1] if i else None
                if (
                    previous is None
                    or previous.type != "hotkey"
                    or (previous.key or "").lower() != "ctrl+l"
                ):
                    raise RuntimeError(
                        "sequencia de navegacao recusada: URL so pode ser "
                        "digitada imediatamente apos hotkey(ctrl+l). Use "
                        "sequence(ctrl+l, type_text(URL), enter)."
                    )
        return Decision(
            action=acts[0],
            source="planner",
            confidence=None,
            kind="sequence",
            steps=acts,
            expected_effect=f"sequência de {len(acts)} primitivas",
            observation_ref=t.get("observation_id", ""),
            reason=f"planner sequence({_steps_label(acts)})",
        ), t

    if dec.type == "perceive":
        # R1/R2: percepção read-only pedida pelo modelo; a releitura (sem
        # input físico) executa no run(). Spec validado aqui; teto no run().
        try:
            base, arg = _parse_perception(dec.perception or "")
        except RuntimeError as e:
            raise RuntimeError(str(e))
        spec_norm = f"{base}:{arg}" if arg else base
        return Decision(
            action=Action(type="perceive", text=spec_norm),
            source="planner",
            confidence=None,
            kind="perception",
            perception=spec_norm,
            expected_effect="releitura sem input físico; fatos voltam como observação",
            observation_ref=t.get("observation_id", ""),
            reason=f"planner perceive({spec_norm})",
        ), t

    if dec.type == "uia_click":
        hit, note = _resolve_uia(items, dec.target or "", wrect, state_title=title)
        if hit is not None:
            return Decision(
                action=hit, source="uia", confidence=None, reason=f'uia_click("{dec.target}")'
            ), t
        if note.startswith("alvo ambíguo"):
            # R2: a visão também adivinharia — o planner desambigua pelo
            # contexto (nomes repetidos) em vez de clicar no escuro.
            raise RuntimeError(note)
        if ctx.get("no_vision"):
            raise RuntimeError(
                f"uia_click: {dec.target!r} não está na "
                "accessibility tree e a visão está desabilitada."
            )
        # miss acessível -> escala p/ visão com a mesma intenção
        dec = PlannerDecision(type="visual_action", instruction=f"Click {dec.target}")
        t["escalated"] = "uia_miss->vision"

    # visual_action: screenshot SÓ agora -> Vocaela -> coords 0..1 -> físico
    s0 = time.perf_counter()
    img, origin, full = capture_for_vision(max_long_edge=int(cfg.get("screenshot_max_width", 1024)))
    t["screenshot_ms"] = round((time.perf_counter() - s0) * 1000, 1)
    try:
        from obs import frame_ref_for

        frame = frame_ref_for(origin, (img.size[0], img.size[1]),
                              title, t.get("observation_id", ""))
        t["frame"] = frame.model_dump()
        t["visual_title"] = title
    except Exception:
        pass
    try:
        from obs import LAST_PNG

        img.save(LAST_PNG)  # prova/depuração do que o Vocaela viu
    except Exception:
        pass
    try:
        va, vms = vocaela.act_sync(img, dec.instruction or "", history=ctx.get("hist_labels", []))
    except Exception as e:
        raise RuntimeError(f"vocaela falhou: {e}")
    t["vision_ms"] = round(vms, 1)
    t["vision_calls"] = 1
    t["visual"] = va.model_dump()
    if va.dropped:
        t["visual_dropped"] = va.dropped
    act = visual_to_action(va, (img.size[0], img.size[1]), origin)
    return Decision(
        action=act,
        source="vocaela",
        confidence=None,
        reason=f'visual "{dec.instruction}" -> {va.type}({va.x},{va.y})',
    ), t


def decide(
    instruction: str,
    step: int,
    ctx: dict,
    cfg: dict,
    planner: MiniCPMPlanner | None = None,
    vocaela: VocaelaAdapter | None = None,
) -> tuple[Decision, dict]:
    """TODA decisão vem dos modelos. Sem planner -> erro honesto (sem router)."""
    if planner is None or vocaela is None:
        raise RuntimeError(
            "arquitetura de 2 modelos exige o planner (8091) e Vocaela (8082) "
            "online; sem fallback programático."
        )
    dec, tm = _decide_planner(instruction, step, ctx, cfg, planner, vocaela)
    # R1: toda decisão carrega a observação de origem (alvo por ID/frame,
    # nunca reutilizado). Vetos Python sobre a ação final (coords físicas
    # de UIA/visão são legítimas aqui; o veto a coords do planner vive
    # em PlannerDecision).
    try:
        if not (dec.observation_ref or "").strip():
            dec.observation_ref = str(tm.get("observation_id", "") or "")
        if not (dec.frame_ref or "").strip():
            dec.frame_ref = str(tm.get("frame_id", "") or "")
    except Exception:
        pass
    a = dec.action
    if a.type == "type" and not (a.text or "").strip():
        raise RuntimeError("type precisa de text")
    if a.type in ("open", "focus") and not (a.target or "").strip():
        raise RuntimeError(f"{a.type} precisa de target")
    if a.type == "hotkey" and not (a.key or "").strip():
        raise RuntimeError("hotkey precisa de key")
    if a.type == "ask" and not (a.text or "").strip():
        raise RuntimeError("ask precisa de text (a pergunta ao humano)")
    if a.type == "perceive" and not (a.text or "").strip():
        raise RuntimeError("perceive precisa de spec (uia_refresh|read_focused|ocr|expand:<nome>)")
    if dec.confidence is not None and not (0.0 <= dec.confidence <= 1.0):
        raise RuntimeError(f"confidence fora de 0..1: {dec.confidence}")
    return dec, tm


# --- verify: observação passiva (NUNCA decide ação nem done) -------------------
_NO_OP_PREFIXES = ("wait(", "answer(", "ask(")


def done_allowed(hist_labels: list[str]) -> bool:
    """`done` exige ao menos uma ação executada que não seja wait/answer."""
    return any(not h.startswith(_NO_OP_PREFIXES) for h in hist_labels)


# Título de página de erro (observação, não decisão): o 1B ignorou
# "Page Not Found - Brave" e repetiu a mesma URL 3x. Só fatos p/ o planner.
_ERROR_TITLE_HINTS = (
    "not found",
    "404",
    "can't be reached",
    "cannot be reached",
    "unable to connect",
    "connection refused",
    "err_",
    "error",
    "problem loading",
    "isn't working",
    "is not working",
)


def observe(action: Action, before_title: str, after_title: str, value: str = "") -> str:
    """Resultado da ação em texto curto p/ o planner (puro, testável).

    Só fatos observados: janela antes/depois, e p/ `type` se o texto
    apareceu no campo focado. Nunca conclui "sucesso" do objetivo.
    """
    parts = []
    if after_title and after_title != before_title:
        parts.append(f"window {before_title or '?'!r} -> {after_title!r}")
    else:
        parts.append(f"window {after_title or '?'!r}")
    low = (after_title or "").lower()
    if action.type == "open" and any(h in low for h in _ERROR_TITLE_HINTS):
        parts.append(
            "page title suggests an ERROR page: do NOT retry the "
            "same URL, go back to the site homepage or search"
        )
    if action.type == "type" and action.text:
        if value and action.text.strip()[:40] in value:
            parts.append("text visible in focused field")
        elif value:
            parts.append(f"focused field now: {value[:60]!r}")
        else:
            parts.append("focused field unreadable")
    elif action.type in ("open", "focus") and after_title == before_title:
        want = (action.target or "").strip().lower().replace(".exe", "")
        first = want.split("||")[0].split()[0] if want else ""
        if first and len(first) >= 3 and first in (after_title or "").lower():
            # O app JÁ está ativo (ex: open(chrome) com 'Google Chrome' na
            # tela, parado num seletor de perfil). "no window change yet"
            # lia-se como falha e o planner reabria em loop; instrução
            # positiva (1B obedece melhor "faça X" que "não faça Y").
            parts.append(
                f"{after_title!r} is already active; do NOT open/focus "
                "again — act INSIDE the window (ctrl+l, type, clicks)"
            )
        else:
            parts.append("no window change yet")
    elif action.type == "hotkey" and after_title == before_title:
        # Tecla de foco (ctrl+l/ctrl+t/...) normalmente NÃO muda o título:
        # título igual não é falha. Só aponta receita p/ combinação
        # desconhecida (run 19/09: ctrl+alt+n 5x sem efeito). Run 20/09:
        # ctrl+l válido era lido como "wrong combo" e confundia o planner.
        key = (action.key or "").lower()
        known = ("ctrl+l", "ctrl+t", "ctrl+w", "ctrl+tab", "enter", "f5", "esc", "tab")
        if any(k in key for k in known):
            parts.append(
                f"window unchanged (expected for hotkey({action.key or '?'})"
                "; focus keys don't change the title — continue with "
                "type/enter/clicks)"
            )
        else:
            # Combinação errada não faz nada observável (run 19/09: ctrl+alt+n
            # 5x sem efeito). Fato + ponteiro p/ receita, sem escolher a próxima.
            parts.append(
                f"hotkey({action.key or '?'}) had no visible effect; "
                "wrong combos do nothing — use the recipe "
                "(ctrl+t new tab, ctrl+l address bar)"
            )
    return "; ".join(parts)


def verify(
    action: Action, instruction: str, cfg: dict, before_title: str = "", dry_run: bool = False
) -> tuple[bool, str, list[str]]:
    """Espera condicional + observação (F3: sem 500+300 ms fixos).

    Espera cancelável por condição/evento com polling curto e deadline
    (= verify_wait_ms); timeout retorna observação, sem inventar sucesso.
    Retorna (ok, nota, ui_after): `ui_after` são os `tipo:nome` do snapshot
    posterior (p/ confirmação específica de modal/conteúdo em
    `confirm_effect`; vazio em dry-run).
    """
    if dry_run:
        # F0 dry-run: bloqueia TODOS os efeitos (sem sleep, sem snapshot).
        return True, "dry_run: efeitos bloqueados (sem verify físico)", []
    deadline_s = max(0.05, int(cfg.get("verify_wait_ms", 500)) / 1000.0)
    ok, _ = verif.wait_for_condition(lambda: False, deadline_s=deadline_s, poll_s=0.1)
    _ = ok  # deadline sempre estoura aqui; a condição real é o snapshot abaixo
    if safety.stop_requested():
        return False, "aborted", []
    items, title, _ = active_window_snapshot()
    value = focused_value() if action.type == "type" else ""
    return bool(title), observe(action, before_title, title, value), format_ui_names(items)


def _progress_made(prev_note: str, cur_note: str) -> bool:
    """Compara fingerprints observados, não a redação das notas."""
    if not prev_note or not cur_note:
        return True
    return prev_note.strip() != cur_note.strip()


def _state_fingerprint(title: str, ui_names: list[str]) -> str:
    """Estado relevante compacto para anti-loop, sem interpretar a tarefa."""
    return json.dumps(
        {"title": title, "ui": list(ui_names[:30])},
        ensure_ascii=False,
        sort_keys=True,
    )


def _loop_has_progress(h3: list, h2: list, h1: list) -> bool:
    """3 entradas [key, fp, result]: houve progresso? (puro, testável).

    R2: progresso = fingerprint mudou OU os dois últimos resultados
    conhecidos (vnote|confirm) diferem. Repetição do mesmo resultado sem
    mudança de estado é ausência de efeito, não avanço.
    """
    same_fp = not _progress_made(h2[1], h1[1])
    r_old = h3[2] if len(h3) > 2 else ""
    r_prev = h2[2] if len(h2) > 2 else ""
    return (not same_fp) or bool(r_old and r_prev and r_old != r_prev)


def _foreground_title() -> str:
    """Título da janela em foreground (best-effort, só leitura)."""
    try:
        import ctypes

        h = ctypes.windll.user32.GetForegroundWindow()
        if not h:
            return ""
        from pywinauto import Desktop

        return Desktop(backend="uia").window(handle=int(h)).window_text() or ""
    except Exception:
        return ""


def _visual_stale_note(dec: Decision, tm: dict) -> str:
    """Alvo visual de frame obsoleto? Mudança de janela/modal entre a captura
    e o clique invalida as coords (R2). Leitura nova, sem decidir nada."""
    if getattr(dec, "source", "") != "vocaela":
        return ""
    expected = (tm.get("visual_title") or "").strip()
    if not expected:
        return ""
    cur = _foreground_title().strip()
    if cur and cur != expected:
        return (
            f"alvo do frame obsoleto: janela mudou {expected!r} -> {cur!r} "
            "entre captura e clique (modal?); reobserve, não clique no escuro"
        )
    return ""


def do_ask(question: str, cfg: dict, dry_run: bool = False) -> str:
    """Pergunta ao humano (human-in-the-loop). Nunca decide pelo modelo.

    Dry-run: não bloqueia (placeholder). Live: stdin com timeout
    (ask.timeout_s, default 60) — sem humano por perto (EOF/timeout),
    segue sem a resposta em vez de travar o run.
    """
    q = (question or "").strip()[:300] or "preciso de uma decisão sua"
    if dry_run:
        return "dry_run: pergunte ao humano no run real"
    timeout = float(cfg.get("ask", {}).get("timeout_s", 60))
    print(f"\n[HUMANO] {q}")
    ans: list = [None]
    import threading

    def _read() -> None:
        try:
            ans[0] = input("> ")
        except Exception as e:
            ans[0] = f"__ERR__:{e}"

    th = threading.Thread(target=_read, daemon=True)
    th.start()
    th.join(timeout=max(1.0, timeout))
    if th.is_alive():
        return "sem resposta (timeout) — seguindo sem o humano"
    a = ans[0]
    if not isinstance(a, str) or a.startswith("__ERR__") or not a.strip():
        return "sem resposta — seguindo sem o humano"
    return a.strip()[:300]


def _key(a: Action, dec: Decision | None = None) -> str:
    base = f"{a.type}:{a.x},{a.y}:{a.text}:{a.key}:{a.target}"
    if dec is not None:
        return f"{dec.kind}:{dec.skill}:{_steps_label(list(dec.steps or []))}:{base}"
    return base


def _verify_action(dec: Decision) -> Action:
    """Ação representativa p/ observe(): sequence usa as combinações.

    Sem isso o verify de sequence observava o placeholder wait(0) e nunca
    dizia que a combinação não teve efeito (run 19/09: ctrl+alt+n 5x).
    """
    steps = list(dec.steps or [])
    if steps:
        keys = ",".join(str(a.key or "") for a in steps if a.type == "hotkey" and a.key)
        if keys:
            return Action(type="hotkey", key=keys[:80])
        types = ",".join(a.type for a in steps)
        return Action(type="hotkey", key=f"sequence:{types}"[:80])
    return dec.action


def _repeat_note(a: Action, dec: Decision | None = None) -> str:
    """last_error anti-repetição com o detalhe do que repetiu (puro, testável).

    O 1B ignorava 'você repetiu focus 3 vezes' e repetia com outro alvo;
    com o alvo citado ele troca de estratégia em vez de variar o texto.
    Sequence cita o CONTEÚDO (a combinação errada é o problema, não o tipo).
    """
    steps = list((dec.steps or []) if dec is not None else [])
    if steps:
        detail = f"sequence({_steps_label(steps)})"
        return (
            f"você repetiu {detail} 3 vezes sem avançar; a combinação "
            "está errada ou incompleta — mude as TECLAS/conteúdo "
            "(ex.: ctrl+t nova aba, ctrl+l endereço, type_text, enter), "
            "não repita a mesma sequência."
        )
    arg = a.target or a.text or a.key or ""
    detail = f"{a.type}({arg[:60]})" if arg else a.type
    return (
        f"você repetiu {detail} 3 vezes sem avançar; "
        "escolha uma ação DIFERENTE (outro tipo de ação, não o mesmo "
        "tipo com outro texto)."
    )


def _vram() -> str:
    try:
        import subprocess

        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        return f"{out} MiB (nvidia-smi)" if out else "n/a"
    except Exception:
        return "n/a (sem nvidia-smi)"


def _build_models(cfg: dict) -> tuple[MiniCPMPlanner, VocaelaAdapter]:
    """F4: constrói via `model_adapters` conforme o perfil (B0 = default)."""
    try:
        from model_adapters import build_adapters

        planner, vision, _prof = build_adapters(cfg)
        return planner, vision
    except Exception:
        pc = cfg.get("planner", {})
        vc = cfg.get("vision", {})
    planner = MiniCPMPlanner(
        base_url=pc.get("base_url", "http://127.0.0.1:8091/v1"),
        model=pc.get("model", "MiniCPM5-2B"),
        temperature=float(pc.get("temperature", 0.1)),
        timeout_s=float(pc.get("timeout_s", 90)),
    )
    vocaela = VocaelaAdapter(
        base_url=vc.get("base_url", "http://127.0.0.1:8082/v1"),
        model=vc.get("model", "Vocaela-2-500M-1024R2"),
        timeout_s=float(vc.get("timeout_s", 180)),
        max_long_edge=int(cfg.get("screenshot_max_width", 1024)),
    )
    return planner, vocaela


def _ensure_local_servers(cfg: dict) -> dict:
    """Sobe os 2 llama-server se os endpoints são desta máquina e estão caídos.

    1ª execução baixa runtime+GGUFs em models/ (gitignored); nas seguintes,
    só sobe/reusa. Config `runtime.auto_start: false` desliga. URLs remotas
    (Sandbox → HOST_IP) são responsabilidade do host: nada é baixado aqui.
    Retorna procs {"planner": Popen|None, "vision": Popen|None}.
    """
    rt = cfg.get("runtime", {})
    if not bool(rt.get("auto_start", True)):
        return {}
    urls = (
        cfg.get("planner", {}).get("base_url", "http://127.0.0.1:8091/v1"),
        cfg.get("vision", {}).get("base_url", "http://127.0.0.1:8082/v1"),
    )
    if not server.needs_local_serve(urls):
        return {}
    try:
        import config as _cfgmod
        from model_adapters import unified_endpoints

        prof = _cfgmod.profile_of(cfg)
        urls = unified_endpoints(urls[0], urls[1], prof)
        out = server.ensure_servers_for_profile(urls, cfg, prof)
        try:
            server.record_manifest(prof, cfg)
            gs = server.gpu_status(cfg)
            if gs.get("warn"):
                print(f"GPU: {gs['warn']}")
        except Exception:
            pass
        return out
    except Exception as e:
        raise RuntimeError(f"runtime local dos modelos falhou: {e}")


# --- run ----------------------------------------------------------------------
def run(instruction: str, cfg: dict, dry_run: bool = False) -> dict:
    """Loop principal. dry_run=True bloqueia TODOS os efeitos (§9.1 F0).

    Efeitos bloqueados: bootstrap/foco, execute físico, verify com sleep/
    snapshot, overlay e sleeps fixos. Decisão por modelos continua (não é
    efeito no desktop). Toda execução grava `runs/<id>/` (telemetry.py);
    `run.jsonl` legado é mantido por compatibilidade.
    """
    dry_run = bool(dry_run or cfg.get("dry_run", False))
    max_steps = int(cfg.get("max_steps", 30))
    print(f"Task: {instruction}" + (" [dry-run]" if dry_run else ""))
    print(f"Stop: {safety.HOTKEY} | Ctrl+C no terminal.")
    if LOG.exists():
        LOG.unlink()

    # F0: run dir reproduzível (nunca apaga o anterior; LOG legado continua).
    run_id = ""
    try:
        import telemetry as _tel

        run_id = _tel.new_run_id("run")
        _tel.write_json(_tel.run_dir(run_id) / "config.json", cfg)
        _tel.write_json(_tel.run_dir(run_id) / "env.json", _tel.collect_env())
        _tel.append_jsonl(
            _tel.run_dir(run_id) / "run.jsonl",
            {"event": "start", "test": instruction, "dry_run": dry_run},
        )
    except Exception:
        run_id = ""

    # --- sobe os dois modelos: OBRIGATÓRIOS (decisão 100% por modelos) ---
    # Runtime próprio: se os endpoints são locais e estão caídos, sobe
    # llama-server (baixa GGUFs na 1ª vez). URLs remotas: responsabilidade
    # de quem as expõe (ex.: host p/ o Sandbox).
    procs: dict = {}
    try:
        procs = _ensure_local_servers(cfg)
    except RuntimeError as e:
        print(f"RUNTIME DOS MODELOS: {e}")
        print("Sem fallback programático: os modelos decidem. Corrija e rode de novo.")
        _log({"event": "no_model", "planner": str(e)[:200]})
        return {
            "test": instruction,
            "result": "no_model",
            "steps": 0,
            "retries": 0,
            "planner_calls": 0,
            "vocaela_calls": 0,
        }

    planner, vocaela = _build_models(cfg)
    st = planner.check()
    if not st.get("ok"):
        print(f"Planner {planner.model} OFFLINE: {st.get('error')}")
        print(
            f"Suba o planner ({planner.base_url}) ou rode `uv run python -m server`. "
            "Sem fallback programático: os modelos decidem."
        )
        _log({"event": "no_model", "planner": str(st.get("error"))[:200]})
        server.stop_servers(procs)
        return {
            "test": instruction,
            "result": "no_model",
            "steps": 0,
            "retries": 0,
            "planner_calls": 0,
            "vocaela_calls": 0,
        }
    no_vision = bool(cfg.get("no_vision", False))
    vs = vocaela.check()
    if not vs.get("ok") and not no_vision:
        print(
            f"Vocaela OFFLINE ({vs.get('error')}); visual_action vai falhar — "
            f"suba o runtime da visão ({vocaela.base_url}) ou `uv run python -m server`."
        )
    print(f"Planner: {planner.model} ({planner.base_url} modelos={st.get('models')})")
    print(
        f"Visão: {vocaela.model} ({vocaela.base_url} "
        f"{'ok' if vs.get('ok') else 'OFFLINE: ' + str(vs.get('error'))[:80]})"
    )

    # safety + overlay só DEPOIS dos checks: nada de borda "controlado" órfã.
    # Dry-run: sem overlay/hook físico (só decisão por modelos).
    if not dry_run:
        safety.start(cfg.get("stop_hotkey"))
    try:
        import overlay as _ov

        ov_on = _ov.start() if not dry_run else False
    except Exception:
        ov_on = False
    print(f"Overlay de controle: {'ON (borda azul)' if ov_on else 'OFF (tkinter indisponível)'}")

    ctx: dict = {
        "no_vision": no_vision,
        "hist_labels": [],
        "last_error": "",
        "dry_run": dry_run,
        "task_state": statemod.init(instruction),
    }
    # F3: orçamentos separados — ações (max_steps), decisões, retries e tempo.
    # Retries não gastam max_steps, mas não mantêm a tarefa viva para sempre.
    max_time_s = float(cfg.get("max_time_s", 600))
    max_total_retries = int(cfg.get("max_total_retries", 12))
    metrics = {
        "planner_ms": 0.0,
        "uia_ms": 0.0,
        "screenshot_ms": 0.0,
        "vision_ms": 0.0,
        "execution_ms": 0.0,
        "step_ms": 0.0,
        "vision_calls": 0,
        "planner_calls": 0,
        "steps": 0,
        "retries": 0,
    }
    history: list[str] = []
    line = 0
    retries = 0  # consecutivos; NÃO consomem max_steps
    step = 0
    total0 = time.perf_counter()
    result = "stopped"

    def emit(layer: str, msg: str, ms) -> None:
        nonlocal line
        line += 1
        print(f"[{line}] {layer:<7} {msg} {ms}" if ms != "" else f"[{line}] {layer:<7} {msg}")

    def fail_step(kind: str, err: str) -> bool:
        """Registra erro recuperável; True = continuar (retry), False = parar."""
        nonlocal retries
        retries += 1
        metrics["retries"] += 1
        ctx["last_error"] = err[:300]
        try:
            statemod.add_failure(ctx.get("task_state"), err)
        except Exception:
            pass
        if metrics["retries"] > max_total_retries:
            print(f"PARADO step {step}: orçamento total de retries esgotado ({max_total_retries}).")
            _log({"step": step, "event": "stuck", "kind": kind, "error": "max_total_retries"})
            return False
        if retries > MAX_RETRIES:
            print(f"PARADO step {step}: {kind} falhou {retries}x: {err}")
            _log({"step": step, "event": "stuck", "kind": kind, "error": err[:300]})
            return False
        print(f"[retry {retries}/{MAX_RETRIES}] step {step}: {err}")
        _log({"step": step, "event": "retry", "kind": kind, "error": err[:300]})
        if not dry_run:
            time.sleep(0.5)
        return True

    try:
        while step < max_steps:
            if safety.stop_requested():
                _log({"step": step, "event": "aborted"})
                result = "aborted"
                break
            if time.perf_counter() - total0 > max_time_s:
                _log(
                    {
                        "step": step,
                        "event": "stuck",
                        "kind": "tempo",
                        "error": f"max_time_s={max_time_s} esgotado",
                    }
                )
                result = "stuck"
                break
            s0 = time.perf_counter()

            try:
                dec, tm = decide(instruction, step, ctx, cfg, planner, vocaela)
            except RuntimeError as e:
                # Veto após chamada ao planner: não perder o custo pago
                # (uia/planner). Sem isso planner_calls=0 esconde a latência.
                partial = ctx.pop("_partial_tm", None)
                if isinstance(partial, dict):
                    metrics["planner_ms"] += partial.get("planner_ms", 0)
                    metrics["uia_ms"] += partial.get("uia_ms", 0)
                    metrics["screenshot_ms"] += partial.get("screenshot_ms", 0)
                    metrics["vision_ms"] += partial.get("vision_ms", 0)
                    metrics["vision_calls"] += partial.get("vision_calls", 0)
                    metrics["planner_calls"] += partial.get("planner_calls", 0)
                if fail_step("decisao", str(e)):
                    continue
                result = "stuck"
                break

            metrics["planner_ms"] += tm.get("planner_ms", 0)
            metrics["uia_ms"] += tm.get("uia_ms", 0)
            metrics["screenshot_ms"] += tm.get("screenshot_ms", 0)
            metrics["vision_ms"] += tm.get("vision_ms", 0)
            metrics["vision_calls"] += tm.get("vision_calls", 0)
            metrics["planner_calls"] += tm.get("planner_calls", 0)

            src = dec.source
            seq_label = _steps_label(list(dec.steps or [])) if list(dec.steps or []) else ""
            if seq_label:
                seen = (tm.get("uia_title", "") or "?")[:40]
                emit(
                    "PLANNER",
                    f"sequence({seq_label}) [viu: {seen!r}]",
                    f"{tm.get('planner_ms', 0):.0f}ms",
                )
            elif src == "planner" and dec.action.type in (
                "open",
                "focus",
                "type",
                "hotkey",
                "wait",
                "answer",
                "done",
                "ask",
                "perceive",
            ):
                a = dec.action
                arg = a.target or a.text or a.key or ""
                seen = (tm.get("uia_title", "") or "?")[:40]
                emit(
                    "PLANNER",
                    f'{a.type}("{arg}") [viu: {seen!r}]',
                    f"{tm.get('planner_ms', 0):.0f}ms",
                )
            else:
                if tm.get("uia_count") is not None:
                    emit(
                        "UIA",
                        f'found {tm.get("uia_count", 0)} ("{tm.get("uia_title", "")}")',
                        f"{tm.get('uia_ms', 0):.0f}ms",
                    )
                if src == "vocaela":
                    va = tm.get("visual", {})
                    vinstr = tm.get("planner_decision", {}).get("instruction", "")
                    emit("PLANNER", f"visual: {vinstr}", f"{tm.get('planner_ms', 0):.0f}ms")
                    if tm.get("screenshot_ms"):
                        emit("SHOT", "active-window capture", f"{tm.get('screenshot_ms', 0):.0f}ms")
                    dropped = tm.get("visual_dropped")
                    extra = f" (+{dropped} descartadas)" if dropped else ""
                    emit(
                        "VISION",
                        f"{va.get('type')}({va.get('x')}, {va.get('y')}){extra}",
                        f"{tm.get('vision_ms', 0):.0f}ms",
                    )
                else:  # uia
                    emit("PLANNER", f"uia: {dec.reason}", f"{tm.get('planner_ms', 0):.0f}ms")

            # guard 3 (F3/R2): anti-loop por ação+estado+RESULTADO — preserva
            # tentativas malsucedidas e distingue repetição legítima
            # (scroll avançou: verify mudou) de ausência de progresso.
            # O resultado (vnote|confirm) preenche o 3º campo após o verify.
            k = _key(dec.action, dec)
            fp = _state_fingerprint(tm.get("uia_title", ""), tm.get("ui_names", []))
            history.append([k, fp, ""])
            if len(history) >= 3 and history[-1][0] == history[-2][0] == history[-3][0]:
                # R2: progresso = estado mudou OU o último resultado mudou
                # (vnote|confirm das execuções anteriores). Sem progresso
                # real, a repetição vira last_error e depois stop.
                if _loop_has_progress(history[-3], history[-2], history[-1]):
                    history.clear()  # progresso real: repetição legítima
                elif not ctx.get("loop_warned"):
                    ctx["loop_warned"] = True
                    history.clear()
                    if fail_step("repeticao", _repeat_note(dec.action, dec)):
                        continue
                    result = "stuck"
                    break
                else:
                    print("loop persistente -> stop.")
                    _log({"step": step, "event": "loop"})
                    result = "loop"
                    break

            e0 = time.perf_counter()
            try:
                if dec.kind == "question":
                    # Human-in-the-loop: pergunta, registra a resposta como
                    # observação e segue. Não consome max_steps (como retry),
                    # mas tem teto (ask.max_asks, default 3).
                    n_asks = int(ctx.get("n_asks", 0))
                    max_asks = int(cfg.get("ask", {}).get("max_asks", 3))
                    question = (dec.action.text or "").strip()[:300]
                    if n_asks >= max_asks:
                        raise RuntimeError(
                            "ask recusado: limite de "
                            f"{max_asks} perguntas por run; decida com o "
                            "observável na tela."
                        )
                    ctx["n_asks"] = n_asks + 1
                    if dry_run:
                        answer = do_ask(question, cfg, dry_run=True)
                        desc = f"ask({question[:60]}) => dry_run"
                    else:
                        emit("ASK", question, "")
                        answer = do_ask(question, cfg)
                        desc = f"ask({question[:60]}) => humano: {answer[:80]}"
                        tm["ask"] = {"question": question, "answer": answer}
                    # Resposta que casa com nome visível vira preferência
                    # lembrada (ex.: perfil do browser) — vale nos próximos.
                    try:
                        if "perfil" in question.lower() or "profile" in question.lower():
                            import prefs as _prefs

                            for n in tm.get("ui_names", []) or []:
                                label = str(n).split(":", 1)[-1].strip()
                                al = answer.strip().lower()
                                if len(al) >= 2 and al in label.lower():
                                    _prefs.remember("browser_profile", label[:80])
                                    break
                    except Exception:
                        pass
                elif dec.kind == "perception":
                    # R1: releitura read-only, sem mouse/teclado. Teto próprio
                    # (perception.max_perceptions); fatos voltam como
                    # observação (nunca evidência confirmada).
                    _check_perception_budget(ctx, cfg)
                    facts = _run_perception(dec.perception, ctx, cfg, tm)
                    ctx["n_perceptions"] = int(ctx.get("n_perceptions", 0)) + 1
                    desc = f"perceive({dec.perception}) => {facts[:160]}"
                    emit("PERCEIVE", facts[:160], "")
                elif dry_run:
                    a = dec.action
                    arg = a.target or a.text or a.key or ""
                    desc = f"dry_run:{a.type}({arg[:60]})"
                elif getattr(dec, "kind", "action") == "skill":
                    # F5: executor CLI restrito, separado do modo GUI e no log.
                    import skills as _sk

                    emit("SKILL", f"{dec.skill}({dec.skill_args})", "")
                    sout = _sk.run_cli_skill(
                        dec.skill or "",
                        dict(dec.skill_args or {}),
                        timeout_s=float(cfg.get("skills", {}).get("cli_timeout_s", 120.0)),
                    )
                    desc = (
                        f"skill:{dec.skill}("
                        f"{str(dec.skill_args)[:80]}) -> "
                        f"{'ok' if sout.get('ok') else 'falha'} "
                        f"code={sout.get('code')}"
                    )
                    tm["skill"] = {
                        "name": dec.skill,
                        "ok": sout.get("ok"),
                        "code": sout.get("code"),
                        "stdout": str(sout.get("stdout", ""))[-500:],
                        "stderr": str(sout.get("stderr", ""))[-500:],
                    }
                elif list(getattr(dec, "steps", []) or []):
                    # F6: sequência de até 3 primitivas c/ guardas locais entre
                    # elas e verificação final; modal/foco inesperado interrompe.
                    acts = list(dec.steps or [])
                    if dry_run:
                        desc = f"dry_run:sequence({len(acts)})"
                    else:
                        parts = []
                        expected_title = tm.get("uia_title", "")
                        for i, prim in enumerate(acts):
                            if safety.stop_requested():
                                raise RuntimeError("sequência interrompida: stop")
                            if i > 0:
                                _it, cur_title, _wr = active_window_snapshot()
                                if (
                                    prim.type == "type"
                                    and cur_title
                                    and expected_title
                                    and cur_title != expected_title
                                ):
                                    raise RuntimeError(
                                        f"sequência interrompida: foco mudou "
                                        f"{expected_title!r} -> {cur_title!r} "
                                        f"antes de type; reobserve"
                                    )
                            parts.append(execute(prim))
                        desc = f"sequence({'+'.join(parts)})"
                else:
                    # R2: alvo visual de frame obsoleto nunca clica — janela ou
                    # modal mudou entre captura e clique? Reobserve.
                    stale = _visual_stale_note(dec, tm)
                    if stale:
                        raise RuntimeError(stale)
                    desc = execute(dec.action)
            except (ValueError, RuntimeError) as e:
                # coords fora da tela, app fora da whitelist...: o planner
                # recebe o motivo e tenta outra coisa; nunca clica no escuro.
                _log({"step": step, "event": "refused", "error": str(e)})
                if fail_step("acao recusada", str(e)):
                    continue
                result = "stuck"
                break
            retries = 0
            exec_ms = (time.perf_counter() - e0) * 1000
            metrics["execution_ms"] += exec_ms
            emit("EXEC", desc, f"{exec_ms:.0f}ms")

            label = desc
            if src == "vocaela" and tm.get("visual"):
                va = tm["visual"]
                label = (
                    f'visual "{tm.get("planner_decision", {}).get("instruction", "")}"'
                    f" -> {va.get('type')}"
                )
            if dec.action.type == "answer":
                # fato reportado (planner ou Vocaela): devolve ao planner, sem input
                who = "planner" if src == "planner" else "visão"
                ctx["last_error"] = f"{who} respondeu: {dec.action.text}"[:300]

            if dec.kind == "finish":
                ctx["hist_labels"].append(label)
                _log(
                    {
                        "step": step,
                        "event": "completion",
                        "source": dec.source,
                        "kind": dec.kind,
                        "did": label,
                        "action": dec.action.model_dump(),
                        "evidences": list(tm.get("planner_decision", {}).get("evidences") or []),
                        "timings": tm,
                    }
                )
                result = "done"
                break

            ok, vnote, ui_after = verify(
                _verify_action(dec),
                instruction,
                cfg,
                before_title=tm.get("uia_title", ""),
                dry_run=dry_run,
            )
            # o planner vê ação + resultado observado no histórico
            ctx["hist_labels"].append(f"{label} => {vnote}")
            ctx["last_result"] = f"{label} => {vnote}"[:600]
            cnote = ""
            try:
                effect_type = dec.action.type
                if dec.action.type == "hotkey":
                    effect_type = f"hotkey:{(dec.action.key or '').lower()}"
                # R2 fatia 3: confirmação específica com UIA antes/depois
                # (modal dispensado, conteúdo visível/novo). Título sozinho
                # continua sem confirmar — as listas vêm do snapshot real.
                ui_kwargs: dict = {}
                if effect_type in ("click", "scroll"):
                    ui_kwargs = {"ui_before": list(tm.get("ui_names", []) or []),
                                 "ui_after": list(ui_after or [])}
                confirmed, cnote = verif.confirm_effect(
                    effect_type, dec.action.text or "", tm.get("uia_title", ""), vnote, vnote,
                    **ui_kwargs,
                )
                if ok and confirmed:
                    statemod.add_evidence(ctx.get("task_state"), f"{label} => {vnote}")
            except Exception:
                cnote = ""
                confirmed = False
            # R1: resultado estruturado (ID, enviado/confirmado, erro, pós-estado,
            # evidências). Só p/ atuação real (action/sequence/skill): pergunta
            # e percepção não enviam input — já têm tm["ask"]/tm["perception"].
            # Dry-run nunca envia: sent=not_sent, sem evidência.
            if dec.kind in ("action", "sequence", "skill"):
                try:
                    from schemas import ActionResult as _AR
                    from schemas import new_id as _nid

                    ev_label = f"{label} => {vnote}"
                    ar = _AR(
                        action_id=_nid("act"),
                        sent=("not_sent" if dry_run else "sent"),
                        confirmed=bool(ok and confirmed and not dry_run),
                        evidences=([ev_label] if (ok and confirmed and not dry_run) else []),
                        error=("" if ok else vnote[:200]),
                        post_state=vnote[:300],
                    )
                    tm["action_result"] = ar.model_dump()
                    ctx["last_action_result"] = ar.model_dump()
                except Exception:
                    pass
            # R2: carimba o resultado na entrada do anti-loop (vnote|confirm).
            try:
                if history and history[-1][0] == k and len(history[-1]) > 2:
                    history[-1][2] = f"{vnote[:120]}|{cnote[:80]}"
            except Exception:
                pass
            emit("VERIFY", vnote, "")
            metrics["steps"] += 1
            _log(
                {
                    "step": step,
                    "source": dec.source,
                    "confidence": dec.confidence,
                    "reason": dec.reason,
                    "did": desc,
                    "action": dec.action.model_dump(),
                    "verify": vnote,
                    "confirm": cnote,
                    "timings": tm,
                    "cpu": psutil.cpu_percent(interval=None),
                    "mem": round(psutil.virtual_memory().percent, 1),
                }
            )
            metrics["step_ms"] += (time.perf_counter() - s0) * 1000
            step += 1
        else:
            result = "max_steps"
    except KeyboardInterrupt:
        result = "aborted"
        print("\nabortado via Ctrl+C.")
    finally:
        safety.stop()
        try:
            import overlay as _ov

            _ov.stop()
        except Exception:
            pass
        server.stop_servers(procs)  # só encerra os que NÓS subimos

    total_ms = time.perf_counter() - total0
    print(f"\n{result.upper()}")
    print(f"Total: {total_ms:.1f}s")
    pcalls = metrics["planner_calls"]
    vcalls = metrics["vision_calls"]
    summary = {
        "test": instruction,
        "result": result,
        "steps": metrics["steps"],
        "retries": metrics["retries"],
        "planner_calls": pcalls,
        "vocaela_calls": vcalls,
        "total_s": round(total_ms, 1),
        "avg_planner_ms": round(metrics["planner_ms"] / pcalls, 1) if pcalls else 0,
        "avg_vision_ms": round(metrics["vision_ms"] / vcalls, 1) if vcalls else 0,
        "ram_pct": round(psutil.virtual_memory().percent, 1),
        "vram": _vram(),
        "metrics_ms": {
            "planner": round(metrics["planner_ms"], 1),
            "uia": round(metrics["uia_ms"], 1),
            "screenshot": round(metrics["screenshot_ms"], 1),
            "vision": round(metrics["vision_ms"], 1),
            "exec": round(metrics["execution_ms"], 1),
        },
        "dry_run": dry_run,
        "run_id": run_id,
    }
    print(f"log em {LOG}" + (f" + runs/{run_id}/" if run_id else ""))
    # F0: espelha o legado em runs/<id>/ (nunca apaga o anterior).
    if run_id:
        try:
            import shutil

            import telemetry as _tel2

            d = _tel2.run_dir(run_id)
            if LOG.exists():
                shutil.copyfile(LOG, d / "run.jsonl")
            _tel2.write_json(d / "summary.json", summary)
        except Exception:
            pass
    return summary
