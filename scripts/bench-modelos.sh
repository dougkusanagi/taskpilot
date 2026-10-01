#!/usr/bin/env bash
# Baterias em SÉRIE nos modelos pequenos candidatos (6 GB de VRAM). Um modelo por vez: o modo
# gerenciado descarrega tudo antes de cada carga; nada roda em paralelo. Logs completos na
# tela e em $OUT/<modelo>.log.
#
#   scripts/bench-modelos.sh                      # SUITE=text (padrão), 3 repetições
#   SUITE=production scripts/bench-modelos.sh     # prompt real do planner (reasoning off, 256 tok)
#   SUITE=trajectory scripts/bench-modelos.sh     # cenários multi-passo em simulador
#   SUITE=ground scripts/bench-modelos.sh         # localizar elementos em screenshots (visão)
#   scripts/bench-modelos.sh --quick              # smoke; argumentos extras vão ao bench
#   MODELOS="qwen3.5-4b|native,off|max" scripts/bench-modelos.sh   # subconjunto
#
# Antes da suíte ground: uv run --with playwright python -m evals.ground_capture
# Não use o LM Studio (chat, outro benchmark) enquanto roda: ele descarrega os modelos residentes.
#
# Cada entrada: chave-do-modelo|variações-de-reasoning|offload-de-GPU
#  - native = padrão do template (ligado nos modelos que raciocinam); off = reasoning_effort none.
#  - low/medium/high não entram: no LM Studio não mudam o raciocínio de forma consistente
#    (medido em 01/10 em qwen3.5-4b, minicpm5-2b, gemma-4-e2b e nemotron-3-nano-4b).
#  - Modelos Instruct (sem reasoning) só têm native.
set -u
cd "$(dirname "$0")/.."

SUITE=${SUITE:-text}
REPS=${REPS:-3}
case "$SUITE" in
  text)
    TOKENS=4096
    PADRAO="qwen3-vl-4b-instruct|native|max
qwen/qwen3-4b-2507|native|max
nvidia/nemotron-3-nano-4b|native,off|max
google/gemma-4-e2b|native,off|max
qwen3.5-4b|native,off|max
qwen3-vl-4b-thinking|native,off|max
minicpm5-2b|native,off|max
mai-ui-2b|native|max
google/gemma-4-e4b|native,off|auto" ;;
  production|trajectory)
    # Produção usa thinking desligado e max_tokens 256; o orçamento maior só vale p/ trajetória.
    if [ "$SUITE" = production ]; then TOKENS=256; else TOKENS=512; fi
    PADRAO="qwen3-vl-4b-instruct|native|max
qwen/qwen3-4b-2507|native|max
nvidia/nemotron-3-nano-4b|off|max
google/gemma-4-e2b|off|max
qwen3.5-4b|off|max
qwen3-vl-4b-thinking|off|max
minicpm5-2b|off|max" ;;
  ground)
    TOKENS=1024
    PADRAO="qwen3-vl-4b-instruct|native|max
qwen3-vl-4b-thinking|native,off|max
qwen3.5-4b|native,off|max
google/gemma-4-e2b|native,off|max
mai-ui-2b|native|max
gui-owl-1.5-2b-instruct|native|max
google/gemma-4-e4b|native,off|auto" ;;
  *) echo "SUITE inválida: $SUITE (text|production|trajectory|ground)"; exit 2 ;;
esac

ENTRADAS=${MODELOS:-$PADRAO}
OUT=${OUT:-runs/bench-$SUITE-$(date +%Y%m%d-%H%M%S)}
mkdir -p "$OUT"
echo "Suíte: $SUITE | repetições: $REPS | max-tokens: $TOKENS"
echo "Saída: $OUT"
echo "Modelos:"; echo "$ENTRADAS"

status=()
while IFS='|' read -r modelo variacoes gpu; do
  [ -z "$modelo" ] && continue
  slug=${modelo//\//_}
  echo
  echo "================ $modelo [$variacoes] gpu=$gpu ($(date +%H:%M:%S)) ================"
  uv run python -m evals.model_bench --lmstudio --yes --suite "$SUITE" --gpu "$gpu" \
    --reps "$REPS" --max-tokens "$TOKENS" --context 8192 --thinking "$variacoes" \
    --model "$modelo" --out "$OUT/$slug" \
    --notes "$SUITE 6GB; $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null)" \
    "$@" 2>&1 | tee "$OUT/$slug.log"
  code=${PIPESTATUS[0]}
  status+=("$modelo: exit $code")
  if [ "$code" = 130 ]; then echo "Interrompido; encerrando a série."; break; fi
done <<< "$ENTRADAS"

echo
echo "================ Resumo ($SUITE) ================"
printf '%s\n' "${status[@]}"
OUT="$OUT" python3 - <<'EOF'
import json, os, pathlib
out = pathlib.Path(os.environ["OUT"])
print(f"\n{'modelo':30} {'reasoning':9} {'acertos':>9} {'sem':>4} {'fmt':>4} {'trunc':>6} "
      f"{'infra':>6} {'p50 ms':>8} {'tok/s':>6} {'VRAM MB':>8}")
for summary in sorted(out.glob("*/model-*/summary.json")):
    for group in json.loads(summary.read_text(encoding="utf-8"))["results"]:
        s, e = group["summary"], group["summary"]["errors"]
        print(f"{summary.parent.parent.name:30} {group['settings']['thinking']:9} "
              f"{s['passed']:>3}/{s['planned']:<5} {e['semantic']:>4} {e['format']:>4} "
              f"{e['truncated']:>6} {e['infra']:>6} {s['latency_ms']['p50'] or 0:>8.0f} "
              f"{s.get('tokens_per_s_p50') or 0:>6.0f} {group.get('gpu', {}).get('peak_mb') or 0:>8}")
EOF
