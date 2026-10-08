"""Gera launcher e manifesto; não registra no perfil real nem no registro Windows."""

import argparse
import json
import re
import shlex
import sys
from pathlib import Path, PureWindowsPath

NAME = "com.taskpilot.bridge"


def manifest(extension_id, native_path, platform):
    if not re.fullmatch(r"[a-p]{32}", extension_id):
        raise ValueError("ID inválido")
    path = PureWindowsPath(native_path) if platform == "windows" else Path(native_path)
    if not path.is_absolute():
        raise ValueError("launcher exige caminho absoluto")
    return {
        "name": NAME,
        "description": "TaskPilot — ponte local autenticada",
        "path": str(path),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }


def generate(
    output: Path,
    extension_id: str,
    config: str,
    *,
    platform="linux",
    python_executable=None,
    project_root=None,
    native_path=None,
):
    root = project_root or str(Path(__file__).resolve().parents[1])
    executable = python_executable or sys.executable
    output.mkdir(parents=True, exist_ok=True)
    launcher = output / ("taskpilot-native.cmd" if platform == "windows" else "taskpilot-native")
    if platform == "linux":
        content = (
            f"#!/bin/sh\ncd {shlex.quote(root)} || exit 1\n"
            f"exec {shlex.quote(executable)} -m browser_bridge.native "
            f'--config {shlex.quote(config)} "$@"\n'
        )
        launcher.write_text(content)
        launcher.chmod(0o700)
    else:
        values = [root, executable, config]
        if any('"' in v or "\n" in v or "\r" in v for v in values):
            raise ValueError("caminho inválido")
        root, executable, config = [v.replace("%", "%%") for v in values]
        content = (
            f'@echo off\r\ncd /d "{root}"\r\n'
            f'"{executable}" -m browser_bridge.native --config "{config}" %*\r\n'
        )
        launcher.write_bytes(content.encode())
    data = manifest(extension_id, native_path or str(launcher.resolve()), platform)
    destination = output / f"{NAME}.json"
    destination.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--extension-id", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--platform", choices=["linux", "windows"], default="linux")
    parser.add_argument("--native-path")
    parser.add_argument("--python-executable")
    parser.add_argument("--project-root")
    args = parser.parse_args()
    print(
        generate(
            args.output,
            args.extension_id,
            args.config,
            platform=args.platform,
            native_path=args.native_path,
            python_executable=args.python_executable,
            project_root=args.project_root,
        )
    )
