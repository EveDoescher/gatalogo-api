#!/bin/bash
set -e

# Função para finalização graciosa
cleanup() {
    echo "[Gatálogo] Encerrando serviços..."
    if [ ! -z "$WORKER_PID" ]; then
        kill -TERM "$WORKER_PID" 2>/dev/null || true
    fi
    if [ ! -z "$API_PID" ]; then
        kill -TERM "$API_PID" 2>/dev/null || true
    fi
    wait
    exit 0
}

trap cleanup SIGTERM SIGINT

PORT="${PORT:-8000}"
ROLE="${SERVICE_ROLE:-all}"

echo "=========================================="
echo " Início do Gatálogo AI Container"
echo " Modo: $ROLE | Porta: $PORT"
echo "=========================================="

# Executar migrações do Alembic se configurado
if [ "${RUN_MIGRATIONS:-true}" = "true" ] && [ ! -z "$DATABASE_URL" ]; then
    echo "[Gatálogo] Aplicando migrações do banco de dados (Alembic)..."
    alembic upgrade head || {
        echo "[Aviso] Falha ao rodar migrações do Alembic. Continuando inicialização..."
    }
fi

if [ "$ROLE" = "worker" ]; then
    echo "[Gatálogo] Iniciando somente o Vision Worker (SAM 2 + DINOv2)..."
    exec python -m app.services.vision_worker

elif [ "$ROLE" = "api" ] || [ "$ROLE" = "web" ]; then
    echo "[Gatálogo] Iniciando somente a API FastAPI na porta $PORT..."
    exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"

elif [ "$ROLE" = "all" ]; then
    echo "[Gatálogo] Modo completo: iniciando Vision Worker em segundo plano..."
    python -m app.services.vision_worker &
    WORKER_PID=$!

    echo "[Gatálogo] Modo completo: iniciando API FastAPI na porta $PORT..."
    uvicorn app.main:app --host 0.0.0.0 --port "$PORT" &
    API_PID=$!

    # Aguardar qualquer um dos processos terminar
    wait -n "$WORKER_PID" "$API_PID"
    cleanup
else
    # Executa comando customizado passado via CMD
    exec "$@"
fi