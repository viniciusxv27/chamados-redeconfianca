# Guia de Solução de Problemas

## 1. Erro de Redirecionamento em Excesso quando DEBUG=False

### Problema
Quando `DEBUG=False`, o Django estava forçando redirecionamentos HTTPS infinitos, causando o erro "Redirecionamento em excesso".

### Solução Aplicada
- Modificado `settings.py` para desabilitar HTTPS forçado por padrão quando `DEBUG=False`
- Criado arquivo `.env.example` com configurações recomendadas
- Para ativar HTTPS em produção, configure as variáveis de ambiente adequadamente

### Configuração para Produção com HTTPS:
```
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS=True
SECURE_HSTS_PRELOAD=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
```

### Configuração para Produção sem HTTPS:
```
SECURE_SSL_REDIRECT=False
SECURE_HSTS_SECONDS=0
SECURE_HSTS_INCLUDE_SUBDOMAINS=False
SECURE_HSTS_PRELOAD=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
```

## 2. Erro "value too long for type character varying(20)"

### Problema
O campo `action_type` no modelo `SystemLog` tinha `max_length=20`, mas algumas actions como `'PRIZE_CATEGORY_CREATE'` têm mais de 20 caracteres.

### Solução Aplicada
- Aumentado `max_length` do campo `action_type` de 20 para 50 caracteres
- Adicionadas todas as actions necessárias ao modelo
- Criadas e aplicadas migrações para corrigir o banco de dados

### Actions Adicionadas:
- `PRIZE_CATEGORY_CREATE` - Criação de Categoria de Prêmio
- `PRIZE_CATEGORY_UPDATE` - Atualização de Categoria de Prêmio  
- `PRIZE_CATEGORY_DELETE` - Exclusão de Categoria de Prêmio
- `SECTOR_CREATE` - Criação de Setor
- `SECTOR_EDIT` - Edição de Setor
- `SECTOR_DELETE` - Exclusão de Setor
- `CATEGORY_CREATE` - Criação de Categoria
- `CATEGORY_UPDATE` - Atualização de Categoria
- `CATEGORY_DELETE` - Exclusão de Categoria
- `WEBHOOK_CREATE` - Criação de Webhook
- `WEBHOOK_UPDATE` - Atualização de Webhook
- `WEBHOOK_DELETE` - Exclusão de Webhook
- `REPORT_CREATE` - Criação de Denúncia
- `REPORT_UPDATE` - Atualização de Denúncia
- `REPORT_COMMENT` - Comentário em Denúncia

## 3. Como Testar as Correções

### Para o Problema de Redirecionamento:
1. Configure `DEBUG=False` no arquivo `.env`
2. Adicione seu domínio em `ALLOWED_HOSTS`
3. Configure as opções de HTTPS conforme sua necessidade
4. Teste o acesso ao sistema

### Para o Problema de Categorias de Prêmio:
1. Tente criar uma nova categoria de prêmio
2. Tente editar uma categoria existente
3. Tente deletar uma categoria existente
4. Verifique se não há mais erros nos logs

## 4. Configurações Recomendadas

### Para Desenvolvimento Local:
```
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1,testserver
SECURE_SSL_REDIRECT=False
```

### Para Produção com Domínio (sem HTTPS):
```
DEBUG=False
ALLOWED_HOSTS=localhost,127.0.0.1,seudominio.com
SECURE_SSL_REDIRECT=False
SECURE_HSTS_SECONDS=0
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
```

### Para Produção com HTTPS:
```
DEBUG=False
ALLOWED_HOSTS=localhost,127.0.0.1,seudominio.com
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS=True
SECURE_HSTS_PRELOAD=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
```

## 5. Verificação das Correções

### Comando para aplicar migrações:
```bash
python manage.py migrate
```

### Comando para verificar status das migrações:
```bash
python manage.py showmigrations
```

### Comando para testar em modo de produção:
```bash
DEBUG=False python manage.py runserver
```

## 6. Logs e Debugging

Caso ainda encontre problemas, verifique:
- Os logs do servidor Django
- Os logs do navegador (Console F12)
- As configurações do servidor web (se usando Nginx/Apache)
- As configurações de firewall e proxy

## Status das Correções
✅ Problema de redirecionamento infinito - RESOLVIDO
✅ Erro de campo muito longo no SystemLog - RESOLVIDO  
✅ Migrações aplicadas - CONCLUÍDO
✅ Novas actions adicionadas - CONCLUÍDO
