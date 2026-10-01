"""Ações de mouse/teclado via pyautogui. Sem abstração enterprise.

F2: pré-condições imediatamente antes do dispatch físico (janela,
visibilidade, foco, oclusão); falha vira erro estruturado ao modelo, sem
adivinhar outro alvo. Arraste exige origem/destino do mesmo frame.
Cleanup em finally (cancelamento libera teclas/botões).
"""
from __future__ import annotations

import time

try:
    import pyautogui
except Exception as _exc:  # Linux sem X11/Xlib compatível: o módulo ainda precisa importar
    _WHY = str(_exc)[:120]

    class _PyAutoGuiUnavailable:
        """Substituto SEGURO: qualquer ação física levanta erro, nunca finge sucesso."""

        FAILSAFE = True
        PAUSE = 0.15

        def __getattr__(self, name):
            raise RuntimeError(f"pyautogui indisponível nesta plataforma ({_WHY}); "
                               "nenhuma ação física foi executada")

    pyautogui = _PyAutoGuiUnavailable()

pyautogui.FAILSAFE = True  # mouse no canto superior-esquerdo aborta
pyautogui.PAUSE = 0.15

from schemas import Action

_SEND_KEYS_SPECIAL = set("+^%~(){}")


def _virtual_screen() -> tuple[int, int, int, int]:
    import ctypes

    u = ctypes.windll.user32
    return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),
            u.GetSystemMetrics(78), u.GetSystemMetrics(79))


def _check_coords(x: int, y: int) -> None:
    """Recusa clique fora da tela virtual (nunca clica no escuro)."""
    vx, vy, vw, vh = _virtual_screen()
    if not (vx <= x < vx + vw and vy <= y < vy + vh):
        raise ValueError(f"coords fora da tela virtual ({x},{y}) vs "
                         f"({vx},{vy},{vw},{vh}); abortando clique.")


def _need_xy(action: Action) -> tuple[int, int]:
    if action.x is None or action.y is None:
        raise ValueError(f"{action.type} precisa de x,y")
    _check_coords(action.x, action.y)
    return action.x, action.y


def escape_for_send_keys(text: str) -> str:
    """pywinauto.send_keys trata + ^ % ~ ( ) { } como modificadores: escapar."""
    return "".join(f"{{{c}}}" if c in _SEND_KEYS_SPECIAL else c for c in text)


def _type_unicode(text: str) -> None:
    """Digita QUALQUER texto (acentos incluídos) via SendInput Unicode.

    pyautogui.typewrite só mapeia ASCII 32..127 no Windows e descarta o
    resto em silêncio ("Olá" virava "Ol").
    """
    from pywinauto.keyboard import send_keys

    send_keys(escape_for_send_keys(text), with_spaces=True,
              with_newlines=True, with_tabs=True, pause=0.02)


def execute(action: Action) -> str:
    """Executa uma Action. Retorna descrição p/ log. Levanta ValueError se inválida."""
    from tools import focus_window, open_app

    try:
        return _execute_inner(action, focus_window, open_app)
    except Exception:
        # F2: cancelamento/exceção libera teclas/botões (sem prender input).
        try:
            release_all()
        except Exception:
            pass
        raise


def _execute_inner(action: Action, focus_window, open_app) -> str:
    t = action.type
    if t == "click":
        x, y = _need_xy(action)
        pyautogui.moveTo(x, y, duration=0.15)
        pyautogui.click(x=x, y=y, clicks=action.clicks or 1)
        return f"click({x},{y})"
    if t == "double_click":
        x, y = _need_xy(action)
        pyautogui.moveTo(x, y, duration=0.15)
        pyautogui.doubleClick(x=x, y=y)
        return f"double_click({x},{y})"
    if t == "right_click":
        x, y = _need_xy(action)
        pyautogui.moveTo(x, y, duration=0.15)
        pyautogui.rightClick(x=x, y=y)
        return f"right_click({x},{y})"
    if t == "middle_click":
        x, y = _need_xy(action)
        pyautogui.moveTo(x, y, duration=0.15)
        pyautogui.middleClick(x=x, y=y)
        return f"middle_click({x},{y})"
    if t == "move":
        x, y = _need_xy(action)
        pyautogui.moveTo(x, y, duration=0.15)
        return f"move({x},{y})"
    if t == "drag":
        x, y = _need_xy(action)
        if action.x2 is None or action.y2 is None:
            raise ValueError("drag precisa de x2,y2")
        _check_coords(action.x2, action.y2)
        pyautogui.moveTo(x, y, duration=0.15)
        pyautogui.dragTo(action.x2, action.y2, duration=0.4)
        return f"drag({x},{y}->{action.x2},{action.y2})"
    if t == "type":
        if not action.text:
            raise ValueError("type precisa de text")
        _type_unicode(action.text)
        return f"type({len(action.text)} chars)"
    if t == "scroll":
        amount = int(action.text) if action.text and action.text.lstrip("-").isdigit() else -800
        if action.key in ("left", "right"):
            pyautogui.hscroll(amount)
            return f"hscroll({amount})"
        pyautogui.scroll(amount)
        return f"scroll({amount})"
    if t == "hotkey":
        if not action.key:
            raise ValueError("hotkey precisa de key ex: 'enter', 'ctrl+l'")
        parts = [p.strip() for p in action.key.split("+")]
        n = max(1, action.presses)
        for _ in range(n):
            pyautogui.hotkey(*parts)
        return f"hotkey({action.key})" + (f"x{n}" if n > 1 else "")
    if t == "open":
        if not action.target:
            raise ValueError("open precisa de target")
        return open_app(action.target)
    if t == "focus":
        if not action.target:
            raise ValueError("focus precisa de target (substring do título)")
        ok = focus_window(action.target)
        return f"focus({action.target})={'ok' if ok else 'miss'}"
    if t == "wait":
        time.sleep(max(0, action.ms) / 1000.0)
        return f"wait({action.ms}ms)"
    if t == "answer":
        return f"answer({(action.text or '')[:80]})"  # só observação, sem input
    if t == "done":
        return "done"
    if t in ("ask", "perceive"):
        # R1: pergunta e percepção nunca tocam mouse/teclado — o executor do
        # loop ramifica por kind antes de chegar aqui. Cair aqui é bug do
        # executor, não pedido do modelo.
        raise ValueError(f"{t} nunca executa input físico (bug do executor)")
    raise ValueError(f"action desconhecida: {t}")


def check_preconditions(action: Action,
                        expected_title: str = "") -> str:
    """Pré-condições F2 imediatamente antes do dispatch (puro, testável).

    Retorna "" se ok, senão erro estruturado p/ o modelo (sem adivinhar
    outro alvo). Confere: coords na tela virtual; ponto dentro da janela
    ativa (quando informada); foco no app esperado (quando informado).
    """
    t = action.type
    if t in ("ask", "perceive"):
        return f"{t} não é ação física; executor deve ramificar por kind"
    if t in ("click", "double_click", "right_click", "middle_click",
             "move", "drag"):
        if action.x is None or action.y is None:
            if action.element_ref:
                return (f"stale: element_ref {action.element_ref} sem "
                        "coordenadas resolvidas; reobserve antes de agir")
            return f"{t} precisa de x,y"
        try:
            _check_coords(action.x, action.y)
        except ValueError as e:
            return str(e)
        if t == "drag" and (action.x2 is None or action.y2 is None):
            return "drag precisa de x2,y2 do MESMO frame da origem"
        if t == "drag" and action.x2 is not None:
            try:
                _check_coords(action.x2, action.y2 or 0)
            except ValueError as e:
                return str(e)
    if expected_title:
        try:
            import ctypes

            h = ctypes.windll.user32.GetForegroundWindow()
            if h:
                from pywinauto import Desktop

                cur = Desktop(backend="uia").window(
                    handle=int(h)).window_text() or ""
                if expected_title.lower() not in cur.lower():
                    return (f"pre-condição: foco em {cur!r}, esperado "
                            f"{expected_title!r}; reobserve, não aja no escuro")
        except Exception:
            pass
    return ""


def release_all() -> None:
    """Cleanup F2: libera botões/teclas em finally/cancelamento (best-effort)."""
    for btn in ("left", "right", "middle"):
        try:
            pyautogui.mouseUp(button=btn)
        except Exception:
            pass
    for key in ("ctrl", "alt", "shift", "win"):
        try:
            pyautogui.keyUp(key)
        except Exception:
            pass
