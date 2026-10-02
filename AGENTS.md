# AGENTS.md — memória do projeto copilot-road-runner

> Arquivo de memória para agentes. Atualize ao mudar arquitetura, plano de
> testes ou descobrir quirk de plataforma. Idioma do repo: PT-BR.

## Direção vigente e ordem de leitura

- **Único plano vigente:** [docs/plano-agente-generico-2026-09-20.md](docs/plano-agente-generico-2026-09-20.md).
  Etapas **R0–R6 propostas, ainda não aprovadas**. Começar por R0; não continuar
  as antigas fases F0–F7. O plano de 19/09 foi removido: os status de conclusão
  não demonstravam funcionalidade end-to-end.
  Default atual **B1**; rollback explícito `--profile B0`. Não trocar default
  sem os gates novos. Matriz de 20/09 e revisão de 18/09 são históricas.
  Existência de contratos/testes offline não prova integração. Em R0, memória
  passou a ser enviada ao planner, done implícito foi removido e checkers foram
  corrigidos; os gates end-to-end continuam pendentes.
  R0 foi concluída em 20/09; relatório parcial:
  `docs/relatorio-r0-r3-2026-09-20.md`. B1 falhou 0/3 no primeiro probe real
  (repetiu ctrl+t); U1 passou 3/3, mas levou ~38 s aquecido em CPU-only.
  Isso não muda o default nem aprova R3/R4/R5.
  R1 concluída em 20/09 (235 verdes: `tests/test_r1_cycle.py` +
  `tests/test_r1_contracts.py`): observação ≠ evidência; `done_items` exige
  `evidence_refs` (proposta vs aceita em `tm`); 6 kinds fim a fim
  (action/perception/question/skill/sequence/finish) — `perceive`
  (`uia_refresh`/`read_focused`, teto 6/run) relê sem input físico e alimenta
  o prompt; `ask`→question, `done`→finish; `ActionResult` só p/ atuação real;
  `observation_ref` em toda decisão. Probes com modelos reais (≥30 cenas) e
  E2E Sandbox ficam p/ R3/R4. Próxima: R2 (percepção visual/OCR, UIA
  diagnosticável, confirmação específica).
  R2 fatia 1 em 20/09 (252 verdes, `tests/test_r2_obs.py` + fixtures
  `uia-vazia`/`modal-dialog`): UIA com diagnóstico (ok/provider_empty/
  truncated/timeout_partial/no_window/error em `uia.LAST_DIAG`,
  `Observation.error` e `tm["uia_diag"]`); alvo ambíguo recusado com
  contextos (sem first-match); caminho visual com `FrameRef`/DPI e guarda
  de frame obsoleto (janela/modal mudou → sem clique); anti-loop considera
  resultado (`_loop_has_progress`). OCR local pendente (sem backend no
  ambiente); expansão de ramo UIA e probes visuais ficam p/ próxima fatia.
  R2 fatia 2 em 20/09 (265 verdes, bateria com 7 fixtures): `value` de tipos
  textuais capturado no walk (`Edit`/`Text`/… → `tipo:nome="valor"`) e
  `perceive` com `expand:<nome>` (subárvore real via `uia.expand_subtree`,
  com miss/ambíguo honestos, sem input). OCR segue pendente: sem `winrt`/
  tesseract no ambiente; candidatos (Windows.Media.Ocr, tesseract) exigem
  spike de backend + medição antes de entrar no `perceive`.
  R2 fatia 3 em 23/09 (286 verdes, `tests/test_r2_visual.py` + `ocr.py`):
  spike de OCR entregue e ligado ao `perceive` (`ocr` como 4ª spec, ao lado
  de uia_refresh/read_focused/expand; `ocr.read()` com backends
  winrt→tesseract e `unavailable` honesto — neste ambiente segue sem backend,
  fato "indisponível", nunca texto inventado); `verify()` devolve `ui_after`
  e `confirm_effect` confirma com UIA antes/depois (modal dispensado,
  conteúdo esperado visível, conteúdo novo pós-scroll) — título sozinho
  continua sem confirmar. R3 fatia 1 commitada (bateria 30 cenas +
  `evals/probe_battery.py`, offline). Gates com modelos reais (probes ≥30
  cenas ×3, leitura visual, E2E Sandbox) seguem pendentes.
- Produto: computer use local, rápido, para GPU a partir de **6 GB de VRAM**;
  entender pedidos, observar monitores/janelas e usar mouse/teclado reais.
  UIA localiza e informa; modo GUI não usa edição semântica invisível ou open_url.
- Evoluir o código existente. Comparar perfil **duplo** (planner textual + visão)
  e **unificado** (VLM que planeja e enxerga), conforme gates do novo plano.
  Não fixar dois modelos como requisito futuro nem trocar default sem benchmark.
  A proibição de screenshots/coordenadas no planner vale para o papel textual;
  o perfil unificado deve usar contrato visual vinculado ao frame.
- Python observa/executa/veta; modelos escolhem ações, alvos, subobjetivos e skills.
  Memória de tarefa, IDs de observação/frame e confirmação de efeitos substituem
  heurísticas por app gradualmente, mantendo safety e testes durante a migração.
- Skills sob demanda podem habilitar CLI/scripts delimitados (ex.: Blender),
  explicitamente separados da GUI. Não habilitar shell arbitrário como fallback.
- `empero-ai/Qwen3.8-2B-Distill` é candidato comunitário experimental, não um
  Qwen3.8-2B oficial com visão validada. Conferir pesos/projetor e suporte antes
  de habilitá-lo como VLM; ver critérios de elegibilidade no plano vigente.
- Uso normal continua local, runtime próprio, sem flagship/API obrigatório.
  Testes dev com cliques continuam exclusivamente no Sandbox; exceções de
  hardware não cobertas exigem ambiente de teste dedicado, nunca o desktop de trabalho.

## Inventário atual (baseline; não representa os gates do novo plano)

- **Planner MiniCPM5-2B** (`:8091`, só texto, nunca recebe screenshot, nunca
  emite coordenadas) → **Vocaela-2** (`:8082`, screenshot → ação visual 0..1)
  → Python executa (tools, UIA, mouse/teclado) e NUNCA decide. (B0 = 1B fica
  como rollback via `--profile B0`.)
- Ordem: `open/focus/type` (planner) → `uia_click` por NOME → Vocaela
  (só quando o elemento não está na accessibility tree).
- Planner com `response_format: json_schema` (coordenadas impossíveis por
  construção); contexto UIA como `tipo:nome` (interativos primeiro);
  Vocaela recebe as últimas 3 ações como histórico.
- Runtime próprio (`server.py`), padrão e sem parâmetros: endpoints locais
  caídos → baixa llama.cpp + GGUFs p/ `models/` (gitignored, 1ª vez) e sobe
  `:8091`/`:8082` (portas fora do padrão: sem conflito com Ollama 11434 /
  LM Studio 1234; nenhuma dependência de `llama-server` externo);
  endpoint vivo é reusado, porta ocupada sem endpoint = erro honesto.
  `runtime.auto_start: false` ou `--no-runtime` desliga; URLs remotas
  (Sandbox → HOST_IP, só em testes dev) nunca baixam aqui.
  `uv run python -m server` deixa os dois no ar (`--host 0.0.0.0` só p/
  expor ao Sandbox em testes).
- Sem modelos online = erro honesto, sem fallback programático.
- Guard-rails determinísticos declarados (`loop.py`): bootstrap da janela do
  app ANTES do planner; anti-janela-errada e anti-repetição viram
  `last_error` p/ o planner; retries (`MAX_RETRIES=3`) NÃO consomem
  `max_steps`. `open_app` = whitelist (`tools.APP_COMMANDS`), nada passa por
  shell. Sem teleporte p/ URL (`open_url` removido): navegar é pela UI do
  navegador (ctrl+l, digitar, enter, cliques), como um humano.
- Revisão histórica: `docs/revisao-codebase-2026-09-18.md`.
  Roadmap vigente: `docs/plano-agente-generico-2026-09-20.md`.
- **UI opcional** (`main.py --ui`, extra `ui`: `uv sync --extra ui`): `app.py`
  = tray (pystray, thread daemon) + janela Spotlight (pywebview/WebView2,
  thread principal) + hotkey `ui.hotkey` (`ctrl+alt+space`) + agente em
  thread (janela se esconde antes de agir). `stt.py` = ditado ao vivo
  (faster-whisper `base` int8; parciais a cada 1 s, silêncio 1,5 s → final
  → `auto_send`). Lógica do ditado é pura (`Dictation.feed`) e testada sem
  mic/modelo. Erro do engine NUNCA vira instrução (`on_error`). Sandbox não
  instala a UI. Histórico de implementação: §10 do relatório; preservar durante
  a refatoração, mantendo STT inicialmente em CPU no orçamento de 6 GB.
- Arquivos-chave: `main.py` (CLI), `loop.py` (observe→decide→act→verify),
  `planner.py`, `vocaela.py`, `server.py` (runtime llama.cpp), `uia.py`,
  `actions.py`, `tools.py`, `safety.py`, `config.py`, `obs.py`.
- Módulos anteriores (integração a auditar em R0): `telemetry.py` + `evals/` (runner dry-run) +
  `runs/<id>/`; `schemas.py`/`state.py` (contratos, sem confidence fictícia);
  `verification.py` (efeito específico, done com evidências);
  `model_adapters.py` + `config.PROFILES` (B0..E2, unificado = 1 processo);
  `skills.py` + `skills/` (4 skills, CLI restrito); `http_pool.py` +
  `sequence` (≤3 primitivas). `--dry-run` bloqueia todos os efeitos.
- Perfil U1 executavel: `--profile U1` usa somente Qwen3-VL-2B-Instruct
  (GGUF Q4_K_M + mmproj F16) em um processo; o planner recebe screenshot em
  toda decisao e o mesmo endpoint faz grounding via `QwenGroundingAdapter`
  (JSON `{"x","y"}` 0..1, nunca `<Action>` do Vocaela). O runtime recusa
  checkpoint diferente, sem fallback silencioso para MiniCPM.

## Comandos

Comparação textual isolada (30/09): `evals.model_bench` usa servidor local
OpenAI-compatible (padrão LM Studio `:1234/v1`), 32 cenas ×3, argumentos
checados e erros incluídos no denominador. Não importa loop/executor nem
captura tela/baixa pesos. `--list` lista IDs; `--model ID --format both`
gera `runs/model-bench-.../resultado.zip` com respostas/manifesto/casos.
Thinking native por padrão; sem medição automática de VRAM/offload e sem
aprovação de visão/E2E. Guia: `docs/teste-modelos-locais.md`; regressões
isoladas: `uv run python -m unittest discover -s tests -p test_model_bench.py`.
O runner antigo `evals.probe_battery` não foi alterado por essa entrega.
Validação do runner: 13 regressões isoladas e lint verdes. Suite completa:
217 testes, mesmos 49 erros de
plataforma e 1 skip do baseline Linux; sem aprovação da suite Windows.

```powershell
uv run python -m unittest discover -s tests   # suite oficial (sempre via uv)
uv run ruff check                             # lint (dev-deps do pyproject)
uv run python main.py --self-test             # sem clicar em nada
uv run python main.py "..." --max-steps 4     # uso real: local, sem parâmetros
uv run python -m evals.runner --pilot --dry-run  # smoke, não valida tarefas
```

Use **uv** — o python do sistema não tem as deps (`pyautogui` etc.).
Análise em Linux de 30/09: lint passou; suite reportou 204 testes, 49 erros
e 1 skip, com falhas de importação PyAutoGUI/MouseInfo/Xlib
(`FamilyServerInterpreted` ausente). Não equivale à bateria completa de
286 testes histórica nem prova regressão no Windows. Isolamento dos testes
puros das deps GUI continua necessário. Ver
`docs/relatorio-viabilidade-6gb-2026-09-30.md` para o parecer e lacunas
reproduzidas; esse relatório não muda o plano/default nem aprova gates.
Sandbox: `.\scripts\Start-Sandbox.ps1` (uso manual, sem admin) e
`.\scripts\Invoke-SandboxTest.ps1 -Command '...' ` (eu rodo; com
`-Bootstrap` faz a bateria completa — 1ª vez demora minutos no winget).
Detalhes em `docs/sandbox-test-env.md`.

## Plano de teste = Windows Sandbox (só p/ testes dev; uso real é local)

Uso real: `uv run python main.py "..."` no host, sem parâmetros (runtime
próprio sobe os modelos sozinho). O Sandbox serve SÓ para testes dev com
cliques descartáveis, acionado apenas pelos scripts abaixo — nunca passar
`--config config.sandbox.json` no host (esse arquivo só existe dentro do
Sandbox, gerado pelo `bootstrap.ps1`).

Histórico: a VM Hyper-V (`crr-test`, scripts `New-TestVm/Reset-TestVm`)
foi removida em favor do Sandbox (commit `3e87285`) — menos setup manual,
cada abertura é um ambiente limpo descartável.

## Segurança (nunca relaxar)

- `pyautogui.FAILSAFE = True` (`actions.py`); hotkey `ctrl+alt+esc`
  (`safety.py`, configurável via `stop_hotkey`; ESC puro NÃO aborta — o
  agente usa `press_key esc`). Começar com `--max-steps 4`. Uso real roda
  no host (não use o PC enquanto age); testes dev com cliques vão no
  Sandbox.
- `type` usa `pywinauto.keyboard.send_keys` (Unicode); `pyautogui.typewrite`
  descarta acentos em silêncio no Windows.

## Quirks e correções históricas (não são backlog nem arquitetura obrigatória)

Preservar descobertas de plataforma/safety. Heurísticas de estratégia por app
abaixo são legado a substituir conforme o plano vigente, não regras a perpetuar.

- Shell das ferramentas = **PowerShell 5.1**: sem `head`/`&&`; usar
  `Select-Object`, `;` ou `; if ($?) { }`.
- `Start-Process powershell -PassThru` + `.ExitCode` **não é confiável**
  (vem vazio) → `sandbox/agent.ps1` usa wrapper com `$LASTEXITCODE`
  em modo estrito (`$ErrorActionPreference='Stop'`).
- Em here-string `@" "@`, `$` expande: escapar como `` `$ `` ao gerar
  scripts (foi o bug do `$ErrorActionPreference` no wrapper).
- `[void][xml]$x = ...` é sintaxe inválida; usar `$x = [xml]...`.
- PS 5.1: `Get-Content -Raw` **não aceita wildcard**; `-File` com
  `-Command '...'` (aspas simples) para `$env:` não expandir no host.
- Host alcança modelos em `127.0.0.1:8091/8082`; no Sandbox o host é o
  **gateway** (`HOST_IP`, preenchido pelo `bootstrap.ps1`).
- `Invoke-SandboxTest.ps1` fecha em `finally` pelo ID com `wsb stop`
  (timeout não deixa órfão); jobs ficam em `.sandbox-job\` (gitignored).
  Histórico: matar só `WindowsSandboxClient` deixou Server/RemoteSession
  órfãos em 18/09. Para instância antiga use `wsb list --raw` +
  `wsb stop --id ID`; nunca matar `vmwp` às cegas — WSL usa outro.
- `LogonCommand` do `.wsb` NÃO dispara nesta máquina (Windows 11 Pro 25H2
  build 26200.9457; confirmado manual em 18/09). Não depender dele:
  `Start-Sandbox.ps1` e `Invoke-SandboxTest.ps1` usam a CLI oficial
  `wsb start/connect/exec -r ExistingLogin/stop`. Smoke test do ciclo
  completo passou. Só um Sandbox por vez; os scripts recusam iniciar se
  já houver uma instância, e encerram pelo ID apenas a que criaram.
- `wsb exec` não retorna stdout (só `{"ExitCode": 0}`) → observabilidade
  do agente é por arquivos em `C:\job\out`. E `cmd /c start "" ...` com
  título vazio morre em silêncio sob `wsb exec` (ExitCode 0, nenhum
  marker): o dispatch usa `start` SEM título vazio (provado T1-T5 em
  18/09: mapping ok, ps direto ok, agent foreground ok, detach sem
  título ok).
- Arquivos `.py` já entraram com UTF-8 duplo + BOM (mojibake no prompt do
  planner); `test_cleanup.test_sem_mojibake_nem_bom` trava. `.editorconfig`
  + `.gitattributes` fixam UTF-8/LF (`.ps1` CRLF).
- `mss.monitors[0]` é o desktop VIRTUAL (left/top podem ser negativos);
  `obs.crop_to_rect` converte tela↔pixel. `uv sync` dentro do Sandbox usa
  `UV_PROJECT_ENVIRONMENT` fora de `C:\crr` (senão sobrescreve o `.venv` do host).
- Timeouts das ferramentas em ms; trial no Sandbox leva ~1 min
  (sem `-Bootstrap`).
- Overlay Tk nasce com título `tk` e pode estar em foreground: o planner via
  `tk`, copiava o exemplo do spec (`focus("trecho do título")`) e focava o
  próprio overlay → LOOP (run real 19/09). Janelas tituladas `crr-overlay`
  são ignoradas em `uia.snapshot` e `tools.focus` (`is_overlay_title`); exemplo
  do spec virou `Google` + regra anti-cópia no prompt; `focus` timeout 8s→3s.
- Run real 19/09 (rtx 5090 na amazon): o 1B copiou o exemplo `focus("Google")`
  e `focus_window` por substring casava QUALQUER aba do Chrome (todas terminam
  em "- Google Chrome") → falso sucesso + loop; `use_skill` GUI virava
  `wait(0ms)` executável → stall. Fixes: `tools.focus_window` casa na PARTE
  DA PÁGINA (`_page_part` stripa o sufixo; só nome de app — chrome/edge… —
  casa no título cheio) com ranking exato>prefixo>palavra; guarda anti-exemplo
  veta `focus("Google")` fora de pedido com "google"; bootstrap cobre
  chrome/edge/brave (`_browser_want`); skill GUI faz re-query com contexto em
  vez de placeholder; `sequence` rejeita passo misto press_key+keys.
- Run real 19/09 22:58 (amazon): ramo `sequence` em `_decide_planner` era
  inalcançável (`_planner_to_action` devolvia `wait(0)` antes) → open_app
  dentro de sequence executava placeholder sem validar; fix retorna None p/
  sequence. Bootstrap abria janela NOVA do Chrome → seletor de perfil
  ("Quem está usando?"); agora é focus-first (janela existente já está no
  perfil certo; adivinhar entre perfis é sensível). Dúvida honesta virou
  ação `ask` (human-in-the-loop, teto 3/run, timeout) + `prefs.json`
  (gitignored: ex. `browser_profile` lembrado após 1ª resposta).
- Run real 19/09 23:12 (amazon): 1B inventou `ctrl+alt+n` p/ nova aba
  (correta: `ctrl+t`) e repetiu 5x sem efeito. Fixes: receita de hotkeys no
  prompt/spec/skill (`ctrl+t` nova aba, `ctrl+l` endereço…); verify de
  sequence observa as combinações (`_verify_action`) e diz "no visible
  effect" + receita quando nada muda; `_repeat_note` cita o conteúdo da
  sequence; logs mostram `sequence(...)` em vez de `wait("")`.
- Runs reais 20/09 (rtx 5090, amazon "Continue shopping"): B1 avançou até
  `Amazon.com` e travou; U1 (174s, 4 vetos) nem saiu do step 1 com
  `planner_calls=0` no summary. Causas: (a) guarda browser-ativo dizia só
  "ctrl+l, type_text e enter" — na página da Amazon o certo é dispensar o
  intersticial via clique, não renavegar; (b) snapshot UIA do Chrome vinha
  com só 6 itens (Minimizar/Restaurar/Fechar/Nova guia/Window/Pane), sem
  conteúdo web — planner sem alvo redigitava URL; (c) `observe()` lia
  `hotkey(ctrl+l)` sem mudança de título como "wrong combo", quando tecla
  de foco NUNCA muda título; (d) `UNIFIED_SYSTEM` dizia só "same as textual"
  e o Qwen ignorou a regra de não-reabrir; (e) veto pós-planner descartava
  `tm`, escondendo custo/latência; (f) visão Qwen com system `<Action>` do
  Vocaela = protocolo errado (~40s p/ falhar em CPU). Fixes: guarda genérica
  (hotkey/type/uia_click/visual/sequence + "parte abrir CUMPRIDA" +
  intersticial primeiro); prompt §§1c/3b/3c + hint "page content NOT in UI
  elements → visual_action"; `observe()` neutro p/ teclas conhecidas
  (ctrl+l/ctrl+t/…); `UNIFIED_SYSTEM` = núcleo textual por extenso + frame
  0..1; `ctx._partial_tm` acumula custo no veto; `QwenGroundingAdapter`
  (JSON `{"x","y"}`) via `build_adapters` p/ modelo com "qwen" (Vocaela segue
  no B0/B1). U1 em CPU continua lento (~40s/chamada, binário CPU-only com
  `ngl=0`): sem GPU não há milagre; medir antes de trocar default (R3/R5).

## Workflow de git

- Commit + push a cada passo testável que valer (suite verde antes).
- Ignorados: `config.sandbox.json`, `sandbox/crr*.wsb`, `.sandbox-job/`,
  `last.png`, `run.jsonl`, `runs/`.

## Benchmark gerenciado LM Studio (30/09)

- `uv run python -m evals.model_bench --lmstudio` delega a
  `evals/lmstudio_bench.py`: CLI local `lms`, inicia daemon/servidor se a porta
  estiver recusando conexão, seleciona model keys contendo minicpm (`--match`/
  `--model` substituem), descarrega residentes e testa um arquivo por vez.
- Contexto solicitado 8192; offload auto por omissão; formatos ambos, native
  thinking, 2048 tokens. Não certifica VRAM ou parâmetros efetivos. Guarda
  inventário/estimativa/ps/comandos em automation.json, comparativo e ZIP conjunto.
  Não altera default B1 nem executa ferramentas GUI. Servidor permanece ativo.
- 7 testes gerenciados + 13 do avaliador verdes; integração com instalação real
  Windows/LM Studio ainda precisa da execução do usuário.
- Suite completa nesta revisão: 224 testes, mesmos 49 erros de plataforma e
  1 skip no Linux; sem commit/push porque a suite oficial não está verde.

## Correções do benchmark textual (30/09, text-32-v2)

- `evals.model_bench`/`lmstudio_bench`: `--quick` seleciona oito cenas de oito
  categorias, uma repetição, formato schema por padrão; `--limit` limita essa
  seleção quando combinado com quick. Aquecimento usa cena própria, fora da
  bateria avaliada. O modo gerenciado encaminha quick ao runner textual.
- Oráculos v2 substituem substring por gramáticas conservadoras de valor em BRL,
  esclarecimento e clique afirmativo; negado/contraditório/alvo diferente não
  passa só por citar o valor ou label esperado. Paráfrases válidas fora dessas
  gramáticas podem falhar: não é juiz semântico geral nem aprovação visual/E2E.
  Args de skill agora são comparados (antes um dict desconhecido era ignorado);
  sequence confere cada passo e rejeita passos extras. Alternativas explícitas
  aceitam UIA na barra e URL+Enter com foco já demonstrado. Tipos/argumentos são
  estritos; campos de outro tipo e coerções de ms são recusados.
- Exit 0 significa execução completa sem infra, mesmo com nota zero; infra
  isolada, abort por cinco erros, execução incompleta ou infra no warmup dão 2;
  Ctrl+C dá 130. `complete` indica tentativas feitas; `execution_ok` e
  `execution_status` indicam validade da execução. ZIPs parciais preservados;
  comparativo distingue infra das tentativas e do warmup. Manifesto v2 registra
  avaliador, IDs selecionados e exit code; taxas v1/v2 não são intercambiáveis.
- LM Studio confirma running+porta por `server status --json --quiet`, inclusive
  após iniciar; `--model` aceita key exata ou trecho único, sem escolher ambíguo.
  Parâmetros não finitos/temperatura fora de 0..2 falham antes de acessar CLI ou
  descarregar residentes. Sem medição automática de VRAM/offload; gpu max segue
  uma solicitação. Não altera perfil/default nem executa GUI.
- Validação: 28 testes do avaliador + 12 gerenciados verdes, lint e diff-check
  verdes. Suite oficial: 244 testes, 49 erros de plataforma conhecidos e 1 skip
  no Linux (PyAutoGUI/MouseInfo/Xlib); sem commit/push pelo gate de suite verde.
  Integração real Windows/LM Studio/GPU continua pendente.

## Pesquisa de modelos e thinking (01/10)

- Pesquisa: `docs/pesquisa-modelos-2026-10-01.md`. Candidatos prioritários
  propostos: LFM2.5-VL-3B, Agents-A1-4B, Gemma 4 E2B e GUI-Owl Instruct;
  pesos/projetores e runtime precisam de validação local. Bonsai 2 27B
  PTQ1_0 + visão ultrapassa 6 GB só em arquivos; Bonsai 27B binário tem
  footprint menor, mas pico/runtime próprios impedem afirmar que cabe.
- Quirk reproduzido no LM Studio local: `chat_template_kwargs.enable_thinking`
  do runner foi ignorado (off ainda gerou 122 tokens no warmup).
  `reasoning_effort="none"` em `/v1/chat/completions` desligou de fato no
  experimento; API nativa `/api/v1/chat` documenta `reasoning: "off"`.
  Não confundir contratos nem inferir estado pelo flag. Runner oficial não
  foi alterado; script/relatórios brutos em `runs/research-qwen-20261001/`.
  Série anterior confirmou reasoning no Qwen3.5-4B (6991 tokens em 32 cenas).
  Ablação real 32×3, mesmo Q4_K_M/schema/temperatura/contexto: native
  79/96 (p50 3275,4 ms; p95 8019,3); off via reasoning_effort=none
  70/96 (p50 330,1 ms; p95 550,5). Zero infra/truncamento; reasoning em
  96/96 native e 0/96 off. Velocidade ~10× com queda de qualidade; não
  recomendar desligamento global como qualidade equivalente. Modelo do
  experimento descarregado ao final; nenhum peso novo baixado.
  Pesquisa/ablação textual não aprovam visão, E2E ou mudança do default B1.

## Melhorias medidas para 6 GB (02/10/2026)

- Relatório: `docs/melhorias-6gb-2026-10-02.md` (+ HTML). Perfis **P1** (Qwen3-4B-Instruct-2507 +
  MAI-UI-2B, 5,3 GB), **P2** (com GUI-Owl) e **U3** (Qwen3-VL-4B unificado, 4,9 GB) com preset
  medido; **não mudam o default B1**. Tudo offline (simulador `evals.trajectory_bench`, bateria
  visual `evals.ground_bench`): sem E2E no Windows/Sandbox.
- `done` cita IDs `E1..` das evidências confirmadas (antes todo `done` era vetado). Schema por ação
  (`planner.features: dynschema`), exemplos (`fewshot`), checklist (`plan`), veto de ambiguidade
  (`guards.py`), `click_text`/`fill`/`save_as`/`perceive wait:` (opt-in `tools`).
- Visão: protocolo por família (`vocaela.default_protocol`): `pyauto` MAI-UI/GUI-Owl, `p2d`
  Qwen3-VL, `json` resto; escala sempre 0..1000. Zoom desligado (piorou).
- Runtime próprio: `runtime.backend` cpu|vulkan|cuda (antes só CPU: `ngl` nunca usava a GPU),
  `kv_cache`, `mmproj_offload`, `parallel`; navegador abre com `--force-renderer-accessibility`.
- Com imagem, 4B em contexto 8192 estoura 6 GB; use 4096. Suite no Linux: 474 testes, 0 erros.

