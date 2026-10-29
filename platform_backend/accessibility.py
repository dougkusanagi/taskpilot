"""AT-SPI passivo no barramento explícito da sessão opt-in."""
import json
import os
import subprocess
from pathlib import Path


def snapshot(timeout=5.0):
    if not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
        return {'error': 'barramento de sessão explícito ausente'}
    try:
        result = subprocess.run(
            ['/usr/bin/python3', str(Path(__file__).with_name('atspi_read.py'))],
            capture_output=True, text=True, timeout=timeout, check=True,
            env={key: value for key, value in os.environ.items() if key != 'AT_SPI_BUS_ADDRESS'},
        )
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {'error': f'AT-SPI indisponível: {exc}'}
