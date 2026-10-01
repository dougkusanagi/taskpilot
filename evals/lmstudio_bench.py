"""Gerencia LM Studio local e executa a bateria textual, um modelo por vez."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from evals import model_bench as bench


def find_lms(explicit=None):
    candidates = [explicit] if explicit else [shutil.which('lms'),
        str(Path.home() / '.lmstudio/bin/lms.exe'),
        str(Path.home() / '.lmstudio/bin/lms')]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise ValueError('CLI lms não encontrada. Instale/ative a CLI do LM Studio ou use --lms-path.')


class Manager:
    def __init__(self, executable):
        self.executable = executable
        self.events = []

    def command(self, *args):
        command = [self.executable, *args]
        print('LM Studio: ' + ' '.join(args), flush=True)
        started = time.monotonic()
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding='utf-8', errors='replace', shell=False)
        try:
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print(f'LM Studio: aguardando {args[0]} ({time.monotonic()-started:.0f}s)',
                          flush=True)
                    if time.monotonic()-started > 600:
                        raise TimeoutError('Comando LM Studio excedeu 10 minutos')
        except BaseException:
            process.kill()
            process.communicate()
            raise
        self.events.append({'args': list(args), 'returncode': process.returncode,
                            'stdout': stdout, 'stderr': stderr,
                            'elapsed_s': round(time.monotonic()-started, 2)})
        if process.returncode:
            raise ValueError(f'lms {args[0]} falhou: {(stderr or stdout)[-2000:]}')
        return stdout


def inventory(raw):
    data = json.loads(raw)
    if isinstance(data, dict):
        data = data.get('models', data.get('llm'))
    if not isinstance(data, list):
        raise ValueError('Formato inesperado de lms ls --json; veja automation.json.')
    models = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError('Inventário inválido')
        key = item.get('modelKey') or item.get('path') or item.get('key')
        if not isinstance(key, str) or not key or key.startswith('-'):
            raise ValueError('Modelo sem modelKey/path/key utilizável; veja automation.json.')
        models.append((key, item))
    return models


def ensure_server(manager, url):
    parsed = urlsplit(url)
    if parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1') \
            or parsed.path != '/v1':
        raise ValueError('Modo gerenciado exige http://127.0.0.1:PORTA/v1')
    with httpx.Client(timeout=3, trust_env=False) as client:
        try:
            response = client.get(url + '/models')
        except httpx.ConnectError:
            manager.command('daemon', 'up')
            manager.command('server', 'start', '--port', str(parsed.port or 80),
                            '--bind', '127.0.0.1')
        else:
            response.raise_for_status()
            response.json()['data']
            # Prova que a CLI responde; não tratar servidor arbitrário como LM Studio.
            manager.command('server', 'status')
            return
        for _ in range(30):
            try:
                response = client.get(url + '/models')
                response.raise_for_status()
                response.json()['data']
                return
            except httpx.ConnectError:
                time.sleep(1)
        raise ValueError('Servidor LM Studio não ficou disponível após iniciar')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--lms-path')
    ap.add_argument('--url', default='http://127.0.0.1:1234/v1')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--match', default='minicpm', help='substring do model key; padrão minicpm')
    ap.add_argument('--model', action='append', help='model key exato do lms ls, substitui --match')
    ap.add_argument('--context', type=int, default=8192)
    ap.add_argument('--gpu', default='auto', choices=('auto', 'max', 'off'))
    ap.add_argument('--out', type=Path)
    ap.add_argument('--reps', type=int, default=3)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--yes', action='store_true',
                    help='aceita descarregar TODOS os modelos residentes do LM Studio')
    ap.add_argument('--quick', action='store_true', help='smoke: 4 cenas, 1 repetição')
    ap.add_argument('--format', choices=('schema', 'prompt', 'both'), default='schema')
    ap.add_argument('--thinking', choices=('native', 'on', 'off'), default='native')
    ap.add_argument('--max-tokens', type=int, default=2048)
    ap.add_argument('--temperature', type=float, default=.1)
    ap.add_argument('--timeout', type=float, default=180)
    ap.add_argument('--notes', default='')
    args = ap.parse_args(argv)
    if args.quick:
        args.limit, args.reps = args.limit or 4, 1
    if args.context < args.max_tokens + 2048 or args.max_tokens < 1 or args.reps < 1 \
            or args.limit < 0 or args.timeout <= 0:
        ap.error('Reserve ao menos max-tokens + 2048 de contexto; parâmetros devem ser positivos')
    directory = args.out or bench.ROOT / 'runs' / (
        'lmstudio-bench-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f'))
    created = False
    manager = None
    results = []
    status = 0
    metadata = {'configuration': vars(args) | {'out': str(directory)}, 'results': results,
                'note': 'Configuração solicitada; sem certificação de VRAM ou parâmetros efetivos.'}
    try:
        manager = Manager(find_lms(args.lms_path))
        ensure_server(manager, bench.local_url(args.url))
        raw = manager.command('ls', '--llm', '--json')
        available = inventory(raw)
        if args.list:
            print(raw)
            return 0
        selected = [(key, info) for key, info in available
                    if key in args.model] if args.model else [
                        (key, info) for key, info in available
                        if args.match.casefold() in key.casefold()]
        if args.model and set(args.model) - {key for key, _ in available}:
            raise ValueError('Model key não encontrado; use --lmstudio --list')
        if not selected:
            raise ValueError('Nenhum modelo corresponde ao filtro; use --lmstudio --list')
        print('Modelos selecionados: ' + ', '.join(key for key, _ in selected), flush=True)
        if not args.yes:
            print('Este modo descarrega TODOS os modelos residentes do LM Studio '
                  '(não são recarregados depois).', flush=True)
            if not sys.stdin.isatty() or input('Continuar? [s/N] ').strip().lower() != 's':
                raise ValueError('Cancelado; use --yes para aceitar sem perguntar.')
        directory.mkdir(parents=True, exist_ok=False)
        created = True
        metadata['inventory'] = [info for _, info in available]
        for index, (key, info) in enumerate(selected):
            identifier = f'taskpilot-bench-{index}'
            result = {'model_key': key, 'inventory': info, 'identifier': identifier}
            results.append(result)
            try:
                # Autorizado no modo gerenciado: isolar consumo e evitar competição de VRAM.
                manager.command('unload', '--all')
                load_args = ['load', key, '--context-length', str(args.context),
                             '--identifier', identifier]
                if args.gpu != 'auto':
                    load_args += ['--gpu', args.gpu]
                result['estimate'] = manager.command(*load_args, '--estimate-only')
                manager.command(*load_args)
                result['loaded_snapshot'] = manager.command('ps', '--json')
                output = directory / f'model-{index:02d}'
                result['directory'] = output.name
                result['exit_code'] = bench.main([
                    '--url', args.url, '--model', identifier, '--out', str(output),
                    '--reps', str(args.reps), '--limit', str(args.limit),
                    '--format', args.format, '--thinking', args.thinking,
                    '--max-tokens', str(args.max_tokens), '--temperature', str(args.temperature),
                    '--timeout', str(args.timeout), '--notes',
                    f'context_requested={args.context}; gpu_requested={args.gpu}; {args.notes}'])
                if result['exit_code'] == 130:
                    raise KeyboardInterrupt
                if result['exit_code']:
                    status = 2
            except (ValueError, OSError) as exc:
                result['error'] = str(exc)
                print(f'Falha em {key}: {exc}', flush=True)
                status = 2
            finally:
                try:
                    manager.command('unload', identifier)
                except (ValueError, OSError) as exc:
                    result['cleanup_error'] = str(exc)
                    status = 2
    except KeyboardInterrupt:
        metadata['interrupted'] = True
        status = 130
    except (ValueError, OSError, httpx.HTTPError, KeyError) as exc:
        print(f'Não foi possível gerenciar LM Studio: {exc}', flush=True)
        metadata['error'] = str(exc)
        status = 2
    finally:
        if created:
            metadata['commands'] = manager.events if manager else []
            (directory / 'automation.json').write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
            lines = ['# Comparação gerenciada pelo LM Studio', '',
                     '| Modelo | Formato | Acertos | Estado |', '| --- | --- | ---: | --- |']
            for result in results:
                path = directory / result.get('directory', '_missing') / 'summary.json'
                if path.is_file():
                    data = json.loads(path.read_text(encoding='utf-8'))
                    for group in data['results']:
                        s = group['summary']
                        lines.append(f"| {result['model_key']} | {group['settings']['format']} | "
                                     f"{s['passed']}/{s['planned']} | "
                                     f"{'completo' if s['complete'] else 'parcial'} |")
                else:
                    lines.append(f"| {result['model_key']} | — | — | falha de infraestrutura |")
            lines += ['', 'Configuração solicitada e comandos: automation.json.',
                      'Estimativa de memória não mede pico VRAM. Sem validação visual/E2E.']
            (directory / 'comparativo.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
            files = list(directory.rglob('*'))
            with zipfile.ZipFile(directory / 'resultado.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
                for path in files:
                    if path.is_file() and path.suffix != '.zip':
                        archive.write(path, path.relative_to(directory))
            print(f'Relatório: {(directory / "resultado.zip").resolve()}', flush=True)
    return status


if __name__ == '__main__':
    raise SystemExit(main())
