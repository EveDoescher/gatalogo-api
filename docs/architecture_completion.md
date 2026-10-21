# Conclusão da rodada arquitetural — 21/10/2026

Branch: `code-quality-cleanup`. A main não recebe alterações nesta rodada.
Este documento substitui a lista de próximos passos da etapa anterior; os relatórios
anteriores preservam as evidências históricas.

## Classificação do mapa

| Pendência | Resultado e fronteira |
|---|---|
| Rotas sociais extensas | Perfis, amizades, dispositivos, feed e chat têm roteadores separados. Feed/chat delegam consultas e comandos a serviços com sessão e argumentos explícitos. Schemas compartilhados ficam fora dos roteadores. Fachada social mantém os exports antigos. |
| Comandos do Quintal | Construção, horta, tarefas, residentes, gatos e recibos têm handlers de domínio. O coordenador mantém carteira, reconciliação, idempotência, débito, revisão e registro na mesma transação. Handlers não fazem commit/rollback. |
| Apresentação e autenticação | Apresentadores compartilhados de habitat, mídia, relatos, chat e social; dependência única de usuário e autorização compartilhada de conversas HTTP/WebSocket, concluídos na etapa anterior. |
| Análise de referência em memória | Job persistido junto ao upload. Claim curto confirmado antes da inferência; nenhuma conexão de banco permanece ocupada durante o processamento. Recuperação por prazo e contador crescente de tentativa impedem aplicação de resultado de consumidor expirado. |
| Reconhecimento e segmentação | Fase de inferência do reconhecimento também libera a conexão de leitura e relê os dados antes de gravar. Cache de segmentação evita repetir detector/refinador na mesma foto; o teste compara máscara e resultado e exige uma única chamada. Conexões SQLite do cache são fechadas explicitamente. |
| Fronteiras transacionais | Comandos sociais confirmam e depois publicam eventos. OTP conserva os commits intencionais de tentativas rejeitadas. Worker confirma resultado/progressão juntos; savepoint revertido descarta eventos. |
| Dependências de execução | Locks Linux amd64/Python 3.12 CPU, Torch oficial CPU e commit completo SAM-2. Imagens Python, PostgreSQL e Mailpit têm digest; pip check é executado nos dois Dockerfiles. |
| Contexto de build | `.dockerignore` exclui segredos locais, uploads, modelos, caches, ambientes e Git. Manifesto de entrada e locks permanecem disponíveis nas imagens. |
| CI | Ruff e testes estáticos de fronteiras/locks com ferramentas fixadas, independentes dos serviços externos. Integração completa permanece executável pelo harness descartável. |
| Arquivos candidatos a remoção | Migrações, patch de sessões não ativado, ferramentas, compatibilidade e assets dinâmicos foram classificados e preservados. Não há exclusão por mera ausência de import. |

## Protocolo e confiabilidade do worker

1. Upload e job são confirmados juntos; o cliente mantém o contrato 202/analyzing.
2. Claim seleciona pendente ou execução com prazo vencido usando SKIP LOCKED,
   persiste início/tentativa e fecha a sessão.
3. Leitura limitada do arquivo roda em thread; a inferência possui timeout e ocorre
   sem conexão, transação ou lock de banco abertos.
4. Resultado adquire conta → gato → foto → job, confere tentativa, estado, hash e
   exclusões, e aplica análise/progressão uma única vez. Resultado antigo é descartado.
5. Falha tem tentativas limitadas; cancelamento devolve o claim a pendente. Morte
   abrupta permite retomada após o prazo. Cancelamento já consumiu uma tentativa,
   pois o claim foi persistido: o teste antigo de rollback foi atualizado por essa
   mudança deliberada do protocolo interno, preservando a retomada externa.
6. Savepoint separa falhas de progressão da atualização do job; eventos revertidos
   não são publicados. Não há nova migração nem mudança de payload público.

O teste de recursos verifica zero conexões retiradas do pool durante a inferência.
Os testes de consumidor atrasado, recuperação, substituição, cancelamento e falha
terminal usam a implementação real da persistência com inferência substituída.

## Ambientes e limites

Os dois Dockerfiles atuais são CPU. Não existe perfil CUDA validado neste projeto;
um futuro perfil CUDA precisa de lock e execução próprios. Um freeze Windows não
foi usado como lock Linux. Pacotes apt continuam recebendo atualizações de segurança;
não se promete reprodução binária do sistema operacional inteiro.

Não foram executadas migrações no banco de uso. O harness cria banco/API e schemas
exclusivos e remove seus recursos. O banco descartável usa tmpfs limitado a 1 GiB
para evitar a latência de fsync do volume Docker observada na inicialização; não é
a configuração de durabilidade do banco de uso. Entrega SMTP/FCM, inferência real Gemini/SAM e
GPU não são comprovadas por mocks, imports ou smoke tests. Permanecem validações
operacionais manuais, e não refatorações arquiteturais omitidas.

## Resultados verificáveis

- Regressão completa integrada: **318 aprovados, zero ignorados e zero falhas**.
- Complemento final de workers/cliente live: **33 aprovados, zero ignorados e zero falhas**.
- Ruff de todo o repositório e três guardas estáticas de arquitetura: aprovados.
- OpenAPI integral antes/depois: idêntico, **122 endpoints HTTP**.
- Duas imagens CPU reconstruídas a partir dos locks; **71 dependências** por
  imagem verificadas diretamente em Linux x86_64/Python 3.12.15, nenhuma ausente,
  incluindo o commit SAM-2 esperado. Não foi usado freeze Windows.
- Smoke test do worker final: imports da aplicação/SAM/Torch/OpenCV, tensor CPU,
  contratos e pip check aprovados. Smoke final da API também aprovado: imports, tensor CPU, contratos e pip check.
Comandos reproduzíveis a partir da raiz:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe scripts/run_integration_checks.py
.\.venv\Scripts\python.exe -m pytest --noconftest tests/test_architecture_boundaries.py -q
docker build -f Dockerfile.vision -t gatalogo-quality-runtime:local .
docker build -f Dockerfile -t gatalogo-quality-api-runtime:local .
```

O OpenAPI integral é comparado com o snapshot anterior à refatoração. Logs de
execução locais ficam ignorados em `.tmp`; o projeto e CI não dependem deles.

Hash SHA-256 do OpenAPI canônico preservado (JSON com chaves ordenadas e
separadores compactos): `59d651068becf3693226f5a6d4f0ff4ffeb5591fde23dc2c97472126f9c8b4ec`.

Árvore instalada reproduzível sem rede após o build:

```powershell
docker run --rm --network none --entrypoint python gatalogo-quality-runtime:local scripts/export_runtime_lock.py requirements.txt
```

As pequenas rotinas `stop` semelhantes dos consumidores foram mantidas junto ao
ciclo de vida de cada classe: extrair um mixin para poucos comandos de cancelamento
não oferece benefício proporcional ao acoplamento entre serviços independentes.
