"""Módulo Documentos no app: meus documentos para assinatura (o que /documentos/ mostra).

Regras de onde vieram (documentos/views.py):
- Entrada: ``my_documents`` só tem @login_required (views.py:84) e o item do menu aparece
  para todo logado (templates/base.html, sem {% if %}).
- Lista: as atribuições do próprio com o documento liberado
  (``DocumentSignature.objects.filter(user=request.user, document__is_visible=True)``, views.py:87-91),
  filtro por situação pendente/assinado (views.py:94-98).
- Detalhe / PDF: documento não liberado só para quem administra (views.py:121); de outra
  pessoa só para quem administra (views.py:125). Fora disso → 404. (A view do PDF da web,
  views.py:146, esquece o "liberado"; aqui vale a régua do detalhe, mais estreita.)
- Assinar: a própria função da web (``api_sign_document``, views.py:224) — só o dono, uma
  vez, documento liberado; ela grava o PDF assinado (original + certificado) no storage.

Não confundir com UserDocument (documentos do pré-cadastro, app users): é outra coisa.
Administração (enviar documento, signatários, categorias, link público) fica no portal.
"""
import io
import json

from django.contrib.messages.storage.cookie import CookieStorage
from django.http import Http404
from django.test import RequestFactory
from django.urls import path
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from documentos import views as web
from documentos.models import DocumentSignature

from .. import dados
from ..arquivos import link, registrar
from ..autenticacao import TokenMobileAuthentication
from . import permissao


def pode_entrar(user):
    # documentos/views.py:84 — só @login_required; o menu (base.html) aparece para todo logado.
    return bool(user and user.is_authenticated and user.is_active)


MODULO = {'rotulo': 'Documentos', 'descricao': 'Documentos para assinar',
          'icone': 'folder-open-outline', 'cor': '#8B5CF6', 'pode_entrar': pode_entrar}

_PERM = [permissao('documentos')]
_AUTH = [TokenMobileAuthentication]


def _assinatura(user, pk):
    """A régua de documents/views.py:121-127; o que não pode ver vira 404."""
    sig = (DocumentSignature.objects.select_related('document', 'document__category', 'user')
           .filter(pk=pk).first())
    if sig is None:
        raise Http404('Documento não encontrado.')
    admin = web.pode_administrar(user)
    if not sig.document.is_visible and not admin:
        raise Http404('Documento não encontrado.')
    if sig.user_id != user.pk and not admin:
        raise Http404('Documento não encontrado.')
    return sig


def _categoria(cat):
    return {'id': cat.pk, 'nome': cat.name, 'cor': cat.color or None} if cat else None


def _resumo(sig):
    doc = sig.document
    return {
        'id': sig.pk,
        'documento_id': doc.pk,
        'titulo': doc.title,
        'descricao': (doc.description or '')[:160] or None,
        'categoria': _categoria(doc.category),
        'assinado': sig.is_signed,
        'assinado_em': dados.iso(sig.signed_at),
        'atribuido_em': dados.iso(sig.created_at),
        'obrigatorio': bool(doc.assinatura_obrigatoria),
    }


def _detalhe(request, sig):
    doc = sig.document
    proprio = sig.user_id == request.user.pk
    base = _resumo(sig)
    base.update({
        'descricao': doc.description or None,
        'de_outra_pessoa': None if proprio else dados.pessoa(request, sig.user),
        # document_detail.html: can_sign = dono e ainda não assinou (views.py:133).
        'pode_assinar': proprio and not sig.is_signed and doc.is_visible,
        'assinatura': ({
            'em': dados.iso(sig.signed_at),
            'ip': sig.signature_ip,
            'hash': (sig.signature_hash[:40] + '…') if sig.signature_hash else None,
            'imagem': sig.signature_image or None,
        } if sig.is_signed else None),
        'pdf': link(request, 'documentos', sig.pk) if (doc.pdf_file and doc.pdf_file.name) else None,
        'pdf_assinado': link(request, 'documentos_assinado', sig.pk) if sig.is_signed else None,
        'caminho_web': f'/documentos/{sig.pk}/',
    })
    return base


@api_view(['GET'])
@authentication_classes(_AUTH)
@permission_classes(_PERM)
def lista(request):
    """?situacao=pendentes|assinados (o ?status=pending|signed da web)."""
    base = (DocumentSignature.objects
            .filter(user=request.user, document__is_visible=True)
            .select_related('document', 'document__category')
            .order_by('-created_at'))
    situacao = (request.query_params.get('situacao') or '').strip()
    itens = base
    if situacao == 'pendentes':
        itens = base.filter(signed_at__isnull=True)
    elif situacao == 'assinados':
        itens = base.filter(signed_at__isnull=False)
    itens = list(itens)
    categorias = {}
    for sig in itens:
        cat = sig.document.category
        if cat and cat.pk not in categorias:
            categorias[cat.pk] = _categoria(cat)
    # Os contadores contam só o que aparece na lista (documento liberado).
    return Response({
        'pendentes': base.filter(signed_at__isnull=True).count(),
        'assinados': base.filter(signed_at__isnull=False).count(),
        'categorias': sorted(categorias.values(), key=lambda c: c['nome']),
        'documentos': [_resumo(s) for s in itens],
        'administra': web.pode_administrar(request.user),
    })


@api_view(['GET'])
@authentication_classes(_AUTH)
@permission_classes(_PERM)
def detalhe(request, pk):
    return Response(_detalhe(request, _assinatura(request.user, pk)))


@api_view(['POST'])
@authentication_classes(_AUTH)
@permission_classes(_PERM)
def assinar(request, pk):
    """Assina com a função da web (documentos/views.py:224): corpo {signature: data:image/png;base64,…}."""
    _assinatura(request.user, pk)                  # quem não vê recebe 404, não 403
    resposta = web.api_sign_document(request._request, pk)
    try:
        corpo = json.loads(resposta.content or b'{}')
    except ValueError:
        corpo = {}
    if resposta.status_code != 200 or not corpo.get('success'):
        return Response({'erro': corpo.get('error') or 'Não foi possível assinar.'},
                        status=resposta.status_code if resposta.status_code >= 400 else 400)
    return Response(_detalhe(request, _assinatura(request.user, pk)))


urlpatterns = [
    path('', lista, name='m_documentos'),
    path('<int:pk>/', detalhe, name='m_documentos_detalhe'),
    path('<int:pk>/assinar/', assinar, name='m_documentos_assinar'),
]


# ── PDF por link assinado ────────────────────────────────────────────────────

def _pdf_da_web(view, user, pk):
    """Chama a view de PDF do portal como o dono do link e devolve os bytes (ou 404)."""
    requisicao = RequestFactory().get('/')
    requisicao.user = user
    requisicao._messages = CookieStorage(requisicao)   # a view usa messages nos desvios
    resposta = view(requisicao, pk)
    if resposta.status_code != 200 or 'pdf' not in (resposta.get('Content-Type') or ''):
        raise Http404('PDF indisponível.')
    return io.BytesIO(resposta.content)


@registrar('documentos')
def _pdf(user, obj_id):
    sig = _assinatura(user, int(obj_id))
    return _pdf_da_web(web.document_pdf, user, sig.pk), f'documento_{sig.document_id}.pdf', 'application/pdf'


@registrar('documentos_assinado')
def _pdf_assinado(user, obj_id):
    sig = _assinatura(user, int(obj_id))
    nome = f'documento_assinado_{sig.document_id}.pdf'
    return _pdf_da_web(web.document_signed_pdf, user, sig.pk), nome, 'application/pdf'
