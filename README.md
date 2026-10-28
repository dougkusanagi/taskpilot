# copilot-road-runner — Computer Use local MVP

> MVP experimental: confiabilidade em tarefas genéricas ainda não validada.
> Default **B1** (MiniCPM5-2B); rollback explícito `--profile B0`.
> Seguir somente o [novo plano R0–R6](docs/plano-agente-generico-2026-09-20.md),
> ainda proposto. Testes offline não demonstram sucesso end-to-end;
> a [matriz anterior](docs/matriz-avaliacao-2026-09-20.md) é histórica.
> No [primeiro probe R0–R3](docs/relatorio-r0-r3-2026-09-20.md), B1 falhou
> 0/3 e U1 passou 3/3 com ~38 s aquecido em CPU-only. O app ainda não está
> aprovado para tarefas genéricas.

Fluxo: instrução → planner MiniCPM5-2B (`:8091`, só texto, nunca recebe
screenshot, nunca emite coordenadas) → UIA por nome (`pywinauto`) →
Vocaela-2 (`:8082`, screenshot → ação visual 0..1, só quando o elemento
não está na accessibility tree) → `pyautogui` → observa → repete.
Python executa, observa e **veta** (guard-rails: bootstrap da janela do app,
anti-janela-errada, anti-repetição, coordenadas fora da tela) mas nunca
escolhe a ação; sem modelos online = erro honesto.

## Setup

```bash
uv sync            # Python 3.12 (.python-version)
uv run ruff check  # lint (dev-deps)
```

Runtime próprio (sem depender de `llama-server` externo): na 1ª execução
o loop baixa o binário (`llama.cpp` Windows x64 CPU) + os GGUFs
(MiniCPM5-2B-Q4_K_M ~1,5 GB, Vocaela-2-Q8_0 + mmproj ~534 MB) para
`models/` (gitignored) e sobe planner em `:8091` + visão em `:8082` —
portas fora do padrão, sem conflito com Ollama (`11434`) nem LM Studio
(`1234`). Nas seguintes, só reusa. Endpoint já vivo na porta é
reaproveitado (não importa quem subiu); porta ocupada sem endpoint =
erro honesto. Desligue com `runtime.auto_start: false` ou `--no-runtime`;
`uv run python -m server` sobe os dois e fica no ar (`--host 0.0.0.0`
expõe ao Sandbox, só p/ testes).
Detalhes do ambiente isolado em `docs/sandbox-test-env.md`.

## Ubuntu (experimental)

O runtime próprio também seleciona binários Ubuntu x86_64 (CPU, Vulkan ou CUDA).
O controle da interface continua opt-in via `--platform wayland`, usando portais do GNOME;
veja [setup e limites](docs/prototipo-wayland-2026-10-02.md).
Isso ainda não comprova tarefas completas no Ubuntu. O default B1 permanece.
[Resultados locais e limites](docs/validacao-ubuntu-2026-10-28.md).

O d1 é experimental. Para iniciar o verificador automaticamente na CPU, configure
`vision.verifier` com `base_url: "http://127.0.0.1:8083/v1"`, `auto_start: true` e
`threshold: 0.2`. Sem `auto_start`, o endpoint continua sendo gerenciado externamente.
O corte 0,20 ainda deixou falsos positivos nas telas nativas; não é garantia de clique correto.

Testes locais, sem controlar o desktop:

```bash
uv run python -m unittest discover -s tests
uv run ruff check
uv run python main.py --self-test
```

## Modelos (manual)

Normalmente você não precisa disso (`main.py` baixa e sobe sozinho).
Para subir na mão ou expor ao Sandbox:

```powershell
# planner MiniCPM5-2B em :8091 (--jinja: template oficial do MiniCPM5)
.\models\bin\llama-server.exe -m .\models\MiniCPM5-2B-Q4_K_M.gguf `
  --host 127.0.0.1 --port 8091 -c 4096 --jinja

# visão Vocaela-2 em :8082 (--mmproj obrigatório p/ imagem)
.\models\bin\llama-server.exe -m .\models\Vocaela-2-500M-1024R2-Q8_0.gguf `
  --mmproj .\models\mmproj-Vocaela-2-500M-1024R2-Q8_0.gguf `
  --host 127.0.0.1 --port 8082 -c 4096

# ou os dois de uma vez (fica no ar até Ctrl+C):
uv run python -m server
# p/ o Sandbox enxergar o host: --host 0.0.0.0 (+ config.sandbox.json → HOST_IP)
uv run python -m server --host 0.0.0.0
```

## Uso real (local, padrão — sem parâmetros)

O sistema roda **no host por padrão**, sem nenhum parâmetro: `config.json`
(`127.0.0.1:8091/8082`) já aponta para o runtime próprio.

```bash
# teste sem clicar em nada
uv run python main.py --self-test

# dry-run do grounding Vocaela (screenshot → ação, sem clicar)
uv run python main.py --locate "Click the address bar"

# end-to-end (comece com --max-steps 4; não use o PC enquanto roda)
uv run python main.py "abra o Edge e busque preço RTX 4060" --max-steps 4

# perfil B0 (rollback): planner MiniCPM5-1B no lugar do 2B
uv run python main.py "abra o chrome e pesquise o preço da rtx 5090 na amazon" --max-steps 10 --profile B0
# default é B1; sem --profile já usa o 2B

# perfil U1: um unico Qwen3-VL-2B (modelo + projetor baixados na primeira vez)
uv run python main.py "abra o chrome, abra uma nova aba e pesquise o preco da rtx 5090 na amazon" --profile U1 --max-steps 10
```

```bash
# suite oficial (sempre via uv)
uv run python -m unittest discover -s tests
```

## Histórico da implementação anterior (não é o plano vigente)

Inventário de módulos, não comprovação de integração ou qualidade. As alegações
abaixo precisam da auditoria R0: memória, verificação e checkers ainda têm lacunas.

- **F0 baseline:** `evals/tasks.json` (20 tarefas), `evals/runner.py`
  (`--pilot --dry-run`, sem clicar no host), `telemetry.py` (GPU/driver/
  backend/RAM/CPU/resolução/carga) e `runs/<id>/` (JSONL, summary,
  config/revisões, timings, decisões, evidências; gitignored). `--dry-run`
  bloqueia TODOS os efeitos (bootstrap/foco/teclado/CLI).
- **F1 contratos:** `schemas.py` (Observation/ElementRef/FrameRef/TaskState/
  Decision/ActionResult/Completion), `state.py` (fato ≠ hipótese),
  `config.profile` + `PROFILES` (B0..E2); confianças fictícias 0,9/0,8
  removidas (None = não calibrada).
- **F2 vínculo:** alvos UIA por `observation_id#element_id` (stale veta),
  `obs.capture_frame` + `FrameRef`, `post_state` reutilizável/invalidável,
  `actions.check_preconditions` + `release_all`, executor único em `safety`.
- **F3 memória/verificação:** `verification.py` (efeito específico, espera
  condicional cancelável, done com evidências vetado), prompts por papel
  (textual + `build_unified_prompt`), anti-loop ação+estado+efeito,
  orçamentos separados (ações/decisões/retries/tempo).
- **F4 perfis:** `model_adapters.py` (capacidades, unificado = 1 processo,
  smoke tests, orçamento 6 GB), `server.ensure_servers_for_profile` +
  `gpu_status` + `record_manifest`. Rollback explícito: `--profile B0`.
- **F5 skills:** `skills.py` (catálogo compacto → skill do modelo →
  referências) + `skills/` (`computer-use`, `browser-gui`, `text-editing`,
  `blender-cli` piloto com executor CLI restrito: argv, sem shell, cwd
  delimitado, timeout, artefatos). `use_skill` no planner.
- **F6 otimização:** `sequence` (≤3 primitivas teclado/espera, guardas entre
  elas), `http_pool.py` (cliente persistente, cancelamento, retries só-HTTP).

O Sandbox é **só para testes de desenvolvimento** e é acionado apenas
pelos scripts (`.\scripts\Start-Sandbox.ps1` manual,
`.\scripts\Invoke-SandboxTest.ps1` automatizado — detalhes em
`docs/sandbox-test-env.md`). Nunca passe `--config config.sandbox.json`
no host: esse arquivo só existe **dentro** do Sandbox (gerado pelo
`bootstrap.ps1` com o HOST_IP).

## Tray + janela Spotlight + ditado (opcional)

```bash
uv sync --extra ui          # pywebview, pystray, faster-whisper, sounddevice
uv run python main.py --ui  # ícone na bandeja; ctrl+alt+space mostra/esconde
```

Caixa de texto + **Enviar** + **microfone**: o texto aparece enquanto você fala
(faster-whisper `base` int8, ~74 MB, baixado na 1ª vez); durante a gravação há
**Parar** (texto fica na caixa para corrigir) e **Enviar**; silêncio de 1,5 s
envia sozinho (`stt.auto_send`). A janela se esconde enquanto o agente age.
Config em `config.json` → seções `ui` e `stt` (modelo `tiny|base|small`,
`silence_ms`, `hotkey`).

## Segurança

- Uso real roda no host: não use mouse/teclado enquanto o agente age.
  Pressione **Ctrl+Alt+Esc** (`stop_hotkey` no `config.json`) para abortar
  o loop; ESC puro não aborta (o agente pode usar `press_key esc`).
  Testes de desenvolvimento (cliques descartáveis) vão no Sandbox.
- Failsafe do `pyautogui`: encoste o mouse no canto superior-esquerdo.
- `open_app` só aceita apps da whitelist (`tools.APP_COMMANDS`).
  Não há teleporte p/ URL: o planner navega pela UI do navegador
  (`ctrl+l`, digitar, `enter`, cliques), como um humano.
  Nada que o planner emite passa por shell.
- Log de cada passo em `run.jsonl` + screenshot em `last.png`.
