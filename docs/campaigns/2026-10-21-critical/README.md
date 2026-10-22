# Campanha crítica — continuação do piloto de 21/10/2026

A bateria ampliou segurança, integridade, resiliência, mutação e acessibilidade em ambiente descartável. **Nenhuma otimização, refatoração ou correção no produto foi aplicada.** Nenhum cenário inteiro foi promovido a concluído. Planos e evidências do [piloto anterior](../2026-10-21-pilot/README.md) continuam preservados.

## Resultado principal

| Execução | Resultado e interpretação |
|---|---|
| security-01 | Baseline 29 PASS; 13 mutantes detectados e MUT-07 sobrevivente. Exit 1 da campanha decorre do sobrevivente, não de falha da baseline. |
| security-02 | 33 PASS / 1 FAIL de fixture de email. Quatro mutantes bloqueados pelo gate; não classificados como detectados. |
| security-03 | Baseline 34 PASS, nenhum skip; MUT-07/08/09/16 detectados. |
| database-01 | Dois casos ampliados PASS; MUT-05 detectado nos dois históricos. |
| accessibility-01/02 | Execuções exploratórias com erros de lifecycle no executor; resultados não usados para aprovar células. |
| accessibility-03 | 4 células do controle de subida PASS; 4 células do login falharam por achados. Dez testes existentes de HabitatService PASS. |
| Harness / análise estática | 7 testes de infraestrutura PASS; Ruff nos scripts/testes PASS; análise Flutter do novo audit sem problemas. |

Contagens não são aditivas como casos únicos: houve repetições, parametrizações e refinamento entre tentativas. A última baseline geral tem 34 casos, e a expansão DB tem 2 casos adicionais executados separadamente, substituindo o caso DB anterior na cobertura.

Mutação: **19 execuções classificadas: 18 KILLED e 1 SURVIVED histórico**, envolvendo **17 variantes distintas em 16 das 28 famílias planejadas**. No resultado mais recente de cada variante, todas as 17 foram detectadas. Isso não certifica as outras 12 famílias nem todo defeito possível de uma família. [Histórico completo](mutation_history.json), [registro cumulativo](mutation_registry.json). MUT-10 tem duas variantes: savepoint e descarte de eventos.

## Segurança e integridade efetivamente verificadas

| Cenários | Variantes executadas | Limites e pendências |
|---|---|---|
| SEC-02 | PNG privado real primário/front: dono recebe 200 com bytes/hash/tipo corretos; duas outras contas 404; anônimo 401. Pull positivo por owner A/B; acesso estrangeiro ao Quintal. | Sem matriz completa de leitura/alteração/exclusão de pistas, anexos, análise, favoritos e demais mídias. Corrigida a lacuna do antigo 404 sem arquivo positivo. |
| SEC-03 | Amigo de ambos, mas fora da conversa: typing/read 403 sem publicação ou marcação indevida; membro autorizado positivo. | Serviço com publicação/FCM substituídos; não há sessão WebSocket TCP completa, mídia de conversa ou fuzz de JSON/UUID. |
| SEC-05 | Travessia POSIX/Windows, aninhada e absoluta em resolve/open/delete do storage; arquivo seguro positivo e sentinela externa preservada. | Não testa toda rota multipart, escrita/sufixos ou symlinks. |
| SEC-07 | Limite de OTP e persistência após rollback, expiração exata, purpose/email, consumo/reuso, cooldown 429 e código anterior invalidado; rotação refresh, token antigo 401, logout e revogação 401. | Sem entrega SMTP/FCM, provedor OAuth ou matriz JWT completa. |
| DATA-02 | PostgreSQL: falha de progressão com rollback de análise/eventos e savepoint; mutações independentes detectadas. | Falha após gravação de arquivo, órfãos/cleanup e toda fronteira DB+filesystem ainda pendentes. Não se presume atomicidade conjunta. |
| DATA-05 | Cinco decisões de conflito: data antiga/nova/igual, device IDs nas duas ordens e tombstone. | Teste direto da função de decisão; persistência HTTP completa e preservação de análise autoritativa pendentes. |
| DATA-06 | Lease, cancelamento/restart, skip_locked, retry terminal/savepoint e recuperação; gato/foto excluídos ou hash substituído entre claim/finish. Novo teste observa tentativa antiga enquanto a nova ainda está running. | Análise substituída; nenhum custo de IA. Não cobre toda combinação de status/hash/evento/lease no transporte. |
| DATA-03/04/07 | Mesma intenção concorrente, replay, payload alterado recusado, mesma chave em conta diferente, ledger por owner; saldo 100→88, um recibo; coleta estrangeira/antecipada recusada, prazo−1ms/0/+1ms, recompensa única 2. | Não cobre toda tabela de preço/estoque/saldo insuficiente nem relógio de aparelho físico. |
| RES-03 | Dez testes existentes de HabitatService, incluindo resposta de cuidado após troca/dispose, chaves persistidas para retry e resposta antiga de placement; mantém chat/inbox anteriores. | Parte dos dez verifica geometria/cache e não é cobertura extra de RES. Catálogo/análise e todas variantes logout/owner/evento ainda pendentes. |

Baseline chama a API por ASGI sem lifespan, com PostgreSQL real descartável e schemas próprios. Asserções de concorrência/rollback usam sessões reais; não representam carga prolongada ou entrega externa. Um mutante só é detectado quando uma asserção do oráculo esperado falha durante a execução, com baseline limpa; erro de setup/importação/skip não conta.

## Histórico, SQL e desempenho

DB-02 usou 40 gatos e históricos de 401 / 4.001 mudanças. Limites 1/200/500 no primeiro e 200/500 no segundo, todos até a página vazia, somando 440 observações de paginação. Cursor consumido, ordem/deduplicação, 40 identidades e revisão mais recente além da primeira página foram comparados com um oráculo independente. **4 SELECTs internos por página não vazia, 1 na vazia; autenticação excluída.** O mutante que faz cursor saltar para a revisão da entidade foi detectado nos dois volumes.

Foram registrados EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) do MAX agrupado e tempos por página em [observações](observations.json). Os tempos incluem o oráculo e setup de sessão: são diagnósticos, sem IC e sem P99, não estimativas confirmatórias de endpoint. Não houve nova campanha A/B de latência, throughput ou consumo de recursos. As medições anteriores permanecem exploratórias, sem declarar ganho definitivo.

O [protocolo confirmatório](confirmatory_protocol.json) prepara ordem A/B/B/A, aquecimento condicionado à estabilidade, blocos independentes, precisão de IC e amostragem por custo. O orçamento inicial de 1000 requisições por célula não garante precisão e não foi executado. Coletores de estacionariedade, IC por blocos e orçamento de tempo ainda precisam ser implementados antes da campanha. A versão B permanece baseline de otimizações futuras, após a comparação oficial confirmatória.

## Acessibilidade sem aparelho

Audit opt-in em `catlogue/tool/campaign_accessibility_test.dart`, fora da coleta habitual. Tema real AppTheme.lightTheme, fontes locais Nunito/Fredoka aguardadas e runtime fetching desativado. Login inicial em 320/412×800, escala 1/2; controle de subida em 320×800, escala 1/2 e redução de movimento ligada/desligada. Guidelines do SDK para contraste, alvos Android 48/iOS 44 e rótulos, mais leitura/ativação de Semantics e detecção de truncamento.

Achados reproduzidos em `accessibility-03`, sem alterar o produto:

- **ACC-F01:** Entrar tem contraste 2,55 frente ao critério 4,5; subtítulo 3,02 frente a 4,5 em escala 1; criar conta também foi sinalizado. Razões/cores compostas por célula no JSON.
- **ACC-F02:** alvo Google com 42 de altura; recuperação de senha 16/31 nas escalas 1/2; criar conta 26 em escala 1. Bounds semânticos abaixo dos mínimos aplicáveis. Hit testing de vizinhos ainda pendente.
- **ACC-F03:** “Continuar com Google” excede as linhas disponíveis nas duas larguras com escala 2. Com fontes corretas, escala 1 não apresentou esse truncamento. Sem exceção de layout capturada; isso não prova que todo o fluxo seja inacessível.

Controle de subida: nome/valor/ação significativos, ativação semântica altera estado, duração 90ms normal e 0 com redução de movimento; quatro células passaram em todos os critérios testados. Sem aprovação global de Semantics, movimento ou aplicativo. TalkBack/VoiceOver, outros estados/telas, rolagem, teclado/insets, escala 1,3, viewports oficiais completos e screenshots anotados permanecem pendentes. Critério iOS avaliado como guideline no host, não como execução em iPhone.

[Problemas e ciclo hipótese→teste→resultado→correção→revalidação](issues.json): três achados de produto abertos com recomendações; três lacunas de executor corrigidas e revalidadas. Alterações propostas em cores/layout podem afetar aparência e exigem avaliação posterior. Nenhuma brecha de autorização ou corrupção foi reproduzida nas variantes de baseline executadas; isso não certifica segurança global.

## Rastreabilidade, preservação e versões

[Manifesto](source_manifest.json) liga as quatro versões congeladas e os commits de executores. São os mesmos códigos do piloto:

| Braço | API | Flutter |
|---|---|---|
| A/main | eced9c3873bc45c56343ce5d46dae6c1843f2a51 | 0ed31b211f31873d6f905116a0f0ccc5588e01c1 |
| B/refatorado | ebb7f689965e9f103c677ce79e8488ee9ad57461 | 469b0abb7a8d7f65c75c6cf417d8112809e9e3f7 |

[Verificação após a bateria](product_source_verification.json) confirma novamente 472 arquivos app/lib contra blobs Git, permitindo só tradução LF/CRLF. Mutantes tiveram cópias próprias; nenhum app/lib do checkout foi editado. Dependências instaladas compartilhadas como no piloto; não se reproduziu deployment histórico.

[Índice de evidências](evidence_index.json) contém caminho local, tamanho e SHA-256; [rastreabilidade](traceability.json) relaciona teste/cenário/código/SHA/executor/resultado. Hashes dos executores congelados em cada tentativa prevalecem sobre edições posteriores de comentário/import. O commit de consolidação deste relatório é posterior ao commit do executor e é localizável por `git log -- docs/campaigns/2026-10-21-critical`.

Antes da bateria foram arquivados 143 membros de evidência; após a bateria, 451 membros, todos conferidos individualmente. [Recibos dos arquivos locais](archive_receipts.json). Nenhum original foi removido. Os tar.gz, JUnit e logs completos permanecem privados em `.tmp`; podem conter credenciais efêmeras de contas descartáveis. Somente resumos sintéticos e observações sem tokens são versionados. O índice registra também a observação final de saúde: serviços originais PostgreSQL/Mailpit saudáveis, API 8000 retornando 200; containers descartáveis removidos. O arquivo de saúde foi acrescentado depois do segundo arquivo tar e permanece no diretório original.

**Errata BR-04:** a classificação parcial anterior estava incorreta; não houve experimento de versões cruzadas cliente/API. A [matriz atual](matrix.md) corrige para PENDING, preservando o piloto como registro histórico. Há 25 cenários parciais, 1 preparado, 55 pendentes e 14 bloqueados por pré-requisitos externos, zero concluídos. ACC-08 é pendente para variantes desktop ainda não executadas; leitores reais continuam bloqueados nesta etapa.

## Próxima bateria recomendada

1. Completar SEC-02 recurso×ação×ator, SEC-03 transporte/UUID/anexos e SEC-05 rotas/escrita/sufixos, com controles positivos em cada recurso.
2. DATA-02 gravação de arquivo e órfãos; DATA-05 sincronização HTTP completa; DATA-07 saldos/estoques e demais limites. Completar RES-03 catálogo/análise e logout/owner/dispose.
3. Mais defeitos nas 12 famílias restantes de mutação e nos mecanismos ainda não exercitados; não confundir 17 variantes com 28 famílias completas.
4. ACC-08 erros/progresso, navegação/rolagem/teclado e demais resoluções, preservando o audit vermelho para achados abertos. Leitores em aparelho ficam para etapa autorizada.
5. Implementar coletores do protocolo confirmatório e validar estabilidade antes da medição A/B. Manter IA paga, carga longa e dispositivos fora desta etapa.

Reprodução e limites: [executores](../../../scripts/campaign/README.md). Os planos originais da [API](../../test_plan_efficiency.md) e do [Flutter](../../../../catlogue/docs/test_plan_efficiency.md) continuam como definição integral de variantes; a matriz registra somente o subconjunto executado.
