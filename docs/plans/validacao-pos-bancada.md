# Validação após a bancada de 6 GB

## Objetivo
Validar a entrega opt-in, integrar e lançar sem alterar o default B1. Testes Windows excluídos desta execução. O usuário incluiu Ubuntu como plataforma a suportar e autorizou continuar os testes locais.

## Passos
- [x] Revisar integração do verificador e executar suíte local inicial (504 testes, 1 skip).
- [x] Corrigir aceitação de probabilidades inválidas pelo verificador, com regressão.
- [x] Suportar seleção e extração do runtime Ubuntu x86_64, com testes de regressão.
- [x] Verificar extração e execução do binário CPU oficial b11954 no Ubuntu.
- [x] Verificar startup e inferência CPU pelo runtime próprio com Qwen3-4B local (HTTP 200).
- [ ] Validar sessão Wayland em compositor isolado, sem atuar na sessão de trabalho.
- [x] Congelar bateria independente: 4 sites + Calculadora/Editor GNOME, 28 casos (14 presentes/14 ausentes), com hashes. Apps nativos Windows ficam fora desta execução.
- [ ] Avaliar d1 nessa bateria sem ajustar o limite nos dados de validação.
- [ ] Se confirmado, implementar inicialização automática d1 na CPU no runtime.
- [ ] Medir Bolt com orçamentos intermediários e tempo total de trajetória; decisão de uso real depende de E2E.
- [ ] Concluir revisão do branch, integrar no master e lançar minor após suíte e build aplicável.
- [ ] Limpar pesos descartados após concluir novas medições.

## Tentativas que falharam
Capturas iniciais rejeitaram links ambíguos e fora da tela antes da inferência. Sessão GNOME isolada abriu o diálogo do portal, mas entrada via portal ainda não foi confirmada. Revisão encontrou que NaN passava pela comparação do limite; correção adiciona validação estrita da resposta.

## Dúvidas abertas
Apps nativos e tempo por tarefa real continuam sem evidência; não substituir pelo simulador.

## Próximo passo
Concluir execução de Bolt 32/64/128 e avaliar d1 na bateria independente; depois registrar resultados e concluir revisão.
