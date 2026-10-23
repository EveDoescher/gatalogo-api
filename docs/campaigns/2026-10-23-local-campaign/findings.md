# Problemas reproduzidos e recomendações

Nenhuma correção abaixo foi aplicada. As prioridades são de revisão técnica nesta campanha, sem classificação CVSS ou conclusão sobre um deployment não inspecionado. Os problemas anteriores continuam no [registro anterior](../2026-10-22-continuation/issues.json).

## SEC-F-APPLE — autenticação externa aceita sem validação criptográfica

Prioridade máxima de revisão. `app/routers/auth.py:apple_login` usa `jwt.decode(..., verify_signature=False)`, compara o emissor por substring, aceita identificador/e-mail externos e associa conta por e-mail. `identity-01` executou quatro entradas sintéticas em cada API A/B: token sem assinatura, assinatura com chave não confiável, assinatura não confiável combinada com expiração e emissor contendo a substring esperada em outro domínio.

As oito receberam 200, emitiram sessão da conta B sintética preexistente e permitiram `/sync/pull` autenticado com a sessão emitida. Os contratos exigindo 401 ficaram vermelhos. Não houve uso de identidade, chave Apple ou conta real. As variantes de expiração/audiência não isolam essas verificações de uma assinatura Apple válida: o achado comprovado é a aceitação de identidade sem confiança criptográfica e seu vínculo por e-mail.

Recomendação: validar criptograficamente a identidade externa, emissor exato, audiência e claims temporais/contextuais exigidos; derivar sujeito e identidade dos claims verificados; revisar associação a conta existente e campos fornecidos pelo cliente. Planejar migração/revalidação de sessões e fluxo Apple legítimo antes de qualquer alteração. Não basta corrigir a comparação textual do emissor. Evidência: `identity-01/critical_runs/identity-A` e `identity-B`.

## DATA-F-CHAT-WIRE — mensagem real decodificada no nível incorreto

`app/services/chat_commands.py` envia `data = {conversation_id, message}`; `lib/services/websocket_service.dart` encaminha esse mapa; `lib/controllers/conversation_controller.dart` chama `ChatMessage.fromMap(data)` diretamente. Em `wire-contract-03`, evento TCP real foi capturado após mensagem HTTP persistida e injetado sem remodelagem no transporte do controlador real B. O ID e o texto consumidos ficaram vazios, embora o ID aninhado coincidisse com a resposta HTTP.

Recomendação: alinhar o contrato numa fronteira única de parsing, preservando IDs, conteúdo e reconciliação nas duas ordens; manter oráculos com envelope real além de fixtures planas. O handler equivalente existe por inspeção em A, mas o consumidor A não foi executado neste experimento. O teste Dart interno vermelho fica preservado; a caracterização Python verde não aprova o produto.

## DB-F-SOCIAL — crescimento linear de consultas

`app/services/chat_queries.py`, `chat_presenter.py`, `social_feed_queries.py` e `social_presenter.py` compõem linhas relacionadas. `social-volume-02` observou 7/64/304 SELECTs para 1/20/100 conversas e 5/24/104 no feed com autores distintos. Com autor repetido o feed ficou em 5, coerente com reutilização dentro da sessão nessa fixture. Os dados e respostas foram verificados para evitar comparar resultados diferentes.

Recomendação: avaliar leituras agrupadas/projeções explícitas para relações repetidas, preservando audiência, unread, empates, ordenação e paginação. Medir primeiro o SQL de cada composição; contagem reduzida não garante, sozinha, menor latência. Não houve alteração de consulta nesta etapa.

## SEC-F-PROVIDER-LOG — erro saneado na resposta e bruto no log

Em `app/services/analysis_service.py`, `_safe_error_detail` removeu a chave sintética da exceção pública, mas `logger.exception` conservou a mensagem bruta da exceção de provedor. `image-02/SEC08-provider-log-canary.json` confirmou ambos. Nenhuma credencial real foi carregada ou exposta pelo teste.

Recomendação: definir um único mecanismo de saneamento também para logs e cadeias de exceção; manter canários na resposta e no logger, incluindo códigos de erro e falhas de retry. Avaliar diagnóstico necessário sem armazenar conteúdo sensível do provedor.

## RES-F-CACHE — cache semântico corrompido impede construção

`app/services/analysis_cache.py` levantou `DatabaseError` ao construir o cache SQLite privado corrompido. Não foi observada recuperação automática. A corrupção foi criada somente no arquivo temporário do caso `image-02/RES08-corrupt-semantic-cache.json`.

Recomendação: avaliar isolamento/quarentena e reconstrução de cache derivado, mantendo dados autoritativos separados e sem ocultar falhas de disco. Testar permissões, disco cheio e observabilidade antes de definir fallback; cache corrompido não autoriza apagar bancos do usuário.

## RSC-F-CLEANUP — arquivo antigo permanece após falha pós-commit

`app/routers/sync.py:upload_photo` confirmou a nova foto no banco antes de a remoção do arquivo antigo falhar com erro de permissão injetado. A repetição recebeu 204, mas não revisitou a chave antiga. `storage-resilience-01/RSC03-postcommit-cleanup.json` registra o órfão pós-commit. É distinto do órfão anterior produzido por rollback após escrever o arquivo.

Recomendação: avaliar uma intenção durável de limpeza/reconciliação, garantindo que nenhuma chave referenciada seja removida e que retries preservem idempotência. Revalidar as duas janelas — antes e depois do commit — com as mesmas fixtures.

## DLV-F-AMBIGUOUS — aceitação SMTP seguida de erro permite duplicação

Mailpit próprio aceitou uma mensagem; uma falha de transporte injetada logo depois levou a rollback do OTP. O retry imediato enviou outra mensagem, e só então o cooldown persistido bloqueou repetição. A observação amplia a falha anterior de aceitação antes de commit.

Recomendação: avaliar intenção persistida/outbox e política para aceitação ambígua, respeitando confidencialidade e validade de códigos. SMTP não fornece garantia geral de entrega exatamente uma vez; não prometer essa propriedade por acrescentar retry.

## VIS-F-GOLDEN — referências visuais atuais não aprovam renderização

Os dois testes opcionais de golden falharam em A e B: login 76,57% de pixels diferentes, home 100%, my_cat 93,72%. As três imagens efetivamente geradas são byte a byte iguais entre A e B; os arquivos de referência e os diffs foram preservados. Na inspeção de login, diferenças incluíam fundo, bordas e elementos decorativos; não eram apenas um número de tolerância.

Isso não demonstra regressão causada pela refatoração nessas três capturas. Recomenda-se revisar a origem/atualidade dos goldens e as condições de renderização; qualquer nova referência requer revisão visual consciente. A falha precoce impede dizer que todas as telas do teste de correções foram comparadas. A captura ampla separada passou, mas captura não equivale a aprovação visual ou de acessibilidade. O log também registrou indisponibilidade da DLL TFLite no runner Windows; as capturas não validam inferência local.
