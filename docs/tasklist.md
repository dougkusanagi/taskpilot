# Tasklist do MVP — 07/10/2026

Ordem e dependências. Detalhes: [arquitetura.md](arquitetura.md), [testes-e-selecao-de-modelo.md](testes-e-selecao-de-modelo.md).
Marque `[x]` só com evidência (teste verde, medição registrada). Cada fase tem **saída** objetiva.

## Fase 0 — Contrato das ferramentas (curta; destrava tudo)
- [x] Schema JSON do cérebro: uma variante por ferramenta (`activate`, `set_text`, `type`, `key`, `read`, `wait_for`, `click_text`,
      `click_visual`, `read_page`, `find`, `click`, `form_input`, `get_page_text`, `navigate`, `tabs_*`, `screenshot`, `handoff`,
      `ask`, `done(answer, evidence)`, `fail`).
- [x] Formato textual da Observation e do Result (delta, evidência, ids com validade por leitura).
- Evidência N0: 127 testes (1 skip), lint verde; `docs/brain-contract-v0.md` e schema publicado.
- **Saída:** contrato v0 congelado em `docs/` + schemas validando em testes N0. Mudança depois = versão nova.

## Fase 1 — Bancada de decisão (seus itens 1)
Teste que usa uma IA: dado o prompt + observação, o modelo deve entender, raciocinar quando preciso e **responder chamando a
ferramenta certa**.
- [ ] Cenas N1 (15 categorias; parte cega) + avaliador determinístico + runner + métricas (ver testes §2).
- [ ] Fixtures web locais (loja com preços, webmail, notícias, injeção, CAPTCHA, senha) em localhost.
- [ ] **Eval loop fino multi-turno** (~100 linhas): alimenta a observação da fixture, executa a ferramenta contra um ambiente
      simulado com estado e devolve o resultado. Vira o núcleo do agent loop na Fase 4 (não é descartável).
- [ ] Comparar com e sem decodificação restrita por schema/gramática; thinking ligado/desligado.
- **Saída:** `uv run python -m evals.brain run --model X` gera `results/X.json`; N0 verde.

## Fase 2 — Seleção do modelo (seu item 2), um a um
- [ ] Qwen3.5-4B (instalado) → registrar. [ ] Qwen3-4B-2507 (baseline só-texto).
- [ ] Baixar (com autorização): GUI-Owl-1.5-4B, Fara1.5-4B, Qwen3-VL-4B.
- [ ] Quantizações do melhor (Q4_K_M vs Q5/IQ4) e projetor Q8_0; VRAM de pico por processo.
- **Saída:** modelo escolhido pelo gate N1 (≥85% nas cenas cegas, ação proibida = 0, JSON ≥98%, p95 ≤5 s, VRAM ≤5 GB).

## Fase 3 — Extensão Chrome + native host + permissões (paralela à 2; depende só da Fase 0)
- [ ] Native host (stdio JSON ↔ socket local autenticado) e manifesto por SO (Linux/Windows).
- [ ] Extensão MV3: `read_page` com refs, `find`, `click`, `form_input`, `get_page_text`, `navigate`, abas/grupo "TaskPilot",
      screenshot; `optional_host_permissions` por origem.
- [ ] Permissões: allowlist por origem (read/interact, validade), categorias sensíveis, confirmação por ação, `handoff`,
      botão de parar, log com screenshots; CLI `taskpilot permissions`.
- [ ] Testes N2 em perfil descartável (`--user-data-dir` temporário, `--load-extension`) contra as fixtures.
- **Saída:** todas as fixtures operáveis só por ferramentas; origem não liberada é barrada pelo Chrome.

## Fase 4 — Agent loop (seu item 3) — só com a Fase 1 verde e o modelo da Fase 2
- [ ] Promover o eval loop: Platform real, cliente do cérebro, verificador por leitura, monitor de travado (thinking → trocar de
      degrau → `ask`), orçamentos (passos/retries/tempo), segurança e parada.
- **Saída:** AC-1, AC-2a, AC-2b e AC-3 **em fixture**, 3 repetições, nos critérios do testes §6.

## Fase 5 — Desktop e visão
- [ ] Adaptadores semânticos AT-SPI/UIA (`activate/set_text/read`), OCR `click_text`, visão `click_visual` (imagem reduzida, zoom).
- **Saída:** calculadora e editor sem visão (3/3) na sessão isolada.

## Fase 6 — Aceite ao vivo (com você presente)
- [ ] Amazon (RTX 5090), Gmail (leitura e rascunho), The Verge/Ars/TechCrunch; Yahoo depois. ≥ 2/3 por tarefa.

## Depois do MVP
- [ ] `schedule(tarefa, cron)` (notícias toda manhã) com grants só-leitura; Yahoo; modelo de ameaças formal; modelo pesado sob demanda.
