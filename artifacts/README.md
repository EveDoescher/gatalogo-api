# Artefatos locais

- `logs/`: logs antigos, separados por assunto.
- `received/`: pacotes e evidências recebidos.
- `reports/`: relatórios locais gerados, quando existentes.

Os arquivos foram realocados sem exclusão. Seu conteúdo permanece local e não entra no Git ou em imagens Docker. Evidências rastreadas em `.tmp/quality-campaign/` e arquivos de runtime/build mantêm seus caminhos. Novos logs devem ser gravados em `artifacts/logs/`, e não na raiz.
