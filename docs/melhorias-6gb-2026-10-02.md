# Melhorias para rodar em 6 GB — implementação e medições (02/10/2026)

Resumo do que foi implementado a partir da lista de ideias, **o que cada uma rendeu medido** e o
que ficou sem verificação. Hardware de teste: RTX 2060 6 GB, Linux, llama.cpp CUDA (o mesmo
`llama-server` que o runtime próprio usa no Windows) e LM Studio. Tudo offline: nenhum clique real
e nenhum teste no Sandbox do Windows (gate R4 segue aberto). Não muda o default B1.

## Resultado em uma tabela

| | Antes | Depois | Como medir |
| --- | ---: | ---: | --- |
| Planner Qwen3-4B, trajetórias (11 cenários, 55 episódios) | 30/55 (55%) | **55/55** | `--suite trajectory` |
| …só nos 3 cenários cegos (nunca usados p/ ajustar) | 5/15 | **15/15** | idem (ver ressalva) |
| Qwen3.5-4B, 5 cenários originais (com o veto) | 19/25 | 25/25 | idem |
| Localização, Qwen3-VL-2B | 39% | **77%** | `--suite ground` |
| Localização, MAI-UI-2B | 0% | **80 a 84%**, 0 cliques falsos | idem |
| Localização, GUI-Owl-1.5-2B | 11% | **75%**, 0 cliques falsos | idem |
| Localização, Qwen3-VL-4B | 63% | **81 a 86%** | idem |
| Planner no prompt de produção (32 cenas), Qwen3.5-4B | 56% | 76% | `--suite production` |
| Planner no prompt de produção, Qwen3-4B-2507 | 39% | 62% | idem |
| Suite de testes no Linux | 49 erros | **0 erros** (474 testes) | `unittest discover` |

## Perfis novos (medidos; `main.py --profile P1`)

| Perfil | Modelos | VRAM de pico* | Trajetórias | Localização | Ausentes | Latência (loc.) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| **P1** | Qwen3-4B-Instruct-2507 + MAI-UI-2B (2 processos) | 5,3 GB | 55/55 | 84% | 6/6 | 121 ms |
| **P2** | Qwen3-4B-Instruct-2507 + GUI-Owl-1.5-2B | ~5,3 GB (não medido junto) | = P1 (mesmo planner; não rodado) | 75% | 6/6 | 174 ms |
| **U3** | Qwen3-VL-4B-Instruct (1 processo) | 4,9 GB | 55/55 | 86% | **0/6** (3 cliques falsos) | 272 ms |

\* VRAM total do sistema (modelos + desktop de ~0,8 GB) por `nvidia-smi`. O preset de cada perfil
(`config.PROFILES[...]["preset"]`) liga os recursos do planner, o protocolo de visão e as flags de
memória do runtime. **Prefira P1** quando o alvo pode não existir (menos cliques no escuro).

## O que foi implementado

### Planner (`planner.py`, `recipes.py`, `guards.py`)
- **`done` cita IDs de evidência (`E1`, `E2`…)** — *defeito real corrigido*: o loop só aceitava `done`
  com `evidences` idênticas ao texto das evidências confirmadas, mas o prompt descrevia `{"type":"done"}`
  e mostrava as evidências como texto livre truncado; todo `done` era vetado. Agora o estado mostra
  `E1=…`, o prompt ensina a citá-las e `verification.resolve_evidence_ref` resolve o ID. Sozinho
  subiu o Qwen3.5-4B de 56% para 66% no prompt de produção.
- **Schema por tipo de ação (`dynschema`)**: uma variante por ação, só com os campos dela. `uia_click`
  e `fill` só aceitam nomes visíveis na tela (sem `Button:`), `key/keys/app` restritos, e **`done` só
  existe se houver evidência confirmada** (mesma regra do verificador). Elimina os campos extras e os
  `done` sem evidência.
- **Exemplos curtos (`fewshot`)** de OUTROS aplicativos: formato, clicar no campo antes de digitar,
  não repetir quando o campo já mostra o valor, `done` com evidências, recuperar de "no visible effect".
  0 a +23 pontos conforme o modelo (Qwen3-4B: 39% → 62%; Gemma 4 E2B: 36% → 55%; Qwen3-VL-4B: sem efeito).
- **Checklist do pedido (`plan`)**: uma chamada curta no início gera os requisitos, guardados no
  estado (`state.requirements`) e mostrados a cada passo. até +5 episódios em 25 (Qwen3.5: 15 → 20; Qwen3-4B: 20 → 25; Qwen3-VL: sem efeito).
- **Veto de ambiguidade (`guards.goal_ambiguity`)**: se o pedido diz só "Ana" e há "Ana pessoal" e
  "Ana trabalho", o clique é recusado com ordem de usar `ask` (a regra do projeto de nunca adivinhar
  entre pessoas). A resposta do humano esclarece; pedido por posição ("o primeiro resultado") não
  é vetado. Corrigiu os últimos fracassos dos Qwen (20/25 → 25/25).
- **Escalada de raciocínio** (`planner.escalate_thinking`, opt-in) e **`cache_prompt`** (prefixo estável).

### Ferramentas (`loop.py`, `ocr.py`)
- **`click_text`**: OCR com caixas (RapidOCR, extra `ocr`; tesseract e Windows.Media.Ocr como outros
  backends — o do Windows **não foi testado**). Recusa ambiguidade e ausência. Medido sem modelo
  (`evals.ocr_bench`): 18/19 alvos com texto, **0 cliques errados**, ~1 s por tela em CPU.
- **`fill`** (clicar, selecionar tudo, digitar; só seleciona se o foco é um campo editável),
  **`save_as`** (só digita o nome se o diálogo Salvar abriu), **`perceive wait:<texto>`** (espera
  ativa que devolve "apareceu"/"não apareceu").
- No simulador essas ferramentas **não** foram um ganho líquido (ver "Não funcionou"): ficam opt-in
  (`planner.features: ["tools"]`).

### Visão (`vocaela.py`)
- **Protocolo de grounding por família** (`vision.protocol`, derivado do modelo): `pyauto`
  (`click(x, y)`) para MAI-UI/GUI-Owl, `p2d` (`{"point_2d":[x,y]}`) para Qwen3-VL, `json` histórico
  para o resto. **Todos respondem em escala 0..1000** (medido: com "pixel" caem a 0–12%); o adaptador
  fixa `coords=1000`. O parser tolerante também entende `x/y`, `coordinate`, `<point>`, `click()`.
- **Zoom em duas etapas** (`vision.zoom`): implementado e testado, **desligado** (piorou tudo).
- O recorte pela janela ativa já existia (`obs.capture_for_vision`).

### Runtime próprio (`server.py`, `tools.py`, `config.py`)
- **`runtime.backend` (`cpu`/`vulkan`/`cuda`)**: o runtime só baixava o llama.cpp *de CPU*, então
  `ngl` nunca usava a GPU (por isso o U1 levava ~38 s por chamada). Troca de backend rebaixa o binário.
- **`kv_cache` (q8_0/q4_0), `mmproj_offload`, `parallel`**: todas as flags conferidas contra o
  `llama-server` real. `mmproj_offload: false` põe o encoder de imagem na CPU: MAI-UI com isso fez
  83% a 121 ms e deixou o par planner+visão em 5,3 GB.
- **Registro de GGUFs** para P1/P2/U3 (URLs conferidas com HEAD 200).
- **Navegador abre com `--force-renderer-accessibility`** (`launch.browser_args`) para o UIA ver a
  página. **Não verificado no Windows** e só vale ao iniciar um processo novo do navegador.

### Medição e dados (`evals/`)
- Suítes `production` (prompt real), `trajectory` (simulador multi-passo, 11 cenários, 3 cegos) e
  `ground` (37 alvos em 12 páginas, rotulados pela DOM); `--features` liga cada recurso; mede VRAM
  de pico e tokens/s por grupo.
- `main.py --record` grava `runs/<id>/sft.jsonl`; `evals.export_sft planner|ground` gera dados de
  treino. **O treino em si não foi feito.**

## Descobertas que mudam decisões
1. Prompt importa mais que modelo: o Qwen3-VL-2B foi de 39% a 77% só trocando o formato do prompt.
2. Com 4B + imagem, **contexto 8192 estoura 6 GB**; 4096 cabe. `--parallel 1` e KV q8 ajudam.
3. A escala nativa dos modelos GUI é 0..1000, não pixels nem 0..1 (o prompt pode mentir; meça).
4. `reasoning_effort` do LM Studio só tem dois estados reais (ligado e `none`); `low/medium/high`
   não mudam nada de forma consistente.

## Não funcionou (e por quê)
- **`why`** (raciocínio curto antes da ação): dobra a latência e não ajuda (55% vs 57%).
- **Zoom em duas etapas**: Qwen3-VL-4B 81% → 77%, Qwen3.5-4B 53% → 42%, GUI-Owl 70% → 67%, MAI-UI 82% → 72%; não ajudou em nenhum.
- **`recipes` e `tools` no simulador**: sem ganho líquido (31–35/40 contra 36–40/40 sem eles, no mesmo modelo; as ferramentas encurtam os caminhos mas induzem repetições); `recipes` ajudou
  na bateria de 32 cenas, mas esses apps são os mesmos das receitas (contaminação) — só vale medir
  em apps que não estão nas receitas.
- **MiniCPM5-2B (planner atual)**: 12/25 nas trajetórias mesmo com a pilha completa; é o modelo
  que mais perde para os Qwen em tudo que foi medido.
- Não testei o Bonsai (sem visão, GGUF ternário sem benchmark de JSON) nem fine-tuning.

## Ressalvas honestas
- O **simulador foi ajustado** várias vezes quando as transcrições mostraram rigidez dele (caminhos
  legítimos que ele rejeitava: `sequence`, menu "Salvar como", Enter no diálogo, rótulos em inglês,
  clicar de novo em "Renomear" para confirmar). O último ajuste (RenameFile) foi feito **depois** de
  ver o resultado cego daquele cenário; os outros dois cenários cegos não foram tocados.
- Os exemplos do `fewshot` foram ajustados olhando falhas dos cenários "ajustados" (100%); o número
  que mede generalização é o dos **cenários cegos** (5/15 → 15/15), com 3 cenários só.
- O simulador entrega a lista de pendências no baseline (`oracle`); o loop real não a gera. O que o
  loop real tem é o `plan` (checklist gerado pelo modelo), que foi o medido nos números finais.
- Nenhum desses números prova o app de ponta a ponta: faltam UIA real, DPI, multi-monitor e Sandbox.

## Como usar
```bash
uv run python main.py "..." --profile P1          # planner Qwen3-4B + MAI-UI-2B (baixa os GGUFs)
uv run python main.py "..." --profile U3          # Qwen3-VL-4B unificado
uv run python main.py "..." --profile P1 --record # grava trajetórias p/ treino futuro
uv sync --extra ocr                               # OCR com caixas (click_text)
SUITE=trajectory FEATURES=dynschema,fewshot,plan scripts/bench-modelos.sh
```
Reverter qualquer recurso: remova de `planner.features`; `vision.protocol: json`; `runtime.backend: cpu`.
