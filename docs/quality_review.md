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

- `docs/session_rotation_review.patch` e a migração `20261018_14` têm ativação documentada; não são lixo. `git apply --check --ignore-space-change` continua aceitando o patch após a refatoração.
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
- Sem teste de deploy, migração real, GPU, entrega de push ou carga concorrente em PostgreSQL nesta revisão.

O arquivo `quality_inventory.md` contém a distribuição, módulos grandes, dependências entre rotas e lista completa das fontes. Pendências acima permanecem explícitas; não se afirma que toda a dívida arquitetural foi eliminada.
