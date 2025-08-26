# 🎫 Sistema de Chamados - Rede Confiança

![Python](https://img.shields.io/badge/Python-3.13.1-blue.svg)
![Django](https://img.shields.io/badge/Django-5.2.5-green.svg)
![MySQL](https://img.shields.io/badge/MySQL-8.0+-orange.svg)
![Tailwind](https://img.shields.io/badge/Tailwind-CSS-blue.svg)

Sistema completo de gerenciamento de chamados para a empresa **Rede Confiança**, desenvolvido com Django e MySQL, featuring sistema hierárquico de usuários, workflow de aprovação e sistema de créditos C$.

## 📋 Índice

- [Características](#-características)
- [Tecnologias](#-tecnologias)
- [Pré-requisitos](#-pré-requisitos)
- [Instalação](#-instalação)
- [Configuração](#-configuração)
- [Uso](#-uso)
- [Estrutura do Projeto](#-estrutura-do-projeto)
- [API Endpoints](#-api-endpoints)
- [Usuários de Teste](#-usuários-de-teste)
- [Contribuição](#-contribuição)

## ✨ Características

### 🏢 **Sistema Hierárquico**
- **SUPERADMIN**: Controle total do sistema
- **ADMINISTRATIVO**: Gestão de usuários e configurações
- **SUPERVISOR**: Supervisão de equipes e aprovações
- **PADRÃO**: Usuários finais

### 🎫 **Gestão de Chamados**
- Criação e acompanhamento de tickets
- Workflow de aprovação automático
- Categorização por setores
- Sistema de comentários e logs
- Notificações via webhook (Discord)

### 💰 **Sistema C$ (Créditos)**
- Economia virtual interna
- Sistema de premiações
- Histórico de transações
- Resgate de prêmios

### 📱 **Interface Moderna**
- Design responsivo com Tailwind CSS
- Fonte Montserrat
- Tema escuro/claro
- Animações suaves
- Icons Font Awesome

### 🔐 **Segurança**
- Autenticação personalizada
- Controle de permissões por hierarquia
- Middleware de auditoria
- Logs de atividades

## 🛠 Tecnologias

### Backend
- **Django 5.2.5** - Framework web
- **Django REST Framework** - API REST
- **MySQL 8.0+** - Banco de dados principal
- **Python 3.13.1** - Linguagem de programação

### Frontend
- **Tailwind CSS** - Framework CSS
- **Font Awesome 6** - Ícones
- **JavaScript Vanilla** - Interatividade
- **Google Fonts (Montserrat)** - Tipografia

### Integrações
- **Discord Webhooks** - Notificações
- **Python Decouple** - Variáveis de ambiente

## 📋 Pré-requisitos

- **Python 3.13.1+**
- **MySQL 8.0+** (ou SQLite para desenvolvimento)
- **Git**
- **Node.js** (opcional, para build do Tailwind)

## 🚀 Instalação

### 1. Clone o repositório
```bash
git clone https://github.com/seu-usuario/chamados-rede-confianca.git
cd chamados-rede-confianca
```

### 2. Crie um ambiente virtual
```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux/Mac
source .venv/bin/activate
```

### 3. Instale as dependências
```bash
pip install -r requirements.txt
```

### 4. Configure as variáveis de ambiente
Crie um arquivo `.env` na raiz do projeto:

```env
# Django Settings
SECRET_KEY=django-insecure-your-secret-key-here
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost

# Database Settings (MySQL)
DB_NAME=redeconfianca_db
DB_USER=root
DB_PASSWORD=sua_senha
DB_HOST=localhost
DB_PORT=3306

# Email Settings
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=seu-email@gmail.com
EMAIL_HOST_PASSWORD=sua-senha-app
EMAIL_USE_TLS=True

# Webhook Settings
WEBHOOK_URL=https://discord.com/api/webhooks/seu-webhook
```

### 5. Configure o banco de dados

#### Para MySQL:
```bash
# Crie o banco de dados no MySQL
mysql -u root -p
CREATE DATABASE redeconfianca_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
EXIT;
```

#### Para desenvolvimento (SQLite):
```bash
# Deixe comentadas as linhas do MySQL no settings.py
# O SQLite será usado automaticamente
```

### 6. Execute as migrações
```bash
python manage.py makemigrations
python manage.py migrate
```

### 7. Crie dados iniciais
```bash
python manage.py shell
```

Execute o script Python para criar dados iniciais:
```python
from users.models import User, Sector
from tickets.models import Category
from prizes.models import Prize

# Criar setores
setores = [
    "Recursos Humanos", "Tecnologia da Informação", 
    "Financeiro", "Comercial", "Operacional", "Diretoria"
]
for nome in setores:
    Sector.objects.get_or_create(name=nome)

# Criar usuários de teste
User.objects.create_user(
    email='admin@redeconfianca.com',
    password='redeconfianca123',
    first_name='Administrador',
    last_name='Sistema',
    hierarchy='SUPERADMIN',
    sector=Sector.objects.get(name='Diretoria'),
    balance_cs=50000
)

User.objects.create_user(
    email='leo.teixeira@redeconfianca.com',
    password='redeconfianca123',
    first_name='Léo',
    last_name='Teixeira',
    hierarchy='ADMINISTRATIVO',
    sector=Sector.objects.get(name='Tecnologia da Informação'),
    balance_cs=20000
)

# Criar categorias
categorias = [
    ("Suporte Técnico", "ti"), ("Recursos Humanos", "rh"),
    ("Financeiro", "financeiro"), ("Infraestrutura", "infraestrutura")
]
for nome, setor in categorias:
    sector_obj = Sector.objects.get(name__icontains=setor.split('_')[0])
    Category.objects.get_or_create(name=nome, sector=sector_obj)

print("Dados iniciais criados com sucesso!")
exit()
```

### 8. Inicie o servidor
```bash
python manage.py runserver
```

Acesse: [http://127.0.0.1:8000](http://127.0.0.1:8000)

## ⚙️ Configuração

### Configuração do Discord Webhook
1. No seu servidor Discord, vá em **Configurações do Canal**
2. Clique em **Integrações** → **Webhooks**
3. Crie um novo webhook e copie a URL
4. Adicione a URL no arquivo `.env`

### Configuração de Email
Para emails em produção, configure um provedor SMTP:
```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=seu-email@empresa.com
EMAIL_HOST_PASSWORD=sua-senha-app
EMAIL_USE_TLS=True
```

## 📱 Uso

### 🔐 **Login no Sistema**
- Acesse `/login/`
- Use as credenciais dos usuários de teste
- Ou crie novos usuários via painel admin

### 🎫 **Criando Chamados**
1. Faça login no sistema
2. Navegue para **Chamados** → **Abrir Chamado**
3. Preencha as informações:
   - Título
   - Descrição
   - Categoria
   - Prioridade
4. Envie o chamado

### 👥 **Gestão de Usuários** (Admin/Superadmin)
1. Acesse **Painel Gestão**
2. Gerencie usuários, setores e categorias
3. Configure permissões e hierarquias

### 💰 **Sistema C$**
- Visualize seu saldo no menu lateral
- Resgate prêmios na seção **Prêmios**
- Acompanhe transações no histórico

## 📁 Estrutura do Projeto

```
chamados/
├── 📁 redeconfianca/          # Configurações principais
│   ├── settings.py            # Configurações Django
│   ├── urls.py               # URLs principais
│   └── wsgi.py               # WSGI config
├── 📁 users/                 # App de usuários
│   ├── models.py             # User, Sector models
│   ├── views.py              # Views de autenticação
│   └── admin.py              # Admin interface
├── 📁 tickets/               # App de chamados
│   ├── models.py             # Ticket, Category, Comment
│   ├── views.py              # CRUD de tickets
│   ├── api.py                # API endpoints
│   └── webhooks.py           # Integrações Discord
├── 📁 communications/        # App de comunicações
│   ├── models.py             # Message, Notification
│   └── views.py              # Sistema de mensagens
├── 📁 prizes/                # App de prêmios
│   ├── models.py             # Prize, Transaction
│   └── views.py              # Sistema C$
├── 📁 core/                  # App principal
│   ├── middleware.py         # Middleware customizado
│   └── utils.py              # Utilitários
├── 📁 templates/             # Templates HTML
│   ├── base.html             # Template base
│   ├── 📁 users/             # Templates de usuários
│   ├── 📁 tickets/           # Templates de tickets
│   └── 📁 admin/             # Templates admin
├── 📁 static/                # Arquivos estáticos
│   ├── 📁 css/               # Estilos customizados
│   ├── 📁 js/                # JavaScript
│   └── 📁 images/            # Imagens e logos
├── 📄 requirements.txt       # Dependências Python
├── 📄 .env                   # Variáveis de ambiente
└── 📄 manage.py              # Django management
```

## 🌐 API Endpoints

### Autenticação
- `POST /api/auth/login/` - Login
- `POST /api/auth/logout/` - Logout

### Tickets
- `GET /api/tickets/` - Listar tickets
- `POST /api/tickets/` - Criar ticket
- `GET /api/tickets/{id}/` - Detalhe do ticket
- `PUT /api/tickets/{id}/` - Atualizar ticket
- `POST /api/tickets/{id}/comment/` - Adicionar comentário

### Categorias
- `GET /api/categories/` - Listar categorias
- `GET /api/categories-by-sector/` - Categorias por setor

### Usuários
- `GET /api/users/` - Listar usuários
- `GET /api/users/me/` - Perfil do usuário

## 👥 Usuários de Teste

| Email | Senha | Hierarquia | Descrição |
|-------|-------|------------|-----------|
| `admin@redeconfianca.com` | `redeconfianca123` | SUPERADMIN | Controle total |
| `leo.teixeira@redeconfianca.com` | `redeconfianca123` | ADMINISTRATIVO | Gestão geral |

## 🤝 Contribuição

1. **Fork** o projeto
2. Crie sua **feature branch** (`git checkout -b feature/AmazingFeature`)
3. **Commit** suas mudanças (`git commit -m 'Add: Amazing Feature'`)
4. **Push** para a branch (`git push origin feature/AmazingFeature`)
5. Abra um **Pull Request**

### 📝 Padrões de Commit
- `Add:` Nova funcionalidade
- `Fix:` Correção de bug
- `Update:` Atualização de código
- `Remove:` Remoção de código
- `Docs:` Documentação

## 📄 Licença

Este projeto está sob a licença MIT. Veja o arquivo [LICENSE](LICENSE) para mais detalhes.

## 📞 Suporte

- **Email**: suporte@redeconfianca.com
- **WhatsApp**: (77) 9 9988-3267
- **LinkedIn**: [Rede Confiança](https://linkedin.com/company/rede-confianca)

---

**Desenvolvido com ❤️ pela equipe Rede Confiança**

*Sistema de Chamados v1.0 - 2025*
