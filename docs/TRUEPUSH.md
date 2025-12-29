# Integração Truepush - Documentação

## O que é o Truepush?

Truepush é uma plataforma de notificações push gratuita que permite enviar notificações para:
- Navegadores web (Chrome, Firefox, Safari, Edge)
- Dispositivos móveis via navegador

## Configuração

### 1. Criar conta no Truepush

1. Acesse [app.truepush.com](https://app.truepush.com/)
2. Crie uma conta gratuita
3. Crie um novo projeto para o seu site
4. Copie a **API Key** e o **Project ID**

### 2. Configurar variáveis de ambiente

Adicione as seguintes variáveis ao seu arquivo `.env`:

```env
# Truepush Configuration
TRUEPUSH_API_KEY=sua_api_key_aqui
TRUEPUSH_PROJECT_ID=seu_project_id_aqui

# URL base do sistema
BASE_URL=https://seu-dominio.com
```

### 3. Reiniciar o servidor

Após configurar as variáveis, reinicie o servidor Django para aplicar as alterações.

## Como funciona

### Integração automática

O script do Truepush é carregado automaticamente no template `base.html` quando o sistema detecta que está configurado. Os usuários verão um prompt para permitir notificações ao acessar o site.

### Dashboard de administração

Acesse `/notifications/truepush/` para:
- Ver estatísticas de assinantes
- Enviar notificações push manualmente
- Gerenciar segmentos

### Integração com o sistema de notificações

O Truepush está integrado ao sistema de notificações existente. Você pode usar o canal `truepush` ao enviar notificações:

```python
from notifications.services import notification_service, NotificationChannel

# Enviar notificação por todos os canais incluindo Truepush
notification_service.send_notification(
    recipients=users,
    title="Título",
    message="Mensagem",
    channels=[NotificationChannel.IN_APP, NotificationChannel.PUSH, NotificationChannel.TRUEPUSH],
    action_url="/destino/"
)
```

Ou usar a função direta para Truepush:

```python
from notifications.services import send_truepush_notification

# Enviar apenas via Truepush para todos os assinantes
send_truepush_notification(
    title="Título",
    message="Mensagem",
    url="/destino/"
)
```

## Comandos de gerenciamento

### Testar Truepush

```bash
python manage.py test_truepush
python manage.py test_truepush --title "Meu Título" --message "Minha mensagem"
```

## API Endpoints

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/notifications/truepush/` | GET | Dashboard de administração |
| `/notifications/truepush/send/` | POST | Enviar notificação |
| `/notifications/truepush/stats/` | GET | Obter estatísticas |
| `/notifications/truepush/subscribers/` | GET | Contagem de assinantes |
| `/notifications/truepush/segments/` | GET | Listar segmentos |
| `/notifications/api/truepush/config/` | GET | Obter configuração (público) |

## Modelos de dados

### TruepushSubscriber
Vincula assinantes do Truepush a usuários do sistema (opcional).

### TruepushNotificationLog
Registra histórico de notificações enviadas via Truepush.

## Troubleshooting

### Notificações não aparecem

1. Verifique se o navegador permite notificações para o site
2. Verifique se o service worker está registrado
3. Verifique se as credenciais do Truepush estão corretas
4. Verifique os logs no Django Admin (`/admin/notifications/truepushnotificationlog/`)

### Erro de API

Se a API retornar erro, verifique:
1. Se a API Key está correta
2. Se o Project ID está correto
3. Se o projeto está ativo no painel do Truepush

## Suporte

Para dúvidas sobre a plataforma Truepush, consulte a [documentação oficial](https://docs.truepush.com/).
