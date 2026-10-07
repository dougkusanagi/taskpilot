# Arquitetura do MVP (normativa) — 07/10/2026

Fonte única do desenho. Racional e números: [arquitetura-do-zero-2026-10-07.html](arquitetura-do-zero-2026-10-07.html).
Seleção do modelo e testes: [testes-e-selecao-de-modelo.md](testes-e-selecao-de-modelo.md).
Mudou a arquitetura → mude este arquivo no mesmo commit.

## 1. Requisitos

| Item | Valor |
| --- | --- |
| GPU | 6 GB de VRAM; ~5 GB utilizáveis (desktop ~1 GB). Pico de VRAM medido por processo, não pelo total do `nvidia-smi` |
| Execução | 100% local, sem API obrigatória; Windows (UIA) e Linux/Wayland (AT-SPI + portal) |
| Latência | decisão p95 ≤ 5 s; tarefa curta p50 ≤ 60 s. Tarefas difíceis podem ser mais lentas (modo thinking) |
| Segurança | ações irreversíveis exigem confirmação humana; parada de emergência sempre disponível |

## 2. Princípios

1. **Interface antes do modelo.** O modelo pequeno acerta quando a ferramenta é fácil (nome exato, texto como texto); mouse
   cego é o último recurso.
2. **Um cérebro residente.** Um único VLM de ~4B planeja e, quando pedido, enxerga. Sem planner + grounder duplicados.
3. **Python orquestra e verifica; nunca decide o objetivo.** Quem escolhe a próxima ação é o modelo.
4. **Verificação independente.** Efeito confirmado só por **leitura do estado** (campo/elemento), nunca pelo título da janela
   nem pela própria ação enviada. `done` exige referências a leituras confirmadas.
5. **Eval primeiro.** Nada entra sem teste dos níveis N1–N3 (ver testes).

## 3. Componentes

```
Usuário → Orquestrador (Python) ⇄ Cérebro (llama.cpp, 1 modelo)
               │
               ├─ Percepção:  Platform.observe()  → Observation (texto) [+ screenshot sob demanda]
               ├─ Ferramentas: escada A → B → C
               ├─ Verificador: lê o estado após cada ação
               └─ Segurança:  vetos, confirmações, parada
```

### 3.1 Platform (um contrato, dois adaptadores)
`observe() → Observation`, `activate(ref)`, `set_text(ref, text)`, `type(text)`, `key(combo)`, `read(ref)`,
`click_xy(x, y)`, `screenshot()`, `launch(app)` (whitelist). Windows = UIA (`uia.py`); Linux = AT-SPI + portal
(`platform_backend/`). Adaptador que não sabe fazer algo **diz que não sabe** (erro tipado), nunca finge sucesso.

### 3.2 Observation (o que o modelo lê, em texto)
```
window: "Calculadora"  app: gnome-calculator  focus: e4
e4  Edit    "campo de texto"  value="67"  focused
e7  Button  "7"
e9  Button  "="
coverage: ok 61/120 elementos (atspi, 85 ms)
```
Ids `eN` são válidos só para essa observação (IDs obsoletos são recusados). Itens sem coordenadas (AT-SPI/Wayland) existem e
são acionáveis por `activate`; `click_xy` nunca recebe (0,0) por falta de dado.

### 3.3 Escada de ferramentas (sempre A → B → C)
- **A · Semântica:** `activate(ref)`, `set_text(ref, text)`, `type(text)`, `key(combo)`, `read(ref)`, `wait_for(text)`.
- **B · Texto na tela:** `click_text(label)` por OCR com caixas (recusa ambiguidade e ausência).
- **C · Visão:** `click_visual(description)` — o próprio modelo localiza no screenshot (imagem reduzida, ~768 px).
- Sempre: `ask(question)`, `done(evidence_refs)`, `fail(reason)`.
Cada degrau só é tentado se o anterior não achou o alvo; o orquestrador registra qual degrau resolveu.

### 3.4 Contrato do cérebro
Entrada: objetivo, memória curta (últimas N ações com **resultado verificado**), `Observation` atual, ferramentas
disponíveis. Saída: **um** objeto JSON validado por schema (uma variante por ferramenta; `done` só existe se há leitura
confirmada). Inválido → o orquestrador devolve o erro e repete (teto de retries). Thinking desligado por padrão.

### 3.5 Verificador
Após cada ação: `read` do estado relevante → `Result{sent, confirmed, evidence_ref, delta}`. O delta (o que mudou na UI, ex.:
`campo de texto "6"->"67"`) volta ao modelo. Confirmação por título/ação enviada é proibida.

### 3.6 Monitor de travado
Mesma ação repetida ≥ 3× sem delta útil, ou nenhum progresso em K passos → escalar: (1) ligar thinking nessa decisão;
(2) trocar de degrau; (3) `ask` ao usuário. Teto de passos, de retries e de tempo por tarefa.

### 3.7 Segurança
Allowlist de apps e de CLIs (sem shell livre); confirmação humana para enviar/apagar/comprar/instalar; escolha entre itens
que o pedido não distingue → `ask`. Parada: hotkey (Windows), botão "parar compartilhamento" do GNOME (Linux), Ctrl+C.
Testes com cliques somente em ambiente isolado (Sandbox no Windows; `scripts/linux-test-session.sh` no Linux).

### 3.8 Runtime
llama.cpp padrão (sem fork), **um** `llama-server`, modelo Q4_K_M + projetor Q8_0 na GPU, KV q8_0, contexto ~8192.
Binário/descoberta: `server.py`. Segundo processo só para tier pesado sob demanda (troca, nunca coexistente).

## 4. Decisões em aberto
- **Ação semântica (AT-SPI/UIA) permitida além de mouse/teclado físicos:** assumida como SIM neste desenho; o antigo
  `AGENTS.md` proibia. Confirmar com o dono do produto.
- Qual modelo é o cérebro: decidido pela bateria (ver testes), não por benchmark de terceiros.

## 5. Estado do código (o que cobre cada parte)
| Parte | Hoje |
| --- | --- |
| Runtime | `server.py` (Windows/Linux) — falta caminho de modelo único |
| Platform Linux | `platform_backend/` (portal, AT-SPI) — falta `activate/set_text/read` semânticos |
| Platform Windows | `uia.py`, `actions.py`, `obs.py` — falta contrato único |
| OCR | `ocr.py` |
| Segurança | `safety.py`, `overlay.py` |
| Orquestrador, Observation/Result novos, verificador, cérebro (cliente), monitor | **a criar** |
| `schemas.py`, `config.py` | herdados; enxugar quando os contratos novos existirem |
