# Protótipo Wayland (Ubuntu/GNOME) — 02/10/2026

Estado: **protótipo opt-in**. Windows e default (B1) inalterados. Nada aqui aprova gates R3/R4/R5.

## O que existe

`platform_backend/` (`__init__.py` escolhe, `wayland.py` cliente, `wayland_bridge.py` ponte):

- **Captura + input pelo mesmo portal** (`xdg-desktop-portal`: RemoteDesktop + ScreenCast). O frame
  e o ponteiro usam o mesmo stream, então não há mapeamento tela↔frame para errar.
- A ponte roda no Python do **sistema** (precisa de `python3-gi` e do plugin `pipewiresrc`); o venv
  (3.12) não enxerga `gi`. Fala JSON-lines com o cliente.
- Ligado só por `--platform wayland` (ou `platform.backend` / `TASKPILOT_PLATFORM`). Sem opt-in, o
  Linux continua recusando ações físicas e a suíte de testes nunca abre sessão no desktop.
- Seams mínimos em `actions.py`, `obs.py`, `tools.py`, `uia.py`; nada muda no caminho Windows.

## Medido em 02/10 nesta máquina (RTX 2060 6 GB, GNOME/Wayland, Ubuntu 26.04)

| Verificação | Resultado |
| --- | --- |
| AT-SPI lê a árvore (15 apps) | ok; mas as posições vêm em `(0,0)` p/ janelas Wayland nativas |
| Screenshot com `mss`/X11 | 100% preto (inútil no Wayland) |
| `grim` | não funciona no GNOME (sem `wlr-screencopy`) |
| Screenshot direto pelo portal | negado pela permission store p/ `com.anthropic.Claude` |
| Chrome aberto do usuário | `--ozone-platform=wayland` (invisível p/ xdotool) |
| P1 com llama-server CUDA do LM Studio | sobe; **5340 MiB** de VRAM total (planner+visão) |
| Testes | 486 OK (11 novos, só stubs/puros), ruff limpo |

**Atualização em 28/10:** diálogo do portal e frame PipeWire de 1280×800 confirmados em GNOME
isolado após corrigir `signal-handoffs` no pipeline. Input pelo portal e loop completo com
modelos continuam pendentes. O cliente recusa input sem a permissão correspondente e pede
para ativar “Allow Remote Interaction” no diálogo. Ver [relatório](validacao-ubuntu-2026-10-28.md).

## Setup Ubuntu

```bash
sudo apt install python3-gi gir1.2-gst-plugins-base-1.0 gstreamer1.0-pipewire gstreamer1.0-plugins-base
uv sync --extra ocr
```

Runtime (atualizado em 28/10): `server.py` seleciona binários Ubuntu x86_64, com CPU por
padrão e `runtime.backend: cuda|vulkan` opcionais. `uv run python -m server` inicia os modelos;
`--no-runtime` continua disponível para usar endpoints já iniciados. A extração e execução do
binário oficial CPU b11954 foram verificadas no Ubuntu. Isso não valida o controle do desktop.
CUDA usa o pacote oficial 12.8; suporte depende do driver instalado. GGUFs são os mesmos do Windows.

## Rodar

```bash
uv run python main.py "abra o editor de texto e escreva ola mundo" --profile P1 --no-runtime --platform wayland --max-steps 6 --record
```

O GNOME pede aprovação (escolha **um** monitor; o `restore_token` em `~/.cache/taskpilot/` evita
repetir). Corte de emergência: botão "parar compartilhamento" na barra superior, ou Ctrl+C.

## Limites declarados

- Sem UIA: modo visual (`visual_action`/OCR). AT-SPI com coordenadas corretas só é confiável em
  janelas X11; leitura por AT-SPI fica para a fase seguinte.
- 1 monitor por sessão. Sem foco programático de janela (Wayland). Sem hotkey global `ctrl+alt+esc`.
- O planner ainda fala "notepad|calc|chrome": a ponte traduz p/ `gnome-text-editor`/`gnome-calculator`/
  `google-chrome` (`LINUX_APPS`). Chrome já aberto ignora `--force-renderer-accessibility`.
- Teste do cenário Amazon: não executado.
