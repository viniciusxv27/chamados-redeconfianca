#!/bin/bash

# Script de deploy para EasyPanel/Nixpacks
echo "🚀 Iniciando deploy..."

# Instalar dependências
echo "📦 Instalando dependências..."
pip install -r requirements.txt

# Aplicar migrações
echo "🗄️ Aplicando migrações do banco..."
python manage.py migrate --noinput

# Coletar arquivos estáticos
echo "📁 Coletando arquivos estáticos..."
python manage.py collectstatic --noinput

# Iniciar servidor
echo "🌐 Iniciando servidor..."
exec gunicorn redeconfianca.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-3}
