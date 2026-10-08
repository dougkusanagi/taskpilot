"""Carrega cenas congeladas; expectativas nunca entram no prompt."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parent


def load_scenes(split: str = "all", corpus: str = "legacy") -> list[dict]:
    scenes = [
        json.loads(p.read_text())
        for p in sorted(
            (ROOT / ("scenes_browser" if corpus == "browser" else "scenes")).glob("*.json")
        )
    ]
    return [s for s in scenes if split == "all" or s["split"] == split]


def corpus_hash(corpus: str = "legacy") -> str:
    digest = hashlib.sha256()
    for path in sorted(
        (ROOT / ("scenes_browser" if corpus == "browser" else "scenes")).glob("*.json")
    ):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
