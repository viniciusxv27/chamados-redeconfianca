// Service Worker para Notificações Push
const CACHE_NAME = 'chamados-push-v1';
const urlsToCache = [
    '/',
    '/static/css/',
    '/static/js/',
    '/static/images/logo.png'
];

// Instalar Service Worker
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => {
                return cache.addAll(urlsToCache);
            })
    );
    self.skipWaiting();
});

// Ativar Service Worker
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(cacheNames => {
            return Promise.all(
                cacheNames.map(cacheName => {
                    if (cacheName !== CACHE_NAME) {
                        return caches.delete(cacheName);
                    }
                })
            );
        })
    );
    self.clients.claim();
});

// Interceptar requisições
self.addEventListener('fetch', event => {
    event.respondWith(
        caches.match(event.request)
            .then(response => {
                // Cache hit - return response
                if (response) {
                    return response;
                }
                return fetch(event.request);
            })
    );
});

// Receber Push Messages
self.addEventListener('push', event => {
    const options = {
        body: 'Nova notificação do Sistema de Chamados',
        icon: '/static/images/notification-icon.png',
        badge: '/static/images/badge-icon.png',
        vibrate: [100, 50, 100],
        data: {
            dateOfArrival: Date.now(),
            primaryKey: 1
        },
        actions: [
            {
                action: 'explore',
                title: 'Ver Detalhes',
                icon: '/static/images/checkmark.png'
            },
            {
                action: 'close',
                title: 'Fechar',
                icon: '/static/images/xmark.png'
            }
        ]
    };
    
    if (event.data) {
        try {
            const data = event.data.json();
            
            options.title = data.title || 'Sistema de Chamados';
            options.body = data.message || options.body;
            options.icon = data.icon || options.icon;
            
            if (data.url) {
                options.data.url = data.url;
            }
            
            if (data.priority === 'URGENT') {
                options.requireInteraction = true;
                options.vibrate = [200, 100, 200, 100, 200];
            }
            
            // Personalizar ações baseado no tipo
            if (data.type === 'TICKET') {
                options.actions = [
                    {
                        action: 'view-ticket',
                        title: 'Ver Chamado',
                        icon: '/static/images/ticket-icon.png'
                    },
                    {
                        action: 'close',
                        title: 'Fechar'
                    }
                ];
            } else if (data.type === 'COMMUNICATION') {
                options.actions = [
                    {
                        action: 'view-communication',
                        title: 'Ver Comunicado',
                        icon: '/static/images/communication-icon.png'
                    },
                    {
                        action: 'close',
                        title: 'Fechar'
                    }
                ];
            }
            
        } catch (error) {
            console.error('Erro ao processar dados do push:', error);
        }
    }
    
    event.waitUntil(
        self.registration.showNotification('Sistema de Chamados', options)
    );
});

// Clique na notificação
self.addEventListener('notificationclick', event => {
    event.notification.close();
    
    const action = event.action;
    const data = event.notification.data;
    
    if (action === 'close') {
        return;
    }
    
    let url = '/notifications/';
    
    if (data && data.url) {
        url = data.url;
    } else if (action === 'view-ticket') {
        url = '/tickets/';
    } else if (action === 'view-communication') {
        url = '/communications/';
    }
    
    event.waitUntil(
        clients.matchAll().then(clientList => {
            // Verificar se já existe uma aba aberta
            for (const client of clientList) {
                if (client.url.includes(self.location.origin) && 'focus' in client) {
                    client.navigate(url);
                    return client.focus();
                }
            }
            
            // Se não há aba aberta, abrir nova
            if (clients.openWindow) {
                return clients.openWindow(url);
            }
        })
    );
});

// Fechar notificação
self.addEventListener('notificationclose', event => {
    const data = event.notification.data;
    
    // Registrar que a notificação foi fechada (opcional)
    if (data && data.notificationId) {
        fetch('/notifications/closed/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                notificationId: data.notificationId,
                closedAt: Date.now()
            })
        }).catch(error => {
            console.error('Erro ao registrar fechamento:', error);
        });
    }
});

// Sincronização em background
self.addEventListener('sync', event => {
    if (event.tag === 'background-sync') {
        event.waitUntil(doBackgroundSync());
    }
});

async function doBackgroundSync() {
    try {
        // Sincronizar notificações pendentes
        const response = await fetch('/notifications/sync/');
        const data = await response.json();
        
        if (data.notifications) {
            for (const notification of data.notifications) {
                await self.registration.showNotification(notification.title, {
                    body: notification.message,
                    icon: notification.icon || '/static/images/notification-icon.png',
                    data: notification.data
                });
            }
        }
    } catch (error) {
        console.error('Erro na sincronização:', error);
    }
}

// Mensagens do cliente
self.addEventListener('message', event => {
    if (event.data && event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }
    
    if (event.data && event.data.type === 'GET_VERSION') {
        event.ports[0].postMessage({
            version: CACHE_NAME
        });
    }
});

// Erro no Service Worker
self.addEventListener('error', event => {
    console.error('Service Worker Error:', event.error);
});

// Promise rejeitada não tratada
self.addEventListener('unhandledrejection', event => {
    console.error('Unhandled Promise Rejection:', event.reason);
    event.preventDefault();
});