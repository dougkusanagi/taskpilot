# Perfil de ferramentas browser-1

O contrato v0 permanece congelado. `evals/brain/browser_contract.py` publica um perfil
versionado que oferece somente ferramentas de browser, ask/handoff/done/fail. Observation e
Result continuam com o formato v0; as regras de refs e evidências permanecem iguais.
O runner usa este perfil e `scenes_browser/` por padrão. `--corpus legacy` reproduz o perfil
anterior, sem alterar cenas, capturas ou código de desktop.

Divergência registrada: a bancada v0 incluía AT-SPI/OCR e estava fora do gate browser-first.
A medição interrompida em 07/10 está preservada em
`evals/brain/results/Qwen3.5-4B-legacy-incompleto.json`: duas condições completas e uma parcial;
a matriz inteira não terminou. Não serve para escolher o cérebro de browser.

Browser-1 congela 96 cenas novas, 16 categorias, 48 dev/48 blind, sem imagem de entrada.
Todas são sintéticas e declaradas. Há famílias similares entre dev/blind; isso limita
representatividade. As expectativas não entram no prompt. Não se ajusta prompt usando blind.
Novas capturas reais de DOM nas fixtures entram numa versão posterior de corpus, com hash
novo, sem transformar resultados antigos em resultados do corpus novo.
