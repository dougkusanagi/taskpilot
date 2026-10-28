"""Emula uma GPU menor numa GPU maior (ferramenta de DESENVOLVIMENTO para benchmarks).

Reserva com o driver CUDA (ctypes, sem dependências) toda a VRAM livre acima do orçamento e
segura essa reserva até ser encerrado. Os modelos carregados depois só encontram o orçamento
livre: se não couberem, o carregamento falha com falta de memória, como falharia na placa
pequena. No Linux o driver NVIDIA não transborda para a RAM; no Windows (WDDM) a placa real
transbordaria para memória compartilhada e ficaria lenta em vez de falhar, então este teste é
mais rígido que o uso real.

Orçamento padrão = placa de 6 GB menos 1,5 GB para o sistema (desktop, navegador e um vídeo).
Na RTX 2060 com Windows o desktop sozinho ocupou 1,3 a 1,7 GB em 27/10.

    uv run python -m evals.vram_budget --card-mb 6144 --reserve-mb 1536
"""
from __future__ import annotations

import argparse
import ctypes
import signal
import sys
import time

CHUNK_MB = 64


def budget_mb(card_mb: int, reserve_mb: int) -> int:
    """VRAM que sobra para os modelos (puro)."""
    if card_mb <= 0 or reserve_mb < 0 or reserve_mb >= card_mb:
        raise ValueError("use --card-mb > 0 e 0 <= --reserve-mb < --card-mb")
    return card_mb - reserve_mb


class Cuda:
    def __init__(self) -> None:
        try:
            self.lib = ctypes.CDLL("libcuda.so.1" if sys.platform != "win32" else "nvcuda.dll")
        except OSError as exc:
            raise RuntimeError(f"driver CUDA indisponível: {exc}") from exc
        self._ok(self.lib.cuInit(0), "cuInit")
        dev = ctypes.c_int()
        self._ok(self.lib.cuDeviceGet(ctypes.byref(dev), 0), "cuDeviceGet")
        self.ctx = ctypes.c_void_p()
        self._ok(self.lib.cuCtxCreate_v2(ctypes.byref(self.ctx), 0, dev), "cuCtxCreate")
        self.ptrs: list[ctypes.c_uint64] = []

    @staticmethod
    def _ok(code: int, what: str) -> None:
        if code != 0:
            raise RuntimeError(f"{what} falhou (CUresult {code})")

    def mem_info_mb(self) -> tuple[int, int]:
        free, total = ctypes.c_size_t(), ctypes.c_size_t()
        self._ok(self.lib.cuMemGetInfo_v2(ctypes.byref(free), ctypes.byref(total)), "cuMemGetInfo")
        return free.value // 2**20, total.value // 2**20

    def hold_until_free(self, target_free_mb: int) -> int:
        """Reserva blocos até restarem ~`target_free_mb` livres. Devolve MB reservados."""
        held = 0
        while True:
            free, _ = self.mem_info_mb()
            step = min(CHUNK_MB, free - target_free_mb)
            if step <= 0:
                return held
            ptr = ctypes.c_uint64()
            if self.lib.cuMemAlloc_v2(ctypes.byref(ptr), step * 2**20) != 0:
                return held
            self.ptrs.append(ptr)
            held += step


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--card-mb", type=int, default=6144, help="VRAM da placa emulada")
    ap.add_argument("--reserve-mb", type=int, default=1536,
                    help="VRAM deixada para o sistema (desktop, navegador, vídeo)")
    args = ap.parse_args(argv)
    budget = budget_mb(args.card_mb, args.reserve_mb)
    cuda = Cuda()
    free, total = cuda.mem_info_mb()
    if free < budget:
        print(f"GPU tem só {free} MB livres; orçamento de {budget} MB impossível", flush=True)
        return 2
    held = cuda.hold_until_free(budget)
    free_after, _ = cuda.mem_info_mb()
    print(f"placa real {total} MB; emulando {args.card_mb} MB com {args.reserve_mb} MB p/ o "
          f"sistema; reservados {held} MB; livres p/ modelos {free_after} MB", flush=True)
    stop = {"now": False}
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.update(now=True))
    while not stop["now"]:
        time.sleep(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
