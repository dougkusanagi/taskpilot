# Bancada 6 GB com modelos novos (28/10/2026)

Orçamento emulado: placa de 6 GB menos 1,5 GB para o sistema (desktop + vídeo) = **4608 MB**
(`evals/vram_budget.py` reserva o resto da VRAM). RTX 5060 Ti, Linux, llama.cpp b11954 CUDA.
No Linux a falta de memória falha; no Windows (WDDM) transbordaria para a RAM e ficaria lenta,
então este teste é mais rígido que o uso real. Tudo offline: sem E2E no Windows/Sandbox.
Não muda o default B1.

Reproduzir: `uv run python -m evals.budget_bench --plan evals/budget_plan.json --llama <llama-server> --models <dir> [--only nomes]`.
Brutos em `runs/budget-20261028-*`.

## Visão (bateria ground-v2: 32 alvos existentes + 31 ausentes, ×3)

| Localizador | Existentes achados | Cliques em ausentes | p50 | VRAM |
| --- | ---: | ---: | ---: | ---: |
| MAI-UI-2B (pyauto) | 84/96 | 15/93 | 90 ms | 2,8 GB |
| LFM3-VL-2B (p2d, 512 tokens) | 96/96 | 38/93 | 863 ms | 2,1 GB |
| LFM3-VL-2B reasoning-budget 256 | 96/96 | 93/93 | — | — |

## LFM3-VL + verificador d1-2B (recorte em volta do ponto, pergunta `noul`)

| Limite | Acertos que clicam | Cliques errados |
| ---: | ---: | ---: |
| 0 (sem verificador) | 96/96 | 45 |
| 0,15 | 96/96 | 15 |
| 0,20 | 93/96 | 9 |
| 0,50 | 87/96 | 3 |

Limite escolhido na mesma bateria: risco de sobreajuste; validar em capturas novas.
Marcar o ponto na tela inteira com um círculo não funcionou; o recorte funciona.

## Planner (trajetória 11×3; produção 32×3)

| Planner | Trajetória | Produção | Latência | VRAM |
| --- | ---: | ---: | --- | ---: |
| Qwen3-4B-Instruct-2507 | 30/33 | 45/96 | 223 ms | 2,9 GB |
| Nemotron 3.5 Bolt E4B IQ3_XXS ("off", ainda raciocina ~110 tokens) | 29/33 | 70/96 | 1,65 s (p95 4 s) | 2,4 GB |
| Bolt E4B reasoning-budget 0 | 27/33 | 51/96 | 485 ms | 2,4 GB |
| Ministral-3-3B | 10/33 | 29/96 | — | — |
| Nemotron-3-Nano-4B off | 13/33 | 20/96 | — | — |
| LFM3-1.7B off | 7/33 | 16/96 | — | — |

## Combinações no orçamento

- Qwen3-4B + MAI-UI ou LFM3-VL, tudo na GPU: **não cabe** (falta de memória na 1ª imagem).
- Com o projetor de visão na CPU (`mmproj_offload: false`): Qwen + LFM3-VL 4,2 GB; Qwen + MAI-UI 4,5 GB.
- Bolt + LFM3-VL cabe todo na GPU (4,5 GB), visão 838 ms.
- d1 como 3º modelo na GPU: falta de memória em todas. d1 na CPU (`ngl 0`): cabe nos dois trios,
  verificador ~310 ms, cliques errados 14 → 2 com limite 0,20, trajetória 10/11.

## Ressalvas

Bateria visual pequena e capturada em páginas web; latência medida em GPU maior que a placa-alvo;
runtime próprio não sobe o d1 sozinho (verificador é opt-in em `vision.verifier.base_url`).
