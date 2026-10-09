# Tasklist do MVP — 07/10/2026

Ordem e dependências. Detalhes: [arquitetura.md](arquitetura.md), [testes-e-selecao-de-modelo.md](testes-e-selecao-de-modelo.md).
Marque `[x]` só com evidência (teste verde, medição registrada). Cada fase tem **saída** objetiva.

**Revisão 07/10 (browser-first):** o MVP é **browser use no Chrome do usuário**. A extensão (Fase 3) é a trilha principal e começa já;
o cérebro de produção é, por padrão, **texto puro lendo DOM/a11y**, com visão só sob demanda; desktop (AT-SPI/UIA, OCR, `click_visual`)
fica na Fase 5, congelado e fora do gate do MVP. Nada do código de desktop é apagado.

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
- [x] Cenas N1 (15 categorias; parte cega) + avaliador determinístico + runner + métricas (ver testes §2).
- [x] Fixtures web locais (loja com preços, webmail, notícias, injeção, CAPTCHA, senha) em localhost.
- [x] **Eval loop fino multi-turno** (~100 linhas): alimenta a observação da fixture, executa a ferramenta contra um ambiente
      simulado com estado e devolve o resultado. Vira o núcleo do agent loop na Fase 4 (não é descartável).
- Evidência N0: 145 testes (1 skip), lint verde; 96 cenas congeladas, HTTP local e loop com oráculos testados.
- [x] Comparar com e sem decodificação restrita por schema/gramática; thinking ligado/desligado.
- Evidência: quatro condições completas do Qwen3.5 sem projetor em `results/Qwen3.5-4B-text-8192.json`, 288 decisões por condição; todas reprovaram o gate, registradas no quadro de testes.
- [x] Medir o **tamanho do snapshot** (tokens) de `read_page`/`get_page_text` nas fixtures de loja, webmail e notícias, com e sem poda/
      paginação (meta: ≤ ~1,5 mil tokens por leitura, arquitetura §3.11). Registrar em `evals/brain/results/`.
- Evidência A1: `results/snapshot-*.json` e `docs/snapshot-browser-a1.md`; máximo podado/paginado 493 tokens (Qwen3) e 481 (Qwen3.5), medidos com os tokenizadores GGUF.
- **Saída:** `uv run python -m evals.brain run --model X` gera `results/X.json`; N0 verde.

## Fase 2 — Seleção do modelo (seu item 2), um a um
- [x] Instalados primeiro: Qwen3-4B-2507 (**linha de base só-texto**, entrada DOM) e Qwen3.5-4B (texto puro, sem projetor, e com
      projetor) → registrar.
- Evidência A2: três configurações completas em `results/*-8192.json`, registradas no quadro de testes; nenhum modelo passou o gate N1.
- [ ] **Matriz modelo × contexto × VRAM** (por processo) para 4B e para uma classe 7–9B só-texto Q4: maior contexto estável e
      acerto N1 em cada ponto (KV q8_0/q4_0, offload parcial só se p95 ≤ 5 s). Pergunta a responder: modelo maior ou contexto maior?
- [ ] Baixar (com autorização, tamanho e origem na pergunta): um 7–9B só-texto; Fara1.5-4B (forte em web); Qwen3-VL-4B como controle.
      GUI-Owl-1.5-4B desce de prioridade (treinado para GUI por pixels; a vantagem some com entrada DOM).
- [ ] Visão sob demanda: VLM pequeno (ou o mesmo 4B com projetor na CPU/carregado só quando preciso) para `screenshot` em páginas
      com DOM inconclusivo e para checagem antes de ação irreversível. Medir o custo; decidir se entra no MVP.
- [ ] Quantizações do melhor (Q4_K_M vs Q5/IQ4); VRAM de pico por processo.
- **Saída:** modelo escolhido pelo gate N1 (≥85% nas cenas cegas, ação proibida = 0, JSON ≥98%, p95 ≤5 s, VRAM ≤5 GB).

## Fase 3 — Extensão Chrome + native host + permissões (**trilha principal do MVP**; começa já, depende só da Fase 0)
- [x] Native host (stdio JSON ↔ socket local autenticado) e manifesto por SO (Linux/Windows).
- Evidência: N0 da ponte (auth, framing, subprocesso, manifestos Linux/Windows) verde; integração real Linux em `evals/browser/results/chrome-linux.json`. Windows executável ainda não medido.
- [ ] Extensão MV3: `read_page` com refs, `find`, `click`, `form_input`, `get_page_text`, `navigate`, abas/grupo "TaskPilot",
      screenshot; `optional_host_permissions` por origem.
- [ ] Permissões: allowlist por origem (read/interact, validade), categorias sensíveis, confirmação por ação, `handoff`,
      botão de parar, log com screenshots; CLI `taskpilot permissions`.
- [x] Snapshot compacto: árvore interativa com refs, poda de ruído, `scope` e paginação (`next`), dentro do orçamento da Fase 1.
      Marcar texto de página como dado não confiável; detectar elementos ocultos/cobertos (anti-injeção por DOM enganoso).
- [x] Testes N2 em perfil descartável (`--user-data-dir` temporário, `--load-extension`) contra as fixtures.
- Evidência parcial da Fase 3: 87 verificações reais das seis ferramentas em `evals/browser/results/chrome-linux.json`; snapshots reais até 444 tokens Qwen3.5 (re-medido em 08/10 após compactar ids e excertos) em `results/snapshot-Chrome-Qwen3.5-4B.json`. Chrome 154 ignorou `--load-extension`; fallback de teste `Extensions.loadUnpacked` registrado em `evals/browser/README.md`. Abas/grupo, screenshot-ferramenta e política completa continuam pendentes.
- **Saída:** todas as fixtures operáveis só por ferramentas; origem não liberada é barrada pelo Chrome.

## Fase 4 — Agent loop (seu item 3) — só com a Fase 1 verde, a Fase 3 operando as fixtures e o modelo da Fase 2
- [ ] Promover o eval loop: Platform **de browser** (ponte da extensão), cliente do cérebro, verificador por leitura, monitor de travado (thinking → trocar de
      degrau → `ask`), orçamentos (passos/retries/tempo), segurança e parada.
- Evidência parcial (08/10): Platform de browser (`BrowserEnv`), contrato `agent-1`, episódios reais com o Qwen3.5-4B na extensão em Chrome
  descartável e verificação independente (estado HTTP da fixture): ver testes §7. Ainda sem monitor de travado, sem produto
  orquestrador, sem parada/segurança executável, sem 7–9B, e fora do aceite (fixture, não AC ao vivo).
- [ ] Confirmação humana e, se a Fase 2 aprovar, checagem por screenshot antes de enviar/apagar/comprar (fora do alcance do modelo).
- **Saída:** AC-1, AC-2a, AC-2b e AC-3 **em fixture**, 3 repetições, nos critérios do testes §6.

## Fase 5 — Desktop e visão (adiada: só depois do aceite web em fixture; fora do gate do MVP)
- [ ] Adaptadores semânticos AT-SPI/UIA (`activate/set_text/read`), OCR `click_text`, visão `click_visual` (imagem reduzida, zoom).
- **Saída:** calculadora e editor sem visão (3/3) na sessão isolada.

## Fase 6 — Aceite ao vivo (com você presente)
- [ ] Amazon (RTX 5090), Gmail (leitura e rascunho), The Verge/Ars/TechCrunch; Yahoo depois. ≥ 2/3 por tarefa.

## Depois do MVP
- [ ] `schedule(tarefa, cron)` (notícias toda manhã) com grants só-leitura; Yahoo; modelo de ameaças formal; modelo pesado sob demanda.
