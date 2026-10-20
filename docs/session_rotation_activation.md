# Ativação da sessão por aparelho

Preparado em 18/10/2026. A usuária executará o Alembic. **Nenhuma migração foi executada por esta revisão.**

A API em hot reload continua compatível com o esquema atual. O código que depende dos novos campos está em `docs/session_rotation_review.patch`, incluindo seus testes. Não aplique esse patch antes da migração: o ORM passaria a consultar colunas ainda ausentes.

Na pasta da API, execute nesta ordem:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade 20261018_14
git apply --check --ignore-space-change docs/session_rotation_review.patch
git apply --ignore-space-change docs/session_rotation_review.patch
.\.venv\Scripts\python.exe -m pytest tests/test_device_session_rotation.py -q
```

O patch preserva o identificador da sessão durante a rotação, grava somente hashes de refresh tokens, vincula a renovação ao aparelho e admite repetição da resposta por 60 segundos quando a conexão cai. Logout e expiração continuam impedindo renovação. O prazo configurado permanece inalterado; não foi aplicado prazo de 365 dias.

Validação anterior: dois testes em cópia isolada, com SQLite em memória, passaram. Concorrência real no PostgreSQL só pode ser verificada depois da aplicação. Não é necessário gerar APK para aplicar ou testar esta parte.
