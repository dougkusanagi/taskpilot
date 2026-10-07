# Contrato do cérebro v0 (congelado)

Fonte executável: `evals/brain/contract.py`; schema publicado ao lado deste documento.
Uma decisão é um objeto com `tool` e somente os argumentos daquela variante. Não há shell,
JavaScript arbitrário nem coordenadas. `tabs_*` significa list/create/select/close; `scroll_to`
integra o vocabulário da arquitetura. Texto Unicode é preservado.

Observation e Result são renderizados como uma linha JSON prefixada por OBSERVATION/RESULT.
Observation contém version, id, url, elements (ref, role, name, value, sensitive), text, coverage,
next. Dados da página são explicitamente não confiáveis. Refs incluem o ID da observação;
qualquer nova leitura invalida os refs anteriores, mesmo que o elemento continue na página.
Não há coordenada fictícia para elementos sem bounds. `next` é cursor opaco de paginação.

Result contém version, sent, confirmed, evidence, delta, error. Enviar uma ação não confirma
seu efeito. A confirmação compara leitura independente do estado relevante antes/depois;
título da janela não serve. Evidências contêm ref da leitura, url e excerpt. Leituras podem
permanecer no histórico após expirar um ref de interação; `done` só cita evidências registradas,
com os três campos idênticos. O avaliador ainda verifica se sustentam a resposta.

Validade contextual e políticas de segurança são verificadas fora do schema JSON.
Mudança incompatível exige nova versão. Este módulo não substitui os contratos herdados nem
implementa adaptadores ou orquestrador.
