#!/usr/bin/env bash
# Bateria textual em SÉRIE nos modelos pequenos que cabem em 6 GB de VRAM.
# Um modelo por vez (o modo gerenciado descarrega tudo antes de cada carga); nada em paralelo.
# Logs completos na tela e em $OUT/<modelo>.log. Uso:
#   scripts/bench-modelos.sh                 # bateria completa, 1 repetição
#   scripts/bench-modelos.sh --quick         # smoke de 8 cenas por modelo
#   scripts/bench-modelos.sh --reps 3        # repetições (argumentos extras vão ao bench)
#   MODELOS="qwen3.5-4b gemma-3-4b" scripts/bench-modelos.sh   # subconjunto
# Não use o LM Studio (chat, outro benchmark) enquanto roda: ele descarrega os modelos residentes.
set -u
cd "$(dirname "$0")/.."

# Ordem: sem thinking primeiro (rápidos), depois os que raciocinam (lentos, podem truncar).
MODELOS=${MODELOS:-"qwen3-vl-2b-instruct qwen3-vl-4b-instruct qwen/qwen3-4b-2507 phi-4-mini-instruct google/gemma-3-4b minicpm5-2b qwen3.5-2b qwen3.5-4b minicpm5-1b-claude-opus-fable5-v2-thinking"}
OUT=${OUT:-runs/bench-serie-$(date +%Y%m%d-%H%M%S)}
mkdir -p "$OUT"
echo "Saída: $OUT"
echo "Modelos: $MODELOS"

status=()
for modelo in $MODELOS; do
  slug=${modelo//\//_}
  echo
  echo "================ $modelo ($(date +%H:%M:%S)) ================"
  uv run python -m evals.model_bench --lmstudio --yes --gpu max --reps 1 \
    --max-tokens 4096 --context 8192 --model "$modelo" --out "$OUT/$slug" \
    --notes "serie 6GB; $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null)" \
    "$@" 2>&1 | tee "$OUT/$slug.log"
  code=${PIPESTATUS[0]}
  status+=("$modelo: exit $code")
  if [ "$code" = 130 ]; then echo "Interrompido; encerrando a série."; break; fi
done

echo
echo "================ Resumo ================"
printf '%s\n' "${status[@]}"
OUT="$OUT" python3 - <<'EOF'
import json, os, pathlib
out = pathlib.Path(os.environ["OUT"])
print(f"\n{'modelo':45} {'acertos':>9} {'sem':>4} {'fmt':>4} {'trunc':>6} {'infra':>6} {'p50 ms':>8}")
for summary in sorted(out.glob("*/model-*/summary.json")):
    for group in json.loads(summary.read_text(encoding="utf-8"))["results"]:
        s, e = group["summary"], group["summary"]["errors"]
        name = f"{group['model']} [{group['settings']['format']}]"
        print(f"{summary.parent.parent.name:45} {s['passed']:>3}/{s['planned']:<5} "
              f"{e['semantic']:>4} {e['format']:>4} {e['truncated']:>6} {e['infra']:>6} "
              f"{s['latency_ms']['p50'] or 0:>8.0f}")
EOF
