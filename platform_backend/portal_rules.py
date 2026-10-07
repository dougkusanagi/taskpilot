"""Regras puras sobre o que o portal concedeu (sem `gi`, para a ponte e os testes usarem igual)."""
from __future__ import annotations

KEYBOARD, POINTER = 1, 2  # bitmask `devices` do RemoteDesktop.Start


def devices_error(granted: int, token_file) -> str:
    """"" se o teclado e o ponteiro foram concedidos; senão a instrução acionável.

    O diálogo do GNOME traz "Permitir interação remota" DESLIGADO por padrão: quem só clica em
    "Compartilhar" recebe uma sessão de captura sem mouse/teclado, e cada clique do agente falharia
    depois com um erro obscuro."""
    need = KEYBOARD | POINTER
    if int(granted) & need == need:
        return ""
    return (f"a sessão foi aprovada SEM controle de mouse/teclado (devices={int(granted)}). No "
            "diálogo do GNOME, ligue 'Permitir interação remota' antes de 'Compartilhar'. Se "
            f"marcou 'Lembrar desta seleção', apague {token_file} e tente de novo.")
