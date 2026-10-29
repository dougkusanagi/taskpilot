# Plano: E2E isolado do Ubuntu termina com `done` de forma repetível

Tipo: tarefa

**Prioridade: concluir este plano ANTES de continuar a implementação do sistema**
(etapas R0–R6 do plano vigente, novas features, troca de modelos ou de default).

## Por quê vem antes

- É o único teste real, de ponta a ponta, com mouse e teclado reais que podemos repetir nesta
  máquina sem tocar o desktop de trabalho. Os testes offline e os benchmarks não provam integração
  (ver AGENTS.md). Até agora nenhum run real chegou a `done` de forma estável.
- Cada lote N=10 já revelou defeitos do código compartilhado (foco após o portal, `focus` na
  janela ativa, ordem dos acentos no IBus, teto da visão), e não só defeitos do teste. Construir
  mais coisas sem esse sinal acumula defeitos que nenhum teste unitário pega.
- O harness é a régua para medir as próximas mudanças (`why`, tetos, prompts). Sem uma linha de
  base estável, nenhum número de R3–R5 é confiável.
- Este trabalho não aprova nenhum gate R3–R6 e não muda o default B1.

## Objetivo

Tarefa: "No Editor de Texto já aberto, use o mouse para clicar no botão + (nova aba), depois
digite exatamente: Olá, Ubuntu!". Critério: o harness, com N=10 sessões limpas, mostra uma taxa
de `done` com texto final exato (lido por AT-SPI) medida e explicada. Cada falha tem uma causa
registrada. A meta é ≥ 8/10; se o limite for do modelo, registre isso com os logs e pare para
perguntar antes de trocar o modelo ou o default.

## Como rodar

```bash
uv run python -m evals.ubuntu_isolated --runs 10 --max-steps 6 --vision-max-tokens 3072 --tag <nome>
# saída: runs/ubuntu-iso-<tag>-<data>/{results.md,results.json,infra.json,sNN*/agent.log,...}
uv run python -m unittest discover -s tests   # suíte (560 verdes em 29/10, 1 skip)
uv run ruff check
```

- Um lote leva ~10–15 min. Rode em background e espere a conclusão (não use polling).
- Pastas `sNN-infraK`: o número K conta as sessões de infra repetidas até ali (falta de frame do
  PipeWire). Elas não entram na taxa. O total fica em `infra.json`.
- Regras: cliques só na sessão isolada; mate só por PID; não mate LM Studio nem processos alheios;
  não baixe pesos; commit e push no branch após cada passo, com a suíte verde; sem merge.

## Estado em 29/10 (branch `test/ubuntu-isolated-e2e`)

Commits: `836bc44` foco após o portal (Super+Esc, confirmado por AT-SPI) · `9bf08e4` harness ·
`69413a6` veto de `focus` na janela já ativa (guard 2c, `loop._already_active_note`) ·
`0118bd5` espera na 1ª entrada Unicode + harness robusto · `30def19` correção do harness.

**Sem commit no working tree:** `platform_backend/wayland.py` com uma pausa ANTES de cada
entrada Unicode (`UNICODE_COMMIT_S = 0.3`). Ainda não foi medida (o lote `t3072b` parou após
1 run, que deu `done`).

| Lote (runs/ubuntu-iso-…) | done + texto certo | Causas |
|---|---|---|
| base-…151610 | 3/10 | "Oál" 3, infra 3, `focus` repetido 1 |
| fix1-…152536 | 0/10 | o título da pasta mudou a decisão do planner; infra 4 |
| fix2-…154543 | 1/10 | "Oál", visão truncada no passo 0, infra |
| t512-…161122 | 2/10 | visão truncada no passo 0 em 6/10 |
| t1024-…162347 | 2/6 (parou) | truncamento 2, clique repetido na aba |
| t3072-…164217 | 0/10 | sem truncamento; "Oál" em 7/10 → digita 3× |

## Passos

- [x] 1. Foco após o portal no backend Wayland, com teste passivo.
- [x] 2. Harness versionado (`evals/ubuntu_isolated.py`) + N=10 com tabela.
- [ ] 2b. Medir a pausa antes da entrada Unicode: lote N=10, teto 3072. Se o "Oál" sumir,
      escreva o teste passivo (a pausa vem antes da sequência Ctrl+Shift+U) e faça commit.
      Se não sumir, a próxima opção é confirmar por AT-SPI depois de digitar.
- [ ] 3. Teto da visão: 512 e 1024 truncam (raciocínio de 122–2719 tokens); 3072 acertou 12/12
      offline. Registrar a latência (5–13 s) e PROPOR o teto ao usuário; não mudar o default sozinho.
- [ ] 4. `why` (planner.features): `evals.trajectory_bench` com e sem `why`, e harness N=10 sem
      `why` (`--features dynschema,fewshot,plan`). Propor o padrão só com os números.
- [ ] 5. Falha restante do modelo: o planner clica na aba "Novo documento" em vez de digitar.
      Medir a frequência depois de 2b; investigar o prompt/estado no código compartilhado, sem
      heurística específica do Editor de Texto.
- [ ] 6. Relatório `docs/teste-ubuntu-isolado-2026-10-28.md` (nova seção de 29/10 com as tabelas
      e logs), fragmento `changelog/unreleased/wayland-foco-portal.md`, `MEMORY.md` (quirks
      duráveis abaixo). Depois apagar este plano e o aviso no AGENTS.md, antes de integrar.

## Fatos descobertos (levar ao relatório e ao MEMORY.md)

- O gnome-shell headless começa na visão geral: nada fica ativo até o Esc do preparo (harness).
- Depois de aprovar o portal, nenhuma janela fica ativa; Super+Esc reativa a janela do topo.
- LFM3-VL raciocina antes do JSON; o tamanho varia muito em telas quase iguais. Com o raciocínio
  desligado, a visão leva 88 ms, mas errou 12/12 pontos.
- O Qwen3-4B (temperatura 0) muda de decisão só porque muda o nome da pasta no título da janela.
- Com 2 CPUs e nice 10, o PipeWire não entrega o 1º frame em ~1/3 das sessões (é infra).
- Walk AT-SPI e Gst no processo do controlador deram segfault e travamento: use subprocessos.

## Tentativas que falharam

- Alt+Tab para devolver o foco: sem janela ativa, não faz nada.
- Pausa só DEPOIS da entrada Unicode (0,12 s, 0,35 s, 1 s na 1ª): o "Oál" continua no agente.
  O "l" anterior sai depois do "á". Fora do agente (probes `/tmp/tp-type-probe.py`), o erro só
  apareceu na 1ª entrada da sessão.
- Visão sem raciocínio (`enable_thinking: false`): rápida, mas sempre erra o "+".
- Tetos de 512 e 1024: truncam (finish_reason=length → sem clique, `stuck` no passo 0).

## Dúvidas abertas

- Aceitar visão com teto 3072 (mais lenta) ou buscar outra forma de limitar o raciocínio?
- A meta ≥ 8/10 é alcançável com Qwen3-4B, ou o limite está no modelo?

## Próximo passo

Lote 2b iniciado em `runs/ubuntu-iso-t3072c-20261029-171212` (N=10, teto 3072).
Ler `results.md` + os `agent.log` ao terminar; não concluir a medição antes disso.
A pausa já está commitada em `c044b86`; regressão passiva adicionada verifica a ordem
da espera antes de Ctrl+Shift+U. Suíte anterior: 560 testes, 1 skip; teste afetado: 28 verdes.
