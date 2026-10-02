"""Verificação F3: confirmação específica do efeito (§4.3).

- Progresso é específico do efeito: valor digitado, seleção, arquivo salvo
  ou diálogo esperado. Mudança de título/árvore, isoladamente, NÃO confirma.
- Sem 500+300 ms fixos: espera cancelável por condição/evento, com polling
  curto limitado e deadline. Timeout retorna observação, sem inventar sucesso.
- Modelo propõe done com evidências; Python veta evidência ausente/obsoleta.
"""

from __future__ import annotations

import time

import safety


def wait_for_condition(cond, deadline_s: float = 2.0, poll_s: float = 0.1) -> tuple[bool, float]:
    """Espera cancelável até cond() verdadeiro ou deadline/cancelamento.

    Retorna (ok, esperados_ms). Timeout/cancelamento = (False, elapsed).
    """
    t0 = time.perf_counter()
    deadline = max(0.05, deadline_s)
    while True:
        try:
            if cond():
                return True, (time.perf_counter() - t0) * 1000
        except Exception:
            pass
        if safety.stop_requested():
            return False, (time.perf_counter() - t0) * 1000
        if time.perf_counter() - t0 >= deadline:
            return False, (time.perf_counter() - t0) * 1000
        time.sleep(min(poll_s, 0.1))


# Tokens genéricos de diálogo/modal (PT+EN): verbos de confirmação comuns a
# qualquer app/site — nunca nomes de loja, produto ou tarefa (§4.3: sem
# `if amazon`). Servem p/ detectar dispensa de intersticial/modal via UIA,
# não p/ escolher ação.
_MODAL_TOKENS = (
    "dialog", "modal", "alert", "confirm", "aviso",
    "continue", "continuar", "prosseguir",
    "não salvar", "nao salvar", "don't save", "dont save",
    "salvar", "save", "cancelar", "cancel", "fechar", "close",
    "dispensar", "dismiss", "entendi", "ok",
)


def has_modal_indicators(ui_names: list[str] | None) -> bool:
    """Há sinais de diálogo/modal/intersticial na lista UIA? (puro, testável)."""
    if not ui_names:
        return False
    blob = " | ".join(str(n or "") for n in ui_names).lower()
    return any(tok in blob for tok in _MODAL_TOKENS)


def modal_dismissed(ui_before: list[str] | None, ui_after: list[str] | None) -> bool:
    """Modal dispensado = indicadores antes, ausentes depois (puro, testável).

    Título sozinho nunca decide: exige as duas listas UIA. Sem listas (None)
    = inconclusivo (False), preservando "título nunca confirma".
    """
    if ui_before is None or ui_after is None:
        return False
    return has_modal_indicators(ui_before) and not has_modal_indicators(ui_after)


def confirm_effect(
    action_type: str, expected: str = "", before: str = "", after: str = "", value: str = "",
    *,
    ui_before: list[str] | None = None,
    ui_after: list[str] | None = None,
) -> tuple[bool, str]:
    """Confirmação específica do efeito (puro, testável).

    Retorna (confirmado, nota). Título/árvore mudando sozinho NÃO confirma.
    `ui_before`/`ui_after` (listas `tipo:nome` do snapshot) habilitam duas
    confirmações específicas sem título:
      - click dispensando modal: indicadores antes, ausentes depois;
      - click/scroll com conteúdo esperado visível depois / conteúdo novo.
    Sem as listas, vale o comportamento anterior (só type/ctrl+t confirmam).
    """
    if action_type == "type" and expected:
        if value and (expected.strip()[:40] in value or "text visible in focused field" in value):
            return True, "efeito confirmado: valor digitado visível"
        return False, "não confirmado: texto esperado ausente no campo focado"
    if action_type in ("open", "focus"):
        if after and before != after:
            return False, f"janela mudou p/ {after!r}; efeito ainda não confirmado"
        return False, "não confirmado: sem mudança observada"
    if action_type == "answer":
        return False, (
            "answer reportado; exige evidência UIA/frame independente"
            if expected
            else "não confirmado: answer vazio"
        )
    if action_type == "done":
        return False, "done exige evidências (verificar à parte)"
    if action_type == "hotkey:ctrl+t":
        if "window " in after and " -> " in after:
            return True, "efeito confirmado: nova aba observada"
        return False, "não confirmado: nova aba não observada"
    if action_type == "click" and modal_dismissed(ui_before, ui_after):
        return True, "efeito confirmado: diálogo/modal dispensado (indicadores sumiram da UIA)"
    if action_type in ("click", "scroll") and expected and ui_after is not None:
        blob = " | ".join(str(n or "") for n in ui_after).lower()
        if expected.strip()[:60].lower() in blob:
            return True, "efeito confirmado: conteúdo esperado visível após a ação"
    if action_type == "scroll" and ui_before is not None and ui_after is not None:
        if set(ui_before) != set(ui_after):
            return True, "efeito confirmado: conteúdo mudou após scroll"
    # Cliques/scroll/hotkey: confirmação exige observação seguinte específica;
    # aqui só registra envio (confirmação vem do próximo snapshot).
    return False, "enviado; confirmação pendente na próxima observação"


def resolve_evidence_ref(ref: str, state_evidences: list[str]) -> str:
    """`E2` -> texto da 2ª evidência confirmada (como o estado compacto mostra); outro texto fica
    como veio. ID fora do intervalo não resolve (continua desconhecido e o done é vetado)."""
    import re

    m = re.fullmatch(r"\s*[Ee](\d+)\s*", str(ref))
    if m and 1 <= int(m.group(1)) <= len(state_evidences):
        return state_evidences[int(m.group(1)) - 1]
    return ref


def done_evidence_ok(
    proposed: list[str], state_evidences: list[str], hist_labels: list[str] | None = None
) -> tuple[bool, str]:
    """Veta done sem referência exata a evidência confirmada.

    ``hist_labels`` permanece para compatibilidade; histórico de ações nunca
    conta como evidência de conclusão.
    """
    _ = hist_labels
    if not proposed:
        return False, "done vetado: sem evidências"
    proposed = [resolve_evidence_ref(e, state_evidences) for e in proposed]
    desconhecidas = [e for e in proposed if e not in state_evidences]
    if desconhecidas:
        return False, f"done vetado: evidência obsoleta/ausente: {desconhecidas[0][:80]}"
    return True, "evidências conferem"
