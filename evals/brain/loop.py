"""Loop fino reutilizável: depende somente de decide(payload) e ambiente de fixture."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from evals.brain.contract import Decision
from evals.brain.fixtures import Fixture


def compact(result: dict) -> dict:
    """O corpo das leituras já está em `evidence`; o histórico leva só as referências."""
    shown = dict(result)
    shown["evidence"] = [e["ref"] for e in shown.get("evidence", [])]
    return shown


@dataclass
class Episode:
    status: str = "budget_exhausted"
    turns: list[dict] = field(default_factory=list)
    answer: dict | None = None
    state: dict = field(default_factory=dict)


def run_episode(
    goal: str,
    env: Fixture,
    decide: Callable[[dict], str],
    *,
    decision=Decision,
    evidence_window: int = 8,
    max_steps: int = 40,
    max_errors: int = 2,
    timeout: float = 180,
) -> Episode:
    episode = Episode()
    history, errors = [], 0
    deadline = time.monotonic() + timeout
    for _ in range(max_steps):
        if time.monotonic() >= deadline:
            episode.status = "timeout"
            break
        payload = {
            "goal": goal,
            "history": history[-8:],
            "observation": env.current.model_dump(),
            "evidence": [e.model_dump() for e in env.evidence[-evidence_window:]],
        }
        turn = {"observation_id": env.current.id, "observation_next": env.current.next}
        started = time.monotonic()
        try:
            raw = decide(payload)
            turn["response"] = raw
            action = decision.validate_json(raw)
            target = env.describe(action) if hasattr(env, "describe") else ""
            result = env.execute(action)
            shown = {**action.model_dump(), **({"target": target} if target else {})}
            turn.update(action=shown, result=result.model_dump())
            history.append({"action": shown, "result": compact(result.model_dump())})
            if action.tool == "done":
                episode.status, episode.answer = "done", action.model_dump()
            elif action.tool in {"ask", "handoff", "fail"}:
                episode.status = action.tool
            elif result.error:
                errors += 1
            else:
                errors = 0
        except Exception as exc:
            errors += 1
            turn["error"] = f"{type(exc).__name__}: {exc}"
            history.append({"error": turn["error"]})
        turn["latency_s"] = time.monotonic() - started
        episode.turns.append(turn)
        if episode.status != "budget_exhausted":
            break
        if errors > max_errors:
            episode.status = "error_budget"
            break
    episode.state = env.snapshot()
    return episode
