"""Windows UI Automation via pywinauto (backend uia). Rápido e podado.

F2: alvos por ID/frame (§4.2–4.3). `snapshot()` devolve `Observation` com
`ElementRef`s vinculados (observation_id + element_id; IDs nunca
reutilizados entre snapshots). `active_window_snapshot()` legado é
compatível e delega p/ o novo caminho.
"""
from __future__ import annotations

import time

from overlay import is_overlay_title

MAX_DEPTH = 6

# R2: diagnóstico do último snapshot (lido pelo loop p/ telemetria; a tupla
# de retorno é estável p/ os mocks). Status: ok | provider_empty | truncated
# | timeout_partial | no_window | error.
LAST_DIAG: dict = {}


def _set_diag(**fields) -> None:
    global LAST_DIAG
    LAST_DIAG = dict(fields)


def _diag_text(diag: dict) -> str:
    """Linha de cobertura p/ Observation (puro, testável)."""
    st = diag.get("status", "unknown")
    n = int(diag.get("count", 0))
    mx = int(diag.get("max_elements", 0))
    ms = float(diag.get("elapsed_ms", 0.0))
    base = f"{n}/{mx} elementos em {ms:.0f}ms"
    if st == "ok":
        return base + "; lista completa do snapshot."
    if st == "provider_empty":
        return base + ("; PROVIDER VAZIO: janela sem conteúdo acessível "
                       "(canvas?/custom UI) — use visual_action/perceive.")
    if st == "truncated":
        return base + ("; TRUNCADO: ausência na lista não prova inexistência; "
                       "expanda ramo/região.")
    if st == "timeout_partial":
        return base + "; TIMEOUT PARCIAL: leitura incompleta, não conclusiva sobre ausência."
    if st == "no_window":
        return "sem janela ativa detectada; abra/focalize o app antes de agir."
    err = str(diag.get("error", "") or "falha de leitura")[:120]
    return f"{base}; ERRO de leitura UIA: {err}."


def focused_value(max_len: int = 80) -> str:
    """Valor/texto do elemento com foco de teclado (Edit/Document), best-effort.

    Serve p/ o planner saber se `type_text` chegou onde devia. "" se não der.
    """
    try:
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo

        el = IUIA().iuia.GetFocusedElement()
        w = UIAWrapper(UIAElementInfo(el))
        val = ""
        try:
            val = w.iface_value.CurrentValue or ""
        except Exception:
            try:
                val = w.legacy_properties().get("Value") or ""
            except Exception:
                val = ""
        if not val:
            try:
                val = w.window_text() or ""
            except Exception:
                val = ""
        val = " ".join(str(val).split())
        return val[:max_len] + ("…" if len(val) > max_len else "")
    except Exception:
        return ""


def focused_control_type() -> str:
    """Tipo UIA do elemento com foco de teclado (`Edit`, `Document`...), "" se não der."""
    try:
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo

        el = IUIA().iuia.GetFocusedElement()
        return str(UIAWrapper(UIAElementInfo(el)).element_info.control_type or "")
    except Exception:
        return ""


def _active_window():
    """Wrapper da janela ativa ou None (filtro de overlay incluído).

    Extração R2 da descoberta antes embutida no snapshot: mesma lógica
    (handle em foreground → foco → primeira com título), reutilizada pela
    expansão de ramo.
    """
    from pywinauto import Desktop

    try:
        desk = Desktop(backend="uia")
    except Exception:
        return None
    active = None
    # 1. tenta handle da janela em foreground (mais preciso)
    try:
        import ctypes

        h = ctypes.windll.user32.GetForegroundWindow()
        if h:
            try:
                active = desk.window(handle=h).wrapper_object()
                try:
                    # O overlay (borda "controlado") pode estar em
                    # foreground: nunca é a janela do app (era 'tk' e o
                    # planner tentava focus("tk") no próprio overlay).
                    if is_overlay_title(active.window_text() or ""):
                        active = None
                except Exception:
                    pass
            except Exception:
                active = None
    except Exception:
        pass
    # 2. fallback: enumera e pega a que tem foco
    if active is None:
        try:
            wins = desk.windows(top_level_only=True, visible_only=True)
        except Exception as e:
            raise RuntimeError(f"enumerate_failed: {e}")
        for w in wins:
            try:
                if is_overlay_title(w.window_text() or ""):
                    continue
                for attr in ("has_focus", "is_active", "has_keyboard_focus"):
                    fn = getattr(w, attr, None)
                    if callable(fn) and fn():
                        active = w
                        break
                if active is not None:
                    break
            except Exception:
                continue
        # 3. fallback final: primeira com título
        if active is None:
            for w in wins:
                try:
                    if is_overlay_title(w.window_text() or ""):
                        continue
                    if (w.window_text() or "").strip():
                        active = w
                        break
                except Exception:
                    continue
    return active


_TEXT_TYPES = {"edit", "document", "text", "hyperlink", "listitem", "combobox"}


def _element_text(elem, ctype: str, name: str) -> str:
    """Texto/valor de elemento textual (preço, campo, item). Best-effort.

    R2: preserva textos úteis (ex.: preço em Text, conteúdo de Edit).
    Retorna "" quando igual ao nome (window_text ecoa o nome) ou ilegível.
    """
    if (ctype or "").strip().lower() not in _TEXT_TYPES:
        return ""
    try:
        try:
            v = elem.iface_value.CurrentValue or ""
        except Exception:
            v = ""
        if not v:
            try:
                v = elem.window_text() or ""
            except Exception:
                v = ""
        v = " ".join(str(v).split())
        if not v or v == (name or "").strip():
            return ""
        return v[:200]
    except Exception:
        return ""


def active_window_snapshot(timeout: float = 5.0,
                           max_elements: int = 120) -> tuple[list[dict], str, tuple | None]:
    """Elementos úteis da janela ATIVA, formato compacto p/ decisão rápida.

    Retorna (items, title, wrect) onde items =
    [{id, name, type, bounds, value, context}]. Ignora invisíveis, sem nome,
    com área trivial ou fora da janela/tela. `value` traz texto de tipos
    textuais (R2); "" quando igual ao nome ou ilegível.
    """
    t0 = time.perf_counter()
    items: list[dict] = []

    def _elapsed_ms() -> float:
        return (time.perf_counter() - t0) * 1000.0

    import platform_backend

    if platform_backend.active() is not None:
        # backend visual (Wayland): sem árvore de acessibilidade; honesto, não "vazio por acaso"
        _set_diag(status="error", error="sem UIA neste backend (modo visual: visual_action/OCR)",
                  count=0, max_elements=max_elements, elapsed_ms=_elapsed_ms())
        return [], "", None

    try:
        active = _active_window()
        if active is None:
            _set_diag(status="no_window", count=0, max_elements=max_elements,
                      elapsed_ms=_elapsed_ms())
            return [], "", None
        try:
            title = active.window_text() or ""
        except Exception:
            title = ""
        try:
            wr = active.rectangle()
            wrect = (wr.left, wr.top, wr.right, wr.bottom)
        except Exception:
            wrect = None
        try:  # tela virtual (multi-monitor): candidatos fora dela são lixo
            import ctypes as _ct

            _u = _ct.windll.user32
            vx, vy, vw, vh = (_u.GetSystemMetrics(76), _u.GetSystemMetrics(77),
                              _u.GetSystemMetrics(78), _u.GetSystemMetrics(79))
        except Exception:
            vx, vy, vw, vh = (0, 0, 10000, 10000)

        flags = {"timed_out": False}

        def walk(elem, depth: int, ancestors: tuple = ()) -> None:
            if len(items) >= max_elements or depth > MAX_DEPTH:
                return
            if time.perf_counter() - t0 > timeout:
                flags["timed_out"] = True
                return
            try:
                info = elem.element_info
                name = (getattr(info, "name", "") or "").strip()[:120]
                ctype = str(getattr(info, "control_type", "") or "")[:60]
                try:
                    r = info.rectangle
                    bounds = (r.left, r.top, r.right, r.bottom)
                except Exception:
                    bounds = (0, 0, 0, 0)
                bl, bt, br, bb = bounds
                area_ok = (br - bl) > 4 and (bb - bt) > 4
                cx, cy = (bl + br) // 2, (bt + bb) // 2
                on_screen = vx <= cx < vx + vw and vy <= cy < vy + vh
                inside = True
                if wrect is not None:
                    wl, wt, wrr, wb = wrect
                    inside = not (br < wl or bl > wrr or bb < wt or bt > wb)
                next_anc = ancestors + (name,) if name else ancestors
                if name and area_ok and inside and on_screen:
                    items.append({"id": len(items), "name": name,
                                  "type": ctype, "bounds": list(bounds),
                                  "value": _element_text(elem, ctype, name),
                                  "context": " > ".join(next_anc[-3:-1])})
                for child in elem.children():
                    walk(child, depth + 1, next_anc)
                    if len(items) >= max_elements:
                        break
            except Exception:
                return

        walk(active, 0)
        if flags["timed_out"] and len(items) < max_elements:
            status = "timeout_partial"
        elif len(items) >= max_elements:
            status = "truncated"
        elif not items and title:
            status = "provider_empty"
        else:
            status = "ok"
        _set_diag(status=status, count=len(items), max_elements=max_elements,
                  elapsed_ms=_elapsed_ms(), timed_out=flags["timed_out"],
                  window=(title or "")[:80])
        return items, title, wrect
    except Exception as e:
        _set_diag(status="error", error=str(e)[:200], count=len(items),
                  max_elements=max_elements, elapsed_ms=_elapsed_ms())
        return [], "", None


def expand_subtree(target: str, timeout: float = 5.0,
                   max_elements: int = 40) -> tuple[list[dict], str, str]:
    """Releitura enraizada no ramo cujo nome contém `target` (R2).

    Retorna (items, title, note): items = descendentes do ramo (até
    max_elements, com value/context); note = "" ou diagnóstico ("não está
    na árvore", "ambíguo: ..." p/ desambiguar). Só leitura, sem input.
    """
    want = (target or "").strip().lower()
    if not want:
        return [], "", "expand vazio: use expand:<nome visível na árvore>"
    t0 = time.perf_counter()
    try:
        active = _active_window()
    except Exception as e:
        return [], "", f"expand falhou: {e}"[:200]
    if active is None:
        return [], "", "expand sem janela ativa"
    try:
        title = active.window_text() or ""
    except Exception:
        title = ""
    matches: list = []
    try:
        queue = list(active.children())
        while queue and len(matches) < 5:
            if time.perf_counter() - t0 > timeout:
                break
            el = queue.pop(0)
            try:
                nm = (el.element_info.name or "").strip()
            except Exception:
                continue
            if nm and want in nm.lower():
                matches.append(el)
                continue  # não desce em ramo já casado
            try:
                queue.extend(el.children())
            except Exception:
                pass
    except Exception:
        pass
    if not matches:
        return [], title, f"expand: {target!r} não está na árvore atual"
    if len(matches) > 1:
        labels = []
        for m in matches:
            try:
                labels.append(f"{m.element_info.control_type}:"
                              f"{(m.element_info.name or '')[:40]}")
            except Exception:
                labels.append("?")
        return [], title, (
            f"expand ambíguo: {len(matches)} ramos p/ {target!r} "
            f"({'; '.join(labels)}); seja mais específico"
        )
    root = matches[0]
    try:
        root_name = (root.element_info.name or "").strip() or target
    except Exception:
        root_name = target
    items: list[dict] = []

    def walk(elem, depth: int, ancestors: tuple = ()) -> None:
        if len(items) >= max_elements or depth > MAX_DEPTH:
            return
        if time.perf_counter() - t0 > timeout:
            return
        try:
            info = elem.element_info
            name = (getattr(info, "name", "") or "").strip()[:120]
            ctype = str(getattr(info, "control_type", "") or "")[:60]
            try:
                r = info.rectangle
                bounds = (r.left, r.top, r.right, r.bottom)
            except Exception:
                bounds = (0, 0, 0, 0)
            bl, bt, br, bb = bounds
            next_anc = ancestors + (name,) if name else ancestors
            if name and (br - bl) > 4 and (bb - bt) > 4:
                items.append({"id": len(items), "name": name,
                              "type": ctype, "bounds": list(bounds),
                              "value": _element_text(elem, ctype, name),
                              "context": " > ".join((root_name,) + next_anc[-2:-1])})
            for child in elem.children():
                walk(child, depth + 1, next_anc)
                if len(items) >= max_elements:
                    break
        except Exception:
            return

    walk(root, 0, ())
    note = "" if items else f"expand: ramo {root_name!r} sem descendentes nomeados"
    return items, title, note


def snapshot(timeout: float = 5.0, max_elements: int = 120) -> object:
    """Snapshot F2/R2 vinculado: Observation com ElementRefs + cobertura.

    IDs são índices do snapshot atual + observation_id único; nunca
    reutilizar refs entre snapshots (stale -> erro estruturado, não clique).
    Ausência em lista truncada NÃO significa inexistência na UIA.
    R2: `error` distingue sem-janela/timeout/provider-vazio/erro de
    provider; `coverage` carrega o diagnóstico em texto.
    """
    from schemas import ElementRef, Observation, new_id

    items, title, wrect = active_window_snapshot(
        timeout=timeout, max_elements=max_elements)
    try:
        diag = dict(LAST_DIAG or {})
    except Exception:
        diag = {}
    _ = wrect
    obs_id = new_id("obs")
    refs = []
    for it in items:
        refs.append(ElementRef(
            observation_id=obs_id, element_id=int(it.get("id", 0)),
            role=str(it.get("type", "?")), name=str(it.get("name", "")),
            context=str(it.get("context", "") or title[:80]),
            value=str(it.get("value", "") or "")[:200],
            state=str(it.get("state", "") or ""),
            bounds=list(it.get("bounds") or [0, 0, 0, 0])))
    truncated = len(refs) >= max_elements or bool(diag.get("timed_out"))
    status = str(diag.get("status", "") or ("ok" if refs or not title else "provider_empty"))
    error = "" if status == "ok" else status
    if status == "error" and diag.get("error"):
        error = f"error: {diag.get('error')}"
    return Observation(
        observation_id=obs_id, app=title, focus=title,
        uia=refs,
        coverage=_diag_text({**diag, "status": status, "count": len(refs),
                             "max_elements": max_elements}),
        truncated=truncated,
        error=error)


def resolve_ref(items: list[dict], ref: str,
                observation_id: str) -> dict | None:
    """Resolve alvo por ID da observação (F2). Stale -> None.

    `ref` = "<observation_id>#<element_id>". observation_id diferente da
    atual = estado obsoleto (nunca clicar por nome adivinhado).
    """
    try:
        oid, _, eid = ref.partition("#")
        if oid != observation_id or not eid.lstrip("-").isdigit():
            return None
        want = int(eid)
    except Exception:
        return None
    for it in items:
        try:
            if int(it.get("id", -1)) == want:
                return it
        except Exception:
            continue
    return None
