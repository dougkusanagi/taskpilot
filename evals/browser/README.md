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

`results/chrome-linux.json`: 105 verificações passaram em 08/10/2026 (a 105ª cobre texto de descendente oculto vazando em `name`). Inclui transações,
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
