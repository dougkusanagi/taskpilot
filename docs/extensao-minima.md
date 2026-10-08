# Extensão mínima de browser — 07/10/2026

`extension/` implementa `read_page`, `get_page_text`, `find`, `click`, `form_input`
e `navigate`, ligados ao native host de [native-host.md](native-host.md).
Não há orquestrador. Abas/grupo, screenshot como ferramenta, expiração de grants,
confirmação humana executável e CLI de permissões continuam pendentes na Fase 3.

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
