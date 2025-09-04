#!/bin/bash

# Script de inicialização para desenvolvimento local
# Execute: chmod +x setup_local.sh && ./setup_local.sh

echo "🚀 Configurando ambiente de desenvolvimento local..."

# Verificar se o Python está instalado
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 não encontrado. Instale o Python 3 primeiro."
    exit 1
fi

# Criar ambiente virtual se não existir
if [ ! -d "venv" ]; then
    echo "📦 Criando ambiente virtual..."
    python3 -m venv venv
fi

# Ativar ambiente virtual
echo "🔧 Ativando ambiente virtual..."
source venv/bin/activate

# Instalar dependências
echo "📥 Instalando dependências..."
pip install -r requirements.txt

# Verificar se existe arquivo .env
if [ ! -f ".env" ]; then
    echo "📝 Criando arquivo .env..."
    cat > .env << EOL
# Configurações de desenvolvimento local
DEBUG=True
SECRET_KEY=django-insecure-development-key-change-in-production
ALLOWED_HOSTS=localhost,127.0.0.1

# Banco SQLite para desenvolvimento (padrão)
DATABASE_URL=sqlite:///db.sqlite3

# Para usar PostgreSQL local, descomente e configure:
# DATABASE_URL=postgresql://user:password@localhost:5432/redeconfianca_db
EOL
    echo "✅ Arquivo .env criado com configurações padrão"
else
    echo "📄 Arquivo .env já existe"
fi

# Executar migrações
echo "🗃️ Aplicando migrações do banco..."
python manage.py migrate

# Coletar arquivos estáticos
echo "📁 Coletando arquivos estáticos..."
python manage.py collectstatic --noinput

# Verificar se há superusuário
echo "👤 Verificando superusuário..."
python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(is_superuser=True).exists():
    print('Nenhum superusuário encontrado.')
    import sys
    sys.exit(1)
" 2>/dev/null

if [ $? -ne 0 ]; then
    echo "🔐 Criando superusuário..."
    echo "Por favor, crie um superusuário para acessar o admin:"
    python manage.py createsuperuser
fi

echo ""
echo "✅ Configuração completa!"
echo ""
echo "🎯 Para iniciar o servidor de desenvolvimento:"
echo "   source venv/bin/activate"
echo "   python manage.py runserver"
echo ""
echo "🌐 Acesse: http://localhost:8000"
echo "🔧 Admin: http://localhost:8000/admin"
echo ""
echo "📘 Para deploy no EasyPanel:"
echo "   1. Faça push para o GitHub"
echo "   2. Configure no EasyPanel com as variáveis:"
echo "      - DATABASE_URL (auto-gerado)"
echo "      - SECRET_KEY (gere uma nova)"
echo "      - DEBUG=False"
echo "      - ALLOWED_HOSTS=seu-dominio.easypanel.app"
echo ""
