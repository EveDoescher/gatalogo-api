# Plano de testes de eficiência, integridade e experiência

Escopo vigente em 23/10/2026: [fechamento sequencial, correções autorizadas e exclusão de iOS](campaigns/2026-10-23-sequential/README.md). As definições históricas abaixo são preservadas; iOS/VoiceOver não contam como pendência da campanha Android/API.

Data: 21/10/2026. API e Flutter, branch `code-quality-cleanup`.

Execução posterior ao planejamento aprovado: [preparação, matriz e resultados da primeira campanha-piloto](campaigns/2026-10-21-pilot/README.md). Os estados abaixo descrevem o plano original; a matriz do relatório registra o andamento real sem sobrescrever o planejamento.

Revisão documental: comparação oficial/refatorada, acessibilidade, reidentificação, amostragem justificada e rastreabilidade. Os 74 cenários e as 28 famílias de mutação originais foram preservados; novos IDs são aditivos.

**Status: especificação para revisão. Nenhum cenário desta campanha foi executado nesta etapa.** Não foram implementados instrumentação, geradores de carga ou mutantes. Referências a testes existentes são pontos de partida, não certificação de cobertura integral dos casos propostos.

Objetivo: medir e melhorar mecanismos mantendo telas, textos, navegação, animações, regras, autorização, qualidade das imagens e resultado funcional. Após a revisão: preparar fixtures, medir baseline, analisar resultados, mudar uma causa por vez e repetir os mesmos casos.

## 1. Estudo do código e implicações

| Fluxo | API: caminhos relativos a app/ | Flutter: caminhos relativos a lib/ | Implicação para os testes |
|---|---|---|---|
| Foto principal | `main.py:analyze_cat`, `services/analysis_service.py`, `services/analysis_cache.py` | `pages/capture_page.dart`, `stores/cat_store.dart`, `services/analysis_api_service.dart` | Separar preflight, metadados, upload, inferência, commit, aplicação local e reconhecimento posterior. Resposta inicial não significa fim dos jobs. |
| Sincronização | `routers/sync.py`, `services/catalog_sync_queries.py` | `services/catalog_sync_service.dart`, `catalog_sync_coordinator.dart`, `data/repositories/` | Cursor consumido difere da revisão mais recente apresentada; preservar dirty, tombstones e desempate por dispositivo. |
| Referências/reconhecimento | `services/reference_analysis.py`, `recognition_service.py`, `vision_jobs.py` | `services/vision_api_service.dart`, `models/cat_reference_photo.dart` | Testar lease, tentativa, hash, exclusão e resultado atrasado com barreiras de concorrência. |
| Conversas | `services/chat_queries.py`, `conversations.py`, `routers/websocket.py` | `controllers/conversation_controller.dart`, `services/websocket_service.dart` | Eco WS antes/depois do HTTP, recibos e autorização; fallback atual de polling de 30 s. |
| Comunidade/resgate | `services/social_feed_queries.py`, `audience.py`, `routers/product.py`, `public_search.py` | `services/social_api_service.dart`, páginas de resgate, `widgets/private_clue_photo.dart` | Paginação após filtragem; não expor pistas/localizações privadas para reduzir consultas. |
| Quintal | `routers/quintal.py`, `routers/habitat.py`, `services/quintal_production.py`, `quintal_commands/` | `services/habitat_service.dart`, `data/habitat_repository.dart`, `game/` | GETs podem reconciliar produção/recompensas ou recuperar presentes. Carteira, fingerprint idempotente e relógio do servidor fazem parte do resultado. |
| Conta/entrega | `services/auth_service.py`, `dependencies.py`, `services/fcm_service.py` | `stores/session_store.dart`, `notification_store.dart`, `controllers/account_activity_controller.dart` | OTP errado persiste tentativas mesmo ao retornar erro; inbox já combina refresh concorrente e descarta respostas antigas por geração. |

Melhorias e contagens anteriormente medidas: [performance_flows.md](performance/performance_flows.md). Não são resultados novos desta campanha.

## 2. Ambientes e protocolo comum

### Ambientes

- **E1 determinístico:** SQLite real temporário, domínio, relógio/RNG controlados e externos substituídos quando declarado. Não representa rede, locks PostgreSQL, IA real ou telefone.
- **E2 integrado:** API, PostgreSQL migrado, storage/cache e proxy de falhas próprios e descartáveis. Aproveitar `scripts/run_integration_checks.py`; o executor atual não implementa toda esta campanha de benchmark.
- **E3 IA real:** E2 com modelos/checkpoints/provedor identificados, fotos consentidas, orçamento e limite de chamadas. Separar carregamento de modelo, cache hit/miss e processamento. Respostas estocásticas independentes não exigem JSON idêntico: regressão dos mecanismos usa resposta gravada; qualidade real usa conjunto rotulado separado.
- **E4 celular físico:** profile Flutter, aparelho/OS/display/rede identificados. Exige preparação posterior de build e acesso ao aparelho. Nesta etapa não haverá build Android ou emulador. Widget/FFI desktop não comprova FPS, câmera, bateria ou fluidez móvel.
- **E5 entrega:** SMTP/Mailpit próprios e, separadamente, projeto FCM/token/dispositivo de teste. Aceite pelo provedor, recepção e exibição são eventos distintos.

Não usar banco, API, Mailpit ou arquivos do ambiente de uso para carga/falhas. Migrações somente na cópia descartável. Mutantes somente em cópias isoladas sem segredos; não modificar main nem o código em uso. Nenhum destinatário real recebe mensagens desta campanha.

### Fixtures a preparar

| Código | Dados reprodutíveis |
|---|---|
| F-AC | Contas A/B/C e convidado local G; A/B amigos, C sem acesso privado; duas sessões de A; tokens sintéticos válidos/expirados/revogados. |
| F-CAT | 0/1/40/499/500/501/2.000 gatos da conta, mais G e registros B; análises ausentes/presentes, quatro ângulos, referências excluídas, tombstones e campos desconhecidos. Volumes maiores são exploração, não promessa de escala. |
| F-HIST | 1/10/100 revisões por gato; páginas 1/200/500; mesmo gato entre páginas e revisão nova além do cursor consumido. |
| F-IMG | Manifesto com SHA-256, origem/permissão, bytes, dimensões, EXIF e expectativa: gato válido, sem gato, múltiplos gatos, escura, borrada, rotacionada, JPEG/PNG, corrompida e arquivo local ausente. Bytes B e pixels P lidos da configuração: B−1/B/B+1 e P−1/P/P+1, com arquivo válido quando aplicável. |
| F-SOC | 0/1/20/100 conversas, vazia/texto/foto/resposta/editada/excluída/resgate, recibos distintos, 0/100/1.000 mensagens; feed com autores repetidos/distintos, público/privado, timestamps iguais e páginas dominadas por itens invisíveis. |
| F-YARD | Quintal vazio/médio/denso dentro das regras, níveis/saldo/estoque conhecidos, tarefa antes/no/depois do prazo, presente pendente e rascunho local. Relógio/RNG controlados em E1/E2. |
| F-NOT | Inbox 0/19/20/21/100, timestamps iguais, lidas/não lidas, vários devices com preferências diferentes e um token inválido de teste. |
| F-NET | Rede local; RTT alvo 150 ms e 10/2 Mbit/s down/up; RTT 400 ms e 1/0,5 Mbit/s; offline; request aceito com resposta perdida. Confirmar RTT observado. Falha determinística por estágio antes de perda aleatória com seed. |

Não executar produto cartesiano completo. Combinar bordas com conta/concorrência/falha nos pontos críticos; registrar os parâmetros de cada variante.

### Método e critérios

1. Fixar SHAs API/Flutter, dependências, schema, configuração não secreta, hardware/recursos Docker, processos/workers, modelos e seed. Definir snapshot inicial e efeitos esperados antes da rodada. A primeira campanha compara `main` com `code-quality-cleanup` conforme seção 2.1, sem alterar `main`. Depois, o par de SHAs refatorado aceito vira baseline das otimizações futuras.
2. Separar processo/modelo/cache frio de operação aquecida. Os números iniciais da versão anterior (20 aquecimentos/1.000 amostras HTTP, 30 repetições móveis e piloto IA de 20) permanecem como referências de planejamento, **não como tamanho suficiente ou regra universal**. Dimensionar e justificar cada execução pela seção 2.2; nenhum percentil extremo é confiável apenas por atingir esses números.
3. Usar relógio monotônico por processo e IDs sintéticos de correlação. Medir cliente, auth, DB, storage, IA, commit, jobs e UI separadamente. Não subtrair relógios de máquinas diferentes sem sincronização nem somar spans paralelos como se fossem sequenciais.
4. Guardar amostras brutas, n, unidade, P50/P95/P99 quando apropriado, máximo, throughput, status e erros/timeouts/cancelamentos. Incluir duração dos erros; timeout é observação censurada, não amostra descartável nem sucesso no limite. Declarar método de percentil e dispersão/intervalos entre rodadas.
5. Carga fechada por concorrência e aberta por taxa de chegada. Registrar demanda oferecida/aceita/concluída, fila e atraso do próprio gerador para não esconder saturação. Gerador em processo separado, monitorado.
6. Validar contrato e estado em cada rodada. Exposição, perda de dados ou débito duplicado reprova a mudança mesmo se mais rápida. Não normalizar ordem, campos desconhecidos ou erros relevantes. IDs/datas não determinísticos só com regra registrada e comparação semântica adicional.
7. Não existem SLOs novos aprovados. Primeira rodada caracteriza baseline. Proposta de alerta para investigação: P95 >10% pior ou tráfego/memória >5% pior, repetidos em 3 rodadas e acima do ruído observado. Não é aprovação automática nem requisito do produto. Integridade/segurança têm tolerância zero.
8. Interromper ensaio por OOM/reinício inesperado, disco de testes >80%, 5xx/timeouts >5% por 60 s ou backlog sem recuperação por 2 min. Registrar interrupção e recuperar apenas serviços da campanha. Limites protegem o ensaio; não prometem capacidade.

### 2.1 Campanha BR — comparação entre branches

Comparar duas versões completas e identificadas: **A oficial = API main + Flutter main**; **B refatorada = API code-quality-cleanup + Flutter code-quality-cleanup**. Avaliar latência, recursos, SQL, throughput e integridade com os mesmos cenários aplicáveis. Não fazer checkout, merge, reset, alteração ou push em `main`; usar cópias isoladas em commits fixos. Nenhuma cópia será criada nesta etapa documental.

Referências locais verificadas em leitura nesta revisão, não execução nem validação de que os remotos permanecem nestes commits:

| Projeto | main local — candidato A | code-quality-cleanup antes desta revisão documental — candidato B |
|---|---|---|
| API | `eced9c3873bc45c56343ce5d46dae6c1843f2a51` | `2324e290e1684b3b44bb7717460e884089a78c5e` |
| Flutter | `0ed31b211f31873d6f905116a0f0ccc5588e01c1` | `88e150fca6e4794d1bf70832a932600c90f32489` |

No preparo, resolver novamente cada ref, verificar se `main` ainda representa a versão oficial anterior e registrar **quatro SHAs completos**, commits do plano/executor e estado limpo das cópias. Se a ref mudou, revisar a escolha explicitamente; não chamar uma versão posterior de baseline histórico. Não registrar nome móvel de branch como única identificação. Documentação pode avançar sem alterar o código medido; registrar os SHAs de fonte e documento separadamente.

- **Dados/configuração:** seed e manifesto idênticos, mesmos IDs/datas, storage/fotos/hashes, histórico e volumes. Restaurar snapshot inicial antes de cada rodada; caches privados por versão, vazios ou preenchidos pelo mesmo procedimento. Não compartilhar banco vivo nem cache mutável entre A/B. Se schemas diferirem, produzir estado lógico equivalente usando fixtures versionadas, registrar schema/migrações de cada lado e comprovar equivalência; não migrar dados reais.
- **Execução:** mesmo host/aparelho/OS/CPU/RAM/limites, DB/driver, rede/proxy, processos/workers, versões de modelo, condições térmicas e carga externa. Alternar ordem A→B/B→A em blocos pareados; não rodar A e B simultaneamente disputando hardware. Separar frio/quente e repetir em sessões independentes conforme 2.2.
- **Dependências:** configuração comum onde suportada, preservando parâmetros de resultado. Registrar locks/runtimes de cada branch. Se dependências/modelos exigidos diferirem, a comparação principal é do pacote entregue e o efeito inclui essa diferença; não atribuir tudo à arquitetura. Um braço controlado com dependências comuns só é permitido se compatível, como experimento adicional identificado, sem editar `main`.
- **Instrumentação equivalente:** harness externo versionado comum, observação SQL/pool/CPU igual e overhead avaliado. A versão oficial pode não conter os testes/helpers novos; adaptar o executor externo ao contrato de cada versão e comparar o mesmo efeito observável, sem copiar implementação refatorada para A. Testes exclusivos de um mecanismo novo ficam como diagnóstico B, não comparação inexistente.
- **Atribuição:** comparação principal mede o conjunto completo A vs B. Quando os contratos permitirem, executar braços API A/B com cliente fixo e Flutter A/B com API fixa para localizar origem de diferenças. Registrar compatibilidade e quatro SHAs em cada braço; incompatibilidade é bloqueio, não zero ou regressão automaticamente.
- **Decisão:** preservar os casos e investigar diferenças funcionais/segurança em vez de normalizá-las. Mostrar valores A/B, diferenças absolutas/relativas, incerteza e custo por operação. SQL esperado em DB-01/02 e pool em DB-05 descrevem B; **não exigir essas contagens otimizadas de A**. Ambos passam pelo mesmo oráculo funcional independente. Bug histórico identificado é achado, não autorização para perpetuá-lo ou alterar funcionalidades nesta etapa.
- **Transição:** após revisão dos resultados, congelar o par B aceito como baseline OPT-0 para futuras otimizações. Não substituir retrospectivamente o relatório A/B. Cada candidato posterior aponta para OPT-0 (ou nova baseline aprovada com cadeia de versões), sem usar `main` móvel.

| ID / prioridade / ambiente | Preparação e ação | Métricas e resultado esperado |
|---|---|---|
| BR-01 P0 E1/E2 | Resolver refs, preparar A/B isolados com fixtures/snapshots equivalentes e harness comum. | Quatro SHAs, hashes/configuração/schema e manifesto; qualquer diferença impeditiva bloqueia a comparação correspondente. |
| BR-02 P1 E2 | Repetir LAT/LOAD/DB/RSC aplicáveis em blocos pareados nas duas versões. | Latência, CPU/RAM/IO/pool, SQL, throughput/erros sob mesma demanda; contagens específicas de B são medidas, não requisitos impostos a A. |
| BR-03 P0 E1/E2/E4 | Repetir DATA/RES/SEC e percursos UX/ACC nas duas versões, incluindo perda de resposta e conta. | Diffs de estado/contrato/visibilidade; classificar regressão, problema preexistente ou inconclusivo com evidência de cada lado. |
| BR-04 P1 E2/E4 | Quando compatível, fixar cliente para variar API e fixar API para variar Flutter. | Separar contribuição de cada projeto; registrar incompatibilidades e limites de atribuição, sem confundir braço controlado com pacote entregue. |
| BR-05 P0 documental | Revisar comparação A/B, congelar SHAs B e vincular campanha OPT-0. | Baseline futura explícita, relatório histórico preservado e nenhuma modificação em main. |

### 2.2 Amostragem, repetições e incerteza

Antes da coleta principal, registrar métrica primária, unidade independente, precisão desejada ou menor diferença de interesse, orçamento/teto, regras de exclusão, aquecimento e parada. Esses objetivos serão aprovados com a campanha; não escolher n, limiar ou método depois de ver qual favorece uma branch. Piloto dimensiona variância/correlação/custo, não comprova melhoria. Se o orçamento não permitir precisão necessária, reduzir a conclusão ou declarar inconclusivo.

| Experimento | Unidade e dimensionamento proposto | Repetições, aquecimento e limites |
|---|---|---|
| Endpoint rápido/SQL | Milhares de requests por célula são viáveis. Piloto de 100–200 requests estima distribuição/correlação e custo. Definir n por precisão do IC do percentil/diferença, não só por média. Para proporção independente, aproximação conservadora n≈z²p(1−p)/e² orienta planejamento; validar condições, usar p=0,5 sem estimativa e não aplicar a percentis. | Começar com 3 blocos A/B independentes, expandir conforme dispersão e precisão fixadas. 20 warmups são partida; pré-definir estabilidade de janelas de latência/CPU e verificar carga de modelo/cache. Se não estabilizar até teto, classificar frio/não estacionário. |
| Carga/soak | Unidade é janela/bloco em patamar, não cada request independente. Duração suficiente para estabilizar e observar fila; 3 min/patamar e soak de 60 min dos cenários são pontos iniciais, ajustados após piloto. | Repetir patamares sustentáveis em sessões independentes e alternar A/B. Bootstrap por blocos/janelas com comprimento estimado pela autocorrelação; sem IC ingênuo de requests correlacionados. |
| Cold start/móvel/energia | Unidade é inicialização/sessão pareada no aparelho. Piloto mede dispersão térmica/cache; 30 inícios ou sessões de 15 min são orçamento inicial, não poder estatístico garantido. Dimensionar pela diferença relevante e variação entre sessões. | Alternar A/B, restaurar condições iniciais e repouso; se população alvo inclui vários aparelhos, medir estratos de hardware. Milhares de frames do mesmo percurso não são milhares de usuários independentes. |
| IA real: custo/latência | Unidade é foto/caminho ou sessão de inferência, estratificada por qualidade/ângulo/cache. Piloto de até 20 por estrato viável estima custo/tempo; ampliar conjunto até precisão/orçamento pré-fixados. | Processamento determinístico: 1 resultado de qualidade por foto, repetições só para tempo; componente estocástico: iniciar com 3 repetições/foto e aumentar se variância entre chamadas dominar. Não usar repetição da mesma foto para inflar diversidade. Separar warmup de modelos e cache miss de hit. |
| Reidentificação/classificação | Unidade primária é indivíduo/sessão fotográfica; pares compartilham gatos e não são independentes. Piloto de identidades para verificar rótulos/viabilidade; n final por IC/risco alvo e suporte por estrato. | Particionar por indivíduo e sessão (seção REID). Bootstrap hierárquico/por identidade para precision/recall/F1/diferenças, preservando a estrutura de pares. Não apresentar todos os pares combinatórios como n independente. |
| Segurança/integridade/ACC/mutação | Matriz de estados, bordas e interleavings, não distribuição de latências. Cobrir cada combinação crítica definida e repetir cada agenda determinística nas duas versões. | Para corridas, começar com 3 agendas controladas por barreira e ampliar por estados não cobertos; stress aleatório com seed é complemento. Zero falhas não prova ausência; reportar agendas/variações e lacunas, não IC artificial de sucesso universal. |

**Percentis:** a cauda acima de P99 contém em expectativa apenas 0,01n observações: com 1.000 requests são cerca de 10, com 10.000 cerca de 100. Isso explica por que 1.000 não basta para garantir estabilidade. Planejar IC não paramétrico por estatísticas de ordem/binomial quando independente; em série temporal usar blocos/sessões. P99 só recebe interpretação comparativa se IC e suporte de cauda atendem precisão pré-declarada e se mantêm entre rodadas; senão marcar exploratório/não estimável e apresentar distribuição/P50/P95 com as mesmas ressalvas. Não extrapolar P99 de piloto IA ou de 30 cold starts.

**Intervalos:** registrar nível (proposta: 95%), método, n independente/efetivo e seed do reamostrador. Para proporções independentes usar Wilson ou exato quando apropriado; para F1/pares dependentes e diferenças A/B, bootstrap por identidade/sessão/bloco pareado. Para zero erros independentes em n ensaios, limite superior unilateral de 95% é 1−0,05^(1/n)≈3/n: zero em 300 não demonstra taxa <0,1%; seriam necessários aproximadamente 2.995 independentes para esse limite. Em gatos/pares correlacionados, não aplicar essa conta diretamente: informar IC por clusters e insuficiência do conjunto.

**Variabilidade e parada:** discriminar entre requests, rodadas, fotos, indivíduos, aparelhos e chamadas do provedor; mostrar amostras por estrato e agregados ponderados com pesos declarados. Caches e erros permanecem estratos próprios. Escolher orçamento fixo ou regra sequencial com correção estatística previamente definida; não parar ao primeiro p-valor favorável nem fazer bootstrap repetido até “passar”. Se vários cenários/limiares forem explorados, identificar exploração e validar hipótese selecionada em dados/rodada independentes. IC estreito não elimina viés de seleção do dataset ou hardware.

## 3. Cenários para revisão

Todos têm estado **PLANEJADO**. Cada linha herda protocolo/fixtures e define preparação, execução, métricas e oráculo. P0: crítico/integridade/segurança; P1: frequente; P2: exploração/capacidade.

### LAT — latência

| ID / prioridade / ambiente | Preparação e ação | Métricas e resultado esperado |
|---|---|---|
| LAT-01 P1 E2/E4 | F-AC/F-CAT; abrir 0/40/501 gatos, cache vazio/cheio, online/offline. | Tempo até coleção utilizável, sync concluído e frame estável separados; ordem/conteúdo/guest/estados iguais. |
| LAT-02 P1 E2/E3/E4 | Foto válida: confirmação até análise inicial e, separadamente, fim do reconhecimento; cache hit/miss. | Preflight, cópia/SQLite, metadados, upload, auth, IA, commit, parse/UI; mesmos estados e resultado com resposta controlada. |
| LAT-03 P0 E2 | Foto rejeitada, 503 controlado, 401 com refresh bem/malsucedido. | Tempo até erro e tentativas; mesmos códigos/mensagens/retryable, sem loop ou sucesso artificial. |
| LAT-04 P1 E2 | Sync com 0/1/40/501 dirty, pull vazio/multipágina e histórico crescente. | Percentis HTTP e ciclo, parse/aplicação, requests; cursor correto sem perda nem upload indevidamente omitido. |
| LAT-05 P1 E2/E4 | F-SOC; abrir lista/conversa, enviar texto/foto; atrasar detalhes e mensagens alternadamente. | Lista, primeiro conteúdo, envio→commit→eco; mesmo paralelismo, ordem, recibos e reconciliação. |
| LAT-06 P1 E2/E4 | F-YARD; abrir cache e executar build/plant/cook/collect. | Cache visível vs autoridade pronta, requests, efeito→frame; mesmo saldo/estoque/presente/calendário/rascunho. |
| LAT-07 P1 E2/E4 | Feed/inbox: paginação, filtros e timestamps iguais. | Tempo/payload/parse/merge; itens autorizados, sem repetição ou buracos. |
| LAT-08 P1 E2 | Análise lenta em paralelo com health, pull e conversa sob carga fixa. | Event-loop lag, P95 de rotas leves e espera do pool; distinguir CPU, I/O, DB e gerador. Health sozinho não prova fluidez. |

### LOAD — carga e concorrência

| ID / prioridade / ambiente | Preparação e ação | Métricas e resultado esperado |
|---|---|---|
| LOAD-01 P2 E2 | Leituras por contas distintas, concorrência 1/5/10/25/50; patamares de 3 min aquecidos. | RPS oferecido/concluído, percentis, erros/CPU/pool; encontrar saturação sem extrapolar localhost para produção. |
| LOAD-02 P1 E2 | Taxa aberta inicial 1/5/10/25 req/s, 3 min/patamar; mix 40% pull, 20% inbox, 20% feed, 20% Quintal com contas separadas. | Custo por rota, fila, reconciliação de GET e recuperação. Mix é hipótese a revisar, não telemetria real. |
| LOAD-03 P0 E2 | 2/10/25 requests com barreira: mesma operação/chave; depois comandos distintos disputando saldo de uma compra. | Um efeito por chave, ledger/saldo/estoque consistentes; recusas corretas por saldo insuficiente. |
| LOAD-04 P1 E2/E3 | 1/10/50 jobs de referência, 1/2 consumidores e inferência controlada; lote real menor separado. | Enqueue→claim→finish, idade do mais antigo, retries/falhas/superseded; ordem/prioridade preservadas. |
| LOAD-05 P1 E2 | 1/10/50 sockets, consumidores lentos, mensagens e reconexão parcial. | Latência WS, falhas por destinatário, recursos e cleanup; inbox persistida e acesso corretos. |
| LOAD-06 P2 E2 | Rajada de 30 s sobre carga sustentável medida; 5 min para drenar; soak de 60 min sustentável. | Recuperação, fila e recursos residuais, sem reinício manual. Sem IA paga no soak inicial. |

### DB — banco de dados

| ID / prioridade / ambiente | Preparação e ação | Métricas e resultado esperado |
|---|---|---|
| DB-01 P1 E1 | F-CAT nas bordas de 500, com G/B; snapshot real do coordinator. | Consultas atuais: 1+2×ceil(V/500) para V>0, 1 se vazio; V=IDs visíveis distintos. Mesmos mapas, ordem e referências kind ASC; medir tempo independentemente. |
| DB-02 P1 E2 | F-HIST, limit 1/200/500, página fria/repetida. | 4 SELECTs internos não vazios/1 vazio, auth separada; JSON/cursor vs legado e impacto de histórico no MAX. |
| DB-03 P1 E2 | 1/20/100 conversas e feed com autores repetidos/distintos. | SQL/linhas/tempo por página; confirmar estimativa 2+3C do inbox. Mesmas não lidas/última mensagem/autorização. |
| DB-04 P1 E2 | Consultas críticas em volumes crescentes, estatísticas atualizadas. | EXPLAIN (ANALYZE, BUFFERS) apenas de SELECT seguro na cópia: planos, estimativas, sort/buffers. Não aplicar a mutação real; GET de domínio pode escrever. |
| DB-05 P0 E2 | Análise principal/referência/reconhecimento pausados na inferência; requests leves paralelos. | Checkout/checkin/espera: nenhuma conexão do respectivo fluxo retida na IA; conexões de fixture/monitor separadas. |
| DB-06 P0 E2 | Upload/substituição/delete concorrem com finish de worker; compras/coleta simultâneas. | Locks/deadlocks/duração de transação; ordem conta→gato→foto→job e estado esperado, sem resultado antigo. |
| DB-07 P0 E1/E2 | Abertura SQLite concorrente, falha temporária ao abrir, falha entre aplicar página e salvar cursor. | Aberturas, rollback, reabertura/retry; nenhum cursor avançado omitindo dados nem descarte de dirty. |

### IMG — fotos, tráfego e inferência

| ID / prioridade / ambiente | Preparação e ação | Métricas e resultado esperado |
|---|---|---|
| IMG-01 P1 E2/E4 | Câmera/galeria, formatos/EXIF/resoluções do manifesto. | Bytes/hash por etapa, orientação/CPU/memória; preservar parâmetros por caminho. Captura usa até 1920/qualidade 85; compressão de post é outro caminho. |
| IMG-02 P1 E2 | Analisar, editar metadado que deixe dirty, sync e reiniciar. | Uploads/bytes/handshakes e hashes; identificar reenvio igual sem pressupor que pode ser removido. Manter metadados pré-análise. |
| IMG-03 P0 E2 | Referência igual, conteúdo novo no mesmo ângulo, só status alterado e tombstone; reabrir offline. | Downloads/hash/disco; mídia corresponde à versão autorizada. Verificar validador real; timestamp igual não prova conteúdo igual. |
| IMG-04 P1 E3 | Fotos fixas, processo frio/quente, caches semântico/segmentação vazios/cheios e caminho com/sem provedor. | Detecção/SAM/cores/Gemini/cache, RAM/VRAM quando disponível, chamadas/custo; não alterar modelo/limiar/máscara. |
| IMG-05 P0 E2/E3 | Sem gato/múltiplos/escura/borrada, bytes/pixels nas bordas, arquivo inválido. | Tempo/recurso até recusa; mesmos códigos/mensagens e ausência de efeito parcial. Aceitação segue regra atual, não rótulo inventado. |
| IMG-06 P1 E3 | Conjunto real rotulado antes/depois de mudança interna, modelo/prompt/seed registrados. | Detecção/IoU quando há ground truth, aceitação e atributos; revisar discrepâncias. Dataset Oxford existente não fornece rótulos de cor/padrão. |
| IMG-07 P0 E2 | Disco/permissão indisponível no storage próprio; arquivo muda entre claim e leitura. | Erro/órfãos/referência DB/hash/cleanup; não aceitar resultado para bytes diferentes ou sucesso sem persistência correspondente. |

### REID — reidentificação visual do mesmo indivíduo

Extensão de IMG, sem substituir IMG-01 a IMG-07. Separar quatro tarefas e seus rótulos: **detecção** localiza gato(s) na foto; **segmentação** delimita pixels do animal; **atributos** classificam cor/pelagem/características; **reidentificação** compara fotografias para recuperar candidatos ao mesmo indivíduo. Boa máscara ou atributos semelhantes não comprovam identidade. A qualidade de cada etapa deve ser avaliada isoladamente e no pipeline completo.

Alvos observados: `services/recognition_service.py:compare_embeddings`, `recognition_features.py`, `dense_features.py`, `config.py` e rotas de decisão de pistas em `routers/vision.py`. O mecanismo atual usa similaridade cosseno, versão/região/qualidade/ângulo, evidências locais/densas e independência de fotos; retorna `identity_confirmed=False`, score não calibrado e pode restringir sugestões ou operar em shadow mode. Registrar configuração efetiva, não só defaults. **Similaridade não é probabilidade e uma sugestão não é identificação confirmada.** Ground truth experimental também não confirma uma pista real nem modifica o estado do app.

#### Dataset F-REID a preparar, sem coleta ou execução nesta etapa

- Fotografias consentidas/licenciadas de indivíduos com identidade estabelecida independentemente do algoritmo: histórico do tutor com sessões distintas e revisão do vínculo entre fotos. Dois revisores para vínculos ambíguos; discordância vira rótulo incerto excluído da avaliação supervisionada principal e quantificado à parte. Aceite/clique do usuário ou sugestão do próprio modelo não basta como verdade de referência.
- Manifesto versionado e privado quando necessário: ID pseudônimo do gato, foto/hash/origem/permissão, sessão/data aproximada, câmera, luz, ângulo/lado (incluindo desconhecido), distância/escala, nitidez/oclusão, pelagem e evidência/confiança do rótulo. Não publicar localização/contato ou fotos sem autorização.
- Positivos: mesmo indivíduo em capturas independentes e sessões distintas. Negativos: indivíduos diferentes, incluindo **negativos difíceis** de pelagem/porte semelhantes, sólidos e pouco distintivos. Variar luz natural/artificial/baixa, frente/perfil/lados opostos/costas, perto/longe e resolução/desfoque/oclusão. Transformações sintéticas são stress separado e não novos indivíduos nem substitutos da diversidade natural.
- Separar desenvolvimento/calibração/teste final **por identidade**; indivíduos do teste não participam de tuning. Dentro de cada partição, galeria e consultas usam fotos/sessões diferentes do mesmo gato. Em open set, incluir gatos de consulta ausentes da galeria. Se houver treinamento, manter identidades de avaliação fora dele quando verificável; origem desconhecida do pré-treino é limitação declarada.
- Hash e similaridade perceptual auditam duplicatas/near-duplicates e frames da mesma rajada. Nada disso atravessa partições ou conta como evidência positiva independente. Duplicatas permanecem apenas no caso específico de robustez REID-06.
- Congelar manifesto, IDs dos pares e composição da galeria antes do teste final; balancear contribuição por gato/sessão, incluir estratos raros e declarar quantidade real de indivíduos/fotos/positivos/negativos por estrato. Dimensionamento conforme 2.2: conjunto pequeno é piloto, não benchmark definitivo. All-pairs não aumenta o n independente.

#### Métricas e política de limiares

- **Verificação de pares:** rótulo positivo = mesmo gato; decisão positiva = par ultrapassa o limiar de recuperação avaliado, ainda candidato. Publicar TP/FP/TN/FN; precision=TP/(TP+FP), recall=TP/(TP+FN), F1=2PR/(P+R), FPR=FP/(FP+TN), FNR=FN/(FN+TP), contagens absolutas e denominadores. Denominador zero é não estimável, não 0/100% por conveniência.
- **Pipeline de sugestões:** medir separadamente recall da recuperação visual, cobertura/rejeição por qualidade/ângulo/modelo, taxa de candidatos restritos e recall/FP das sugestões elegíveis após todas as regras. Um mesmo indivíduo bloqueado por preflight/segmentação ou filtro conta como falha/abstenção ponta a ponta, discriminada por etapa; não calcular “acerto” apenas nos pares fáceis que sobreviveram. Negação por política/privacidade não autoriza afrouxá-la para melhorar recall.
- **Busca em galeria:** Recall@1/@5 quando a galeria permite, posição do verdadeiro indivíduo e taxa de falsa sugestão por consulta sem correspondente (open set). Deduplicar por indivíduo ao ranquear, registrar tamanho da galeria; FP por par e falsa sugestão por consulta são denominadores diferentes.
- Reportar macro por indivíduo e por estrato, além de agregado com pesos declarados; precision depende da prevalência de positivos. Conjunto balanceado/hard negatives é diagnóstico, sem promessa de precision operacional. Se prevalência real desconhecida, mostrar sensibilidade a prevalências explicitadas como hipóteses.
- Limiar atual congelado é a comparação principal A/B. Em calibração, varrer scores observados/grade registrada cobrindo o domínio admitido por cada parâmetro, incluir fronteiras e gerar curvas precision-recall/ROC e tabela de falsos positivos/negativos. Avaliar score bruto e política completa separadamente; cosseno tem domínio matemático diferente da faixa aceita por determinado setting. Não desabilitar corroboradores nem usar o teste final para escolher limiar.
- Escolher ponto operacional **antes de abrir o teste final**, conforme custo/risco de falso positivo e recall mínimo a aprovar; F1 máximo isolado não define segurança. Congelar limiar/modelo/política e reavaliar holdout com IC por identidade/sessão. Qualquer sugestão de mudança fica como hipótese futura, sem alterar configuração nesta etapa.
- Comparar A/B nos mesmos indivíduos/pares/galerias, com IC da diferença por clusters e orçamento real. Se A não suportar uma etapa, marcar inaplicável e avaliar o efeito ponta a ponta comum; não importar algoritmo B para A. Não converter score em confiança percentual sem calibração independente demonstrada.

| ID / prioridade / ambiente | Preparação e ação | Métricas e resultado esperado |
|---|---|---|
| REID-01 P0 E3 | Auditar identidades/consentimento, splits por gato/sessão e duplicatas do F-REID antes de inferir. | Suporte por estrato, discordâncias e vazamento entre partições; avaliação principal só com rótulos independentes e rastreáveis. |
| REID-02 P1 E3 | Mesmo gato em luz, ângulo/lado, distância e sessões distintos, com galeria fixa. | Recall/FNR/Recall@k por variação e etapa; mostrar pares filtrados como abstenção/falha ponta a ponta, sem escondê-los. |
| REID-03 P0 E3 | Gatos diferentes com pelagem semelhante, sólidos e pouco distintivos; negativos fáceis separados. | FP/FPR/precision e IC; avaliar restrições/evidências sem confundir atributo ou score alto com identidade. |
| REID-04 P1 E3 | Baixa qualidade/oclusão e detecção/segmentação problemática; medir componente e pipeline. | Cobertura, abstenção, FN e custo por etapa; decompor erro sem remover os casos da métrica final. |
| REID-05 P0 E3 | Calibração de limiar e depois holdout bloqueado, threshold atual e candidato hipotético registrados. | Matrizes/curvas/IC e custo FP/FN; nenhuma escolha baseada no teste final ou ajuste no app nesta fase. |
| REID-06 P0 E2/E3 | Imagem repetida/recomprimida/crop, região/versão incompatível, ângulo oposto e apenas uma evidência independente. | Near-duplicates não viram múltipla confirmação; preservar restrições, `identity_confirmed=False` e ausência de probabilidade inventada. |
| REID-07 P0 E3 | Consultas com/sem indivíduo na galeria, tamanhos crescentes e múltiplas fotos por indivíduo. | Recall@k, falsa sugestão por consulta open set, latência/memória; registrar efeito de galeria maior e dependência dos pares. |
| REID-08 P0 E2/E3 | Candidato restrito/não restrito, shadow on/off no ambiente próprio e decisões humanas de confirmar/rejeitar. | Separar recuperação/sugestão/notificação/decisão explícita; score não confirma pista, não concede identidade/recompensa por si só e não publica candidato privado. |

### ACC — acessibilidade Flutter

Categoria própria, distinta de UX técnico. **Definição única de cenários, ferramentas, critérios e evidências no [roteiro Flutter — ACC](https://github.com/EveDoescher/gatalogo/blob/code-quality-cleanup/docs/test_plan_efficiency.md#acc--acessibilidade-flutter)**, para não duplicar a matriz. IDs globais reservados: ACC-01 contraste, ACC-02 fontes, ACC-03 toque, ACC-04 leitor de tela, ACC-05 semântica, ACC-06 movimento, ACC-07 resoluções, ACC-08 tarefas/erros. Ambientes E1/E4, P0/P1 definidos lá. Fonte de verdade dos demais cenários permanece neste plano; protocolo estatístico e modelo de resultados são compartilhados.

A/B deve registrar problemas de acessibilidade preexistentes e regressões separadamente. Uma não conformidade é achado para decisão posterior; a inclusão da categoria não autoriza mudar telas, rótulos ou animações nesta etapa. Os oito IDs herdam o ciclo de rastreabilidade da seção 8.

### DATA — mutação de dados e integridade

Comparar snapshots antes/depois de tabelas, arquivos, filas, cursor e eventos. Definir escritas esperadas por ação. Contar INSERT/UPDATE/DELETE e efeitos lógicos, sem exigir zero escrita de GETs que legitimamente reconciliam estado.

| ID / prioridade / ambiente | Preparação e ação | Métricas e resultado esperado |
|---|---|---|
| DATA-01 P0 E2 | Análise determinística; snapshots após metadados, persistência e job. | Diff RemoteCat/CatPhoto/SyncChange/progressão/jobs/storage; somente efeitos previstos e evento após commit. |
| DATA-02 P0 E2 | Falha após arquivo salvo, durante progressão e antes de commit; observar em outra sessão. | Sem meia análise/recompensa/evento de sucesso; detectar órfãos e política de cleanup. DB+filesystem não têm atomicidade conjunta presumida. |
| DATA-03 P0 E2 | Replay serial/paralelo da chave Quintal; resposta perdida após commit e reinício. | Um ledger/débito/recompensa por intenção, resultado original e fila termina usando a mesma chave. |
| DATA-04 P0 E2 | Mesma chave com action/target/data diferente; mesma chave em A e B. | Rejeitar fingerprint diferente na mesma conta, sem mudar saldo; contas não compartilham resultado. |
| DATA-05 P0 E1/E2 | Datas antiga/nova/igual, device IDs em ordens inversas, tombstone seguido de edição, análise já existente. | Desempate por device_id; não ressuscitar excluído; preservar identidade/revisão/análise autoritativas, função e HTTP. |
| DATA-06 P0 E2 | Finish após substituir/deletar foto/gato; lease expira e tentativa nova termina antes da antiga. | Fencing de tentativa/hash; resultado antigo não altera análise/status/recompensa/revisão nem publica evento indevido. |
| DATA-07 P0 E2 | Coleta dupla, saldo exato/insuficiente, estoque mínimo, prazo −1 ms/0/+1 ms, cliente adiantado. | Relógio servidor, saldo/estoque consistentes e uma recompensa/recibo; cliente não antecipa tarefa/jogo. |
| DATA-08 P0 E1/E2 | Leitura seguida de resposta atrasada; eco WS/HTTP nas duas ordens, evento duplicado e timestamps iguais. | Um item/mensagem por identidade confirmada, recibos sem regressão e rascunho correto; não presumir idempotência de reenvio não oferecida pelo contrato. |
| DATA-09 P0 E2 | Criar/alterar/resolver busca, repetir avistamento e confirmar/rejeitar pista. | Eventos/recompensas/notificações e visibilidade por transição; não duplicar observação pelo mesmo clientEventId nem expor busca resolvida. |

### RES — resiliência e offline

| ID / prioridade / ambiente | Preparação e ação | Métricas e resultado esperado |
|---|---|---|
| RES-01 P0 E1/E2/E4 | Capturar/editar offline, fechar/reabrir e recuperar rede. | Persistência de foto/fila/dirty, tentativas e tempo de convergência; sem perda ou requisição vazia apagando mídia. |
| RES-02 P0 E2 | Cortar upload no meio, resposta antes de commit e depois de commit em casos distintos com barreiras. | Estado servidor/cliente; replay quando suportado e erro atual quando não. Socket perdido não comprova rollback. |
| RES-03 P0 E1/E2 | Pausar resposta A de catálogo/análise/chat/inbox/Quintal; trocar para B/logout/dispose; liberar A. | Não aplicar resposta/evento A em B, cache/filas por owner e nenhum notify após dispose. |
| RES-04 P0 E2 | Encerrar worker próprio após claim, expirar lease e iniciar consumidor; resultado antigo tardio em caso separado. | Reclaim/tentativas/limite/status e fencing corretos, sem conexão retida na espera. |
| RES-05 P1 E2 | Queda/retorno de DB/storage próprios; Gemini lento/503/JSON inválido; SMTP/FCM indisponível separadamente. | Timeouts efetivos, retries/recibos/fila e recuperação; identificar operação pendurada. |
| RES-06 P1 E1/E2 | WS cai, fallback de 30 s, WS volta; eventos antes/depois. | Requests/recuperação/duplicatas; timers encerram com conversa, sem múltiplas assinaturas. |
| RES-07 P1 E1/E2 | Muitos gatilhos simultâneos sync/refresh; primeira operação falha, seguinte funciona. | Single-flight real, Future liberado após erro, sem multiplicação nem bloqueio permanente. |
| RES-08 P0 E1/E2 | SQLite/cache/storage próprios corrompidos/indisponíveis e interrupção durante página. | Erro/recuperação e preservação do que permanece íntegro; documentar lacuna, não apagar dados silenciosamente para passar. |

### SEC — segurança

Falta de proteção é achado para correção posterior; não aceitar exposição existente como oráculo. Relatórios não incluem tokens/OTP/coordenadas/fotos privadas; canários e IDs são sintéticos, URLs com token são redigidas.

| ID / prioridade / ambiente | Preparação e ação | Métricas e resultado esperado |
|---|---|---|
| SEC-01 P0 E2 | Sem token, assinatura/issuer/type inválidos, expirado/revogado, sessão de outro user, conta inativa; HTTP e abertura WS. | Negação sem dados/efeitos sensíveis; status/mensagem/fechamento apropriados. |
| SEC-02 P0 E2 | A tenta ler/alterar/excluir mídia, análise, pistas, conversa/anexo, favoritos e Quintal de B sem permissão. | Matriz recurso×ação×ator e diff; zero exposição/mutação indevida. Distinguir client_id e ID interno. |
| SEC-03 P0 E2 | C fora da conversa manda typing/read WS e acessa mídia; JSON/tipo/UUID inválidos. | Nenhum destinatário recebe evento indevido, recibos intactos e loop controlado. |
| SEC-04 P0 E2 | Público/amigo/estranho no feed/galeria/pistas, preferência muda durante paginação; busca pública ativa/resolvida. | Autorização consistente em JSON/mídia/link; sem EXIF/localização privada expostos. |
| SEC-05 P0 E1/E2 | `../`, caminho absoluto, barras Windows/POSIX, nomes/sufixos incomuns em storage/rotas de arquivo. | Nada fora da raiz é lido/escrito/apagado; sentinelas de teste externas intactas, erro controlado. |
| SEC-06 P0 E2 | Foto excedente/MIME falso/pixels excessivos/multipart inválido e campos/paginação fora do limite. | Rejeição e recurso gasto limitados. Se não há limite, registrar lacuna antes de ampliar carga; não enviar tamanho ilimitado no host. |
| SEC-07 P0 E2/E5 | OTP errado até limite, expiração exata/reuso/purpose/email trocados, reenvio <60 s; refresh/logout/replay de token antigo. | Tentativas persistidas apesar do erro, código consumido não reutilizado/cooldown 429; conferir política real de rotação/revogação. |
| SEC-08 P0 E2 | Erros de auth/upload/WS/IA, logs com canários, export A com B no banco. | Sem segredo em logs/traces/erros, export só de A. OTP dev atualmente é logado com SMTP desligado: registrar contexto/política, não confundir com SMTP real. |
| SEC-09 P1 E1/E2 | Inventário de dependências/lockfiles, configuração release/CORS/docs expostas. | Revisão local; advisories atualizados somente na futura execução. Release exige API_BASE_URL válido sem fallback LAN; achado estático não é exploração comprovada. |
| SEC-10 P0 E2 | Abrir WS válido, revogar sessão/expirar token e tentar ações/novas conexões. | Medir janela real em conexão aberta e registrar lacuna. Código autentica na abertura: não presumir revogação contínua instantânea. |

### UX — usabilidade técnica no celular

Referência é a experiência atual; sem redesign, textos novos ou redução de qualidade/animação para melhorar números.

| ID / prioridade / ambiente | Preparação e ação | Métricas e resultado esperado |
|---|---|---|
| UX-01 P1 E4 | Frio/quente, logado/deslogado, cache vazio/cheio, online/offline; iniciar e abrir coleção. | Startup, primeiro conteúdo útil e estabilidade; mesmos estados/navegação em vídeo/timeline. |
| UX-02 P1 E4 | 40/501 gatos, mídia carregada/não carregada, scroll/filtros/detalhes repetíveis. | Build/raster/frames lentos/memória/IO; conteúdo/hit targets iguais. Orçamento conforme display: ≈16,7 ms em 60 Hz, ≈8,3 ms em 120 Hz. |
| UX-03 P1 E4 | Quintal nas três densidades, pan/zoom/seleção/construção/efeitos. | CPU/UI/raster/GPU quando disponível, toque→feedback; mesma luz/oclusão/colisão/hit testing/animação. |
| UX-04 P1 E4 | Câmera/galeria, preflight, permissão negada e GPS lento/ausente. | Tempo/CPU/memória/bloqueio UI; mesma orientação/qualidade/feedback e localização assíncrona. |
| UX-05 P1 E4 | Chat com histórico/teclado/foto e rede ruim; inbox com rajada de eventos. | Toque→feedback, rebuilds/frames; mesmo foco/rascunho/scroll/recibos, sem atualização depois de sair. |
| UX-06 P0 E1/E4 | Capturas determinísticas e percursos foto→detalhes, offline→retomada, compra→coleta. | Comparação visual com tema/fonte/locale/dados/tempo fixos e revisão de diferenças; mesmas ações/textos/semântica. Screenshot não substitui testes de regras/acessibilidade. |
| UX-07 P1 E4 | Background/foreground/lock durante upload/produção; 20 ciclos de entrar/sair. | Estado/retomada/foco/listeners/timers; sem duplicação, travamento ou vazamento entre contas. |

### RSC — recursos e energia

| ID / prioridade / ambiente | Preparação e ação | Métricas e resultado esperado |
|---|---|---|
| RSC-01 P1 E2/E3 | Iniciar API/worker, lote fixo frio/quente e repouso. | CPU/processo, RSS/PSS se disponível, pico e estabilidade; separar cache esperado de vazamento. |
| RSC-02 P1 E2 | 100 ciclos de sync/foto/conversa/erro, 3 rodadas com repouso sem reiniciar. | Pool/HTTP/sockets/handles/FDs/threads baseline/pico/repouso; faixa estável sem esgotamento. |
| RSC-03 P1 E2/E4 | Substituir/excluir fotos, repetir downloads/status e reabrir offline. | Bytes/requests/handshakes/IO/cache/storage/órfãos por ciclo; cleanup não remove foto ainda referenciada. |
| RSC-04 P1 E4 | 30 ciclos câmera/detalhes/chat/Quintal; heap antes/depois de repouso/GC observado. | Retained objects/imagens/interpreters/subscriptions/timers; tendência, não um snapshot isolado. |
| RSC-05 P2 E4 | Sessões pareadas de 15 min coleção/chat/Quintal, brilho/rádio/condição térmica fixos, sem carregar quando viável. | Energia/temperatura/throttling/CPU/tráfego; bateria percentual isolada não comprova ganho pequeno. Usar ferramenta e declarar incerteza. |
| RSC-06 P1 E2 | LOAD-04/LAT-08 com limites CPU/RAM e concorrência constantes. | Tempo/erros/filas sob restrição; comprovar gargalo antes de sugerir workers/cache, sem reduzir qualidade. |

### DLV — SMTP, FCM e entrega percebida

| ID / prioridade / ambiente | Preparação e ação | Métricas e resultado esperado |
|---|---|---|
| DLV-01 P0 E2/E5 | Registrar/recuperar conta sintética, SMTP habilitado em Mailpit próprio; receber e usar OTP. | Request→aceite SMTP→mensagem, destinatário/purpose/expiração; sem código no relatório. Mailpit não prova chegada a Gmail/Outlook. |
| DLV-02 P1 E2/E5 | SMTP lento/recusa/queda, falha antes/depois de envio e repetição respeitando cooldown. | OTP/transação/resposta e duplicatas; SMTP+DB não têm commit atômico conjunto presumido. |
| DLV-03 P1 E5/E4 | App foreground/background/encerrado, permissão concedida/negada, token real de teste. | Commit→envio→aceite FCM→recepção→exibição/abertura→inbox, eventos separados. |
| DLV-04 P0 E2/E5 | Devices com preferências diferentes, UNREGISTERED, erro transitório e sucesso parcial/retry. | Destinatários/delivered/retry/remoção de inválido; não reenviar aos já concluídos nem tratar opt-out como falha. |
| DLV-05 P0 E1/E2/E4 | Mesmo notification_id via WS/push nas duas ordens; refresh/loadMore/markRead concorrentes e troca de conta. | Requests combinados, inbox sem duplicatas, leitura/contagem/páginas corretas; considerar coalescência já existente. |
| DLV-06 P1 E2/E5 | Reconectar push worker e repetir evento confirmado; ausência de credencial em caso separado. | Recibo/retry/tempo; falta de credencial não é entrega aprovada, aceite FCM não prova exibição. |

## 4. MUT — mutação do código

DATA mede efeitos sobre dados; MUT verifica se os testes detectam um defeito deliberado. Cobertura de linha não substitui isso.

Procedimento posterior à revisão:

1. Criar cópias isoladas dos SHAs sem dados/segredos reais, DB/storage próprios. Baseline dos testes selecionados precisa passar sem skips relevantes.
2. Um diff mínimo por mutante; confirmar linha exercitada. Começar com mutação manual dirigida Python/Dart, ferramenta automática ainda não selecionada. Não instalar pacote só para documentar.
3. Rodar teste comportamental e integração correspondente para conta/locks/commit. Falha pela causa prevista mata o mutante; syntax/import error ou infraestrutura indisponível não contam como morte útil.
4. Guardar diff/log sanitizado; descartar apenas a cópia própria. Nunca alterar fonte em uso/main ou contaminar o mutante seguinte.
5. Classificar: morto, sobrevivente coberto, não coberto, equivalente justificado, inválido, timeout/inconclusivo. Score=mortos/(mortos+sobreviventes+não cobertos), excluindo equivalentes/invalidade justificados. Mostrar inconclusivos à parte e não tratá-los como sucesso; publicar cobertura/duração/denominador.
6. Meta proposta: nenhum sobrevivente confirmado entre mutantes críticos de autorização/idempotência/rollback/fencing. Sem promessa de 100% global; equivalência requer revisão.

“Deve detectar” é expectativa a comprovar, não alegação de que a suíte atual já mata o mutante.

| ID / prioridade | Defeito a introduzir na cópia | Cenário e oráculo que deve detectar |
|---|---|---|
| MUT-01 P0 | `routers/sync.py:pull`: retirar filtro user_id. | SEC-02/DB-02: B não aparece no pull A. |
| MUT-02 P0 | `_incoming_wins`: permitir edição viva sobre tombstone. | DATA-05: excluído não ressuscita. |
| MUT-03 P0 | Mesmo alvo: inverter desempate device_id ou trocar > por >= no timestamp. | DATA-05: datas iguais e IDs nas duas ordens, vencedor e writes exatos. |
| MUT-04 P0 | `catalog_sync_queries.py`: incluir referência excluída ou kind primário. | DB-02/IMG-03: JSON integral exclui ambos da lista complementar. |
| MUT-05 P0 | Pull avança cursor pela maior revisão apresentada, não última consumida. | DB-02/F-HIST: próxima página não perde alteração intermediária. |
| MUT-06 P1 | `main.py:analyze_cat`: retirar rollback de leitura pré-IA. | DB-05: conexão do fluxo continua retida, ainda que JSON seja igual. |
| MUT-07 P0 | `reference_analysis._finish`: ignorar comparação de attempt. | DATA-06/RES-04: tentativa velha não sobrescreve nova. |
| MUT-08 P0 | `_current` ou `_read_image`: ignorar hash alterado. | IMG-07/DATA-06: bytes substituídos não validam resultado antigo. |
| MUT-09 P0 | Worker ignora deleted_at de cat/photo. | DATA-06: delete entre claim/finish impede escrita/recompensa. |
| MUT-10 P0 | `_finish`: retirar savepoint ou descarte de eventos na falha de progressão. | DATA-02: análise/recompensa revertidas, evento de sucesso ausente. |
| MUT-11 P0 | `quintal_production.operation`: ignorar recibo idempotente. | DATA-03/LOAD-03: um efeito após retry/perda de resposta. |
| MUT-12 P0 | Mesmo alvo aceita chave com fingerprint diferente. | DATA-04: conflito sem segundo efeito. |
| MUT-13 P0 | Mesmo alvo busca recibo sem user_id. | DATA-04/SEC-02: resultado A não é devolvido a B. |
| MUT-14 P0 | Handler de prazo em `quintal_commands/` aceita coleta antes da hora. | DATA-07: −1 ms/0/+1 ms e relógio cliente falso. |
| MUT-15 P0 | `auth_service.consume_otp`: retirar commit de tentativas erradas ou validação de expiração. | SEC-07: limite efetivo entre requests, expirado rejeitado. |
| MUT-16 P0 | `authenticated_user`: ignorar revogação/status/vínculo da sessão. | SEC-01: acesso negado sem efeito. |
| MUT-17 P0 | Conversa/evento WS ignora verificação de membro. | SEC-03: C não emite typing/read nem lê mídia. |
| MUT-18 P0 | `storage_service.absolute_path`: retirar validação de raiz. | SEC-05: sentinela externa não acessada/alterada. |
| MUT-19 P0 | Flutter coordinator retira checagem de owner após await. | RES-03: snapshot A não aplicado em B. |
| MUT-20 P1 | `sqliteIdBatches`: pular/sobrepor borda ou não deduplicar. | DB-01: 499/500/501/repetidos/ausentes, igualdade e contagem. |
| MUT-21 P0 | Flutter sync marca synced/cursor antes da aplicação concluir. | DB-07/RES-02: interrupção/reinício recupera tudo. |
| MUT-22 P0 | `NotificationStore`: retirar guarda generation/disposed ou readIds. | RES-03/DLV-05: resposta antiga não reaparece/desfaz leitura. |
| MUT-23 P1 | Retirar single-flight de refresh ou sync. | RES-07: N gatilhos compartilham operação, retry após erro funciona. |
| MUT-24 P0 | `ConversationController`: retirar reconciliação de eco otimista. | DATA-08/LAT-05: uma mensagem nas duas ordens HTTP/WS, rascunho em erro. |
| MUT-25 P0 | `HabitatService`: chave nova ao repetir intenção persistida após reinício. | DATA-03/cliente live: uma chave/intenção/débito com resposta perdida. |
| MUT-26 P0 | FCM ignora delivered anterior ou preferência. | DLV-04: só devices pendentes/habilitados recebem retry. |
| MUT-27 P0 | Publicar evento antes do commit. | DATA-02: observador externo não recebe sucesso de transação abortada. |
| MUT-28 P1 | `AuthenticatedApiClient`: retirar limite retried nos 401. | LAT-03/SEC-07: refresh/reenvio limitados, fluxo encerra. |

São 28 famílias dirigidas. Alternativas de uma linha viram sub-IDs separados, um defeito/diff por execução; não contar uma família como vários mutantes mortos.

## 5. Cobertura atual: ponto de partida, não execução nova

| Testes existentes | O que ajudam a verificar | O que falta nesta campanha |
|---|---|---|
| API `tests/test_sync_query_performance.py` | JSON vs legado, SELECTs, conexão pré-IA, SQLite/PG condicionais. | Latência amostrada, histórico longo, tráfego/carga HTTP. |
| Flutter `test/catalog_read_performance_test.dart`, `local_database_test.dart`, `auto_sync_test.dart` | Snapshot/SQLite real/bordas/sync. | Aparelho, falhas por fase, uploads e handshakes. |
| API `test_reference_analysis.py`, `test_recognition_database.py` em tests/ | Claims/fencing/cancelamento/substituição/rollback/pool e PG condicional. | Soak/idade de fila/interferência e IA real instrumentada. |
| API `test_quintal_postgres.py`, `test_quintal_live_api.py`; Flutter `test/quintal_live_client_test.dart` | Sessões/concorrência, socket perdido após commit, SQLite/reinício/conta quando habilitados. | Matriz ampliada de falhas, taxa sustentável, recursos/mutantes. |
| API testes de feed/social/chat/resgate/realtime/WS; Flutter `conversation_controller_test.dart`, `notification_store_test.dart` | Autorização/eventos/páginas/eco/leitura/ciclo de vida. | Matriz ator×recurso, revogação WS aberto, carga/entrega real. |
| API `test_account_settings.py`, `test_storage_service.py`; Flutter `authenticated_api_client_test.dart`, `session_store_test.dart` | Conta/export/arquivos/auth cliente. | OTP/refresh concorrentes, logs com canários e SMTP real com falha. |
| API `test_quintal_push.py`, `test_quintal_scheduler.py`, `test_realtime_events.py`; Flutter `push_notification_service_test.dart` | Preferências/recibos/mecanismos de entrega. | FCM até aparelho, aceite vs exibição, sucesso parcial real. |
| Flutter visuais/Quintal/câmera/capturas condicionais | Composição/regras/regressão controlada. | Profile físico, touch/frame time, retomada/memória/energia. |
| API benchmark/ e testes preprocessing/segmentation/color | Dataset e métricas locais específicas. | Não comprovam latência ponta a ponta, rótulos completos de atributos ou SMTP/FCM. |

Testes condicionais precisam de flags/pré-requisitos. Skip é não executado, nunca aprovado. Não somar suítes sobrepostas como testes únicos nem inferir mutation testing da existência dos testes.

## 6. Instrumentação posterior à revisão

- Cliente HTTP de carga a selecionar, amostras brutas/flow_id, tamanho/etapas e proxy determinístico. Multipart usa arquivo válido e oráculo de resultado, não só status 200.
- API: spans monotônicos, SQLAlchemy events para SQL/pool e PG locks/plans. pg_stat_statements só se configurado no container próprio; ausência não significa zero.
- Workers: enqueue/claim/finish, attempts/hash/status/motivo de superseded, sem conteúdo privado.
- Flutter: Stopwatch/Timeline e contadores em teste; profile/DevTools para frame/heap/CPU. Medir/igualar overhead; sem campos novos no contrato ou mudanças visíveis.
- SMTP/FCM: Mailpit próprio, recibos e observação em aparelho; fake apenas para falha determinística e identificado como fake.
- Mutantes manuais inicialmente em cópias limpas; automação posterior sem nova dependência obrigatória nesta etapa.

## 7. Ordem sugerida

0. Preparar BR-01 e desenho amostral 2.2; congelar quatro SHAs, fixtures, precisão/orçamento e harness. Aplicar etapas 1–6 a A/B quando pertinentes, concluir BR-05 e só então comparar futuras otimizações com a baseline refatorada aceita. REID exige manifesto rotulado e partições auditadas; ACC exige matriz Flutter e dispositivo para validação assistiva real.

1. Correção: SEC-01/02/03/05/07, DATA-02/03/04/05/06/07 e RES-03. Falha impede aprovar otimização dependente do mecanismo.
2. Baseline barato: DB-01/02/03/05, LAT-03/04/05/06, IMG-02/03, DLV-05, RES-07. Sem IA paga.
3. Mutação dirigida: começar MUT-01/05/07/10/11/12/15/19/21/25/27, revisar sobreviventes e completar demais famílias.
4. Carga/falhas/recursos e restante de segurança, sem estado contaminado entre rodadas.
5. IA real/SMTP/FCM com orçamento e destinatários/dispositivos próprios. Fake não substitui essa evidência.
6. Celular: UX, latência ponta a ponta, memória/energia, separados do servidor.
7. Escolher gargalo comprovado, registrar hipótese, refatorar pequeno e repetir invariantes/cenários afetados/regressão apropriada. Não prometer ganho antes de medir.

## 8. Resultados e revisão

Usar [test_results_template.md](test_results_template.md). Revisão pode escolher IDs, cargas, orçamento e disponibilidade de aparelho. Estado: PLANEJADO → PREPARADO → EXECUTADO com APROVADO/FALHOU/INCONCLUSIVO; BLOQUEADO quando faltar pré-requisito, com motivo. MUT usa classificações próprias.

Rastrear **hipótese → cenário/variante → código nos SHAs → teste/script versionado → execução → evidência → resultado → problema → correção → revalidação**. IDs estáveis de campanha/HIP/RUN/PROB/FIX/REV ligam tabelas do modelo; relações são muitos-para-muitos, sem apagar rodadas falhas. Registrar símbolo/caminho/linhas no SHA (linhas do workspace atual não são âncora suficiente), node ID do teste ou comando/script, commit/hash do executor e arquivos de evidência com hash. Executor ainda não implementado deve constar como planejado, não caminho fictício existente.

Problema identificado recebe descrição/impacto/reprodução/hipótese e evidências. Após autorização de implementação, ligar diff/commit da correção e comparação antes/depois a reexecuções com o mesmo cenário/dados, novos SHAs e testes de regressão. Resultado inconclusivo, correção rejeitada ou revalidação que falha ficam preservados. Nenhum problema/correção/resultado foi produzido nesta revisão documental.

Guardar campanha/caso/rodada, configuração sanitizada, fixtures/seeds/hashes, amostras, traces/SQL, logs e diffs de estado. Arquivos grandes/sensíveis fora do Git; referências e resumo sanitizado em docs. Campos vazios não significam zero ou sucesso.

Comparação final deve mostrar contrato, segurança/integridade, latência, recursos e limitações juntos. Menos SQL/bytes/CPU só será chamado de ganho de experiência quando medido no nível correspondente.

## 9. Referências metodológicas

- [Flutter — Accessibility testing](https://docs.flutter.dev/ui/accessibility/accessibility-testing): inspeção automatizada e validação assistiva no dispositivo, detalhadas no roteiro ACC.
- [W3C — WCAG 2.2](https://www.w3.org/TR/WCAG22/): critérios de acessibilidade de referência, sem alegação de certificação automática do app nativo.
- [NIST — intervalos para proporções](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm): fundamentos para incerteza binomial; pares correlacionados exigem tratamento por clusters descrito em 2.2.
- [scikit-learn — avaliação de modelos](https://scikit-learn.org/stable/modules/model_evaluation.html): definições de métricas classificatórias e curvas; não exige adicionar dependência ao projeto.

Os dimensionamentos/ICs devem ser justificados por experimento; as fontes não estabelecem os tamanhos iniciais deste plano como suficientes para este produto.


### Continuação da campanha crítica — 21/10/2026

[Resultados, limitações e próximos casos](campaigns/2026-10-21-critical/README.md),
[matriz cumulativa](campaigns/2026-10-21-critical/matrix.md) e
[rastreabilidade por variante](campaigns/2026-10-21-critical/traceability.json).
Preserva o piloto, corrige BR-04 para pendente e mantém todos os cenários parciais
até a execução de suas variantes integrais. As definições de cobertura deste plano
não foram reduzidas pelos subconjuntos executados.


### Continuacao de 22/10/2026

[Resultados e matriz cumulativa](campaigns/2026-10-22-continuation/README.md).
Preserva todos os cenarios anteriores; cobertura parcial nao significa conclusao.


### Continuação local de 23/10/2026

[Relatório incremental, matriz e problemas reproduzidos](campaigns/2026-10-23-local-campaign/README.md).
Preserva relatórios anteriores e os cenários integrais. Inclui contratos Apple e chat vermelhos, comparação cruzada de clientes/APIs, falhas de serviços, SQL, capturas e medições exploratórias. Nenhuma correção de produto ou conclusão de ganho foi aplicada.


### Recuperação, históricos e acessibilidade — 23/10/2026

[Resultados e matriz incremental](campaigns/2026-10-23-recovery/README.md). Inclui reinício em processos desktop, três janelas de interrupção de upload, históricos maiores, controles de identidade, revisão estática de dependências e auditoria de quatro páginas. Preserve os limites por variante; nenhum problema de produto foi corrigido nesta etapa.
