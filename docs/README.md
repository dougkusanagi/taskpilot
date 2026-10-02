# Documentação do projeto

- [Melhorias para 6 GB — 02/10/2026](melhorias-6gb-2026-10-02.md) ([HTML](relatorio-melhorias-2026-10-02.html)):
  o que foi implementado, medido e o que não funcionou; perfis P1/P2/U3. Offline, sem E2E.
- [Pesquisa de modelos e thinking — 01/10/2026](pesquisa-modelos-2026-10-01.md):
  candidatos recentes para texto/visão em 6 GB, quantizações Bonsai e ablação
  de reasoning do Qwen3.5-4B; recomendações de teste, sem mudança de default.
- [Comparação automatizada de modelos locais](teste-modelos-locais.md):
  32 cenas textuais sem inputs, servidor LM Studio/OpenAI-compatible e ZIP
  para análise posterior; não valida visão, E2E ou pico de VRAM.
- [Viabilidade em 6 GB — análise de 30/09/2026](relatorio-viabilidade-6gb-2026-09-30.md):
  parecer técnico, lacunas reproduzidas, modelos candidatos, extensão sensora
  e linguagem. Relatório de análise; não substitui o plano vigente nem aprova gates.
- [Único plano vigente — 20/09/2026](plano-agente-generico-2026-09-20.md):
  agente genérico local, reaproveitamento de bases, critérios de funcionalidade
  e avaliação em 6 GB. Etapas R0–R6 propostas; começar por R0.
- [Ambiente Sandbox](sandbox-test-env.md): operação dos testes dev existentes.
- [Matriz anterior](matriz-avaliacao-2026-09-20.md): histórico de probes;
  não define mais gates, ordem de modelos ou defaults.
- [Revisão de 18/09/2026](revisao-codebase-2026-09-18.md): histórico de achados
  e correções; seu roadmap foi substituído pelo plano vigente.

[AGENTS.md](../AGENTS.md) registra regras, quirks e inventário atual.
O plano de 19/09 foi removido para evitar instruções concorrentes.
Para evolução, seguir o plano vigente; para execução, conferir o estado atual
do código e o guia Sandbox. Não tratar funcionalidade planejada como disponível.
