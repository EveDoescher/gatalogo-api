# Fechamento sequencial e correções

## Meta de 85 — continuação autorizada em 27/10/2026

**57 concluídos, 15 parciais, 13 pendentes e 10 bloqueados externos.**

[LOAD-01 concluído](LOAD01-20261027-results.json): cinco patamares de concorrência (1/5/10/25/50), com 30 segundos de aquecimento e três minutos medidos em cada um; respostas, consultas SQL, recursos e integridade registrados.

[LOAD-02 concluído](LOAD02-20261027-results.json): patamares abertos de 1/5/10/25 req/s. A 25 req/s ocorreram 45 timeouts e 59 descartes pelo teto de segurança do gerador; 1/5/10 req/s sem essas falhas. O teste de estabilidade LOAD-06 usa 8 req/s (80% do maior patamar sem falhas), ainda em execução e sem fechamento antecipado. O host é compartilhado: compilação Flutter e inferência local real começaram após LOAD-02; LOAD-06 deve ser interpretado como estabilidade sob essa atividade concorrente, sem comparação causal de latência ou certificação de capacidade.

O LOAD-01 registrou quatro falhas de transporte nos patamares 25/50 (ReadError, RemoteProtocolError e ReadTimeout). Não houve resposta HTTP de erro nem alteração de integridade. O servidor descartável força keepalive de um segundo; uma corrida de reutilização de conexão é hipótese a confirmar, sem atribuição automática ao produto. Concluir o experimento significa executar suas variantes e registrar também as falhas encontradas.

[LAT-03 concluído: erro, refresh, tentativas e persistência](LAT03-20261027-results.json). Sete variantes, 35 amostras medidas e sete aquecimentos; sem alegar percentis confiáveis. Corrigido no Flutter o 503 HTML interpretado como falha permanente.

Nesta continuação, o usuário autorizou cargas prolongadas descartáveis e IA paga. Nenhum aparelho físico será utilizado nesta etapa, por USB ou Wi-Fi; iOS permanece fora do escopo. A autorização não substitui a execução: cenários físicos conservam seus critérios e não serão concluídos com evidência desktop. Os lotes pagos terão orçamento e contagem limitados, sem credenciais nos relatórios.

As medições anteriores e seus limites ficam preservados abaixo.

<!-- no-usb-20261027:start -->
## Atualização sem USB — 27/10/2026

**52 concluídos, 18 parciais, 15 pendentes e 10 bloqueados externos; 95 cenários.**

Concluídos nesta bateria: DLV-02, RES-05, RSC-06, RSC-01, IMG-05. Nenhum aparelho, teste iOS ou chamada de IA paga. A main permanece preservada.

- [SMTP, resiliência, correções e regressões](DLV02-RES05-20261027-results.json): seis casos SMTP e 25 de resiliência, além de autenticação, acesso privado e rollback.
- [Dois mutantes detectados](MUT13-MUT14-20261027-results.json): ordem commit/envio e proteção de arquivos referenciados, com baselines verdes e cópias isoladas.
- [Limites de CPU/memória](RSC06-20261027-results.json): 244 jobs e 1.800 requisições medidas; controle de um núcleo/512 MiB verificado pelo Windows; sem alegação de ganho.
- [Modelos locais reais](RSC01-20261027-results.json): quatro processos, 16 execuções, detector/SAM2 e DINOv2, oito jobs persistidos e repouso; sem Gemini real ou identificação confirmada.
- [Rejeições reais e bordas](IMG05-20261027-results.json): cache de múltiplos gatos corrigido sem mudar coordenadas/limiares; repetição e efeitos de negócio preservados.

[Quatro problemas corrigidos e recomendações restantes](ISSUES-20261027-results.json). Os 55 testes locais também passaram. [Índice do arquivo bruto local](RAW-20261027-manifest.json): ZIP e manifesto em `artifacts/reports/quality-campaign/2026-10-27-no-usb/`, fora do Git; nenhum original foi removido.

As tentativas inválidas e os resultados anteriores foram preservados. Falhas de fixture não são resultados de desempenho. Fontes, SHAs, seletores, hashes das evidências, hipótese/correção e revalidação estão nos relatórios. SMTP não promete entrega exatamente uma vez; cliente com timeout não prova deadline SQL; amostras pequenas não sustentam P99.

Fora desta execução continuam os percursos que exigem aparelho, entrega real FCM/credenciais externas, conjuntos rotulados independentes e variantes pagas de IA. LOAD-01/02/06 exigem carga prolongada, ainda excluída nesta etapa. BR-05 depende da comparação A/B completa. Modelos locais disponíveis e uma foto CC0 não substituem um conjunto de reidentificação.
<!-- no-usb-20261027:end -->

## Escopo vigente

Por orientação do usuário em 23/10/2026, esta etapa passa a permitir correções de defeitos encontrados, seguidas de revalidação. Não autoriza remover funcionalidades, alterar a aparência do aplicativo nem modificar `main`. Os relatórios anteriores permanecem históricos: a regra antiga de somente investigar não descreve esta nova etapa.

Testes específicos de iOS, iPhone e VoiceOver ficam **FORA_DO_ESCOPO**, por decisão do usuário. Isso não equivale a resultado aprovado e não impede fechar o escopo Android/API. A indicação anterior de que não haveria testes físicos deixa de ser o limite desta etapa para Android; a execução depende de detectar e autorizar o aparelho conectado. TalkBack ainda exige avaliação assistiva com participação humana.

## Regra de fechamento

Fechar um cenário significa executar suas variantes aplicáveis, comparar com o contrato, corrigir os defeitos encontrados e revalidar. Dependências reais são registradas separadamente, sem transformar uma execução parcial em aprovação. Nenhum teste de iOS será usado como condição de fechamento de Android/API. Não há autorização nova para IA paga, destinatários externos, carga prolongada ou mudanças em produção.

A ordem operacional começa pela preparação BR-01 e segue a prioridade de correção definida no plano: SEC-01/02/03/05/07, DATA-02/03/04/05/06/07 e RES-03. A comparação estatística BR-02 não é condição para iniciar uma correção de segurança; cada correção terá um candidato identificado, preservando A e B históricos.

## Evidências

As novas execuções ficam no diretório privado `.tmp/quality-campaign/20261021T194312Z-953aa74e/sequential/`. O executor `scripts/run_security_regression.py` cria PostgreSQL próprio em porta aleatória, aplica migrações e executa testes sem iniciar lifespan de API, IA ou entregas. As fontes avaliadas são registradas por SHA-256; alterações durante uma execução invalidam a comparação. Credenciais, tokens, logs brutos e arquivos de banco não devem entrar no relatório público.

Autenticação Apple é um contrato de segurança da API, mesmo com iOS fora do escopo. A correção verifica assinatura e claims segundo a [documentação da Apple](https://developer.apple.com/documentation/signinwithapple/verifying-a-user). Para habilitar esse login, `APPLE_CLIENT_ID` deve identificar o destinatário cadastrado. Os testes usam JWKS/chaves RSA locais; não representam uma sessão real com o provedor.

Os resultados e a matriz desta etapa serão anexados após sua validação, sem editar os relatórios históricos.

## SEC-01 — concluído em 24/10/2026

64 testes passaram, sem falhas ou skips, em PostgreSQL descartável com migrações, HTTP e WebSocket ASGI. [Resultado, seletores, hashes, correções e revalidação](SEC01-20261024-results.json). Commit da correção: `d6573b0401491ad350460d1054bdba414e53f873`.

As rejeições cobrem token ausente/malformado, assinatura/issuer/type inválidos, expiração/revogação, campos obrigatórios, IDs inválidos, sessão inexistente/de outra conta e conta inativa. Controles positivos preservam login, acesso e pong. A revalidação também cobre sessões WS já abertas e ociosas e validação criptográfica Apple com RSA/JWKS locais. Não representa login real no provedor nem teste de latência. Para habilitar Apple no deployment, configure `APPLE_CLIENT_ID`; nunca derive esse destinatário do token recebido.

## SEC-02 — concluído em 24/10/2026

24 testes aprovados, sem falhas ou skips. [Matriz de cobertura, controles positivos, evidências, hashes e correções dos fixtures](SEC02-20261024-results.json). Fotos/ângulos, análise/sync, pistas, conversas/anexos, favoritos e Quintal foram avaliados em cópia congelada com banco descartável. Não houve alteração do produto. Rotas inexistentes e os mecanismos reais de remoção estão discriminados; não foram contados como testes omitidos.

## SEC-03 — concluído em 24/10/2026

6 testes aprovados. [Variantes, protocolo, recibos, seletores e hashes](SEC03-20261024-results.json). Doze variantes inválidas/não autorizadas mantiveram o loop vivo, sem eventos indevidos ou alteração dos recibos. Os membros receberam digitação e recibo de leitura, persistido em PostgreSQL; mídia privada teve controles positivos/negativos. Sem nova correção no produto.

## SEC-05 — concluído em 24/10/2026

72 testes de segurança e 2 de compatibilidade aprovados. [Antes/depois, correção, seletores e evidências](SEC05-20261024-results.json). Os 33 casos vermelhos passaram após normalizar extensões nos seis gravadores. Bytes, hashes, leitura do proprietário e chaves válidas/versionadas foram preservados. Não houve escape comprovado da raiz: o defeito confirmado era a aceitação de sufixos não portáveis, inclusive sintaxe de streams no Windows.

## SEC-07 — concluído em 24/10/2026

16 testes aprovados. [Antes/depois, limites, SMTP, concorrência e correção](SEC07-20261024-results.json). A corrida gerava dois e-mails e dois OTPs ativos; após o lock transacional, as respostas foram 202/429 com uma mensagem e um código ativo. Recuperação real nos dois endpoints, limites exatos, consumo único, refresh/logout e isolamento passaram. A entrega SMTP antes de um commit que falha continua explicitamente no cenário DLV-02.

## DATA-02 — concluído em 24/10/2026

8 testes aprovados, mais 72 de regressão de segurança do storage e 2 de compatibilidade. [Antes/depois, variantes, hashes e limites](DATA02-20261024-results.json). A escrita parcial danificava o destino; a substituição atômica preserva os bytes anteriores e remove o temporário após exceção. Rollback não expõe meia análise, saldo ou evento de sucesso. Um arquivo completo ainda pode sobreviver ao rollback: a [política de recuperação](../../quality/storage_recovery.md) exige inventário integral e escritores pausados; nenhum arquivo ou serviço do usuário foi removido/interrompido. Não foi implementada coleta automática concorrente.

## DATA-05 — concluído em 24/10/2026

7 testes aprovados, sem alteração do produto. [Variantes, fontes, executores e hashes](DATA05-20261024-results.json). Datas e desempates foram testados na função e por HTTP nas duas ordens dos dispositivos, incluindo replay idêntico, tombstone e isolamento entre contas. Análise, identidade e revisão de identidade do servidor permaneceram intactas.

## DATA-06 — concluído em 24/10/2026

9 testes aprovados, sem alteração do produto. [Interleavings, invariantes, fontes e hashes](DATA06-20261024-results.json). Resultados antigos não alteraram análise, status, hash, payload, saldo, revisões nem eventos após substituição/exclusão ou retomada por nova tentativa, inclusive já concluída. O resultado da tentativa atual foi persistido no controle positivo.

## DATA-07 — concluído em 24/10/2026

15 testes aprovados, sem alteração do produto. [Limites exatos, concorrência, HTTP, fontes e hashes](DATA07-20261024-results.json). Coleta em -1/0/+1 ms, replay e duas intenções concorrentes, saldos zero/insuficiente/exato/excedente e estoques abaixo/no/acima do mínimo passaram. Relógio forjado pelo cliente não antecipou coleta.

## RES-03 — concluído em 24/10/2026

48 testes aprovados, com hashes de fontes inalterados, e análise estática sem problemas. [Cobertura dos cinco fluxos, falhas, correções e evidências](RES03-20261024-results.json). Foram corrigidos carregamento do Quintal após descarte e gravação de rejeição de análise após troca/logout/descarte. Controles positivos preservam os estados normais. Correção Flutter: `af6e82b2bfad0c461261877d6f82a1cbea12a473`. São testes de componentes/SQLite, sem alegação de validação Android físico ou de contrato de eventos DATA-08.

## DATA-08 — concluído em 24/10/2026

47 testes Flutter e 1 PostgreSQL aprovados. [Antes/depois, fontes e evidências](DATA08-20261024-results.json). Corrigidos envelope real do chat, desempate por horário/ID e regressão de recibos. Ecos HTTP/WS nas duas ordens, duplicatas, histórico atrasado e restauração de rascunho passaram. API e Flutter publicados na branch de qualidade, sem alterar a main.

## DATA-03 — concluído em 24/10/2026

3 testes aprovados. [Replays, processo independente, fila, fontes e hashes](DATA03-20261024-results.json). Perda de resposta injetada após commit real e repetição em novo interpretador conservaram um débito, uma tarefa, um recibo e o mesmo resultado. Repetição de preparo reservou uma única vaga/consumo, inclusive com fila cheia. Sem alteração do produto. ACC-04 foi reclassificado para pendente: Android está disponível; iOS/VoiceOver permanece excluído.

## DATA-04 — concluído em 24/10/2026

4 testes aprovados, sem alteração do produto. [Fingerprint, escopo, fontes e hashes](DATA04-20261024-results.json). Mudanças de ação/alvo/conteúdo foram rejeitadas pelo motivo correto, sem efeitos extras; replay original e a mesma chave em outra conta permaneceram válidos.

## DATA-05 / MUT-03 — reforço do oráculo

O mutante `>=` no desempate sobreviveu aos 7 testes anteriores: o payload idêntico escondia uma revisão extra. [Ciclo completo e evidências](DATA05-MUT03-20261024-results.json). Foi acrescentado empate com o mesmo dispositivo e contagem exata de revisões por operação. O baseline passou em 8 testes e o mutante foi detectado por 3 asserções. Não houve correção no produto, que já tratava o empate corretamente. O sobrevivente histórico permanece preservado.

## Mutação crítica — candidato atual

[Resultados, receitas, seletores e hashes](mutations-critical-20261024-results.json). MUT-03 (empate), MUT-07 (attempt do worker) e MUT-12 (fingerprint) foram detectados, respectivamente por 3, 2 e 4 falhas esperadas; seus baselines passaram em 8, 9 e 4 testes. O sobrevivente inicial de MUT-03 foi preservado e levou ao reforço do teste, sem mudança no produto. Isso não substitui nem agrega indevidamente os resultados históricos das demais famílias.

## SEC-10 — concluído em 24/10/2026

4 testes aprovados com tempo real e intervalo padrão de 30 s. [Medições, variantes, fontes e hashes](SEC10-20261024-results.json). Revogação/expiração bloquearam o próximo frame em aproximadamente 6,03/1,49 ms nesta execução; conexões ociosas fecharam após 30,014/27,069 s desde a invalidação observada. Abertura posterior retornou 1008, HTTP 401 e outra conta permaneceu válida. Sem percentis ou inferência de ganho: são amostras pontuais ASGI, sem TCP/proxy/aparelho.

## DB-02 — concluído em 24/10/2026

4 testes aprovados e 1.448 execuções de página. [Datasets, SQL, variantes, planos e hashes](DB02-20261024-results.json). F-HIST 1/10/100 foi percorrido em limites 1/200/500, sempre primeira execução/repetição; extensão de 20.001 revisões percorreu 200/500 e amostrou bordas em limite 1. JSON/cursor igual ao legado e 4/1 SELECTs internos não vazio/vazio em todas as páginas. Conexão nova não significa buffer físico frio. Sem otimização nem alegação de ganho.

## SEC-08 — concluído em 24/10/2026

19 testes e 7 regressões de exceção aprovados. [Defeitos, correções, canários, fontes e hashes](SEC08-20261024-results.json). Exportação de relatos foi corrigida, inclusive com gato excluído logicamente. Canários do provedor não aparecem mais nos logs/traces testados; token de query não vaza nos logs reais do Uvicorn, incluindo nome percent-encoded. SMTP real isolado não loga OTP. O fluxo intencional de OTP de desenvolvimento com SMTP desligado permanece contextualizado, sem alegação de configuração de produção aprovada.

## ACC-05 — cabeçalhos corrigidos e revalidados

[Antes/depois, hashes e limites](ACC05-header-20261024-results.json). Perfil e notificações agora têm nomes e papel de botão. Os 10 testes passaram, análise estática sem problemas e 10 imagens permaneceram idênticas byte a byte. Após hot reload confirmado pelo usuário, a árvore nativa Android confirmou os dois rótulos e limites inalterados. ACC-05 permanece parcial: demais componentes e navegação TalkBack ainda precisam de avaliação.

## SEC-06 — concluído em 24/10/2026

29 testes aprovados, sem mudança no produto. [Variantes, limites e evidências](SEC06-20261024-results.json). Rejeições de uploads/campos/paginação não alteraram arquivos, revisões ou jobs; MIME falso/pixels excedentes não chegaram aos modelos. O leitor de análise parou no overflow limitado. Limites globais de ingresso/spool continuam uma verificação de implantação separada; não foram presumidos a partir destes testes ASGI.

## SEC-04 — concluído em 24/10/2026

21 testes aprovados. [Variantes, defeito, correção e hashes](SEC04-20261024-results.json). Feed/galeria/pistas respeitaram os atores, revogação entre páginas e remoção de EXIF; busca pública ativa, QR revogado e resolução preservaram privacidade. Corrigido cabeçalho da foto autenticada de busca para `private, no-store`; antes o teste reproduziu `public, max-age=300`. Sem mudança na imagem ou interface e sem alegação de exploração em proxy real.

## DLV-01 — concluído em 24/10/2026

3 testes de cadastro/recuperação passaram em SMTP real descartável, com destinatário, purpose, expiração e uso único verificados; tempos request→Mailpit observados sem inferência estatística. [Variantes, fontes e evidências](DLV01-20261024-results.json). Sem alteração no produto.

## LOAD-03 — concluído em 24/10/2026

6 variantes de barreira (2/10/25, mesma intenção e intenções distintas) aprovadas; um único débito/recibo/efeito, sem saldo negativo ou alteração de outra conta. [Variantes, fontes e evidências](LOAD03-20261024-results.json). Sem alteração no produto.

## RES-04 — concluído em 24/10/2026

6 testes PostgreSQL aprovados: término de processo próprio, reclaim em novo processo, fencing, cancelamento, limite de tentativas e conexão liberada durante inferência. [Variantes, fontes e evidências](RES04-20261024-results.json). Sem alteração no produto.

## DB-07 — concluído em 24/10/2026

3 testes aprovados em SQLite real: abertura concorrente/falha temporária, página parcialmente aplicada e cursor preservado com reprocessamento sem perda/duplicação. [Fontes, variantes e evidências](DB07-20261024-results.json). Sem mudança no produto.

## RES-07 — concluído em 24/10/2026

9 testes aprovados: 2/8/25 gatilhos de sync, inbox e renovação de sessão compartilham uma operação; falha inicial libera o bloqueio e a próxima tentativa funciona. [Fontes, variantes e evidências](RES07-20261024-results.json). Sem mudança no produto.

## RES-06 — concluído em 24/10/2026

2 percursos completos aprovados: eventos antes/depois da queda, fallback de 30 s, falha transitória, reconexão, deduplicação e cancelamento ao descartar. [Fontes, variantes e evidências](RES06-20261024-results.json). Sem mudança no produto.

## DLV-05 — concluído em 24/10/2026

6 testes aprovados: WS/push nas duas ordens, requisições combinadas, paginação/leitura concorrentes, isolamento após troca de conta e descarte. [Fontes, variantes e evidências](DLV05-20261024-results.json). Sem mudança no produto.

## DLV-04 — concluído em 25/10/2026

12 testes aprovados: preferências, oito tipos, sucesso parcial, UNREGISTERED, erro transitório/rede, retry só dos devices pendentes e recibo PostgreSQL persistido. [Variantes, fontes e evidências](DLV04-20261025-results.json). Sem alteração no produto.

## RES-08 — concluído em 25/10/2026

22 testes API e 2 SQLite Flutter aprovados. [Defeito, correção, antes/depois e hashes](RES08-20261025-results.json). Cache opcional indisponível não interrompe mais a análise; arquivo original é preservado e resultados determinísticos permanecem iguais. SQLite autoritativo rejeita corrupção/escrita protegida sem apagar dados; restauração explícita do backup da fixture e retry preservaram foto, registro dirty e cursor. Interrupção durante página reutiliza evidência DB-07 com hashes de lib iguais.

## IMG-07 — concluído em 25/10/2026

5 testes aprovados: falha de espaço/permissão, recuperação de arquivo ausente e fencing de hash antes da análise em SQLite/PostgreSQL. [Variantes, fontes e evidências](IMG07-20261025-results.json). Sem alteração no produto.

## DB-01 — concluído em 25/10/2026

20 testes aprovados e 42 snapshots reais, de 0 a 2.000 gatos, com G/B. Mapas/ordem/referências preservados e contagem SQL exata nas bordas de 500. [Fontes, variantes e evidências](DB01-20261025-results.json). Sem mudança no produto.

## DATA-01 — concluído em 25/10/2026

5 testes aprovados: snapshots completos de análise/metadados/job, rollout 0/100 e gato próprio/não próprio; revisões exatas, ledger/XP coerentes e eventos após commit. [Variantes, fontes e evidências](DATA01-20261025-results.json). Sem alteração no produto.

## DATA-09 — concluído em 25/10/2026

5 testes aprovados: ciclo completo de busca/pista, replay de avistamento e confirmação concorrente, amizade/rollout, recompensa única, notificação após commit e retirada da busca resolvida. [Variantes, fontes e evidências](DATA09-20261025-results.json). Sem alteração no produto.

## DB-06 — concluído em 25/10/2026

10 testes aprovados: seis interleavings com espera real por lock em PostgreSQL, três rajadas de compra e uma coleta concorrente; sem sobrescrita obsoleta, débito ou recompensa duplicados. [Variantes, fontes e evidências](DB06-20261025-results.json). Sem alteração no produto.

## DB-03 — concluído em 25/10/2026

12 testes de volume/isolamento/paginação aprovados em 1/20/100 itens, autores repetidos/distintos; consultas, bytes, tempo, CPU e RSS registrados. Crescimento SQL observado permanece oportunidade documentada, sem otimização aplicada. [Variantes, fontes e evidências](DB03-20261025-results.json). Sem alteração no produto.

## DB-05 — concluído em 25/10/2026

4 testes aprovados: análise principal, referência e reconhecimento liberam suas conexões durante a inferência controlada; health/pull/conversas respondem em paralelo, sem conexão retida. [Variantes, fontes e evidências](DB05-20261025-results.json). Sem alteração no produto.

## DB-03 — complemento de cobertura

Os 12 testes de volume passaram. A auditoria de escopo reabriu temporariamente DB-03 para revalidar também os históricos F-SOC de 0/100/1.000 mensagens e lista vazia no candidato atual. A classificação inicial foi corrigida sem apagar as evidências.

## DB-03 — complemento concluído em 25/10/2026

19 testes adicionais passaram; total de 31 datasets e 147 observações de requests. [Históricos, consultas, bytes, tempo e recursos](DB03-history-20261025-results.json). Cobertos 0/100/1.000 mensagens, autores repetidos/distintos e lista própria vazia com histórico estrangeiro. Preservados unread/última mensagem/ordem/isolamento. A rota de histórico mantém seu contrato sem paginação; N+1 e payload integral continuam oportunidades documentadas, sem otimização ou alegação de ganho.

## DB-04 — concluído em 25/10/2026

3 datasets aprovados e 9 avaliações de famílias de leitura em schema migrado; planos ANALYZE/BUFFERS, índices e versão registrados em transações READ ONLY. Sem otimização aplicada. [Variantes, fontes e evidências](DB04-20261025-results.json). Sem alteração no produto.

## RES-01 / IMG-02 — concluídos em 25/10/2026

[Seis processos, métricas e rastreabilidade](RES01-IMG02-20261025-results.json). Captura/edição offline, reabertura, análise real do protocolo com inferência controlada, sincronização, edição após análise, isolamento e reabertura final passaram. Fotos foram criadas pelo fluxo do cliente, sem PUT manual Python. A fase de retomada convergiu em 62,786 s com zero tentativa explícita, observando o timer normal de um minuto. Metadados preservaram a análise autoritativa. Tentativas de infraestrutura/oráculo anteriores permanecem preservadas; sem alegação de validação Android físico ou IA real.

## IMG-03 concluído; LAT-04 ampliado — 25/10/2026

[Correções, mutantes, medições e evidências](SYNC-IMG03-LAT04-20261025-results.json). 54 regressões passaram, incluindo sete variantes de mídia e dez de lotes. Quatro mutantes isolados foram detectados após baseline verde. A integração atual executou 17 casos Flutter com API/PostgreSQL próprios: lotes 500+1, histórico, aplicação multipágina e recuperação após 503 sem perda/duplicação. Corrigidos envio acima do limite e consumo de revisão após falha de mídia. LAT-04 permanece parcial para perfil separado de parse e confirmação estatística; não há alegação de ganho ou P99. Tentativas com falha de instrumentação/tempo permanecem preservadas e classificadas.

## RES-02 — concluído em 25/10/2026

[Três janelas TCP e duas recuperações do cliente](RES02-20261025-results.json). Cinco casos passaram. Corpo parcial preservou estado anterior; barreira PostgreSQL comprovou desconexão antes do commit; proxy descartou resposta após commit real. Replay manteve hash/uma foto primária e isolamento 200/404. O cliente preservou fila/foto/cursor e retomou após reabrir SQLite. Desconexão antes do commit resultou em commit após liberar a barreira: não implica rollback nem exatamente uma revisão. A primeira tentativa com pool entre event loops foi preservada como falha do executor.

## LOAD-05 — concluído em 25/10/2026

[Fanout, recursos, inbox e rastreabilidade](LOAD05-20261025-results.json). Seis testes passaram: componentes e TCP em 1/10/50 dispositivos, reconexão parcial e leitor pausado. Foram 762 leituras WS observadas; histórico/inbox mantiveram 13 mensagens por caso e nenhuma notificação cruzada no remetente. Destinatário quebrado não bloqueou os demais no teste do componente. Presence e pool ficaram livres após fechar sockets. Medições descritivas incluem filas/leitura deliberadamente atrasada; sem P99, ganho, saturação ou capacidade de produção.

## Android físico — fonte, teclado e TalkBack, 25/10/2026

A correção de `IllustratedProgress` está no Flutter `cf6cc8b`: a altura mínima permanece, enquanto o texto ampliado passa a determinar a altura necessária. Seis variantes de XP (duas larguras × fontes 1,0/1,3/2,0), cinco de Home e dez de cabeçalho passaram; as duas imagens com fonte normal são byte a byte iguais às anteriores. A fonte a 200% foi revalidada no celular após hot reload. Os 172 scripts da aplicação carregados no processo corresponderam às fontes atuais. O APK instalado não foi reconstruído nesta operação.

Foram registradas capturas físicas de Home/Coleção/Social/Quintal com fontes 1,0/1,3/2,0, além de Home/Coleção em quatro classes de viewport com fonte 1,0/2,0. São configurações de um único Android, não quatro aparelhos ou um tablet físico. A busca com teclado exibiu o estado vazio para `qqq` e voltou aos 19 gatos após limpar. A classificação considera a aba selecionada efetiva: `collection-scale100` e os primeiros arquivos `network-*social` permaneceram em Home/Coleção, respectivamente, e não comprovam os percursos sugeridos por seus nomes. Capturas posteriores confirmam a Coleção padrão.

O usuário confirmou sem problemas o roteiro TalkBack de cabeçalho, coleção/análise existente, galeria com cancelamento, notificação e ação do Quintal. É evidência humana, sem gravação de áudio. Na etapa final, o usuário também confirmou saída/entrada na conta, seleção efetiva de foto com cancelamento antes de Analisar/Salvar e envio de mensagem ao segundo usuário de desenvolvimento. ACC-04 está concluído no Android por avaliação humana explícita; iOS permanece excluído. Sobreposição de arte/texto de missões em fonte ampliada e alvos nativos pequenos do cabeçalho continuam pontos de avaliação; a correção do XP não certifica todas as telas de acessibilidade.

## RSC-02 — execução parcial preservada

[Rodadas, diagnóstico e hashes](RSC02-20261025-results.json). Uma rodada de 100 ciclos HTTP/foto/WS terminou com zero conexão PostgreSQL emprestada, zero socket próprio, duas threads, três tasks e 222 handles após repouso. A segunda rodada encontrou timeout no upload multipart negado no ciclo 28; o teste completo de três rodadas não passou. Dois diagnósticos adicionais executaram 200 uploads negados com conexão persistente e 200 com fechamento, sem reproduzir a falha. Isso não inocenta o percurso misto. As cinco tentativas anteriores, ajustes de observador/oráculo e evidências brutas continuam preservados. Não houve correção no produto nem alegação de ausência de vazamento da API.


## RSC-04 / ACC-04 — concluídos no Android, 25/10/2026

[Relatório, variantes, hashes e rastreabilidade](ANDROID-USB-20261025-results.json). Três ciclos de aquecimento e 30 ciclos medidos de câmera/cancelamento, detalhes, chat e Quintal concluídos no mesmo processo Android. Houve pausa após 17 ciclos durante reinício da API; a retomada preservou os ciclos anteriores, com espera limitada ajustada e referências do observador isoladas. Não é uma comparação estatística entre versões de executor. Após repouso/GC e inspeção independente, zero telas de detalhes, zero telas de conversa e zero controladores de conversa; um interpretador local e os estados persistentes esperados. O heap foi de 214,67 MiB aquecido para 215,44 MiB após repouso.

A memória gráfica observada aumentou de 875.411 para 945.615 KiB; PSS total, de 1.379.442 para 1.448.404 KiB. O cache Flutter final tinha 41 entradas, 40 imagens vivas e zero pendentes, usando 94.922.664 bytes sob limite de 104.857.600. A atribuição do crescimento nativo permanece um achado aberto: requer perfil equivalente de texturas/renderização em profile/release antes de otimizar; o resultado não comprova vazamento nem ausência dele no subsistema gráfico.

Social foi confirmado pela aba/estado efetivo, exibiu erro offline e recuperou sem erro após restaurar Wi-Fi/dados. Na redução de movimento, as flags efetivas passaram de 128 para 132 e `HabitatGame.reducedMotion` de false para true; vídeos pareados de seis segundos foram preservados. Celebrações e todos os efeitos ainda não foram certificados por esse complemento de ACC-06.

O roteiro TalkBack original mais os três passos finais foram confirmados pelo usuário sem problemas. Nenhum áudio foi capturado remotamente. Fontes, resolução, densidade, animações, acessibilidade, Wi-Fi e dados móveis foram restaurados e comparados com os valores originais. Energia de 15 minutos e medições confirmatórias profile/release continuam pendentes, sem manter o USB ocupado nesta etapa.


## SEC-09 — revisão local concluída em 27/10/2026

[Variantes, inventário, configurações e hashes](SEC09-20261027-results.json). Superfície HTTP (um caso) e quatro contratos Flutter desktop aprovados. OSV consultado somente com o payload de 254 pacotes previamente autorizado; zero divergência com as versões atuais. Oito pacotes tiveram correspondências, agrupadas em 68 registros distintos. A revisão registra caminhos de dependência e fontes atuais, incluindo a correção anterior da validação Apple. Correspondência não comprova exploração.

Docs/OpenAPI respondem 200 localmente; preflight não confiável respondeu 405 sem autorização CORS. Release exige URL explícita válida. Defaults de desenvolvimento JWT/OTP e ausência de lock de produção continuam achados de configuração/reprodutibilidade a revisar na implantação; valores reais não foram inspecionados. Conclusão da auditoria local não certifica implantação segura nem significa que avisos de dependência foram corrigidos.


## RSC-02 — concluído em 27/10/2026

[Três rodadas, diagnósticos, ambiente e hashes](RSC02-20261027-results.json). Três testes passaram: dez ciclos de aquecimento mais três rodadas de 100 ciclos mistos no mesmo processo e 400 uploads negados nos dois diagnósticos de transporte. Mantido o timeout HTTP de 10 s. Os 310 envios foram persistidos uma vez cada; não houve desvio do envelope de handles/threads/tasks/HTTP, e pool emprestado/sockets próprios voltaram a zero.

Após repouso, as rodadas tiveram respectivamente 232/229/221 handles, duas threads, três tasks, duas conexões HTTP ociosas e RSS de 171.515.904/168.718.336/171.229.184 bytes. A repetição mista durou 137,3 s; a suíte inteira durou 168,07 s. São medidas do processo API/teste, sem alegação de ganho ou ausência universal de vazamento. O timeout histórico de 25/10 não reapareceu, mas sua causa continua indeterminada; evidências anteriores permanecem intactas.


## LAT-04 — baseline E2 concluída em 27/10/2026

[21 casos, decomposição e amostragem](LAT04-20261027-results.json). Cobertos 0/1/40/501 dirty, pull vazio/multipágina, histórico crescente e recuperação após 503 no segundo lote. Cinco bancos SQLite novos para o lote normal de 501 tiveram ciclos de 82,426–98,545 s, mediana 93,701 s; a mediana da aplicação em `replaceCatFromSync` foi 68,422 s. IDs, nomes, foto original, fila e cursor foram conferidos com o servidor em todos os casos. JSON/UTF8 foi perfilado fora do cronômetro do sync; a aplicação executou o método real de produção.

A amostra grande foi limitada pelo custo do piloto, com cinco repetições e um caso adicional de recuperação. Limites de confiança da mediana são apresentados apenas sob hipóteses explícitas de IID/estacionariedade, não comprovadas por repetições no mesmo processo/servidor; P95 empírico é descritivo e P99 não foi apresentado. Nenhum ganho ou desempenho Android é inferido desses tempos Windows/SQLite FFI. Os dados indicam onde investigar transações/aplicação antes de qualquer otimização.


## LAT-08 — concluído em 27/10/2026

[Gerador isolado, percentis, loop lag, pool e recursos](LAT08-20261027-results.json). Passaram 9.000 requisições medidas mais 270 de aquecimento, em três fases com 0/1/5 análises controladas aguardando. Cada rota — health, pull de cinco registros e histórico de vinte mensagens — recebeu 1.000 amostras por fase, com concorrência fixa cinco. O gerador rodou em PIDs distintos do servidor. Todas as leituras terminaram antes de liberar a inferência; as seis análises concluíram 200 após a liberação e o pool voltou a zero. Nenhuma chamada paga.

P95 observado: health 24,45–29,59 ms, pull 441,62–633,32 ms e histórico 492,13–727,04 ms. Também foram coletados atraso do event loop, aquisição do pool e CPU/RSS de servidor/gerador. Os intervalos reutilizam o cálculo binomial por estatísticas de ordem, condicionado a IID/estacionariedade não certificados. Fases não são blocos aleatórios pareados e houve geração de relatórios no host; diferenças não são atribuídas causalmente à quantidade de análises. Não há afirmação de P99, ganho, saturação ou capacidade de produção. A primeira tentativa, interrompida por ausência de amizade nas fixtures, permanece preservada.
