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
