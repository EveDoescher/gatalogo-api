# Executores da campanha aprovada

Código de teste externo ao produto. Entrada: `scripts/quality_campaign.py`.
Método: [plano aprovado](../../docs/test_plan_efficiency.md).
Primeiro resultado: [campanha-piloto](../../docs/campaigns/2026-10-21-pilot/README.md).

## Reprodução segura

Requer Python da `.venv` da API, Docker local e Flutter já instalado com dependências
resolvidas no repositório vizinho. Nenhuma dependência nova foi instalada. A falta
de `psutil` é contornada por métricas nativas do processo Windows; campos ausentes
em outras plataformas não significam zero.

Na raiz da API, ambos os repositórios em `code-quality-cleanup`:

```powershell
.\.venv\Scripts\python.exe scripts/quality_campaign.py
```

Guardar o caminho impresso como `<campanha>`. O comando registra os quatro SHAs e
exporta Git archives isolados para `.tmp/quality-campaign/`. Alterações locais são
listadas e preservadas, mas não entram no código medido. Não cria checkout nem
altera refs de main. Arquivos de credenciais são excluídos; `.env.example` permanece.
No Windows, Git pode traduzir LF/CRLF; `verify_sources.py` verifica conteúdo contra
blobs e discrimina essa única normalização permitida em arquivos de texto.

```powershell
.\.venv\Scripts\python.exe scripts/quality_campaign.py --run <campanha> --prepare --attempt pilot-01
.\.venv\Scripts\python.exe scripts/quality_campaign.py --run <campanha> --execute-api --attempt pilot-01
.\.venv\Scripts\python.exe scripts/quality_campaign.py --run <campanha> --execute-flutter --attempt pilot-01
.\.venv\Scripts\python.exe scripts/campaign/verify_sources.py --run <campanha>
.\.venv\Scripts\python.exe scripts/campaign/report.py --run <campanha> --output <novo-diretorio-do-relatorio>
```

Executar API e Flutter **sequencialmente** para comparar tempos sem compilação
concorrente. Ordem de cada componente: A/B/B/A. `--no-mutations` executa só as
quatro rodadas API; os mutantes dependem de duas baselines B aprovadas na mesma
tentativa. Execuções pagas, Android, emuladores e dispositivo físico não fazem
parte destes comandos. O teste Flutter é desktop/SQLite FFI.

O harness existente `run_integration_checks.py` fornece PostgreSQL/API descartáveis,
migrações, readiness e cleanup. É copiado e identificado por hash na tentativa.
Não usar DATABASE_URL de uso real. O probe exige `recognition_test` em porta
efêmera, cria um schema por rodada e verifica `current_schema()` antes de DDL.
FCM e análise do probe são substituídos; sem lifespan no cliente ASGI. A API de
apoio fica em loopback sem credenciais externas e é encerrada pelo próprio runner.
Os subprocessos têm teto de tempo e a matriz não aprova falha de infraestrutura.

Cada tentativa possui executores congelados e resultados próprios. Nunca reutilizar
um nome de tentativa já executada nem sobrescrever evidências; corrigir executor e
preparar novo `--attempt`. As cópias de produto A/B continuam iguais. Só testes
externos são adicionados ao diretório test/ das cópias Flutter; mutantes recebem
cópia separada de B. Logs brutos podem conter detalhes internos e ficam locais.

## Componentes e limites

| Arquivo | Responsabilidade | Limitação explícita |
|---|---|---|
| fixtures.py | IDs/clock/seeds, catálogos, JSON social/inbox, imagens sintéticas, especificações F-* | Sem gato real rotulado. F-SOC JSON não é carga social no PostgreSQL; variações não exercitadas permanecem pendentes. |
| api_probe.py | Auth real + ASGI + SQL/pool/CPU/RAM/handles; replay concorrente do Quintal | Não mede TCP, telefone, IA real nem capacidade sob carga. |
| pilot_cases.py | Quatro subprocessos A/B/B/A e gates de integridade | Expectativa diagnóstica histórica DB-05 de A é relatada; nenhuma falha de B é dispensada. |
| mutation_cases.py | MUT-01/MUT-12 com diff mínimo/cópia nova/baseline limpa | Outras 26 famílias só têm estratégia/oráculos no registro, ainda sem executores próprios. |
| catlogue/tool/campaign_probe_test.dart | API pública comum CatStore.loadCats, SQLite/queries/tempo/RSS | Desabilita caminhos de rede em subclass de teste; usa interface interna da factory para observar queries, sem mudar código medido. Não representa celular. |
| measurements.py | Perf counter, CPU do processo, Win32 RAM/handles, quantis exploratórios | nearest-rank; P99 sempre ausente neste piloto. CPU Windows tem resolução grosseira. |
| fault_proxy.py | Delay limitado e drop antes/depois do encaminhamento, loopback sem redirects | Não implementa limitação de banda/perda real de pacotes. Testado em servidor próprio; não comprova commit do produto por si só. |
| verify_sources.py | Integridade dos arquivos app/lib vs commits Git | LF/CRLF registrado; não aceita outras divergências. |
| report.py | Matriz 95 IDs, registro 28 MUT, hashes, resumo e amostras comprimidas | Execução parcial jamais promove cenário inteiro a aprovado. |

Casos de teste da infraestrutura:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_quality_campaign_harness.py
.\.venv\Scripts\python.exe -m ruff check scripts/quality_campaign.py scripts/campaign tests/test_quality_campaign_harness.py
```

Os arquivos `pilot_cases.py`/`mutation_cases.py` só são chamados explicitamente pelo
runner, não entram na coleta pytest comum. As amostras versionadas são sintéticas,
sanitizadas e comprimidas; abrir com `gzip.open(..., 'rt', encoding='utf-8')` e
`json.load`. O índice mantém SHA-256 das evidências locais originais.


## Bateria cr�tica e acessibilidade (continua��o)

Preservar cada tentativa anterior. Primeiro arquivar com `archive_evidence.py --run RUN`;
o arquivo em `.tmp/quality-archives` cont�m logs privados e n�o deve ser publicado.
O recibo verifica cada membro SHA-256 sem remover originais.

```powershell
.\.venv\Scripts\python.exe scripts/quality_campaign.py --run RUN --prepare --attempt NOVO
.\.venv\Scripts\python.exe scripts/quality_campaign.py --run RUN --execute-critical --attempt NOVO
# Em outra tentativa, ap�s finalizar a API:
.\.venv\Scripts\python.exe scripts/quality_campaign.py --run RUN --prepare --attempt OUTRO
.\.venv\Scripts\python.exe scripts/quality_campaign.py --run RUN --execute-accessibility --attempt OUTRO
```

`critical_suite.py` roda `security_cases.py` e os nove testes PostgreSQL existentes
selecionados. Cada mutante tem c�pia pr�pria, baseline limpa e seu or�culo deve ter
sido executado. Erro de setup/importa��o, skip ou aus�ncia de casos n�o mata mutante.
`QUALITY_MUTANT_FILTER` aceita IDs separados por v�rgula; `QUALITY_BASELINE_FILTER=DB-02`
seleciona somente o hist�rico ampliado (usar junto com `MUT-05`). Remover filtros para
bateria completa. Os filtros s�o registrados nas novas tentativas; as anteriores
mant�m seletores efetivos e hashes originais. Timeout de subprocesso � infraestrutura,
nunca aprova��o. A baseline usa OTP/JWT reais e PNG sint�tico; an�lise e FCM substitu�dos.

O audit Flutter fica em `catlogue/tool/campaign_accessibility_test.dart`, fora da
coleta normal de `test/`: � uma auditoria expl�cita que permanece vermelha diante de
achados. Usa tema real, fontes locais, Semantics e guidelines do SDK, sem Android ou
leitor f�sico. O comando tamb�m roda os dez testes de HabitatService. Escopo � o
viewport inicial do login e o controle de subida; n�o � aprova��o global de ACC.
`confirmatory_protocol.json` descreve medi��es futuras, ainda n�o executadas e sem
coletor de estacionariedade/IC implementado; n�o autoriza carga automaticamente.

Relat�rio consolidado: `docs/campaigns/2026-10-21-critical/README.md`.


Current executors and probe relocation: [CONTINUATION.md](CONTINUATION.md).
