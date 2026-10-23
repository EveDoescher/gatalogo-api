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


A fonte canônica do probe comum está em `catlogue/tool/campaign_probe_test.dart`,
para ser analisada dentro do pacote Flutter. `prepare` congela esse arquivo junto
com os outros `tool/campaign_*test.dart`; o executor copia o snapshot para o diretório
de testes da versão avaliada. As tentativas antigas conservam seu snapshot original
`flutter_probe_test.dart` e seu executor original, sem alteração das evidências.


## Baterias locais adicionais — 23/10/2026

Depois de preparar uma tentativa NOVA, os seletores `--execute-delivery`,
`--execute-crossed`, `--execute-social-volume`, `--execute-image`,
`--execute-storage-resilience`, `--execute-mechanisms`, `--execute-pipeline`,
`--execute-worker-race`, `--execute-query-plan`, `--execute-socket-fanout`,
`--execute-wire-contract` e `--execute-identity` usam os executores congelados.
`QUALITY_API_ARM=A/B` escolhe o braço somente de `crossed`; identity executa A e B
em processos separados contra schemas próprios. A suíte identity conserva contratos
vermelhos de segurança e não pode ser usada como gate verde de mutação.

O gate de wire-contract reconhece especificamente a TestFailure de ID vazio no
reporter Flutter, junto do evento HTTP/TCP e arquivo consumido; seu PASS caracteriza
uma falha real, não uma aprovação. Casos inválidos/timeout de executores permanecem
separados. Não generalizar `isFailure=false` como ausência de asserção.

```powershell
# Ajustar RUN/NEW e usar uma tentativa nova para cada comando.
python scripts/campaign/flutter_resilience.py --run RUN --attempt NEW --baseline-only --files campaign_notification_races_test.dart
python scripts/campaign/flutter_catalog.py --run RUN --attempt NEW
python scripts/campaign/flutter_visual.py --run RUN --attempt NEW
python scripts/campaign/flutter_visual.py --run RUN --attempt NEW --arm A --goldens-only
python scripts/campaign/health_campaign.py --run RUN --attempt NEW
```

A prova de catálogo aceita `QUALITY_FLUTTER_COUNT=0/40/501` e
`QUALITY_FLUTTER_NO_GUEST=1`; o default mantém o piloto 40+guest. O executor de saúde
pré-registra amostra, aquecimento e orçamento. Não repetir até obter uma comparação
favorável. Uma célula instável permanece explícita. Não equivale a carga prolongada.

Filtros de revalidação usados nesta etapa: `QUALITY_STORAGE_FILTER=revalidate`,
`QUALITY_MECHANISM_FILTER=money`, `QUALITY_WORKER_FILTER=process`; selectors,
snapshots e evidências mostram o subconjunto exato. A ausência de uma falha na
revalidação filtrada não converte toda a tentativa anterior em verde.

`local_report.py --run RUN --output DIRETORIO_NOVO` conserva os relatórios anteriores,
indexa resultados/snapshots e mantém a matriz parcial. Documentar separadamente
recomendações e limitações; não publicar logs, tars, JUnit, SQLite ou eventos brutos.

[Relatório local e pendências](../../docs/campaigns/2026-10-23-local-campaign/README.md).


## Recuperação e controles adicionais

`--execute-identity-controls`, `--execute-offline-flow`, `--execute-upload-transport`,
`--execute-social-history` e `--execute-social-order` exigem preparação com um nome
novo. O teste de ordem mantém o contrato vermelho nos dois braços e não é gate verde.
O fluxo offline usa cinco processos Flutter/SQLite reais, com sessão/plataforma
simuladas; a foto pendente não é apresentada como analisada ou automaticamente enviada.

`flutter_accessibility_flows.py --run RUN --attempt NEW` executa 48 células em quatro
páginas, mais o controle do destino de evidência, com diretório/SQLite/PackageInfo
próprios. O status error do reporter é preservado junto dos motivos do SDK; não se
confunde com erro de compilação ou com aprovação de leitor físico.

`dependency_applicability.py --run RUN --attempt NEW` lê somente o material já
consultado e a instalação local; não realiza rede nem executa payloads. Classificações
são precondições estáticas com limites, não exploração confirmada ou ausência de risco.

`followup_report.py --run RUN --previous RELATORIO_ANTERIOR --output DIRETORIO_NOVO
--attempts A,B,C` gera um incremento para tentativas explícitas e mantém as fontes,
seletores, caminhos revisados e snapshots. Conserva todos os resultados brutos.

[Resultados de recuperação e acessibilidade](../../docs/campaigns/2026-10-23-recovery/README.md).
