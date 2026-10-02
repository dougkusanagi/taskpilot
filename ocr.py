"""OCR local opcional (R2, fatia 3): leitura de texto da tela sem input físico.

Complemento mensurável da UIA (§4.2 do plano): quando a árvore expõe só
moldura (provider vazio) ou o valor de um campo é ilegível, o planner pode
pedir `perceive(ocr)` e recebe fatos de texto — nunca evidência confirmada,
nunca instrução.

Backends, nesta ordem (primeiro disponível vence):
  1. `winrt` (Windows.Media.Ocr, in-box no Windows 10/11) — sem dependência
     nova quando o pacote `winrt`/`pywinrt` está instalado;
  2. binário `tesseract` no PATH (via subprocess, `--psm 6`, sem dep nova);
  3. `rapidocr` (extra opcional `ocr`: modelos ONNX embutidos, só CPU, ~1 s por tela cheia).

`read_words()` devolve linhas/palavras COM caixas (base do `click_text`); `find_text()` acha um
texto nelas e recusa ambiguidade (nunca adivinha entre dois lugares).

Sem nenhum dos dois: `available()` é False e `read()` devolve `ok=False`
com o motivo honesto — o planner recebe "OCR indisponível" como fato e
segue com UIA/visão. Nenhum texto é inventado.

Tudo aqui é só leitura (screenshot/PIL ou arquivo); nenhum mouse/teclado.
"""

from __future__ import annotations

import shutil
import time
import unicodedata

_TESSERACT_CMD = "tesseract"


def backend() -> str:
    """Backend OCR efetivo: `winrt` | `tesseract` | `unavailable` (puro)."""
    try:
        import winrt.windows.media.ocr  # noqa: F401
        return "winrt"
    except Exception:
        pass
    if shutil.which(_TESSERACT_CMD):
        return "tesseract"
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return "rapidocr"
    except Exception:
        pass
    return "unavailable"


def available() -> bool:
    """Há backend OCR local neste ambiente? (puro, testável)."""
    return backend() != "unavailable"


def status() -> dict:
    """Diagnóstico honesto p/ logs e fatos de percepção (puro)."""
    b = backend()
    if b != "unavailable":
        return {"backend": b, "available": True, "reason": ""}
    return {
        "backend": "unavailable",
        "available": False,
        "reason": "sem backend OCR (tesseract no PATH, pacote winrt ou extra ocr: rapidocr)",
    }


def _to_png_bytes(image) -> bytes:
    """PIL Image | caminho | bytes -> PNG bytes. Erro honesto se inválido."""
    if isinstance(image, (bytes, bytearray)):
        return bytes(image)
    try:
        from pathlib import Path

        p = Path(str(image))
        if p.is_file():
            return p.read_bytes()
    except Exception:
        pass
    try:
        import io

        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        raise ValueError(f"OCR: imagem inválida ({e})")


def _read_tesseract(png: bytes, lang: str) -> str:
    """OCR via binário tesseract (subprocess, sem dep nova)."""
    import os
    import subprocess
    import tempfile

    langs = "por+eng" if (lang or "").lower().startswith("pt") else "eng"
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(png)
        src = f.name
    try:
        r = subprocess.run(
            [_TESSERACT_CMD, src, "stdout", "-l", langs, "--psm", "6"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    finally:
        try:
            os.unlink(src)
        except Exception:
            pass
    if r.returncode != 0:
        raise RuntimeError(f"tesseract falhou (code={r.returncode}): {r.stderr[:200]}")
    return r.stdout or ""


def _read_winrt(png: bytes, lang: str) -> str:
    """OCR via Windows.Media.Ocr (in-box; exige pacote winrt instalado)."""
    import asyncio

    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    async def _run() -> str:
        langs = list(OcrEngine.available_recognizer_languages)
        pick = None
        want = (lang or "pt").lower()[:2]
        for lg in langs:
            try:
                tag = str(lg.language_tag or "").lower()
            except Exception:
                tag = ""
            if tag.startswith(want):
                pick = lg
                break
        if pick is not None:
            engine = OcrEngine.try_create_from_language(pick)
        else:
            engine = OcrEngine.try_create_from_user_profile_languages()
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(png)
        await writer.store_async()
        await writer.flush_async()
        stream.seek(0)
        from winrt.windows.graphics.imaging import BitmapDecoder

        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)
        try:
            return result.text or ""
        except Exception:
            return str(getattr(result, "text", "") or "")

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result(timeout=30)
    return asyncio.run(_run())


def read(image, lang: str = "pt") -> dict:
    """Lê texto da imagem. Retorna dict (nunca levanta por falta de backend).

    `{"ok", "text", "backend", "ms", "reason"}` — `ok=False` com `reason`
    honesto quando indisponível/falha; o chamador vira isso em fato de
    percepção ("OCR indisponível"), nunca em evidência nem em erro fatal.
    """
    t0 = time.perf_counter()
    b = backend()
    if b == "unavailable":
        st = status()
        return {"ok": False, "text": "", "backend": b,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "reason": str(st.get("reason", "OCR indisponível"))}
    try:
        png = _to_png_bytes(image)
    except ValueError as e:
        return {"ok": False, "text": "", "backend": b,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "reason": str(e)[:200]}
    try:
        if b == "winrt":
            text = _read_winrt(png, lang)
        elif b == "rapidocr":
            text = " ".join(w["text"] for w in _words_rapidocr(png))
        else:
            text = _read_tesseract(png, lang)
    except Exception as e:
        return {"ok": False, "text": "", "backend": b,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "reason": f"OCR ({b}) falhou: {e}"[:200]}
    ms = round((time.perf_counter() - t0) * 1000, 1)
    norm = " ".join(str(text or "").split())
    if not norm:
        return {"ok": False, "text": "", "backend": b, "ms": ms,
                "reason": "OCR não encontrou texto legível na captura"}
    return {"ok": True, "text": norm[:2000], "backend": b, "ms": ms, "reason": ""}


# --- palavras/linhas com caixas (click_text) ---------------------------------------------------
_RAPID = None


def _words_rapidocr(png: bytes) -> list[dict]:
    """Linhas com caixa via RapidOCR (CPU). Cada item: {text, box=[x0,y0,x1,y1], conf}."""
    global _RAPID
    import io

    import numpy as np
    from PIL import Image

    if _RAPID is None:
        from rapidocr_onnxruntime import RapidOCR

        _RAPID = RapidOCR()
    rgb = np.array(Image.open(io.BytesIO(png)).convert("RGB"))
    result, _ = _RAPID(rgb[:, :, ::-1])  # RapidOCR espera BGR
    words = []
    for quad, text, conf in result or []:
        xs, ys = [p[0] for p in quad], [p[1] for p in quad]
        words.append({"text": str(text), "conf": round(float(conf), 3),
                      "box": [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))]})
    return words


def _words_tesseract(png: bytes, lang: str) -> list[dict]:
    """Palavras com caixa via `tesseract ... tsv` (nível 5 = palavra)."""
    import os
    import subprocess
    import tempfile

    langs = "por+eng" if (lang or "").lower().startswith("pt") else "eng"
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(png)
        src = f.name
    try:
        r = subprocess.run([_TESSERACT_CMD, src, "stdout", "-l", langs, "--psm", "11", "tsv"],
                           capture_output=True, text=True, timeout=30)
    finally:
        try:
            os.unlink(src)
        except Exception:
            pass
    if r.returncode != 0:
        raise RuntimeError(f"tesseract falhou (code={r.returncode}): {r.stderr[:200]}")
    words = []
    for line in r.stdout.splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) < 12 or cols[0] != "5" or not cols[11].strip():
            continue
        left, top, width, height = (int(cols[i]) for i in (6, 7, 8, 9))
        words.append({"text": cols[11].strip(), "conf": float(cols[10]) / 100,
                      "box": [left, top, left + width, top + height]})
    return words


def _words_winrt(png: bytes, lang: str) -> list[dict]:
    """Linhas com caixa via Windows.Media.Ocr (união das caixas das palavras de cada linha).
    Não testado fora do Windows."""
    import asyncio

    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    async def _run() -> list[dict]:
        engine = None
        want = (lang or "pt").lower()[:2]
        for lg in list(OcrEngine.available_recognizer_languages):
            if str(getattr(lg, "language_tag", "") or "").lower().startswith(want):
                engine = OcrEngine.try_create_from_language(lg)
                break
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(png)
        await writer.store_async()
        await writer.flush_async()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        result = await engine.recognize_async(await decoder.get_software_bitmap_async())
        out = []
        for line in result.lines:
            rects = [w.bounding_rect for w in line.words]
            if not rects:
                continue
            out.append({"text": line.text, "conf": 1.0,
                        "box": [round(min(r.x for r in rects)), round(min(r.y for r in rects)),
                                round(max(r.x + r.width for r in rects)),
                                round(max(r.y + r.height for r in rects))]})
        return out

    return asyncio.run(_run())


def read_words(image, lang: str = "pt") -> dict:
    """Linhas/palavras com caixas em pixels da própria imagem (nunca levanta por falta de backend).

    `{"ok","words":[{text,box,conf}],"size":(w,h),"backend","ms","reason"}`.
    """
    t0 = time.perf_counter()
    b = backend()

    def fail(reason: str) -> dict:
        return {"ok": False, "words": [], "size": (0, 0), "backend": b,
                "ms": round((time.perf_counter() - t0) * 1000, 1), "reason": reason[:200]}

    if b == "unavailable":
        return fail(str(status().get("reason", "OCR indisponível")))
    try:
        png = _to_png_bytes(image)
    except ValueError as e:
        return fail(str(e))
    try:
        if b == "winrt":
            words = _words_winrt(png, lang)
        elif b == "rapidocr":
            words = _words_rapidocr(png)
        else:
            words = _words_tesseract(png, lang)
        import io

        from PIL import Image

        size = Image.open(io.BytesIO(png)).size
    except Exception as e:
        return fail(f"OCR ({b}) falhou: {e}")
    return {"ok": True, "words": words, "size": size, "backend": b, "reason": "",
            "ms": round((time.perf_counter() - t0) * 1000, 1)}


def _fold(text: str) -> str:
    """Minúsculas sem acento, 1:1 por caractere (mantém posições p/ recortar a caixa)."""
    out = []
    for ch in text:
        base = unicodedata.normalize("NFKD", ch)[:1] if ch.isalpha() else ch
        out.append(base.casefold()[:1] or " ")
    return "".join(out)


def _tier(text: str, query: str) -> tuple[int, int]:
    """(tier, posição): 0 igual, 1 começa com, 2 palavra inteira dentro, 3 contém; -1 não casa."""
    t = " ".join(text.split())
    i = t.find(query)
    if i < 0:
        return -1, -1
    if t == query:
        return 0, i
    if i == 0:
        return 1, i
    end = i + len(query)
    before_ok = not t[i - 1].isalnum()
    after_ok = end >= len(t) or not t[end].isalnum()
    return (2 if before_ok and after_ok else 3), i


def _merge_wrapped(words: list[dict]) -> list[dict]:
    """Une pares de linhas vizinhas verticalmente e alinhadas (rótulo quebrado em 2 linhas, ex.:
    botão "Aceitar" / "todos"). Só pares; as linhas originais continuam disponíveis."""
    out = []
    for a in words:
        ax0, ay0, ax1, ay1 = a["box"]
        for b in words:
            if b is a:
                continue
            bx0, by0, bx1, by1 = b["box"]
            gap = by0 - ay1
            heights = min(ay1 - ay0, by1 - by0)
            same_column = abs((ax0 + ax1) / 2 - (bx0 + bx1) / 2) <= 0.6 * max(ax1 - ax0, bx1 - bx0)
            if -0.5 * heights <= gap <= 0.8 * heights and same_column:
                out.append({"text": f"{a['text']} {b['text']}",
                            "split": len(" ".join(_fold(str(a["text"])).split())),
                            "box": [min(ax0, bx0), ay0, max(ax1, bx1), by1],
                            "conf": min(float(a.get("conf", 1)), float(b.get("conf", 1)))})
    return out


def find_text(words: list[dict], query: str, min_conf: float = 0.5) -> dict:
    """Acha `query` nas linhas OCR. Estados: ok (uma posição), ambiguous (≥2 lugares distintos no
    melhor nível: nunca escolhe), miss. Trecho dentro de uma linha longa tem a caixa recortada
    proporcionalmente ao nº de caracteres (aproximação; exata só se a linha inteira casa)."""
    q = " ".join(_fold(query or "").split())
    if not q:
        return {"status": "miss", "reason": "texto vazio", "matches": []}
    found = []
    words = [w for w in words if float(w.get("conf", 1.0)) >= min_conf]
    for w in [*words, *_merge_wrapped(words)]:
        folded = " ".join(_fold(str(w["text"])).split())
        tier, pos = _tier(folded, q)
        if tier < 0:
            continue
        x0, y0, x1, y1 = w["box"]
        n = max(1, len(folded))
        if "split" in w:  # linha junta: só vale se o texto CRUZA a fronteira entre as duas linhas
            if not pos < w["split"] < pos + len(q):
                continue
            bx0, bx1 = x0, x1
        else:
            bx0 = x0 + (x1 - x0) * pos / n
            bx1 = x0 + (x1 - x0) * (pos + len(q)) / n
        found.append({"tier": tier, "text": w["text"], "box": [round(bx0), y0, round(bx1), y1],
                      "center": [round((bx0 + bx1) / 2), round((y0 + y1) / 2)]})
    if not found:
        return {"status": "miss", "reason": "texto não encontrado", "matches": []}
    best = min(m["tier"] for m in found)
    top = [m for m in found if m["tier"] == best]
    distinct = []
    for m in top:  # mesmo lugar lido duas vezes não é ambiguidade
        if all(abs(m["center"][0] - d["center"][0]) > 8 or abs(m["center"][1] - d["center"][1]) > 8
               for d in distinct):
            distinct.append(m)
    if len(distinct) > 1:
        return {"status": "ambiguous", "reason": f"{len(distinct)} lugares com esse texto",
                "matches": distinct}
    return {"status": "ok", "reason": "", "matches": distinct, **distinct[0]}
