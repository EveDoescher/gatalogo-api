# Executores da continuação — 22/10/2026

Preparar uma tentativa com nome novo antes de cada execução:

```powershell
.\.venv\Scripts\python.exe scripts/quality_campaign.py --run RUN --prepare --attempt NOVO
.\.venv\Scripts\python.exe scripts/quality_campaign.py --run RUN --execute-extended --attempt NOVO
```

`--execute-targeted`, `--execute-queue`, `--execute-privacy` e `--execute-full`
também usam PostgreSQL/API próprios. O runner encerra somente seus próprios recursos.
A suíte privacy preserva um contrato vermelho conhecido; não é gate de mutação.
A revalidação `--execute-diagnostic` exige `QUALITY_LIVE_REWARDS_VARIANT=enabled/disabled`,
`PROGRESSION_REWARDS_ENABLED=true` e `PROGRESSION_ROLLOUT_PERCENT=100/0` no processo
que cria o ambiente descartável. Os valores são registrados, sem alterar a API usual.

Depois de preparar outras tentativas, executar sequencialmente:

```powershell
.\.venv\Scripts\python.exe scripts/campaign/flutter_resilience.py --run RUN --attempt OUTRO
.\.venv\Scripts\python.exe scripts/campaign/flutter_resilience.py --run RUN --attempt SQLITE --baseline-only --files campaign_resilience_test.dart
.\.venv\Scripts\python.exe scripts/campaign/flutter_full.py --run RUN --attempt REGRESSAO
```

`--mutants` e `--files` só restringem as mutações quando todos os oráculos selecionados
passaram na baseline. `--baseline-only` não classifica mutantes. O live client não entra
no gate de mutação porque sem API própria seria pulado. Na regressão completa, o skip
fica separado; a integração live com perda de resposta/reinício é exercitada pelo runner da API.

`review_flutter_mutations.py` acrescenta revisão aos JSONL existentes, preservando as
classificações originais. `failure` do reporter pode indicar uma asserção válida;
compilação/setup/timeout nunca é um kill. Timeout 124 é infraestrutura e encerra
somente a árvore de processo criada pelo executor.

`preflight.py` observa CPU/RAM; `statistics_checks.py` implementa verificações de
estacionariedade e intervalos. Faltam integração, orçamento e condições controladas
para medição confirmatória. Não há nova conclusão de ganho de desempenho.

A consulta OSV foi autorizada para os 254 nomes/versões do payload revisado e os dois
endpoints especificados. Os scripts de rede não rodam automaticamente na campanha;
a autorização não se estende a outros dados/destinatários. Nenhum exploit é executado.

`continuation_report.py` cria um diretório novo, exige tentativas e manifestos existentes
e consolida evidências sem publicar logs brutos. Arquivar antes e depois com
`archive_evidence.py`; conservar tars, JUnit, SQLite e logs apenas no ambiente privado.

Relatório: [continuação](../../docs/campaigns/2026-10-22-continuation/README.md).
