"""Visual actor: Vocaela-2-500M-1024R2 (vocaela/Vocaela-2-500M-1024R2).

Enxerga + localiza + produz ação visual. NÃO planeja, NÃO decide tools.

Formato copiado do model card oficial (não inventado):
- System message: `Vocaela_Computer_Use_System_Message` abaixo, verbatim.
- Entrada: screenshot (longest edge 1024 no treino; recomendado < 2048)
  + instrução curta ("Click the ...").
- Saída: <Action>[{...}]</Action>, JSON array; coordenadas [x,y] em 0..1,
  [0,0]=top-left. Ex: {"action": "click", "coordinate": [0.1, 0.5]}.
- Ação desktop: click/mouse_move/drag(+coordinate2)/right_click/
  middle_click/double_click/scroll(scroll_direction)/press_key(key,presses)/
  hotkey(hotkeys)/type(text).

Runtime: GGUF oficial vocaela/Vocaela-2-500M-1024R2-GGUF via llama-server
(suporte a llama.cpp documentado no card + repo demo). O adapter fala
OpenAI-compatible (/chat/completions com image_url); se o runtime exigir
outro protocolo, trocar SÓ este arquivo.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import time
from typing import TYPE_CHECKING, Literal

import httpx
from PIL import Image
from pydantic import BaseModel, field_validator

if TYPE_CHECKING:
    from schemas import Action

# --- system message oficial p/ computer use (verbatim do model card) ---------
VOCAELA_COMPUTER_SYSTEM = """You are an assistant trained to navigate the computer screen.
Given a task instruction, a screen observation, and an action history sequence,
output the next actions and wait for the next observation.

## Allowed ACTION_TYPEs and parameters:
1. `PRESS_KEY`: Press one specified key. Two parameters: `key`, string, the single key to press; `presses`, integer, the number of times to press the key (default is 1).
2. `TYPE`: Type a string into an element. Parameter: `text`, string, the text to type.
3. `MOUSE_MOVE`: Move the mouse cursor to a specified position. Parameter: `coordinate`, formatted as [x,y], the position to move the cursor to.
4. `CLICK`: Click left mouse button once on an element. Parameter: `coordinate`, formatted as [x,y], the position to click on.
5. `DRAG`: Drag the cursor with the left mouse button pressed, start and end positions are specified. Two parameters: `coordinate`, formatted as [x,y], the start position to drag from; `coordinate2`, formatted as [x2,y2], the end position to drag to.
6. `RIGHT_CLICK`: Click right mouse button once on an element. Parameter: `coordinate`, formatted as [x,y], the position to right click on.
7. `MIDDLE_CLICK`: Click middle mouse button once on an element. Parameter: `coordinate`, formatted as [x,y], the position to middle click on.
8. `DOUBLE_CLICK`: Click left mouse button twice on an element. Parameter: `coordinate`, formatted as [x,y], the position to double click on.
9. `SCROLL`: Scroll the screen (via mouse wheel). Parameter: `scroll_direction`, the direction (`up`/`down`/`left`/`right`) to scroll.
10. `HOTKEY`: Press a combination of keys simultaneously. Parameter: `hotkeys`, list of strings, the keys to press together.
11. `ANSWER`: Answer a specific question. Required parameter: `text`, string, the answer text.

* NOTE *: The `coordinate` and `coordinate2` parameters (formatted as [x,y]) are the relative coordinates on the screenshot scaled to range of 0-1, [0,0] is the top-left corner and [1,1] is the bottom-right corner.

## Format your response as
<Action>the next actions</Action>

`The next actions` can be one or multiple actions. Format `the next actions` as a JSON array of objects as below, each object is an action:
[{"action": "<ACTION_TYPE>", "key": "<key>", "presses": <presses>, "hotkeys": ["<hotkeys>"], "text": "<text>", "coordinate": [x,y], "coordinate2": [x2,y2], "scroll_direction": "<scroll_direction>"}]

If a parameter is not applicable, don't include it in the JSON object.
"""

VisualActionType = Literal["click", "double_click", "right_click", "middle_click",
                           "move", "drag", "scroll", "type", "key", "hotkey",
                           "answer"]

_VOC_TO_INTERNAL = {
    "CLICK": "click", "DOUBLE_CLICK": "double_click", "RIGHT_CLICK": "right_click",
    "MIDDLE_CLICK": "middle_click", "MOUSE_MOVE": "move", "DRAG": "drag",
    "SCROLL": "scroll", "TYPE": "type", "PRESS_KEY": "key", "HOTKEY": "hotkey",
    "ANSWER": "answer",  # resposta textual: vira observação, não input
}


class VisualAction(BaseModel):
    """Ação visual normalizada. Coordenadas sempre 0.0..1.0 relativas ao crop."""
    type: VisualActionType
    x: float | None = None
    y: float | None = None
    x2: float | None = None
    y2: float | None = None
    text: str | None = None
    key: str | None = None
    presses: int = 1
    scroll_direction: str | None = None
    dropped: int = 0  # ações extras do array que NÃO foram executadas

    @field_validator("x", "y", "x2", "y2")
    @classmethod
    def _range(cls, v: float | None) -> float | None:
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError(f"coordenada fora de 0..1: {v}")
        return v


def _prep_image(img: Image.Image, max_long_edge: int = 1024,
                jpeg_quality: int = 70) -> tuple[str, tuple[int, int]]:
    """Redimensiona p/ longest-edge<=1024 preservando aspect. Retorna (b64, (w,h))."""
    img = img.convert("RGB")
    w, h = img.size
    long_edge = max(w, h)
    if long_edge > max_long_edge:
        scale = max_long_edge / long_edge
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                         Image.LANCZOS)
        w, h = img.size
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=jpeg_quality)
    return base64.b64encode(buf.getvalue()).decode("ascii"), (w, h)


def parse_vocaela_output(text: str) -> VisualAction:
    """Parser do formato oficial <Action>[{...}]</Action> (+fallbacks tolerantes).

    Usa a PRIMEIRA ação do array (passo low-level único); `dropped` conta
    as demais p/ log. Aceita tipos em qualquer caixa. Erro honesto se
    inválido — nunca inventa coordenada.
    """
    t = text.strip()
    m = re.search(r"<Action>(.*?)</Action>", t, re.DOTALL | re.IGNORECASE)
    payload = m.group(1).strip() if m else t
    payload = re.sub(r"^```(?:json)?\s*|\s*```$", "", payload).strip()
    try:
        d = json.loads(payload)
    except Exception:
        m2 = re.search(r"\[.*\]|\{.*\}", payload, re.DOTALL)
        if not m2:
            raise ValueError(f"Vocaela não retornou ação válida: {text[:200]!r}")
        d = json.loads(m2.group(0))
    if isinstance(d, list):
        if not d:
            raise ValueError(f"Vocaela devolveu array vazio: {text[:200]!r}")
        first, dropped = d[0], len(d) - 1
    else:
        first, dropped = d, 0
    if not isinstance(first, dict):
        raise ValueError(f"ação Vocaela malformada: {text[:200]!r}")
    va = _normalize(first)
    va.dropped = dropped
    return va


def _coord(v: object, what: str) -> tuple[float, float]:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ValueError(f"{what} precisa ser [x,y] 0..1, veio {v!r}")
    x, y = float(v[0]), float(v[1])
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise ValueError(f"{what} fora de 0..1: {[x, y]}")
    return round(x, 4), round(y, 4)


def _normalize(d: dict) -> VisualAction:
    raw_type = str(d.get("action", "")).strip().upper()
    if not raw_type or raw_type not in _VOC_TO_INTERNAL:
        raise ValueError(f"action desconhecida do Vocaela: {raw_type!r} em {d}")
    t = _VOC_TO_INTERNAL[raw_type]
    kw: dict = {"type": t}
    if "coordinate" in d and d["coordinate"] is not None:
        kw["x"], kw["y"] = _coord(d["coordinate"], "coordinate")
    if "coordinate2" in d and d["coordinate2"] is not None:
        kw["x2"], kw["y2"] = _coord(d["coordinate2"], "coordinate2")
    if t == "drag" and ("x" not in kw or "x2" not in kw):
        raise ValueError(f"drag precisa de coordinate+coordinate2: {d}")
    if t in ("click", "double_click", "right_click", "middle_click", "move") \
            and "x" not in kw:
        raise ValueError(f"{t} precisa de coordinate: {d}")
    if t in ("type", "answer"):
        kw["text"] = str(d.get("text", ""))
        if not kw["text"]:
            raise ValueError(f"{t} sem text: {d}")
    if t == "key":
        kw["key"] = str(d.get("key", ""))
        if not kw["key"]:
            raise ValueError(f"press_key sem key: {d}")
        presses = d.get("presses", 1)
        kw["presses"] = presses if isinstance(presses, int) and presses > 0 else 1
    if t == "hotkey":
        hk = d.get("hotkeys", [])
        if not isinstance(hk, list) or not hk:
            raise ValueError(f"hotkey sem hotkeys: {d}")
        kw["key"] = "+".join(str(k) for k in hk)
    if t == "scroll":
        sd = str(d.get("scroll_direction", "down")).lower()
        if sd not in ("up", "down", "left", "right"):
            sd = "down"
        kw["scroll_direction"] = sd
        kw["text"] = "-800" if sd in ("down", "right") else "800"
    return VisualAction(**kw)


def visual_to_action(va: VisualAction, size: tuple[int, int],
                     origin: tuple[int, int] = (0, 0)) -> Action:
    """Converte 0..1 (relativo ao crop) → pixels físicos. Import tardio p/ testes."""
    from schemas import Action

    w, h = size
    ox, oy = origin

    def px(f: float | None, total: int, off: int) -> int | None:
        return None if f is None else off + int(round(f * total))

    t = va.type
    if t in ("click", "double_click", "right_click", "middle_click", "move"):
        return Action(type=t, x=px(va.x, w, ox), y=px(va.y, h, oy))
    if t == "drag":
        return Action(type="drag", x=px(va.x, w, ox), y=px(va.y, h, oy),
                      x2=px(va.x2, w, ox), y2=px(va.y2, h, oy))
    if t == "scroll":
        # key carrega a direção: left/right -> hscroll no executor
        return Action(type="scroll", text=va.text or "-800",
                      key=va.scroll_direction or "down")
    if t == "type":
        return Action(type="type", text=va.text)
    if t in ("key", "hotkey"):
        return Action(type="hotkey", key=va.key, presses=va.presses)
    if t == "answer":
        return Action(type="answer", text=va.text)
    raise ValueError(f"ação visual sem mapeamento: {t}")


class VocaelaAdapter:
    """Adapter específico do Vocaela. screenshot+instrução → VisualAction."""

    def __init__(self, base_url: str = "http://127.0.0.1:8082/v1",
                 model: str = "Vocaela-2-500M-1024R2",
                 timeout_s: float = 180.0, max_long_edge: int = 1024):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.max_long_edge = max_long_edge

    def check(self) -> dict:
        try:
            r = httpx.get(f"{self.base_url}/models", timeout=10.0)
            r.raise_for_status()
            data = r.json()
            return {"ok": True,
                    "models": [m.get("id", "?") for m in data.get("data", [])]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def act(self, screenshot: Image.Image | str,
                  instruction: str,
                  history: list[str] | None = None) -> tuple[VisualAction, float]:
        return await asyncio.to_thread(self.act_sync, screenshot, instruction, history)

    def act_sync(self, screenshot: Image.Image | str,
                 instruction: str,
                 history: list[str] | None = None) -> tuple[VisualAction, float]:
        """Retorna (VisualAction, vision_ms).

        `history`: últimas ações visuais como texto (o system oficial fala em
        "action history sequence", que o adapter não enviava — §5.5).
        """
        if isinstance(screenshot, Image.Image):
            b64, _ = _prep_image(screenshot, self.max_long_edge)
        else:
            b64 = str(screenshot)
        user_text = instruction
        if history:
            seq = "\n".join(f"- {h[:120]}" for h in history[-3:])
            user_text = (f"Action history sequence:\n{seq}\n"
                         f"Current instruction: {instruction}")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": VOCAELA_COMPUTER_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]},
            ],
            "temperature": 0.0,
            "max_tokens": 128,
        }
        t0 = time.perf_counter()
        try:
            # F6: pool persistente + cancelamento; retries só-HTTP.
            import http_pool as _pool

            data, _ms = _pool.post_json(self.base_url, "/chat/completions",
                                        payload, self.timeout_s, retries=2)
        except RuntimeError as e:
            raise RuntimeError(f"vocaela HTTP falhou: {e}")
        ms = (time.perf_counter() - t0) * 1000
        content = data["choices"][0]["message"]["content"]
        return parse_vocaela_output(content), ms


QWEN_GROUNDING_SYSTEM = """You are a UI grounding model. Locate the single UI element described by the user instruction on the screenshot.
Return ONLY one JSON object, no markdown, no explanation: {"x": 0.5, "y": 0.5}
x,y are relative coordinates 0..1 (0,0 = top-left, 1,1 = bottom-right) of the element CENTER.
If the element is not visible, return {"x": null, "y": null}."""


def parse_qwen_grounding(text: str) -> VisualAction:
    """Parser do grounding JSON do Qwen (puro, testável).

    Aceita {"x":0.5,"y":0.5}, {"coordinate":[x,y]} ou [x,y]. null/ausente =
    alvo não visível (erro honesto, vira last_error em vez de clique no escuro).
    """
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t).strip()
    try:
        d = json.loads(t)
    except Exception:
        m = re.search(r"\{[^{}]*\}|\[.*?\]", t, re.DOTALL)
        if not m:
            raise ValueError(f"Qwen grounding inválido: {text[:200]!r}")
        d = json.loads(m.group(0))
    if isinstance(d, (list, tuple)) and len(d) == 2:
        x, y = _coord(list(d), "coordinate")
        return VisualAction(type="click", x=x, y=y)
    if isinstance(d, dict):
        if "coordinate" in d and d["coordinate"] is not None:
            x, y = _coord(d["coordinate"], "coordinate")
            return VisualAction(type="click", x=x, y=y)
        x, y = d.get("x"), d.get("y")
        if x is None or y is None:
            raise ValueError(f"alvo não visível p/ o grounding: {text[:200]!r}")
        x, y = _coord([x, y], "x/y")
        return VisualAction(type="click", x=x, y=y)
    raise ValueError(f"Qwen grounding inválido: {text[:200]!r}")


# --- grounding tolerante a formato/escala + zoom em duas etapas ---------------------------------
COORD_MODES = ("unit", "auto", "1000", "pixel")
_NUM = r"-?\d+(?:\.\d+)?"
_POINT_PATTERNS = (
    # {"x": 0.5, "y": 0.5}
    rf'"x"\s*:\s*({_NUM})\s*,\s*"y"\s*:\s*({_NUM})',
    # {"point_2d": [x, y]} / {"coordinate": [x, y]} / "coordinates": [x, y]
    rf'"(?:point_2d|point|coordinate|coordinates|click_point)"\s*:\s*\[\s*({_NUM})\s*,\s*({_NUM})',
    # <point>x, y</point> / <point x=.. y=..>
    rf'<point>\s*\(?\s*({_NUM})\s*,\s*({_NUM})',
    # click(x, y) / pyautogui.click(x=.., y=..)
    rf'click\(\s*(?:x\s*=\s*)?({_NUM})\s*,\s*(?:y\s*=\s*)?({_NUM})',
    # (x, y) ou [x, y] soltos
    rf'[\[(]\s*({_NUM})\s*,\s*({_NUM})\s*[\])]',
)
_ABSENT = re.compile(r'"x"\s*:\s*null|not visible|não visível|cannot find|not found', re.I)


def parse_point_any(text: str, size: tuple[int, int] | None = None,
                    coords: str = "auto") -> VisualAction:
    """Ponto do centro em 0..1 a partir de formatos/escala variados (puro, testável).

    `coords`: `unit` = só 0..1 (protocolo de produção); `1000` = 0..1000 (Qwen3-VL nativo);
    `pixel` = pixels da imagem enviada (exige `size`); `auto` = 0..1 se couber, senão 0..1000 se
    couber, senão pixels dentro de `size`. Valores que não cabem em nenhuma escala = erro
    honesto (nunca inventa coordenada). Alvo ausente = ValueError "alvo não visível".
    """
    if coords not in COORD_MODES:
        raise ValueError(f"coords inválido: {coords!r}; use {COORD_MODES}")
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip()).strip()
    if _ABSENT.search(t) and not any(re.search(p_, t) for p_ in _POINT_PATTERNS[:2]):
        raise ValueError(f"alvo não visível p/ o grounding: {t[:200]!r}")
    nums = None
    for pattern in _POINT_PATTERNS:
        m = re.search(pattern, t)
        if m:
            nums = (float(m.group(1)), float(m.group(2)))
            break
    if nums is None:
        raise ValueError(f"grounding sem ponto reconhecível: {t[:200]!r}")
    x, y = nums

    def unit(v: float, scale: float) -> float:
        return round(v / scale, 4)

    inside_unit = 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0
    mode = coords
    if mode == "auto":
        if inside_unit:
            mode = "unit"
        elif 0 <= x <= 1000 and 0 <= y <= 1000:
            mode = "1000"
        else:
            mode = "pixel"
    if mode == "unit":
        if not inside_unit:
            raise ValueError(f"x/y fora de 0..1: {[x, y]}")
        return VisualAction(type="click", x=round(x, 4), y=round(y, 4))
    if mode == "1000":
        if not (0 <= x <= 1000 and 0 <= y <= 1000):
            raise ValueError(f"x/y fora de 0..1000: {[x, y]}")
        return VisualAction(type="click", x=unit(x, 1000), y=unit(y, 1000))
    if not size or not (0 <= x <= size[0] and 0 <= y <= size[1]):
        raise ValueError(f"x/y fora da imagem {size}: {[x, y]}")
    return VisualAction(type="click", x=unit(x, size[0]), y=unit(y, size[1]))


def crop_around(size: tuple[int, int], center: tuple[float, float], frac: float = 0.35,
                min_px: int = 320) -> tuple[int, int, int, int]:
    """Caixa (x0,y0,x1,y1) em pixels ao redor de `center` (0..1), `frac` de cada lado e no mínimo
    `min_px`, deslocada para caber na imagem. Puro."""
    w, h = size
    cw, ch = min(w, max(min_px, int(w * frac))), min(h, max(min_px, int(h * frac)))
    x0 = int(min(max(0, center[0] * w - cw / 2), w - cw))
    y0 = int(min(max(0, center[1] * h - ch / 2), h - ch))
    return x0, y0, x0 + cw, y0 + ch


def zoom_ground(ground_fn, image: Image.Image, instruction: str, frac: float = 0.35,
                coords: str = "auto", unconfirmed: str = "first") -> tuple[VisualAction, dict]:
    """Localização em duas etapas: ponto grosso na imagem toda, depois recorte em volta dele na
    resolução original e um ponto fino. `ground_fn(PIL, instrução) -> texto do modelo`.

    `unconfirmed`: se o recorte não confirma o alvo — "first" mantém o ponto grosso, "reject"
    recusa (menos cliques falsos, menos acertos). Alvo ausente na 1ª etapa propaga ValueError.
    """
    first = parse_point_any(ground_fn(image, instruction), image.size, coords)
    box = crop_around(image.size, (first.x, first.y), frac)
    crop = image.crop(box)
    meta = {"zoom": True, "box": list(box), "first": [first.x, first.y]}
    try:
        second = parse_point_any(ground_fn(crop, instruction), crop.size, coords)
    except ValueError as exc:
        meta["zoom_status"] = "unconfirmed"
        if unconfirmed == "reject":
            raise ValueError(f"alvo não confirmado no zoom: {exc}") from exc
        return first, meta
    w, h = image.size
    x = (box[0] + second.x * (box[2] - box[0])) / w
    y = (box[1] + second.y * (box[3] - box[1])) / h
    meta.update(zoom_status="ok", second=[second.x, second.y])
    return VisualAction(type="click", x=round(min(max(x, 0), 1), 4),
                        y=round(min(max(y, 0), 1), 4)), meta


class QwenGroundingAdapter:
    """Grounding JSON p/ perfis Qwen (D1/D2/U1/U2). Mesma interface do Vocaela.

    Motivo (run U1 20/09): apontar o VocaelaAdapter — com system message
    `<Action>` do Vocaela — p/ um endpoint Qwen faz o modelo responder num
    formato que o parser rejeita, após ~40s de inferência em CPU. Qwen entende
    instrução JSON simples (capability structured_output do perfil); o parser
    acima valida 0..1 sem inventar coordenada.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8082/v1",
                 model: str = "Qwen3-VL-2B-Instruct",
                 timeout_s: float = 180.0, max_long_edge: int = 1024,
                 zoom: bool = False, coords: str = "unit", zoom_frac: float = 0.35):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.max_long_edge = max_long_edge
        # Padrão = comportamento histórico (0..1 estrito, 1 chamada). `zoom` e `coords=auto`
        # só devem ligar depois de medidos na bateria `ground` do model_bench.
        if coords not in COORD_MODES:
            raise ValueError(f"vision.coords inválido: {coords!r}; use {COORD_MODES}")
        self.zoom, self.coords, self.zoom_frac = bool(zoom), coords, float(zoom_frac)

    def check(self) -> dict:
        try:
            r = httpx.get(f"{self.base_url}/models", timeout=10.0)
            r.raise_for_status()
            data = r.json()
            return {"ok": True,
                    "models": [m.get("id", "?") for m in data.get("data", [])]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def act(self, screenshot: Image.Image | str,
                  instruction: str,
                  history: list[str] | None = None) -> tuple[VisualAction, float]:
        return await asyncio.to_thread(self.act_sync, screenshot, instruction,
                                       history)

    def act_sync(self, screenshot: Image.Image | str,
                 instruction: str,
                 history: list[str] | None = None) -> tuple[VisualAction, float]:
        """Retorna (VisualAction click 0..1, vision_ms)."""
        t0 = time.perf_counter()
        if self.zoom and isinstance(screenshot, Image.Image):
            def ask(img: Image.Image, text: str) -> str:
                return self._ask(_prep_image(img, self.max_long_edge)[0], text, history)

            va, self.last_meta = zoom_ground(ask, screenshot, instruction, self.zoom_frac,
                                             self.coords)
            return va, (time.perf_counter() - t0) * 1000
        if isinstance(screenshot, Image.Image):
            b64, size = _prep_image(screenshot, self.max_long_edge)
        else:
            b64, size = str(screenshot), None
        content = self._ask(b64, instruction, history)
        if self.coords == "unit":
            return parse_qwen_grounding(content), (time.perf_counter() - t0) * 1000
        return parse_point_any(content, size, self.coords), (time.perf_counter() - t0) * 1000

    def _ask(self, b64: str, instruction: str, history: list[str] | None) -> str:
        user_text = f"Instruction: {instruction}\nReturn ONLY the JSON point."
        if history:
            seq = "\n".join(f"- {h[:120]}" for h in history[-3:])
            user_text = (f"Recent actions:\n{seq}\n{user_text}")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": QWEN_GROUNDING_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]},
            ],
            "temperature": 0.0,
            "max_tokens": 64,
        }
        try:
            import http_pool as _pool

            data, _ms = _pool.post_json(self.base_url, "/chat/completions",
                                        payload, self.timeout_s, retries=2)
        except RuntimeError as e:
            raise RuntimeError(f"qwen grounding HTTP falhou: {e}")
        return data["choices"][0]["message"]["content"]
