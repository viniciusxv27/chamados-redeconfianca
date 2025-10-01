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

// Push notifications
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
  
  const options = {
    body: notificationData.body || 'Nova notificação',
    icon: notificationData.icon || '/static/images/logo.png',
    badge: notificationData.badge || '/static/images/logo.png',
    vibrate: notificationData.vibrate || [100, 50, 100],
    data: notificationData.data || {
      dateOfArrival: Date.now(),
      url: '/'
    },
    requireInteraction: notificationData.requireInteraction || false,
    silent: notificationData.silent || false,
    actions: notificationData.actions || [
      {
        action: 'open',
        title: 'Ver mais',
        icon: '/static/images/logo.png'
      },
      {
        action: 'close',
        title: 'Fechar',
        icon: '/static/images/logo.png'
      }
    ]
  };

  console.log('Service Worker: Showing notification with options:', options);

  event.waitUntil(
    self.registration.showNotification(notificationData.title || 'Rede Confiança', options)
      .then(() => {
        console.log('Service Worker: Notification shown successfully');
      })
      .catch(error => {
        console.error('Service Worker: Failed to show notification:', error);
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
