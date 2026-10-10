# Extensão mínima de browser — 07/10/2026

`extension/` implementa `read_page`, `get_page_text`, `find`, `click`, `form_input`
e `navigate`, ligados ao native host de [native-host.md](native-host.md).
Não há orquestrador. Screenshot como ferramenta (bloqueado, ver arquitetura §3.9), expiração de grants,
e CLI de permissões continuam pendentes na Fase 3. A confirmação humana é executável: o orquestrador reenvia o clique com `confirmed` pelo canal autenticado do broker (nunca pela ação do modelo) e a extensão só o honra em `click`; N2 prova envio barrado sem humano e executado com ele.

O popup pede `optional_host_permissions` ao Chrome por gesto do usuário e compartilha
uma aba com nível read/interact. Há duas barreiras: a concessão do Chrome e o grant
da ponte para a origem exata, incluindo porta. Match patterns do Chrome não distinguem
portas; por isso o worker verifica também a origem exata. Grants ficam somente em
`storage.session`; parar desconecta o host e revoga as permissões opcionais.
Permissão read bloqueia click, form_input e navigate. A página não tem canal de
mensagens externo; scripts e refs rodam no mundo ISOLATED.

Snapshots omitem ruído de navegação/rodapé, hidden, aria-hidden, CSS invisível e
elementos cobertos no viewport. Cada resposta completa tem teto conservador de
1.200 bytes UTF-8 e paginação; texto individual maior exige scope ou retorna erro,
sem truncar silenciosamente. `next` preserva o filtro original. Nova leitura invalida
refs anteriores; mutação do DOM, navegação e mudança de valor também as invalidam.
Texto de página é marcado como dado não confiável. Isso não resolve toda injeção
de prompt; o gate N1 continua necessário.

Senha/OTP/cartão e CAPTCHA visíveis devolvem `human_required`; não há resolução
automática. Click de enviar/apagar/comprar e submit desconhecido exige humano.
O bloqueio por rótulo é conservador, não uma classificação completa de páginas
arbitrárias: esta versão é uma bancada para fixtures, ainda não aceite ao vivo.
Click informa sent, mas não confirmed; preenchimento confirma por leitura do campo.
Navegação confirma pela URL final, sem usar título de janela.
Redirect inesperado pode carregar outra origem no Chrome; a ponte sinaliza
`redirect_not_granted` e bloqueia leituras/interações nessa origem. Host permissions
protegem acesso ao DOM, não impedem o pedido HTTP de redirecionamento. N2 mediu
essa diferença; não se declara bloqueio de rede.

N0: política de origens, níveis e abas compartilhadas testada em Node sem dependências;
manifesto sem host_permissions obrigatório, content_scripts ou externally_connectable.
N2 usa somente o perfil descartável criado pela bancada; nunca registra host nem
permissões no perfil real.

## Abas (09/10)

`tabs_list`, `tabs_create`, `tabs_select` e `tabs_close` existem na extensão. O agente só enxerga abas **compartilhadas** (o
título da aba é dado da página: truncado em 80 caracteres e marcado não confiável); só cria aba em origem já liberada com
`interact` (redirecionamento para outra origem fecha a aba e devolve `redirect_not_granted`); só fecha aba que ele mesmo criou
(`not_agent_tab`). Abas criadas entram no grupo "TaskPilot" (permissão `tabGroups`). O `BrowserEnv` acompanha a aba de trabalho.
N2 real confere lista, grupo (lido pela API do Chrome), leitura da aba nova, bloqueio de origem e de fechamento da aba do usuário.

## Validade de grants e CLI (09/10)

O popup escolhe a validade: **esta tarefa** (30 min, contados no worker) ou **sessão do Chrome**. Ao expirar, a extensão apaga o
grant **e remove a permissão de host do Chrome** (só quando nenhuma outra origem usa o mesmo host, porque o Chrome não distingue
porta). Não existe "sempre": persistir acesso é decisão de segurança do dono. O CLI não concede nada (isso é gesto do usuário no
popup); ele enxerga e revoga pelo canal de controle autenticado do broker:

```sh
uv run python -m browser_bridge.permissions list            # origem, nível, validade, tempo restante
uv run python -m browser_bridge.permissions revoke https://exemplo.com
uv run python -m browser_bridge.permissions revoke-all
```

Lê o mesmo arquivo de configuração 0600 do native host (`$XDG_RUNTIME_DIR/taskpilot/bridge.json` por padrão; `--config` troca) e
recusa configuração que não seja privada. Comandos de admin só existem nesse canal: a ação do modelo não os alcança.
