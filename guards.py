"""Vetos determinísticos do Python (puros, sem GUI): regras do produto que o modelo não deve
precisar acertar sozinho.

`goal_ambiguity`: nunca adivinhar entre itens parecidos que o pedido não distingue (perfis de
pessoas, contas, arquivos com o mesmo começo). Em vez de clicar, o loop recusa e manda o planner
usar `ask`.
"""
from __future__ import annotations

import re
import unicodedata

_STOP = frozenset("""a o os as um uma uns umas de da do das dos em no na nos nas para por com sem
e ou que se ao aos à às the to of in on and or for with from open abra abrir clique clicar click
vá ir va go selecione select escolha choose perfil profile conta account botão botao button
link item menu opção opcao option""".split())


# O pedido que escolhe por posição ("o primeiro resultado") já distingue os itens: sem veto.
_BY_POSITION = frozenset("""primeiro primeira segundo segunda terceiro terceira ultimo ultima
first second third last qualquer any whichever 1o 2o 3o""".split())


def _tokens(text: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return {t for t in re.findall(r"[a-z0-9]+", folded) if len(t) >= 2 and t not in _STOP}


def goal_ambiguity(goal: str, target: str, names: list[str]) -> list[str]:
    """Nomes que o pedido não distingue de `target` (vazio = sem ambiguidade).

    `target` é ambíguo quando (1) o pedido cita parte do nome dele, (2) o nome tem uma parte que o
    pedido NÃO cita e (3) outro elemento visível casa com a mesma parte citada mas difere no resto.
    Ex.: "Abra o perfil de Ana" com {"Ana pessoal", "Ana trabalho"}.
    """
    want = _tokens(goal)
    if want & _BY_POSITION or re.search(r"\b[1-9]\s*[ºo°]", goal.casefold()):
        return []
    mine = _tokens(target)
    cited = mine & want
    if not cited or not (mine - want):
        return []  # o pedido não menciona o alvo, ou já diz o nome inteiro
    rivals = []
    for name in names:
        other = _tokens(name)
        if name != target and other != mine and cited <= other and (other - want):
            rivals.append(name)
    return sorted(set(rivals))


def goal_conflict(goal: str, target: str, names: list[str]) -> list[str]:
    """Nomes que o pedido (com a resposta do humano) já indica por inteiro e que NÃO são `target`,
    quando `target` tem uma parte que o pedido não cita: clicar `target` contradiz o pedido.
    Ex.: pedido "Abra o perfil de Ana" + resposta "Ana trabalho", alvo "Ana pessoal"."""
    want = _tokens(goal)
    mine = _tokens(target)
    cited = mine & want
    if not cited or not (mine - want):
        return []
    return sorted({n for n in names if n != target and _tokens(n) != mine
                   and _tokens(n) and _tokens(n) <= want and cited <= _tokens(n)})
