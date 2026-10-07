# AGENTS.md — memória do projeto (branch `mvp/do-zero`)

> Atualize ao mudar arquitetura, plano de testes ou ao descobrir quirk de plataforma. Idioma do repo: PT-BR.

## Direção

- Arquitetura normativa: [docs/arquitetura.md](docs/arquitetura.md); seleção de modelo e testes:
  [docs/testes-e-selecao-de-modelo.md](docs/testes-e-selecao-de-modelo.md); racional: [docs/arquitetura-do-zero-2026-10-07.html](docs/arquitetura-do-zero-2026-10-07.html). Produto: computer use **local**
  para GPU de **6 GB de VRAM**. Cérebro = 1 VLM de 4B (GUI-Owl-1.5-4B ou Qwen3.5-4B, a decidir por bateria própria);
  ferramentas na ordem **semântica (AT-SPI/UIA, teclado) → OCR com caixas → visão (reserva)**; Python orquestra, verifica,
  veta e executa; verificador lê o **campo**, nunca o título da janela.
- **Decidido:** ação semântica permitida; **browser use no Chrome do usuário via extensão + native messaging e permissões por origem é a prioridade nº 2 do MVP** (após a bancada). Aceite: Amazon (menor preço RTX 5090), e-mail (leitura e rascunho), notícias do dia — ver docs/testes-e-selecao-de-modelo.md §6.
- O orquestrador ainda não existe. O desenho antigo (planner texto + grounder, `loop.py`, `planner.py`) foi removido desta
  branch e vive no `master`. Não ressuscitar sem decisão explícita; reaproveitar ideias, não código acoplado.
- Eval primeiro: nenhuma feature entra sem bateria com verificação por screenshot independente.
- Testes com cliques: Windows = Sandbox (`scripts/*.ps1`); Linux = sessão isolada `scripts/linux-test-session.sh`
  (ver docs/teste-sessao-linux.md). **Nunca no desktop de trabalho.**

## Módulos (papel na arquitetura)

Runtime: `server.py`, `http_pool.py`, `config.py` (perfis antigos a reescrever). Platform: `platform_backend/` (Wayland),
`uia.py`, `actions.py`, `obs.py`, `tools.py`. OCR: `ocr.py`. Segurança: `safety.py`, `overlay.py`. Medição: `telemetry.py`.
Contratos herdados: `schemas.py` (enxugar). Dados: `evals/ground*`, `evals/ocr_bench.py`. **A criar:** orquestrador, cliente do
cérebro, Observation/Result novos, verificador, monitor de travado, `evals/brain/`.
Removidos (no histórico): vocaela, guards, prefs, skills, state, verification, UI/ditado (`app.py`, `stt.py`, `ui/`).

## Comandos

```
uv run python -m unittest discover -s tests   # suíte oficial (sempre via uv)
uv run ruff check
uv run python -m server --profile P1          # modelos em :8091 (planner/cérebro) e :8082 (visão)
.\scripts\Start-Sandbox.ps1                  # Windows, manual; .\scripts\Invoke-SandboxTest.ps1 -Command '...' (testes dev)
```
Detalhes do Sandbox em `docs/sandbox-test-env.md` (wrapper com `$LASTEXITCODE` em modo estrito).

## Segurança (nunca relaxar)

- `pyautogui.FAILSAFE = True`; hotkey `ctrl+alt+esc` (Windows). No Linux **não existe** hotkey global: o corte é o botão
  "parar compartilhamento" do GNOME (a ponte escuta `Closed` e aciona `safety.stop`) e Ctrl+C.
- `type` usa `pywinauto.keyboard.send_keys` (Unicode); `pyautogui.typewrite` descarta acentos no Windows.
- Nada de shell arbitrário; `open_app` só por whitelist.

## Quirks de plataforma

- Windows: shell das ferramentas = PowerShell 5.1 (sem `&&`/`head`); `Start-Process -PassThru` + `.ExitCode` não é confiável;
  `wsb exec` não devolve stdout (usar arquivos em `C:\job\out`); só um Sandbox por vez, fechar pelo ID (`wsb stop --id`);
  `LogonCommand` do `.wsb` não dispara. `mss.monitors[0]` é o desktop virtual (left/top podem ser negativos).
- Arquivos `.py` em UTF-8 sem BOM (`test_cleanup` trava mojibake); `.gitattributes` fixa LF (`.ps1` CRLF).
- Linux/Wayland: ponte em processo separado com o Python do sistema (`gi`); captura+input pelo mesmo portal.
  O diálogo do GNOME vem com "Permitir interação remota" DESLIGADO (`portal_rules.devices_error`); o 1º evento de teclado
  após abrir a sessão é descartado (aquecimento com Shift); `fakesink` usa `signal-handoffs` (usamos `appsink`);
  AT-SPI no Wayland dá nomes/valores/estados mas **sem posição** (itens `nobounds`); GTK4/libadwaita aninha ~25 níveis;
  estados GTK4 = `SENSITIVE`; mount FUSE do document-portal exige `fusermount3 -u -z` antes de apagar; `Introspect.GetWindows`
  e `Stop` do Mutter são negados fora do dono. `pkill -f <padrão do próprio comando>` derruba o seu shell.
- Medido na RTX 2060 6 GB (P1: Qwen3-4B-2507 + MAI-UI-2B): planner 0,3–0,5 s; visão 9–10 s/clique (encoder de imagem na CPU;
  na GPU não cabe junto do planner); imagem 768 px ≈ 5,6 s. VRAM P1 ≈ 4,4 GB.

## Git

- Commit a cada passo testável, suíte verde antes. Ignorados: `config.sandbox.json`, `sandbox/crr*.wsb`, `.sandbox-job/`,
  `last.png`, `run.jsonl`, `runs/`, `models/`.
