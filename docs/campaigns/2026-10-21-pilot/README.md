# Campanha-piloto de qualidade — 21/10/2026

Campanha `20261021T194312Z-953aa74e`. Preparação e piloto seguro concluídos; **não significa conclusão dos 95 cenários**. Não houve otimização, mudança em app/lib, alteração de regra/interface, merge ou modificação de main. O conteúdo de 472 arquivos de produto nas quatro cópias foi conferido contra os blobs Git, discriminando apenas a tradução LF/CRLF do checkout Windows: [verificação](product_source_verification.json).

## Versões congeladas e executores

| Conjunto | API | Flutter |
|---|---|---|
| A — oficial | `eced9c3873bc45c56343ce5d46dae6c1843f2a51` | `0ed31b211f31873d6f905116a0f0ccc5588e01c1` |
| B — refatorado | `ebb7f689965e9f103c677ce79e8488ee9ad57461` | `469b0abb7a8d7f65c75c6cf417d8112809e9e3f7` |

Infraestrutura commitada separadamente: `263ab82181296e480f60c86eeb73804e015de7e6`. Os executores efetivamente utilizados em cada tentativa foram congelados e identificados por SHA-256; pequenas correções do harness entre tentativas não alteraram os quatro códigos acima. A fórmula de quantil do helper foi ajustada posteriormente para nearest-rank em n múltiplo de 20; para n=30 efetivamente medido a posição é idêntica. O teste da borda foi incluído, sem recalcular artificialmente os resultados.

O [manifesto](source_manifest.json) registra versões instaladas/refs/clock/host; [índice de evidências](evidence_index.json) registra arquivos locais e hashes. Windows 10, CPU Intel64 Family 6 Model 58, 4 CPUs lógicas e cerca de 15,9 GiB RAM. Python/dependências instaladas foram os mesmos entre A/B, sem novas dependências. **Isso é comparação controlada dos fontes sob um runtime comum; não representa reprodução dos ambientes históricos de deployment.**

As cópias vieram de Git archives e não contêm .env real/conta de serviço. Nenhuma branch oficial foi aberta/resetada/mesclada. Bancos e schemas próprios, migrações somente no PostgreSQL descartável. O runner existente foi reutilizado; cada um dos três ambientes API/PostgreSQL foi removido ao terminar. PostgreSQL/Mailpit de uso não foram alvos dos experimentos.

## Infraestrutura e fixtures

[Reprodução e limites dos executores](../../../scripts/campaign/README.md).

- F-AC: contas A/B ativas, sessões reais/JWT para auth, inválido/expirado/revogado. Conta C, issuer/type/session mismatch e matriz completa ainda pendentes.
- F-CAT/F-HIST: geração determinística de IDs/datas/volumes; piloto remoto com 40 registros A, um B, tombstone e referências primária/front/left/back; local com 40 A + convidado + B/excluído. Históricos longos/revisão além da página/limites 499–501 não foram exercitados neste piloto.
- F-YARD: duas contas com 100 moedas/market nível 1; pedido chicken custa 12, duas chamadas concorrentes, replay após reconexão e fingerprint alterado.
- F-IMG: PNG sintético sem gato e arquivo inválido; manifesto/hashes e gerador limitado de bytes. Sem conjunto semântico de gatos ou imagem de 36 milhões de pixels. LAT-03 substitui a análise antes de qualquer inferência.
- F-SOC/F-NOT: geradores de registros/JSON determinísticos preparados, sem materialização da matriz completa no PostgreSQL. Os testes existentes de chat/inbox foram reutilizados na validação de conta B.
- F-NET: proxy restrito a loopback, delay e corte antes/depois de encaminhar; verificado em servidor próprio. Não há shaping de banda/perda de pacotes real. O replay Quintal deste probe usa commit/reconexão; **não é perda TCP comprovada no endpoint de produção**.
- REID: esquema de manifesto e bloqueio por ausência de fotos consentidas/identidades rotuladas. ACC: roteiros aprovados, aparelho/build/leitores e execução assistiva permanecem pendentes. E3/E4/E5 não foram executados.

O probe mede ASGI em processo + PostgreSQL real, sem lifespan/modelos; FCM/análise substituídos. O teste Flutter usa SQLite FFI real e a API pública comum `CatStore.loadCats`; caminhos de rede/background são substituídos em subclass exclusivamente do teste. Não mede câmera, GPU do celular, FPS, bateria ou entrega real.

## Tentativas e resultados

| RUN/grupo | Resultado | Evidência local relativa à campanha |
|---|---|---|
| Inicial — executores v1 | 4 probes API interrompidos antes dos casos por schema; 4 tentativas Flutter com wrapper incompatível. Mutantes bloqueados por baseline inválida. | `results/`, `flutter_results/`, `sources/B/api/.tmp/integration-e1a3eff2502e/pytest.log` |
| pilot-02 API | 4 probes A/B/B/A válidos + 2 mutantes: 6 casos pytest aprovados em 131,18 s. | `attempts/pilot-02/results/`, `sources/B/api/.tmp/integration-a9aa12dc7104/pytest.log` |
| pilot-02 Flutter | 4 probes SQLite A/B/B/A aprovados e 11 testes de controllers/inbox B aprovados, sem skips. | `attempts/pilot-02/flutter_results/` |
| pilot-03 API | Repetição sequencial sem compilação Flutter concorrente: 4 probes aprovados em 132,64 s. | `attempts/pilot-03/results/`, `sources/B/api/.tmp/integration-987e0f22908c/pytest.log` |
| Infraestrutura | 6 testes aprovados (última execução 3,09 s), Ruff limpo. | `harness-tests-final.log` |

Não somar suítes sobrepostas como casos únicos. Cada probe API tem 13 verificações selecionadas; no conjunto A, DB-05 é diagnóstico negativo preservado (conexão retida), não falha dispensada de B. **Não foram encontrados novos defeitos críticos do produto nos subcasos exercitados; isso não certifica segurança/integridade dos cenários completos.**

## Métricas observadas

| Mecanismo e dados | A oficial | B refatorado | Alcance |
|---|---|---|---|
| Pull de 40 registros: SELECTs incluindo autenticação | 123 | 6 | 121 vs 4 internos; mesmos 2 SELECTs de autenticação. Contagem constante nos samples. |
| Snapshot SQLite de 41 visíveis | 43 queries | 3 queries | Dados/conta/referências verificados nas quatro rodadas. |
| Conexões durante análise 503 substituída | 1 | 0 | Autenticação real + erro controlado; não mede IA nem todos os outcomes. |
| Payload pull | 24.509 bytes | 24.509 bytes | JSON integral idêntico nas quatro rodadas pilot-03. [Resposta sintética](canonical_pull_response.json). |
| Replay Quintal | saldo 88, uma tarefa/um recibo | saldo 88, uma tarefa/um recibo | Mesma chave concorrente + reconexão; fingerprint diferente rejeitado, B isolada. |

Latência **exploratória** em pilot-03, 5 aquecimentos e 30 samples/rodada, quantis nearest-rank:

| Rodada | P50 ms | P95 ms | Condição |
|---|---:|---:|---|
| 01-A | 582,69 | 2.922,48 | ASGI em processo, PG efêmero |
| 02-B | 30,56 | 60,57 | Mesmo cliente/dados/configuração |
| 03-B | 37,68 | 100,21 | Mesmo cliente/dados/configuração |
| 04-A | 803,35 | 3.747,36 | Mesmo cliente/dados/configuração |

Nesse piloto B teve menor custo/tempo nas rodadas observadas, mas há variabilidade considerável do host/IO e apenas dois blocos A/B por tentativa. **Não há IC inferencial, P99 confiável, taxa sustentável ou percentual estável de ganho aprovado.** pilot-02 ainda teve compilação Flutter concorrente e fica separado como rodada de diagnóstico. Não agrupar as duas tentativas como amostras independentes de uma campanha confirmatória.

No Flutter, os samples locais ficaram na ordem de dezenas de ms em A e cerca de uma dezena em B; [resumo exato por rodada](pilot_summary.json). Isso demonstra custo da leitura local no desktop, não melhora comprovada de UX móvel. Cada rodada tem 3 aquecimentos + 20 samples, sem P99/IC.

CPU por request, RSS inicial/final/pico e handles estão nos samples. Nos quatro processos API pilot-03, RSS final ficou aproximadamente entre 125,7 e 126,4 MiB; handles entre 202 e 207. Não foi demonstrada redução estável de memória/vazamento. CPU por request tem quantização grosseira no Windows. Queries têm duração individual registrada; planos PG, taxa de chegada aberta, throughput sob concorrência, IO/threads/filas/soak permanecem pendentes. Não usar inverso de latência sequencial como capacidade do servidor.

## Integridade, segurança e mutação

- SEC-01 parcial: ausente/inválido/expirado/revogado retornam 401 com sessões reais. O inválido utilizado é token malformado; assinatura estruturada incorreta, issuer/type e sessão de outro usuário ainda pendentes. SEC-02 parcial: pull de A e de B expõe apenas seus respectivos registros; MUT-01 comprova que a asserção detecta a retirada do filtro. O caso de mídia retornou 404, mas o registro B não tinha arquivo acessível de controle positivo: **não comprova ainda isolamento de mídia existente**.
- DATA-03/04 parcial: duas sessões concorrentes e replay mantêm tarefa/recibo/débito únicos; conteúdo diferente na mesma chave é recusado; chave igual em outra conta é independente. Falha antes do commit e perda TCP por proxy no fluxo completo ainda não cobertas aqui.
- RES-03 parcial em B: chat descarta envio atrasado após troca de conta; inbox ignora resposta antiga após clear/troca e aceita a nova. Os 11 testes incluem outros comportamentos existentes; não representam 11 variações de RES-03. Análise/catálogo/Quintal com resposta atrasada ainda pendentes.
- [Registro das 28 famílias](mutation_registry.json): MUT-01 e MUT-12 executados em cópias únicas; ambos mortos pelo comportamento esperado, sem usar syntax/import/infra error como morte. Tempos: 13,80 s e 15,53 s. Dois mutantes exercitados, **não score global das 28 famílias**. Demais famílias possuem mudança/oráculo catalogados, mas ainda requerem implementação de executores.

## Rastreabilidade HIP → cenário → código → executor → RUN → EV → PROB → FIX → REV

| HIP | Cenários/subcasos | Código avaliado nos SHAs da tabela inicial | Executor/resultado |
|---|---|---|---|
| HIP-01 isolamento | SEC-01/02 | API `services/auth_service.py:authenticated_user`, `routers/sync.py:pull/download_photo` | `api_probe.py`; RUN pilot-02/03 01-A/02-B/03-B/04-A; EV no índice |
| HIP-02 replay | DATA-03/04 | API `services/quintal_production.py:operation`, carteira/ledger | `api_probe.py:transact`; mesmos RUNs; saldo/tarefas/recibos registrados |
| HIP-03 custo pull | DB-02/LAT-04 | API `routers/sync.py:pull`, B `services/catalog_sync_queries.py` | samples SQL/perf counter + resposta integral, sem alterar handlers |
| HIP-04 conexão IA | DB-05/LAT-03 | API `main.py:analyze_cat` | Analyzer substituído registra checkout durante 503; A negativo/B positivo |
| HIP-05 leitura local | DB-01 | Flutter `stores/cat_store.dart:loadCats`, `data/cat_database.dart`; B coordinator/repositories | `flutter_probe_test.dart`; RUN pilot-02 Flutter 4 rodadas, dados verificados |
| HIP-06 conta atrasada | RES-03 | Flutter B `controllers/conversation_controller.dart`, `stores/notification_store.dart` | testes existentes copiados no SHA B; RUN pilot-02/RES-03 |
| HIP-07 força dos oráculos | MUT-01/12 | API B `routers/sync.py`/`services/quintal_production.py` em cópias mutantes | `mutation_cases.py`, diff e classificação próprios por mutante |

Os caminhos API da tabela são relativos a app/. Código histórico é identificado pelo SHA, não pelo estado móvel da branch. O executor está no commit separado; hashes das revisões efetivamente executadas ficam no índice/manifests locais. [Samples brutos comprimidos](pilot_samples.json.gz) e [resumo legível JSON](pilot_summary.json) preservam falhas e resultados. Dados são sintéticos; logs stdout volumosos permanecem locais sob `.tmp/quality-campaign/20261021T194312Z-953aa74e/` na raiz API. Arquivos cujo hash consta no índice devem ser arquivados antes de remover essa pasta. As amostras comprimidas e o manifesto sanitizado continuam no Git.

## Problemas e hipóteses priorizados

| ID / classe / prioridade | Evidência, impacto e estado | Hipótese/ação futura; FIX/REV |
|---|---|---|
| PROB-INF-01 / infraestrutura / P1 | Inicial: schema com maiúscula não selecionado, DDL atingiu public do DB descartável e falhou por tabela já existente. Nenhuma métrica válida naquela tentativa. | Causa reproduzida: resolução não citada de identificador PG. FIX-INF-01: schema minúsculo + assert current_schema antes do DDL, commit 263ab82. REV: pilot-02/03 válidos; initial preservado. |
| PROB-INF-02 / infraestrutura / P1 | Inicial: factory SQLite genérica rejeitada pelo sqflite. Nenhum dado de DB-01 válido. | FIX-INF-02: interface requerida pela versão instalada, só no wrapper do teste, commit 263ab82. REV: 4 probes Flutter aprovados. Dependência interna exige revalidação após upgrade. |
| PROB-INF-03 / lacuna do teste / P1 | Mídia B sem controle positivo; 404 isolado não prova confidencialidade de arquivo existente. Não é bug confirmado do produto. | Preparar arquivo sintético B, comprovar download por B e negar por A/sem token, inclusive variantes de referência/anexo/pista. FIX/REV pendentes. |
| PROB-HIST-01 / custo preexistente A / P2 | A retém conexão durante o erro de IA e usa mais consultas; B já possui a correção anterior. | Nenhuma correção nova; resultado histórico do mecanismo comprovado neste escopo. Validar demais outcomes/filas sob concorrência antes de prometer capacidade maior. |
| HIP-LAT-01 / limitação / P2 | Tempos oscilam apesar de separar compilação; não há IC/poder suficiente. | Host mais controlado, spans/planos e maior amostra dimensionada. Não iniciar otimização ou culpar DB/CPU sem evidência adicional. FIX/REV de produto não aplicáveis. |

Não houve nova correção do produto. Somente o harness teve FIX/REV. Nenhum achado urgente de segurança/integridade foi confirmado no produto nesta amostra.

## Matriz, bloqueios e próxima bateria

[Matriz dos 95 IDs](matrix.md): **13 parcialmente executados, 1 preparado, 60 pendentes, 21 bloqueados por pré-requisitos externos**. Nenhum cenário completo foi promovido automaticamente a aprovado. As 28 famílias de mutação são contadas separadamente.

Próxima bateria recomendada: SEC-03/05/07, completar mídia em SEC-02, DATA-02/05/06/07, RES-03 nos fluxos restantes; ampliar DB-02 para histórico/páginas e obter planos. Reutilizar PG/SQLite descartáveis e barreiras determinísticas, sem IA paga. Estimativa operacional inicial de 20–40 min de execução após implementar os casos, baseada em ~2 min por grupo API e ~1 min de compilação inicial Flutter deste piloto; preparação/revisão não incluídas, ajustar por host/readiness. Não iniciar carga extensa/soak como consequência automática.

Para BR, ainda falta o pacote Flutter+API ponta a ponta, dependências históricas, validação móvel e escopo completo de integridade. **OPT-0 ainda não foi declarado baseline aprovada**; preservar estes SHAs e resultados para revisão. REID exige dataset/rotulagem/consentimento e orçamento autorizado; ACC/UX/RSC móvel exigem aparelho/build/autorizações; SMTP/FCM exigem ambientes/destinatários próprios e autorização específica. Throughput/carga alta e perfis prolongados continuam fora desta primeira etapa.
