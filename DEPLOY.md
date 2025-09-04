# 🚀 Guia de Deploy para Produção

## 📋 Configuração PostgreSQL e EasyPanel via Nixpacks

### ⚙️ Configuração Atual
- ✅ PostgreSQL configurado
- ✅ Gunicorn para produção
- ✅ WhiteNoise para arquivos estáticos
- ✅ Configurações de segurança para HTTPS
- ✅ Nixpacks configurado
- ✅ Variáveis de ambiente organizadas

## 🌐 Deploy no EasyPanel via Nixpacks

### 1. Preparar o Repositório
```bash
# Confirmar todas as alterações
git add .
git commit -m "Configure PostgreSQL and Nixpacks deployment"
git push origin main
```

### 2. Configuração no EasyPanel

#### Variáveis de Ambiente Necessárias:
```env
# Obrigatórias
DATABASE_URL=postgresql://[gerado automaticamente pelo EasyPanel]
SECRET_KEY=your-super-secure-secret-key-here
DEBUG=False
ALLOWED_HOSTS=seu-dominio.easypanel.app

# Opcionais para produção segura
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=31536000
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
```

#### Passos no EasyPanel:
1. Crie um novo projeto
2. Conecte ao repositório GitHub
3. EasyPanel detectará automaticamente o `nixpacks.toml`
4. Configure as variáveis de ambiente
5. O PostgreSQL será provisionado automaticamente

### 3. Arquivos de Configuração Criados

#### `nixpacks.toml`
```toml
[phases.build]
dependsOn = ["install"]
cmds = [
  "python manage.py collectstatic --noinput",
  "python manage.py migrate --noinput"
]

[phases.install]
cmds = ["pip install -r requirements.txt"]

[start]
cmd = "gunicorn redeconfianca.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --timeout 120"

[variables]
PORT = "8000"
PYTHON_VERSION = "3.11"

[providers]
postgres = true
```

#### `Procfile` (backup)
```
web: gunicorn redeconfianca.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --timeout 120
release: python manage.py migrate --noinput && python manage.py collectstatic --noinput
```

### 4. Dependências Atualizadas (`requirements.txt`)
```
# Principais adições:
psycopg2-binary==2.9.9    # PostgreSQL driver
dj-database-url==2.1.0    # Database URL parsing
gunicorn==21.2.0          # Production server
whitenoise==6.6.0         # Static files handling
```

### 5. Desenvolvimento Local com PostgreSQL

#### Instalar PostgreSQL localmente:
```bash
# macOS
brew install postgresql
brew services start postgresql

# Ubuntu/Debian
sudo apt install postgresql postgresql-contrib
sudo systemctl start postgresql
```

#### Configurar banco local:
```bash
# Conectar ao PostgreSQL
psql postgres

# Criar banco e usuário
CREATE DATABASE redeconfianca_db;
CREATE USER redeconfianca_user WITH PASSWORD 'sua_senha';
GRANT ALL PRIVILEGES ON DATABASE redeconfianca_db TO redeconfianca_user;
\q
```

#### Variáveis de ambiente local (`.env`):
```env
DATABASE_URL=postgresql://redeconfianca_user:sua_senha@localhost:5432/redeconfianca_db
DEBUG=True
SECRET_KEY=seu-secret-key-desenvolvimento
ALLOWED_HOSTS=localhost,127.0.0.1
```

### 6. Comandos para Desenvolvimento

```bash
# Instalar dependências atualizadas
pip install -r requirements.txt

# Aplicar migrações
python manage.py migrate

# Criar superusuário
python manage.py createsuperuser

# Executar localmente
python manage.py runserver

# Coletar arquivos estáticos
python manage.py collectstatic
```

### 7. Pós-Deploy no EasyPanel

Após o deploy bem-sucedido:

1. **Verificar logs** no painel do EasyPanel
2. **Executar comandos via console** (se disponível):
   ```bash
   python manage.py createsuperuser
   ```
3. **Testar funcionalidades** principais
4. **Configurar domínio customizado** (se necessário)

### 8. Monitoramento e Manutenção

#### Logs importantes:
- Deploy logs no EasyPanel
- Application logs via Gunicorn
- PostgreSQL connection logs

#### Backup do banco:
```bash
# Via pg_dump (se tiver acesso)
pg_dump DATABASE_URL > backup.sql
```

### 9. Troubleshooting Comum

#### Erro de migração:
- Verificar se DATABASE_URL está correta
- Verificar conectividade com PostgreSQL

#### Arquivos estáticos não carregando:
- Verificar se `collectstatic` executou corretamente
- Confirmar configuração do WhiteNoise

#### Erro 500:
- Verificar SECRET_KEY
- Verificar ALLOWED_HOSTS
- Revisar logs de aplicação

---

## 📋 Checklist de Deploy

- [ ] ✅ PostgreSQL configurado
- [ ] ✅ Nixpacks configurado
- [ ] ✅ Gunicorn configurado
- [ ] ✅ WhiteNoise configurado
- [ ] ✅ Variáveis de ambiente definidas
- [ ] ✅ Migrações funcionando
- [ ] ✅ Arquivos estáticos coletados
- [ ] 🔄 Deploy no EasyPanel
- [ ] 🔄 Testes pós-deploy
- [ ] 🔄 Configuração de domínio
- [ ] 🔄 Monitoramento ativo

*Guia atualizado para PostgreSQL + EasyPanel - v2.0*

## 🐧 Deploy com Ubuntu/Linux

### 1. Preparar Servidor
```bash
# Atualizar sistema
sudo apt update && sudo apt upgrade -y

# Instalar dependências
sudo apt install python3 python3-venv python3-pip mysql-server nginx git -y

# Configurar MySQL
sudo mysql_secure_installation
```

### 2. Configurar Aplicação
```bash
# Clonar repositório
cd /var/www/
sudo git clone https://github.com/seu-usuario/chamados-rede-confianca.git
sudo chown -R $USER:$USER chamados-rede-confianca/
cd chamados-rede-confianca/

# Criar ambiente virtual
python3 -m venv .venv
source .venv/bin/activate

# Instalar dependências
pip install -r requirements.txt
pip install gunicorn

# Configurar banco
mysql -u root -p
CREATE DATABASE redeconfianca_production CHARACTER SET utf8mb4;
CREATE USER 'redeconfianca_user'@'localhost' IDENTIFIED BY 'senha-segura';
GRANT ALL PRIVILEGES ON redeconfianca_production.* TO 'redeconfianca_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;

# Executar migrações
python manage.py migrate
python manage.py collectstatic

# Criar superusuário
python manage.py createsuperuser
```

### 3. Configurar Gunicorn
```bash
# Criar arquivo de serviço
sudo nano /etc/systemd/system/redeconfianca.service
```

Conteúdo do arquivo:
```ini
[Unit]
Description=Rede Confiança Django App
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/chamados-rede-confianca
Environment="PATH=/var/www/chamados-rede-confianca/.venv/bin"
ExecStart=/var/www/chamados-rede-confianca/.venv/bin/gunicorn --workers 3 --bind unix:/var/www/chamados-rede-confianca/redeconfianca.sock redeconfianca.wsgi:application

[Install]
WantedBy=multi-user.target
```

```bash
# Iniciar serviço
sudo systemctl start redeconfianca
sudo systemctl enable redeconfianca
```

### 4. Configurar Nginx
```bash
sudo nano /etc/nginx/sites-available/redeconfianca
```

Conteúdo do arquivo:
```nginx
server {
    listen 80;
    server_name seudominio.com www.seudominio.com;

    location = /favicon.ico { access_log off; log_not_found off; }
    
    location /static/ {
        root /var/www/chamados-rede-confianca;
    }

    location / {
        include proxy_params;
        proxy_pass http://unix:/var/www/chamados-rede-confianca/redeconfianca.sock;
    }
}
```

```bash
# Ativar site
sudo ln -s /etc/nginx/sites-available/redeconfianca /etc/nginx/sites-enabled
sudo nginx -t
sudo systemctl restart nginx
```

### 5. Configurar SSL (Let's Encrypt)
```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d seudominio.com -d www.seudominio.com
```

## 🐳 Deploy com Docker

### Dockerfile
```dockerfile
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "redeconfianca.wsgi:application"]
```

### docker-compose.yml
```yaml
version: '3.8'

services:
  web:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - .:/app
    environment:
      - DEBUG=False
    depends_on:
      - db

  db:
    image: mysql:8.0
    environment:
      MYSQL_DATABASE: redeconfianca_production
      MYSQL_USER: redeconfianca_user
      MYSQL_PASSWORD: senha-segura
      MYSQL_ROOT_PASSWORD: root-senha
    volumes:
      - mysql_data:/var/lib/mysql

volumes:
  mysql_data:
```

```bash
# Build e iniciar
docker-compose build
docker-compose up -d

# Executar migrações
docker-compose exec web python manage.py migrate
docker-compose exec web python manage.py collectstatic
```

## ☁️ Deploy na AWS/Digital Ocean

### Usando AWS EC2:
1. Criar instância EC2 (Ubuntu 20.04+)
2. Configurar Security Groups (HTTP, HTTPS, SSH)
3. Seguir passos do deploy Linux
4. Configurar RDS para MySQL
5. Usar S3 para arquivos estáticos

### Usando Digital Ocean Droplet:
1. Criar Droplet Ubuntu
2. Configurar firewall
3. Seguir deploy Linux
4. Usar Managed Database
5. Configurar CDN para estáticos

## 📊 Monitoramento

### Logs
```bash
# Ver logs do Gunicorn
sudo journalctl -u redeconfianca

# Ver logs do Nginx
sudo tail -f /var/log/nginx/error.log
```

### Backup Automático
```bash
# Script de backup
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M%S)
mysqldump -u redeconfianca_user -p redeconfianca_production > backup_$DATE.sql
```

## 🔧 Manutenção

### Atualizações
```bash
# Backup antes de atualizar
mysqldump -u user -p database > backup_pre_update.sql

# Atualizar código
git pull origin main

# Instalar novas dependências
pip install -r requirements.txt

# Executar migrações
python manage.py migrate

# Coletar estáticos
python manage.py collectstatic

# Reiniciar serviços
sudo systemctl restart redeconfianca
sudo systemctl restart nginx
```

### Performance
- Configurar cache Redis
- Otimizar queries do banco
- Comprimir arquivos estáticos
- Configurar CDN

---

*Guia de Deploy v1.0 - Rede Confiança*
