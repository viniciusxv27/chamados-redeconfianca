"""Telas e ações do feed da rede (ver social/models.py). As ações respondem JSON com o HTML pronto."""
import json

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_POST

from . import servicos
from .models import Comentario, Curtida, Post


def _html_do_post(request, post):
    post = servicos.com_ultimos_comentarios(
        [servicos.anotar_feed(Post.objects.filter(pk=post.pk), request.user).get()])[0]
    return render_to_string('social/_post.html', {'p': post, 'user': request.user}, request=request)


def _corpo(request):
    try:
        return json.loads(request.body or b'{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


@login_required
def feed(request):
    posts, tem_mais = servicos.pagina_do_feed(request.user)
    return render(request, 'social/feed.html', {'posts': posts, 'tem_mais': tem_mais,
                                                'ultimo': posts[-1].pk if posts else ''})


@login_required
@require_GET
def mais(request):
    """Próxima página do feed (rolagem infinita): o HTML dos posts e se ainda há mais."""
    antes = request.GET.get('antes') or ''
    posts, tem_mais = servicos.pagina_do_feed(request.user, antes=int(antes) if antes.isdigit() else None)
    html = render_to_string('social/_lista.html', {'posts': posts, 'user': request.user}, request=request)
    return JsonResponse({'ok': True, 'html': html, 'tem_mais': tem_mais,
                         'ultimo': posts[-1].pk if posts else None})


@login_required
def post(request, post_id):
    """Um post sozinho (o link do aviso de comentário), com todos os comentários abertos."""
    p = servicos.anotar_feed(Post.objects.filter(pk=post_id, ativo=True), request.user).first()
    if p is None:
        raise Http404
    p = servicos.com_ultimos_comentarios([p])[0]
    return render(request, 'social/detalhe.html', {
        'p': p, 'comentarios': p.comentarios.filter(ativo=True).select_related('autor'), 'abrir_todos': True})


@login_required
@require_POST
def publicar(request):
    arquivo = request.FILES.get('midia')
    try:
        tipo, largura, altura = servicos.conferir_midia(arquivo)
    except servicos.MidiaInvalida as erro:
        return JsonResponse({'ok': False, 'erro': str(erro)}, status=400)
    # A tela manda a medida lida no navegador (vídeo, HEIC); a do servidor vale quando existe.
    def _medida(nome, lida):
        if lida:
            return lida
        texto = (request.POST.get(nome) or '').strip()
        return int(texto) if texto.isdigit() and 0 < int(texto) < 20000 else None
    novo = Post.objects.create(
        autor=request.user, midia=arquivo, tipo=tipo, largura=_medida('largura', largura),
        altura=_medida('altura', altura), legenda=(request.POST.get('legenda') or '').strip()[:2200])
    return JsonResponse({'ok': True, 'id': novo.pk, 'html': _html_do_post(request, novo)})


@login_required
@require_POST
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
    return JsonResponse({'ok': True, 'curti': curti, 'total': p.total_curtidas})


@login_required
@require_POST
def comentar(request, post_id):
    p = get_object_or_404(Post.objects.select_related('autor'), pk=post_id, ativo=True)
    corpo = _corpo(request)
    texto = str((corpo or {}).get('texto') or '').strip()[:1000]
    if not texto:
        return JsonResponse({'ok': False, 'erro': 'Escreva o comentário.'}, status=400)
    c = Comentario.objects.create(post=p, autor=request.user, texto=texto)
    Post.objects.filter(pk=p.pk).update(total_comentarios=F('total_comentarios') + 1)
    p.refresh_from_db(fields=['total_comentarios'])
    servicos.avisar_comentario(c)
    html = render_to_string('social/_comentario.html', {'c': c, 'p': p, 'user': request.user}, request=request)
    return JsonResponse({'ok': True, 'html': html, 'total': p.total_comentarios})


@login_required
@require_GET
def comentarios(request, post_id):
    """Todos os comentários do post (o "ver todos")."""
    p = get_object_or_404(Post, pk=post_id, ativo=True)
    lista = p.comentarios.filter(ativo=True).select_related('autor')
    return HttpResponse(render_to_string('social/_comentarios.html', {'comentarios': lista, 'p': p, 'user': request.user},
                                         request=request))


@login_required
@require_POST
def apagar(request, post_id):
    p = get_object_or_404(Post, pk=post_id, ativo=True)
    if not servicos.pode_apagar_post(request.user, p):
        return JsonResponse({'ok': False, 'erro': 'Só quem publicou apaga.'}, status=403)
    Post.objects.filter(pk=p.pk).update(ativo=False, apagado_por=request.user)
    return JsonResponse({'ok': True})


@login_required
@require_POST
def apagar_comentario(request, comentario_id):
    c = get_object_or_404(Comentario.objects.select_related('post'), pk=comentario_id, ativo=True)
    if not servicos.pode_apagar_comentario(request.user, c):
        return JsonResponse({'ok': False, 'erro': 'Você não pode apagar este comentário.'}, status=403)
    Comentario.objects.filter(pk=c.pk).update(ativo=False)
    Post.objects.filter(pk=c.post_id, total_comentarios__gt=0).update(total_comentarios=F('total_comentarios') - 1)
    total = Post.objects.filter(pk=c.post_id).values_list('total_comentarios', flat=True).first() or 0
    return JsonResponse({'ok': True, 'total': total})
