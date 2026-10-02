"""Schemas F1: contratos tipados do plano (§4.1).

Observation/ElementRef/FrameRef/TaskState/Decision/ActionResult/Completion.

Regras:
- Schemas discriminados por tipo de ação, sem campos irrelevantes
  preenchidos com null (construtores validam o essencial por tipo).
- Validar também no Python: JSON válido não garante decisão correta
  (ver `validate_decision`).
- `confidence` é AUSENTE (None) quando não calibrada — nunca 0,9/0,8
  fixos como certeza (F1 remove as confianças fictícias do baseline).
- IDs de observação/frame/elemento nunca reutilizados entre snapshots.

Compatibilidade B0: `Action`/`Decision(source, reason)` mantidos; campos
novos são opcionais.
"""
from __future__ import annotations

import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field, model_validator

ActionType = Literal["click", "double_click", "right_click", "middle_click", "move",
                     "drag", "type", "scroll", "hotkey", "open", "focus", "wait",
                     "answer", "done", "ask", "perceive"]
SourceType = Literal["planner", "uia", "vocaela", "ocr"]

DecisionKind = Literal["action", "perception", "skill", "sequence", "question", "finish"]
CompletionStatus = Literal["success", "partial", "blocked", "cancelled"]
SentStatus = Literal["sent", "not_sent", "unknown"]


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class Action(BaseModel):
    type: ActionType
    x: int | None = None
    y: int | None = None
    x2: int | None = None  # p/ drag: destino
    y2: int | None = None
    text: str | None = None
    key: str | None = None  # p/ hotkey: "enter", "ctrl+l"; p/ scroll: direção
    target: str | None = None  # p/ open: comando/app; p/ focus: substring do título
    ms: int = 0  # p/ wait
    clicks: int = 1
    presses: int = 1  # p/ hotkey: repetições (PRESS_KEY presses do Vocaela)
    # F2/F6: referência vinculada ao estado/frame (alvo por ID, não fuzzy).
    element_ref: str | None = None  # "<observation_id>#<element_id>"
    frame_ref: str | None = None  # frame_id da captura de origem


class ElementRef(BaseModel):
    """Alvo UIA vinculado a UMA observação (§4.1)."""

    observation_id: str
    element_id: int
    role: str = "?"
    name: str = ""
    context: str = ""  # ancestral próximo p/ desambiguar duplicados
    value: str = ""
    state: str = ""  # enabled/visible/selected...
    bounds: list[int] = Field(default_factory=lambda: [0, 0, 0, 0])

    @property
    def ref(self) -> str:
        return f"{self.observation_id}#{self.element_id}"


class FrameRef(BaseModel):
    """Captura vinculada ao frame (§4.1): coords só valem neste frame."""

    frame_id: str = Field(default_factory=lambda: new_id("frm"))
    observation_id: str = ""
    monitor: int = 0
    window_title: str = ""
    origin: list[int] = Field(default_factory=lambda: [0, 0])
    scale_dpi: float = 1.0
    size: list[int] = Field(default_factory=lambda: [0, 0])
    captured_at: float = Field(default_factory=time.time)


class Observation(BaseModel):
    observation_id: str = Field(default_factory=lambda: new_id("obs"))
    timestamp: float = Field(default_factory=time.time)
    monitors: list[dict] = Field(default_factory=list)
    app: str = ""
    pid: int | None = None
    hwnd: int | None = None
    focus: str = ""
    modal: str = ""
    uia: list[ElementRef] = Field(default_factory=list)
    delta: str = ""  # o que mudou desde a observação anterior
    coverage: str = ""  # cortes/truncamento informados ao modelo
    truncated: bool = False
    error: str = ""  # R2: diagnóstico (no_window/timeout/provider_empty/...) — "" = ok


class TaskState(BaseModel):
    """Memória da tarefa: dura toda a execução (§5.1)."""

    objective: str = ""
    subgoal: str = ""
    # Checklist do pedido, gerado UMA vez no início (planner.plan_requirements) e guardado pelo
    # Python: o modelo não precisa lembrar o que falta; o done exige evidência p/ cada item.
    requirements: list[str] = Field(default_factory=list)
    pending: list[str] = Field(default_factory=list)
    done_items: list[str] = Field(default_factory=list)
    evidences: list[str] = Field(default_factory=list)
    facts: dict[str, str] = Field(default_factory=dict)  # fato -> obs_id
    hypotheses: dict[str, str] = Field(default_factory=dict)  # hipótese != fato
    recent_failures: list[str] = Field(default_factory=list)
    active_skill: str = ""


class Decision(BaseModel):
    action: Action
    source: SourceType
    # F1: ausente quando não calibrada (nunca 0,9/0,8 fixos).
    confidence: float | None = None
    reason: str = ""
    # F1/F3: decisão pode ser ação, percepção, skill ou conclusão.
    kind: DecisionKind = "action"
    perception: str = ""  # ex.: "expand_branch#12", "crop:monitor1"
    skill: str = ""
    skill_args: dict = Field(default_factory=dict)
    expected_effect: str = ""
    observation_ref: str = ""
    frame_ref: str = ""
    task_update: dict = Field(default_factory=dict)  # atualização compacta
    # F6/R1: sequência de até 3 primitivas (action = 1ª primitiva,
    # representativa; execução usa steps).
    steps: list[Action] = Field(default_factory=list)
    # Guarda extra entre as primitivas de uma sequência (ex.: "save_dialog" = só digita o nome
    # se o diálogo Salvar realmente abriu).
    sequence_guard: str = ""

    @model_validator(mode="after")
    def _discriminated(self) -> Decision:
        # R1: união discriminada por kind — sem ação fictícia contrabandeada.
        # sequence carrega primitivas em steps (action = 1ª primitiva,
        # representativa); skill CLI carrega skill/skill_args (action é
        # placeholder nunca executado — o executor ramifica por kind antes
        # de execute()); perception/question/finish amarram action e campos.
        if self.kind == "sequence" and not self.steps:
            raise ValueError("kind=sequence exige steps (1..3 primitivas)")
        if self.kind == "skill" and not (self.skill or "").strip():
            raise ValueError("kind=skill exige skill")
        if self.kind in ("action", "perception", "question", "finish") and self.steps:
            raise ValueError(f"kind={self.kind} não carrega steps (use kind=sequence)")
        if self.kind == "question" and self.action.type != "ask":
            raise ValueError("kind=question exige action ask")
        if self.kind == "finish" and self.action.type != "done":
            raise ValueError("kind=finish exige action done")
        if self.kind == "perception" and (
            self.action.type != "perceive" or not (self.perception or "").strip()
        ):
            raise ValueError("kind=perception exige action perceive + spec em perception")
        if self.kind == "action" and self.action.type in ("ask", "done", "perceive"):
            raise ValueError(
                f"action {self.action.type} exige kind próprio "
                "(question/finish/perception), não kind=action"
            )
        return self


class ActionResult(BaseModel):
    action_id: str = Field(default_factory=lambda: new_id("act"))
    sent: SentStatus = "unknown"
    confirmed: bool = False
    evidences: list[str] = Field(default_factory=list)
    error: str = ""
    post_state: str = ""  # snapshot compacto reutilizável enquanto válido

    @model_validator(mode="after")
    def _no_auto_repeat(self) -> ActionResult:
        # sent=true/unknown nunca autoriza repetição automática de escrita
        # não idempotente: quem repete sem observar está violando o contrato.
        return self


class Completion(BaseModel):
    status: CompletionStatus
    evidence_refs: list[str] = Field(default_factory=list)
    note: str = ""

    @model_validator(mode="after")
    def _needs_evidence(self) -> Completion:
        if self.status in ("success", "partial") and not self.evidence_refs:
            raise ValueError("conclusão exige evidências (refs observadas)")
        return self


# --- validação Python (JSON válido != decisão correta) -------------------------

_COORD_KEYS = ("x", "y", "coordinate", "bbox", "bbox_2d")


def validate_decision(raw: dict, decision: Decision) -> None:
    """Vetos determinísticos sobre a decisão já parseada."""
    for k in _COORD_KEYS:
        if k in raw:
            raise ValueError(f"planner emitiu coordenadas (proibido): {raw}")
    t = decision.action.type
    a = decision.action
    if t == "type" and not (a.text or "").strip():
        raise ValueError("type precisa de text")
    if t in ("click", "double_click", "right_click", "middle_click", "move") \
            and (a.x is None or a.y is None) and not a.element_ref:
        raise ValueError(f"{t} precisa de x,y ou element_ref")
    if t == "drag" and (a.x is None or a.x2 is None) and not a.element_ref:
        raise ValueError("drag precisa de origem/destino ou element_ref")
    if t in ("open", "focus") and not (a.target or "").strip():
        raise ValueError(f"{t} precisa de target")
    if t == "hotkey" and not (a.key or "").strip():
        raise ValueError("hotkey precisa de key")
    if decision.confidence is not None and not (0.0 <= decision.confidence <= 1.0):
        raise ValueError(f"confidence fora de 0..1: {decision.confidence}")
