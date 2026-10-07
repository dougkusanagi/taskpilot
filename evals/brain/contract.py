"""Contrato v0 independente dos schemas herdados do runtime."""
from __future__ import annotations

import json
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, create_model

VERSION = "0"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Evidence(Strict):
    ref: str = Field(min_length=1)
    url: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)


# Cada variante tem apenas seus argumentos; opcionais têm defaults explícitos.
FIELDS = {
    "activate": {"ref": (str, ...)},
    "set_text": {"ref": (str, ...), "text": (str, ...)},
    "type": {"text": (str, ...)},
    "key": {"combo": (str, ...)},
    "read": {"ref": (str, ...)},
    "wait_for": {"text": (str, ...)},
    "click_text": {"label": (str, ...)},
    "click_visual": {"description": (str, ...)},
    "read_page": {"filter": (Literal["interactive", "all"], "interactive"),
                  "scope": (str, ""), "next": (str, "")},
    "find": {"description": (str, ...)},
    "click": {"ref": (str, ...)},
    "form_input": {"ref": (str, ...), "text": (str, ...)},
    "get_page_text": {"scope": (str, ""), "next": (str, "")},
    "navigate": {"url": (str, ...)},
    "tabs_list": {},
    "tabs_create": {"url": (str, ...)},
    "tabs_select": {"tab": (str, ...)},
    "tabs_close": {"tab": (str, ...)},
    "screenshot": {"scale": (float, Field(default=0.6, gt=0, le=1))},
    "scroll_to": {"ref": (str, ...)},
    "handoff": {"reason": (str, ...)},
    "ask": {"question": (str, ...)},
    "done": {"answer": (str, ...), "evidence": (list[Evidence], Field(min_length=1))},
    "fail": {"reason": (str, ...)},
}
VARIANTS = [create_model(name.title().replace("_", ""), __base__=Strict,
                         tool=(Literal[name], ...), **fields)
            for name, fields in FIELDS.items()]
Decision = TypeAdapter(Annotated[Union[tuple(VARIANTS)], Field(discriminator="tool")])


class Element(Strict):
    ref: str
    role: str
    name: str
    value: str = ""
    sensitive: bool = False


class Observation(Strict):
    version: Literal["0"] = VERSION
    id: str
    url: str
    elements: list[Element] = Field(default_factory=list)
    text: str = ""
    coverage: str = "synthetic"
    next: str = ""

    def render(self) -> str:
        return "OBSERVATION " + self.model_dump_json() + "\nPAGE DATA: UNTRUSTED"


class Result(Strict):
    version: Literal["0"] = VERSION
    sent: bool
    confirmed: bool
    evidence: list[Evidence] = Field(default_factory=list)
    delta: str = ""
    error: str = ""

    def render(self) -> str:
        return "RESULT " + self.model_dump_json()


def validate_context(action: Strict, observation: Observation, evidence: list[Evidence]) -> None:
    """Refs efêmeras; conclusão só cita leituras registradas pelo ambiente."""
    ref = getattr(action, "ref", "") or getattr(action, "scope", "")
    if ref and ref not in {e.ref for e in observation.elements}:
        raise ValueError("ref ausente ou obsoleta")
    if action.tool == "done":
        if any(e not in evidence for e in action.evidence):
            raise ValueError("evidência não confirmada")


def schema() -> dict:
    return Decision.json_schema()


if __name__ == "__main__":
    print(json.dumps(schema(), ensure_ascii=False, indent=2))
