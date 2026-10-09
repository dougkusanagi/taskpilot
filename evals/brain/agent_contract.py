"""Contrato do loop (agent-1): browser-1 com `done.evidence` por referência.

O modelo cita refs de leituras (`r:…`); o ambiente resolve, rejeita refs inventadas e anexa o
registro. Copiar excertos por extenso estourava o teto de tokens e errava refs (episódios
reais de 08/10). O contrato browser-1 do N1 permanece congelado e não muda.
"""

from typing import Annotated, Literal, Union

from pydantic import Field, TypeAdapter

from evals.brain.browser_contract import models as browser_models
from evals.brain.contract import Strict

VERSION = "agent-1"


class Done(Strict):
    tool: Literal["done"]
    answer: str
    evidence: list[str] = Field(min_length=1)


models = [m for m in browser_models if m.model_fields["tool"].annotation.__args__[0] != "done"]
Decision = TypeAdapter(Annotated[Union[tuple(models + [Done])], Field(discriminator="tool")])


def schema():
    return Decision.json_schema()
