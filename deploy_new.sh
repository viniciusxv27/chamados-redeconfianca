#!/bin/bash
set -e  # Exit on any error

# Script de deploy para Docker/EasyPanel
echo "🚀 Iniciando deploy do Rede Confiança..."

# Verificar se as variáveis essenciais estão definidas
if [ -z "$DATABASE_URL" ]; then
    echo "⚠️ DATABASE_URL não definida, usando SQLite como fallback"
    export DATABASE_URL="sqlite:///db.sqlite3"
fi

# Criar diretórios necessários
echo "📁 Criando diretórios necessários..."
mkdir -p staticfiles media logs
touch staticfiles/.keep

# Verificar conectividade com PostgreSQL (se aplicável)
if [[ $DATABASE_URL == postgresql* ]]; then
    echo "🔍 Verificando conectividade PostgreSQL..."
    for i in {1..30}; do
        if python -c "
import os
import psycopg2
from urllib.parse import urlparse
url = urlparse(os.environ['DATABASE_URL'])
try:
    conn = psycopg2.connect(
        host=url.hostname,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        database=url.path[1:] if url.path else '',
        connect_timeout=5
    )
    conn.close()
    print('✅ PostgreSQL conectado com sucesso!')
    exit(0)
except Exception as e:
    print(f'Tentativa {i}/30 - Erro: {str(e)}')
    exit(1)
" 2>/dev/null; then
            break
        fi
        echo "⏳ Aguardando PostgreSQL... ($i/30)"
        sleep 2
    done
fi

# Aplicar migrações
echo "🗄️ Aplicando migrações do banco de dados..."
python manage.py migrate --noinput || {
    echo "❌ Erro ao aplicar migrações!"
    echo "Detalhes do erro:"
    python manage.py migrate --verbosity=2 --noinput || true
    exit 1
}

# Coletar arquivos estáticos
echo "📁 Coletando arquivos estáticos..."
python manage.py collectstatic --noinput --clear || {
    echo "❌ Erro ao coletar arquivos estáticos!"
    echo "Verificando diretórios..."
    ls -la staticfiles/ || true
    exit 1
}

# Verificar configuração inicial
echo "🔍 Verificando configuração inicial..."
python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
count = User.objects.filter(is_superuser=True).count()
total_users = User.objects.count()
print(f'👥 Total de usuários: {total_users}')
print(f'👑 Superusuários: {count}')
if count == 0:
    print('⚠️ ATENÇÃO: Nenhum superusuário encontrado!')
    print('💡 Execute: python manage.py createsuperuser')
" 2>/dev/null || echo "⚠️ Não foi possível verificar usuários"

# Verificar se staticfiles foi criado corretamente
if [ ! -d "staticfiles" ] || [ -z "$(ls -A staticfiles)" ]; then
    echo "❌ Erro: Diretório staticfiles vazio ou inexistente!"
    echo "Criando estrutura mínima..."
    mkdir -p staticfiles
    touch staticfiles/.keep
fi

echo "✅ Deploy configurado com sucesso!"
echo "🌐 Iniciando servidor Gunicorn..."
echo "📊 Configurações:"
echo "   - Porta: ${PORT:-8000}"
echo "   - Workers: ${WEB_CONCURRENCY:-3} x ${WEB_THREADS:-8} threads"
echo "   - Timeout: ${WEB_TIMEOUT:-300}s"
echo "   - Database: ${DATABASE_URL:0:20}..."

# Iniciar servidor com logs detalhados
# gthread (25/11/2026): com 3 workers "sync", cada requisição lenta (ZIP do Drive de
# 30–50 s, envio de e-mail, API do ponto, planilha da comissão, leitura do SAP) prendia
# um worker inteiro; três delas ao mesmo tempo e o portal parava para todo mundo.
# Com threads, cada worker atende WEB_THREADS requisições ao mesmo tempo — a lenta
# segura só uma thread. O timeout no gthread vale para o worker travado, não para a
# requisição: upload grande (vídeo de entrevista) continua subindo enquanto o worker
# responde ao arbiter.
exec gunicorn redeconfianca.wsgi:application \
    --bind 0.0.0.0:${PORT:-8000} \
    --workers ${WEB_CONCURRENCY:-3} \
    --worker-class gthread \
    --threads ${WEB_THREADS:-8} \
    --timeout ${WEB_TIMEOUT:-300} \
    --graceful-timeout 60 \
    --keep-alive 5 \
    --max-requests 2000 \
    --max-requests-jitter 200 \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    --capture-output
