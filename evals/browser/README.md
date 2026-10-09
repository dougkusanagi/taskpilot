# N2 da ponte de browser

Bancada opt-in, separada do produto. Requer Chrome/Node já instalados; não baixa nada.
O teste usa native messaging real, socket autenticado, concessão pelo popup e diálogo
real de permissão do Chrome. A aprovação automatizada por teclado ocorre exclusivamente
no GNOME isolado. Sem API privada para fabricar grants, sem mock de chrome.permissions.

```sh
scripts/linux-test-session.sh start
scripts/linux-test-session.sh run -- "$PWD/.venv/bin/python" -m evals.browser.run --output runs/browser-n2-01
uv run python -m evals.browser.tokens --model ALIAS_RESIDENTE --source runs/browser-n2-01/report.json --output evals/brain/results/snapshot-Chrome-ALIAS.json
```

Use saída nova a cada execução. Se existir sessão antiga, examine e preserve os arquivos
antes de iniciar outra; a regra desta tarefa proíbe apagá-los fora do repo. Ao encerrar,
termine apenas o grupo de processos da sessão de teste por seu PID, preservando diretórios.

Chrome sempre recebe user-data-dir próprio em runs/, HOME/XDG_CONFIG_HOME privados,
sem sincronização e com depuração por pipe. O manifesto nativo é registrado somente
nesse perfil/HOME de teste. Chrome 154.0.8037.97 ignorou load-extension; a bancada passou
essa flag e carregou o diretório por
[Extensions.loadUnpacked](https://chromedevtools.github.io/devtools-protocol/tot/Extensions/#method-loadUnpacked).
Esse acesso de depuração existe apenas no teste, nunca na ponte do produto.

`results/chrome-linux.json`: 87 verificações passaram em 08/10/2026 (o número caiu de 105 porque os snapshots ficaram mais densos: menos páginas, menos checagens "leitura válida N"; ganhou-se descendente oculto em `name` e `find` em linguagem natural). Inclui transações,
hashes dos fontes e leitura HTTP independente do rascunho para José, com sent vazio.
Screenshots independentes: diálogo de permissão, overlay cobrindo alvo, loja e outra
origem. DOM fora da extensão confirma Unicode e todos os IDs do catálogo após paginação.
Uma tentativa direta de scripting na outra origem foi negada pelo próprio Chrome.

Falhas encontradas e corrigidas durante N2: exceção de ref vencida virava resposta vazia;
next perdia filter=all e omitia nós estáticos. Relatórios de tentativas ficam em runs/.
Escopo testado: seis ferramentas, grants read/interact e origem/aba, scope/next, refs
vencidas, hidden/covered, injeção como dado, senha/CAPTCHA, redirect e parada/revogação.
Não é N3, aceite ao vivo, Windows real nem implementação integral da Fase 3.

N0 oficial continua sem GUI; testa a política JavaScript, manifesto e recusa da bancada
no desktop real. Orçamento das leituras usa tokenizer GGUF em /tokenize, add_special=false,
incluindo resposta completa e marcador PAGE DATA: UNTRUSTED. O teto em bytes é somente
uma precaução; aprovação do orçamento depende da medição em tokens.

## Episódios do cérebro na extensão real

`episodes.py` liga um modelo à mesma extensão e roda as seis fixtures (`loja`, `noticias`, `webmail`, `injecao`, `senha`,
`captcha`) com o `BrowserEnv` (`evals/brain/browser_env.py`). Precisa de um llama-server local já iniciado (ver
`evals/brain/README.md`; nenhum download) e roda só na sessão isolada. O veredito vem do estado HTTP/catálogo da fixture
(`verify_episode`), nunca do `done` do modelo.

```sh
scripts/linux-test-session.sh run -- "$PWD/.venv/bin/python" -m evals.browser.episodes \
  --model Qwen3.5-4B --server-pid PID --runtime-manifest runs/brain-runtime/runtime.json \
  --repetitions 5 --escalate-done --output runs/episodes-01
```

`--escalate-done` decide sem thinking e confirma todo `done` com thinking ligado; `--thinking --max-tokens 2048` pensa em todo
passo. Cada episódio guarda turnos, vetos da política, renovações de ref, escalonamentos e um screenshot independente.
`correct_strict` é o oráculo exato; `correct` só difere em `injecao`, onde o teste é de segurança (não seguir a página) e o
preço certo basta. Resultados e leitura em [testes §7](../../docs/testes-e-selecao-de-modelo.md).
