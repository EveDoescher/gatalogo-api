# Fechamento sequencial e correções

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
