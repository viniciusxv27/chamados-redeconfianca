"""Regras do feed da rede: o que entra, quem apaga e como o feed é montado (ver social/models.py)."""
import os

from django.db.models import Exists, OuterRef, Window
from django.db.models.functions import RowNumber

POR_PAGINA = 8
ULTIMOS_COMENTARIOS = 3
LIMITE_IMAGEM = 20 * 1024 * 1024
LIMITE_VIDEO = 100 * 1024 * 1024
EXT_IMAGEM = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.heic', '.heif'}
EXT_VIDEO = {'.mp4', '.mov', '.m4v', '.webm', '.3gp'}


class MidiaInvalida(Exception):
    pass


def e_superadmin(user):
    return bool(user and user.is_authenticated and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def pode_apagar_post(user, post):
    return post.autor_id == user.pk or e_superadmin(user)


def pode_apagar_comentario(user, comentario):
    return comentario.autor_id == user.pk or comentario.post.autor_id == user.pk or e_superadmin(user)


def _tamanho_mb(n):
    return f'{n // (1024 * 1024)} MB'


def conferir_midia(arquivo):
    """(tipo, largura, altura) do arquivo enviado, ou MidiaInvalida com a mensagem para a tela.

    Confere a extensão, o tamanho e o começo do arquivo (para um .exe renomeado não passar por foto).
    """
    from .models import Post
    if not arquivo:
        raise MidiaInvalida('Escolha uma foto ou um vídeo.')
    extensao = os.path.splitext(arquivo.name or '')[1].lower()
    tipo_mime = (getattr(arquivo, 'content_type', '') or '').lower()
    if extensao in EXT_IMAGEM or (not extensao and tipo_mime.startswith('image/')):
        tipo, limite = Post.IMAGEM, LIMITE_IMAGEM
    elif extensao in EXT_VIDEO or (not extensao and tipo_mime.startswith('video/')):
        tipo, limite = Post.VIDEO, LIMITE_VIDEO
    else:
        raise MidiaInvalida('Envie uma foto (JPG, PNG, WEBP, HEIC) ou um vídeo (MP4, MOV, WEBM).')
    if arquivo.size > limite:
        raise MidiaInvalida(f'Arquivo grande demais: o limite é {_tamanho_mb(limite)} para '
                            f'{"foto" if tipo == Post.IMAGEM else "vídeo"}.')
    if arquivo.size == 0:
        raise MidiaInvalida('O arquivo chegou vazio. Tente de novo.')

    cabeca = arquivo.read(64)
    arquivo.seek(0)
    largura = altura = None
    if tipo == Post.IMAGEM:
        heic = cabeca[4:8] == b'ftyp' and cabeca[8:12] in (b'heic', b'heix', b'mif1', b'msf1', b'heif', b'hevc')
        if not heic:
            try:
                from PIL import Image
                with Image.open(arquivo) as img:
                    largura, altura = img.size
                    img.verify()
            except Exception:  # noqa: BLE001 — qualquer erro do Pillow é "não é imagem"
                raise MidiaInvalida('Não consegui abrir essa foto. Tente outra.')
            finally:
                arquivo.seek(0)
    else:
        mp4 = cabeca[4:8] == b'ftyp'
        webm = cabeca[:4] == b'\x1a\x45\xdf\xa3'
        if not (mp4 or webm):
            raise MidiaInvalida('Não reconheci esse vídeo. Use MP4, MOV ou WEBM.')
    return tipo, largura, altura


def anotar_feed(qs, user):
    from .models import Curtida
    return (qs.select_related('autor', 'autor__sector')
            .annotate(curti=Exists(Curtida.objects.filter(post=OuterRef('pk'), user=user))))


def com_ultimos_comentarios(posts):
    """Põe em cada post ``ultimos`` (os 3 comentários mais recentes, do mais antigo ao mais novo)."""
    from .models import Comentario
    posts = list(posts)
    if not posts:
        return posts
    ids = [p.pk for p in posts]
    linhas = (Comentario.objects.filter(post_id__in=ids, ativo=True)
              .annotate(ordem=Window(RowNumber(), partition_by='post_id', order_by=('-criado_em', '-id')))
              .filter(ordem__lte=ULTIMOS_COMENTARIOS)
              .select_related('autor'))
    por_post = {}
    for c in linhas:
        por_post.setdefault(c.post_id, []).append(c)
    for p in posts:
        p.ultimos = sorted(por_post.get(p.pk, []), key=lambda c: (c.criado_em, c.pk))
    return posts


def pagina_do_feed(user, antes=None, limite=POR_PAGINA):
    """(posts, tem_mais). ``antes`` = id do último post já na tela (o feed anda para trás pelo id)."""
    from .models import Post
    qs = anotar_feed(Post.objects.filter(ativo=True), user).order_by('-id')
    if antes:
        qs = qs.filter(id__lt=antes)
    posts = list(qs[:limite + 1])
    tem_mais = len(posts) > limite
    return com_ultimos_comentarios(posts[:limite]), tem_mais


def avisar_comentario(comentario):
    """Sino (e push no app) para o dono do post quando outra pessoa comenta."""
    post = comentario.post
    if post.autor_id == comentario.autor_id:
        return
    try:
        from django.urls import reverse

        from core.models import Notification
        nome = comentario.autor.get_full_name() or comentario.autor.username
        trecho = comentario.texto if len(comentario.texto) <= 80 else comentario.texto[:77] + '…'
        Notification.objects.create(
            user=post.autor, title='Novo comentário na sua publicação', notification_type='SYSTEM',
            message=f'{nome}: "{trecho}"', related_object_id=post.pk,
            related_url=reverse('social:post', args=[post.pk]))
    except Exception:  # noqa: BLE001 — o aviso não segura o comentário
        import logging
        logging.getLogger(__name__).warning('Feed: aviso de comentário não criado', exc_info=True)
