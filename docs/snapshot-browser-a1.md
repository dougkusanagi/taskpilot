# A1 — orçamento de snapshot (07/10/2026)

**Medido** com `/tokenize` dos GGUFs instalados (add_special=false), incluindo envelope
Observation e marcador UNTRUSTED. Protótipo sobre HTML das fixtures, sem Chrome/GUI.
Arquivos completos, snapshots e hashes: `evals/brain/results/snapshot-*.json`.
24 condições por modelo: 3 fixtures × 2 ferramentas × poda sim/não × paginação sim/não.
Paginação em blocos de três artigos; poda remove navegação institucional e mantém campos,
links e fatos dos artigos. Conteúdo hidden/aria-hidden e scripts nunca é exposto.

| Tokenizador | Maior read_page bruto sem paginação | Maior leitura com poda + paginação |
| --- | ---: | ---: |
| Qwen3-4B-Instruct-2507 Q4_K_M | 4.376 tokens | 493 tokens |
| Qwen3.5-4B Q4_K_M (sem projetor) | 3.943 tokens | 481 tokens |

**Conclusão medida para estas fixtures:** poda + paginação fica abaixo de 1.500 tokens
por leitura. Não se presume que páginas reais tenham o mesmo tamanho. A extensão deve
usar limites conservadores, informar truncamento/next e repetir a medição sobre DOM real
antes de seu aceite. Esta medida destrava a implementação da ponte/extensão.

**Limitação:** o parser HTML não mede visibilidade computada nem cobertura por overlays.
Essas checagens são do content script e serão testadas em Chrome descartável. Um artigo
individual enorme precisa divisão adicional, não pode ser aceito só porque contém um item.
A leitura não corta fatos de preço/data/URL nos exemplos medidos.

Reprodução: subir um modelo local com `evals.brain.runtime` e executar:

```bash
uv run python -m evals.brain.snapshot_bench --model ALIAS \
  --output evals/brain/results/snapshot-ALIAS.json
```

## Confirmação no DOM real

N2 da extensão confirmou as leituras paginadas, todos os IDs do catálogo por DOM
independente, hidden/covered e scope. `evals/brain/results/snapshot-Chrome-Qwen3.5-4B.json`
mede corpo completo (Observation + Result + marcador de dado não confiável): máximo
538 tokens no Qwen3.5 e 539 no Qwen3, com tokenizadores GGUF.
O segundo arquivo é `evals/brain/results/snapshot-Chrome-Qwen3-4B-2507.json`. Relatório e screenshots em `evals/browser/results/`.
O teto conservador de 1.200 bytes da extensão não substitui medição em tokens.
