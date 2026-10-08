# Arquitetura do MVP (normativa) — 07/10/2026

Fonte única do desenho. Racional e números: [arquitetura-do-zero-2026-10-07.html](arquitetura-do-zero-2026-10-07.html).
Seleção do modelo e testes: [testes-e-selecao-de-modelo.md](testes-e-selecao-de-modelo.md).
Mudou a arquitetura → mude este arquivo no mesmo commit.

Implementação parcial de browser: [extensao-minima.md](extensao-minima.md) e
[native-host.md](native-host.md). Seis ferramentas já existem; o orquestrador e o
restante das permissões da Fase 3 seguem pendentes.

## 1. Requisitos

| Item | Valor |
| --- | --- |
| GPU | 6 GB de VRAM; ~5 GB utilizáveis (desktop ~1 GB). Pico de VRAM medido por processo, não pelo total do `nvidia-smi` |
| Execução | 100% local, sem API obrigatória; Windows (UIA) e Linux/Wayland (AT-SPI + portal) |
| Latência | decisão p95 ≤ 5 s; tarefa curta p50 ≤ 60 s. Tarefas difíceis podem ser mais lentas (modo thinking) |
| Prioridade | **Browser use no Chrome do usuário (extensão + permissões) é a trilha principal do MVP**; desktop (AT-SPI/UIA, OCR, visão) é fase posterior |
| Segurança | ações irreversíveis exigem confirmação humana; parada de emergência sempre disponível |

## 2. Princípios

1. **Interface antes do modelo.** O modelo pequeno acerta quando a ferramenta é fácil (nome exato, texto como texto); mouse
   cego é o último recurso.
2. **Um cérebro residente.** Um único modelo planeja. No browser use ele lê **texto** (DOM/a11y), então o padrão é **só-texto**; a
   visão é opcional e sob demanda (projetor na CPU ou carregado quando preciso). A VRAM liberada vira contexto ou um modelo maior
   (7–9B), decidido por medição (testes §3). Sem planner + grounder duplicados.
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
No **browser use (MVP)** a escada é: **A** = DOM/a11y por `ref` (§3.9) → **C** = `screenshot` como reserva e como checagem independente.
O degrau B (OCR) e os adaptadores de desktop abaixo ficam para a Fase 5 da tasklist.

- **A · Semântica:** `activate(ref)`, `set_text(ref, text)`, `type(text)`, `key(combo)`, `read(ref)`, `wait_for(text)`.
- **B · Texto na tela:** `click_text(label)` por OCR com caixas (recusa ambiguidade e ausência).
- **C · Visão:** `click_visual(description)` — o próprio modelo localiza no screenshot (imagem reduzida, ~768 px).
- Sempre: `handoff(motivo)` (passa o controle ao usuário: CAPTCHA, login, 2FA, pagamento; traz a aba à frente, notifica, espera
  "continuar" com timeout e **relê o estado** para confirmar que o bloqueio sumiu), `ask(question)`, `done(answer, evidence)`, `fail(reason)`. `answer` é a resposta ao usuário (ex.: o menor preço);
  `evidence` lista as leituras que a sustentam (ref + url/trecho). Resposta sem evidência rastreável é vetada.
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
llama.cpp padrão (sem fork), **um** `llama-server`, modelo Q4_K_M, KV q8_0, contexto ~8192 (a matriz modelo × contexto × VRAM da
tasklist Fase 2 define o valor final). Projetor de visão (Q8_0) só se a visão sob demanda for aprovada, fora da GPU quando possível.
Binário/descoberta: `server.py`. Segundo processo só para tier pesado sob demanda (troca, nunca coexistente).

### 3.9 Browser use: o Chrome do próprio usuário
Requisito: agir **no Chrome do usuário** (continuar abas, usar contas logadas), nunca num navegador embutido nem num perfil à parte.
- **Por que não CDP:** desde o Chrome 136, `--remote-debugging-port` é ignorado no perfil padrão (proteção contra roubo de
  sessão). Perfil separado perderia os logins, que é o ponto do requisito.
- **Como:** extensão MV3 "TaskPilot Bridge" (Chrome/Chromium/Edge/Brave) + **native messaging host** (executável do projeto,
  `allowed_origins` restrito ao ID da extensão) falando JSON por stdio com o orquestrador (socket local autenticado).
  A extensão usa `tabs`, `scripting` (conteúdo: árvore compacta com refs, leitura de texto, eventos reais) e `debugger` só
  quando precisa de screenshot/input confiável (o Chrome mostra o aviso de depuração).
- **Vocabulário** (mesmo dos adaptadores de desktop): `read_page(filter=interactive)`, `find(descrição)`, `click(ref)`,
  `form_input(ref, valor)`, `get_page_text`, `navigate(url)`, `tabs_*`, `screenshot(scale)`, `scroll_to(ref)`, `wait_for(texto)`.
  Refs valem só para a leitura em que nasceram. Screenshot é sob demanda; coordenadas só no degrau C.
- **Escopo de abas:** o agente atua só em abas que criou ou que o usuário **compartilhou** explicitamente; grupo de abas
  "TaskPilot" visível. Continuar uma tarefa do usuário = ele compartilha a aba.

### 3.10 Permissões e segurança do browser use
Padrão **negar**. Camadas independentes (defesa em profundidade):
1. **Allowlist por origem** (`esquema+eTLD+1`) em arquivo local do usuário (`taskpilot permissions list|allow|revoke`), com
   nível `read` (só observar) ou `interact` (clicar, preencher, navegar dentro da origem) e validade (esta tarefa / sessão
   do Chrome / sempre). A extensão **também** pede `optional_host_permissions` por origem: o próprio Chrome impede tocar em
   site não liberado, mesmo que o orquestrador erre.
2. **Categorias sensíveis** (bancos, pagamentos, e-mail, gerenciador de senhas, painéis de administração, governo): negadas
   por padrão; liberar exige modo explícito "confirmar cada ação".
3. **Confirmação humana por ação**, independente de grant: enviar mensagem/e-mail, publicar, apagar, comprar/pagar, alterar
   conta ou segurança, aceitar termos/OAuth, baixar arquivo, instalar.
3b. **CAPTCHA e verificações anti-robô: o agente não tenta resolver nem contornar.** Detecta (iframes reCAPTCHA/hCaptcha/Turnstile,
    páginas "Robot Check") e faz `handoff`. Motivos: viola os termos dos sites e arrisca bloqueio da conta; modelos pequenos não
    resolvem desafios modernos (muitos são comportamentais). Usar o Chrome real e logado, em ritmo humano e sem abas em paralelo,
    já reduz a frequência.
4. **Segredos fora do alcance:** o agente nunca lê nem digita senha, código 2FA ou campo de cartão (pede ao usuário e espera);
   cookies/localStorage não são expostos ao modelo; o modelo não executa JavaScript próprio (só scripts fixos do projeto).
5. **Injeção de prompt:** texto de página é **dado não confiável**, marcado como tal no prompt; instruções vindas da página
   nunca são seguidas; dado lido numa origem **não** é digitado em outra sem confirmação; navegação para origem não liberada
   (inclusive por redirecionamento) para e pergunta.
6. **Transparência e corte:** indicador permanente (grupo de abas + selo da extensão), log local de ações com screenshots,
   botão/atalho de parar que revoga a sessão. Tudo roda local: nada de página ou credencial sai da máquina.
Pontos abertos (ver §4) incluem limite de taxa, revisão periódica dos grants e modelo de ameaças formal.

### 3.11 Orçamento de contexto e paginação
Páginas reais são grandes (lista de resultados, caixa de entrada, home de notícias). Regras: cada leitura devolve ≤ ~1,5 mil
tokens; `read_page`/`get_page_text` aceitam `scope` (ref) e **paginam** (`next`); o orquestrador mantém o resultado dos itens já
lidos em uma memória estruturada (ex.: lista de {título, preço, url}) em vez de reenviar texto cru; o contexto do modelo é
~8 mil tokens. Extração (menor preço, resumo do dia) é feita lendo página a página e acumulando itens com evidência.

## 4. Decisões em aberto
- **Ação semântica (AT-SPI/UIA) permitida além de mouse/teclado físicos:** assumida como SIM neste desenho; o antigo
  `AGENTS.md` proibia. Confirmar com o dono do produto.
- **Browser:** distribuição da extensão (modo desenvolvedor vs. Chrome Web Store); Firefox fora do MVP; modelo de ameaças
  formal e política de revisão dos grants. Navegador = Chrome/Chromium; **sem** navegador embutido.
- Qual modelo é o cérebro: decidido pela bateria (ver testes), não por benchmark de terceiros.

## 5. Estado do código (o que cobre cada parte)
| Parte | Hoje |
| --- | --- |
| Runtime | `server.py` (Windows/Linux) — falta caminho de modelo único |
| Platform Linux | `platform_backend/` (portal, AT-SPI) — falta `activate/set_text/read` semânticos |
| Platform Windows | `uia.py`, `actions.py`, `obs.py` — falta contrato único |
| OCR | `ocr.py` |
| Segurança | `safety.py`, `overlay.py` |
| Extensão Chrome + native host + permissões | **a criar** (§3.9–3.10) |
| Orquestrador, Observation/Result novos, verificador, cérebro (cliente), monitor | **a criar** |
| `schemas.py`, `config.py` | herdados; enxugar quando os contratos novos existirem |

## 6. Ordem de implementação (prioridade)
1. **Bancada:** `evals/brain/` (cenas N1) + fixtures web locais + sessão isolada. Sem isso nada é aceito.
2. **Extensão Chrome + native host + permissões** (§3.9–3.10), testada em perfil descartável com as fixtures. **Topo do MVP.**
3. **Orquestrador + cliente do cérebro + verificador + monitor** com o vocabulário de browser.
4. **Seleção do modelo** (um a um) sobre as cenas e as tarefas de aceite web (ver testes §6).
5. **Adiado:** adaptadores de desktop semânticos (AT-SPI/UIA) e escada OCR/visão para apps que não são web.
6. Aceite ao vivo no Chrome real, com o usuário presente (Amazon, e-mail, notícias).

## 7. Futuro (fora do MVP, já previsto)
- **Tarefas agendadas** (ex.: resumo de notícias toda manhã): tool `schedule(tarefa, cron)`. Execução **sem o usuário** exige
  grants prévios e restritos a `read`; sem `handoff` possível — se aparecer CAPTCHA/login, adia e notifica; resultado entregue
  como arquivo/notificação; só roda com a máquina ligada e o Chrome aberto. Ações irreversíveis nunca rodam sem confirmação.
