# Plano da API — Habitat e Social

Data: 04/10/2026. Branch: `feature/habitat-social-improvements`, base `main` em `94e9827`. Esta rodada cria o plano; não implementa funcionalidades.

Plano completo e critérios visuais: [plano do Gatálogo](../../catlogue/docs/plano_habitat_social_2026-10-04.md), mantido no repositório irmão `catlogue`. Se os checkouts não forem irmãos, consultar `docs/plano_habitat_social_2026-10-04.md` na mesma branch do repositório `gatalogo`.

## Entregas da API

1. **Loja grátis para testes:** configuração de preço efetivo zero para itens compráveis, expansões e petiscos, preservando preços base, exclusividade de recompensas, saldo real, limites e idempotência. Catálogo informa `base_price`, `price`, `purchasable`, modo de teste e versão. Remover a associação implícita entre preço positivo e elegibilidade de compra. Validar também o modo com preços normais.
2. **Editor:** preservar IDs, inventário e revisão do layout ao mover/guardar/recolocar caminhos; verificar regras de camadas, água e pontes. A falha de gestos é investigada no app antes de alterar contratos desnecessariamente.
3. **Cartazes:** reutilizar detalhes públicos já persistidos. PNG/PDF são gerados no app; endpoint de geração no servidor não é necessário nesta primeira versão. Não incluir informações privadas adicionais nos arquivos exportados.
4. **Posts de texto:** apoiar uma tela de criação semelhante ao composer do Twitter, com texto amplo, audiência, anexos e associação opcional a gato. Tornar associação ao gato e mídia opcionais, exigir conteúdo útil, preservar publicações antigas, autorização de uploads, audiência, paginação, realtime e progressão. Ajustar o fingerprint/contrato para não depender de nome fictício de gato. Falha/repetição preservam rascunho e não duplicam posts. Repost genérico e ranking de tendências ficam fora deste pedido.
5. **Satisfação:** calcular 0–100 para gatos presentes usando conforto acessível e cuidados reais. Separar da afinidade permanente; pesos configuráveis, média dos gatos e nenhum requisito de compra paga.
6. **Encontros e presentes:** serviço separado para elegibilidade/sorteio persistido por usuário/janela, entrega pendente e resgate transacional. Proposta inicial: janelas de 20 minutos durante visitas, chance de 10–35%, até 3 presentes por dia e um pendente. Parâmetros serão calibrados. Persistir resultado/conteúdo; não sortear novamente com refresh ou troca de gatos. API controla tempo, saldo e inventário. Visitantes decorativos continuam presentes sem prêmio, mesmo com presente pendente ou limite diário atingido.

## Contratos e persistência propostos

- Snapshot do Habitat acrescenta `satisfaction` com componentes, visita recompensável e `pending_gift` com ID, estado de entrega e âncora externa.
- Operação de encontro verifica a janela atual e retorna resultado existente ou um novo resultado elegível; unicidade por usuário/janela e limite diário.
- Resgate por ID de presente valida proprietário, trava carteira, aplica moedas/porções/itens e registra resultado idempotente. Repetição devolve o mesmo prêmio confirmado.
- `HabitatEncounter` guarda janela, probabilidade/satisfação usadas, visitante, conteúdo do presente, estado de entrega e estado de resgate; migração preserva carteiras, afinidade e layouts. Separar visita autorizada, entrega disponível e resgate confirmado. A transição de disponibilidade é idempotente e recuperável após interrupção; o cliente não escolhe o prêmio nem concede saldo.
- Criar uma notificação interna única por presente quando a entrega ficar disponível, vinculada ao ID do presente. Usar badge/realtime e push conforme preferências existentes. Leitura/abertura apenas navega ao presente; não o resgata. Após resgate, o aviso pode permanecer no histórico, com destino indicando que a recompensa já foi recebida.
- Inventário no limite usa conversão prevista para moedas. Um presente entregue permanece disponível após sair; nenhum sorteio/resgate é confirmado offline.
- Entrega permanece do lado de fora da área editável até resgate confirmado, com contorno claro e acesso pela câmera no app. Expandir reposiciona sua âncora externa sem mudar ID/conteúdo, perder notificação ou conceder recompensa. A operação de expansão e a resolução de âncoras precisam tratar presentes pendentes.
- Animação de brincar, vegetação, rotas de fauna e posições interpoladas são responsabilidade local do app. Cenário também ocupa terrenos ainda não expandidos e é removido/reposicionado quando eles são liberados, sem afetar objetos do usuário. Visitantes decorativos e entregadores compartilham rotinas de explorar, beber água, brincar, descansar e sair; só o encontro recompensável tem entrega persistida. Visitante decorativo não concede prêmio.

## Validação

Cobrir compras gratuitas/normais, SKUs exclusivos, compras em lote e repetição; compatibilidade dos posts e audiência; regras de satisfação; relógio/RNG injetados; janelas sem repetição; limites diários; concorrência de resgate, proprietário incorreto, inventário cheio e recuperação após timeout. Validar notificação única na disponibilidade, leitura sem coleta, recuperação de visita/entrega interrompida e expansão com presente pendente. Confirmar que rotina/posição de animal decorativo não concede prêmios. Aplicar migrações em banco descartável e executar a suíte completa antes da integração.

Ordem coordenada: editor/loja → brincadeira → cartaz → feed → entorno/visitantes → satisfação/presentes → integração final. Registrar alterações de contrato e garantir atualização do catálogo offline no app. Os detalhes de UX, composição e critérios de aceite estão no plano canônico.
