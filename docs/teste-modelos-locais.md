# Comparação automatizada de modelos locais

## Execução automática com LM Studio

Na mesma máquina onde o LM Studio está instalado, execute:

```powershell
# Inicia/reutiliza o servidor e lista model keys dos arquivos baixados
uv run python -m evals.model_bench --lmstudio --list

# Smoke: todos os arquivos cujo model key contém minicpm
uv run python -m evals.model_bench --lmstudio --limit 2 --reps 1

# Rodada completa: todos os MiniCPM baixados, sequencialmente
uv run python -m evals.model_bench --lmstudio

# Outra família, ou arquivos exatos retornados pela listagem
uv run python -m evals.model_bench --lmstudio --match qwen
uv run python -m evals.model_bench --lmstudio --model "MODEL_KEY_EXATO" --model "OUTRO_MODEL_KEY"
```

O modo `--lmstudio` usa a CLI oficial `lms`, encontrada no PATH ou em
`~/.lmstudio/bin/`. Se necessário, informe `--lms-path "C:\caminho\lms.exe"`.
Requer uma versão com `daemon up`, `ls --json`, `load --estimate-only` e
`ps --json`; não instala nem atualiza o LM Studio. Em versões incompatíveis,
mostra o erro da CLI. Execute no Windows quando o LM Studio estiver no Windows,
e não no WSL. A instalação deve ter runtime e modelos disponíveis previamente.

Se a porta não responder, inicia o daemon e o servidor em localhost; um servidor
existente é reutilizado. O servidor permanece disponível ao terminar.
**Este modo descarrega todos os modelos residentes antes de cada teste** para
isolar memória e carrega somente o arquivo selecionado. Não o use simultaneamente
com chats ou outros clientes do LM Studio. Descarrega o modelo do teste ao terminar,
inclusive em interrupção. Não restaura os modelos que estavam carregados antes.

Configuração inicial comum: contexto **8192**, `max_tokens=2048`, temperatura
**0,1**, thinking **native**, três repetições e formatos **schema + prompt**.
Offload padrão **auto**: omite o override da CLI e deixa o LM Studio determinar
GPU conforme seus guardrails. `--gpu max` solicita todas as camadas; `--gpu off`
permite uma comparação em CPU. Não altera guardrails ou muda contexto quando
um modelo falha. `--context` permite uma rodada explicitamente diferente.
Os parâmetros de batch, threads, cache e flash attention permanecem sob os
padrões do runtime: não existe uma configuração universal ótima sem medir hardware.
As chamadas de inferência continuam sequenciais.

Cada modelo gera seu próprio relatório em `model-00/`, `model-01/`, etc.
`comparativo.md` apresenta o conjunto, e `automation.json` registra inventário,
configuração solicitada, estimativas, snapshot dos modelos carregados, comandos
com stdout/stderr e falhas de infraestrutura. **Estimativas não certificam pico
VRAM ou execução integral em GPU**; `ps` é guardado bruto, sem inventar parâmetros
que o runtime não informar. Uma falha de carregamento aparece no comparativo e o
próximo modelo é tentado. Ctrl+C preserva a rodada parcial e encerra a sequência.
O ZIP final na raiz da rodada reúne os relatórios de todos os modelos.

O filtro seleciona todos os arquivos correspondentes, inclusive quantizações
diferentes. Confira `--lmstudio --list` antes de uma rodada longa; `--model` usa
model keys da CLI, que podem diferir dos IDs da API usados no modo manual abaixo.
A automação segue a [CLI oficial do LM Studio](https://lmstudio.ai/docs/cli/local-models/load).


Use esta bateria para comparar os três MiniCPM baixados e outros modelos
no servidor local do LM Studio ou em outro endpoint OpenAI-compatible.
O comando consulta apenas o modelo: **não clica, digita, abre aplicativos,
executa terminal, captura tela nem baixa pesos**. Pode ser executado no host.
Não depende do runtime Windows do projeto nem importa o loop GUI.

A bateria tem 32 cenas textuais congeladas, três repetições por padrão e
checagem de ações e argumentos. Cobre progresso/memória fornecida no contexto,
foco, teclado, Unicode, alvos, ambiguidade, leitura, arquivos, capacidades,
conclusão e instruções maliciosas em observações. Há pares com pedidos iguais
e estados diferentes. As respostas esperadas não são enviadas ao modelo.

São probes de desenvolvimento, não execução end-to-end, holdout, benchmark
de visão ou teste do desktop. A memória é fornecida em snapshots preparados;
não é uma trajetória de ações executadas. A interpretação de perguntas e
instruções de localização é parcial, baseada nas classes/argumentos esperados;
inspecionar as respostas originais antes de decidir um vencedor.

## Preparar o LM Studio

Na aba Developer, ative o servidor local. O endereço padrão usado aqui é
`http://127.0.0.1:1234/v1`; ajuste `--url` se sua porta for outra.
[Servidor local](https://lmstudio.ai/docs/developer/core/server),
[API compatível](https://lmstudio.ai/docs/developer/openai-compat).

Carregue um modelo por vez. Para comparar em 6 GB, evite manter outros
modelos residentes. Use a mesma resolução de contexto, backend e condições
de desktop entre os candidatos; registre diferenças de quantização.
Contexto de 8K é um ponto de partida para os comandos abaixo; não valida
memória/latência por si só. GPU offload precisa estar realmente ativo se
esse for o hardware comparado.

Os nomes da captura são rótulos da interface, não necessariamente IDs da
API. Consulte os IDs reais primeiro, na raiz do projeto:

```powershell
uv run python -m evals.model_bench --list
```

Copie o `id` correspondente ao modelo. Se o servidor exigir autenticação,
esta versão do runner não envia token; use o servidor local sem esse
requisito para o experimento. O runner recusa URLs fora de loopback.

## Fazer um smoke antes da bateria completa

Substitua `ID_EXATO_DA_API` pelo ID retornado em `--list`:

```powershell
uv run python -m evals.model_bench --model "ID_EXATO_DA_API" --limit 2 --reps 1
```

Esse smoke faz uma chamada de aquecimento e duas decisões. Confirma
conexão, identidade anunciada e formato; não classifica o modelo.
Se houver HTTP 400 de schema, registre o erro e rode a variante `--format prompt`.
O programa não muda formato ou modelo silenciosamente para obter sucesso.

## Rodar a comparação completa

Para cada modelo, execute com seu ID e registre hardware e quantização:

```powershell
uv run python -m evals.model_bench --model "ID_EXATO_DA_API" --reps 3 --format both --thinking native --max-tokens 2048 --notes "GPU: informe; VRAM: informe; GGUF/quantizacao: informe; contexto: 8192; offload: informe; versao LM Studio: informe"
```

`both` mede dois contratos separadamente: JSON restrito pelo servidor
(`schema`) e JSON pedido somente no prompt (`prompt`). São 192 decisões
avaliadas por modelo, além de dois aquecimentos. Para uma primeira rodada
mais curta, use `--format schema`: 96 decisões mais um aquecimento.
A saída informa o progresso de cada chamada; não há inferências concorrentes.

Repita para os três rótulos da captura:

| Rótulo baixado | Identidade informada na captura |
| --- | --- |
| minicpm5-1b-claude-opus-fable5-thinking | Fine-tune comunitário de GnLOLot |
| minicpm5-1b-claude-opus-fable5-v2-thinking | Segunda versão de GnLOLot |
| minicpm5-2b | Modelo de openbmb |

O card da V2 descreve um modelo textual baseado em MiniCPM5-1B com
template thinking e chamadas de tools em XML. O nome com Claude não indica
que seja um checkpoint Claude. Esta bateria testa o contrato JSON do projeto,
não a qualidade de seu protocolo XML nativo.
[Card do publicador](https://huggingface.co/GnLOLot/MiniCPM5-1B-Claude-Opus-Fable5-V2-Thinking).

É possível repetir `--model` para vários IDs na mesma execução, mas o
runner não descarrega/carrega modelos. Só use isso se já configurou a gestão
de memória do servidor para manter um modelo residente por vez.

## Thinking e orçamento de saída

O padrão `--thinking native` preserva o template do servidor sem enviar
override. Isso evita desligar inadvertidamente o raciocínio dos fine-tunes.
O prompt pede uma decisão final JSON; se o servidor separar reasoning, ele
fica preservado na resposta bruta. Um bloco `<think>` fechado antes do JSON
também é aceito e marcado como formato tolerante, distinto de JSON puro.

`--thinking on` ou `off` envia `chat_template_kwargs.enable_thinking`.
É uma solicitação; o servidor/modelo pode ignorá-la. Não interpretar o flag
como comprovação de que thinking foi ativado/desativado.

`--max-tokens 2048` é o orçamento comum inicial, não uma afirmação de que
todo modelo thinking consegue concluir nele. `finish_reason=length` conta
como truncamento, inclusive quando há JSON aparentemente válido. Se houver
muitos truncamentos, compare TODOS os modelos da rodada com `--max-tokens 4096`
em uma execução nova e registre contexto suficiente. Não aumentar orçamento
só para o favorito depois de ver os resultados.

Temperatura padrão: 0,1; pode ajustar `--temperature`, mantendo igualdade na
rodada. Repetições testam estabilidade e latência, mas não são amostras
independentes de generalização. `--timeout` é por requisição, padrão 180 s;
timeouts entram como falhas e não são reexecutados automaticamente.

## Relatório para a próxima análise

A execução cria um diretório novo em `runs/model-bench-.../` e imprime
o caminho absoluto para `resultado.zip`. Esse ZIP contém:

- `relatorio.md`: tabela de acertos, formato e latências por modelo/contrato.
- `summary.json`: parâmetros, hashes, sistema, aquecimentos, erros e métricas.
- `rows.jsonl`: decisão completa, resposta bruta, reasoning se retornado,
  tokens/finish_reason se fornecidos pelo servidor, erros e tempo por tentativa.
- `cases.json`: snapshots e gabaritos exatos da rodada para reprodução.

Envie os ZIPs dos três modelos na próxima chamada. Os dados são sintéticos;
o programa não inclui screenshot, cookies, histórico real ou arquivo pessoal.
`runs/` permanece gitignored. `--out` aceita outro diretório novo e recusa
sobrescrever uma execução existente. Ctrl+C preserva relatório parcial com
tentativas faltantes explícitas.

Todas as tentativas entram no denominador. Falhas de HTTP, formato,
truncamento e semântica aparecem separadamente. Latência inclui erros e
timeouts, mas não o warmup. Cold start e aquecimentos ficam registrados
separadamente; um warmup não garante ausência de carregamento posterior.
Este runner não mede pico de VRAM, GPU offload ou TTFT; não certificar 6 GB
com suas taxas de acerto. `--notes` registra as condições informadas.

## Modelos adicionais para baixar

Minha prioridade é comparar duas escalas de VLM geral e uma referência
visual do projeto. Comece pelas três primeiras linhas. Q4_K_M é a escolha
inicial sugerida para comparação em 6 GB; o pico depende também de contexto,
visão, projetor e runtime. Não é uma garantia de caber ou ser rápido.

| Prioridade | Modelo | Download e finalidade |
| --- | --- | --- |
| 1 | Qwen3.5-2B | [GGUF Unsloth](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF); candidato geral pequeno de texto e visão |
| 2 | Qwen3.5-4B | [GGUF Unsloth](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF); comparar ganho de capacidade com custo de memória/tempo |
| 3 | Qwen3-VL-2B-Instruct | [GGUF Unsloth](https://huggingface.co/unsloth/Qwen3-VL-2B-Instruct-GGUF); referência U1 do projeto |
| 4 | GUI-Owl-1.5-2B-Instruct | [Checkpoint do publicador](https://huggingface.co/mPLUG/GUI-Owl-1.5-2B-Instruct); especialista GUI, com conversão/runtime a validar |

Os GGUFs Unsloth são conversões de terceiro; os checkpoints originais
Qwen são publicados em [Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B),
[Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) e
[Qwen3-VL-2B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct).
Para visão, preserve também o `mmproj` correspondente ao checkpoint e à
revisão. Não confundir disponibilizar pesos com suporte validado no LM Studio.

Todos podem participar da avaliação textual se o servidor os expuser como
chat com o contrato escolhido. **Esta rodada não envia imagens a nenhum
deles.** Uma reprovação de especialista visual nesta rodada não mede seu
grounding; o teste seguinte deve fornecer imagens congeladas por cena,
medir leitura/localização e só depois executar os finalistas no Sandbox.

## Validar o runner sem modelos

```powershell
uv run python -m unittest discover -s tests -p test_model_bench.py
uv run ruff check
```

Os testes usam HTTP simulado e verificam que erros não desaparecem do
denominador, argumentos errados não passam por tipo correto, JSON truncado
falha, gabaritos não entram no prompt, diretórios não são sobrescritos e
o ZIP contém as evidências necessárias. Não exigem GUI, GPU ou servidor.
