#!/usr/bin/env bash
# Sessão GNOME/Wayland headless e DESCARTÁVEL para testes dev com cliques reais no Linux
# (equivalente do Windows Sandbox; guia em docs/teste-sessao-linux.md).
#
# Nunca toca no desktop real: barramento D-Bus, XDG_RUNTIME_DIR, HOME, PipeWire e socket Wayland
# são próprios. Só pode haver uma instância por vez. Uso:
#
#   scripts/linux-test-session.sh start [LARGURAxALTURA]   # sobe (padrão 1280x800)
#   scripts/linux-test-session.sh run -- COMANDO [ARGS...] # roda COMANDO dentro da sessão
#   scripts/linux-test-session.sh shot ARQUIVO.png         # screenshot da sessão
#   scripts/linux-test-session.sh approve                  # aprova o diálogo de compartilhamento
#   scripts/linux-test-session.sh status | stop
#
# Requer: gnome-shell (>= 46), dbus-run-session, pipewire, wireplumber, python3-gi, gstreamer
# pipewire. O diálogo do portal (compartilhar tela + controle) é aprovado por `approve` via API
# D-Bus direta do Mutter — só vale dentro desta sessão descartável.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UID_NUM="$(id -u)"
RT="/run/user/${UID_NUM}/taskpilot-test-session"
ENVF="${RT}/env.sh"
REAL_HOME="${HOME}"
DISPLAY_NAME="wayland-tp"

die() { echo "linux-test-session: $*" >&2; exit 1; }

alive() { [ -f "${RT}/pid" ] && kill -0 "$(cat "${RT}/pid")" 2>/dev/null; }

cmd_start() {
  local size="${1:-1280x800}"
  [[ "$size" =~ ^[0-9]+x[0-9]+$ ]] || die "tamanho inválido: $size (use LARGURAxALTURA)"
  alive && die "já há uma sessão de teste (use stop)"
  [ -d "$RT" ] && cleanup_dir
  command -v gnome-shell >/dev/null || die "gnome-shell não encontrado"
  mkdir -p "$RT/home/.config" "$RT/home/.cache"
  chmod 700 "$RT"

  # O dbus-daemon herda este ambiente e repassa aos serviços ativados (portais, apps): por isso
  # WAYLAND_DISPLAY/XDG_CURRENT_DESKTOP entram ANTES de subir o barramento.
  (
    export XDG_RUNTIME_DIR="$RT" HOME="$RT/home" XDG_SESSION_TYPE=wayland
    export XDG_CURRENT_DESKTOP=GNOME XDG_SESSION_DESKTOP=gnome WAYLAND_DISPLAY="$DISPLAY_NAME"
    unset DISPLAY XDG_SESSION_ID
    export TP_SIZE="$size" TP_ENVF="$ENVF" TP_REAL_HOME="$REAL_HOME"
    exec setsid dbus-run-session -- bash "$HERE/linux-test-session-inner.sh" \
      >"$RT/session.log" 2>&1
  ) &
  echo $! >"$RT/pid"

  for _ in $(seq 1 60); do
    if [ -f "$RT/ready" ]; then
      # o GNOME headless nasce no Overview (sem janelas): dispensa, senão o 1º frame não é a área
      # de trabalho. Falha aqui não impede o uso.
      ( source "$ENVF"; /usr/bin/python3 "$HERE/linux_session_tool.py" key Escape ) 2>/dev/null || true
      echo "sessão pronta: ${size} em $RT"; return 0
    fi
    alive || { tail -20 "$RT/session.log" >&2; die "a sessão morreu ao subir (log: $RT/session.log)"; }
    sleep 1
  done
  tail -20 "$RT/session.log" >&2
  die "a sessão não ficou pronta em 60 s"
}

cleanup_dir() {
  # O document-portal deixa um mount FUSE em $RT/doc: desmontar ANTES de apagar.
  if mount | grep -q " ${RT}/doc "; then
    fusermount3 -u -z "${RT}/doc" 2>/dev/null || fusermount -u -z "${RT}/doc" 2>/dev/null || true
    sleep 0.5
  fi
  mount | grep -q " ${RT}" && die "ainda há mounts em ${RT}; desmonte-os na mão (mount | grep ${RT})"
  rm -rf "$RT"
}

cmd_stop() {
  if [ -f "${RT}/pid" ]; then
    local pid; pid="$(cat "${RT}/pid")"
    # setsid fez o grupo: derrubar o grupo inteiro (dbus, shell, pipewire, apps) e nada além dele.
    local pgid; pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
    if [ -n "$pgid" ] && [ "$pgid" != "$(ps -o pgid= -p $$ | tr -d ' ')" ]; then
      kill -TERM -- "-${pgid}" 2>/dev/null || true
      for _ in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
      kill -KILL -- "-${pgid}" 2>/dev/null || true
    fi
  fi
  [ -d "$RT" ] && cleanup_dir
  echo "sessão de teste encerrada"
}

cmd_status() {
  if alive; then echo "ativa: $RT (pid $(cat "$RT/pid"))"; else echo "inativa"; fi
}

need_session() { alive && [ -f "$ENVF" ] || die "sessão inativa (scripts/linux-test-session.sh start)"; }

cmd_run() {
  need_session
  [ "${1:-}" = "--" ] && shift
  [ "$#" -gt 0 ] || die "faltou o comando (run -- COMANDO...)"
  # shellcheck disable=SC1090
  ( source "$ENVF"; cd "$PWD"; exec "$@" )
}

cmd_shot() {
  need_session
  [ -n "${1:-}" ] || die "faltou o arquivo de saída"
  ( source "$ENVF"; /usr/bin/python3 "$HERE/linux_session_tool.py" shot "$1" )
}

cmd_approve() {
  need_session
  ( source "$ENVF"; /usr/bin/python3 "$HERE/linux_session_tool.py" approve "${1:-60}" )
}

case "${1:-}" in
  start) shift; cmd_start "$@" ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  run) shift; cmd_run "$@" ;;
  shot) shift; cmd_shot "$@" ;;
  approve) shift; cmd_approve "$@" ;;
  *) sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 2 ;;
esac
