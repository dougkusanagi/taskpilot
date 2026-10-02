"""Camada de plataforma (PROTÓTIPO): ponto único onde o agente escolhe o backend físico.

Padrão = comportamento histórico: no Windows tudo segue por pywinauto/pyautogui/mss; no Linux
as ações físicas continuam recusadas (`actions._PyAutoGuiUnavailable`). Um backend alternativo
só liga por opt-in explícito (`--platform wayland`, `platform.backend` na config ou a variável
`TASKPILOT_PLATFORM`), nunca por detecção silenciosa: a suíte de testes e qualquer import
casual não podem abrir sessão de captura/controle no desktop real.

Hoje existe um único backend alternativo: `wayland` (portais ScreenCast + RemoteDesktop; ver
`docs/prototipo-wayland-2026-10-02.md`).
"""
from __future__ import annotations

import os
import sys

_ACTIVE = None  # instância do backend alternativo ou None


def enable(name: str | None = None):
    """Liga o backend `name` ('wayland') ou lê TASKPILOT_PLATFORM; ''/None/'default' = não liga."""
    global _ACTIVE
    name = (name or os.environ.get("TASKPILOT_PLATFORM") or "").strip().lower()
    if name in ("", "default", "native", "windows"):
        return _ACTIVE
    if name != "wayland":
        raise ValueError(f"platform.backend desconhecido: {name!r}; use 'wayland'")
    if _ACTIVE is None:
        from .wayland import WaylandBackend

        _ACTIVE = WaylandBackend()
    return _ACTIVE


def active():
    """Backend alternativo ligado (objeto) ou None."""
    return _ACTIVE


def disable() -> None:
    """Encerra e desliga o backend alternativo (fecha a sessão do portal)."""
    global _ACTIVE
    if _ACTIVE is not None:
        try:
            _ACTIVE.close()
        finally:
            _ACTIVE = None


def wrap_input(native):
    """Devolve `native` (pyautogui ou o substituto seguro) intacto quando nenhum backend está
    ligado; com backend, delega a ele. Identidade no Windows: nada muda no caminho padrão."""
    if sys.platform == "win32":
        return native
    return _InputDispatch(native)


class _InputDispatch:
    """Proxy fino com a API de pyautogui usada por actions.py (moveTo, click, hotkey, ...)."""

    def __init__(self, native):
        object.__setattr__(self, "_native", native)

    def _target(self):
        return _ACTIVE.input if _ACTIVE is not None else self._native

    def __getattr__(self, name):
        return getattr(self._target(), name)

    def __setattr__(self, name, value):
        setattr(self._native, name, value)
