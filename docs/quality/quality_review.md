# Revisão de qualidade e arquitetura — 20/10/2026

## Escopo e método

Revisão dos repositórios `gatalogo-api` e `gatalogo`, com inventário de todas as fontes Python/Dart versionadas e novas, análise estática, grafo de importações Dart, comparação de corpos de funções Python, inspeção dos módulos centrais e execução dos testes existentes. O inventário não equivale a uma prova formal de cada fluxo: integrações externas, PostgreSQL, GPU, push real e dispositivos móveis exigem ambientes próprios.

As alterações pendentes foram preservadas no commit `eced9c3`, publicado na `main`. A limpeza foi iniciada a partir dessa base em `code-quality-cleanup`. Não foram executadas migrações no banco.

## Correções realizadas

| Achado | Correção | Evidência |
|---|---|---|
| `app/exceptions.py` redefinia a classe base e três exceções; subclasses antigas não pertenciam à classe base exportada no fim do módulo | Uma única hierarquia, preservando nomes e mensagens | Sete casos em `tests/test_exceptions.py` |
| Rotas importavam `current_user` do módulo de endpoints de autenticação | Dependência única em `app/dependencies.py`; alias anterior preservado para consumidores e overrides | Suíte de endpoints e autenticação |
| Produto importava a autorização privada de conversas de social | `app/services/conversations.py` compartilha consulta de amizades e autorização, mantendo 403/404 e regras de conversas de resgate | Testes de produto, chat e social |
| Produto importava serializador privado de vision | `app/services/report_presenter.py` concentra a resposta dos relatos | Testes de relatos e correções de tela |
| Social importava constantes e funções de produto, inclusive símbolos sem uso | Consulta direta ao catálogo legado em milestones | Testes de progressão e social |
| Imports e variáveis sem uso | Removidos imports reais sem uso; chamadas com efeitos colaterais preservadas; fixtures pytest declaradas em `__all__` | Ruff e suíte completa |
| Ausência de verificação Python de correção no CI | Ruff fixado em `requirements-dev.txt`, configuração e workflow de lint | `python -m ruff check .` |

A remoção de dois imports da migração `20260927_11` não modifica suas operações SQL, identificador ou reversão.

## Mapa de pendências e prioridades

| Prioridade | Local | Evidência / problema | Próxima mudança segura |
|---|---|---|---|
| Alta | `app/routers/social.py` | Aproximadamente 1.475 linhas reúnem perfis, feed, amizade, chat e consultas | Extrair serviços por domínio com testes de autorização, paginação e transação; manter URLs |
| Alta | `app/services/quintal_production.py` | Mais de 650 linhas concentram construção, estoque, receitas e coleta | Separar regras puras e operações transacionais; validar concorrência PostgreSQL antes de alterar locks |
| Alta | `app/routers/sync.py` | `_analyze_reference_photo` é iniciada com `asyncio.create_task`; trabalho em memória pode ser perdido ao encerrar o processo | Avaliar migração para a fila persistente existente sem alterar a confirmação do upload |
| Média | `app/routers/quintal.py` | Ainda importa `_snapshot` de habitat em dois fluxos | Extrair apresentação do habitat para serviço compartilhado |
| Média | `app/routers/public_search.py` | Chama `report_photo` da rota de produto | Extrair resolução/autorização da mídia; preservar regras de exposição pública |
| Média | `app/services/auth_service.py`, rotas e workers | Responsabilidade de commit distribuída entre camadas | Documentar fronteiras por operação antes de reorganizar; OTP e entrega de eventos precisam manter semântica atual |
| Média | `app/services/websocket_manager.py`, `app/routers/websocket.py` | Tratamentos amplos de exceção em conexão/envio | Distinguir encerramento esperado e erro operacional com logging; não remover fallback indiscriminadamente |
| Média | `requirements.txt`, Dockerfiles | Dependências abertas com `>=`, referência Git do SAM-2 e imagem base sem digest | Introduzir lock reproduzível por plataforma CPU/GPU em mudança separada e testada |
| Média | Testes ignorados | Parte da cobertura depende de flags/serviços externos | Executar PostgreSQL e cenários live no ambiente adequado antes de considerar concorrência comprovada |

## Arquivos preservados e decisões de remoção

- `docs/security/session_rotation_review.patch` e a migração `20261018_14` têm ativação documentada; não são lixo. `git apply --check --ignore-space-change` continua aceitando o patch após a refatoração.
- Migrações históricas são necessárias para bancos existentes e novos; não devem ser excluídas por falta de imports Python.
- Caches, modelos, credenciais, logs, uploads, ambientes virtuais e `node_modules` estão ignorados. Não foram apagados: são dados locais, não código morto comprovado.
- Arquivos `__init__.py` vazios preservam pacotes e não representam funcionalidades obsoletas.
- Endpoints de compatibilidade e tabelas legadas não foram removidos: podem ser consumidos por versões instaladas do aplicativo.

## Validação

- Antes da limpeza: **242 aprovados, 37 ignorados**.
- Após refatorações e limpeza dos testes: **249 aprovados, 37 ignorados**.
- Ruff: verificação de todo o repositório, incluindo migrações, scripts e testes.
- Comparação AST com a main: **123 assinaturas e declarações de endpoints inalteradas**.
- Estrutura e descrição do README atualizadas para o escopo real da API.
- Patch de sessão: verificação de aplicação sem aplicá-lo.
- A etapa inicial não executou integrações externas; a atualização de 21/10 abaixo registra migrações e concorrência PostgreSQL no ambiente descartável. Deploy, migração no banco de uso, GPU e entrega real de push/e-mail continuam fora desta validação.

O arquivo `quality_inventory.md` contém a distribuição, módulos grandes, dependências entre rotas e lista completa das fontes. Pendências acima permanecem explícitas; não se afirma que toda a dívida arquitetural foi eliminada.

## Complemento de integração — 21/10/2026

O ambiente isolado executou **286 testes aprovados, zero ignorados e zero falhas**,
incluindo os 37 cenários condicionais da etapa anterior. Foram aplicadas todas as
migrações no banco descartável, exercitadas transações concorrentes e chamadas
HTTP reais, e executados dois processos Flutter com SQLite persistente, perda de
resposta após commit, replay e troca de conta.

A execução inicial com todas as flags expôs três falhas, tratadas neste complemento:

- Corrigida emissão de notificação para candidato visual restrito: a persistência
  agora respeita `notification_eligible`. O candidato permanece registrado para
  revisão; evidência suficiente pode promovê-lo depois e notificar uma única vez.
  O teste verifica ausência de aviso/evento enquanto restrito e a transição sem duplicação.
- Atualizado o teste de conquista: cadastro sincronizado não equivale a descoberta
  confirmada. O cenário agora exige confirmação de análise pelo servidor, valida
  persistência da conquista após exclusão do registro e uma única notificação de resultado.
- Atualizado o fluxo do cliente Flutter para selecionar produto, quantidade,
  pagamento e confirmar antes de simular a perda de resposta. O replay após reinício
  reproduz exatamente o pedido original e reutiliza a chave de idempotência.

O executor `scripts/run_integration_checks.py` cria banco/API temporários e encerra
os processos e o contêiner ao final. Usa segredo JWT e credenciais próprios da
execução, sem alterar flags globais ou migrar o banco de uso. Seu modo de seleção
foi validado separadamente com os **4 testes de concorrência PostgreSQL aprovados**.
SMTP fica desabilitado: a disponibilidade do Mailpit não é evidência de teste de entrega.

A suíte padrão continua opt-in para essas integrações; o comando documentado no
README habilita as flags e fornece os pré-requisitos automaticamente. Os demais
itens de arquitetura mapeados acima permanecem explícitos.


## Refatoração arquitetural — 21/10/2026

O mapa inicial acima é histórico. A execução e as pendências atuais estão em
[architecture_refactoring.md](../architecture/architecture_refactoring.md).

Foram extraídos apresentadores de habitat/mídia, domínios sociais, regras de
produção e análise de referência persistente. HTTP e WebSocket compartilham
recibos e autorização de conversa. O OpenAPI integral permaneceu idêntico.
Validação da etapa:

- Rodada completa integrada: **295 aprovados, zero ignorados e zero falhas**.
- Complemento final de fila, WebSocket e produto: **26 aprovados, zero ignorados e zero falhas**, incluindo os testes adicionados após a rodada completa.
- Entrega/limpeza WebSocket: **13 aprovados** na verificação final focada.
- Ruff do repositório inteiro: **sem apontamentos**.
- OpenAPI antes/depois: **idêntico**, 122 endpoints HTTP.
- Comparação AST das regras de produção: **22 corpos de função idênticos**.
- Os dois ambientes PostgreSQL/API descartáveis foram removidos pelo executor. A API de uso respondeu `ok`; PostgreSQL e Mailpit originais permaneceram saudáveis.

Logs locais: `.tmp/integration-abb3538bf34f/pytest.log`,
`.tmp/integration-bf3497674c11/pytest.log` e `.tmp/architecture-events-tests.log`.
As próximas fronteiras maiores e a limitação de validação GPU/Gemini real
continuam explícitas no documento de arquitetura.


## Conclusão da rodada — 21/10/2026

Todas as linhas do mapa anterior foram revisadas e classificadas em
[architecture_completion.md](../architecture/architecture_completion.md). Concluídos os comandos
por domínio do Quintal, serviços de consultas/comandos sociais, contratos
compartilhados, claims persistentes curtos e locks Linux CPU com imagens validadas.

A revisão específica de recursos removeu a conexão mantida durante a inferência
de referências e também liberou a transação de leitura do reconhecimento antes
do processamento. Retomada por lease, tentativa crescente e releitura impedem
aplicação de resultado atrasado. Savepoints preservam foto/progressão e descartam
eventos revertidos. Testes verificam zero conexões ocupadas durante a inferência.
Cache de segmentação e detector de passagem única evitam recomputação; os testes
comparam máscara/resultado e contagem de chamadas, sem inventar ganho de throughput.

Validação final:

- Regressão integrada completa: **318 aprovados, zero ignorados, zero falhas**.
- Complemento final de workers e HTTP/cliente live: **33 aprovados, zero ignorados e zero falhas**.
- Ruff do repositório inteiro e guardas estáticas: aprovados.
- OpenAPI integral idêntico: **122 endpoints HTTP**; hash no documento de conclusão.
- Locks verificados dentro das duas imagens finais: **71 dependências** por imagem,
  Linux x86_64/Python 3.12.15, nenhuma ausente, commit SAM-2 conferido.
- Builds e smoke tests finais dos dois Dockerfiles CPU aprovados, incluindo imports da aplicação, SAM/Torch/OpenCV, operação tensorial CPU, contratos e pip check.
- Os bancos/APIs da regressão completa e do complemento foram removidos; nenhuma migração no banco de uso.

Logs locais: `.tmp/integration-04526fcc5a39/pytest.log`,
`.tmp/integration-f6a98d052555/pytest.log`, `.tmp/architecture-complete-complement.log`,
`.tmp/architecture-worker-release-build.log`, `.tmp/architecture-worker-release-smoke.log`,
`.tmp/architecture-web-final-build.log`, `.tmp/architecture-web-final-smoke.log`.
As árvores instaladas ficam em `.tmp/web-verified-lock.json` e
`.tmp/worker-verified-lock.json`; fonte e CI não dependem desses artefatos locais.

O perfil existente é CPU. CUDA, inferência real, smartphone e entrega SMTP/FCM
exigem validação operacional própria; não foram apresentados como aprovados por
mocks ou imports. Não houve build Android, emulador, deploy ou merge na main.


## Mapeamento de fluxos e mecanismos — 21/10/2026

Fluxos, medições e oportunidades priorizadas estão em
[performance_flows.md](../performance/performance_flows.md). Leituras em lote substituem buscas
por gato; a API também libera a conexão de autenticação antes da análise principal.
As consultas, contratos, dados visíveis e regras foram comparados com o caminho
anterior. Nenhuma alteração em páginas, widgets, modelos de IA ou parâmetros
de imagem. Resultado desta rodada: **324 testes completos + 10 complementares; sem falhas ou ignorados; Ruff e OpenAPI aprovados**.
As contagens de consultas/conexões não são medições de latência em smartphone.
