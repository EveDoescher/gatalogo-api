# Imagem base oficial do Python
FROM python:3.12-slim

# Configurações de ambiente do Python e Gatálogo
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    SERVICE_ROLE=all \
    MODEL_CACHE_DIR=/service/.model-cache \
    ANALYSIS_CACHE_DIR=/service/.analysis-cache \
    PHOTO_STORAGE_DIR=/service/storage \
    SAM2_CHECKPOINT=/service/.model-cache/sam2.1_hiera_tiny.pt \
    SAM2_CONFIG=configs/sam2.1/sam2.1_hiera_t.yaml \
    VISION_DEVICE=cpu

# Dependências do sistema (Git para SAM-2, bibliotecas C para OpenCV e curl)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /service

# 1. Copiar requirements e instalar PyTorch CPU + dependências
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install --no-cache-dir -r requirements.txt

# 2. Criar diretórios de cache e armazenamento
RUN mkdir -p /service/.model-cache /service/.analysis-cache /service/storage

# 3. Baixar previamente o checkpoint público do SAM 2 para acelerar o boot na nuvem
RUN curl -L -o /service/.model-cache/sam2.1_hiera_tiny.pt \
    https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt

# 4. Copiar o código-fonte, migrações e scripts
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts
COPY entrypoint.sh ./

# Garantir permissão de execução no entrypoint
RUN chmod +x /service/entrypoint.sh

# Porta exposta para a API FastAPI
EXPOSE 8000

# Script de inicialização que gerencia a API FastAPI e o Vision Worker
ENTRYPOINT ["/service/entrypoint.sh"]
CMD ["all"]