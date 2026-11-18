"""Módulo Facilitador no app (/api/mobile/v1/m/facilitador/): arquivos e links úteis.

Regras de onde vieram (facilitador/views.py):
- Entrada: ``inicio`` só tem @login_required (views.py:131) e o item do menu aparece para
  todo logado (templates/base.html:448, sem {% if %}).
- Recorte: quem gerencia (SUPERADMIN, ``pode_gerenciar`` views.py:37) vê tudo, inclusive o
  oculto; os outros só ``ativo=True`` (views.py:133). Detalhe/arquivo/link: ``_visivel``
  (views.py:47) — oculto para quem não gerencia é 404. Não há recorte por setor/grupo.
- Busca/categoria/tipo: os filtros da tela (views.py:134-144).
- Abrir link: a própria view ``ir`` (views.py:254) — conta o clique e devolve o destino; o
  app abre no navegador do sistema.
- Arquivo: link assinado (api_mobile/arquivos.py); o resolvedor confere ``_visivel`` de novo
  com o dono do link e conta o download como ``baixar`` (views.py:165). Só PDF/imagem/mídia/
  documento abre na tela; HTML/SVG/XML vai como download (o portal também nunca serve
  arquivo enviado como página na própria origem — a página HTML usa o link público isolado,
  ``link_da_pagina``/``pagina_publica``, views.py:212 e 244).

Cadastro, edição, ocultar, excluir e QR code ficam no portal.
"""
import mimetypes
from types import SimpleNamespace

from django.db.models import Q
from django.urls import path
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from facilitador import views as web
from facilitador.models import Recurso

from .. import dados
from ..arquivos import link, registrar
from ..autenticacao import TokenMobileAuthentication
from . import permissao


def pode_entrar(user):
    # facilitador/views.py:131 — só @login_required; o menu (base.html:448) aparece para todo logado.
    return bool(user and user.is_authenticated and user.is_active)


MODULO = {'rotulo': 'Facilitador', 'descricao': 'Arquivos e links úteis',
          'icone': 'link-outline', 'cor': '#14B8A6', 'pode_entrar': pode_entrar}

AUTH = [TokenMobileAuthentication]
PERM = [permissao('facilitador')]

# Tipo de arquivo que o navegador executaria como página: vai sempre como download.
_PERIGOSOS = ('html', 'xml', 'svg', 'javascript', 'ecmascript')


def _visivel(user, pk):
    """``facilitador.views._visivel`` com o usuário do app (ele só usa ``request.user``)."""
    return web._visivel(SimpleNamespace(user=user), pk)


def _resumo(r, gestor):
    return {
        'id': r.pk,
        'titulo': r.titulo,
        'descricao': (r.descricao or '')[:160] or None,
        'tipo': 'link' if r.e_link else 'arquivo',
        'categoria': r.categoria or None,
        'extensao': None if r.e_link else (r.extensao or None),
        'tamanho': None if r.e_link else (r.tamanho or None),
        'dominio': r.dominio or None if r.e_link else None,
        'pagina': bool(r.e_html and r.arquivo),
        'oculto': (not r.ativo) if gestor else False,
    }


@api_view(['GET'])
@authentication_classes(AUTH)
@permission_classes(PERM)
def lista(request):
    """?busca= &categoria= &tipo=arquivo|link (os filtros da tela, facilitador/views.py:131)."""
    gestor = web.pode_gerenciar(request.user)
    recursos = Recurso.objects.all() if gestor else Recurso.objects.filter(ativo=True)
    categorias = sorted({c for c in recursos.values_list('categoria', flat=True) if c}, key=str.lower)
    busca = (request.query_params.get('busca') or '').strip()
    categoria = (request.query_params.get('categoria') or '').strip()
    tipo = (request.query_params.get('tipo') or '').strip().upper()
    if busca:
        recursos = recursos.filter(Q(titulo__icontains=busca) | Q(descricao__icontains=busca)
                                   | Q(categoria__icontains=busca) | Q(nome_original__icontains=busca))
    if categoria:
        recursos = recursos.filter(categoria__iexact=categoria)
    if tipo in (Recurso.TIPO_ARQUIVO, Recurso.TIPO_LINK):
        recursos = recursos.filter(tipo=tipo)
    return Response({
        'categorias': categorias,
        'recursos': [_resumo(r, gestor) for r in recursos],
        'gerencia': gestor,
    })


@api_view(['GET'])
@authentication_classes(AUTH)
@permission_classes(PERM)
def detalhe(request, pk):
    gestor = web.pode_gerenciar(request.user)
    r = _visivel(request.user, pk)
    saida = _resumo(r, gestor)
    saida.update({
        'descricao': r.descricao or None,
        'url': r.url or None if r.e_link else None,
        'nome_arquivo': None if r.e_link else (r.nome_original or (r.arquivo.name.rsplit('/', 1)[-1] if r.arquivo else None)),
        'tem_arquivo': bool(not r.e_link and r.arquivo),
        'atualizado_em': dados.iso(r.atualizado_em),
        # Quantos cliques/downloads: a web mostra só para quem gerencia (detalhe.html:132).
        'acessos': r.acessos if gestor else None,
        'caminho_web': f'/facilitador/{r.pk}/',
    })
    return Response(saida)


@api_view(['POST'])
@authentication_classes(AUTH)
@permission_classes(PERM)
def abrir(request, pk):
    """O destino para abrir agora: o link (contado pela view ``ir`` da web), a página HTML
    (link público isolado, enquanto visível) ou o arquivo (link assinado de 5 min)."""
    r = _visivel(request.user, pk)
    if r.e_link:
        if not r.url:
            return Response({'erro': 'Este item não tem link.'}, status=status.HTTP_404_NOT_FOUND)
        resposta = web.ir(request._request, pk)       # conta o clique (views.py:254) e redireciona
        return Response({'tipo': 'link', 'url': resposta['Location']})
    if not r.arquivo:
        return Response({'erro': 'Este item não tem arquivo.'}, status=status.HTTP_404_NOT_FOUND)
    if r.e_html and r.ativo and r.token_pagina and request.data.get('como') != 'arquivo':
        # A página abre isolada no link público (pagina_publica conta o acesso, views.py:244).
        return Response({'tipo': 'pagina', 'url': web.link_da_pagina(request._request, r)})
    return Response({'tipo': 'arquivo', 'url': link(request, 'facilitador', r.pk)})


urlpatterns = [
    path('', lista, name='m_facilitador'),
    path('<int:pk>/', detalhe, name='m_facilitador_detalhe'),
    path('<int:pk>/abrir/', abrir, name='m_facilitador_abrir'),
]


# ── Arquivo por link assinado ────────────────────────────────────────────────

def _tipo_seguro(nome):
    tipo = mimetypes.guess_type(nome or '')[0] or 'application/octet-stream'
    if any(p in tipo for p in _PERIGOSOS):
        return 'application/octet-stream'
    return tipo


@registrar('facilitador')
def _arquivo(user, obj_id):
    r = _visivel(user, int(obj_id))                 # oculto para quem não gerencia → 404
    if r.e_link or not r.arquivo:
        raise LookupError('Sem arquivo.')
    nome = r.nome_original or r.arquivo.name.rsplit('/', 1)[-1]
    Recurso.objects.filter(pk=r.pk).update(acessos=web.F('acessos') + 1)   # como ``baixar`` (views.py:172)
    return r.arquivo, nome, _tipo_seguro(nome)
