# Fluxos e mecanismos de desempenho — 21/10/2026

Escopo: reduzir trabalho interno mantendo interface, regras, mensagens, dados,
ordenação observável, autorização e funcionamento offline. Branch `code-quality-cleanup`.
As contagens abaixo são medições de consultas/conexões em testes, não benchmarks
em smartphone nem promessas de redução percentual de tempo.

## Mapa dos fluxos

| Fluxo | Caminho efetivo | Responsabilidade que deve permanecer |
|---|---|---|
| Entrada e sessão | SessionStore → autenticação/renovação → catálogo local → sync/progresso/notificações | Tokens, conta capturada, preferência de auto-sync e recuperação offline. |
| Nova foto | CapturePage → preflight/localização → CatStore.addFromTempPhoto → SQLite → sync de metadados → POST /analyze → AnalysisService → gravação/progressão/job de reconhecimento → resposta → atualização local/sync | Confirmação local, mesmos bytes e parâmetros, mensagens de rejeição, estados, histórico e recompensas. Metadados antes da análise evitam gato órfão. |
| Análise/reconhecimento | Preprocessamento/qualidade → detector/SAM → cores → cache/Gemini quando necessário → resultado; job separado para reconhecimento | Nenhum modelo, limiar, qualidade JPEG, resolução, regra de aceitação ou política de retry alterado nesta rodada. |
| Foto complementar | SQLite/dirty → PUT /sync/.../reference-photos/... → job persistido → worker → progressão/revisão → pull | Claim, prazo, resultado atrasado, hash, cancelamento e publicação após commit. |
| Abrir/atualizar coleção | CatStore → CatalogSyncCoordinator → gatos, análises, referências SQLite → agrupamento/apresentação | Conta + convidados, tombstones, análise por ID, fotos por tipo e ordem dos gatos. |
| Sincronizar/reconectar | Dirty cats/analyses → /sync/push → upload aplicável → /sync/pull?cursor → mídia → SQLite → cursor | Conflitos offline, exclusões, cursor, retry, identidade e troca de conta. |
| Conversas | Lista → detalhes/mensagens em paralelo → mensagem otimista → POST/commit → WebSocket/FCM → reconciliação | Recibos, ordem, anexos, privacidade, reconexão e fallback de polling. Foto de chat tem upload antes do envio da mensagem. |
| Comunidade e buscas | Feed/paginação, perfis/amizades, alertas/pistas → autorização/consulta → apresentadores → UI | Post privado e localização privada nunca podem ser expostos para reduzir consultas. |
| Quintal | Cache local → habitat/loja → produção → comandos idempotentes → lock/carteira/reconciliação → commit → resposta/cache/cena | Mesmos preços, relógio, estoque, recompensa, efeitos, hit testing e replay após perda de resposta. |
| Notificações/favoritos | Eventos WebSocket/push → controllers/stores → consulta/merge/cache → contadores/UI | Deduplicação, recibos de leitura, conta e sincronização; não remover fallback necessário. |

## Melhorias implementadas

### Pull em lote

`routers/sync.py` carrega os gatos da página em uma consulta e delega a
`services/catalog_sync_queries.py` revisões e referências em lote. Sem cache global,
novas transações, commits ou mudanças nos modelos públicos.

- Antes: `1 + 3N` SELECTs internos para N gatos distintos em uma página fria.
- Depois: **4 SELECTs**, ou **1** quando não há alterações.
- Caso medido de 40 gatos: **121 → 4**. Autenticação HTTP não faz parte dessa contagem.
- A revisão apresentada continua sendo a mais recente, mesmo além do cursor da
  página; o cursor avança somente pelas alterações consumidas.
- IDs são remontados na ordem do mapa anterior; registros de outra conta,
  foto primária e referência excluída continuam fora da resposta aplicável.
- Teste compara o JSON integral com o caminho anterior, incluindo tombstones,
  propriedades desconhecidas, datas, fotos, revisão e paginação.

### Conexão no envio principal de foto

A autenticação deste endpoint apenas lê sessão/usuário. O identificador é
capturado antes de encerrar a transação de leitura; a conexão é devolvida ao pool
antes da construção/chamada da análise. Escritas, locks e commit continuam depois
da inferência, na mesma ordem. A análise das referências e o reconhecimento já
liberavam suas conexões na rodada arquitetural anterior.

O teste antes da mudança registrou **1 conexão retida durante a IA**; depois exige
**0**, preservando resposta de sucesso, persistência, job, evento após commit,
erro 503 e rejeição 422, incluindo a chamada de registro da rejeição.
Modelos são substituídos somente nos testes de controle de recursos.

### SQLite do cliente

O cliente lê análises/referências apenas dos IDs necessários e usa lotes de no
máximo 500 parâmetros. Não muda versão/schema SQLite nem métodos existentes.
O push também deixa de consultar análise separadamente para cada gato dirty.
Detalhes e evidências estão no relatório de desempenho do repositório Flutter.

## Próximas oportunidades: identificadas, ainda não implementadas

| Prioridade | Evidência no código | Próxima comprovação antes de mudar |
|---|---|---|
| Alta | `chat_queries.conversations_list` faz buscas de participante, última mensagem e contador por conversa (`2 + 3C`, estimativa de inspeção) | Medir por tamanho de inbox e testar consultas em lote com conversa vazia, resgate, recibos e ordenação. |
| Alta | `CatalogSyncService` usa helpers HTTP e envio multipart que podem abrir clientes separados ao longo de um sync | Instrumentar handshakes/conexões; avaliar um cliente por operação single-flight, com fechamento garantido e os mesmos headers, timeouts e retries. |
| Alta | Após análise, futuras sincronizações podem reenviar a mesma foto primária quando o registro fica dirty | Traçar bytes/requisições reais; exigir confirmação por hash e conta antes de suprimir upload, inclusive retry, reinício e substituição. Não remover o sync pré-análise. |
| Média | Feed consulta preferências de autor e, para posts privados, amizade durante a paginação | Medir por página/autor; pré-carregar por requisição sem enfraquecer autorização ou mudar paginação depois da filtragem. |
| Média | Pull de referência baixa arquivos ao aplicar registros; atualização textual/estado pode não ter alterado a imagem | Medir tráfego; validação por conteúdo/ETag antes de reaproveitar arquivo. Timestamp sozinho não foi tratado como prova de identidade do conteúdo. |
| Média | Carregamento do Quintal lê /habitat e depois /quintal, que também inclui habitat | O GET /habitat também recupera entregas de presentes; preservar esse efeito, calendário, rascunho e fallback antes de unificar requisições; medir latência e payload. |
| Dependente de perfil | Cena Flame e overlays possuem cálculos por quadro; notificações podem chegar por push e WebSocket | Perfil de CPU/build/raster no aparelho. NotificationStore já combina refreshes concorrentes; não adicionar debounce geral nem cache visual sem invalidação comprovada. |

Estas oportunidades têm status explícito: não são ganhos já entregues. Limites de
inferência, concorrência de escrita e ordem de notificações não foram removidos
para produzir números melhores.

## Validação

- Suíte integrada completa: **324 aprovados, zero ignorados e zero falhas**.
- Complemento final dos mecanismos (incluindo liberação da conexão na análise
  principal): **10 aprovados, zero ignorados e zero falhas**, em SQLite/PostgreSQL.
- Ruff e comparação integral OpenAPI aprovados, com os mesmos 122 endpoints HTTP.
- Flutter: 278 aprovados, 6 condicionais e zero falhas; analyzer final limpo.

Logs: `.tmp/integration-37866e44fd2b/pytest.log`,
`.tmp/integration-4e40fba2791e/pytest.log` e `.tmp/performance-full-integration.log`.
Os dois ambientes descartáveis foram removidos. Fonte e CI não dependem desses logs.

Testes novos: `tests/test_sync_query_performance.py` (SQLite e
PostgreSQL descartável), comparações de resposta, contagem de SELECTs e uso do pool.
Nenhuma migração ou teste foi direcionado ao banco de uso. Sem build Android,
emulador, nova dependência ou alteração de tela nesta rodada.


Reprodução dos testes focados:

```powershell
.\.venv\Scripts\python.exe scripts/run_integration_checks.py tests/test_sync_query_performance.py
```

O executor fornece as flags e um PostgreSQL/API descartáveis. O cenário de pool
usa inferência substituída; não é benchmark de SAM/Gemini. O baseline deliberado
anterior à correção está em `.tmp/performance-primary-baseline.log`; resultados
após a correção ficam em `.tmp/performance-mechanisms-focused.log`.
