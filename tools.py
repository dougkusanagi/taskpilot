"""Ferramentas determinísticas. Registry simples de funções, sem framework."""
from __future__ import annotations

import time

# Whitelist: o alvo vem do PLANNER (que lê texto de tela) e por isso nunca
# passa por shell. Fora daqui = erro honesto (vira last_error p/ o modelo).
# Não há "teleporte p/ URL": navegar é pela UI do navegador (ctrl+l, digitar,
# enter, cliques), como um humano — por isso open_url foi removido.
APP_COMMANDS = {
    "msedge": "msedge", "edge": "msedge", "microsoft edge": "msedge",
    "chrome": "chrome", "brave": "brave",
    "notepad": "notepad", "notepad.exe": "notepad", "bloco de notas": "notepad",
    "calc": "calc", "calc.exe": "calc", "calculator": "calc", "calculadora": "calc",
}


_FOCUS_HINTS = {
    "msedge": "edge||msedge", "edge": "edge||msedge",
    "microsoft edge": "edge||msedge",
    "chrome": "chrome", "brave": "brave",
    "notepad": "bloco de notas||notepad", "notepad.exe": "bloco de notas||notepad",
    "bloco de notas": "bloco de notas||notepad",
    "calc": "calculadora||calculator", "calc.exe": "calculadora||calculator",
    "calculator": "calculadora||calculator", "calculadora": "calculadora||calculator",
}


# Navegadores só expõem a árvore de acessibilidade da PÁGINA (links, campos, botões) ao UIA quando
# detectam um leitor de tela — sem isso o snapshot traz só a moldura da janela (run 20/09: 6
# itens). Esta flag força a árvore. Só vale ao iniciar um processo novo (janela já aberta usa as
# flags de quem a abriu). Configurável: config.launch.browser_args.
BROWSER_EXES = ("chrome", "msedge", "brave")
BROWSER_ARGS: tuple[str, ...] = ("--force-renderer-accessibility",)


def configure(cfg: dict | None) -> None:
    """Aplica `launch.browser_args` da config (vazio desliga a flag)."""
    global BROWSER_ARGS
    args = (cfg or {}).get("launch", {}).get("browser_args")
    if args is not None:
        BROWSER_ARGS = tuple(str(a) for a in args)


def launch_args(exe: str) -> tuple[str, ...]:
    """Argumentos extras do processo (puro): só navegadores recebem a flag de acessibilidade."""
    return BROWSER_ARGS if exe in BROWSER_EXES else ()


def open_app(target: str) -> str:
    """Abre app da whitelist via ShellExecute (App Paths resolve msedge etc.).

    Equivale a clicar no ícone/Menu Iniciar: a partir daqui tudo é UI
    (teclado/mouse/tela). Nunca usa shell=True: sem injeção via `&`, `"` ou `%`.

    Lançar ≠ estar em primeiro plano (cold start lento, foreground-lock do
    Windows): por isso aguarda a janela (polling generoso — Chrome com
    perfis pode levar >10s) e RELATA o resultado.
    """
    import platform_backend

    if platform_backend.active() is not None:
        return platform_backend.active().open_app(target)
    key = target.strip().lower()
    exe = APP_COMMANDS.get(key)
    if exe is None:
        raise ValueError(f"app fora da whitelist: {target!r}; "
                         f"use um de {sorted(set(APP_COMMANDS.values()))}")
    _launch(exe)
    time.sleep(1.2)
    if focus_window(_FOCUS_HINTS.get(key, key), timeout=15.0):
        return f"opened {exe} (janela ativa)"
    return f"opened {exe} (janela ainda não em primeiro plano)"


def _launch(exe: str) -> None:
    """ShellExecute (resolve App Paths: msedge/chrome) com fallback Popen em lista."""
    import os
    import subprocess

    extra = launch_args(exe)
    try:
        if extra:
            os.startfile(exe, arguments=" ".join(extra))
        else:
            os.startfile(exe)
    except OSError:
        subprocess.Popen([exe, *extra], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


_BROWSER_SUFFIXES = (
    " - google chrome", " — google chrome", " – google chrome",
    " - microsoft edge", " — microsoft edge", " – microsoft edge",
    " - brave", " — brave", " – brave",
)


def _page_part(title_lower: str) -> str:
    """Título sem o sufixo do navegador ("... - Google Chrome").

    Puro, testável. Evita que focus("Google") case com QUALQUER aba do
    Chrome (todas terminam no sufixo) — foi o loop do run de 19/09.
    """
    for suf in _BROWSER_SUFFIXES:
        if title_lower.endswith(suf):
            return title_lower[: -len(suf)].strip()
    return title_lower.strip()


# Consulta que nomeia o APP (não a página): pode casar no título cheio,
# incluindo o sufixo. Qualquer outra consulta só vale na parte da página.
_APP_TOKENS = frozenset(
    list(APP_COMMANDS) + ["google chrome", "microsoft edge", "msedge"]
)


def _match_score(title_lower: str, query: str) -> int:
    """3 exato, 2 prefixo, 1 palavra, 0 substring fraca, -1 sem match."""
    page = _page_part(title_lower)
    if not query or not page:
        return -1
    if page == query:
        return 3
    if page.startswith(query):
        return 2
    import re

    if re.search(r"(^|\W)" + re.escape(query) + r"(\W|$)", page):
        return 1
    if query in page:
        return 0
    if query in _APP_TOKENS and query in title_lower:
        return 0  # ex.: focus("chrome") com Chrome aberto (qualquer aba)
    return -1


def focus_window(title_substr: str, timeout: float = 3.0) -> bool:
    """Traz janela p/ frente por substring do título. Aceita alternativas com '||'.

    Estrito (F2): a consulta casa na PARTE DA PÁGINA (sem o sufixo
    "- Google Chrome"); só nome de app (chrome, edge...) casa no título
    cheio. Ranking: exato > prefixo > palavra > substring; desempate
    prefere aba nova/documento novo.
    """
    import platform_backend

    if platform_backend.active() is not None:
        return platform_backend.active().focus_window(title_substr)
    from pywinauto import Desktop

    from overlay import is_overlay_title

    alts = [a.strip().lower() for a in title_substr.split("||") if a.strip()]
    t0 = time.perf_counter()
    desk = Desktop(backend="uia")
    while time.perf_counter() - t0 < timeout:
        try:
            scored = []
            for w in desk.windows(top_level_only=True, visible_only=True):
                try:
                    title = w.window_text() or ""
                    if is_overlay_title(title):
                        continue  # nunca focar a própria borda "controlado"
                    low = title.lower()
                    best = max((_match_score(low, a) for a in alts),
                               default=-1)
                    if best >= 0:
                        scored.append((best, title, w))
                except Exception:
                    continue
            if scored:
                # melhor match; desempate prefere aba/documento novo
                # (nova guia/untitled: não digita em doc do usuário)
                scored.sort(key=lambda t: (
                    -t[0], 0 if any(k in t[1].lower() for k in (
                        "sem t", "untitled", "new tab", "nova guia",
                        "nova aba")) else 1))
                scored[0][2].set_focus()
                time.sleep(0.4)
                return True
        except Exception:
            pass
        time.sleep(0.5)
    # 2ª passada: janela pode estar minimizada (inclui ocultas + restore)
    try:
        scored = []
        for w in desk.windows(top_level_only=True, visible_only=False):
            try:
                title = w.window_text() or ""
                if is_overlay_title(title):
                    continue
                low = title.lower()
                best = max((_match_score(low, a) for a in alts), default=-1)
                if best < 0:
                    continue
                scored.append((best, title, w))
            except Exception:
                continue
        if scored:
            scored.sort(key=lambda t: (
                -t[0], 0 if any(k in t[1].lower() for k in (
                    "sem t", "untitled", "new tab", "nova guia",
                    "nova aba")) else 1))
            try:
                scored[0][2].restore()
                time.sleep(0.4)
            except Exception:
                pass
            scored[0][2].set_focus()
            time.sleep(0.4)
            return True
    except Exception:
        pass
    return False


