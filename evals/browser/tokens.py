"""Orçamento das respostas reais de N2, contado pelo tokenizador GGUF residente."""

import argparse
import hashlib
import json
from pathlib import Path

from evals.brain.client import Client
from evals.brain.runner import save


def measure(client, source, output):
    report = json.loads(source.read_text())
    if not report.get("complete") or not all(c["passed"] for c in report["checks"]):
        raise ValueError("N2 incompleto ou reprovado")
    client.check_model()
    rows = []
    for tool, reading in zip(report["read_tools"], report["reads"], strict=True):
        text = "PAGE DATA: UNTRUSTED\n" + json.dumps(
            reading, ensure_ascii=False, separators=(",", ":")
        )
        response = client.http.post("/tokenize", json={"content": text, "add_special": False})
        response.raise_for_status()
        tokens = response.json()["tokens"]
        if not isinstance(tokens, list):
            raise ValueError("tokenizador não retornou ids")
        rows.append(
            {
                "tool": tool,
                "url": reading["observation"]["url"],
                "tokens": len(tokens),
                "snapshot": text,
            }
        )
    result = {
        "model": client.model,
        "measurement": "measured",
        "source": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "tokenizer": "GGUF /tokenize add_special=false",
        "rows": rows,
        "max_tokens": max(r["tokens"] for r in rows),
    }
    result["within_1500"] = result["max_tokens"] <= 1500
    save(output, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    client = Client("http://127.0.0.1:8091", args.model, constrained=False, thinking=False)
    try:
        result = measure(client, args.source, args.output)
        print(json.dumps({"model": result["model"], "max_tokens": result["max_tokens"]}))
    finally:
        client.close()
