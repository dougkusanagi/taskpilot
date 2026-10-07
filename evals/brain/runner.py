"""N1 congelado, três repetições; escrita incremental para não perder falhas."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from evals.brain.client import Client
from evals.brain.grading import grade
from evals.brain.metrics import VramMonitor, summarize
from evals.brain.scenes import corpus_hash, load_scenes


def save(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def run(
    model: str,
    endpoint: str,
    *,
    pid: int | None = None,
    repetitions: int = 3,
    split: str = "all",
    mode: str = "matrix",
    max_tokens: int = 512,
    output: Path | None = None,
    runtime_manifest: Path | None = None,
) -> dict:
    if repetitions < 1:
        raise ValueError("repetitions deve ser positivo")
    if Path(model).name != model or model in {".", ".."}:
        raise ValueError("model deve ser alias, não caminho")
    output = output or Path(__file__).parent / "results" / f"{model}.json"
    report = {
        "model": model,
        "date_utc": datetime.now(timezone.utc).isoformat(),
        "corpus_sha256": corpus_hash(),
        "contract_version": "0",
        "temperature": 0.1,
        "repetitions": repetitions,
        "split": split,
        "endpoint": endpoint,
        "server_pid": pid,
        "max_tokens": max_tokens,
        "conditions": [],
        "complete": False,
        "limitations": [
            "Cenas sintéticas e capturas declaradas; não é N2/N3.",
            "VRAM amostrada a cada 200 ms; não mede alocações entre amostras.",
        ],
    }
    conditions = [(False, False), (True, False), (False, True), (True, True)]
    if mode != "matrix":
        conditions = [("schema" in mode, "on" in mode)]
    if runtime_manifest:
        metadata = json.loads(runtime_manifest.read_text())
        if metadata["pid"] != pid or metadata["model"] != model:
            raise ValueError("manifesto do runtime não corresponde ao PID/modelo solicitado")
        report["runtime"] = metadata
    scenes = load_scenes(split)
    for constrained, thinking in conditions:
        name = f"{'schema' if constrained else 'free'}-thinking-{'on' if thinking else 'off'}"
        condition = {
            "name": name,
            "constrained": constrained,
            "thinking": thinking,
            "rows": [],
            "warmup": {},
            "complete": False,
        }
        report["conditions"].append(condition)
        client = Client(
            endpoint, model, constrained=constrained, thinking=thinking, max_tokens=max_tokens
        )
        try:
            preflight_error = ""
            try:
                client.check_model()
            except Exception as exc:
                preflight_error = f"{type(exc).__name__}: {exc}"
                condition["preflight_error"] = preflight_error
            warmup = {
                "goal": "Pergunte qual tarefa o usuário quer.",
                "history": [],
                "observation": {"id": "warmup", "elements": [], "text": ""},
                "evidence": [],
            }
            try:
                if preflight_error:
                    raise RuntimeError(preflight_error)
                response = client.complete(warmup)
                condition["warmup"] = {
                    "response": response.content,
                    "latency_s": response.latency_s,
                    "finish_reason": response.finish_reason,
                }
            except Exception as exc:
                condition["warmup"] = {"error": f"{type(exc).__name__}: {exc}"}
            with VramMonitor(pid) as monitor:
                for scene in scenes:
                    for repeat in range(repetitions):
                        row = {
                            "scene": scene["id"],
                            "categoria": scene["categoria"],
                            "split": scene["split"],
                            "repeat": repeat,
                            "valid_json": False,
                            "correct": False,
                            "forbidden": False,
                        }
                        payload = {
                            k: scene[k] for k in ["goal", "history", "observation", "evidence"]
                        }
                        payload["seed"] = 42 + repeat
                        if scene.get("image"):
                            payload["image"] = scene["image"]
                        started = time.monotonic()
                        try:
                            if preflight_error:
                                raise RuntimeError(preflight_error)
                            response = client.complete(payload)
                            row.update(grade(scene, response.content).dump())
                            row.update(
                                response=response.content,
                                reasoning=response.reasoning,
                                prompt_tokens=response.prompt_tokens,
                                completion_tokens=response.completion_tokens,
                                finish_reason=response.finish_reason,
                            )
                        except Exception as exc:
                            row["infrastructure_error"] = f"{type(exc).__name__}: {exc}"
                        row["latency_s"] = time.monotonic() - started
                        condition["rows"].append(row)
                        condition["summary"] = summarize(condition["rows"], monitor.peak_mib)
                        condition["summary"]["gate_n1"] = False
                        condition["summary"]["protocol_complete"] = False
                        save(output, report)
                    print(
                        f"{name}: {scene['id']} "
                        f"({len(condition['rows'])}/{len(scenes) * repetitions})",
                        flush=True,
                    )
                condition["vram_errors"] = monitor.errors
                condition["summary"] = summarize(condition["rows"], monitor.peak_mib)
            if runtime_manifest:
                metadata = json.loads(runtime_manifest.read_text())
                report["runtime"] = metadata
                peak = max(monitor.peak_mib or 0, metadata.get("peak_vram_mib") or 0) or None
                condition["summary"] = summarize(condition["rows"], peak)
            protocol_complete = (
                repetitions == 3
                and split == "all"
                and len(scenes) >= 96
                and len(condition["rows"]) == len(scenes) * repetitions
            )
            condition["summary"]["protocol_complete"] = protocol_complete
            condition["summary"]["gate_n1"] &= protocol_complete
            condition["complete"] = True
        finally:
            client.close()
            save(output, report)
    report["complete"] = True
    save(output, report)
    return report
