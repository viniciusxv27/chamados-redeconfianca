# 🚀 Guia de Deploy para Produção

## 📋 Checklist Pré-Deploy

### ⚙️ Configurações
- [ ] Configurar variáveis de ambiente de produção
- [ ] Configurar banco MySQL em produção
- [ ] Configurar servidor de email (SMTP)
- [ ] Configurar webhooks do Discord
- [ ] Gerar nova SECRET_KEY
- [ ] Configurar ALLOWED_HOSTS
- [ ] Configurar DEBUG=False

### 🔒 Segurança
- [ ] Configurar HTTPS
- [ ] Configurar CSRF_COOKIE_SECURE=True
- [ ] Configurar SESSION_COOKIE_SECURE=True
- [ ] Configurar SECURE_SSL_REDIRECT=True
- [ ] Backup do banco de dados

### 📁 Arquivos Estáticos
- [ ] Configurar STATIC_ROOT
- [ ] Executar collectstatic
- [ ] Configurar servidor web (Nginx/Apache)

## 🌐 Variáveis de Ambiente para Produção

```env
# Django Settings
SECRET_KEY=sua-chave-super-secreta-aqui
DEBUG=False
ALLOWED_HOSTS=seudominio.com,www.seudominio.com

# Database (MySQL)
DB_NAME=redeconfianca_production
DB_USER=redeconfianca_user
DB_PASSWORD=senha-super-segura
DB_HOST=localhost
DB_PORT=3306

# Email (Produção)
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.empresa.com
EMAIL_PORT=587
EMAIL_HOST_USER=sistema@redeconfianca.com
EMAIL_HOST_PASSWORD=senha-email
EMAIL_USE_TLS=True

# Webhook (Produção)
WEBHOOK_URL=https://discord.com/api/webhooks/seu-webhook-producao

# Security Settings
CSRF_COOKIE_SECURE=True
SESSION_COOKIE_SECURE=True
SECURE_SSL_REDIRECT=True
SECURE_BROWSER_XSS_FILTER=True
SECURE_CONTENT_TYPE_NOSNIFF=True
```

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
