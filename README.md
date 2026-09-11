# Gatálogo AI API

API FastAPI responsável pela análise de pelagem das fotos enviadas pelo aplicativo **Gatálogo**.

## O que já está pronto

- `GET /health`
- `POST /analyze` com `multipart/form-data`
- upload de foto + `cat_id`
- validação de tipo, tamanho e quantidade de pixels da imagem
- correção de orientação EXIF, redução e compressão antes do Gemini
- análise técnica local de nitidez, brilho, contraste e exposição extrema
- detector aberto + SAM 2 para segmentar o gato e gerar um recorte focado
- integração preparada com `gemini-3.6-flash`
- saída estruturada validada com Pydantic
- chave da Gemini API isolada em `.env`
- normalização dos percentuais de cor
- erro específico quando nenhum gato é identificado

## Estrutura

```text
gatalogo-ai-api/
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── exceptions.py
│   ├── main.py
│   ├── models/
│   │   ├── __init__.py
│   │   └── analysis.py
│   ├── prompts/
│   │   ├── __init__.py
│   │   └── coat_analysis.py
│   └── services/
│       ├── __init__.py
│       └── analysis_service.py
├── benchmark/
│   ├── manifests/
│   └── README.md
├── scripts/
│   ├── prepare_oxford_pet_benchmark.py
│   └── evaluate_oxford_pet_segmentation.py
├── .env
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## 1. Criar o ambiente virtual

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Se sua política do PowerShell bloquear o script de ativação, você pode usar temporariamente:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## 2. Instalar dependências

```powershell
pip install -r requirements.txt
```

## 3. Configurar a chave

Abra o arquivo `.env`:

```env
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash-lite
MAX_IMAGE_MB=10
MAX_IMAGE_PIXELS=36000000
VISION_MAX_SIDE=1280
GEMINI_MAX_SIDE=768
JPEG_QUALITY=88
MODEL_CACHE_DIR=.model-cache
DETECTOR_CONFIDENCE=0.6
DETECTOR_FALLBACK_CONFIDENCE=0.40
SECONDARY_CAT_MAX_AREA_RATIO=0.35
GEMINI_MAX_RETRIES=1
GEMINI_MAX_OUTPUT_TOKENS=1536
ANALYSIS_CACHE_DIR=.analysis-cache
LOCAL_SEMANTIC_GATE_ENABLED=true
LOCAL_SOLID_DOMINANCE_THRESHOLD=0.96
LOCAL_SOLID_MIN_DETECTOR_CONFIDENCE=0.85
SAM2_CHECKPOINT=.model-cache/sam2.1_hiera_tiny.pt
SAM2_CONFIG=configs/sam2.1/sam2.1_hiera_t.yaml
VISION_DEVICE=cpu
COLOR_CLUSTER_COUNT=4
COLOR_MIN_COVERAGE=0.03
COLOR_MERGE_DISTANCE=18
COLOR_SHADE_CHROMA_DISTANCE=14
COLOR_BLACK_L_THRESHOLD=45
```

Cole sua chave depois do `=`:

```env
GEMINI_API_KEY=SUA_CHAVE
```

O arquivo `.env` está no `.gitignore` e não deve ser enviado ao GitHub.

## Contas, PostgreSQL e sincronização

As tabelas de conta, sessão, catálogo remoto e tombstones são criadas por
Alembic. Configure no `.env` os valores correspondentes de `.env.example`:
`DATABASE_URL`, `JWT_SECRET`, `OTP_PEPPER`, SMTP, `GOOGLE_WEB_CLIENT_ID` e
`PHOTO_STORAGE_DIR`. Em desenvolvimento, `SMTP_ENABLED=false` escreve o OTP
apenas no log local da API; em produção, ative SMTP e nunca use esse modo.

Depois de instalar as dependências, aplique a migração:

```powershell
alembic upgrade head
```

### PostgreSQL com Docker

Com Docker Desktop aberto, defina `POSTGRES_PASSWORD` no `.env` (e use a mesma
senha em `DATABASE_URL`) e inicie somente o banco:

```powershell
docker compose up -d postgres
alembic upgrade head
```

O volume `gatalogo_postgres_data` mantém os dados entre reinicializações.

### OTP com Mailpit local

O mesmo Compose inclui Mailpit. Com a API executada no Windows, use no `.env`
`SMTP_HOST=127.0.0.1`, `SMTP_PORT=1025`, `SMTP_USE_TLS=false` e
`SMTP_ENABLED=true`. Depois suba o serviço e abra a caixa de entrada local:

```powershell
docker compose up -d mailpit
```

A interface fica em `http://localhost:8025`. Nenhum e-mail é enviado para fora
do computador durante esses testes.

As fotos ficam fora do banco, em `storage/users/<id>/cats/`, e só são servidas
por rotas autenticadas. O diretório está ignorado pelo Git para poder ser
substituído posteriormente por object storage sem alterar o cliente.

### Modelo do SAM 2

Baixe uma vez o checkpoint oficial compacto para o caminho configurado em
`SAM2_CHECKPOINT`. No PowerShell, a partir da raiz do projeto:

```powershell
New-Item -ItemType Directory -Force .model-cache
Invoke-WebRequest `
  https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt `
  -OutFile .model-cache/sam2.1_hiera_tiny.pt
```

O detector Faster R-CNN do Torchvision baixa seus pesos na primeira execução
para `MODEL_CACHE_DIR`. Os dois artefatos ficam fora do Git.

## 4. Rodar

Na raiz do projeto:

```powershell
uvicorn app.main:app --reload
```

Swagger:

```text
http://127.0.0.1:8000/docs
```

Health check:

```text
http://127.0.0.1:8000/health
```

## 5. Testar `/analyze`

No Swagger, abra `POST /analyze`, clique em **Try it out** e envie:

- `cat_id`: por exemplo `teste-001`
- `image`: uma foto de gato

Resposta esperada:

```json
{
  "cat_id": "teste-001",
  "coat_type": "Bicolor",
  "primary_color": "Preto",
  "colors": [
    {
      "name": "Preto",
      "percentage": 68.0
    },
    {
      "name": "Branco",
      "percentage": 32.0
    }
  ],
  "confidence": 0.92,
  "analyzed_at": "2026-08-25T18:00:00Z"
}
```

## Contrato para o Flutter

O endpoint recebe:

```text
POST /analyze
Content-Type: multipart/form-data

cat_id = UUID/local id do gato
image  = arquivo da foto
```

Em uma análise bem-sucedida, retorna:

```json
{
  "cat_id": "string",
  "coat_type": "Sólido | Bicolor | Tricolor | Rajado | Tuxedo | Escaminha | Colorpoint | Outro",
  "primary_color": "Preto | Branco | Cinza | Laranja | Marrom | Creme | Outro",
  "colors": [
    {
      "name": "Preto",
      "percentage": 70
    }
  ],
  "confidence": 0.93,
  "analyzed_at": "ISO-8601"
}
```

## Observação sobre a análise

Antes de chamar o Gemini, o Faster R-CNN identifica a instância principal do gato e o SAM 2 transforma a caixa detectada em uma máscara refinada. Imagens sem gato retornam `NOT_A_CAT`; fotos com dois gatos principais retornam `MULTIPLE_CATS`. O Gemini recebe um recorte do gato, reduzindo a influência do fundo.

Os percentuais passam a ser calculados localmente apenas nos pixels da máscara do gato. O Gemini recebe os clusters medidos, associa cada um a uma categoria semântica e interpreta o padrão espacial. O agrupamento também sinaliza clusters de cromia semelhante que diferem sobretudo por luz ou sombra, reduzindo a tendência de chamar uma pelagem branca sombreada de cinza ou creme. Os percentuais representam a **pelagem visível na fotografia**, não uma reconstrução completa do corpo.

O próximo passo do projeto Flutter é substituir o `MockAnalysisService` por uma chamada HTTP para este endpoint.

## Benchmark público da segmentação

O projeto inclui uma seleção reprodutível do **Oxford-IIIT Pet Dataset** para
medir o detector aberto + SAM 2 contra máscaras anotadas. As instruções de
download, licença, criação do manifesto e execução da avaliação estão em
[`benchmark/README.md`](benchmark/README.md).
# Reconhecimento visual e alertas

O catálogo continua sendo servido pela API normal. O reconhecimento avançado é
assíncrono: cada foto principal ou de referência cria um registro em
`vision_jobs`, consumido pelo serviço `vision-worker` no Docker Compose.

Antes de subir esse serviço, é necessário:

1. Baixar o checkpoint público `sam2.1_hiera_tiny.pt` no caminho de
   `SAM2_CHECKPOINT`.
2. Construir os serviços e aplicar a migração Alembic `20260828_03`.
3. O aplicativo usa OpenStreetMap para escolher o ponto de busca; mantenha a
   atribuição visível e não faça pré-carregamento de áreas para uso offline.

Sem o worker, fotos continuam salvas e sincronizadas; os trabalhos de visão
ficam pendentes até o worker ficar disponível.

## Comparação atual de fotografias

A versão v3 acrescenta patches densos do DINOv2, extraídos na mesma passagem
do vetor global e selecionados dentro da máscara. `MATCH_FINE_PAIR_LIMIT=6`
limita a verificação fina aos pares mais promissores de cada candidato.
`MATCH_SHADOW_MODE=true` é o padrão: registra sugestões e decisões hipotéticas
sem gerar novas notificações automáticas de correspondência. Isso permite
avaliar os sinais antes de ativar notificações em produção. `probability`
permanece nulo enquanto não houver calibração validada.

Reenfileire as fotos após atualizar o extrator, conforme as instruções abaixo.
A [avaliação da especificação](benchmark/recognition-spec-review.md) explica
as propostas aproveitadas, as correções e o que depende de dados confirmados.

O reconhecimento usa detector + SAM 2, DINOv2 e evidência local RootSIFT com
verificação geométrica. O recorte preserva o corpo inteiro por preenchimento
lateral; orientação EXIF, tamanho e pixels são validados antes da inferência.
A qualidade e os detalhes são medidos dentro da máscara, excluindo o cenário.

A similaridade é um índice de busca, **não uma probabilidade de identidade**.
Um único par parecido fica restrito. Fotos repetidas ou quase duplicadas não
contam como novas observações. Pelagem sólida ou desconhecida permanece
inconclusiva mesmo com similaridade alta: o modelo atual não foi treinado nem
calibrado para identificar indivíduos por anatomia facial. Detalhes locais
podem corroborar marcas visíveis; sua ausência não exclui o mesmo gato.

Nenhuma planilha, CSV ou JSON antigo serve como verdade de referência. O modo
antigo `--own-cats-csv` foi desativado porque grupos de captura não comprovam
identidade. Não há taxa de acerto validada neste conjunto.

### Testar diretamente duas fotos, sem banco ou Gemini

```powershell
python scripts/compare_cats.py --query caminho/foto-a.jpg --reference caminho/foto-b.jpg --output outputs/comparacao.md
```

É possível repetir `--query` e `--reference` para várias fotos de cada lado;
as referências devem ser do mesmo candidato. Para comparar uma seleção sem
assumir que as fotos pertencem ao mesmo gato:

```powershell
python scripts/inspect_photo_gallery.py --photos caminho/a.jpg caminho/b.jpg caminho/c.jpg --output outputs/inspecao.md
```

Os comandos extraem evidências novas diretamente das imagens. Pesos oficiais
do DINOv2 são baixados na primeira execução; as fotos são processadas localmente.

### Atualizar o reconhecimento persistido

Com o worker parado, aplique a migração e enfileire as fotos atuais:

```powershell
alembic upgrade head
python scripts/reindex_recognition.py --apply
python -m app.services.vision_worker
```

A migração `20260908_04` guarda versão do extrator, hash da foto e evidências.
Vetores legados ficam inelegíveis até o reprocessamento. Não misture workers
antigos e novos. O comando sem `--apply` mostra apenas a quantidade de fotos.

Cada cadastro com latitude/longitude válidas registra a observação. Um novo
alerta procura observações existentes, e o término de qualquer foto refaz a
busca nos dois sentidos, incluindo fotos do próprio gato desaparecido. O raio
é obrigatório antes da comparação. Sem coordenadas, não há busca regional.
Fotos removidas, substituídas e gatos excluídos não geram novas sugestões.
Resultados pendentes podem ficar `stale` quando sua evidência deixa de existir.
O campo `evidence` de `/vision/matches` informa os motivos de incerteza.

### Verificações

```powershell
python -m pytest -q
```

Os testes de banco exigem `RECOGNITION_TEST_DATABASE_URL` apontando para um
PostgreSQL **descartável** chamado `recognition_test`, com migrações aplicadas.
Não configure essa variável para o catálogo de usuários.

Os testes verificam funcionamento e regressões. Para medir reidentificação,
ainda precisamos de fotos com identidade confirmada, incluindo gatos diferentes
de mesma cor, fotos em dias/ambientes distintos e indivíduos ausentes da galeria.
Treinar ou calibrar a partir de identidades inferidas pelo próprio modelo
produziria uma avaliação circular.

Referência do extrator: [DINOv2, implementação oficial](https://github.com/facebookresearch/dinov2).
