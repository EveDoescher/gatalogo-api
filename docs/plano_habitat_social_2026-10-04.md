# Plano da API — Habitat e Social

Data: 04/10/2026. Branch: `codex/habitat-social-improvements`, base `main` em `94e9827`. Esta rodada cria o plano; não implementa funcionalidades.

Plano completo e critérios visuais: [plano do Gatálogo](../../catlogue/docs/plano_habitat_social_2026-10-04.md), mantido no repositório irmão `catlogue`. Se os checkouts não forem irmãos, consultar `docs/plano_habitat_social_2026-10-04.md` na mesma branch do repositório `gatalogo`.

## Entregas da API

1. **Loja grátis para testes:** configuração de preço efetivo zero para itens compráveis, expansões e petiscos, preservando preços base, exclusividade de recompensas, saldo real, limites e idempotência. Catálogo informa `base_price`, `price`, `purchasable`, modo de teste e versão. Remover a associação implícita entre preço positivo e elegibilidade de compra. Validar também o modo com preços normais.
2. **Editor:** preservar IDs, inventário e revisão do layout ao mover/guardar/recolocar caminhos; verificar regras de camadas, água e pontes. A falha de gestos é investigada no app antes de alterar contratos desnecessariamente.
3. **Cartazes:** reutilizar detalhes públicos já persistidos. PNG/PDF são gerados no app; endpoint de geração no servidor não é necessário nesta primeira versão. Não incluir informações privadas adicionais nos arquivos exportados.
4. **Posts de texto:** tornar associação ao gato e mídia opcionais, exigir conteúdo útil, preservar publicações antigas, autorização de uploads, audiência, paginação, realtime e progressão. Ajustar o fingerprint/contrato para não depender de nome fictício de gato. Repost genérico e ranking de tendências ficam fora deste pedido.
5. **Satisfação:** calcular 0–100 para gatos presentes usando conforto acessível e cuidados reais. Separar da afinidade permanente; pesos configuráveis, média dos gatos e nenhum requisito de compra paga.
6. **Encontros e presentes:** serviço separado para elegibilidade/sorteio persistido por usuário/janela, presente pendente e resgate transacional. Proposta inicial: janelas de 20 minutos durante visitas, chance de 10–35%, até 3 presentes por dia e um pendente. Parâmetros serão calibrados. Persistir resultado/conteúdo; não ressortear com refresh ou troca de gatos. API controla tempo, saldo e inventário.

## Contratos e persistência propostos

- Snapshot do Habitat acrescenta `satisfaction` com componentes e `pending_gift`.
- Operação de encontro verifica a janela atual e retorna resultado existente ou um novo resultado elegível; unicidade por usuário/janela e limite diário.
- Resgate por ID de presente valida proprietário, trava carteira, aplica moedas/porções/itens e registra resultado idempotente. Repetição devolve o mesmo prêmio confirmado.
- `HabitatEncounter` guarda janela, probabilidade/satisfação usadas, visitante, conteúdo do presente e estado de resgate; migração preserva carteiras, afinidade e layouts.
- Inventário no limite usa conversão prevista para moedas. Um presente entregue permanece disponível após sair; nenhum sorteio/resgate é confirmado offline.
- Animação de brincar, vegetação, rotas de fauna e posições interpoladas são responsabilidade local do app. Visitante decorativo não concede prêmio.

## Validação

Cobrir compras gratuitas/normais, SKUs exclusivos, compras em lote e repetição; compatibilidade dos posts e audiência; regras de satisfação; relógio/RNG injetados; janelas sem repetição; limites diários; concorrência de resgate, proprietário incorreto, inventário cheio e recuperação após timeout. Aplicar migrações em banco descartável e executar a suíte completa antes da integração.

Ordem coordenada: editor/loja → brincadeira → cartaz → feed → entorno/visitantes → satisfação/presentes → integração final. Registrar alterações de contrato e garantir atualização do catálogo offline no app. Os detalhes de UX, composição e critérios de aceite estão no plano canônico.
