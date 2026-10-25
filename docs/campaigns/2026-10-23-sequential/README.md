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
