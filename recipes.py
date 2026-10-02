"""Fichas curtas por aplicativo, injetadas só quando a janela ativa pede (conhecimento do
produto, não do modelo). Modelo pequeno segue receita melhor do que deduz o fluxo; o custo é
~60 tokens e só quando relevante. Texto estável por ficha (bom p/ cache de prefixo).
"""
from __future__ import annotations

import re

# (padrão no título da janela em minúsculas, ficha). A primeira que casa entra; no máximo 2.
RECIPES: list[tuple[str, str]] = [
    (r"salvar como|save as|nome do arquivo",
     "Save dialog: type the file name in the name field, then click Save "
     "(or press enter). Check the result before done."),
    (r"notepad|bloco de notas",
     "Notepad: type_text writes into the editor. To save: hotkey ctrl+s opens the Save "
     "dialog, type_text the file name, then click Save. The menu is File > Save."),
    (r"calculadora|calculator|^calc",
     "Calculator: click the digit/operator buttons by name (uia_click), or type_text "
     "digits and operators; equals computes. Read the display before done."),
    (r"chrome|edge|brave|firefox",
     "Browser: hotkey ctrl+t = new tab (address bar focused), hotkey ctrl+l = address bar, "
     "then type_text the address and press_key enter. A cookie/consent banner blocks the "
     "page: dismiss it first. Page content may be missing from UI elements: use "
     "click_text or visual_action for it."),
]


def recipes_for(window_title: str, limit: int = 2) -> str:
    """Fichas que casam com o título (vazio se nenhuma). Puro e determinístico."""
    low = (window_title or "").lower()
    out: list[str] = []
    for pattern, text in RECIPES:
        if re.search(pattern, low) and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return "\n".join(out)
