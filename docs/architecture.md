# Arquitetura

O loop observa a interface, consulta os modelos, aplica vetos e executa a ação escolhida.
O perfil B1 continua padrão; perfis e recursos experimentais exigem opt-in.

```mermaid
flowchart TD
    CLI[CLI main.py] --> Loop[Loop observe / decide / act / verify]
    Loop --> Planner[Planner textual ou unificado]
    Loop --> Vision[Localizador visual]
    Vision --> Verifier[Verificador d1 opcional / servidor externo]
    Loop --> Guard[Vetos e executor Python]
    Guard --> Windows[Windows: UIA / PyAutoGUI / MSS]
    Guard --> Wayland[Ubuntu opt-in: cliente Wayland]
    Wayland --> Bridge[Ponte Python do sistema / GI]
    Bridge --> Portal[Portais RemoteDesktop e ScreenCast / PipeWire]
    Runtime[Runtime próprio server.py] --> Assets[Binários e GGUFs locais]
    Runtime --> Servers[Processos llama-server]
    Servers --> Planner
    Servers --> Vision
```

O runtime seleciona ZIP do Windows x64 ou tar.gz do Ubuntu x86_64, com backend CPU por padrão
e CUDA/Vulkan opcionais. A seleção do runtime Linux não habilita controle de desktop: o backend
Wayland continua explícito (`--platform wayland`). Nenhum teste unitário abre sessão de portal.
O verificador d1 ainda depende de endpoint configurado e não é iniciado pelo runtime.

As baterias em `evals/` consultam servidores locais e avaliam respostas sobre dados congelados ou
simuladores. Não executam as ações de desktop e não demonstram sucesso end-to-end.
