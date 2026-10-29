### Corrigido

- No Wayland, o foco volta à janela que estava ativa antes do diálogo de autorização do portal (Super e Esc, confirmado por AT-SPI), e o primeiro passo não é mais gasto para reativar o app.
- Focar uma janela que já está ativa não gasta mais um passo: o agente recebe o aviso e age dentro da janela.
- Acentos digitados no Wayland não saem mais fora de ordem ("Oál").
