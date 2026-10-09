"""Ambiente de browser real: mesma interface do Fixture, executando pela ponte da extensão."""

from __future__ import annotations

import json
import time
from typing import Callable
from urllib.request import urlopen

from evals.brain.contract import Evidence, Observation, Result, Strict, validate_context

# Ferramentas que a extensão implementa; o resto é devolvido como não suportado (sem fingir).
SUPPORTED = {"read_page", "get_page_text", "find", "click", "form_input", "navigate"}
READS = {"read_page", "get_page_text", "find"}
# Erros de política: o cérebro tentou algo que a extensão ou a política barrou.
VETOES = {
    "confirmation_required",
    "destination_not_granted",
    "destination_read_only",
    "origin_not_granted",
    "origin_changed",
    "redirect_not_granted",
    "read_only",
    "human_required",
    "tab_not_shared",
    "chrome_permission_missing",
    "origin_not_allowed",
}


class StaleRef(Exception):
    """Ref vencida sem alvo equivalente único na observação atual."""


class BrowserEnv:
    """`request(action_dict)` devolve a resposta crua da extensão: observation/result/error."""

    def __init__(
        self,
        request: Callable[[dict], dict],
        page: str,
        base_url: str,
        catalog: dict,
        *,
        state_url: str | None = None,
        settle: float = 0.4,
    ):
        self.request, self.page, self.base_url = request, page, base_url.rstrip("/")
        self.catalog = catalog
        self.state_url = state_url or f"{self.base_url}/state"
        self.settle = settle
        self.evidence: list[Evidence] = []
        self.vetoes: list[dict] = []
        self.final_evidence: list[Evidence] = []
        self.known: dict[str, tuple[str, str]] = {}
        self.renewals: list[dict] = []
        self.serial = 0
        self.current = self._stub(f"{self.base_url}/{page}", "sem leitura ainda")
        self.refresh(record=True)

    def snapshot(self) -> dict:
        """Estado HTTP do servidor da fixture, independente da página e do agente."""
        with urlopen(self.state_url, timeout=5) as response:
            return json.load(response)

    def _stub(self, url: str, coverage: str) -> Observation:
        self.serial += 1
        return Observation(id=f"s{self.serial}", url=url, coverage=coverage)

    def refresh(self, *, settle: bool = False, record: bool = False) -> None:
        """Observação nova (refs válidas) sem contar como evidência de leitura.

        Depois de ação que pode navegar (clique, envio de formulário) espera a página assentar.
        """
        if settle and self.settle:
            time.sleep(self.settle)
        action = {"tool": "read_page", "filter": "interactive", "scope": "", "next": ""}
        reply = self.request(action)
        if "observation" not in reply and reply.get("error") not in VETOES and settle:
            time.sleep(self.settle)
            reply = self.request(action)
        self._adopt(reply)
        if record and "result" in reply:
            # A página de entrada foi lida para o modelo: é leitura registrada, citável no done.
            self.evidence.extend(Result.model_validate(reply["result"]).evidence)

    def _adopt(self, reply: dict) -> None:
        if "observation" in reply:
            self.current = Observation.model_validate(reply["observation"])
            self.known.update({e.ref: (e.role, e.name) for e in self.current.elements})
        elif reply.get("error"):
            reason = f" ({reply['reason']})" if reply.get("reason") else ""
            self.current = self._stub(self.current.url, f"erro da ponte: {reply['error']}{reason}")

    def _veto(self, action: Strict, error: str) -> None:
        if error in VETOES:
            self.vetoes.append({"tool": action.tool, "error": error})

    def describe(self, action: Strict) -> str:
        """Alvo legível de uma ação: o histórico mostra o que foi tocado, não só uma ref vencida."""
        seen = self.known.get(getattr(action, "ref", "") or getattr(action, "scope", ""))
        return f"{seen[0]}: {seen[1]}" if seen else ""

    def renew(self, action: Strict) -> Strict:
        """Ref vencida vira a ref atual do mesmo (papel, nome) se for único; senão, erro."""
        current = {e.ref for e in self.current.elements}
        for field in ("ref", "scope"):
            ref = getattr(action, field, "")
            if not ref or ref in current or ref not in self.known:
                continue
            same = [e for e in self.current.elements if (e.role, e.name) == self.known[ref]]
            if len(same) != 1:
                raise StaleRef(ref)
            self.renewals.append({"from": ref, "to": same[0].ref, "target": self.describe(action)})
            action = action.model_copy(update={field: same[0].ref})
        return action

    def resolve(self, refs: list[str]) -> list[Evidence]:
        """Refs citadas → leituras registradas. Ref desconhecida é evidência inventada."""
        known = {e.ref: e for e in self.evidence}
        for ref in refs:
            # Citar um elemento (`abc123:e2`) vale como citar a leitura (`r:abc123`).
            base = ref.split(":e")[0].removeprefix("r:")
            if known.get(ref) is None and known.get(f"r:{base}") is None:
                raise ValueError(f"evidência não confirmada: {ref}")
        return list(self.evidence)

    def execute(self, action: Strict) -> Result:
        if action.tool == "done":
            self.final_evidence = self.resolve(action.evidence)
            return Result(sent=False, confirmed=True)
        try:
            action = self.renew(action)
        except StaleRef:
            return Result(sent=False, confirmed=False, error="stale_ref")
        validate_context(action, self.current, self.evidence)
        tool = action.tool
        if tool in {"ask", "handoff", "fail"}:
            return Result(
                sent=False, confirmed=False, error="human_required" if tool != "fail" else "fail"
            )
        if tool not in SUPPORTED:
            return Result(sent=False, confirmed=False, error="unsupported_tool")
        cursor = getattr(action, "next", "")
        if cursor and cursor != self.current.next:
            # A extensão guarda só o cursor da última leitura; dizer qual vale evita insistência.
            latest = self.current.next or "nenhum: a leitura anterior foi a última página"
            return Result(
                sent=False, confirmed=False, error="stale_cursor", delta=f"vale: {latest}"
            )
        reply = self.request(action.model_dump())
        if reply.get("error"):
            reason = f":{reply['reason']}" if reply.get("reason") else ""
            self._veto(action, reply["error"])
            if tool in READS:
                self._adopt(reply)
            return Result(sent=False, confirmed=False, error=reply["error"] + reason)
        if tool in READS:
            self._adopt(reply)
            result = Result.model_validate(reply["result"])
            self.evidence.extend(result.evidence)
            return result
        if tool == "navigate":
            self.refresh(settle=True, record=True)
            return Result(
                sent=bool(reply.get("sent")),
                confirmed=bool(reply.get("confirmed")),
                delta=json.dumps({"url": reply.get("url", "")}),
            )
        result = Result.model_validate(reply["result"])
        if tool == "form_input":
            self.evidence.extend(result.evidence)
        elif result.error == "effect_requires_read":
            # Clique enviado não é efeito comprovado: o cérebro precisa reler para confirmar.
            result = result.model_copy(
                update={"error": "", "delta": "efeito não confirmado; releia"}
            )
        self.refresh(settle=True)
        return result
