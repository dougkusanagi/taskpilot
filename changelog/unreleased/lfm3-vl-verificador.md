### Adicionado
- Inicialização opcional do d1 na CPU pelo runtime (`vision.verifier.auto_start`), com limpeza dos processos próprios após falha.
- Runtime próprio para Ubuntu x86_64 (CPU, Vulkan e CUDA), mantendo controle Wayland opt-in.
- Avaliador do d1 aceita bateria independente por `--cases`.
- Localizador visual LFM3-VL-2B (opt-in) e verificador d1 do alvo antes do clique (`vision.verifier`), que reduz cliques em alvos inexistentes.

### Alterado
- Runtime próprio usa o llama.cpp b11954 e baixa de novo um binário de release anterior.
- Resposta de localização truncada vira "alvo não visível", sem clique.

### Corrigido
- `--self-test` e `--locate` respeitam o backend Wayland escolhido e encerram a sessão ao sair.
- Verificador recusa probabilidades inválidas, inclusive NaN, antes de permitir um clique.
