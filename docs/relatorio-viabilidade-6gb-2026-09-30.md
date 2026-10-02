# Viabilidade do Taskpilot com modelos locais em 6 GB de VRAM

Data: 30/09/2026. Base analisada: commit `d150ea4`, árvore inicialmente limpa.
Este relatório avalia a proposta; não altera a arquitetura, o perfil B1 nem
aprova etapas do plano vigente de 20/09.

**Parecer: vale continuar com um experimento delimitado. Um assistente local
para tarefas curtas de navegador e desktop é uma hipótese tecnicamente
plausível em uma GPU de 6 GB. Um operador geral, confiável e autônomo para
qualquer tarefa de computador ainda não está demonstrado pelo projeto ou
pelas evidências externas examinadas.**

O próximo investimento deve medir capacidade dos modelos e corrigir as
fronteiras do ciclo de execução. Multiplicar skills ou reescrever Python
agora teria pouco efeito sobre os gargalos identificados.

## Escopo e evidências da análise

Foram examinados o plano vigente, o relatório parcial R0–R3, configuração,
runtime, planner, adaptadores visuais, observação, UIA, executor, memória,
verificação, skills, avaliação e testes. Fontes externas são documentação
dos próprios projetos e model cards dos publicadores, consultadas em 30/09.

Nesta sessão foram executados lint, a suíte oficial e pequenas reproduções
com funções puras e dados sintéticos. Não foram executados comandos do agente
com controle físico, baixados pesos ou realizados benchmarks de GPU. Os
resultados históricos de modelos reais são os registrados no repositório;
não foram repetidos aqui.

| Validação nesta sessão | Resultado e limite |
| --- | --- |
| `uv run ruff check` | Passou |
| `uv run python -m unittest discover -s tests` | Reportou 204 testes, 49 erros e 1 skip; não ficou verde |
| Ambiente da suíte | Linux, CPython 3.12.14; erros de importação da cadeia PyAutoGUI/MouseInfo/Xlib, incluindo `FamilyServerInterpreted` ausente |
| Reproduções puras | Confirmaram problemas no denominador dos probes, confirmação de efeitos e validação de diretório CLI |
| Windows Sandbox e GPU física de 6 GB | Não avaliados nesta sessão |

A contagem de 204 inclui falhas de carregamento de módulos e não equivale
à execução completa dos 286 testes históricos. A falha neste Linux revela
acoplamento da suíte offline às dependências de GUI; não demonstra regressão
no Windows nem permite reafirmar que a versão atual passa nele.

## O que já pode ser reaproveitado

O projeto tem uma base útil: captura local, input Unicode, cliques esquerdo,
direito, duplo e central, movimento, arraste, teclado, scroll, cancelamento,
UIA, contratos, estado da tarefa, logs, perfis de modelos e scripts de teste
descartável. A interface opcional e o ditado também podem ser preservados.

Há quatro skills com catálogo e carregamento sob demanda. O loop já recebe
memória e resultado anterior e distingue ação, percepção, pergunta, skill,
sequência e conclusão. Essas integrações são avanços concretos, embora parte
das garantias documentadas continue mais forte que o caminho efetivamente
executado.

Os resultados históricos são estreitos: na cena de Chrome com nova aba já
criada, B1/MiniCPM5-2B repetiu `ctrl+t` em 3/3 decisões; U1/Qwen3-VL-2B
escolheu `ctrl+l` em 3/3. B1 levou aproximadamente 0,7–0,8 s aquecido;
U1 levou aproximadamente 38 s em CPU. Isso separa uma falha de decisão do
B1 de uma limitação de runtime do U1, sem provar sucesso de tarefa ou
inviabilidade em GPU. Fonte: relatório parcial R0–R3 do repositório.

## Lacunas que afetam diretamente a viabilidade

### Runtime e identidade dos modelos

O runtime próprio baixa explicitamente um binário Windows CPU-only e usa
`ngl=0`. Portanto, a experiência automática atual não explora o hardware
alvo. Aumentar `ngl` não transforma esse binário em CUDA ou Vulkan.
O detector `gpu_status` também deduz CPU-only pelo nome do caminho, que
contém `bin`; isso não substitui a inspeção do backend realmente carregado.

Os perfis declarados em `config.PROFILES` não são todos produtos executáveis.
`planner_gguf_for` seleciona arquivos por substrings, com fallback para
MiniCPM; `ensure_assets` tem mapeamentos fixos para MiniCPM/Vocaela e
Qwen3-VL-2B. O perfil U2/Qwen3.5-4B não tem provisionamento próprio de pesos
e projetor. G1/GUI-Owl cai no adaptador Vocaela porque seu nome não contém
`qwen`. Um endpoint externo correto pode permitir experimentos específicos,
mas a lista de perfis não comprova compatibilidade automática.

Recomendação: registro explícito por checkpoint, revisão, hashes, licença,
template, protocolo de ações, projetor e backend. Modelo desconhecido deve
produzir erro. Logs devem comprovar offload real e memória do processo.

### O perfil unificado ainda faz duas etapas de inferência visual

`QwenVLPlanner` recebe screenshot, mas utiliza o mesmo `PlannerDecision`
e schema que proíbem coordenadas. O prompt unificado herda regras como
"não vê a tela" e "nunca coordenadas", enquanto adiciona orientações sobre
coordenadas do frame. Há uma contradição entre papel, prompt e contrato.

Quando a decisão é `visual_action`, o loop captura outra imagem e chama
grounding novamente. U1 unifica o processo e os pesos, mas não a decisão
com localização. O `QwenGroundingAdapter` produz apenas clique esquerdo:
não representa diretamente arraste, clique direito ou scroll. Esses inputs
existem no executor e no caminho Vocaela, mas não são capacidades completas
do caminho visual Qwen atual.

Recomendação: contrato específico para VLM, permitindo escolher a primitiva
e fornecer alvo vinculado ao frame na mesma chamada. No perfil textual,
manter a proibição de coordenadas e retornar percepção ao planner como dados.

### Observação e ferramentas não estão inteiramente conectadas

O caminho principal usa a tupla legada de `active_window_snapshot` e nomes
compactados, não todo o contrato `Observation` com identidade e referências.
`_resolve_uia` devolve um clique por coordenadas sem `element_ref`.
`actions.check_preconditions`, `schemas.validate_decision` e o lock de
executor de `safety` existem, mas não são chamados pelo caminho principal
analisado. A interface pode limitar concorrência em seu próprio fluxo;
isso não estabelece exclusividade do motor entre todas as entradas.

A guarda visual anterior ao clique compara título da janela. Ela não
detecta necessariamente um modal, elemento movido ou troca de conteúdo com
o mesmo título. O timeout de UIA é conferido entre chamadas; uma chamada
COM bloqueada pode ultrapassá-lo.

OCR já tem caminho opcional e erro honesto, mas o histórico registra ausência
de backend. No perfil duplo, a visão orientada a ações não equivale a uma
ferramenta de leitura com evidências. Ler informação e localizar onde clicar
precisam ser avaliados separadamente.

Recomendação: observação única consumida pelo modelo e executor, referências
de elementos, HWND/PID, foco e validade do frame; validação imediatamente
antes do envio; trabalhador isolado para providers bloqueantes; OCR e leitura
visual efetivamente instalados e medidos.

### Evidência existente ainda pode confirmar o efeito errado

Há proteção útil contra `done` sem referências, mas o verificador confere
pertencimento à lista de evidências. Ele não exige cobertura de todos os
requisitos do pedido. Em `state.apply_update`, uma referência válida pode
acompanhar várias conclusões propostas, sem vínculo individual de suporte.
As evidências usadas pelo loop são strings; proveniência e escopo semântico
ainda são limitados.

Reproduções puras desta sessão mostraram:

| Entrada sintética | Resultado atual | Problema |
| --- | --- | --- |
| `hotkey:ctrl+t`, nota de janela Chrome mudando para outro app | Nova aba confirmada | Qualquer mudança de título com o formato esperado pode ser aceita |
| Clique, UIA antes com `Button:Continuar`, depois vazia | Modal dispensado confirmado | Falta de observação pode ser tratada como desaparecimento do modal |
| `done` com referência existente a "aba criada" | Referências aceitas | Essa checagem não demonstra que uma pesquisa ou salvamento também terminou |

`verify` usa `wait_for_condition(lambda: False)`: sempre espera o prazo e
só depois observa. A espera é cancelável, mas ainda não aguarda uma condição
real de efeito. O resultado é mais latência e confirmação frágil.

Recomendação: distinguir envio, efeito observado e requisito concluído;
recusar inferência de ausência quando UIA falha; vincular cada requisito
à evidência correspondente; responder a consultas com o conteúdo observado
e sua fonte. Se o objetivo já estiver cumprido inicialmente, admitir evidência
de leitura validada sem exigir uma ação artificial.

### O runner pode aprovar uma bateria ruim

`evals.probe_battery.summarize` retira linhas com `error_kind` do denominador
de validade e formato. Com **1 decisão correta e 9 erros de JSON**, a
reprodução retornou `pass_rate=1.0`, `format_valid_rate=1.0` e
`gate_pass=True`. O gate também não exige cobertura mínima das 30 cenas.

`smoke_distinguish_images` retorna `ok=True` mesmo quando seu próprio
indicador registra respostas idênticas. Resposta idêntica não prova por si
só que a imagem foi ignorada, mas o smoke atual tampouco demonstra que
ela foi usada. Além disso, o planner unificado captura a tela corrente ao
receber fixtures textuais: a bateria não fornece uma imagem correspondente
e congelada para cada cena.

Recomendação: denominador explícito de todas as tentativas; erros de formato
como falhas; problemas de infraestrutura como resultado separado que impede
aprovação de bateria incompleta; mínimo de cenas e repetições; imagens por
fixture e pares contrafactuais com expectativas verificáveis.

### Terminal e skills CLI ainda são um piloto limitado

Não existe uma ferramenta geral de terminal no contrato atual. A whitelist
de abertura cobre navegadores, Notepad e calculadora. A skill Blender executa
receitas delimitadas, mas seus scripts criam texto com extensão `.blend`
e PNG mínimo; não criam cenas Blender reais.

O confinamento de `cwd` aceita prefixo `runs`. A entrada
`runs/../../fora` passa nessa condição e resolve fora do repositório.
Essa resolução foi reproduzida sem criar arquivos. O cancelamento da skill
é consultado após `communicate`, não durante toda a execução do subprocesso.

Recomendação: corrigir confinamento pelo caminho resolvido e cancelamento
do processo antes de ampliar CLI. Uma skill não torna um script confiável.
Terminal deve ter sessão, diretório, capacidades, limites e resultados
próprios. Digitar comandos num terminal por mouse/teclado também precisa
dessas regras; a forma de entrada não reduz o poder do comando.

## Organização proposta de tools e skills

A premissa do usuário é adequada: o modelo escolhe como usar capacidades
genéricas e observa o resultado. Eu separaria os dois conceitos:

- **Tool:** implementação executável de captura, clique, arraste, digitação,
  tecla, leitura ou comando permitido; contrato pequeno e validável.
- **Skill:** instruções e recursos para usar um conjunto de tools com boas
  precondições, percepção e verificação, carregados quando necessários.

É possível ter uma skill para cada primitiva, mas isso acrescenta descoberta
e contexto para operações muito pequenas. Minha recomendação é agrupar em
percepção, mouse, teclado, navegador e terminal, mantendo cada operação como
tool. Skills de domínio podem explicar um aplicativo ou formato sem fixar
receitas para os casos de teste.

| Capacidade | Contrato proposto |
| --- | --- |
| Percepção | Capturar janela/monitor, ler UIA/OCR e descrever uma região; retornar frame e proveniência |
| Mouse | Mover, clicar com botão e contagem, arrastar entre pontos do mesmo frame, rolar; retornar enviado/incerto/não enviado |
| Teclado | Digitar Unicode, pressionar teclas e combinações; validar foco e liberar modificadores |
| Navegador | Observar aba e conteúdo visível, localizar controles e executar as primitivas; preservar vínculo com aba/frame |
| Terminal | Abrir sessão ou executar capacidade explícita, com diretório delimitado, prazo, cancelamento e stdout/stderr |
| Conclusão | Retornar resultado, requisitos cobertos e evidências; distinguir parcial, bloqueado e sucesso |

O ciclo recomendado continua sendo observar, decidir, validar, executar,
reobservar e atualizar memória. Python aplica invariantes e mede; o modelo
escolhe estratégia e alvos. Recuperação deve ser escolhida a partir de efeito
real, em vez de acrescentar regras por loja ou produto.

## Modelos leves e orçamento de memória

Há candidatos reais abaixo de 7B. A shortlist proposta é manter U1 como
referência e experimentar apenas mais dois checkpoints: um especialista GUI
de 2B e um VLM geral de 4B. É uma recomendação de teste, não troca de default.

| Candidato | Papel proposto e limite |
| --- | --- |
| Qwen3-VL-2B-Instruct | Referência U1 já integrada; medir corretamente em GPU e com contrato visual próprio. O card oficial descreve capacidades de agente visual, mas não valida este produto. [Model card](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct) |
| GUI-Owl-1.5-2B-Instruct | Candidato especializado para decidir e agir com visão; criar adaptador próprio, testar PT-BR e conversão quantizada. [Model card](https://huggingface.co/mPLUG/GUI-Owl-1.5-2B-Instruct) |
| Qwen3.5-4B | Candidato geral de texto e visão; provisionamento e adaptação novos, com medição de memória. [Model card](https://huggingface.co/Qwen/Qwen3.5-4B) |
| B1 com Vocaela | Baseline de comparação, preservado; o probe negativo impede presumir que o planner melhora apenas adicionando mais skills |

O publicador do GUI-Owl informa 43,5 em OSWorld-Verified para 2B e 48,2
para 4B; o card Qwen3.5-4B informa 35,6 nesse benchmark. São resultados
publicados em condições próprias, sem validação nesta sessão de equivalência
entre protocolos, quantização, português ou hardware. Eles demonstram
capacidade relevante e limitações substanciais, sem provar 90% de sucesso
geral. O exemplo de deploy GUI-Owl foi validado pelo publicador em A100 de
96 GB; não é uma medição de requisito mínimo nem uma demonstração em 6 GB.
Fontes: [GUI-Owl](https://huggingface.co/mPLUG/GUI-Owl-1.5-2B-Instruct),
[Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B).

Para memória, `parâmetros × bits / 8` fornece apenas uma estimativa nominal
do armazenamento de pesos, usando GB decimais:

| Escala | Pesos nominais em 4 bits | Interpretação para GPU de 6 GB |
| --- | ---: | --- |
| 2B | 1,0 GB | Boa margem inicial para um experimento; ainda há visão, cache e buffers |
| 4B | 2,0 GB | Hipótese razoável com um processo, contexto e resolução controlados |
| 7B | 3,5 GB | Margem mais apertada; só admitir após medição completa |

Esses valores não são tamanho exato do GGUF nem pico de VRAM. Quantização
mista, escalas, encoder/projetor visual, KV cache, estados de outras
arquiteturas, buffers, contexto e aplicações abertas precisam entrar na
medição. Algumas partes podem estar incluídas na contagem publicada de
parâmetros; evitar contá-las duas vezes.

Manter como gate inicial o orçamento do plano: aproximadamente 5 GB de
dedicada incremental para o agente e 1 GB de reserva ao desktop, ajustando
a reserva se a carga real exigir. Uma GPU com a mesma VRAM pode ter latência
distinta; largura de banda, driver, resolução e backend importam. A meta de
6 GB não promete funcionamento equivalente em placas com menos memória.

Eu começaria com um VLM unificado, uma geração ativa, screenshot corrente
e recortes sob demanda. UIA/OCR reduzem a dependência de pixels para texto
pequeno. STT fica inicialmente em CPU. O perfil duplo continua um competidor
legítimo se medir melhor qualidade e latência; duas redes caberem na memória
não é motivo suficiente para adotá-las.

O llama.cpp já é uma infraestrutura nativa C/C++ e documenta servidor
multimodal, projetor e offload. A versão e os artefatos exatos ainda precisam
ser validados para cada candidato. [Runtime](https://github.com/ggml-org/llama.cpp),
[documentação multimodal](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md).

## Extensão de navegador

Uma extensão pode ajudar especialmente porque o histórico do projeto mostra
UIA do Chrome expondo apenas a moldura. O benefício esperado é melhor
observação de aba, foco, texto visível e posição dos controles, reduzindo
adivinhação visual. Isso não substitui planejamento e confirmação.

Minha proposta inicial preserva a premissa do projeto: extensão como sensor,
com texto renderizado, rótulos, bounds, viewport, zoom e estado da aba;
Python executa cliques e teclado reais. Filtrar conteúdo oculto e elementos
fora da região observada e tratar todo conteúdo web como dados não confiáveis.
Canvas e interfaces sem semântica continuam precisando de screenshot.

As APIs do Chrome oferecem captura de aba e integração com content scripts;
Native Messaging conecta uma extensão a um processo local. Essa ponte pode
ser pequena e não exige migrar todo o motor para JavaScript.
[Tabs API](https://developer.chrome.com/docs/extensions/reference/api/tabs),
[Native Messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging).

A API `debugger` dá acesso a domínios CDP como Accessibility, DOM e Input.
Ela pode melhorar instrumentação, mas CDP para atuação ou edição semântica
é outro modo de produto, incompatível com o contrato GUI vigente se usado
como atalho oculto. Caso esse modo seja escolhido futuramente, declarar e
medir separadamente. [Debugger API](https://developer.chrome.com/docs/extensions/reference/api/debugger).

Claude in Chrome confirma que a abordagem de extensão é real; a documentação
da Anthropic menciona leitura de DOM e controles de permissão. Isso não
permite inferir que o mesmo desempenho venha de modelos locais pequenos.
[Publicação da Anthropic](https://claude.com/blog/claude-for-chrome).

A extensão tem custo: instalação, permissões, frames, zoom, páginas especiais
e manutenção. Uma versão sensora é um experimento de escopo menor que um
segundo executor completo. Também não controla aplicativos nativos nem
diálogos externos ao navegador; o executor de desktop permanece necessário.

## Linguagem e bases externas

**Manter Python é a recomendação para esta rodada.** A inferência já acontece
em processo nativo; trocar o orquestrador não reduz automaticamente o custo
de interpretar screenshots ou resolver planejamento. A análise identificou
problemas de integração e avaliação, sem medição que atribua o gargalo ao
interpretador Python.

Extrair gradualmente responsabilidades do `loop.py`, atualmente com 1.949
linhas, tende a facilitar testes e adapters. O núcleo deve poder ser testado
sem importar PyAutoGUI, Xlib ou COM. Isolar observação, política, decisão,
execução e evidência vale mais agora que uma reescrita integral.

TypeScript é adequado para a extensão e pode ser escolhido para UI/eventos
se isso simplificar o produto. Rust ou C++ podem servir depois para um
executor nativo ou componentes de distribuição. Cada migração deve ter
ganho medido em latência, confiabilidade, instalação ou manutenção.

UI-TARS Desktop oferece operadores de computador e navegador e é uma boa
referência para um spike de reaproveitamento. Agent S pode informar desenho
do ciclo e recuperação. A existência desses frameworks não melhora por si
só um modelo de 2B nem demonstra que suas configurações cabem em 6 GB.
[UI-TARS Desktop](https://github.com/bytedance/UI-TARS-desktop),
[Agent S](https://github.com/simular-ai/Agent-S).

## Escopo de produto que considero plausível

Esta classificação é julgamento de engenharia, não resultado medido:

| Escopo | Parecer |
| --- | --- |
| Navegar, preencher poucos campos e extrair informação visível, com supervisão | Bom candidato ao MVP, condicionado aos probes |
| Editar texto curto, salvar e conferir arquivo em apps comuns | Plausível com bons verificadores e fixtures |
| Transferir informação entre dois aplicativos | Plausível, com maior risco de foco e memória |
| Operações CLI delimitadas e verificáveis | Plausível após corrigir e implementar o executor real |
| Tarefas longas, layout novo, muitos modais e recuperação autônoma | Alto risco; não prometer antes de testes próprios |
| Operar qualquer PC/app com confiabilidade semelhante à humana | Evidência insuficiente no alvo de 6 GB |

Acertar ações isoladas não basta. Em uma ilustração com erros independentes
e sem recuperação, 20 passos com 95% de acerto individual dão 35,8% de
sucesso total; com 98%, 66,8%; com 99%, 81,8%. São cálculos ilustrativos,
não previsões de benchmark. Explicam por que observação posterior, redução
de passos e recuperação importam tanto quanto velocidade de geração.

Skills ajudam a usar ferramentas, mas não acrescentam treinamento visual
ou capacidade de raciocínio automaticamente. Se o melhor candidato falhar
nas mesmas classes após uma rodada delimitada de adapters/contexto, avaliar
redução de escopo ou treino específico com trajetórias e rejeições. Fine-tuning
é um investimento separado; os 6 GB de inferência não garantem treino útil
na mesma placa. Evitar substituir generalização por receitas por site.

## Experimento recomendado antes de expandir o produto

Esta proposta detalha prioridades dentro do plano vigente; não cria um
roadmap concorrente nem aprova R3–R6.

1. **Corrigir a medição e os contratos essenciais.** Denominador e cobertura
   dos probes, imagens por fixture, verificação sem inferir ausência de
   leitura, referências de alvo e integração das precondições. Separar
   testes puros das dependências de plataforma.
2. **Provar runtime GPU.** Build CUDA/Vulkan adequado, checkpoint/projetor
   fixos e logs de offload. Medir pesos, memória incremental e total,
   dedicada/compartilhada, carga do desktop, cold start e inferência aquecida
   em GPU física de 6 GB. Inputs de testes seguem no Sandbox ou ambiente
   dedicado; inferência no hardware alvo não é comprovada por uma VM genérica.
3. **Comparar capacidade.** B1/U1 como referências e no máximo os dois
   candidatos adicionais propostos. Pelo menos 30 cenas ×3, com planejamento,
   leitura, grounding, alvo ausente/duplicado e contrafactuais separados.
   Registrar erros e tentativas vetadas, inclusive seu custo.
4. **Testar a extensão sensora se percepção web for o gargalo.** Comparar
   os mesmos casos com e sem ela, preservando inputs físicos. Fixar
   critérios antes de medir; não oferecer respostas de fixtures ao modelo.
5. **Executar E2E com finalistas.** Primeiro tarefas de desenvolvimento,
   depois aceitação congelada de 20 tarefas ×5 por finalista, distribuídas
   pelas famílias do plano. Checkers independentes conferem resultado;
   ajuda humana, bloqueio e falso sucesso ficam separados.

Preservar os gates propostos no plano: ≥90% de decisões semanticamente
válidas nos probes, ≥99% de formato e zero violações executáveis; na
aceitação, ≥90/100 sucessos autônomos, ≥80% por família e zero falsos
sucessos observados. Medir tempo de tarefa curta p50 ≤60 s e p95 ≤120 s,
decisão visual aquecida p95 ≤8 s e textual ≤5 s, além de memória e reserva.
Esses são objetivos de teste, não números já alcançados.

Reportar intervalos de confiança, carga de desktop, GPU, resolução,
quantização e condições de acessibilidade. Mesmo aprovação em 100 runs
limita-se ao escopo e condições examinados. Se nenhum candidato passar,
registrar qual requisito falhou e decidir entre escopo menor, outra rodada
de treinamento ou hardware diferente, sem promover um resultado insuficiente.

O projeto merece esse teste porque já dispõe de infraestrutura reaproveitável
e há modelos pequenos com capacidade GUI publicada. A evidência que falta
é um ciclo completo que mantenha decisões corretas, efeitos verificáveis e
latência útil na placa alvo. Essa é a próxima decisão de investimento.
