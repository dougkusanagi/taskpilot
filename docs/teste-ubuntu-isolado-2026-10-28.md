# Teste real no Ubuntu sem controlar o desktop de trabalho — 28/10/2026

O teste foi executado em um GNOME headless dedicado. A tarefa **falhou** no limite de quatro
passos. A localização visual separada encontrou o alvo corretamente, mas isso não constitui
sucesso da tarefa nem aprovação do trio de modelos para uso real.

## Isolamento e recursos

- Bus D-Bus privado, socket Wayland `taskpilot-isolated`, PipeWire e diretórios XDG próprios.
- Monitor virtual `Meta-0`, 1280×800; sem `DISPLAY` no ambiente do agente.
- Prioridade nice 10, afinidade a dois CPUs e dois threads por modelo; projetor visual e d1
  na CPU. Nenhum peso novo foi baixado, nenhum modelo do usuário foi encerrado.
- Sem reserva artificial de VRAM; watchdog encerra só processos marcados pelo identificador
  único do teste se a memória disponível cair abaixo de 2 GiB ou o tempo exceder oito minutos.
- O diálogo de autorização foi aprovado por AT-SPI somente no PID do portal pertencente ao
  bus privado. O switch interno do GTK4 possui a ação que não existe no wrapper externo.
- A autorização concedeu teclado e mouse: os RPCs de entrada não foram recusados. O efeito
  desejado no aplicativo não foi comprovado, pois a tarefa terminou na visão geral do GNOME.
- Processos próprios foram encerrados e a GPU voltou à ocupação dos processos preexistentes.
  Nenhum evento foi dirigido ao barramento ou ao monitor da sessão de trabalho.

O isolamento é gráfico e de configurações, não uma VM de segurança: arquivos e a GPU continuam
no mesmo host. Usamos somente aplicativos e arquivos de teste locais. O monitoramento de
recursos reduz a disputa com o uso do PC, mas não mede a percepção de fluidez do usuário.

## Tarefa e resultado

Pedido exato:

> No Editor de Texto já aberto, use o mouse para clicar no botão + (nova aba), depois digite
> exatamente: Olá, Ubuntu!

Configuração privada do teste: Qwen3-4B-Instruct-2507 Q4_K_M com `dynschema,fewshot,plan`,
LFM3-VL-2B QAD-Q4_0 com p2d e teto de 512 tokens, d1-2B Q4_K_M com corte provisório 0,20.
Contexto 4096 no planner/localizador e 1024 no d1; llama.cpp b11954 CUDA. Default B1 do
repositório não foi alterado; os nomes efetivos foram sobrescritos só na configuração do teste.

Resultado do loop: `max_steps`, quatro ações executadas, uma repetição vetada, cinco consultas
ao planner e nenhuma consulta visual. O planner abriu `notepad` (mapeado para Editor de Texto
do GNOME) duas vezes, tentou repetir a abertura e, após o veto, enviou `ctrl+t` duas vezes.
Não escreveu a frase. A captura final mostra documentos vazios na visão geral do GNOME.

O loop não tinha título nem árvore de acessibilidade nesse backend; o diagnóstico informou
isso honestamente. O planner não usou a ação visual para resolver a ausência de observação.
A receita `ctrl+t` também não demonstrou abrir uma aba no Editor de Texto. São lacunas reais
de integração e contexto de plataforma; não há evidência para atribuir tudo aos pesos.

Tempo total: 115,2 s, dos quais cerca de 105,5 s foram a espera pela autorização do portal na
primeira ação de teclado. As consultas ao planner somaram 1,71 s. O tempo total inclui a
preparação manual da autorização e não deve ser usado como latência autônoma da combinação.

## Localização visual separada

Depois da falha, `main.py --locate` recebeu:

> Click the plus (+) New Tab button in the right-hand text editor window.

O localizador retornou `(0.566, 0.284)`, convertido para `(724, 227)` no frame 1280×800.
A inspeção da imagem confirma que o ponto está no botão + da janela direita. O d1 processou
a requisição e permitiu o alvo pelo corte 0,20. O caminho completo de localização levou
7,17 s nesta configuração de baixa prioridade e dois CPUs. Nenhum clique foi executado
pelo modo `--locate`; não se pode inferir que clicar ali abriria a aba enquanto o GNOME
estivesse na visão geral.

## Evidências e reprodução local

- Sessão e logs: `runs/ubuntu-e2e-20261028-162232/`.
- Loop real: `runs/run-20261028-132350/summary.json`, `run.jsonl` e `sft.jsonl`.
- Captura final: `runs/ubuntu-e2e-20261028-162232/after-consent.png`.
- Localização: `runs/ubuntu-e2e-20261028-162232/locate.log` e logs dos servidores.
- Harness e parâmetros exatos copiados para `runs/ubuntu-e2e-20261028-162232/harness/`.

Uma primeira preparação (`runs/ubuntu-e2e-20261028-161956/`) falhou na captura do stream
nativo vinculado ao driver, antes de iniciar modelos. A captura independente do driver e
a captura pelo portal de produção permitiram a segunda execução. O `mapping-id` do stream
não serviu como argumento do driver nativo (retornou `Unknown stream`); isso não foi corrigido
por desativar guardas ou usar input na sessão de trabalho.

Durante o preparo, foi corrigido o caminho do token do portal para respeitar `XDG_CACHE_HOME`.
Antes, o token era escrito em `~/.cache` mesmo com diretórios XDG isolados. A regressão é
coberta por teste sem abrir portal ou executar input.

## Próximo passo

Melhorar a observação real do Ubuntu (título/foco/AT-SPI ou OCR) e o uso de percepção visual
quando a árvore não estiver disponível; então repetir a mesma tarefa isolada. Validar mouse,
foco e digitação com efeito visível antes de ampliar a bateria. Não trocar o default B1 nem
aprovar gates R3–R6 com este resultado.

## Correção após o teste

O bootstrap agora deixa de presumir ausência de aplicativo no backend visual. O prompt
explica que uma árvore vazia não prova que o app está fechado, solicita localização visual
antes de digitar e alerta para não transferir atalhos do navegador para outros apps.
Regressão do prompt e suíte local verificadas; a repetição real isolada continua pendente.

## Repetição após correção do prompt

Execução `runs/ubuntu-e2e-20261028-172116`, loop `run-20261028-142142`.
O limite inotify por usuário foi elevado de 128 para 512 via sysctl (sem persistência).
A captura voltou a funcionar. O planner solicitou localização visual e o loop abriu
novas abas pelo botão +, mas repetiu o clique e não digitou a frase em quatro passos.
A captura final confirma abas novas e documentos vazios. Tarefa continua reprovada:
falta confirmação de efeito no backend visual para avançar à digitação.
Os processos isolados foram encerrados; o desktop de trabalho não recebeu input.

## Integração AT-SPI e novas repetições — 28/10

Foi integrado um leitor AT-SPI passivo, em subprocesso do Python do sistema com timeout,
no barramento explícito da sessão opt-in. `AT_SPI_BUS_ADDRESS` herdado é removido para
que a descoberta use esse barramento. A árvore informa janela ativa, abas e texto/foco;
janela ativa ausente/ambígua, timeout e erro continuam diagnósticos, sem evidência inventada.
A travessia inicial de 20 níveis não alcançava o campo do GTK; a versão final tem limite
80/5000 nós e informa truncamento. Nenhum bound AT-SPI é usado para clicar nesta entrega.
Elementos com bounds ausentes continuam no contexto textual; cliques usam grounding visual.

A confirmação de nova aba exige aumento de elementos `TabItem` antes/depois, e não título
ou pixels. A verificação de texto passa a exigir a frase inteira na leitura do foco, em vez
somente dos primeiros 40 caracteres. O histórico e a memória agora recebem a confirmação.
Foi corrigido também o alvo perdido na escalada `uia_click` → visão: o histórico registrava
`visual "None"`, mesmo quando o alvo conhecido era `New Tab`. Campos de texto sem nome são
preservados no prompt. Observação simples não vira evidência de conclusão.

### Resultados reais

Sessão dedicada: `runs/ubuntu-e2e-20261028-173237/`.

- `run-20261028-143300`: falhou (`stuck`), após reabertura do editor e grounding truncado.
  O portal devolveu a sessão à visão geral; essa execução não prova falha do clique no app.
- Após dispensar a visão geral no preparo, `run-20261028-143408`: quatro cliques, `max_steps`.
  Novas abas confirmadas por AT-SPI, mas nenhuma digitação. Histórico ainda perdia o alvo.
- Com alvo preservado e orientação genérica sobre avançar após efeitos confirmados,
  `run-20261028-143521`: quatro cliques, `max_steps`, nenhuma digitação.
- Com confirmação também descrita em inglês e orientação junto à decisão seguinte,
  `run-20261028-143631`: quatro cliques, `max_steps`, nenhuma digitação.

**Resultado end-to-end: falha.** O efeito de abrir novas abas foi comprovado; a frase não
foi digitada. A leitura independente final (`final-atspi.json`) contou dez abas e texto
vazio. `final.png`, inspecionada visualmente, mostra abas e documento vazio. Não houve
conclusão falsa do loop. Não foi feita a segunda tarefa de variação porque a primeira não
passou. As repetições posteriores reutilizaram a mesma sessão/modelos e acumularam abas;
não são trials independentes de ambiente limpo. A etapa de input do portal foi autorizada
somente pelo PID do portal no barramento privado; nenhuma ação da tarefa foi feita à mão.

O prompt efetivo em `runs/run-20261028-143631/sft.jsonl` contém o alvo, os `TabItem`, a
confirmação e a memória; ainda assim o planner escolhe `uia_click New Tab` novamente.
A integração corrige a falta de observação, mas **não resolve a repetição desse planner**.
Não foi introduzida uma estratégia Python específica para o editor para mascarar a falha.
O d1 continua validando alvo, sem contrato de confirmação visual antes/depois; esse fallback
não foi inventado. Sem AT-SPI, o efeito segue inconclusivo.

### Reprodução e segurança

Os scripts exatos usados estão em `runs/ubuntu-e2e-20261028-173237/harness/`: launcher,
controller, modelos, autorização por PID e retry privado. O launcher inicia bus/Wayland/
PipeWire/XDG privados, nice 10, dois CPUs e watchdog de oito minutos/2 GiB disponíveis.
O retry valida os identificadores privados e tem timeout de 150 s, grupo próprio e cleanup.
A afinidade e prioridade são herdadas pelos modelos. Não houve download de pesos nem
encerramento de modelos alheios. O limite inotify já estava em 512 e não foi alterado.
A primeira tentativa do launcher sem terminal (`173224`) recebeu EOF e foi limpa antes
iniciar avaliação/modelos; a sessão de avaliação usou terminal para manter o controlador.

Use o launcher em terminal, aguarde `ready`, envie `models` e espere `config.json`;
inspecione `isolation.json`, dispense a visão geral apenas no preparo e capture `before`.
Envie `agent`, autorize pelo script restrito ao portal privado se houver diálogo. As linhas
para o controlador são JSON, por exemplo `{"cmd":"models"}`, `{"cmd":"agent"}`,
`{"cmd":"capture","name":"final"}` e `{"cmd":"quit"}`. O retry exige que essa sessão
continue viva e reutiliza sua configuração. Os scripts usam caminhos locais e arquivos
em `/tmp`; copie-os de volta para esses caminhos antes de reproduzir. O launcher/controller
fazem cleanup em `finally`, e `cleanup.json` registra os PIDs próprios encerrados.

Validação local: suíte oficial via uv, 538 testes, zero falhas e um skip; ruff verde.
Os testes novos são passivos, com mocks, sem abrir portais ou controlar desktop.
Nenhum gate Windows/6 GB foi aprovado; default B1 permanece. A VRAM reportada pelo loop
é a ocupação global do host, inclui processos preexistentes e não certifica orçamento.

## Correção da repetição (após os runs acima)

Causa no código: o Task state listava os requisitos sem status. A evidência confirmada
(nova aba) não era ligada ao requisito (1), e o anti-loop aceitava a repetição porque cada
clique tinha efeito novo. Agora uma evidência confirmada cumpre o primeiro requisito aberto
(`state.satisfy_next_requirement`), o resumo mostra `[cumprido/done: E1]` e `[PRÓXIMO/NEXT]`,
e repetir a mesma intenção (sem coordenadas) com requisito aberto vira `last_error` que
indica o próximo requisito (guard 2b em `loop.py`). Vale só com checklist (`plan`).
Limite: a ligação é posicional; uma ação preparatória confirmada pode cumprir o requisito
errado. Suíte local: 542 testes, um skip; ruff verde. **A repetição real isolada ainda não
foi feita**: o end-to-end continua sem aprovação.

## Repetição real após as correções — 28–29/10

Três falhas encontradas em runs reais e corrigidas no código compartilhado:

1. **Repetição do clique:** o guard 2b bloqueou os cliques repetidos
   (`run-20261028-145200`, parada honesta `stuck`). Mesmo assim, o Qwen3-4B
   insistiu no clique. Ablação offline com o mesmo prompt: só o campo `why`
   (feature já existente, opt-in) fez o modelo escolher `type_text` depois da
   confirmação (3 de 4 prompts; 1 escolheu `focus_window`). Nos passos
   anteriores, ele continua clicando no "+". Ligado só na configuração do teste;
   o default do repositório não mudou.
2. **Escalada `uia_click` → visão** enviava o nome cru (`Click Nova aba`). O d1
   dava p=0,19–0,21 (corte 0,20) e recusou 4×. Agora a instrução usa o papel do
   elemento da árvore (`Click the "Nova aba" button`), e o d1 dá p=0,51 no mesmo frame.
3. **Acentos no Wayland:** o Mutter descarta keysyms fora do layout ativo, e
   "Olá" virou "Ol". Fora do ASCII, `type_text` agora usa a entrada Unicode do
   IBus/GTK (Ctrl+Shift+U, hex, espaço).

Sessão `runs/ubuntu-e2e-20261029-100531/` (scripts usados em `harness/`):

- Run principal: aba nova confirmada; depois `focus_window` sem efeito; `max_steps`.
- Retry na mesma sessão: aba nova, `focus_window` desperdiçado, depois
  `type_text`. `final-retry.png` mostra uma aba nova com **exatamente "Olá, Ubuntu!"**.

**Resultado do loop: ainda não aprovado.** Terminou em `max_steps` (limite 4),
sem `done`: a leitura AT-SPI do campo voltou "focused field unreadable", então a
digitação não virou evidência confirmada. O efeito na tela está correto (inspeção
visual), mas o loop não consegue provar isso sozinho. O passo 0 também gasta
um clique enquanto o diálogo do portal está aberto. Não foi verificado se esse
clique teve efeito.

Notas de execução: depois do reboot, o limite inotify voltou a 128 e a captura
funcionou mesmo assim. Uma tentativa (`20261028-181217`) foi parada pelo watchdog
por memória, porque duas sessões antigas ainda estavam vivas. Use um arquivo de
comandos por sessão. O diálogo do portal pode vir em português; o script de
aprovação aceita os dois idiomas. Cleanup confirmado em todas as sessões; nenhum
input chegou ao desktop de trabalho.

Próximos passos: ler o texto do GtkSourceView via AT-SPI (interface Text) para
confirmar a digitação; não gastar passo durante o diálogo do portal; medir
`why` em mais cenas antes de considerá-lo como padrão.
