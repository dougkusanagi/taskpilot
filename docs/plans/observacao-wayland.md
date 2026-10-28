# Observação e confirmação Wayland

Objetivo: observar AT-SPI na sessão correta e comprovar efeitos sem interferir no desktop de trabalho.

- [x] Revisar alterações existentes, memória, plano vigente e relatório.
- [x] Integrar leitura AT-SPI com diagnóstico e limites, sem coordenadas inválidas.
- [x] Confirmar abas e texto com referências de evidência e regressões passivas.
- [x] Executar suíte oficial e lint locais.
- [x] Repetir avaliação isolada, capturar e conferir resultado independente.
- [ ] Obter sucesso end-to-end e executar variação de conteúdo.

Tentativas: quatro avaliações nesta sessão, detalhadas no relatório. A primeira falhou na
visão geral; após preparo, três repetições confirmaram abas, mas o planner continuou clicando.
Corrigidos: árvore sempre vazia; profundidade insuficiente; alvo perdido como `visual None`;
confirmação ausente no histórico/memória. Orientações gerais de progresso não resolveram.

Estado: implementação testada, efeito de nova aba comprovado; tarefa real continua falhando,
sem digitação e sem falsa conclusão. Sessão/modelos próprios encerrados, sem processos
marcados restantes. Evidências: runs/ubuntu-e2e-20261028-173237 e runs/run-20261028-143631.
Próximo passo: investigar a decisão repetida do planner usando o prompt/resposta gravados,
sem criar estratégia específica de editor em Python; repetir sessão limpa e variação somente
após resolver a falha. Não ampliar gates nem mudar default.
