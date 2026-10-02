"""Observação F2: screenshot rápida via mss + resize via Pillow.

Coordenadas vinculadas ao frame (§4.2): Python transforma frame→desktop
virtual uma única vez. Recorte/zoom tem transformação explícita; nunca
reaproveitar pixels de imagem velha nem misturar bounds UIA com pixels
redimensionados. `post_state` reutilizável enquanto válido (invalidado em
escrita, troca de janela, modal, rolagem ou evento relevante).
"""
from __future__ import annotations

import time
from pathlib import Path

import mss
from PIL import Image

LAST_PNG = Path("last.png")

# post_state compacto reutilizável (F2): válido até invalidação explícita.
_post_state: dict = {"text": "", "valid": False, "reason": "empty"}


def _grab_virtual() -> tuple[Image.Image, tuple[int, int]]:
    """Captura o DESKTOP VIRTUAL inteiro (todos os monitores).

    Retorna (imagem, (left, top)): o pixel (0,0) da imagem corresponde à
    coordenada de tela (left, top) — negativa se houver monitor à
    esquerda/acima do primário. mss.monitors[0] é a caixa envolvente de
    todos, não o primário.
    """
    import platform_backend

    if platform_backend.active() is not None:  # Wayland: portal ScreenCast (mss só vê preto)
        return platform_backend.active().grab()
    with mss.mss() as sct:
        mon = sct.monitors[0]
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.rgb)
    return img, (int(mon["left"]), int(mon["top"]))


def take_screenshot(dest: str | Path = LAST_PNG) -> tuple[str, tuple[int, int]]:
    """Captura o desktop virtual, salva PNG full-res, retorna (path, (w,h))."""
    dest = Path(dest)
    img, _ = _grab_virtual()
    w, h = img.size
    img.save(dest)
    return str(dest), (w, h)


def _foreground_rect() -> tuple[int, int, int, int] | None:
    """Rect da janela ativa (left, top, right, bottom) ou None."""
    try:
        import ctypes

        u = ctypes.windll.user32
        h = u.GetForegroundWindow()
        if not h:
            return None
        r = (ctypes.c_long * 4)()
        if not u.GetWindowRect(h, r):
            return None
        left, top, right, bottom = (r[0], r[1], r[2], r[3])
        if right - left < 50 or bottom - top < 50:
            return None
        return (left, top, right, bottom)
    except Exception:
        return None


def capture_for_vision(
    max_long_edge: int = 1024,
) -> tuple["Image.Image", tuple[int, int], tuple[int, int]]:
    """Captura SÓ quando a visão é necessária (chamar só no branch visual).

    Prefere a janela ativa (crop preservando offset); senão o desktop
    virtual inteiro. Retorna (PIL.Image, origin_xy, full_size), com
    origin_xy em COORDENADAS DE TELA (pode ser negativo em multi-monitor):
    as coordenadas do Vocaela (0..1) são relativas à imagem retornada →
    `origin + frac * size` dá o pixel físico. Aspect ratio preservado.
    """
    full, (vx, vy) = _grab_virtual()
    return crop_to_rect(full, (vx, vy), _foreground_rect())


def crop_to_rect(full: Image.Image, virtual_origin: tuple[int, int],
                 rect: tuple[int, int, int, int] | None,
                 ) -> tuple[Image.Image, tuple[int, int], tuple[int, int]]:
    """Recorta `rect` (coords de tela) de `full`, cujo (0,0) é `virtual_origin`.

    Puro (sem GUI) p/ teste. Retorna (crop, origin_tela, full_size).
    """
    fw, fh = full.size
    vx, vy = virtual_origin
    if rect:
        left, top, right, bottom = rect
        # tela -> pixel da imagem, limitado à imagem
        pl, pt = max(0, left - vx), max(0, top - vy)
        pr, pb = min(fw, right - vx), min(fh, bottom - vy)
        if pr - pl >= 50 and pb - pt >= 50:
            return full.crop((pl, pt, pr, pb)), (pl + vx, pt + vy), (fw, fh)
    return full, (vx, vy), (fw, fh)


def _system_dpi() -> float:
    """Escala do sistema (96 DPI = 1.0). Best-effort, nunca levanta."""
    try:
        import ctypes

        u = ctypes.windll.user32
        dpi = u.GetDpiForSystem() if hasattr(u, "GetDpiForSystem") else 96
        return float(dpi) / 96.0
    except Exception:
        return 1.0


def frame_ref_for(
    origin: tuple[int, int],
    size_px: tuple[int, int],
    title: str,
    observation_id: str,
) -> object:
    """FrameRef vinculado a uma captura já feita (R2).

    `origin` em coords de tela (pode ser negativo em multi-monitor),
    `size_px` o tamanho da imagem cujas coords 0..1 o localizador usa.
    Transformação explícita única: tela = origin + frac * size.
    """
    from schemas import FrameRef, new_id

    w, h = int(size_px[0]), int(size_px[1])
    return FrameRef(
        frame_id=new_id("frm"),
        observation_id=observation_id,
        window_title=(title or "")[:120],
        origin=[int(origin[0]), int(origin[1])],
        scale_dpi=_system_dpi(),
        size=[w, h],
        captured_at=time.time(),
    )


def list_monitors() -> list[dict]:
    """Monitores com posições negativas, DPI e z-order básico (F2, best-effort)."""
    try:
        with mss.mss() as sct:
            mons = [{"index": i, "left": int(m["left"]), "top": int(m["top"]),
                     "width": int(m["width"]), "height": int(m["height"])}
                    for i, m in enumerate(sct.monitors[1:], start=1)]
        try:
            import ctypes

            u = ctypes.windll.user32
            dpi = u.GetDpiForSystem() if hasattr(u, "GetDpiForSystem") else 96
            scale = float(dpi) / 96.0
        except Exception:
            scale = 1.0
        for m in mons:
            m["scale_dpi"] = scale
        return mons
    except Exception:
        return []


def capture_frame(max_long_edge: int = 1024,
                  observation_id: str = "") -> tuple[Image.Image, object]:
    """Captura + FrameRef vinculado (F2). Transformação explícita única."""
    from schemas import FrameRef, new_id

    full, (vx, vy) = _grab_virtual()
    rect = _foreground_rect()
    crop, origin, full_size = crop_to_rect(full, (vx, vy), rect)
    try:
        import ctypes

        u = ctypes.windll.user32
        dpi = u.GetDpiForSystem() if hasattr(u, "GetDpiForSystem") else 96
        scale = float(dpi) / 96.0
    except Exception:
        scale = 1.0
    try:
        title = ""
        import ctypes as _ct

        h = _ct.windll.user32.GetForegroundWindow()
        if h:
            from pywinauto import Desktop

            try:
                title = Desktop(backend="uia").window(
                    handle=int(h)).window_text() or ""
            except Exception:
                title = ""
    except Exception:
        title = ""
    frame = FrameRef(frame_id=new_id("frm"), observation_id=observation_id,
                     window_title=title[:120], origin=[int(origin[0]),
                                                      int(origin[1])],
                     scale_dpi=scale, size=[int(crop.size[0]),
                                           int(crop.size[1])],
                     captured_at=time.time())
    _ = (max_long_edge, full_size, rect)
    return crop, frame


def frame_is_stale(frame: object, max_age_s: float = 5.0) -> bool:
    """Pixels velhos nunca reaproveitados: idade > limite = stale."""
    try:
        age = time.time() - float(getattr(frame, "captured_at", 0.0))
        return age > max_age_s
    except Exception:
        return True


def set_post_state(text: str) -> None:
    _post_state.update({"text": (text or "")[:300], "valid": True,
                        "reason": "fresh"})


def get_post_state() -> tuple[str, bool]:
    return str(_post_state.get("text", "")), bool(_post_state.get("valid"))


def invalidate_post_state(reason: str) -> None:
    """Escrita, troca de janela, modal, rolagem ou evento relevante."""
    _post_state.update({"valid": False, "reason": (reason or "event")[:120]})


if __name__ == "__main__":
    t0 = time.perf_counter()
    p, (w, h) = take_screenshot()
    dt = (time.perf_counter() - t0) * 1000
    print(f"saved={p} size={w}x{h} in {dt:.0f}ms")
