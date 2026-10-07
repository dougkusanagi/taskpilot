# Rodar e testar no Linux (GNOME/Wayland) — 06/10/2026

Estado: **caminho Linux funcional de ponta a ponta numa sessão de teste isolada** (runtime próprio +
perfil P1 + portal + AT-SPI). Continua opt-in (`--platform wayland`), não muda o default B1 nem o
caminho Windows, e **não aprova gates R3/R4/R5** (ver o fim: o que foi e o que não foi medido).

## Uso real (no seu desktop)

```bash
sudo apt install python3-gi gir1.2-atspi-2.0 gir1.2-gst-plugins-base-1.0 gstreamer1.0-pipewire
uv sync --extra ocr
uv run python main.py "abra o editor de texto e escreva ola mundo" \
  --profile P1 --platform wayland --max-steps 8
```

- O runtime sobe sozinho: reaproveita o `llama-server` do LM Studio (CUDA 12 + libs do `vendor/`),
  senão o do `PATH`, senão baixa o release fixado. Os GGUFs do P1 vão para `models/`. Outro binário:
  `runtime.llama_server` no `config.json` ou `TASKPILOT_LLAMA_SERVER`.
- **O GNOME abre um diálogo "Área de trabalho remota". Ligue "Permitir interação remota" antes de
  "Compartilhar"** — vem DESLIGADO; sem isso a sessão só captura e a ponte recusa com a instrução
  (`devices=...`). "Lembrar desta seleção" evita o diálogo nas próximas execuções.
- **Parada de emergência:** `ctrl+alt+esc` **não existe** no Linux (a lib `keyboard` pede root e não
  captura globalmente no Wayland). Use o botão "parar compartilhamento" do GNOME (barra superior,
  ícone laranja): a ponte percebe o `Closed` do portal e o loop aborta. Ctrl+C no terminal também.
- Sem `--platform`, o Linux falha na hora (antes: rodava minutos de modelo e só falhava no 1º clique).
- Não use o PC enquanto o agente age, como no Windows.

## Como o agente "vê" no Linux

| Canal | Fonte | Entrega |
| --- | --- | --- |
| Texto (planner) | AT-SPI (`platform_backend/a11y_atspi.py`, na ponte) | título da janela ativa, elementos nomeados (`Button:7`, `Edit:campo de texto="..."`), texto do campo focado |
| Localização (clique) | visão (MAI-UI-2B) sobre o frame do portal | ponto 0..1 no frame |
| Frame | portal ScreenCast (PipeWire → appsink) | mesma geometria do input |

O AT-SPI no Wayland **informa mas não localiza**: não há posição de janela, então todo item sai com
`nobounds` e `uia_click "7"` vira miss simples → o loop escala para `visual_action "Click 7"`. Nunca
clica em (0,0). Janela ativa: estado `ACTIVE` > filho `FOCUSED` > mais recente (palpite, declarado em
`how`). O campo focado cai para "o único campo editável" quando o compositor não marca `FOCUSED`.

## Sessão de teste isolada (equivalente Linux do Windows Sandbox)

Testes com cliques reais **nunca** no desktop de trabalho. `scripts/linux-test-session.sh` sobe um
GNOME Shell headless com D-Bus, `XDG_RUNTIME_DIR`, HOME, PipeWire e socket Wayland próprios
(`/run/user/UID/taskpilot-test-session`); uma por vez.

```bash
scripts/linux-test-session.sh start 1280x800       # ~3 s
scripts/linux-test-session.sh approve 90 &          # aprova o diálogo (liga a interação) por AT-SPI
scripts/linux-test-session.sh run -- .venv/bin/python -u main.py "..." --profile P1 \
  --platform wayland --max-steps 8 --record
scripts/linux-test-session.sh shot /tmp/tela.png    # confira o estado FINAL por conta própria
scripts/linux-test-session.sh stop                  # desmonta o FUSE do document-portal e apaga
```

- Os modelos rodam no host (`python -m server --profile P1` ou o auto-start do `main.py`); o agente
  dentro da sessão só os alcança por `127.0.0.1`.
- O token do portal fica no HOME privado da sessão: a 2ª execução não abre diálogo.
- `linux_session_tool.py` usa a API D-Bus direta do Mutter só para screenshot/teclas/aprovação **dentro
  da sessão descartável**; o agente continua usando o portal.
- Armadilhas já resolvidas no script: o ibus ativa o `xdg-desktop-portal` antes do compositor
  (o script derruba esses dois processos pelo dono do nome no barramento privado, nunca por padrão
  de nome); o GNOME headless nasce no Overview (o `start` envia Esc); acessibilidade ligada antes
  do primeiro app GTK; mount FUSE do document-portal.
- Evite `pkill -f <padrão>` em comandos que contenham o próprio padrão: derruba o seu shell.

## O que a verificação ponta a ponta achou e corrigiu (06/10)

1. `server.py` recusava o Linux → runtime Linux (descoberta + download de fallback).
2. **A ponte nunca tinha subido com o portal real**: `fakesink ... signal-handoff` não existe
   (é `signal-handoffs`); agora `appsink` com 1 buffer.
3. Diálogo do portal com interação remota desligada → sessão só-captura sem aviso → `devices`
   verificado, com a instrução acionável.
4. **O 1º evento de teclado após abrir a sessão é descartado** (`XYZ789` virava `YZ789`; com um
   Shift antes, saiu inteiro) → aquecimento com Shift ao abrir a sessão.
5. Planner cego (sem UIA): repetia `open("notepad")` até o anti-loop → observação AT-SPI.
6. **Falso sucesso**: `confirm_effect` recebia a nota inteira (com o título da janela) como "valor do
   campo"; o título do editor ecoa o texto, então `la mundoola mundo` foi aceito como "valor digitado
   visível" → só a leitura do campo confirma `type`.
7. Histórico do planner dizia `visual "None" -> click` e nada do efeito → rótulo com o alvo real e
   `ui changed: campo de texto "6"->"67"` na nota de verificação.
8. Parada de emergência anunciada (`ctrl+alt+esc`) mas inexistente no Linux → mensagem honesta +
   `Closed` do portal aciona o stop.

## Medido e não medido

Medido (RTX 2060 6 GB, Ryzen 5 4500, P1 = Qwen3-4B-Instruct-2507 + MAI-UI-2B, ~5,5 GB de VRAM):

- "abra o editor de texto e escreva ola mundo": **concluiu em ~8 s**, texto conferido por screenshot
  independente (2 execuções após as correções).
- Planner ~0,4 s por decisão; **visão ~9–10 s por clique** (encoder de imagem na CPU: o encoder na
  GPU não cabe junto do planner em 6 GB — o processo aborta por falta de VRAM).
- Calculadora ("12 vezes 7"): a visão acerta a tecla (7, 8 e "=" caem dentro do botão), mas o planner
  clicou no 7 em loop antes das correções 6 e 7; reavaliação em `docs/teste-sessao-linux.md` §Pendências.

Não medido: tarefas web (Chrome com `--force-renderer-accessibility`), multi-monitor, escala HiDPI
fracionária, o Windows (nenhuma linha do caminho Windows foi exercitada além dos 532 testes), nenhuma
bateria ≥30 cenas com modelos reais, nada do R3/R4/R5.

## Pendências

- Latência da visão (tamanho da imagem × acerto): ver `docs/teste-sessao-linux.md` seção final quando
  a medição de grounding por tamanho for fechada.
- Janela ativa conhecida só por tamanho (AT-SPI): sem posição não há recorte da janela para a visão.
- Chrome/Firefox ainda não testados na sessão isolada.
