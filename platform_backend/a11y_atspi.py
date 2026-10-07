"""Leitura AT-SPI da janela ativa (Linux): o "UIA" do backend Wayland.

Roda dentro da ponte (Python do sistema, com `gi`). Devolve só dados simples (dict/JSON) para o
planner textual saber O QUE há na tela: título da janela, elementos nomeados (botões, campos,
rótulos), texto do campo com foco. NÃO devolve coordenadas: no Wayland o AT-SPI reporta extents
relativos/zerados (a janela não sabe onde está na tela), então todo item sai com `nobounds` e quem
localiza para clicar é a visão. Informar ≠ localizar.

`snapshot(atspi=...)` aceita o módulo `Atspi` injetado: os testes usam uma árvore falsa.
"""
from __future__ import annotations

import time

# Apps do próprio desktop/infra: nunca são "a janela do usuário".
SKIP_APPS = ("gnome-shell", "xdg-desktop-portal", "ibus", "gsd-", "at-spi", "evolution",
             "goa-", "orca", "gjs", "mutter", "tracker", "gvfs")
FRAME_ROLES = ("frame", "window", "dialog", "alert", "file chooser")
# papel AT-SPI -> tipo no vocabulário que o loop já usa (nomes do pywinauto/UIA)
ROLE_TYPES = {
    "push button": "Button", "button": "Button", "toggle button": "Button",
    "check box": "CheckBox", "radio button": "RadioButton", "switch": "CheckBox",
    "text": "Edit", "entry": "Edit", "password text": "Edit", "spin button": "Edit",
    "terminal": "Edit", "document text": "Document", "document web": "Document",
    "label": "Text", "static": "Text", "heading": "Text", "link": "Hyperlink",
    "menu item": "MenuItem", "check menu item": "MenuItem", "radio menu item": "MenuItem",
    "page tab": "TabItem", "list item": "ListItem", "combo box": "ComboBox",
    "table cell": "DataItem", "tree item": "TreeItem", "slider": "Slider",
    "tool bar": "ToolBar", "menu": "Menu", "menu bar": "MenuBar",
}
TEXT_TYPES = {"Edit", "Document", "ComboBox", "Text", "Hyperlink", "ListItem"}
MAX_DEPTH = 48  # GTK4/libadwaita aninha ~25 níveis até o campo de texto
VALUE_LEN = 200


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _children(node):
    n = _safe(node.get_child_count, 0) or 0
    for i in range(n):
        ch = _safe(lambda i=i: node.get_child_at_index(i))
        if ch is not None:
            yield ch


def _has(state_set, atspi, name: str) -> bool:
    return bool(_safe(lambda: state_set.contains(getattr(atspi.StateType, name)), False))


def _text_of(node, atspi, limit: int = VALUE_LEN) -> str:
    """Conteúdo textual (campos/documentos). "" se o nó não tem interface de texto."""
    total = _safe(lambda: atspi.Text.get_character_count(node), 0) or 0
    if total <= 0:
        return ""
    txt = _safe(lambda: atspi.Text.get_text(node, 0, min(total, limit + 1)), "") or ""
    txt = " ".join(str(txt).split())
    return txt[:limit] + ("…" if len(txt) > limit else "")


def _is_infra(app_name: str) -> bool:
    low = (app_name or "").lower()
    return any(low.startswith(p) for p in SKIP_APPS)


def candidate_windows(desktop, atspi) -> list[dict]:
    """Janelas visíveis dos apps do usuário (puro sobre a árvore): [{app, node, active}]."""
    out = []
    for app in _children(desktop):
        app_name = _safe(app.get_name, "") or ""
        if _is_infra(app_name):
            continue
        for fr in _children(app):
            if _safe(fr.get_role_name, "") not in FRAME_ROLES:
                continue
            st = _safe(fr.get_state_set)
            if st is None or not _has(st, atspi, "SHOWING"):
                continue
            out.append({"app": app_name, "node": fr, "active": _has(st, atspi, "ACTIVE")})
    return out


def _has_focus_below(node, atspi, budget: list[int], depth: int = 0) -> bool:
    if budget[0] <= 0 or depth > MAX_DEPTH:
        return False
    budget[0] -= 1
    st = _safe(node.get_state_set)
    if st is not None and _has(st, atspi, "FOCUSED"):
        return True
    return any(_has_focus_below(c, atspi, budget, depth + 1) for c in _children(node))


def pick_window(cands: list[dict], atspi) -> tuple[dict | None, str]:
    """Janela ativa; devolve (candidata, como foi escolhida).

    Ordem: estado ACTIVE > janela com um filho FOCUSED > a mais recente (último app do registro).
    O GNOME headless/sem foco real pode não marcar ACTIVE; o terceiro critério é um palpite e é
    declarado como tal (`how`), nunca apresentado como certeza."""
    if not cands:
        return None, "none"
    active = [c for c in cands if c["active"]]
    if active:
        return active[-1], "active"
    for c in reversed(cands):
        if _has_focus_below(c["node"], atspi, [400]):
            return c, "focused"
    return cands[-1], "latest" if len(cands) > 1 else "only"


def snapshot(max_elements: int = 120, timeout: float = 3.0, atspi=None) -> dict:
    """Árvore da janela ativa em itens compactos (ver módulo). Nunca levanta: erro vira status."""
    t0 = time.perf_counter()

    def elapsed() -> float:
        return round((time.perf_counter() - t0) * 1000.0, 1)

    try:
        if atspi is None:
            import gi

            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi as atspi  # noqa: N813

            atspi.init()
        desktop = atspi.get_desktop(0)
        cands = candidate_windows(desktop, atspi)
        win, how = pick_window(cands, atspi)
        if win is None:
            return {"status": "no_window", "title": "", "app": "", "items": [], "focused": None,
                    "windows": [], "how": how, "elapsed_ms": elapsed()}
        frame = win["node"]
        title = (_safe(frame.get_name, "") or "").strip()
        items: list[dict] = []
        focused: dict | None = None
        flags = {"timed_out": False}
        seen: set[tuple] = set()
        unnamed = {"n": 0}

        def walk(node, depth: int, ancestors: tuple) -> None:
            nonlocal focused
            if len(items) >= max_elements or depth > MAX_DEPTH:
                return
            if time.perf_counter() - t0 > timeout:
                flags["timed_out"] = True
                return
            st = _safe(node.get_state_set)
            if st is not None and not _has(st, atspi, "SHOWING"):
                return  # subárvore invisível (aba oculta, menu fechado): poda
            role = _safe(node.get_role_name, "") or ""
            name = (_safe(node.get_name, "") or "").strip()[:120]
            ctype = ROLE_TYPES.get(role, "")
            is_focus = st is not None and _has(st, atspi, "FOCUSED")
            editable = st is not None and _has(st, atspi, "EDITABLE")
            value = _text_of(node, atspi) if (ctype in TEXT_TYPES or editable) else ""
            if value == name:
                value = ""
            if is_focus and focused is None:
                focused = {"type": ctype or role, "name": name, "value": value,
                           "editable": editable}
            usable = ctype and (name or (ctype == "Edit" and editable) or (is_focus and ctype))
            if usable:
                label = name
                if not label:  # campo sem nome: nunca deduplicar (são campos distintos), numerar
                    unnamed["n"] += 1
                    label = "campo de texto" + (f" {unnamed['n']}" if unnamed["n"] > 1 else "")
                key = (ctype, label, " > ".join(ancestors[-2:]))
                if key not in seen:
                    seen.add(key)
                    state = []
                    if is_focus:
                        state.append("focused")
                    if editable:
                        state.append("editable")
                    # GTK4 marca SENSITIVE (não ENABLED): desabilitado = nenhum dos dois
                    if st is not None and not (_has(st, atspi, "ENABLED")
                                               or _has(st, atspi, "SENSITIVE")):
                        state.append("disabled")
                    if st is not None and _has(st, atspi, "CHECKED"):
                        state.append("checked")
                    items.append({"id": len(items), "name": label, "type": ctype,
                                  "bounds": [0, 0, 0, 0], "nobounds": True, "value": value,
                                  "context": " > ".join(ancestors[-3:-1]),
                                  "state": ",".join(state)})
            nxt = ancestors + (name,) if name else ancestors
            for ch in _children(node):
                walk(ch, depth + 1, nxt)
                if len(items) >= max_elements or flags["timed_out"]:
                    break

        walk(frame, 0, ())
        focus_how = "focused" if focused is not None else ""
        if focused is None:
            # Sem estado FOCUSED (compositor headless / app sem foco real): se há UM só campo
            # editável na janela, ele é o foco provável. Declarado como palpite (`focus_how`).
            edits = [it for it in items if it["type"] == "Edit" and "editable" in it["state"]]
            if len(edits) == 1:
                e = edits[0]
                focused = {"type": e["type"], "name": e["name"], "value": e["value"],
                           "editable": True}
                focus_how = "guess"
        if flags["timed_out"] and len(items) < max_elements:
            status = "timeout_partial"
        elif len(items) >= max_elements:
            status = "truncated"
        elif not items:
            status = "provider_empty"
        else:
            status = "ok"
        return {"status": status, "title": title, "app": win["app"], "items": items,
                "focused": focused, "focus_how": focus_how, "how": how, "elapsed_ms": elapsed(),
                "windows": [(c["app"], _safe(c["node"].get_name, "") or "") for c in cands]}
    except Exception as e:  # sem barramento de acessibilidade, gi ausente, etc.
        return {"status": "error", "error": f"{type(e).__name__}: {e}"[:200], "title": "",
                "app": "", "items": [], "focused": None, "windows": [], "how": "error",
                "elapsed_ms": elapsed()}
