# Native host mínimo (Trilha B)

`browser_bridge/` é transporte, sem orquestrador. Um Broker escuta TCP em 127.0.0.1,
porta efêmera; token aleatório de 256 bits autentica cada conexão. O host nativo valida
também a origem exata da extensão, passa JSON entre stdio e socket e não executa shell.
Só ações do perfil browser-1 são aceitas. Correlation IDs internos isolam respostas;
desconexão, ação inválida e timeout são erros explícitos.

Framing: uint32 em byte order nativo + JSON UTF-8, tamanho máximo 1 MiB, sem NaN.
Formato conforme [documentação do Chrome](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging).
Diagnósticos ficam em stderr. Token não aparece no prompt, stdout ou manifesto público.

## Teste/instalação explícita

Primeiro obtenha o ID da extensão carregada no perfil **descartável**. Depois:

```bash
uv run python -m browser_bridge.broker --extension-id ID \
  --config /run/user/1000/taskpilot-bridge.json
uv run python -m browser_bridge.install --extension-id ID \
  --config /run/user/1000/taskpilot-bridge.json --output runs/native-host
```

Copie/registre o manifesto gerado somente no HOME/config do Chrome descartável para N2.
O gerador não registra nada no Chrome real nem no registro do Windows.
No Windows, execute o gerador nesse SO com `--platform windows`; o launcher .cmd usa o
Python e caminho do projeto daquela instalação. Registro HKCU do host é uma etapa explícita.
Manifestos Linux/Windows são validados em N0; execução Windows ainda não foi exercitada.

**Quirk medido:** neste NTFS, chmod(0600) deixa modo 0777. Configuração com token no repo
é recusada **antes de gravar o segredo**. Use filesystem com permissões reais, como
`XDG_RUNTIME_DIR`; arquivo deve pertencer ao usuário e ser 0600. Não há remoção automática
de arquivos fora do repo. TCP foi usado como socket local portátil; o token continua obrigatório.

A extensão ainda deve aplicar grants por origem, escopo de abas, proteção de segredos e
confirmações. Autenticação da ponte não é autorização para agir na página.
