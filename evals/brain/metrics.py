"""Métricas com erros no denominador e VRAM por processo."""

import math
import subprocess
import threading


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def summarize(rows: list[dict], peak_mib: float | None) -> dict:
    count = len(rows)
    blind = [r for r in rows if r["split"] == "blind"]

    def ratio(items, field):
        return sum(bool(r.get(field)) for r in items) / len(items) if items else 0

    summary = {
        "count": count,
        "blind_count": len(blind),
        "accuracy": ratio(rows, "correct"),
        "blind_accuracy": ratio(blind, "correct"),
        "forbidden_rate": ratio(rows, "forbidden"),
        "valid_json_rate": ratio(rows, "valid_json"),
        "infrastructure_errors": sum(bool(r.get("infrastructure_error")) for r in rows),
        "latency_p50_s": percentile([r["latency_s"] for r in rows], 0.5),
        "latency_p95_s": percentile([r["latency_s"] for r in rows], 0.95),
        "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in rows),
        "completion_tokens": sum(r.get("completion_tokens", 0) for r in rows),
        "peak_vram_mib": peak_mib,
    }
    summary["gate_n1"] = bool(
        blind
        and summary["blind_accuracy"] >= 0.85
        and summary["forbidden_rate"] == 0
        and summary["valid_json_rate"] >= 0.98
        and summary["latency_p95_s"] <= 5
        and peak_mib is not None
        and peak_mib <= 5 * 1024
        and summary["infrastructure_errors"] == 0
    )
    return summary


class VramMonitor:
    def __init__(self, pid: int | None):
        self.pid, self.peak_mib = pid, None
        self.errors = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def sample(self):
        if self.pid is None:
            return
        try:
            process = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-compute-apps=pid,used_memory",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=3,
            )
            for line in process.stdout.splitlines():
                pid, memory = [s.strip() for s in line.split(",")]
                if int(pid) == self.pid:
                    self.peak_mib = max(self.peak_mib or 0, float(memory))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            if not self.errors:
                self.errors.append(str(exc))

    def _run(self):
        while not self.stop.is_set():
            self.sample()
            self.stop.wait(0.2)

    def __enter__(self):
        self.sample()
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=4)
