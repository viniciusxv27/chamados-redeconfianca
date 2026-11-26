"""Feed da rede no app (/social/ do portal, 25/11/2026): fotos e vídeos com curtidas e comentários.

As regras são as do portal (social/servicos.py), chamadas daqui: qualquer pessoa logada vê e posta;
quem postou e o SUPERADMIN apagam o post; o comentário sai pelo autor, pelo dono do post ou pelo
SUPERADMIN; apagar é desativar. O aviso de comentário (sino e push) é o mesmo do portal.
"""
from django.db import IntegrityError, transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.urls import path
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, parser_classes, permission_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from social import servicos
from social.models import Comentario, Curtida, Post

from . import dados
from .autenticacao import TokenMobileAuthentication
from .permissoes import SemBloqueio


def _api(metodos=('GET',)):
    def decorar(funcao):
        funcao = parser_classes([JSONParser, MultiPartParser, FormParser])(funcao)
        funcao = permission_classes([SemBloqueio])(funcao)
        funcao = authentication_classes([TokenMobileAuthentication])(funcao)
        return api_view(list(metodos))(funcao)
    return decorar


def _erro(mensagem, codigo=status.HTTP_400_BAD_REQUEST):
    return Response({'erro': mensagem}, status=codigo)


def _comentario(request, c, post):
    return {
        'id': c.pk,
        'autor': dados.pessoa(request, c.autor),
        'texto': c.texto,
        'criado_em': dados.iso(c.criado_em),
        'pode_apagar': bool(c.autor_id == request.user.pk or post.autor_id == request.user.pk
                            or servicos.e_superadmin(request.user)),
    }


def _post(request, p, comentarios=None):
    lista = comentarios if comentarios is not None else getattr(p, 'ultimos', [])
    return {
        'id': p.pk,
        'autor': dados.pessoa(request, p.autor),
        'tipo': p.tipo,                                   # IMAGEM | VIDEO
        'midia': dados.url_absoluta(request, p.midia),
        'largura': p.largura,
        'altura': p.altura,
        'proporcao': p.proporcao,                         # altura/largura em % (já limitada como no portal)
        'legenda': p.legenda,
        'curtidas': p.total_curtidas,
        'curti': bool(getattr(p, 'curti', False)),
        'total_comentarios': p.total_comentarios,
        'comentarios': [_comentario(request, c, p) for c in lista],
        'criado_em': dados.iso(p.criado_em),
        'pode_apagar': servicos.pode_apagar_post(request.user, p),
    }


def _um_post(request, post_id):
    p = servicos.anotar_feed(Post.objects.filter(pk=post_id, ativo=True), request.user).first()
    if p is None:
        return None
    return servicos.com_ultimos_comentarios([p])[0]


@_api()
def feed(request):
    """Uma página do feed (8 posts com os 3 últimos comentários). ``antes`` = id do último já na tela."""
    antes = str(request.query_params.get('antes') or '')
    try:
        limite = min(max(int(request.query_params.get('limite') or servicos.POR_PAGINA), 1), 20)
    except ValueError:
        limite = servicos.POR_PAGINA
    posts, tem_mais = servicos.pagina_do_feed(request.user, antes=int(antes) if antes.isdigit() else None,
                                              limite=limite)
    return Response({'resultados': [_post(request, p) for p in posts], 'tem_mais': tem_mais,
                     'ultimo': posts[-1].pk if posts else None})


@_api()
def post(request, post_id):
    """Um post com todos os comentários (o link do aviso de comentário)."""
    p = _um_post(request, post_id)
    if p is None:
        return _erro('Esta publicação foi apagada.', status.HTTP_404_NOT_FOUND)
    todos = list(p.comentarios.filter(ativo=True).select_related('autor'))
    return Response(_post(request, p, comentarios=todos))


@_api(['POST'])
def publicar(request):
    arquivo = request.FILES.get('midia')
    try:
        tipo, largura, altura = servicos.conferir_midia(arquivo)
    except servicos.MidiaInvalida as erro:
        return _erro(str(erro))

    # O app manda a medida lida no celular (vídeo, HEIC); a do servidor vale quando existe.
    def _medida(nome, lida):
        if lida:
            return lida
        texto = str(request.data.get(nome) or '').strip()
        return int(texto) if texto.isdigit() and 0 < int(texto) < 20000 else None

    novo = Post.objects.create(
        autor=request.user, midia=arquivo, tipo=tipo, largura=_medida('largura', largura),
        altura=_medida('altura', altura), legenda=str(request.data.get('legenda') or '').strip()[:2200])
    return Response(_post(request, _um_post(request, novo.pk)), status=status.HTTP_201_CREATED)


@_api(['POST'])
def curtir(request, post_id):
    p = get_object_or_404(Post, pk=post_id, ativo=True)
    apagadas, _ = Curtida.objects.filter(post=p, user=request.user).delete()
    if apagadas:
        Post.objects.filter(pk=p.pk, total_curtidas__gt=0).update(total_curtidas=F('total_curtidas') - 1)
        curti = False
    else:
        try:
            with transaction.atomic():
                Curtida.objects.create(post=p, user=request.user)
            Post.objects.filter(pk=p.pk).update(total_curtidas=F('total_curtidas') + 1)
        except IntegrityError:
            pass                                  # dois toques ao mesmo tempo: a curtida já existe
        curti = True
    p.refresh_from_db(fields=['total_curtidas'])
    return Response({'curti': curti, 'curtidas': p.total_curtidas})


@_api(['POST'])
def comentar(request, post_id):
    p = get_object_or_404(Post.objects.select_related('autor'), pk=post_id, ativo=True)
    texto = str(request.data.get('texto') or '').strip()[:1000]
    if not texto:
        return _erro('Escreva o comentário.')
    c = Comentario.objects.create(post=p, autor=request.user, texto=texto)
    Post.objects.filter(pk=p.pk).update(total_comentarios=F('total_comentarios') + 1)
    p.refresh_from_db(fields=['total_comentarios'])
    servicos.avisar_comentario(c)
    return Response({'comentario': _comentario(request, c, p), 'total_comentarios': p.total_comentarios},
                    status=status.HTTP_201_CREATED)


@_api(['POST'])
def apagar(request, post_id):
    p = get_object_or_404(Post, pk=post_id, ativo=True)
    if not servicos.pode_apagar_post(request.user, p):
        return _erro('Só quem publicou apaga.', status.HTTP_403_FORBIDDEN)
    Post.objects.filter(pk=p.pk).update(ativo=False, apagado_por=request.user)
    return Response({'ok': True})


@_api(['POST'])
def apagar_comentario(request, comentario_id):
    c = get_object_or_404(Comentario.objects.select_related('post'), pk=comentario_id, ativo=True)
    if not servicos.pode_apagar_comentario(request.user, c):
        return _erro('Você não pode apagar este comentário.', status.HTTP_403_FORBIDDEN)
    Comentario.objects.filter(pk=c.pk).update(ativo=False)
    Post.objects.filter(pk=c.post_id, total_comentarios__gt=0).update(total_comentarios=F('total_comentarios') - 1)
    total = Post.objects.filter(pk=c.post_id).values_list('total_comentarios', flat=True).first() or 0
    return Response({'ok': True, 'total_comentarios': total})


urlpatterns = [
    path('', feed, name='social_feed'),
    path('publicar/', publicar, name='social_publicar'),
    path('<int:post_id>/', post, name='social_post'),
    path('<int:post_id>/curtir/', curtir, name='social_curtir'),
    path('<int:post_id>/comentar/', comentar, name='social_comentar'),
    path('<int:post_id>/apagar/', apagar, name='social_apagar'),
    path('comentario/<int:comentario_id>/apagar/', apagar_comentario, name='social_apagar_comentario'),
]
