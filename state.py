"""Estado da tarefa F1/F3: memória que dura toda a execução (§4.1, §5.1).

- O modelo atualiza subobjetivos/fatos com referências observadas;
  o Python armazena e valida. Fato != hipótese.
- Resumo nunca apaga falhas ainda relevantes.
- Preferências/procedimentos entre sessões são separados e versionados;
  nunca tratados como observação atual da tela (fora deste módulo).
"""

from __future__ import annotations

from schemas import TaskState

MAX_FAILURES = 5
MAX_EVIDENCES = 50


def init(objective: str) -> TaskState:
    return TaskState(objective=objective, subgoal=objective)


def apply_update(state: TaskState, update: dict) -> TaskState:
    """Aplica atualização compacta do modelo, validada (puro, testável).

    R1: proposta ≠ aceita. `done_items` (concluídas) só são aceitos com
    `evidence_refs` que existam em `state.evidences` (efeito confirmado
    posterior à ação). Sem refs válidas, as concluídas propostas são
    ignoradas — alegação do modelo não vira fato. Subobjetivo, pendências,
    fatos e hipóteses continuam aceitos (fatos seguem falíveis/auditáveis).
    """
    if not isinstance(update, dict):
        return state
    sub = update.get("subgoal") or update.get("subobjetivo")
    if isinstance(sub, str) and sub.strip():
        state.subgoal = sub.strip()[:300]
    for key in ("pending", "pendencias"):
        val = update.get(key)
        if isinstance(val, list):
            state.pending = [str(v)[:200] for v in val[:20]]
    refs = update.get("evidence_refs") or update.get("evidences") or update.get("evidence")
    if isinstance(refs, str):
        refs = [refs]
    valid_refs = (
        [str(r) for r in refs if str(r) in state.evidences]
        if isinstance(refs, list)
        else []
    )
    for key in ("done_items", "concluidas", "done"):
        val = update.get(key)
        if isinstance(val, list):
            if not valid_refs:
                continue  # R1: concluída sem evidência confirmada = ignorada
            for v in val[:20]:
                s = str(v)[:200]
                if s and s not in state.done_items:
                    state.done_items.append(s)
    facts = update.get("facts") or update.get("fatos")
    if isinstance(facts, dict):
        for k, v in list(facts.items())[:20]:
            state.facts[str(k)[:120]] = str(v)[:300]
    hyps = update.get("hypotheses") or update.get("hipoteses")
    if isinstance(hyps, dict):
        for k, v in list(hyps.items())[:20]:
            state.hypotheses[str(k)[:120]] = str(v)[:300]
    return state


def add_evidence(state: TaskState, evidence: str) -> None:
    e = (evidence or "").strip()[:300]
    if e and e not in state.evidences:
        state.evidences.append(e)
        del state.evidences[:-MAX_EVIDENCES]


def add_failure(state: TaskState, failure: str) -> None:
    f = (failure or "").strip()[:300]
    if f:
        state.recent_failures.append(f)
        del state.recent_failures[:-MAX_FAILURES]


def compact(state: TaskState) -> str:
    """Resumo curto p/ o prompt (nunca apaga falhas relevantes)."""
    parts = [f"objetivo: {state.objective[:150]}"]
    if state.subgoal and state.subgoal != state.objective:
        parts.append(f"subobjetivo: {state.subgoal[:150]}")
    if state.requirements:
        parts.append("requisitos do pedido: " + "; ".join(
            f"({i}) {r[:80]}" for i, r in enumerate(state.requirements[:6], 1)))
    if state.done_items:
        parts.append("concluídas: " + "; ".join(state.done_items[-5:]))
    if state.pending:
        parts.append("pendências: " + "; ".join(state.pending[:5]))
    if state.recent_failures:
        parts.append("falhas recentes: " + "; ".join(state.recent_failures[-3:]))
    if state.evidences:
        # IDs estáveis (posição na lista): o planner cita "E2" no done em vez de copiar o texto.
        start = max(0, len(state.evidences) - 5)
        parts.append("evidências confirmadas: " + "; ".join(
            f"E{start + i + 1}={e[:120]}" for i, e in enumerate(state.evidences[start:])))
    if state.facts:
        parts.append("fatos: " + "; ".join(f"{k}={v}" for k, v in list(state.facts.items())[-5:]))
    return " | ".join(parts)
