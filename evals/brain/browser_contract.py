"""Perfil browser v1; contrato v0 e ferramentas de desktop permanecem congelados."""

from typing import Annotated, Union

from pydantic import Field, TypeAdapter

from evals.brain.contract import VARIANTS

VERSION = "browser-1"
TOOLS = {
    "read_page",
    "get_page_text",
    "find",
    "click",
    "form_input",
    "navigate",
    "tabs_list",
    "tabs_create",
    "tabs_select",
    "tabs_close",
    "screenshot",
    "scroll_to",
    "wait_for",
    "handoff",
    "ask",
    "done",
    "fail",
}
models = [m for m in VARIANTS if m.model_fields["tool"].annotation.__args__[0] in TOOLS]
Decision = TypeAdapter(Annotated[Union[tuple(models)], Field(discriminator="tool")])


def schema():
    return Decision.json_schema()
