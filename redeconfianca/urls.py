"""
URL configuration for redeconfianca project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve
from django.http import HttpResponse, Http404
from rest_framework.routers import DefaultRouter
from users.views import UserViewSet, SectorViewSet, login_view, logout_view
from tickets.views import TicketViewSet, CategoryViewSet
from communications.views import home_feed
import os

# Router para API REST (com autenticação)
router = DefaultRouter()
router.register(r'users', UserViewSet)
router.register(r'sectors', SectorViewSet)
router.register(r'tickets', TicketViewSet)
router.register(r'categories', CategoryViewSet)

# Router para API pública (sem autenticação - para produção)
from rest_framework import permissions
from users.views import PublicUserViewSet, PublicSectorViewSet
from tickets.views import PublicTicketViewSet, PublicCategoryViewSet

public_router = DefaultRouter()
public_router.register(r'users', PublicUserViewSet, basename='public-users')
public_router.register(r'sectors', PublicSectorViewSet, basename='public-sectors')
public_router.register(r'tickets', PublicTicketViewSet, basename='public-tickets')
public_router.register(r'categories', PublicCategoryViewSet, basename='public-categories')

try:
    from tickets.views import WebhookViewSet
    router.register(r'webhooks', WebhookViewSet)
except ImportError:
    pass

try:
    from communications.views import CommunicationViewSet
    router.register(r'communications', CommunicationViewSet)
except ImportError:
    pass

# View para servir o service worker da raiz
def service_worker_view(request):
    """Serve the service worker from the root directory"""
    sw_path = os.path.join(settings.BASE_DIR, 'sw.js')
    
    if os.path.exists(sw_path):
        with open(sw_path, 'r', encoding='utf-8') as f:
            content = f.read()
        response = HttpResponse(content, content_type='application/javascript')
        response['Service-Worker-Allowed'] = '/'
        response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response
    else:
        raise Http404("Service worker not found")

# View para servir o OneSignal Service Worker
def onesignal_worker_view(request):
    """Serve the OneSignal service worker from the root directory"""
    sw_path = os.path.join(settings.BASE_DIR, 'OneSignalSDKWorker.js')
    
    if os.path.exists(sw_path):
        with open(sw_path, 'r', encoding='utf-8') as f:
            content = f.read()
        response = HttpResponse(content, content_type='application/javascript')
        response['Service-Worker-Allowed'] = '/'
        response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response
    else:
        raise Http404("OneSignal Service worker not found")

urlpatterns = [
    path('admin/', admin.site.urls),
    path('sw.js', service_worker_view, name='service_worker'),  # Service Worker na raiz
    path('OneSignalSDKWorker.js', onesignal_worker_view, name='onesignal_worker'),  # OneSignal Service Worker
    path('', home_feed, name='home'),  # Home feed como página inicial
    path('login/', login_view, name='login'),  # Login na raiz
    path('logout/', logout_view, name='logout'),  # Logout na raiz
    path('commission/', include('users.urls_commission')),  # Comissionamento na raiz
    path('users/', include('users.urls')),
    path('tickets/', include('tickets.urls')),
    path('communications/', include('communications.urls')),
    path('prizes/', include('prizes.urls')),
    path('projects/', include('projects.urls')),
    path('assets/', include('assets.urls')),
    path('files/', include('files.urls')),
    path('trainings/', include('trainings.urls')),
    path('suppliers/', include('suppliers.urls')),
    path('purchases/', include('purchases.urls')),
    path('notifications/', include('notifications.urls')),
    path('compliments/', include('compliments.urls')),  # Sistema de elogios
    path('checklists/', include('checklists.urls')),  # Sistema de checklists
    path('benefits/', include('benefits.urls')),  # Clube de Benefícios
    path('trilhas/', include('knowledge_trails.urls')),  # Trilhas de Conhecimento
    path('bet/', include('betting.urls')),  # Confiança BET - Sistema de Apostas
    path('reports/', include('core.reports_urls')),  # Sistema de denúncias
    path('groups/', include('core.group_urls')),  # Sistema de gerenciamento de grupos
    path('', include('core.urls')),  # Inclui marketplace, dashboard, training
    path('api/', include(router.urls)),
    path('api/public/', include(public_router.urls)),  # APIs públicas para produção
    path('api-auth/', include('rest_framework.urls')),
]

# Forçar servir arquivos de mídia sempre que necessário
# Em produção real, configure nginx/apache para servir estes arquivos
urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
else:
    # Em produção, usar arquivos coletados
    urlpatterns += [
        re_path(r'^static/(?P<path>.*)$', serve, {'document_root': settings.STATIC_ROOT}),
    ]
