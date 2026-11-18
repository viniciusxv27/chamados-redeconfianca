"""Módulo Power BI no app — o /power-bi/ do portal no celular.

Regras (de onde veio cada uma):

- entrar: a view web ``power_bi_list_view`` (power_bi/views.py:652) só pede
  login, e o item "Power BI" do menu (templates/base.html:328) aparece para
  todo mundo logado — então aqui também basta estar logado (o bloqueio do
  portal continua valendo pelo ``permissao('power_bi')``);
- quais dashboards: ``_visible_reports_for`` (power_bi/views.py:597), a mesma
  lista da tela web (ativos + ``PowerBIReport.is_visible_to``: usuário, grupo,
  setor principal e setores, hierarquia; sem regra = todos; SUPERADMIN vê tudo);
- abrir: a mesma conferência do ``power_bi_viewer`` (power_bi/views.py:664):
  ativo e ``is_visible_to``; senão 404 aqui (a web volta para a lista).
  O app não tem WebView, então:
    * dashboard com "Nova guia permitida" (``allow_open_in_new_tab``): a web
      mostra o link direto do embed (templates/power_bi/viewer.html:14) → o app
      recebe o link e registra o ``PowerBIAccessLog`` igual ao viewer da web;
    * sem essa opção: a web só mostra o embed dentro da página do portal → o
      app abre a página do portal (/power-bi/<id>/), que registra o acesso
      sozinha ao carregar (sem log duplicado aqui).
"""
from urllib.parse import urlparse

from django.http import Http404
from django.urls import path
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from power_bi import views as pv
from power_bi.models import PowerBIAccessLog, PowerBIReport

from .. import dados
from ..autenticacao import TokenMobileAuthentication
from . import permissao


def pode_entrar(user):
    # power_bi/views.py:652 (@login_required) e templates/base.html:328 (item do menu sem condição).
    return bool(user and user.is_authenticated and user.is_active)


MODULO = {'rotulo': 'Power BI', 'descricao': 'Os dashboards liberados para você', 'icone': 'analytics-outline',
          'cor': '#F2C811', 'pode_entrar': pode_entrar}


def _origem(url):
    """Categoria para o filtro do app: o model não tem categoria; separa pelo endereço do painel."""
    host = (urlparse(url or '').netloc or '').lower()
    if host.endswith('powerbi.com'):
        return 'power_bi'
    return 'outros'


def _dashboard(request, r):
    return {
        'id': r.id,
        'nome': r.name,
        'descricao': r.description or '',
        'icone': r.icon_class or 'fas fa-chart-line',
        'cor': r.predominant_color or '#f97316',
        'imagem': dados.url_absoluta(request, r.card_background_image),
        'abre_direto': bool(r.allow_open_in_new_tab),
        'origem': _origem(r.embed_url),
    }


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('power_bi')])
def lista(request):
    # power_bi/views.py:653 — a mesma lista (e ordem) da tela web.
    relatorios = pv._visible_reports_for(request.user)
    return Response({'resultados': [_dashboard(request, r) for r in relatorios]})


def _relatorio_visivel(user, report_id):
    # power_bi/views.py:666-674 — ativo e visível para a pessoa; o resto é 404.
    r = (PowerBIReport.objects.prefetch_related('allowed_groups', 'allowed_sectors', 'allowed_users')
         .filter(id=report_id, is_active=True).first())
    if r is None or not r.is_visible_to(user):
        raise Http404
    return r


@api_view(['POST'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('power_bi')])
def abrir(request, report_id):
    r = _relatorio_visivel(request.user, report_id)
    if r.allow_open_in_new_tab:
        # power_bi/views.py:676 — o viewer registra cada abertura.
        PowerBIAccessLog.objects.create(report=r, user=request.user)
        return Response({'url': r.embed_url, 'onde': 'direto', 'registrado': True})
    return Response({'url': dados.absoluta(request, f'/power-bi/{r.id}/'), 'onde': 'portal', 'registrado': False})


urlpatterns = [
    path('', lista, name='power_bi_lista'),
    path('<int:report_id>/abrir/', abrir, name='power_bi_abrir'),
]
