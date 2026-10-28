# Validação local no Ubuntu — 28/10/2026

O runtime próprio agora seleciona o binário Ubuntu x86_64 e os diagnósticos da CLI respeitam
`--platform wayland`. Isso remove duas barreiras concretas; não certifica o agente completo.
Default B1 preservado. Hardware: RTX 5060 Ti de 16 GB; a reserva de VRAM não
transforma sua latência na de uma placa real de 6 GB. Nenhum evento de entrada foi enviado à sessão de trabalho.

## Runtime e regressões

- Extração do pacote CPU oficial b11954 e `llama-server --version`: passaram.
- `_spawn_one` iniciou Qwen3-4B-Instruct-2507 Q4_K_M em CPU, contexto 512, quatro threads;
  `/v1/chat/completions` respondeu HTTP 200 com texto. Processo encerrado pelo teste.
- Regressões cobrem URLs por plataforma, arquitetura não suportada sem download,
  extração com permissões, bibliotecas CUDA ao lado do servidor e recusa de path traversal.
- `--self-test` e `--locate` antes ignoravam a seleção de plataforma. Agora inicializam o
  backend escolhido e o encerram em sucesso ou falha; `--dry-run` não habilita o backend.
- Suíte local após essas mudanças: 526 testes, zero falhas, um skip; Ruff e diff-check verdes.
- Não há build de distribuição configurado neste repositório.

Brutos locais: `runs/ubuntu-runtime-smoke-20261028/` (resposta e log).
A inferência usa pesos já presentes em `~/llm-bench`; não foram baixados novos modelos.

## Bolt: limite de raciocínio e tempo da tarefa

Mesma bateria de 11 cenários ×3, `dynschema,fewshot,plan`, contexto 4096, IQ3_XXS,
llama.cpp b11954 CUDA. `thinking=off` do cliente não demonstra desligamento; o limite foi
passado ao servidor por `--reasoning-budget`. Orçamento de VRAM emulado de 4608 MB.
Latência inclui todos os passos da trajetória, inclusive tentativas que falharam.

| Limite | Concluídas | Tempo total p50 | Tempo total p95 | Ações sem efeito | done prematuro |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 32 | 26/33 | 5,13 s | 11,01 s | 17 | 3 |
| 64 | 25/33 | 6,33 s | 15,57 s | 16 | 3 |
| 128 | 27/33 | 9,15 s | 22,55 s | 9 | 0 |

Todos usaram pico de 2438 MB por PID e terminaram sem erro de infraestrutura. Não existe
relação monotônica entre limite e acerto nesta amostra. Repetições dos mesmos 11 cenários não
são 33 tarefas independentes; não tratar diferenças pequenas como conclusão estatística.

Como referência histórica desta mesma bancada, Qwen concluiu 30/33 com p50 de 1,08 s por
trajetória; Bolt com orçamento 0 concluiu 27/33 com p50 de 2,00 s e Bolt sem teto explícito,
com `thinking=off`, 29/33 com p50 de 12,87 s. Essas medições foram de execuções anteriores,
não intercaladas com a nova série. O Qwen continua candidato de menor latência; a vantagem do
Bolt na bateria textual isolada não provou vantagem em tempo total no simulador.
Não há base para trocar o default nem escolher definitivamente o planner de uso real.

Brutos: `runs/budget-20261028-152643/`; plano exato copiado em
`runs/holdout-ubuntu-20261028/bolt-plan.json`.

## Bateria independente do d1

28 casos congelados antes da inferência: 14 presentes e 14 ausentes; sites SQLite, Rust,
Debian e pytest, mais Calculadora e Editor de Texto do GNOME em sessão isolada. O corte
0,20 foi mantido do experimento anterior. Hashes dos casos e imagens em
`runs/holdout-ubuntu-20261028/manifest.json`.

Capturas iniciais com nomes ambíguos e caixas fora do viewport foram rejeitadas antes de
consultar modelos. As telas nativas mostram janelas na visão geral do GNOME: testam
localização pelos pixels, não a eficácia de clicar numa janela já focada. Nenhum app nativo
do Windows foi testado. Não ampliar a conclusão para esse ambiente.

Resultado: 84 tentativas (28 casos ×3), 42/42 acertos preservados. Cliques errados
caíram de 15 sem verificador para 6 com corte 0,20. Nos sites, 3→0; nas telas nativas,
12→6. Os seis erros restantes são duas cenas repetidas três vezes, não seis cenários
independentes. p50: localizador 927 ms; verificador CPU 409 ms.

O ganho se repetiu fora da bateria usada para escolher o corte, mas o d1 ainda aprova
alvos inexistentes em apps nativos. Não fixar 0,20 como limite validado para produção nem
ajustá-lo usando estes mesmos resultados. O verificador permanece opt-in e experimental.

Para evitar subir o processo à mão, o runtime aceita `vision.verifier.auto_start: true`,
com `base_url` local explícita. Inicia d1 com pesos e projetor na CPU, contexto 1024 e um
slot; GPU do perfil principal não muda isso. URLs remotas e servidores existentes não são
iniciados nem encerrados. Servidor existente com checkpoint diferente causa erro. Falha
de startup encerra apenas os processos criados nesta execução.

Smoke real: `ensure_verifier` iniciou d1 com o runtime CPU oficial e
`/v1/systemone` devolveu uma probabilidade válida; o processo foi encerrado pelo teste.

## Controle do Ubuntu

GNOME headless iniciou em bus D-Bus, diretórios XDG e PipeWire próprios. Capturas pelo
ScreenCast do compositor funcionaram; o portal de produção chegou ao diálogo de autorização.
O pipeline de produção falhava por usar `signal-handoff` no fakesink. Após corrigir para
`signal-handoffs`, a ponte de produção recebeu e gravou um frame de 1280×800 pelo portal.
Captura aprovada não implica permissão de entrada: o cliente agora confere os dispositivos
concedidos antes de enviar mouse/teclado e explica como habilitar interação quando ausente.
O controle completo pelo portal e o loop com modelos ainda precisam de confirmação.
Isso não equivale a E2E nem aprova os gates R3 em diante.

## Limpeza e próximos gates

Removidos de `~/llm-bench/models` os candidatos descartados LFM3-1.7B, Ministral-3-3B e
Nemotron-3-Nano-4B, após verificar que não estavam abertos nem mapeados por processos.
Espaço liberado: 5,68 GiB. Qwen, Bolt, LFM3-VL, d1 e MAI-UI foram preservados.
Respostas, imagens, hashes e logs dos experimentos permanecem em `runs/` (gitignored).

Próximos gates: autorizar e confirmar input no portal em sessão isolada; executar tarefas
curtas com modelos no Ubuntu; repetir em placa física de 6 GB; validar apps Windows no
ambiente dedicado quando essa etapa voltar ao escopo. Nada nesta entrega aprova R3–R6.
