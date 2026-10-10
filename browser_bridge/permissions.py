"""CLI `taskpilot permissions`: lista e revoga grants da extensão pelo canal de controle do broker.

Uso: python -m browser_bridge.permissions list|revoke ORIGEM|revoke-all [--config ARQUIVO]
O arquivo de configuração é o mesmo do native host (0600, em XDG_RUNTIME_DIR). A concessão
continua sendo um gesto do usuário no popup da extensão; o CLI só enxerga e revoga.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from browser_bridge.broker import control_call


def default_config() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise ValueError("XDG_RUNTIME_DIR ausente; use --config")
    return Path(runtime) / "taskpilot" / "bridge.json"


def call(config: Path, command: str, origin: str | None = None) -> dict:
    stat = config.stat()
    if os.name != "nt" and (stat.st_uid != os.getuid() or stat.st_mode & 0o077):
        raise ValueError("configuração da ponte não é privada")
    data = json.loads(config.read_text())
    message = {"admin": command} | ({"origin": origin} if origin else {})
    reply = control_call(data["address"], data["token"], message)
    if "error" in reply or "error" in reply.get("result", {}):
        raise ValueError(reply.get("error") or reply["result"]["error"])
    return reply["result"]


def render(grants: list[dict]) -> str:
    if not grants:
        return "Nenhuma origem liberada."
    lines = ["ORIGEM  NÍVEL  VALIDADE  RESTA"]
    for g in grants:
        left = "-" if not g["expires_in_s"] else f"{g['expires_in_s']}s"
        lines.append(f"{g['origin']}  {g['level']}  {g['validity']}  {left}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="taskpilot permissions")
    parser.add_argument("command", choices=["list", "revoke", "revoke-all"])
    parser.add_argument("origin", nargs="?")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)
    if args.command == "revoke" and not args.origin:
        parser.error("revoke exige a origem (ex.: https://exemplo.com)")
    try:
        config = args.config or default_config()
        if args.command == "list":
            print(render(call(config, "grants_list")["grants"]))
        elif args.command == "revoke":
            call(config, "grants_revoke", args.origin)
            print(f"Revogado: {args.origin}")
        else:
            for grant in call(config, "grants_list")["grants"]:
                call(config, "grants_revoke", grant["origin"])
                print(f"Revogado: {grant['origin']}")
    except (OSError, ValueError, KeyError) as exc:
        print(f"taskpilot permissions: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
