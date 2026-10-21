# Refatoração arquitetural — 21/10/2026

Branch: `code-quality-cleanup`, derivada da main sincronizada na etapa anterior.
Este documento registra a execução do mapa de qualidade, do menor para o maior risco.

## Fronteiras extraídas

| Etapa | Resultado |
|---|---|
| Apresentação do habitat | `habitat_presenter` monta item/snapshot com a sessão recebida; Quintal não chama mais uma rota de habitat. Os aliases antigos continuam disponíveis. |
| Fotos de alertas e pistas | `report_media` resolve a foto e gera JPEG sem EXIF/GPS, limitado a 1200 px. Autorização, exposição do cartaz e cabeçalhos de cache continuam nas respectivas rotas. |
| Produção do Quintal | `quintal_clock`, `quintal_inventory`, `quintal_care`, `quintal_simulation` e `quintal_projection` separam calendário, recursos, cuidados, avanço temporal e resposta. `quintal_production.operation` permanece o coordenador da mutação idempotente. |
| Domínios sociais | `social_profiles`, `social_friendships`, `social_feed`, `social_devices` e `social_chat` têm roteadores próprios. `social.py` registra esses roteadores e mantém exports de compatibilidade. Consultas de grafo e apresentadores ficam em serviços compartilhados. |
| HTTP e WebSocket | `conversations` centraliza autorização, destinatários e recibos de leitura. Digitação e leitura no WebSocket agora exigem participação e a mesma regra de amizade do HTTP. JSON inválido não derruba a conexão. Limpeza e atualização de presença usam um único `finally`. |
| Análise de referência | Upload grava um job `analysis:<ângulo>` na transação do catálogo. `ReferenceAnalysisWorker` consome esses jobs durante a vida da API; o worker GPU continua responsável apenas pelo reconhecimento. |

Não há nova migração nem alteração de URL, assinatura HTTP ou schema de resposta.
A comparação integral do OpenAPI antes/depois permaneceu idêntica, com 122 endpoints HTTP.

## Quem controla a transação

| Operação | Responsável por commit/rollback | Publicação |
|---|---|---|
| Comando HTTP social, catálogo ou Quintal | Rota/coordenador do comando | Após commit, por `publish_committed_events` |
| Consultas e apresentadores compartilhados | Recebem a sessão; não fazem commit | Não publicam |
| Regras de estoque, cuidado, simulação e projeção | Sessão do chamador; preservam flush e ordem dos locks | Não publicam |
| Tentativa de OTP | Serviço de autenticação mantém commits intencionais de tentativas, inclusive rejeitadas | Não deslocar esses commits para uma resposta de sucesso |
| Worker GPU | Uma transação de resultado por job, com verificação do lease/hash | Após commit |
| Worker de análise de referência | Claim e resultado na mesma transação, com savepoint para falha de análise/progressão | Após commit; hints de savepoint revertido são descartados |

Não foi feita remoção indiscriminada de commits: isso perderia tentativas de OTP,
quebraria locks de carteira ou enviaria eventos sobre alterações revertidas.

## Protocolo da análise persistente

1. O upload grava foto, revisão e jobs de reconhecimento/análise antes de confirmar o commit; mantém a resposta 202 `analyzing`.
2. O consumidor seleciona um job pendente com `FOR UPDATE SKIP LOCKED`. Outros processos da API ignoram o job ocupado.
3. Durante inferência, somente a linha do job fica travada. A conexão/transação fica ocupada até o resultado; a inferência tem o limite configurado em `vision_job_lease_seconds`.
4. Após inferência, adquire conta e catálogo na ordem conta → catálogo e relê o hash e os tombstones. Foto excluída ou substituída invalida o resultado antigo.
5. Sucesso confirma foto, progressão e revisão uma única vez. Falha reverte o savepoint e volta a pendente até `vision_job_max_attempts`; a última falha marca a foto atual como `failed`.
6. Cancelamento ou encerramento do processo reverte o claim para pendente. O próximo consumidor retoma o trabalho. Não depende de memória local nem do processo GPU estar ativo.

Os jobs internos de análise não entram na listagem de jobs de reconhecimento.
Os filtros do worker GPU, inclusive recuperação de leases, excluem `analysis:`.
O uso da tabela existente não exige migração nem ativa o patch de rotação de sessões.

## Validação

Rodada completa: 295 aprovados; complemento final: 26 aprovados; ambos sem
falhas ou skips. Ruff passou e as 22 funções de produção preservaram seus corpos
na comparação AST. Os resultados consolidados e comandos estão em `quality_review.md`.
Os cenários novos cobrem retomada após cancelamento, tentativas limitadas,
rollback de foto/progressão/hints, substituição antes e durante a inferência,
isolamento do worker GPU e consumidores concorrentes em PostgreSQL.
As tabelas dos testes de fila PostgreSQL vivem em schemas descartáveis exclusivos;
o banco e a API de uso não são migrados.

## Dívida que continua explícita

- O coordenador de comandos de produção e alguns handlers de feed/chat ainda são extensos. Extrair handlers menores exige manter a transação externa única; não criar commits em cada operação extraída.
- Locks de dependências reproduzíveis CPU/GPU e digest de imagens ainda precisam de resolução e validação nas imagens alvo. O freeze do ambiente Windows não é um lock Linux/GPU válido.
- A validação usa análise substituída por um stub nos testes da fila; não comprova inferência real GPU/Gemini ou entrega de SMTP/FCM.
