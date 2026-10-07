# TaskPilot — MVP em reconstrução

Computer use **local** para GPU de **6 GB de VRAM**. Esta branch (`mvp/do-zero`) recomeça a partir do plano em
[docs/arquitetura-do-zero-2026-10-07.html](docs/arquitetura-do-zero-2026-10-07.html): um VLM de 4B como cérebro único,
ferramentas semânticas (AT-SPI/UIA, teclado) primeiro, OCR depois e visão só como reserva.

**Estado:** o orquestrador ainda não existe. O desenho antigo (planner de texto + grounder, `loop.py`) está no `master`.
Aproveitado aqui: runtime llama.cpp (Windows e Linux), adaptadores de plataforma (UIA, AT-SPI + portal Wayland, OCR),
segurança, contratos/verificação, skills, UI/ditado e a sessão de teste isolada no Linux.

```bash
uv sync
uv run python -m unittest discover -s tests   # suíte
uv run python -m server --profile P1          # sobe os modelos (baixa na 1ª vez)
uv run python main.py --self-test             # screenshot, sem clicar
```

Linux: veja [docs/teste-sessao-linux.md](docs/teste-sessao-linux.md). Windows: [docs/sandbox-test-env.md](docs/sandbox-test-env.md).
