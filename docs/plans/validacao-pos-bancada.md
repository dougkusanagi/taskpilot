# Validação após a bancada de 6 GB

## Objetivo
Validar a entrega opt-in, integrar e lançar sem alterar o default B1. Testes Windows excluídos pelo usuário desta execução.

## Passos
- [x] Revisar integração do verificador e executar suíte local inicial (504 testes, 1 skip).
- [x] Corrigir aceitação de probabilidades inválidas pelo verificador, com regressão.
- [ ] Capturar bateria independente de outros sites; apps nativos Windows dependem de ambiente Windows.
- [ ] Avaliar d1 nessa bateria sem ajustar o limite nos dados de validação.
- [ ] Se confirmado, implementar inicialização automática d1 na CPU no runtime.
- [ ] Medir Bolt com orçamentos intermediários e tempo total de trajetória; decisão de uso real depende de E2E.
- [ ] Concluir revisão do branch, integrar no master e lançar minor após suíte e build aplicável.
- [ ] Limpar pesos descartados após concluir novas medições.

## Tentativas que falharam
Nenhuma nesta execução. Revisão encontrou que NaN passava pela comparação do limite; correção adiciona validação estrita da resposta.

## Dúvidas abertas
Apps nativos e tempo por tarefa real continuam sem evidência; não substituir pelo simulador.

## Próximo passo
Bateria independente de sites, preservando a bateria anterior e os pesos necessários às comparações.
