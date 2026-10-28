### Adicionado
- Localizador visual LFM3-VL-2B (opt-in) e verificador d1 do alvo antes do clique (`vision.verifier`), que reduz cliques em alvos inexistentes.

### Alterado
- Runtime próprio usa o llama.cpp b11954 e baixa de novo um binário de release anterior.
- Resposta de localização truncada vira "alvo não visível", sem clique.

### Corrigido
- Verificador recusa probabilidades inválidas, inclusive NaN, antes de permitir um clique.
