const CACHE_NAME = 'rede-confianca-v1';
const urlsToCache = [
  '/',
  '/static/css/custom.css',
  '/static/js/app.js',
  '/static/images/logo.png',
  '/static/images/logo.svg',
  'https://cdn.tailwindcss.com',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css'
];

// Install event
self.addEventListener('install', event => {
  console.log('Service Worker: Installing...');
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        console.log('Service Worker: Cache opened');
        return cache.addAll(urlsToCache);
      })
      .then(() => {
        console.log('Service Worker: Install complete');
        // Forçar ativação imediata
        return self.skipWaiting();
      })
      .catch(error => {
        console.error('Service Worker: Install failed:', error);
      })
  );
});

// Fetch event
self.addEventListener('fetch', event => {
  event.respondWith(
    caches.match(event.request)
      .then(response => {
        // Return cached version or fetch from network
        if (response) {
          return response;
        }
        return fetch(event.request);
      }
    )
  );
});

// Activate event
self.addEventListener('activate', event => {
  console.log('Service Worker: Activating...');
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheName !== CACHE_NAME) {
            console.log('Service Worker: Deleting old cache:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => {
      console.log('Service Worker: Activated');
      // Assumir controle imediatamente
      return self.clients.claim();
    })
  );
});

// Background sync for notifications
self.addEventListener('sync', event => {
  if (event.tag === 'background-sync') {
    event.waitUntil(doBackgroundSync());
  }
});

// Push notifications with enhanced iOS support
self.addEventListener('push', event => {
  console.log('Service Worker: Push event received', event);
  
  let notificationData = {};
  
  try {
    // Tentar parsear os dados da notificação
    notificationData = event.data ? event.data.json() : {};
    console.log('Service Worker: Parsed notification data:', notificationData);
  } catch (e) {
    console.log('Service Worker: Failed to parse JSON, using text fallback');
    // Fallback para texto simples
    notificationData = {
      title: 'Rede Confiança',
      body: event.data ? event.data.text() : 'Nova notificação'
    };
  }
  
  // Detectar se é iOS/Safari baseado no user agent do service worker
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
  const isSafari = /Safari/.test(navigator.userAgent) && !/Chrome/.test(navigator.userAgent);
  
  // Configurações básicas compatíveis com iOS
  const baseOptions = {
    body: notificationData.body || 'Nova notificação',
    icon: notificationData.icon || '/static/images/logo.png',
    badge: notificationData.badge || '/static/images/logo.png',
    data: notificationData.data || {
      dateOfArrival: Date.now(),
      url: notificationData.url || '/',
      id: notificationData.id || Date.now()
    },
    tag: notificationData.tag || 'rede-confianca',
    renotify: true,
    timestamp: Date.now()
  };
  
  // Adicionar funcionalidades avançadas apenas se não for iOS/Safari
  if (!isIOS && !isSafari) {
    baseOptions.vibrate = notificationData.vibrate || [200, 100, 200];
    baseOptions.requireInteraction = notificationData.requireInteraction || false;
    baseOptions.silent = notificationData.silent || false;
    baseOptions.actions = notificationData.actions || [
      {
        action: 'open',
        title: 'Abrir',
        icon: '/static/images/logo.png'
      },
      {
        action: 'dismiss',
        title: 'Dispensar'
      }
    ];
    
    // Adicionar imagem se disponível (não suportado no iOS Safari)
    if (notificationData.image) {
      baseOptions.image = notificationData.image;
    }
  } else {
    // Para iOS, usar configurações simplificadas
    console.log('Service Worker: iOS/Safari detected, using simplified notification options');
  }

  console.log('Service Worker: Showing notification with options:', baseOptions);

  event.waitUntil(
    self.registration.showNotification(
      notificationData.title || 'Rede Confiança', 
      baseOptions
    )
    .then(() => {
      console.log('Service Worker: Notification shown successfully');
      
      // Para iOS, tentar usar API nativa se disponível
      if (isIOS && 'Notification' in self && Notification.permission === 'granted') {
        try {
          new Notification(notificationData.title || 'Rede Confiança', {
            body: baseOptions.body,
            icon: baseOptions.icon
          });
        } catch (e) {
          console.log('Service Worker: Native Notification API not available or failed');
        }
      }
    })
    .catch(error => {
      console.error('Service Worker: Failed to show notification:', error);
      
      // Fallback para iOS: tentar API nativa
      if (isIOS && 'Notification' in self) {
        try {
          if (Notification.permission === 'granted') {
            new Notification(notificationData.title || 'Rede Confiança', {
              body: baseOptions.body,
              icon: baseOptions.icon
            });
          }
        } catch (e) {
          console.error('Service Worker: Fallback notification also failed:', e);
        }
      }
    })
  );
});

// Notification click
self.addEventListener('notificationclick', event => {
  event.notification.close();

  const data = event.notification.data || {};
  const url = data.url || '/';

  if (event.action === 'open' || !event.action) {
    event.waitUntil(
      clients.matchAll().then(clientList => {
        // Tentar focar uma aba existente
        for (const client of clientList) {
          if (client.url === url && 'focus' in client) {
            return client.focus();
          }
        }
        // Se não houver, abrir nova aba
        if (clients.openWindow) {
          return clients.openWindow(url);
        }
      })
    );
  }
});

function doBackgroundSync() {
  // Background sync logic here
  return Promise.resolve();
}