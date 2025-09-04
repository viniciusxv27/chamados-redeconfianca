# 🎯 Configuração Completa: PostgreSQL + EasyPanel

## ✅ O que foi configurado

### 1. **Banco de Dados**
- ✅ PostgreSQL como banco principal
- ✅ SQLite como fallback para desenvolvimento
- ✅ Suporte a `DATABASE_URL` para flexibilidade
- ✅ Configuração automática via `dj-database-url`

### 2. **Deploy no EasyPanel**
- ✅ `nixpacks.toml` configurado
- ✅ `Procfile` para backup
- ✅ `runtime.txt` especificando Python 3.11
- ✅ Gunicorn como servidor de produção
- ✅ WhiteNoise para arquivos estáticos

### 3. **Dependências Atualizadas**
- ✅ `psycopg2-binary` - Driver PostgreSQL
- ✅ `dj-database-url` - Parse de URL do banco
- ✅ `gunicorn` - Servidor WSGI para produção
- ✅ `whitenoise` - Servir arquivos estáticos

### 4. **Configurações de Segurança**
- ✅ HTTPS redirect em produção
- ✅ Cookies seguros
- ✅ Headers de segurança
- ✅ HSTS configurado

### 5. **Scripts de Automação**
- ✅ `setup_local.sh` - Setup automático local
- ✅ Configurações separadas dev/prod
- ✅ Documentação atualizada

## 🚀 Como fazer o deploy

### 1. **Preparar repositório**
```bash
git add .
git commit -m "Configure PostgreSQL and EasyPanel deployment"
git push origin main
```

### 2. **Configurar no EasyPanel**
1. Criar novo projeto
2. Conectar ao GitHub
3. EasyPanel detecta `nixpacks.toml` automaticamente
4. PostgreSQL é provisionado automaticamente

### 3. **Variáveis de ambiente obrigatórias**
```env
DATABASE_URL=postgresql://... # (auto-gerado)
SECRET_KEY=sua-chave-super-segura-aqui
DEBUG=False
ALLOWED_HOSTS=seu-dominio.easypanel.app
```

### 4. **Pós-deploy**
- Criar superusuário via console
- Testar funcionalidades
- Configurar domínio customizado

## 🛠️ Desenvolvimento Local

### Opção 1: SQLite (mais simples)
```bash
./setup_local.sh
# Usa SQLite automaticamente
```

### Opção 2: PostgreSQL local
```bash
# 1. Instalar PostgreSQL
brew install postgresql  # macOS
brew services start postgresql

# 2. Criar banco
createdb redeconfianca_db

# 3. Configurar .env
DATABASE_URL=postgresql://user:password@localhost:5432/redeconfianca_db

# 4. Executar setup
./setup_local.sh
```

## 📁 Arquivos criados/modificados

```
├── nixpacks.toml          # Configuração do Nixpacks
├── Procfile              # Comandos de execução
├── runtime.txt           # Versão do Python
├── setup_local.sh        # Script de setup local
├── .env.production       # Exemplo para produção
├── requirements.txt      # Dependências atualizadas
├── redeconfianca/settings.py  # Configurações do Django
├── DEPLOY.md             # Guia atualizado
└── .gitignore            # Arquivos ignorados
```

## 🔍 Verificações

### Antes do deploy:
```bash
# Verificar configurações
python manage.py check --deploy

# Testar migrações
python manage.py migrate --dry-run

# Testar coleta de estáticos
python manage.py collectstatic --noinput --dry-run
```

### Após deploy:
- [ ] Site acessível via HTTPS
- [ ] Admin funcionando
- [ ] Login/logout funcionando
- [ ] Criação de chamados
- [ ] Upload de arquivos
- [ ] Sistema de comunicados
- [ ] Marketplace de prêmios
- [ ] Módulo de ativos

## 🆘 Solução de Problemas

### Erro de conexão com banco:
1. Verificar `DATABASE_URL` no EasyPanel
2. Verificar se PostgreSQL está ativo
3. Testar conectividade

### Erro 500:
1. Verificar logs no EasyPanel
2. Verificar `SECRET_KEY`
3. Verificar `ALLOWED_HOSTS`

### Arquivos estáticos não carregam:
1. Verificar se `collectstatic` executou
2. Verificar configuração do WhiteNoise
3. Verificar `STATIC_ROOT`

## 📞 Suporte

- 📖 Documentação: `DEPLOY.md`
- 🐛 Issues: GitHub Issues
- 💬 Logs: EasyPanel Dashboard

---

**Status:** ✅ Pronto para deploy no EasyPanel
**Última atualização:** 4 de setembro de 2025
