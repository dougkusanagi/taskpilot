# Pesquisa de modelos locais para planejar e perceber interfaces

Pesquisa de 01/10/2026 para selecionar a próxima rodada do TaskPilot em GPU
de 6 GB. As prioridades propostas são Qwen3.5-4B com controle real de thinking,
LFM2.5-VL-3B, Agents-A1-4B e Gemma 4 E2B. GUI-Owl merece uma avaliação visual
própria. Bonsai merece um experimento separado por exigir runtime específico.
São recomendações de teste, sem promoção de perfil ou alteração do default B1.

## Evidência da rodada existente

A série `runs/bench-serie-20260930-222032/` usa `text-32-v2`, 32 cenas,
uma repetição, JSON schema, temperatura 0,1, contexto solicitado 8192,
saída máxima 4096 e `thinking=native`. As notas informam RTX 2060 com
6144 MiB. O inventário registra Qwen3.5-4B Q4_K_M e Qwen3.5-2B Q8_0;
portanto, nem a quantização é igual entre esses dois modelos.

| Modelo | Acertos do avaliador | Mediana | p95 |
| --- | ---: | ---: | ---: |
| Qwen3.5-4B | 27/32 | 3,169 s | 6,405 s |
| Qwen3-VL-4B-Instruct | 22/32 | 0,228 s | 0,400 s |
| MiniCPM5-2B | 21/32 | 4,409 s | 22,910 s |
| Qwen3-4B-2507 | 19/32 | 0,207 s | 0,373 s |
| Qwen3.5-2B | 12/32 | 4,254 s | 45,091 s |
| Gemma 3 4B | 9/32 | 0,492 s | 0,589 s |
| Qwen3-VL-2B-Instruct | 7/32 | 0,189 s | 0,259 s |
| Phi-4-mini-instruct | 5/32 | 0,299 s | 0,489 s |
| MiniCPM5-1B Fable V2 Thinking | 0/32 | 1,745 s | 38,617 s |

O Qwen3.5-4B realmente raciocinou: há `reasoning_content` em 32/32
respostas e 6991 tokens de reasoning dos 7545 tokens de saída (92,7%).
Mediana de 185,5 tokens de reasoning por decisão. O 2B gerou 31297 tokens
de reasoning e truncou cinco respostas. O Qwen3-VL-4B-Instruct registrou zero
tokens de reasoning. O vencedor recente é o Qwen3.5-4B, diferente do
Qwen3-VL-2B-Instruct do probe U1 histórico em CPU.

Há limitações do oráculo: duas respostas de preço do 4B contêm o valor
correto, mas a frase “O preço visível da oferta é...” é rejeitada pela
gramática conservadora. Não alteramos regras ou notas retrospectivamente.
Uma inspeção humana deve acompanhar a classificação, para separar esses
falsos negativos dos erros reais, como escolher uma pessoa entre alvos ambíguos.
Todos esses testes são textuais; não medem OCR, grounding ou trajetória GUI.

## Desligar o reasoning do Qwen

O [card oficial do Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B)
documenta thinking por padrão e modo direto via
`chat_template_kwargs.enable_thinking=False`. `/nothink` não é o controle
oficial dessa família. A manutenção de qualidade ao desligar precisa ser medida.

Nesta instalação do LM Studio, o override usado pelo runner foi ignorado:
um aquecimento com `--thinking off` ainda retornou 122 tokens de reasoning.
Essa primeira execução também sofreu descarregamento do modelo pela API e
abortou por cinco erros de infraestrutura; seus resultados não entram na
comparação. Arquivos preservados em `runs/qwen35-ablation-20261001-off/`.

Um probe posterior confirmou que `reasoning_effort="none"` no endpoint
OpenAI-compatible retornava zero tokens de reasoning. `reasoning="off"`
e `enable_thinking=False` no nível superior não funcionaram nesse probe.
Isso é uma descoberta local, não uma garantia para toda versão/backend.
A [API nativa do LM Studio](https://lmstudio.ai/docs/developer/rest/chat)
documenta `reasoning: "off"` para `/api/v1/chat`, que é um endpoint diferente.

A comparação controlada está preservada em
`runs/research-qwen-20261001/`: `ablation.py` reutiliza o avaliador sem mudar
seu código, substituindo o campo de template por `reasoning_effort="none"`
somente no braço off. O braço native omite ambos. Mesmo processo/modelo,
32 cenas × 3 repetições, schema, temperatura 0,1, 4096 tokens e contexto 8192.
O braço off é executado antes de native; não há contrabalanceamento de ordem.
Os hashes dos prompts/casos e as respostas brutas ficam nos relatórios.

### Resultado da ablação nesta máquina

| Modo real | Acertos do avaliador | Mediana | p95 | Tokens de reasoning |
| --- | ---: | ---: | ---: | ---: |
| Ligado, native | 79/96 (82,3%) | 3,275 s | 8,019 s | 19871 |
| Desligado, reasoning_effort=none | 70/96 (72,9%) | 0,330 s | 0,551 s | 0 |

As 96 respostas native têm reasoning; nenhuma das 96 off tem reasoning.
Ambos terminaram sem erro de infraestrutura ou truncamento. Native teve três
erros de validação de formato/contrato; off, zero. Os hashes de casos e prompts
são iguais entre os braços e iguais aos da série recente. Há um aquecimento
separado por modo. O modelo carregado foi descarregado ao encerrar a comparação.

Sem thinking, a mediana foi 9,92 vezes menor, mas a nota caiu 9,38 pontos
percentuais. Off inventou skills não disponibilizadas nos casos de navegação e
Unicode, e escolheu uma pessoa no caso ambíguo em 3/3 respostas. Native passou
Unicode e ambiguidade em 3/3, mas também teve erros próprios de foco, recuperação
e leitura. Portanto, **não preservou a mesma qualidade ao desligar** nas
condições medidas. Nenhum modo passou o alvo de 90% de decisões da proposta R3.

Essa conclusão vale para os casos congelados, a amostragem e o runtime usados.
Repetir cenas três vezes mede estabilidade; não transforma 32 cenas em 96
cenários independentes. A comparação não mede visão nem permite escolher modo
automaticamente por uma regra de app. Uma política híbrida escolhida pelo modelo
ou uma distilação para respostas curtas exigiria avaliação própria.

Relatórios brutos: `runs/research-qwen-20261001/off/resultado.zip` e
`runs/research-qwen-20261001/native/resultado.zip`. Conjunto e comparativo:
`runs/research-qwen-20261001/resultado.zip` e `comparativo.md`.

## Candidatos prioritários

Os tamanhos abaixo são arquivos em GB decimais, conferidos na API de arquivos
do Hugging Face em 01/10. Não são consumo de VRAM. Cache, buffers, resolução
visual e memória do desktop precisam ser medidos à parte.

| Prioridade proposta | Modelo e origem | Arquivos iniciais | Motivo e limitação |
| --- | --- | --- | --- |
| 1 | [LFM2.5-VL-3B](https://huggingface.co/LiquidAI/LFM2.5-VL-3B), agosto/2026 | [GGUF oficial](https://huggingface.co/LiquidAI/LFM2.5-VL-3B-GGUF): Q4_K_M 1,674 GB + projetor Q8_0 0,583 GB | Pós-treinamento para OCR, localização e tool use; hipótese de bom equilíbrio entre percepção e latência. Validar CUDA/LM Studio, texto pequeno e decisões em PT-BR. |
| 2 | [Agents-A1-4B](https://huggingface.co/InternScience/Agents-A1-4B), julho/2026 | [GGUF do publicador](https://huggingface.co/InternScience/Agents-A1-4B-Q4_K_M-GGUF): Q4_K_M 2,709 GB + projetor 0,672 GB | Pós-treinamento com múltiplos professores e foco em planejamento, instruções e ferramentas; arquitetura Qwen3.5 com visão. Benchmark agentic do autor não demonstra computer use Windows. |
| 3 | [Gemma 4 E2B IT](https://huggingface.co/google/gemma-4-E2B-it), abril/2026 | [GGUF Unsloth](https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF): Q4_K_M 3,107 GB + projetor F16 0,986 GB | Família diferente, com entendimento de tela, OCR, pointing e function calling. E2B tem 5,1B parâmetros totais com embeddings, não apenas 2B residentes. Testar thinking on/off e suporte do runtime. |
| 4 | [GUI-Owl-1.5-2B/4B-Instruct](https://huggingface.co/mPLUG/GUI-Owl-1.5-4B-Instruct), fevereiro/2026 | [GGUF 4B de terceiro](https://huggingface.co/mradermacher/GUI-Owl-1.5-4B-Instruct-GGUF): Q4_K_M 2,497 GB + projetor Q8_0 0,454 GB | Especialista em desktop, browser e mobile, baseado em Qwen3-VL. O 2B já aparece no inventário local. Avaliar visualmente com o contrato apropriado antes de descartá-lo pelo teste textual. |

A soma dos arquivos do LFM é ~2,26 GB; Agents-A1 ~3,38 GB; Gemma E2B com
projetor F16 ~4,09 GB; GUI-Owl 4B com Q8 ~2,95 GB. Essas somas só ajudam a
priorizar downloads. Não demonstram que o conjunto cabe no orçamento do produto.
O [GGUF de ggml-org para Gemma E2B](https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF)
também oferece projetor Q8_0 de ~0,557 GB; usar pesos/projetor correspondentes
e conferir resultados, sem presumir equivalência de qualquer combinação.

Eu retiraria Phi-4-mini, Gemma 3 e o Fable V2 da próxima rodada prioritária,
preservando seus resultados históricos. Manteria Qwen3.5-4B e Qwen3-VL-4B
como controles. Qwen3.5-2B merece uma comparação sem thinking antes de ser
descartado: nesta rodada pequena, gastou mais tokens raciocinando que o 4B.

## Bonsai e quantização extrema

O mais recente encontrado foi
[Ternary Bonsai 2 27B](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf),
lançado em setembro/2026 e derivado de Qwen3.8-27B. Seu menor arquivo PTQ1_0
tem 5,947 GB, mais 0,629 GB de projetor Q8_0 para visão: ~6,58 GB antes
de cache/buffers/desktop. A placa de 6144 MiB tem ~6,44 GB decimais.
Não é candidato para execução visual integral em GPU de 6 GB nesse arranjo.
Offload parcial é um diagnóstico separado, sem aprovação do gate.

O [Bonsai 27B binário anterior](https://huggingface.co/prism-ml/Bonsai-27B-gguf),
baseado em Qwen3.6-27B, é mais plausível para um spike: Q1_0 3,803 GB +
projetor Q8_0 0,629 GB. Contudo, o próprio publicador mede pico textual de
5,2 GB em 4K e 5,6 GB em 10K sem compressão de KV; imagem e desktop somam
ao orçamento. Não afirmar que cabe só pelos ~4,43 GB de arquivos.
Começar sem drafter especulativo, que acrescentaria ~1,79 GB. É um candidato
experimental condicionado à medição de memória e latência, não primeira escolha.

Para planner textual, há
[Bonsai 8B Q1_0](https://huggingface.co/prism-ml/Bonsai-8B-gguf) (~1,159 GB)
e [Ternary Bonsai 8B](https://huggingface.co/prism-ml/Ternary-Bonsai-8B-gguf)
(~2,182 GB). São baseados em Qwen3-8B, sem visão. Todos exigem conferir os
formatos/kernels do [fork PrismML](https://github.com/PrismML-Eng/Bonsai-demo);
não presumir que o runtime comum do projeto ou LM Studio os carrega corretamente.
“Bonsai Image 4B” é
[geração de imagens](https://prismml.com/news/bonsai-image-4b), outra tarefa.

## Alternativas que merecem uma segunda rodada

- [LFM2.5-2.6B textual](https://huggingface.co/LiquidAI/LFM2.5-2.6B): Q4_K_M
  oficial ~1,674 GB, foco agentic e português entre os idiomas. O card diz
  que sempre raciocina; não propor `thinking off` como configuração suportada.
  A variante VL deve ser avaliada separadamente.
- [Nanbeige4.2-3B](https://huggingface.co/Nanbeige/Nanbeige4.2-3B), julho/2026:
  modelo textual pós-treinado para agentes, com thinking configurável e
  Looped Transformer. Reutilizar camadas economiza pesos, mas não significa
  custo computacional de um 3B comum. Exige runtime compatível com a arquitetura;
  não é VLM. GGUF de terceiros existe, sem validação local nesta pesquisa.
- [Empero Qwen3.8-4B-Distill](https://huggingface.co/empero-ai/Qwen3.8-4B-Distill):
  fine-tune comunitário de Qwen3.5-4B, diferente de lançamento oficial Qwen.
  O autor informa treinamento textual e visão não avaliada; o GGUF pesquisado
  não lista projetor. Candidato textual secundário, com risco de reasoning longo.
- [GELab-Zero-4B-preview](https://huggingface.co/stepfun-ai/GELab-Zero-4B-preview):
  especialista GUI sobre Qwen3-VL-4B, anterior a 2026, com
  [guia de exportação GGUF/projetor](https://github.com/stepfun-ai/gelab-zero).
  Alternativa ao GUI-Owl se o primeiro especialista não passar a rodada visual.
- [Ministral 3 3B Instruct](https://huggingface.co/mistralai/Ministral-3-3B-Instruct-2512):
  alternativa geral com visão de outra família, lançada em dezembro/2025.
  Considerar após os candidatos prioritários para limitar o custo da bateria.
- [Orchard-GUI/OpenWebRL](https://github.com/microsoft/Orchard-Agentic):
  pesquisa de pós-treinamento sobre Qwen3-VL-4B; o repositório consultado
  divulga receitas/dados/resultados, mas não confirmou nesta leitura um
  checkpoint pronto correspondente para download. Não tratar paper como modelo
  imediatamente instalável.

## Próximos testes propostos

1. Preservar contrato/casos para comparar thinking ligado/desligado; verificar
   tokens reais, não apenas flags. Separar uma eventual comparação com amostragem
   recomendada pelo autor da ablação que muda somente thinking.
2. Fazer smoke de carga/template/schema e depois 32 cenas × 3 para os candidatos
   prioritários. Tratar incompatibilidade de runtime como infraestrutura,
   sem classificar como baixa qualidade do modelo.
3. Testar imagens sanitizadas contrafactuais: valores/preços alterados, texto
   pequeno, modal, UIA vazia, nomes duplicados e diferentes DPI/resoluções.
   Separar leitura, escolha da ação e localização do alvo. Confirmar projetor.
4. Medir tempo até decisão válida, pico de VRAM e offload efetivo na RTX 2060;
   só então avançar finalistas para E2E Windows Sandbox conforme o plano vigente.

Nenhum peso novo foi baixado por esta pesquisa. Não houve atuação GUI. Uma
nova recomendação não muda os gates, as regras de segurança ou o default.
