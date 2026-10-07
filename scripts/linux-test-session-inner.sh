#!/usr/bin/env bash
# Corpo da sessão de teste (roda DENTRO do `dbus-run-session` de linux-test-session.sh).
# Sobe PipeWire + WirePlumber + gnome-shell headless, espera ficar pronto, grava o ambiente em
# $TP_ENVF e fica de pé até ser encerrado.
set -u

shell_pid=""
pids=()

cleanup() {
  for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done
  [ -n "$shell_pid" ] && kill "$shell_pid" 2>/dev/null || true
  # o document-portal monta FUSE em $XDG_RUNTIME_DIR/doc; sem isto o mount fica pendurado
  fusermount3 -u -z "$XDG_RUNTIME_DIR/doc" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT TERM INT

pipewire >"$XDG_RUNTIME_DIR/pipewire.log" 2>&1 &
pids+=($!)
for _ in $(seq 1 20); do [ -S "$XDG_RUNTIME_DIR/pipewire-0" ] && break; sleep 0.25; done
wireplumber >"$XDG_RUNTIME_DIR/wireplumber.log" 2>&1 &
pids+=($!)

# O compositor não pode ver WAYLAND_DISPLAY (senão tentaria rodar aninhado num servidor alheio).
env -u WAYLAND_DISPLAY gnome-shell --headless --wayland --no-x11 \
  --wayland-display="$WAYLAND_DISPLAY" --virtual-monitor "$TP_SIZE" \
  >"$XDG_RUNTIME_DIR/shell.log" 2>&1 &
shell_pid=$!

ready=0
for _ in $(seq 1 60); do
  kill -0 "$shell_pid" 2>/dev/null || { echo "gnome-shell morreu"; tail -20 "$XDG_RUNTIME_DIR/shell.log"; exit 1; }
  if [ -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ] && gdbus call --session \
      --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
      --method org.freedesktop.DBus.NameHasOwner org.gnome.Mutter.RemoteDesktop 2>/dev/null | grep -q true; then
    ready=1; break
  fi
  sleep 1
done
[ "$ready" = 1 ] || { echo "gnome-shell não ficou pronto"; exit 1; }

# Acessibilidade ligada ANTES de qualquer app GTK abrir janela: é por AT-SPI que o diálogo do portal
# é aprovado (sem foco/ponteiro neste ambiente headless) e que se inspeciona a árvore de widgets.
gdbus call --session --dest org.a11y.Bus --object-path /org/a11y/bus \
  --method org.freedesktop.DBus.Properties.Set org.a11y.Status IsEnabled "<true>" >/dev/null 2>&1 || true

# O ibus (iniciado pelo shell) ativa o xdg-desktop-portal cedo demais, antes do compositor existir
# no barramento: o backend GNOME então só expõe "settings". Derruba esses processos (identificados
# pelo dono do nome NESTE barramento, nunca por padrão de nome) para a próxima chamada reativar
# com o compositor já no ar.
for name in org.freedesktop.impl.portal.desktop.gnome org.freedesktop.portal.Desktop; do
  pid=$(gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
        --method org.freedesktop.DBus.GetConnectionUnixProcessID "$name" 2>/dev/null \
        | tr -dc '0-9') || true
  [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null || true
done
sleep 1

{
  echo "export XDG_RUNTIME_DIR='$XDG_RUNTIME_DIR'"
  echo "export HOME='$HOME'"
  echo "export DBUS_SESSION_BUS_ADDRESS='$DBUS_SESSION_BUS_ADDRESS'"
  echo "export WAYLAND_DISPLAY='$WAYLAND_DISPLAY'"
  echo "export XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=GNOME XDG_SESSION_DESKTOP=gnome"
  echo "export TASKPILOT_REAL_HOME='$TP_REAL_HOME'"
  echo "export TASKPILOT_TEST_SESSION=1"
  echo "unset DISPLAY"
} >"$TP_ENVF"
touch "$XDG_RUNTIME_DIR/ready"
echo "pronto: $WAYLAND_DISPLAY ($TP_SIZE)"

# fica de pé até o grupo de processos receber TERM
while kill -0 "$shell_pid" 2>/dev/null; do sleep 2; done
