# Plano da API — Quintal e Social

Data: 04/10/2026. Branch: `feature/habitat-social-improvements`, base `main` em `94e9827`. Implementação aplicada; evidências e limites estão registrados abaixo.

Plano completo e critérios visuais: [plano do Gatálogo](../../../catlogue/docs/product/plano_habitat_social_2026-10-04.md), mantido no repositório irmão `catlogue`. Se os checkouts não forem irmãos, consultar `docs/product/plano_habitat_social_2026-10-04.md` na mesma branch do repositório `gatalogo`.

## Entregas da API

1. **Loja grátis para testes:** configuração de preço efetivo zero para itens compráveis, expansões e petiscos, preservando preços base, exclusividade de recompensas, saldo real, limites e idempotência. Catálogo informa `base_price`, `price`, `purchasable`, modo de teste e versão. Remover a associação implícita entre preço positivo e elegibilidade de compra. Validar também o modo com preços normais.
2. **Editor:** preservar IDs, inventário e revisão do layout ao mover/guardar/recolocar caminhos; verificar regras de camadas, água e pontes. A falha de gestos é investigada no app antes de alterar contratos desnecessariamente.
3. **Cartazes:** reutilizar detalhes públicos já persistidos. PNG/PDF são gerados no app; endpoint de geração no servidor não é necessário nesta primeira versão. Não incluir informações privadas adicionais nos arquivos exportados.
4. **Posts de texto:** apoiar uma tela de criação semelhante ao composer do Twitter, com texto amplo, audiência, anexos e associação opcional a gato. Tornar associação ao gato e mídia opcionais, exigir conteúdo útil, preservar publicações antigas, autorização de uploads, audiência, paginação, realtime e progressão. Ajustar o fingerprint/contrato para não depender de nome fictício de gato. Falha/repetição preservam rascunho e não duplicam posts. Repost genérico e ranking de tendências ficam fora deste pedido.
5. **Satisfação:** calcular 0–100 para gatos presentes usando conforto acessível e cuidados reais. Separar da afinidade permanente; pesos configuráveis, média dos gatos e nenhum requisito de compra paga.
6. **Encontros e presentes:** serviço separado para elegibilidade/sorteio persistido por usuário/janela, entrega pendente e resgate transacional. Proposta inicial: janelas de 20 minutos durante visitas, chance de 10–35%, até 3 presentes por dia e um pendente. Parâmetros serão calibrados. Persistir resultado/conteúdo; não sortear novamente com refresh ou troca de gatos. API controla tempo, saldo e inventário. Visitantes decorativos continuam presentes sem prêmio, mesmo com presente pendente ou limite diário atingido.

## Contratos e persistência propostos

- Snapshot do Quintal acrescenta `satisfaction` com componentes, visita recompensável e `pending_gift` com ID, estado de entrega e âncora externa.
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


## Registro da implementação — 04/10/2026

Loja zero, posts de texto, satisfação, visitas, entregas e resgates estão implementados. Catálogo versão 5 informa preço base, preço efetivo e elegibilidade de compra. A audiência e autorização de fotos permanecem verificadas; `request_id` identifica tentativas de publicação e retorna o post anterior apenas para conteúdo idêntico.

A proposta de criar `HabitatEncounter` foi substituída pelo uso de estado limitado no JSON `HabitatWallet.care_state`: janela já avaliada, contagem diária, visita/presente pendente e porções extras. Não é necessária migração e não há reset de dados. O prêmio e a espécie do entregador são persistidos no sorteio. A coleta trava a carteira e registra `HabitatTransaction` por `gift:{id}`; repetição retorna o prêmio anterior. Notificação única nasce na disponibilidade, com evento/publicação após commit. Leitura não coleta. Recuperação após interrupção torna a entrega disponível a partir do snapshot.

Configuração padrão em `.env.example`: loja grátis; janela de 20 minutos; até três presentes por dia UTC; chance 10% + até 25% pela satisfação. Pesos, chance e limites são parâmetros de `Settings`. Conforto exige mobília alcançável; cuidado exige registros reais do dia. SKU no limite é convertido em 12 moedas. Visitantes decorativos e âncoras externas relativas à expansão são apresentados pelo app e não concedem saldo.

Validação: 143 testes passaram, 23 integrações foram ignoradas pelo ambiente. Foram cobertos preços normais/grátis, recompensa exclusiva, repetição de compras, sorteio sem rerrolagem, limites, recuperação, notificação única, proprietário, inventário cheio, porções/itens, conforto/cuidados e publicação de texto sem duplicação. O banco em memória não comprova concorrência PostgreSQL; confirmar em homologação antes da publicação. Não houve deploy remoto.


### Fluxo de alerta e personalização do cartaz — 05/10/2026

A criação do alerta reúne gato, foto e área de busca. Após ativar, abre a personalização do cartaz, mantendo o detalhe do alerta acessível ao voltar. Telefones e WhatsApp deixam o formulário inicial e passam ao editor reutilizável do cartaz. O tutor escolhe endereço, data, coleira, marcas, microchip, QR code e quais dos até cinco telefones divulgar. A prévia, PNG, PDF e detalhes públicos usam a mesma seleção. Telefones desmarcados são preservados somente na resposta do tutor; a projeção pública nunca os retorna.

`PUT /vision/missing-reports/{id}/poster` salva opções e contatos em `poster_details`, sem nova tabela. Somente o tutor pode editar; alerta encerrado responde 409. `POST /social/reports/{id}/publish` publica o cartaz do tutor vinculado ao alerta, com idempotência por busca/usuário. Reposts continuam em sua rota própria; o feed abre o caso para compartilhar a arte completa. As ações de exportação são um widget reutilizável nos cartazes próprios e nos casos da comunidade, com validação e salvamento antes de divulgar.

O QR passa a apontar para `${API_BASE_URL}/public/busca/{id}`, uma página implementada na API que não exige login. A página e sua foto retornam `no-store` e não são indexáveis; informações ocultas não entram no HTML. Desmarcar QR revoga a página enquanto ativa. Após encerrar, a página informa o encerramento e não mostra foto nem contatos; o endpoint da foto também bloqueia acesso. Arquivos já salvos ou enviados por terceiros não podem ser recolhidos.

Para que QR codes funcionem fora da rede local, gerar o aplicativo com `API_BASE_URL` configurado para uma API HTTPS pública, com estas rotas implantadas. O endereço local de desenvolvimento não é um domínio público. Não foi realizado deploy nem envio de cartazes a terceiros nesta rodada.

Validação: testes de modelo/exportação e controles Flutter, análise estática, testes de persistência/projeção pública/idempotência da API e galeria permanente. Os testes com PostgreSQL descartável continuam opcionais; integração nativa de impressão/compartilhamento precisa de validação em aparelho.
