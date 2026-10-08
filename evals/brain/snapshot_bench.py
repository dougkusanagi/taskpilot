"""A1: tokens reais via /tokenize do GGUF residente, sem estimativa por caracteres."""

import argparse
import hashlib
import json
from pathlib import Path

from evals.brain.client import Client
from evals.brain.runner import save
from evals.brain.snapshot import snapshots


def measure(client: Client, output: Path):
    client.check_model()
    rows = []
    for page in ["loja", "webmail", "noticias"]:
        for tool in ["read_page", "get_page_text"]:
            for prune in [False, True]:
                for paginate in [False, True]:
                    reads = snapshots(page, tool, prune=prune, paginate=paginate)
                    counts = []
                    for text in reads:
                        response = client.http.post(
                            "/tokenize", json={"content": text, "add_special": False}
                        )
                        response.raise_for_status()
                        tokens = response.json()["tokens"]
                        if not isinstance(tokens, list):
                            raise ValueError("tokenizador não retornou ids")
                        counts.append(len(tokens))
                    rows.append(
                        {
                            "fixture": page,
                            "tool": tool,
                            "prune": prune,
                            "paginate": paginate,
                            "tokens_per_read": counts,
                            "max_tokens": max(counts),
                            "within_1500": max(counts) <= 1500,
                            "snapshots": reads,
                            "sha256": hashlib.sha256("\n".join(reads).encode()).hexdigest(),
                        }
                    )
    report = {
        "model": client.model,
        "tokenizer": "GGUF /tokenize add_special=false",
        "measurement": "measured",
        "rows": rows,
        "scope": "protótipo sobre HTML das fixtures; confirmar com DOM da extensão em N2",
    }
    save(output, report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8091")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    client = Client(args.endpoint, args.model, constrained=False, thinking=False)
    try:
        report = measure(client, args.output)
        print(
            json.dumps(
                [
                    {k: v for k, v in r.items() if k not in {"snapshots", "sha256"}}
                    for r in report["rows"]
                ],
                indent=2,
            )
        )
    finally:
        client.close()
