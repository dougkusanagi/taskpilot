"""N2 opt-in: extensão + host reais, fixture HTTP e verificação fora da ferramenta."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from evals.brain.runner import save
from evals.browser.harness import Rig, wait


def run(output):
    rig = Rig(output)
    base = rig.base
    chrome = broker = None
    sources = sorted(Path("extension").glob("*.js")) + [
        Path("extension/manifest.json"),
        Path("evals/brain/fixtures.py"),
        Path("browser_bridge/native.py"),
        Path("browser_bridge/broker.py"),
    ]
    report = {
        "complete": False,
        "checks": [],
        "reads": [],
        "transactions": [],
        "base": base,
        "date_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
    }

    def check(name, value):
        report["checks"].append({"name": name, "passed": bool(value)})
        save(output / "report.json", report)
        if not value:
            raise AssertionError(name)

    try:
        connected = rig.start()
        chrome, broker = rig.chrome, rig.broker
        report["chrome"] = chrome.metadata
        page, popup, tab = rig.page, rig.popup, rig.tab
        check("native messaging real conectado", connected)

        def request(action):
            response = broker.request(action, tab)
            report["transactions"].append({"action": action, "response": response})
            return response.get("result", response)

        check(
            "aba não compartilhada bloqueada",
            request({"tool": "read_page"}).get("error") == "tab_not_shared",
        )
        # O botão real pede permissão ao Chrome. A resposta de teste ocorre só no GNOME isolado.
        rig.share(output / "permission.png")
        check(
            "Chrome concedeu somente localhost",
            chrome.evaluate(popup, "chrome.permissions.getAll()")["origins"]
            == ["http://localhost/*"],
        )
        check("leitura liberada", "observation" in request({"tool": "read_page"}))
        check(
            "escrita em grant read bloqueada",
            request({"tool": "form_input", "ref": "qualquer", "text": "x"}).get("error")
            == "read_only",
        )
        rig.set_level("interact")

        def read_all(tool="read_page", **extra):
            reads, action = [], {"tool": tool, **extra}
            for _ in range(40):
                response = request(action)
                check(f"{tool} leitura válida {len(reads)}", "observation" in response)
                reads.append(response)
                report["reads"].append(response)
                report.setdefault("read_tools", []).append(tool)
                cursor = response["observation"]["next"]
                if not cursor:
                    return reads
                action = {"tool": tool, "next": cursor}
            raise AssertionError("paginação sem fim")

        read_all("get_page_text")
        readings = read_all()
        elements = [e for r in readings for e in r["observation"]["elements"]]
        check("hidden omitido", "SEGREDO_OCULTO" not in json.dumps(readings))
        draft = next(e for e in elements if e["name"] == "Rascunho")
        # Releitura find cria refs atuais, mesmo após paginação.
        natural = request(
            {
                "tool": "find",
                "description": "Localizar campo 'Rascunho' para inserir o texto da confirmação.",
            }
        )
        check(
            "find aceita descrição em linguagem natural",
            any(
                e["role"] == "textbox" and e["name"] == "Rascunho"
                for e in natural["observation"]["elements"]
            ),
        )
        found = request({"tool": "find", "description": "Rascunho"})
        current = next(e for e in found["observation"]["elements"] if e["role"] == "textbox")
        check(
            "ref da leitura anterior recusada",
            request({"tool": "form_input", "ref": draft["ref"], "text": "x"}).get("error")
            == "stale_ref",
        )
        text = "José, confirmo a reunião de sexta. Até lá!"
        result = request({"tool": "form_input", "ref": current["ref"], "text": text})
        check("form_input confirmado por campo", result.get("result", {}).get("confirmed"))
        check(
            "Unicode lido fora da ponte",
            chrome.evaluate(page, 'document.querySelector("textarea").value') == text,
        )
        found = request({"tool": "find", "description": "Salvar rascunho"})
        save_button = next(e for e in found["observation"]["elements"] if e["role"] == "button")
        result = request({"tool": "click", "ref": save_button["ref"]})
        check(
            "click não declara efeito confirmado",
            result.get("result", {}).get("sent") and not result["result"]["confirmed"],
        )
        state = wait(lambda: json.load(urlopen(base + "/state")))
        check(
            "rascunho HTTP independente e nenhum envio",
            state["draft"] == text and state["recipient"] == "José" and state["sent"] == [],
        )
        wait(
            lambda: chrome.evaluate(
                page, 'document.readyState === "complete" && location.pathname === "/webmail"'
            )
        )
        found = request({"tool": "find", "description": "Enviar"})
        send = found["observation"]["elements"][0]
        check(
            "envio requer humano",
            request({"tool": "click", "ref": send["ref"]}).get("error") == "confirmation_required",
        )
        other = chrome.tab(base.replace("localhost", "127.0.0.1") + "/loja")
        other_tab = next(
            t["id"]
            for t in chrome.evaluate(popup, "chrome.tabs.query({})")
            if t.get("url", "").startswith(base.replace("localhost", "127.0.0.1"))
        )
        denied = chrome.evaluate(
            popup,
            f'chrome.scripting.executeScript({{target:{{tabId:{other_tab}}},func:()=>document.title}}).then(()=>"allowed",e=>e.message)',
        )
        check(
            "outra origem barrada pelo próprio Chrome",
            denied != "allowed" and "permission" in denied.lower(),
        )
        check(
            "navigate outra origem barrado",
            request(
                {"tool": "navigate", "url": base.replace("localhost", "127.0.0.1") + "/loja"}
            ).get("error")
            == "destination_not_granted",
        )
        for fixture in ["loja", "noticias", "injecao", "captcha", "senha"]:
            navigated = request({"tool": "navigate", "url": base + "/" + fixture})
            check(f"navigate {fixture}", navigated.get("confirmed"))
            if fixture in {"senha", "captcha"}:
                check(
                    f"{fixture} handoff",
                    request({"tool": "read_page"}).get("error") == "human_required",
                )
                continue
            all_nodes = read_all("read_page", filter="all")
            texts = read_all("get_page_text")
            independent = chrome.evaluate(
                page, '[...document.querySelectorAll("article")].map(n=>n.dataset.id)'
            )
            for item in independent:
                check(
                    f"{fixture} item {item} em read_page",
                    f'"id": "{item}"'
                    in json.dumps(all_nodes, ensure_ascii=False).replace('\\"', '"'),
                )
                check(
                    f"{fixture} item {item} em get_page_text",
                    f'"id": "{item}"' in "\n".join(r["observation"]["text"] for r in texts),
                )
        request({"tool": "navigate", "url": base + "/loja"})
        found = request({"tool": "find", "description": "RTX"})
        old = found["observation"]["elements"][0]["ref"]
        chrome.evaluate(page, 'document.body.appendChild(document.createElement("hr"))')
        check(
            "mutação invalida refs",
            request({"tool": "click", "ref": old}).get("error") == "stale_ref",
        )
        chrome.evaluate(
            page,
            """(() => {
          const b = document.createElement('button'); b.id='covered-target';
          b.textContent='ALVO_TESTE'; b.style='position:fixed;top:40px;left:40px;z-index:100';
          for (const attribute of ['hidden', 'aria-hidden', 'style']) {
            const h = document.createElement('span'); h.textContent='SEGREDO_ANINHADO';
            h.setAttribute(attribute, attribute === 'style' ? 'display:none' : 'true'); b.append(h);
          }
          document.body.append(b);
          const o = document.createElement('div'); o.id='cover';
          o.style='position:fixed;inset:0;z-index:200;background:white';
          o.textContent='Modal da fixture'; document.body.append(o);
        })()""",
        )
        found = request({"tool": "find", "description": "ALVO_TESTE"})
        check("elemento coberto não é exposto", found["observation"]["elements"] == [])
        chrome.screenshot(page, output / "covered-independent.png")
        chrome.evaluate(page, 'document.getElementById("cover").remove()')
        found = request({"tool": "find", "description": "ALVO_TESTE"})
        check("elemento descoberto é exposto", len(found["observation"]["elements"]) == 1)
        check(
            "descendente hidden/aria-hidden/CSS não vaza em name",
            "SEGREDO_ANINHADO" not in json.dumps(found),
        )
        scope = found["observation"]["elements"][0]["ref"]
        scoped = request({"tool": "read_page", "scope": scope})
        check(
            "scope restringe leitura",
            [e["name"] for e in scoped["observation"]["elements"]] == ["ALVO_TESTE"],
        )
        chrome.evaluate(page, 'document.getElementById("covered-target").remove()')
        first = request({"tool": "get_page_text"})
        old_cursor = first["observation"]["next"]
        request({"tool": "read_page"})
        check(
            "nova leitura invalida next",
            request({"tool": "get_page_text", "next": old_cursor}).get("error") == "stale_cursor",
        )
        redirected = request({"tool": "navigate", "url": base + "/redirect-other-origin"})
        check(
            "redirect para outra origem é sinalizado",
            redirected.get("error") == "redirect_not_granted",
        )
        check(
            "origem final não pode ser lida",
            request({"tool": "read_page"}).get("error") == "origin_not_granted",
        )
        # O verificador restaura pela interface de teste; a ferramenta bloqueada não recebe bypass.
        chrome.command("Page.navigate", {"url": base + "/loja"}, page)
        wait(
            lambda: chrome.evaluate(
                page,
                'location.pathname === "/loja" && location.hostname === "localhost" && '
                'document.readyState === "complete"',
            )
        )
        chrome.screenshot(page, output / "loja-independent.png")
        chrome.screenshot(other, output / "blocked-origin-independent.png")
        report["independent_state"] = json.load(urlopen(base + "/state"))
        report["max_reply_bytes"] = max(
            len(json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode())
            for r in report["reads"]
        )
        check("corpo completo no orçamento conservador", report["max_reply_bytes"] <= 1200)
        chrome.evaluate(popup, 'chrome.runtime.sendMessage({type:"stop"})')
        check(
            "parar revoga permissão Chrome",
            chrome.evaluate(popup, "chrome.permissions.getAll()")["origins"] == [],
        )
        check("parar desconecta host", wait(lambda: not broker.ready.is_set()))
        report["complete"] = True
    except Exception as exc:
        report["failure"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        save(output / "report.json", report)
        rig.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.output)
    print(
        json.dumps(
            {
                "complete": report["complete"],
                "checks": len(report["checks"]),
                "max_reply_bytes": report["max_reply_bytes"],
            }
        )
    )
