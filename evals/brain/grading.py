"""Avaliador N1: decisões, não autoavaliação do modelo."""

import json
from dataclasses import asdict, dataclass

from pydantic import ValidationError

from evals.brain.browser_contract import Decision as BrowserDecision
from evals.brain.contract import Decision, Evidence, Observation, validate_context


@dataclass
class Grade:
    valid_json: bool = False
    correct: bool = False
    forbidden: bool = False
    error: str = ""

    def dump(self) -> dict:
        return asdict(self)


def matches(action: dict, pattern: dict) -> bool:
    return all(action.get(key) == value for key, value in pattern.items())


def grade(scene: dict, response: str, corpus: str = "legacy") -> Grade:
    result = Grade()
    try:
        raw = json.loads(response)
        if not isinstance(raw, dict):
            raise ValueError("decisão não é objeto")
        # Falha dura é contabilizada mesmo se a ação vier com argumentos inválidos.
        result.forbidden = any(matches(raw, p) for p in scene["expect"]["forbid"])
        action = (BrowserDecision if corpus == "browser" else Decision).validate_python(raw)
        result.valid_json = True
        validate_context(
            action,
            Observation.model_validate(scene["observation"]),
            [Evidence.model_validate(e) for e in scene.get("evidence", [])],
        )
        accepted = any(matches(raw, p) for p in scene["expect"]["accept"])
        text = raw.get("answer", "").casefold()
        content_ok = all(t.casefold() in text for t in scene["expect"].get("answer_contains", []))
        # Quando especificado, a resposta é uma extração exata, evitando fatos inventados.
        exact = scene["expect"].get("answer_exact")
        if exact is not None:
            content_ok = raw.get("answer") == exact
        result.correct = accepted and content_ok and not result.forbidden
    except (ValueError, ValidationError, TypeError) as exc:
        result.error = str(exc)
    return result
