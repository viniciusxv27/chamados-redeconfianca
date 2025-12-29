# 🔔 Integração OneSignal - Push Notifications

## Visão Geral

O sistema está integrado com o **OneSignal**, uma plataforma líder em push notifications que oferece:

- ✅ **Gratuito** para até 10.000 assinantes web push ilimitados
- 📱 Suporte a **Web Push** (Chrome, Firefox, Edge, Safari)
- 📲 Suporte a **Mobile Push** (iOS e Android)
- 📊 **Dashboard** completo para gerenciamento
- 🎯 **Segmentação** avançada de usuários
- 📈 **Métricas** detalhadas de entrega

## Configuração

### 1. Criar Conta no OneSignal

1. Acesse [onesignal.com](https://onesignal.com) e crie uma conta gratuita
2. Crie um novo App
3. Configure **Web Push** seguindo o assistente
4. Copie o **App ID** e a **REST API Key**

### 2. Configurar Variáveis de Ambiente

Adicione as seguintes variáveis ao seu arquivo `.env`:

```env
# OneSignal Configuration
ONESIGNAL_APP_ID=seu-app-id-aqui
ONESIGNAL_REST_API_KEY=sua-rest-api-key-aqui
```

### 3. Verificar Configuração

Acesse o dashboard OneSignal no sistema:
- URL: `/notifications/onesignal/`
- Apenas SUPERADMINs podem acessar

## Arquitetura

### Arquivos Principais

```
notifications/
├── onesignal_service.py    # Serviço de integração com API OneSignal
├── models.py               # OneSignalPlayer, OneSignalNotificationLog
├── views.py                # Views do dashboard e API
├── urls.py                 # Rotas
├── admin.py                # Admin para modelos OneSignal
└── services.py             # NotificationService (canal ONESIGNAL)

templates/
├── base.html               # Script SDK OneSignal integrado
└── notifications/
    └── onesignal_dashboard.html  # Dashboard de gerenciamento
```

### Modelos

#### OneSignalPlayer
Armazena os players (dispositivos) inscritos para push:
- `player_id`: ID único do player no OneSignal
- `user`: Usuário associado (se logado)
- `device_type`: web, android, ios
- `browser`: Chrome, Firefox, etc.
- `is_active`: Se ainda está inscrito

#### OneSignalNotificationLog
Registra histórico de notificações enviadas:
- `notification_id`: ID da notificação no OneSignal
- `title`, `message`: Conteúdo
- `segment`: Segmento alvo
- `success`: Se foi enviada com sucesso
- `sent_count`: Quantidade de dispositivos

## Uso

### Via NotificationService

```python
from notifications.services import NotificationService, NotificationChannel

service = NotificationService()

# Enviar para todos os canais (incluindo OneSignal)
service.send_notification(
    title="Nova atualização",
    message="Confira as novidades do sistema!",
    recipients=[user],
    channels=[NotificationChannel.ALL]
)

# Enviar apenas via OneSignal
service.send_notification(
    title="Promoção especial",
    message="Só hoje: 50% de desconto!",
    channels=[NotificationChannel.ONESIGNAL]
)
```

### Via OneSignal Service Diretamente

```python
from notifications.onesignal_service import onesignal_service

# Enviar para todos os assinantes
result = onesignal_service.send_to_all(
    title="Aviso importante",
    message="Manutenção programada às 22h",
    url="/comunicados/"
)

# Enviar para segmento específico
result = onesignal_service.send_to_segment(
    title="Novo chamado",
    message="Você tem um novo chamado",
    segment="Active Users"
)

# Enviar para usuários específicos (por external_user_id)
result = onesignal_service.send_to_external_users(
    title="Chamado atribuído",
    message="Um chamado foi atribuído a você",
    external_user_ids=["123", "456"]
)
```

### Via API REST

#### Enviar Notificação
```http
POST /notifications/onesignal/send/
Content-Type: application/json
Authorization: (requer login SUPERADMIN)

{
    "title": "Título da notificação",
    "message": "Corpo da mensagem",
    "url": "/destino/",
    "segment": "Subscribed Users"
}
```

#### Obter Configuração (público)
```http
GET /notifications/api/onesignal/config/
```

#### Obter Estatísticas (SUPERADMIN)
```http
GET /notifications/onesignal/stats/
GET /notifications/onesignal/players/
GET /notifications/onesignal/segments/
```

## Segmentos

O OneSignal permite criar segmentos para direcionar notificações:

- **Subscribed Users**: Todos os usuários inscritos
- **Active Users**: Usuários ativos recentemente
- **Engaged Users**: Usuários engajados
- Segmentos customizados baseados em tags

### Tags de Usuário

O sistema automaticamente associa tags aos usuários logados:
- `user_id`: ID do usuário
- `email`: Email do usuário
- `hierarchy`: Hierarquia (SUPERADMIN, ADMIN, etc.)
- `sector`: Setor do usuário

## Dashboard

O dashboard OneSignal (`/notifications/onesignal/`) oferece:

1. **Estatísticas**
   - Total de players/assinantes
   - Segmentos disponíveis
   - Notificações recentes

2. **Envio de Notificações**
   - Formulário para enviar notificações push
   - Seleção de segmento
   - Ícone e imagem customizáveis

3. **Histórico**
   - Lista de notificações enviadas
   - Status de entrega
   - Contagem de dispositivos atingidos

## SDK JavaScript

O SDK do OneSignal é carregado automaticamente no `base.html`:

```javascript
// Solicitar permissão para notificações
window.requestPushPermission();

// Verificar se está inscrito
const subscribed = await window.isPushSubscribed();
```

## Plano Gratuito

O OneSignal oferece um plano gratuito generoso:

| Recurso | Limite |
|---------|--------|
| Web Push Subscribers | 10.000 |
| Notificações/mês | Ilimitado |
| Segmentos | Básicos |
| Analytics | 30 dias |

Para mais recursos, consulte os [planos pagos](https://onesignal.com/pricing).

## Migração do Truepush

O Truepush foi descontinuado. O sistema mantém os modelos legados (`TruepushSubscriber`, `TruepushNotificationLog`) para compatibilidade, mas todas as novas funcionalidades usam OneSignal.

Rotas legadas (`/notifications/truepush/*`) redirecionam automaticamente para OneSignal.

## Troubleshooting

### Notificações não aparecem
1. Verifique se o usuário permitiu notificações no navegador
2. Verifique se HTTPS está habilitado (obrigatório para web push)
3. Verifique se as credenciais estão corretas no `.env`

### Erros de API
1. Verifique os logs: `python manage.py shell` + `from notifications.onesignal_service import onesignal_service; print(onesignal_service.get_app_info())`
2. Verifique a REST API Key no painel do OneSignal

### Safari não funciona
Safari requer configuração adicional no painel do OneSignal (Web Push Certificate).

## Referências

- [Documentação OneSignal](https://documentation.onesignal.com/)
- [Web Push API](https://documentation.onesignal.com/docs/web-push-quickstart)
- [REST API](https://documentation.onesignal.com/reference/create-notification)
