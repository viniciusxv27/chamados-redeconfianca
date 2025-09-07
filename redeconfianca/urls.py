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
from rest_framework.routers import DefaultRouter
from users.views import UserViewSet, SectorViewSet, login_view, logout_view
from tickets.views import TicketViewSet, CategoryViewSet
from communications.views import home_feed

# Router para API REST
router = DefaultRouter()
router.register(r'users', UserViewSet)
router.register(r'sectors', SectorViewSet)
router.register(r'tickets', TicketViewSet)
router.register(r'categories', CategoryViewSet)

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

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', home_feed, name='home'),  # Home feed como página inicial
    path('login/', login_view, name='login'),  # Login na raiz
    path('logout/', logout_view, name='logout'),  # Logout na raiz
    path('users/', include('users.urls')),
    path('tickets/', include('tickets.urls')),
    path('communications/', include('communications.urls')),
    path('prizes/', include('prizes.urls')),
    path('assets/', include('assets.urls')),
    path('reports/', include('core.reports_urls')),  # Sistema de denúncias
    path('', include('core.urls')),  # Inclui marketplace, dashboard, training
    path('api/', include(router.urls)),
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
