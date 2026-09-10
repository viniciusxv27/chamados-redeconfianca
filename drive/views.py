"""Telas do módulo Drive — espelho do Google Drive da empresa.

Toda view valida a permissão NO SERVIDOR antes de qualquer leitura/escrita
(RNF01/02). O acesso por id resolve a cadeia de pastas até um setor autorizado
(RNF05): a interface só esconde botões; quem decide é o motor de permissões.
"""
import io
import logging
import re
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import (HttpResponse, HttpResponseNotModified, JsonResponse, Http404,
                         StreamingHttpResponse)
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from communications.models import CommunicationGroup
from users.models import Sector

from . import audit
from . import gdrive
from . import visualizacao as vis
from . import permissions as perms
from .models import (DriveAuditLog, DriveConfig, DriveFavorite, DrivePermission,
                     SectorDriveMapping, HIERARQUIAS)

logger = logging.getLogger(__name__)
User = get_user_model()
FOLDER_MIME = gdrive.FOLDER_MIME
ORDEM = perms.ORDEM


# ─── utilitários ─────────────────────────────────────────────────────────────

def humano_bytes(n):
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return '—'
    for u in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or u == 'TB':
            return f'{n:.0f} {u}' if u == 'B' else f'{n:.1f} {u}'
        n /= 1024


def _previewavel(mime):
    return vis.tipo(mime) is not None


def _enriquecer(f):
    f['is_folder'] = f.get('mimeType') == FOLDER_MIME
    f['size_h'] = '' if f['is_folder'] else humano_bytes(f.get('size'))
    f['previewavel'] = (not f['is_folder']) and _previewavel(f.get('mimeType', ''))
    return f


CAMPOS_CONTEUDO = 'id,name,mimeType,md5Checksum,version,modifiedTime,size'
# Vídeo e áudio saem em pedaços deste tamanho; o player pede o próximo sozinho.
TRECHO_MIDIA = 4 * 1024 * 1024
SEM_FORMATO = 'Formato não disponível para este arquivo.'
CSP_SANDBOX = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"


def _texto_simples(conteudo, status):
    return HttpResponse(conteudo, status=status, content_type='text/plain; charset=utf-8')


def _como_texto(dados):
    try:
        return dados.decode('utf-8')
    except UnicodeDecodeError:
        return dados.decode('latin-1')


def _nome_pdf(nome):
    return (nome.rsplit('.', 1)[0] if '.' in nome else nome) + '.pdf'


def _mesma_versao(request, marca):
    """O navegador já tem esta versão? Aceita a ETag enfraquecida por proxy (W/) e lista."""
    pedido = request.headers.get('If-None-Match') or ''
    return any(p.strip().removeprefix('W/') == marca for p in pedido.split(','))


def _faixa(cabecalho, tamanho):
    """(início, fim) do primeiro intervalo de `Range: bytes=…`, ou None se não dá para atender."""
    if not cabecalho.startswith('bytes=') or tamanho <= 0:
        return None
    ini, _, fim = cabecalho[6:].split(',')[0].strip().partition('-')
    try:
        if not ini:                                   # bytes=-500: os últimos 500
            ultimos = int(fim)
            return (max(tamanho - ultimos, 0), tamanho - 1) if ultimos > 0 else None
        inicio, final = int(ini), (int(fim) if fim else tamanho - 1)
    except ValueError:
        return None
    if inicio >= tamanho or final < inicio:
        return None
    return inicio, min(final, tamanho - 1)


def _cabecalhos(resp, nome, anexo, marca):
    resp['Content-Disposition'] = f"{'attachment' if anexo else 'inline'}; filename*=UTF-8''{quote(nome)}"
    resp['X-Content-Type-Options'] = 'nosniff'
    resp['ETag'] = marca
    # Privado: fica só no navegador de quem abriu, e revalida em 5 minutos.
    resp['Cache-Control'] = 'private, max-age=300'
    if (resp.get('Content-Type') or '').split(';')[0].strip() in vis.EXECUTAVEIS:
        resp['Content-Security-Policy'] = CSP_SANDBOX
    return resp


def _servir_midia(request, file_id, meta, marca, auditoria):
    """Vídeo e áudio em trechos (Range), como os players pedem.

    Sem isso o Safari — e o app no iPhone — nem começa a tocar, e avançar o
    vídeo exigiria trazer o arquivo inteiro para a memória do servidor.
    """
    tamanho = int(meta.get('size') or 0)
    mime = meta.get('mimeType') or 'application/octet-stream'
    nome = meta.get('name') or 'arquivo'
    pedido = request.headers.get('Range') or ''
    if pedido:
        faixa = _faixa(pedido, tamanho)
        if faixa is None:
            resp = HttpResponse(status=416)
            resp['Content-Range'] = f'bytes */{tamanho}'
            return resp
        inicio = faixa[0]
        dados = gdrive.baixar_trecho(file_id, inicio, min(faixa[1], inicio + TRECHO_MIDIA - 1))
        if not dados:
            raise gdrive.DriveError('O Google devolveu um trecho vazio.')
        resp = HttpResponse(dados, status=206, content_type=mime)
        resp['Content-Range'] = f'bytes {inicio}-{inicio + len(dados) - 1}/{tamanho}'
    else:
        inicio = 0

        def pedacos():
            for de in range(0, tamanho, TRECHO_MIDIA):
                yield gdrive.baixar_trecho(file_id, de, min(de + TRECHO_MIDIA, tamanho) - 1)

        resp = StreamingHttpResponse(pedacos(), content_type=mime)
        resp['Content-Length'] = str(tamanho)
    if inicio == 0:
        # O player pede dezenas de pedaços; na auditoria entra só a abertura.
        audit.registrar(request.user, 'VIEW', request=request, file_id=file_id,
                        file_name=nome, **auditoria)
    resp['Accept-Ranges'] = 'bytes'
    return _cabecalhos(resp, nome, False, marca)


def _servir_conteudo(request, file_id, anexo, url_voltar, pode_baixar=True, **auditoria):
    """Bytes de um arquivo para ver no portal (inline) ou baixar (anexo).

    Quem chama já checou a permissão. Inline, cada tipo sai do jeito que o
    navegador mostra: Word, Excel, PowerPoint e Docs do Google como PDF
    (convertido pelo Google e guardado por versão); texto como texto puro;
    vídeo e áudio em trechos; PDF e imagem como são. No download, `?formato=`
    escolhe o formato.
    """
    formato = (request.GET.get('formato') or '').strip().lower()
    try:
        meta = gdrive.obter(file_id, fields=CAMPOS_CONTEUDO)
    except gdrive.DriveNaoConfigurado as e:
        return _drive_off(request, e)
    except gdrive.DriveError:
        raise Http404('Arquivo não encontrado.')

    mime = meta.get('mimeType') or ''
    tipo = vis.tipo(mime)
    nome = meta.get('name') or 'arquivo'
    marca = vis.etag(meta, f"{'dl' if anexo else 'ver'}:{formato}")
    midia = not anexo and tipo in ('video', 'audio') and int(meta.get('size') or 0) > 0

    # Já está no navegador e o arquivo não mudou: confirma sem mandar de novo.
    if not request.headers.get('Range') and _mesma_versao(request, marca):
        resp = HttpResponseNotModified()
        resp['ETag'] = marca
        resp['Cache-Control'] = 'private, max-age=300'
        return resp

    try:
        if midia:
            return _servir_midia(request, file_id, meta, marca, auditoria)
        if anexo and tipo == 'google':
            codigo = formato or vis.DOWNLOAD_PADRAO_GOOGLE.get(mime, 'pdf')
            alvo = vis.exportacao(mime, codigo)
            if not alvo:
                return _texto_simples(SEM_FORMATO, 400)
            mime_saida, ext = alvo
            dados = vis.pdf_da_previa(meta) if codigo == 'pdf' else gdrive.exportar(file_id, mime_saida)
            nome_saida = nome + ext
        elif anexo and formato in ('', 'original'):
            buf, nome_saida, mime_saida = gdrive.baixar(file_id)
            dados = buf.read()
        elif anexo and formato == 'pdf' and tipo == 'office':
            dados, mime_saida, nome_saida = vis.pdf_da_previa(meta), vis.PDF, _nome_pdf(nome)
        elif anexo:
            return _texto_simples(SEM_FORMATO, 400)
        elif tipo in ('office', 'google'):
            # Doc do Google não tem extensão no nome: "Ata 10.09" vira "Ata 10.09.pdf".
            nome_saida = _nome_pdf(nome) if tipo == 'office' else nome + '.pdf'
            dados, mime_saida = vis.pdf_da_previa(meta), vis.PDF
        elif tipo:
            buf, nome_saida, mime_saida = gdrive.baixar(file_id)
            dados = buf.read()
            if tipo == 'texto':
                # text/plain sempre: um .html enviado ao Drive não roda script no portal.
                dados, mime_saida = _como_texto(dados), 'text/plain; charset=utf-8'
        else:
            return _texto_simples('Pré-visualização indisponível para este tipo de arquivo.', 415)
    except gdrive.DriveNaoConfigurado as e:
        return _drive_off(request, e)
    except gdrive.DriveError as e:
        logger.warning('Conteúdo do Drive não gerado (%s, formato=%s): %s', file_id, formato, e)
        if midia:
            return _texto_simples('Não foi possível carregar o arquivo agora.', 502)
        if anexo:
            messages.error(request, 'Não foi possível gerar o arquivo neste formato agora. '
                                    'Tente de novo ou escolha outro formato.')
            return redirect(url_voltar)
        # Dentro do iframe: uma página curta que explica e oferece o download.
        return render(request, 'drive/_sem_previa.html',
                      {'url_baixar': f'{request.path}?dl=1' if pode_baixar else ''})

    audit.registrar(request.user, 'DOWNLOAD' if anexo else 'VIEW', request=request,
                    file_id=file_id, file_name=nome_saida, **auditoria)
    return _cabecalhos(HttpResponse(dados, content_type=mime_saida), nome_saida, anexo, marca)


def _deny(request, **kw):
    audit.registrar(request.user, DriveAuditLog.Acao.DENY, request=request, **kw)
    raise PermissionDenied('Sem acesso a este conteúdo no Drive.')


def _drive_off(request, exc=None):
    return render(request, 'drive/indisponivel.html', {
        'configurado': gdrive.configurado(),
        'erro': str(exc) if exc else '',
        'is_superadmin': perms.is_superadmin(request.user),
    })


def _valida_arquivo(cfg, up):
    ext = (up.name.rsplit('.', 1)[-1].lower() if '.' in up.name else '')
    permitidas = cfg.extensoes()
    if permitidas and ext not in permitidas:
        return f'extensão .{ext or "?"} não permitida'
    if up.size > cfg.max_file_bytes:
        return f'{humano_bytes(up.size)} — passa do limite de {cfg.max_file_mb} MB'
    return None


def _usuarios_do_setor(sector):
    return User.objects.filter(Q(sector=sector) | Q(sectors=sector), is_active=True).distinct()


def _notificar(mapping, cfg, novo=True, quantos=1, ator=None, folder_id=''):
    """RF37/38: avisa os usuários do setor sobre novo/atualizado documento."""
    if (novo and not cfg.notify_new) or (not novo and not cfg.notify_updated):
        return
    try:
        from core.models import NotificationMixin
        destinatarios = [u for u in _usuarios_do_setor(mapping.sector) if not ator or u.id != ator.id]
        if not destinatarios:
            return
        titulo = 'Novo documento no Drive' if novo else 'Documento atualizado no Drive'
        corpo = (f'{quantos} novo(s) documento(s) em {mapping.sector.name}.' if novo
                 else f'Um documento de {mapping.sector.name} foi atualizado.')
        NotificationMixin.create_notifications_for_users(
            users=destinatarios, title=titulo, message=corpo,
            notification_type='SYSTEM',
            related_url=reverse_browse(mapping.sector_id, folder_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning('Notificação do Drive não enviada: %s', exc)


def reverse_browse(sector_id, folder_id=''):
    from django.urls import reverse
    if folder_id:
        return reverse('drive:browse_folder', args=[sector_id, folder_id])
    return reverse('drive:browse', args=[sector_id])


# ─── landing ─────────────────────────────────────────────────────────────────

@login_required
def index(request):
    cfg = DriveConfig.get()
    setores = perms.sectors_visible(request.user)
    favoritos = list(DriveFavorite.objects.filter(user=request.user)[:12])

    recentes, vistos = [], set()
    for r in (DriveAuditLog.objects.filter(user=request.user, acao__in=['VIEW', 'DOWNLOAD'])
              .exclude(file_id='').order_by('-criado_em')[:60]):
        if r.file_id in vistos:
            continue
        vistos.add(r.file_id)
        recentes.append(r)
        if len(recentes) >= 8:
            break

    return render(request, 'drive/index.html', {
        'cfg': cfg, 'setores': setores, 'favoritos': favoritos, 'recentes': recentes,
        'is_superadmin': perms.is_superadmin(request.user),
        'e_gestor_algum': any(perms._e_gestor(request.user, m) for m in setores),
        'drive_ok': gdrive.configurado(),
    })


# ─── navegação ───────────────────────────────────────────────────────────────

@login_required
def browse(request, sector_id, folder_id=None):
    mapping = perms.mapping_por_setor(sector_id)
    if not mapping:
        messages.error(request, 'Este setor ainda não tem pasta no Drive configurada.')
        return redirect('drive:index')

    alvo = folder_id or mapping.folder_id
    nivel = perms.level_for_folder(request.user, mapping, alvo if folder_id else None)
    if nivel < ORDEM['VIEW']:
        _deny(request, sector=mapping.sector, folder_id=alvo, detalhe='browse')

    # RNF05: uma subpasta pedida precisa mesmo estar dentro do setor.
    if folder_id and folder_id != mapping.folder_id and not gdrive.dentro_de(folder_id, mapping.folder_id):
        _deny(request, sector=mapping.sector, folder_id=folder_id, detalhe='pasta fora do setor')

    try:
        itens, prox = gdrive.listar(alvo, page_token=request.GET.get('t') or None, page_size=60)
        trilha = gdrive.caminho(alvo, ate_root=mapping.folder_id)
    except gdrive.DriveNaoConfigurado as e:
        return _drive_off(request, e)
    except gdrive.DriveError as e:
        messages.error(request, f'Google Drive: {e}')
        return redirect('drive:index')

    favset = set(DriveFavorite.objects.filter(user=request.user).values_list('file_id', flat=True))
    itens = [_enriquecer(f) for f in itens]
    for f in itens:
        f['fav'] = f['id'] in favset

    ctx = {
        'mapping': mapping, 'sector': mapping.sector, 'folder_id': alvo,
        'itens': itens, 'prox': prox, 'trilha': trilha, 'nivel': nivel,
        'pode_download': nivel >= ORDEM['DOWNLOAD'],
        'pode_upload': nivel >= ORDEM['UPLOAD'], 'pode_editar': nivel >= ORDEM['EDIT'],
        'pode_excluir': nivel >= ORDEM['DELETE'], 'is_superadmin': perms.is_superadmin(request.user),
        'e_raiz': not folder_id or folder_id == mapping.folder_id,
    }
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return render(request, 'drive/_lista.html', ctx)
    return render(request, 'drive/browse.html', ctx)


# ─── arquivo: preview / conteúdo / versões ───────────────────────────────────

@login_required
def file_preview(request, file_id):
    mapping, nivel = perms.file_allowed(request.user, file_id)
    if not mapping or nivel < ORDEM['VIEW']:
        _deny(request, file_id=file_id, detalhe='preview')
    try:
        meta = _enriquecer(gdrive.obter(file_id))
    except gdrive.DriveNaoConfigurado as e:
        return _drive_off(request, e)
    except gdrive.DriveError:
        raise Http404('Arquivo não encontrado.')

    audit.registrar(request.user, 'VIEW', request=request, file_id=file_id,
                    file_name=meta.get('name', ''), sector=mapping.sector)
    return render(request, 'drive/preview.html', {
        'meta': meta, 'mapping': mapping, 'sector': mapping.sector, 'nivel': nivel,
        'fav': DriveFavorite.objects.filter(user=request.user, file_id=file_id).exists(),
        'visualizacao': vis.tipo(meta.get('mimeType')),
        'formatos': vis.formatos_de_download(meta),
        'pode_download': nivel >= ORDEM['DOWNLOAD'], 'pode_editar': nivel >= ORDEM['EDIT'],
        'pode_excluir': nivel >= ORDEM['DELETE'], 'is_superadmin': perms.is_superadmin(request.user),
    })


@login_required
@xframe_options_sameorigin
def file_content(request, file_id):
    """Serve os bytes: inline (preview, exige VER) ou anexo (download, exige DOWNLOAD).

    ``xframe_options_sameorigin`` libera o preview dentro do <iframe> na mesma
    origem (o portal recusa frames por padrão, como em /documentos/).
    """
    anexo = bool(request.GET.get('dl'))
    mapping, nivel = perms.file_allowed(request.user, file_id)
    if not mapping or nivel < (ORDEM['DOWNLOAD'] if anexo else ORDEM['VIEW']):
        _deny(request, file_id=file_id, detalhe='download' if anexo else 'inline')
    return _servir_conteudo(request, file_id, anexo, reverse('drive:file_preview', args=[file_id]),
                            pode_baixar=nivel >= ORDEM['DOWNLOAD'], sector=mapping.sector)


@login_required
def file_versions(request, file_id):
    mapping, nivel = perms.file_allowed(request.user, file_id)
    if not mapping or nivel < ORDEM['VIEW']:
        _deny(request, file_id=file_id, detalhe='versions')
    try:
        meta = gdrive.obter(file_id, fields='id,name,mimeType')
        revs = gdrive.revisoes(file_id)
    except gdrive.DriveError as e:
        messages.error(request, str(e))
        return redirect('drive:file_preview', file_id=file_id)
    n = len(revs)
    for i, r in enumerate(revs):
        r['num'] = i + 1
        r['size_h'] = humano_bytes(r.get('size'))
        r['atual'] = (i == n - 1)
    return render(request, 'drive/versions.html', {
        'meta': meta, 'revs': list(reversed(revs)), 'mapping': mapping, 'sector': mapping.sector,
        'pode_restaurar': nivel >= ORDEM['DELETE'], 'is_superadmin': perms.is_superadmin(request.user),
    })


@login_required
@require_POST
def version_restore(request, file_id):
    mapping, nivel = perms.file_allowed(request.user, file_id)
    if not mapping or nivel < ORDEM['DELETE']:
        _deny(request, file_id=file_id, detalhe='restore version')
    rev_id = (request.POST.get('rev') or '').strip()
    try:
        from googleapiclient.http import MediaIoBaseDownload
        svc = gdrive.service()
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, svc.revisions().get_media(fileId=file_id, revisionId=rev_id))
        done = False
        while not done:
            _, done = dl.next_chunk()
        buf.seek(0)
        meta = gdrive.obter(file_id, fields='id,name,mimeType')
        gdrive.nova_versao(file_id, buf, mimetype=meta.get('mimeType'))
    except Exception as e:  # noqa: BLE001
        messages.error(request, f'Não foi possível restaurar esta versão: {e}')
        return redirect('drive:file_versions', file_id=file_id)
    audit.registrar(request.user, 'RESTORE', request=request, file_id=file_id,
                    file_name=meta.get('name', ''), sector=mapping.sector, detalhe=f'revisão {rev_id}')
    messages.success(request, 'Versão restaurada como a mais recente.')
    return redirect('drive:file_versions', file_id=file_id)


# ─── escrita ─────────────────────────────────────────────────────────────────

@login_required
@require_POST
def upload(request, sector_id):
    mapping = perms.mapping_por_setor(sector_id)
    folder_id = (request.POST.get('folder_id') or '').strip() or (mapping.folder_id if mapping else '')
    if not mapping or not perms.can(request.user, mapping, 'upload', folder_id):
        _deny(request, sector=mapping.sector if mapping else None, folder_id=folder_id, detalhe='upload')
    if folder_id != mapping.folder_id and not gdrive.dentro_de(folder_id, mapping.folder_id):
        _deny(request, sector=mapping.sector, folder_id=folder_id, detalhe='upload fora do setor')

    cfg = DriveConfig.get()
    arquivos = request.FILES.getlist('arquivos') or request.FILES.getlist('arquivo')
    if not arquivos:
        return _resp(request, False, 'Nenhum arquivo enviado.', sector_id, folder_id)

    ok, erros = 0, []
    for up in arquivos:
        erro = _valida_arquivo(cfg, up)
        if erro:
            erros.append(f'{up.name}: {erro}')
            continue
        try:
            f = gdrive.enviar(up.name, up.content_type, up, folder_id)
            ok += 1
            audit.registrar(request.user, 'UPLOAD', request=request, file_id=f.get('id', ''),
                            file_name=up.name, sector=mapping.sector, folder_id=folder_id)
        except gdrive.DriveError as e:
            erros.append(f'{up.name}: {e}')
    if ok:
        _notificar(mapping, cfg, novo=True, quantos=ok, ator=request.user, folder_id=folder_id)
    msg = f'{ok} arquivo(s) enviado(s).' + (f' {len(erros)} com erro.' if erros else '')
    return _resp(request, ok > 0, msg, sector_id, folder_id, erros=erros, extra={'ok': ok})


@login_required
@require_POST
def mkdir(request, sector_id):
    mapping = perms.mapping_por_setor(sector_id)
    folder_id = (request.POST.get('folder_id') or '').strip() or (mapping.folder_id if mapping else '')
    nome = (request.POST.get('nome') or '').strip()
    if not mapping or not perms.can(request.user, mapping, 'mkdir', folder_id):
        _deny(request, sector=mapping.sector if mapping else None, folder_id=folder_id, detalhe='mkdir')
    if folder_id != mapping.folder_id and not gdrive.dentro_de(folder_id, mapping.folder_id):
        _deny(request, sector=mapping.sector, folder_id=folder_id, detalhe='mkdir fora do setor')
    if not nome:
        return _resp(request, False, 'Informe o nome da pasta.', sector_id, folder_id)
    try:
        f = gdrive.criar_pasta(nome, folder_id)
    except gdrive.DriveError as e:
        return _resp(request, False, f'Google Drive: {e}', sector_id, folder_id)
    audit.registrar(request.user, 'MKDIR', request=request, file_id=f.get('id', ''),
                    file_name=nome, sector=mapping.sector, folder_id=folder_id)
    return _resp(request, True, f'Pasta "{nome}" criada.', sector_id, folder_id)


@login_required
@require_POST
def file_rename(request, file_id):
    mapping, nivel = perms.file_allowed(request.user, file_id)
    if not mapping or nivel < ORDEM['EDIT']:
        _deny(request, file_id=file_id, detalhe='rename')
    nome = (request.POST.get('nome') or '').strip()
    if not nome:
        return _resp(request, False, 'Informe o novo nome.', mapping.sector_id)
    try:
        gdrive.renomear(file_id, nome)
    except gdrive.DriveError as e:
        return _resp(request, False, str(e), mapping.sector_id)
    audit.registrar(request.user, 'RENAME', request=request, file_id=file_id,
                    file_name=nome, sector=mapping.sector, detalhe='renomeado')
    return _resp(request, True, 'Renomeado.', mapping.sector_id, request.POST.get('folder_id', ''))


@login_required
@require_POST
def file_move(request, file_id):
    mapping, nivel = perms.file_allowed(request.user, file_id)
    if not mapping or nivel < ORDEM['EDIT']:
        _deny(request, file_id=file_id, detalhe='move')
    destino = (request.POST.get('destino') or '').strip()
    # RF11: mover só DENTRO do mesmo setor.
    if not destino or not gdrive.dentro_de(destino, mapping.folder_id):
        return _resp(request, False, 'Escolha uma pasta de destino dentro do mesmo setor.', mapping.sector_id)
    try:
        gdrive.mover(file_id, destino)
    except gdrive.DriveError as e:
        return _resp(request, False, str(e), mapping.sector_id)
    audit.registrar(request.user, 'MOVE', request=request, file_id=file_id, sector=mapping.sector,
                    folder_id=destino, detalhe='movido')
    return _resp(request, True, 'Movido.', mapping.sector_id, destino)


@login_required
@require_POST
def file_replace(request, file_id):
    """RF20: substitui o conteúdo gerando nova versão."""
    mapping, nivel = perms.file_allowed(request.user, file_id)
    if not mapping or nivel < ORDEM['EDIT']:
        _deny(request, file_id=file_id, detalhe='replace')
    up = request.FILES.get('arquivo')
    if not up:
        return _resp(request, False, 'Envie o arquivo da nova versão.', mapping.sector_id)
    erro = _valida_arquivo(DriveConfig.get(), up)
    if erro:
        return _resp(request, False, erro, mapping.sector_id)
    try:
        gdrive.nova_versao(file_id, up, mimetype=up.content_type)
    except gdrive.DriveError as e:
        return _resp(request, False, str(e), mapping.sector_id)
    audit.registrar(request.user, 'VERSION', request=request, file_id=file_id,
                    file_name=up.name, sector=mapping.sector, detalhe='nova versão')
    _notificar(mapping, DriveConfig.get(), novo=False, ator=request.user)
    return _resp(request, True, 'Nova versão enviada.', mapping.sector_id)


@login_required
@require_POST
def file_delete(request, file_id):
    """RF31: exclusão vai primeiro para a lixeira (do Google)."""
    mapping, nivel = perms.file_allowed(request.user, file_id)
    if not mapping or nivel < ORDEM['DELETE']:
        _deny(request, file_id=file_id, detalhe='delete')
    try:
        meta = gdrive.obter(file_id, fields='id,name')
        gdrive.para_lixeira(file_id, True)
    except gdrive.DriveError as e:
        return _resp(request, False, str(e), mapping.sector_id)
    audit.registrar(request.user, 'DELETE', request=request, file_id=file_id,
                    file_name=meta.get('name', ''), sector=mapping.sector, detalhe='para a lixeira')
    return _resp(request, True, 'Movido para a lixeira.', mapping.sector_id, request.POST.get('folder_id', ''))


def _resp(request, ok, msg, sector_id=None, folder_id='', erros=None, extra=None):
    """Resposta padrão: JSON para AJAX, redirect+mensagem para POST normal."""
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        payload = {'ok': ok, 'msg': msg, 'erros': erros or []}
        if extra:
            payload.update(extra)
        return JsonResponse(payload, status=200 if ok else 400)
    (messages.success if ok else messages.error)(request, msg)
    if sector_id:
        return redirect(reverse_browse(sector_id, folder_id))
    return redirect('drive:index')


# ─── favoritos / recentes ────────────────────────────────────────────────────

@login_required
@require_POST
def favorite_toggle(request, file_id):
    mapping, nivel = perms.file_allowed(request.user, file_id)
    if not mapping or nivel < ORDEM['VIEW']:
        _deny(request, file_id=file_id, detalhe='favoritar')
    fav = DriveFavorite.objects.filter(user=request.user, file_id=file_id).first()
    if fav:
        fav.delete()
        estado = False
    else:
        try:
            meta = gdrive.obter(file_id, fields='id,name,mimeType')
        except gdrive.DriveError:
            meta = {'name': '', 'mimeType': ''}
        DriveFavorite.objects.create(
            user=request.user, file_id=file_id, file_name=meta.get('name', ''),
            mime_type=meta.get('mimeType', ''), sector=mapping.sector)
        estado = True
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'favorito': estado})
    return redirect(request.META.get('HTTP_REFERER') or 'drive:index')


@login_required
def favoritos(request):
    itens = list(DriveFavorite.objects.filter(user=request.user).select_related('sector'))
    return render(request, 'drive/favoritos.html', {
        'itens': itens, 'is_superadmin': perms.is_superadmin(request.user)})


@login_required
def recentes(request):
    recentes, vistos = [], set()
    for r in (DriveAuditLog.objects.filter(user=request.user, acao__in=['VIEW', 'DOWNLOAD', 'UPLOAD'])
              .exclude(file_id='').select_related('sector').order_by('-criado_em')[:120]):
        if r.file_id in vistos:
            continue
        vistos.add(r.file_id)
        recentes.append(r)
        if len(recentes) >= 40:
            break
    return render(request, 'drive/recentes.html', {
        'itens': recentes, 'is_superadmin': perms.is_superadmin(request.user)})


# ─── busca ───────────────────────────────────────────────────────────────────

@login_required
def busca(request):
    termo = (request.GET.get('q') or '').strip()
    setor_id = (request.GET.get('setor') or '').strip()
    tipo = (request.GET.get('tipo') or '').strip()
    setores = perms.sectors_visible(request.user)
    roots = {m.folder_id: m for m in setores}
    if setor_id.isdigit():
        setores_f = [m for m in setores if m.sector_id == int(setor_id)]
        roots = {m.folder_id: m for m in setores_f}

    resultados = []
    if termo and roots:
        mime = {
            'pdf': 'application/pdf', 'img': 'image/', 'planilha': 'spreadsheet',
            'doc': 'document', 'zip': 'application/zip',
        }.get(tipo, '')
        try:
            achados, _ = gdrive.buscar(nome=termo, mime=(mime if mime.startswith('application/') else ''),
                                       page_size=80)
        except gdrive.DriveNaoConfigurado as e:
            return _drive_off(request, e)
        except gdrive.DriveError as e:
            messages.error(request, f'Google Drive: {e}')
            achados = []
        # RNF05: só devolve o que está sob um setor que o usuário pode ver.
        favset = set(DriveFavorite.objects.filter(user=request.user).values_list('file_id', flat=True))
        for f in achados:
            m, nivel = perms.file_allowed(request.user, f['id'])
            if not m or nivel < ORDEM['VIEW']:
                continue
            if tipo in ('img', 'planilha', 'doc') and mime not in (f.get('mimeType') or ''):
                continue
            f = _enriquecer(f)
            f['setor_nome'] = m.sector.name
            f['fav'] = f['id'] in favset
            resultados.append(f)
            if len(resultados) >= 60:
                break
    return render(request, 'drive/busca.html', {
        'termo': termo, 'setor_id': setor_id, 'tipo': tipo, 'setores': setores,
        'resultados': resultados, 'is_superadmin': perms.is_superadmin(request.user)})


# ─── lixeira (RF31–33) ───────────────────────────────────────────────────────

# A lixeira do Google é da CONTA inteira e pode ter milhares de itens; cada um
# precisa descobrir a que setor pertence. Tudo de uma vez travava o botão.
LIXEIRA_POR_PARTE = 25
LIXEIRA_PAGINAS_DRIVE = 4
LIXEIRA_TAMANHO_PAGINA_DRIVE = 50


@login_required
def lixeira(request):
    """Lixeira em partes: a tela abre na hora e os itens chegam aos poucos.

    Antes a view listava 200 itens e checava a permissão de um por um — cada
    checagem subindo a árvore de pastas com uma chamada ao Google por nível —
    antes de mostrar qualquer coisa. Agora a página abre sem falar com o Google
    e busca `?parte=1` em pedaços, com "Mostrar mais".
    """
    setores = perms.sectors_visible(request.user)
    meu = _exige_meu_drive(request)
    superadmin = perms.is_superadmin(request.user)
    if request.GET.get('parte') != '1':
        return render(request, 'drive/lixeira.html', {
            'tem_acesso': bool(setores or meu),
            'retencao': DriveConfig.get().trash_retention_days,
            'is_superadmin': superadmin})

    contexto = {'itens': [], 'prox': None, 'is_superadmin': superadmin}
    if not (setores or meu):
        return render(request, 'drive/_lixeira_itens.html', contexto)

    token = request.GET.get('t') or None
    pais = {}
    try:
        for _ in range(LIXEIRA_PAGINAS_DRIVE):
            achados, token = gdrive.listar_lixeira(page_token=token,
                                                   page_size=LIXEIRA_TAMANHO_PAGINA_DRIVE)
            acessos = perms.resolver_acessos(request.user, achados, mapeamentos=setores, pais=pais)
            for f in achados:
                m, nivel = acessos.get(f.get('id'), (None, 0))
                if m and nivel >= ORDEM['DELETE']:
                    f = _enriquecer(f)
                    f['setor_nome'] = m.sector.name
                    contexto['itens'].append(f)
                elif meu and not m:
                    # Excluído pelo Meu Drive: sem isso, a exclusão só se desfazia
                    # indo ao próprio Google Drive.
                    f = _enriquecer(f)
                    f['setor_nome'] = 'Meu Drive'
                    contexto['itens'].append(f)
            if len(contexto['itens']) >= LIXEIRA_POR_PARTE or not token:
                break
    except gdrive.DriveNaoConfigurado:
        contexto['erro'] = 'O Google Drive não está conectado.'
        return render(request, 'drive/_lixeira_itens.html', contexto, status=503)
    except gdrive.DriveError as e:
        logger.warning('Lixeira do Drive não carregada: %s', e)
        contexto['erro'] = 'Não foi possível carregar a lixeira agora.'
        return render(request, 'drive/_lixeira_itens.html', contexto, status=502)

    contexto['prox'] = token
    return render(request, 'drive/_lixeira_itens.html', contexto)


@login_required
@require_POST
def lixeira_restaurar(request, file_id):
    mapping, nivel = perms.file_allowed(request.user, file_id)
    do_meu_drive = not mapping and bool(_exige_meu_drive(request))
    if not do_meu_drive and (not mapping or nivel < ORDEM['DELETE']):
        _deny(request, file_id=file_id, detalhe='restaurar lixeira')
    try:
        meta = gdrive.obter(file_id, fields='id,name')
        gdrive.para_lixeira(file_id, False)
    except gdrive.DriveError as e:
        messages.error(request, str(e))
        return redirect('drive:lixeira')
    audit.registrar(request.user, 'RESTORE', request=request, file_id=file_id,
                    file_name=meta.get('name', ''), sector=mapping.sector if mapping else None,
                    detalhe='restaurado da lixeira' + (' (Meu Drive)' if do_meu_drive else ''))
    messages.success(request, 'Documento restaurado.')
    return redirect('drive:lixeira')


@login_required
@require_POST
def lixeira_excluir(request, file_id):
    """RF33: exclusão definitiva — só SUPERADMIN."""
    if not perms.is_superadmin(request.user):
        _deny(request, file_id=file_id, detalhe='excluir definitivo (não superadmin)')
    mapping, _ = perms.file_allowed(request.user, file_id)
    try:
        meta = gdrive.obter(file_id, fields='id,name')
        gdrive.excluir_definitivo(file_id)
    except gdrive.DriveError as e:
        messages.error(request, str(e))
        return redirect('drive:lixeira')
    audit.registrar(request.user, 'DELETE', request=request, file_id=file_id,
                    file_name=meta.get('name', ''), sector=mapping.sector if mapping else None,
                    detalhe='exclusão definitiva')
    messages.success(request, 'Documento excluído definitivamente.')
    return redirect('drive:lixeira')


# ─── administração (SUPERADMIN) ──────────────────────────────────────────────

def _exige_super(request):
    if not perms.is_superadmin(request.user):
        messages.error(request, 'Área exclusiva do SUPERADMIN.')
        return False
    return True


@login_required
def dashboard(request):
    if not _exige_super(request):
        return redirect('drive:index')
    logs = DriveAuditLog.objects
    total_downloads = logs.filter(acao='DOWNLOAD').count()
    total_uploads = logs.filter(acao='UPLOAD').count()
    mais_acessados = (logs.filter(acao__in=['VIEW', 'DOWNLOAD']).exclude(file_id='')
                      .values('file_id', 'file_name')
                      .annotate(n=Count('id')).order_by('-n')[:10])
    recentes = (logs.filter(acao='UPLOAD').exclude(file_id='')
                .select_related('sector', 'user').order_by('-criado_em')[:10])
    por_setor = (logs.filter(acao='UPLOAD').values('sector__name')
                 .annotate(n=Count('id')).order_by('-n'))
    acessos = perms.usuarios_com_acesso()

    espaco = ''
    try:
        if gdrive.configurado():
            sobre = gdrive.service().about().get(fields='storageQuota').execute()
            q = sobre.get('storageQuota', {})
            espaco = humano_bytes(q.get('usage'))
    except Exception:  # noqa: BLE001
        espaco = '—'

    return render(request, 'drive/dashboard.html', {
        'total_setores': SectorDriveMapping.objects.filter(ativo=True).count(),
        'total_usuarios_acesso': len(acessos),
        'total_downloads': total_downloads, 'total_uploads': total_uploads,
        'espaco': espaco, 'mais_acessados': mais_acessados, 'recentes': recentes,
        'por_setor': por_setor, 'is_superadmin': True,
    })


@login_required
def auditoria(request):
    setores = perms.sectors_visible(request.user)
    e_super = perms.is_superadmin(request.user)
    if not e_super and not any(perms._e_gestor(request.user, m) for m in setores):
        messages.error(request, 'Sem acesso à auditoria.')
        return redirect('drive:index')

    qs = DriveAuditLog.objects.select_related('user', 'sector').order_by('-criado_em')
    if not e_super:
        setor_ids = [m.sector_id for m in setores if perms._e_gestor(request.user, m)]
        qs = qs.filter(sector_id__in=setor_ids)
    acao = request.GET.get('acao', '').strip()
    if acao:
        qs = qs.filter(acao=acao)
    from django.core.paginator import Paginator
    pagina = Paginator(qs, 50).get_page(request.GET.get('page'))
    return render(request, 'drive/auditoria.html', {
        'pagina': pagina, 'acao': acao, 'acoes': DriveAuditLog.Acao.choices, 'is_superadmin': e_super})


@login_required
def acessos(request):
    if not _exige_super(request):
        return redirect('drive:index')
    dados = perms.usuarios_com_acesso()
    linhas = sorted(
        ({'user': u, 'setores': sorted(v['setores']), 'gestor_de': sorted(v['gestor_de'])}
         for u, v in dados.items()),
        key=lambda x: x['user'].full_name.lower())
    return render(request, 'drive/acessos.html', {'linhas': linhas, 'total': len(linhas), 'is_superadmin': True})


@login_required
def gestao_setores(request):
    if not _exige_super(request):
        return redirect('drive:index')
    if request.method == 'POST':
        sector_id = request.POST.get('sector')
        folder_id = (request.POST.get('folder_id') or '').strip()
        sector = get_object_or_404(Sector, pk=sector_id)
        mapping, _ = SectorDriveMapping.objects.get_or_create(sector=sector)
        mapping.folder_id = folder_id
        mapping.ativo = request.POST.get('ativo') == 'on'
        nome = ''
        if folder_id:
            try:
                nome = gdrive.obter(folder_id, fields='name').get('name', '')
            except gdrive.DriveError:
                nome = ''
        mapping.folder_name = nome
        mapping.save()
        mapping.managers.set(User.objects.filter(id__in=request.POST.getlist('managers'), is_active=True))
        messages.success(request, f'Setor {sector.name} configurado.')
        return redirect('drive:gestao_setores')

    mappings = (SectorDriveMapping.objects.select_related('sector')
                .prefetch_related('managers').order_by('sector__name'))
    ja = {m.sector_id for m in mappings}
    return render(request, 'drive/gestao_setores.html', {
        'mappings': mappings,
        'setores_livres': Sector.objects.exclude(id__in=ja).order_by('name'),
        'pessoas': User.objects.filter(is_active=True).order_by('first_name', 'last_name'),
        'is_superadmin': True,
    })


@login_required
def gestao_permissoes(request):
    if not _exige_super(request):
        return redirect('drive:index')
    if request.method == 'POST':
        mapping = get_object_or_404(SectorDriveMapping, pk=request.POST.get('mapping'))
        alvo = request.POST.get('alvo')
        p = DrivePermission(mapping=mapping, alvo=alvo, nivel=request.POST.get('nivel', 'VIEW'),
                            folder_id=(request.POST.get('folder_id') or '').strip(),
                            criado_por=request.user)
        if alvo == 'USER':
            p.target_user_id = request.POST.get('target_user') or None
        elif alvo == 'GROUP':
            p.target_group_id = request.POST.get('target_group') or None
        elif alvo == 'SECTOR':
            p.target_sector_id = request.POST.get('target_sector') or None
        elif alvo == 'HIERARCHY':
            p.target_hierarchy = request.POST.get('target_hierarchy') or ''
        if p.folder_id:
            try:
                p.folder_name = gdrive.obter(p.folder_id, fields='name').get('name', '')
            except gdrive.DriveError:
                p.folder_name = ''
        p.save()
        audit.registrar(request.user, 'PERM', request=request, sector=mapping.sector,
                        detalhe=f'{p.get_alvo_display()} · {p.get_nivel_display()}')
        messages.success(request, 'Permissão adicionada.')
        return redirect('drive:gestao_permissoes')

    return render(request, 'drive/gestao_permissoes.html', {
        'mappings': SectorDriveMapping.objects.filter(ativo=True).select_related('sector').order_by('sector__name'),
        'permissoes': (DrivePermission.objects.select_related(
            'mapping__sector', 'target_user', 'target_group', 'target_sector').order_by('-criado_em')),
        'grupos': CommunicationGroup.objects.all().order_by('name'),
        'setores': Sector.objects.all().order_by('name'),
        'pessoas': User.objects.filter(is_active=True).order_by('first_name', 'last_name'),
        'hierarquias': HIERARQUIAS, 'niveis': DrivePermission.Nivel.choices, 'is_superadmin': True,
    })


@login_required
@require_POST
def permissao_excluir(request, pk):
    if not _exige_super(request):
        return redirect('drive:index')
    p = get_object_or_404(DrivePermission, pk=pk)
    p.delete()
    messages.success(request, 'Permissão removida.')
    return redirect('drive:gestao_permissoes')


@login_required
def configuracao(request):
    if not _exige_super(request):
        return redirect('drive:index')
    cfg = DriveConfig.get()
    if request.method == 'POST':
        # Remover a credencial enviada.
        if request.POST.get('remover_credencial') == '1':
            if cfg.sa_json:
                try:
                    cfg.sa_json.delete(save=False)
                except Exception:  # noqa: BLE001
                    pass
            cfg.sa_json = None
            cfg.sa_client_email = ''
            cfg.save()
            gdrive.resetar()
            messages.success(request, 'Credencial removida.')
            return redirect('drive:configuracao')

        # Upload do JSON da conta de serviço (guardado em storage PRIVADO).
        arq = request.FILES.get('sa_json')
        if arq:
            import json as _json
            from django.core.files.base import ContentFile
            try:
                bruto = arq.read()
                info = _json.loads(bruto.decode('utf-8'))
            except Exception:  # noqa: BLE001
                messages.error(request, 'O arquivo enviado não é um JSON válido.')
                return redirect('drive:configuracao')
            if info.get('type') != 'service_account' or not info.get('private_key') or not info.get('client_email'):
                messages.error(request, 'Este JSON não parece a chave de uma conta de serviço do Google.')
                return redirect('drive:configuracao')
            cfg.sa_json.save('service_account.json', ContentFile(bruto), save=False)
            cfg.sa_client_email = info.get('client_email', '')

        cfg.impersonate_email = (request.POST.get('impersonate_email') or '').strip()

        # Credenciais do cliente OAuth. O segredo nunca volta preenchido para a
        # tela, então campo vazio significa "mantém o que está lá" — e não
        # "apague", que desconectaria a conta a cada salvamento.
        cid = (request.POST.get('oauth_client_id') or '').strip()
        if cid or request.POST.get('oauth_client_id') is not None:
            cfg.oauth_client_id = cid
        segredo = (request.POST.get('oauth_client_secret') or '').strip()
        if segredo:
            cfg.oauth_client_secret = segredo

        modo = (request.POST.get('modo') or '').strip()
        if modo in DriveConfig.Modo.values:
            # Só deixa ficar no modo conta própria se houver conta conectada:
            # senão a tela diria "conectado pela minha conta" com o Drive fora.
            if modo == DriveConfig.Modo.OAUTH and not cfg.oauth_refresh_token:
                messages.warning(
                    request, 'Conecte a sua conta Google antes de usar esse modo.')
            else:
                cfg.modo = modo

        cfg.ativo = request.POST.get('ativo') == 'on'
        cfg.shared_drive_id = (request.POST.get('shared_drive_id') or '').strip()
        cfg.allowed_extensions = (request.POST.get('allowed_extensions') or '').strip()
        for campo in ('max_file_mb', 'storage_cap_gb', 'trash_retention_days'):
            val = (request.POST.get(campo) or '').strip()
            if val.isdigit():
                setattr(cfg, campo, int(val))
        cfg.notify_new = request.POST.get('notify_new') == 'on'
        cfg.notify_updated = request.POST.get('notify_updated') == 'on'
        cfg.atualizado_por = request.user
        cfg.save()
        gdrive.resetar()   # a credencial/impersonação pode ter mudado
        messages.success(request, 'Configuração salva.' + (' Credencial atualizada.' if arq else ''))
        return redirect('drive:configuracao')

    ok, msg = gdrive.testar_conexao()
    return render(request, 'drive/configuracao.html', {
        'cfg': cfg, 'conexao_ok': ok, 'conexao_msg': msg,
        'sa_email': cfg.sa_client_email or _sa_email(), 'is_superadmin': True,
        # O endereço de retorno tem que ser colado igualzinho no Google Cloud.
        'redirect_uri': _redirect_uri(request),
        'tem_segredo': bool(cfg.oauth_client_secret),
        'modos': DriveConfig.Modo.choices})


# ─── Conectar a conta Google do dono (OAuth) ─────────────────────────────────
# Conta de serviço só vê o que foi compartilhado com ela. Para enxergar TODOS
# os arquivos de uma conta o Google exige delegação em todo o domínio, que só
# existe com Workspace. Sem Workspace, o caminho é este: o dono autoriza o
# portal uma vez e o portal passa a agir como ele.

CHAVE_STATE = 'drive_oauth_state'


# Hosts em que o Google aceita http:// — em qualquer outro ele exige https.
HOSTS_LOCAIS = ('localhost', '127.0.0.1', '[::1]', '0.0.0.0')


def _e_local(host):
    nome = (host or '').split(':')[0].lower()
    return nome in HOSTS_LOCAIS or nome.endswith('.localhost')


def _redirect_uri(request):
    """O endereço de retorno — precisa bater EXATAMENTE com o do Google Cloud.

    Montado a partir do host da requisição para funcionar igual em produção e
    em homologação, sem uma segunda configuração para manter em dia.

    O `https` é forçado fora de localhost porque o portal roda atrás de um
    proxy que termina o TLS: o Django recebe a requisição em http e montaria
    `http://…/callback/`, que o Google recusa duas vezes — ele só aceita https
    fora de localhost, e compara o URI recebido com o cadastrado letra por
    letra (era o `Erro 400: redirect_uri_mismatch`).
    """
    uri = request.build_absolute_uri(reverse('drive:oauth_callback'))
    if uri.startswith('http://') and not _e_local(request.get_host()):
        uri = 'https://' + uri[len('http://'):]
    return uri


@login_required
@require_POST
def oauth_conectar(request):
    """Manda o SUPERADMIN para a tela de consentimento do Google."""
    if not _exige_super(request):
        return redirect('drive:index')

    cfg = DriveConfig.get()
    if not (cfg.oauth_client_id and cfg.oauth_client_secret):
        messages.error(request, 'Preencha o ID e o segredo do cliente OAuth antes de conectar.')
        return redirect('drive:configuracao')

    # `state` amarra o retorno a ESTA sessão: sem ele, um link forjado poderia
    # fazer o navegador do superadmin trocar um código de outra conta.
    import secrets
    state = secrets.token_urlsafe(32)
    request.session[CHAVE_STATE] = state
    return redirect(gdrive.url_de_consentimento(
        cfg.oauth_client_id, _redirect_uri(request), state))


@login_required
def oauth_callback(request):
    """Volta do Google com o código e guarda o refresh token."""
    if not _exige_super(request):
        return redirect('drive:index')

    esperado = request.session.pop(CHAVE_STATE, None)
    recebido = request.GET.get('state')
    if not esperado or not recebido or not secrets_iguais(esperado, recebido):
        messages.error(request, 'A autorização não confere com esta sessão. Tente de novo.')
        return redirect('drive:configuracao')

    erro = request.GET.get('error')
    if erro:
        messages.error(request, f'Autorização cancelada no Google ({erro}).')
        return redirect('drive:configuracao')

    codigo = request.GET.get('code')
    if not codigo:
        messages.error(request, 'O Google não devolveu o código de autorização.')
        return redirect('drive:configuracao')

    cfg = DriveConfig.get()
    try:
        tokens = gdrive.trocar_codigo(
            codigo, cfg.oauth_client_id, cfg.oauth_client_secret, _redirect_uri(request))
    except gdrive.DriveError as exc:
        messages.error(request, str(exc))
        return redirect('drive:configuracao')

    cfg.oauth_refresh_token = tokens['refresh_token']
    cfg.modo = DriveConfig.Modo.OAUTH
    cfg.oauth_conectado_em = timezone.now()
    cfg.oauth_conectado_por = request.user
    cfg.save()
    gdrive.resetar()

    # Qual conta ficou conectada? Vem da própria API, não do que foi digitado.
    try:
        sobre = gdrive.service().about().get(fields='user(emailAddress)').execute()
        cfg.oauth_email = (sobre.get('user') or {}).get('emailAddress', '')
        cfg.save(update_fields=['oauth_email'])
    except Exception:  # noqa: BLE001
        pass

    audit.registrar(request.user, 'PERM', request,
                    detalhe=f'Conta Google conectada: {cfg.oauth_email or "?"}')
    messages.success(
        request,
        f'Conta {cfg.oauth_email or "Google"} conectada. O portal agora enxerga '
        f'todos os arquivos do seu Drive.')
    return redirect('drive:configuracao')


def secrets_iguais(a, b):
    """Comparação em tempo constante (o `state` é um segredo de sessão)."""
    import hmac
    return hmac.compare_digest(str(a), str(b))


@login_required
@require_POST
def oauth_desconectar(request):
    """Tira a autorização e volta para a conta de serviço."""
    if not _exige_super(request):
        return redirect('drive:index')

    cfg = DriveConfig.get()
    if cfg.oauth_refresh_token:
        gdrive.revogar(cfg.oauth_refresh_token)
    antiga = cfg.oauth_email
    cfg.oauth_refresh_token = ''
    cfg.oauth_email = ''
    cfg.oauth_conectado_em = None
    cfg.oauth_conectado_por = None
    cfg.modo = DriveConfig.Modo.SA
    cfg.save()
    gdrive.resetar()
    audit.registrar(request.user, 'PERM', request,
                    detalhe=f'Conta Google desconectada: {antiga or "?"}')
    messages.success(request, 'Conta Google desconectada.')
    return redirect('drive:configuracao')


# ─── Meu Drive: todas as funções de pastas e arquivos ────────────────────────
# Porta própria de propósito: o motor de permissões por setor protege o módulo
# para a empresa toda, e abrir uma exceção lá dentro para o dono da conta
# arriscaria vazar o Drive pessoal. Aqui tudo passa por `_exige_meu_drive`.

# Ids do Google são letras, números, "-" e "_". Validar antes de montar URL de
# redirect ou mandar para a API evita que um valor forjado vá parar em algum lugar.
ID_DRIVE = re.compile(r'^[A-Za-z0-9_-]{1,160}$')


def _id_valido(valor):
    return bool(valor) and bool(ID_DRIVE.match(valor))


def _exige_meu_drive(request):
    """SUPERADMIN + conta própria conectada. Devolve o cfg ou None."""
    if not perms.is_superadmin(request.user):
        return None
    cfg = DriveConfig.get()
    if not (cfg.usa_conta_propria and cfg.oauth_refresh_token):
        return None
    return cfg


def _enriquecer_meu(f, favset=()):
    """`_enriquecer` entendendo atalhos (shortcut).

    Atalho é muito comum no Drive pessoal (o "Adicionar ao Meu Drive" de algo
    compartilhado cria um). Tratado como arquivo comum, abrir um atalho de
    pasta tentava baixar o próprio atalho e dava erro.
    """
    f = _enriquecer(f)
    atalho = f.get('mimeType') == gdrive.SHORTCUT_MIME
    detalhe = (f.get('shortcutDetails') or {}) if atalho else {}
    mime_real = detalhe.get('targetMimeType') or f.get('mimeType', '')
    f['e_atalho'] = atalho
    f['alvo_id'] = detalhe.get('targetId') or f['id']
    f['icone_mime'] = mime_real
    f['is_folder'] = mime_real == FOLDER_MIME
    if f['is_folder']:
        f['size_h'] = ''
    f['previewavel'] = (not f['is_folder']) and _previewavel(mime_real)
    # Docs/Planilhas do Google não recebem arquivo como nova versão.
    f['aceita_versao'] = (not f['is_folder']) and not mime_real.startswith('application/vnd.google-apps.')
    f['fav'] = f['alvo_id'] in favset
    return f


def _favset(request):
    return set(DriveFavorite.objects.filter(user=request.user).values_list('file_id', flat=True))


def _url_meu_drive(folder_id=''):
    if folder_id and folder_id != gdrive.RAIZ_MEU_DRIVE and _id_valido(folder_id):
        return reverse('drive:meu_drive_folder', args=[folder_id])
    return reverse('drive:meu_drive')


def _pasta_do_post(request):
    fid = (request.POST.get('folder_id') or '').strip()
    return fid if _id_valido(fid) else gdrive.RAIZ_MEU_DRIVE


def _resp_meu(request, ok, msg, folder_id='', extra=None):
    """JSON para AJAX; redirect de volta para a pasta num POST normal."""
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        dados = {'ok': ok, 'msg': msg}
        dados.update(extra or {})
        return JsonResponse(dados, status=200 if ok else 400)
    (messages.success if ok else messages.error)(request, msg)
    return redirect(_url_meu_drive(folder_id))


def _trilha_meu_drive(folder_id):
    """[(id, nome)] da raiz até a pasta, sem repetir o próprio "Meu Drive"."""
    raiz = gdrive.id_da_raiz()
    return [(i, n) for i, n in gdrive.caminho(folder_id) if i != raiz]


@login_required
def meu_drive(request, folder_id=None):
    """Navega o Meu Drive inteiro da conta conectada (só SUPERADMIN)."""
    if not _exige_super(request):
        return redirect('drive:index')
    cfg = _exige_meu_drive(request)
    if not cfg:
        messages.error(request, 'Conecte a sua conta Google em Configuração para navegar o Meu Drive.')
        return redirect('drive:configuracao')
    if folder_id and not _id_valido(folder_id):
        raise Http404('Pasta inválida.')

    alvo = folder_id or gdrive.RAIZ_MEU_DRIVE
    ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    try:
        itens, prox = gdrive.listar(alvo, page_token=request.GET.get('t') or None, page_size=60)
        trilha = _trilha_meu_drive(alvo) if (folder_id and not ajax) else []
    except gdrive.DriveNaoConfigurado as e:
        return _drive_off(request, e)
    except gdrive.DriveError as e:
        messages.error(request, f'Google Drive: {e}')
        return redirect('drive:meu_drive' if folder_id else 'drive:index')

    favset = _favset(request)
    ctx = {
        'cfg': cfg, 'itens': [_enriquecer_meu(f, favset) for f in itens], 'prox': prox,
        'trilha': trilha, 'folder_id': alvo, 'e_raiz': not folder_id,
        'pai_id': trilha[-2][0] if len(trilha) > 1 else '',
        'is_superadmin': True,
    }
    if ajax:
        return render(request, 'drive/_lista_meu_drive.html', ctx)
    audit.registrar(request.user, 'VIEW', request, folder_id=alvo, detalhe='Meu Drive')
    return render(request, 'drive/meu_drive.html', ctx)


@login_required
def meu_drive_arquivo(request, file_id):
    """Preview de um arquivo do Meu Drive, com todas as ações dele."""
    if not _exige_meu_drive(request) or not _id_valido(file_id):
        _deny(request, file_id=file_id, detalhe='meu drive')
    try:
        meta = _enriquecer_meu(gdrive.obter(file_id), _favset(request))
    except gdrive.DriveNaoConfigurado as e:
        return _drive_off(request, e)
    except gdrive.DriveError:
        raise Http404('Arquivo não encontrado.')

    if meta['e_atalho'] and meta['alvo_id'] != file_id:
        destino = 'drive:meu_drive_folder' if meta['is_folder'] else 'drive:meu_drive_arquivo'
        return redirect(destino, meta['alvo_id'])
    if meta['is_folder']:
        return redirect('drive:meu_drive_folder', folder_id=file_id)

    audit.registrar(request.user, 'VIEW', request, file_id=file_id,
                    file_name=meta.get('name', ''), detalhe='Meu Drive')
    pai = (meta.get('parents') or [''])[0]
    return render(request, 'drive/meu_drive_arquivo.html', {
        'meta': meta, 'pai_id': pai if _id_valido(pai) else '', 'is_superadmin': True,
        'visualizacao': vis.tipo(meta.get('mimeType')),
        'formatos': vis.formatos_de_download(meta), 'pode_download': True})


@login_required
@xframe_options_sameorigin
def meu_drive_conteudo(request, file_id):
    """Conteúdo (inline para o preview, anexo para baixar) do Meu Drive."""
    if not _exige_meu_drive(request) or not _id_valido(file_id):
        _deny(request, file_id=file_id, detalhe='meu drive conteúdo')
    anexo = bool(request.GET.get('dl'))
    return _servir_conteudo(request, file_id, anexo,
                            reverse('drive:meu_drive_arquivo', args=[file_id]), detalhe='Meu Drive')


@login_required
@require_POST
def meu_drive_upload(request):
    if not _exige_meu_drive(request):
        _deny(request, detalhe='meu drive upload')
    pasta = _pasta_do_post(request)
    cfg = DriveConfig.get()
    arquivos = request.FILES.getlist('arquivos') or request.FILES.getlist('arquivo')
    if not arquivos:
        return _resp_meu(request, False, 'Nenhum arquivo enviado.', pasta)

    ok, erros = 0, []
    for up in arquivos:
        erro = _valida_arquivo(cfg, up)
        if erro:
            erros.append(f'{up.name}: {erro}')
            continue
        try:
            f = gdrive.enviar(up.name, up.content_type, up, pasta)
        except gdrive.DriveError as e:
            erros.append(f'{up.name}: {e}')
            continue
        ok += 1
        audit.registrar(request.user, 'UPLOAD', request, file_id=f.get('id', ''),
                        file_name=up.name, folder_id=pasta, detalhe='Meu Drive')

    msg = f'{ok} arquivo(s) enviado(s).'
    if erros:
        # Num POST normal a mensagem é tudo o que a pessoa vê: diz o porquê.
        msg += f' {len(erros)} com erro: ' + '; '.join(erros[:3])
    return _resp_meu(request, ok > 0, msg, pasta, extra={'enviados': ok, 'erros': erros})


@login_required
@require_POST
def meu_drive_nova_pasta(request):
    if not _exige_meu_drive(request):
        _deny(request, detalhe='meu drive nova pasta')
    pasta = _pasta_do_post(request)
    nome = (request.POST.get('nome') or '').strip()[:255]
    if not nome:
        return _resp_meu(request, False, 'Informe o nome da pasta.', pasta)
    try:
        f = gdrive.criar_pasta(nome, pasta)
    except gdrive.DriveError as e:
        return _resp_meu(request, False, f'Google Drive: {e}', pasta)
    audit.registrar(request.user, 'MKDIR', request, file_id=f.get('id', ''),
                    file_name=nome, folder_id=pasta, detalhe='Meu Drive')
    return _resp_meu(request, True, f'Pasta "{nome}" criada.', pasta)


@login_required
@require_POST
def meu_drive_renomear(request, file_id):
    if not _exige_meu_drive(request) or not _id_valido(file_id):
        _deny(request, file_id=file_id, detalhe='meu drive renomear')
    pasta = _pasta_do_post(request)
    nome = (request.POST.get('nome') or '').strip()[:255]
    if not nome:
        return _resp_meu(request, False, 'Informe o novo nome.', pasta)
    try:
        gdrive.renomear(file_id, nome)
    except gdrive.DriveError as e:
        return _resp_meu(request, False, f'Google Drive: {e}', pasta)
    audit.registrar(request.user, 'RENAME', request, file_id=file_id,
                    file_name=nome, detalhe='Meu Drive')
    return _resp_meu(request, True, f'Renomeado para "{nome}".', pasta)


@login_required
@require_POST
def meu_drive_mover(request, file_id):
    if not _exige_meu_drive(request) or not _id_valido(file_id):
        _deny(request, file_id=file_id, detalhe='meu drive mover')
    pasta = _pasta_do_post(request)
    destino = (request.POST.get('destino') or '').strip()
    if not _id_valido(destino):
        return _resp_meu(request, False, 'Escolha a pasta de destino.', pasta)
    # Pasta para dentro dela mesma (ou de uma subpasta sua) criaria um laço:
    # o Google recusa, mas com uma mensagem que ninguém entende.
    if destino == file_id or gdrive.dentro_de(destino, file_id):
        return _resp_meu(request, False, 'Uma pasta não pode ir para dentro dela mesma.', pasta)
    try:
        gdrive.mover(file_id, destino)
    except gdrive.DriveError as e:
        return _resp_meu(request, False, f'Google Drive: {e}', pasta)
    audit.registrar(request.user, 'MOVE', request, file_id=file_id,
                    folder_id=destino, detalhe='Meu Drive')
    return _resp_meu(request, True, 'Movido.', destino, extra={'destino': destino})


@login_required
def meu_drive_pastas(request):
    """Subpastas de uma pasta, para o seletor de destino do "Mover"."""
    if not _exige_meu_drive(request):
        return JsonResponse({'ok': False, 'msg': 'Sem acesso.'}, status=403)
    pai = (request.GET.get('pai') or '').strip() or gdrive.RAIZ_MEU_DRIVE
    if not _id_valido(pai):
        return JsonResponse({'ok': False, 'msg': 'Pasta inválida.'}, status=400)
    try:
        pastas, _ = gdrive.listar(pai, apenas_pastas=True, page_size=200)
        trilha = _trilha_meu_drive(pai) if pai != gdrive.RAIZ_MEU_DRIVE else []
    except gdrive.DriveError as e:
        return JsonResponse({'ok': False, 'msg': str(e)}, status=400)
    acima = ''
    if pai != gdrive.RAIZ_MEU_DRIVE:
        acima = trilha[-2][0] if len(trilha) > 1 else gdrive.RAIZ_MEU_DRIVE
    return JsonResponse({
        'ok': True, 'pai': pai, 'acima': acima,
        'nome': trilha[-1][1] if trilha else 'Meu Drive',
        'pastas': [{'id': p['id'], 'nome': p.get('name', '')} for p in pastas],
    })


@login_required
@require_POST
def meu_drive_nova_versao(request, file_id):
    if not _exige_meu_drive(request) or not _id_valido(file_id):
        _deny(request, file_id=file_id, detalhe='meu drive nova versão')
    up = request.FILES.get('arquivo')
    if not up:
        messages.error(request, 'Envie o arquivo da nova versão.')
        return redirect('drive:meu_drive_arquivo', file_id=file_id)
    erro = _valida_arquivo(DriveConfig.get(), up)
    if erro:
        messages.error(request, erro)
        return redirect('drive:meu_drive_arquivo', file_id=file_id)
    try:
        gdrive.nova_versao(file_id, up, mimetype=up.content_type)
    except gdrive.DriveError as e:
        messages.error(request, f'Google Drive: {e}')
        return redirect('drive:meu_drive_arquivo', file_id=file_id)
    audit.registrar(request.user, 'VERSION', request, file_id=file_id,
                    file_name=up.name, detalhe='Meu Drive')
    messages.success(request, 'Nova versão enviada. A anterior fica no histórico.')
    return redirect('drive:meu_drive_arquivo', file_id=file_id)


@login_required
@require_POST
def meu_drive_excluir(request, file_id):
    """Vai para a lixeira do Google — dá para restaurar em Drive → Lixeira."""
    if not _exige_meu_drive(request) or not _id_valido(file_id):
        _deny(request, file_id=file_id, detalhe='meu drive excluir')
    pasta = _pasta_do_post(request)
    try:
        meta = gdrive.obter(file_id, fields='id,name,parents')
        gdrive.para_lixeira(file_id, True)
    except gdrive.DriveError as e:
        return _resp_meu(request, False, f'Google Drive: {e}', pasta)
    audit.registrar(request.user, 'DELETE', request, file_id=file_id,
                    file_name=meta.get('name', ''), detalhe='Meu Drive · para a lixeira')
    # Excluir a pasta que se está vendo: volta para a pasta de cima dela.
    if pasta == file_id:
        pasta = (meta.get('parents') or [''])[0]
    return _resp_meu(request, True, f'"{meta.get("name", "Item")}" foi para a lixeira.', pasta)


@login_required
@require_POST
def meu_drive_favoritar(request, file_id):
    if not _exige_meu_drive(request) or not _id_valido(file_id):
        _deny(request, file_id=file_id, detalhe='meu drive favoritar')
    fav = DriveFavorite.objects.filter(user=request.user, file_id=file_id).first()
    if fav:
        fav.delete()
        estado = False
    else:
        try:
            meta = gdrive.obter(file_id, fields='id,name,mimeType')
        except gdrive.DriveError:
            meta = {}
        DriveFavorite.objects.create(
            user=request.user, file_id=file_id, file_name=meta.get('name', ''),
            mime_type=meta.get('mimeType', ''), sector=None)
        estado = True
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'favorito': estado})
    volta = (request.POST.get('voltar') or '').strip()
    if volta == 'arquivo':
        return redirect('drive:meu_drive_arquivo', file_id=file_id)
    if volta == 'favoritos':
        return redirect('drive:favoritos')
    return redirect(_url_meu_drive(_pasta_do_post(request)))


@login_required
def meu_drive_versoes(request, file_id):
    if not _exige_meu_drive(request) or not _id_valido(file_id):
        _deny(request, file_id=file_id, detalhe='meu drive versões')
    try:
        meta = gdrive.obter(file_id, fields='id,name,mimeType')
        revs = gdrive.revisoes(file_id)
    except gdrive.DriveError as e:
        messages.error(request, f'Google Drive: {e}')
        return redirect('drive:meu_drive_arquivo', file_id=file_id)
    n = len(revs)
    for i, r in enumerate(revs):
        r['num'] = i + 1
        r['size_h'] = humano_bytes(r.get('size'))
        r['atual'] = (i == n - 1)
    return render(request, 'drive/meu_drive_versoes.html', {
        'meta': meta, 'revs': list(reversed(revs)), 'is_superadmin': True})


@login_required
@require_POST
def meu_drive_versao_restaurar(request, file_id):
    if not _exige_meu_drive(request) or not _id_valido(file_id):
        _deny(request, file_id=file_id, detalhe='meu drive restaurar versão')
    rev_id = (request.POST.get('rev') or '').strip()
    if not _id_valido(rev_id):
        messages.error(request, 'Versão inválida.')
        return redirect('drive:meu_drive_versoes', file_id=file_id)
    try:
        from googleapiclient.http import MediaIoBaseDownload
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, gdrive.service().revisions().get_media(
            fileId=file_id, revisionId=rev_id))
        done = False
        while not done:
            _, done = dl.next_chunk()
        buf.seek(0)
        meta = gdrive.obter(file_id, fields='id,name,mimeType')
        gdrive.nova_versao(file_id, buf, mimetype=meta.get('mimeType'))
    except Exception as e:  # noqa: BLE001
        messages.error(request, f'Não foi possível restaurar esta versão: {e}')
        return redirect('drive:meu_drive_versoes', file_id=file_id)
    audit.registrar(request.user, 'RESTORE', request, file_id=file_id,
                    file_name=meta.get('name', ''), detalhe=f'Meu Drive · revisão {rev_id}')
    messages.success(request, 'Versão restaurada como a mais recente.')
    return redirect('drive:meu_drive_versoes', file_id=file_id)

def _sa_email():
    """E-mail da service account (para o guia de compartilhamento), se der."""
    import json
    import os
    from django.conf import settings as st
    try:
        raw = (getattr(st, 'GOOGLE_DRIVE_SA_JSON', '') or '').strip()
        if raw.startswith('{'):
            return json.loads(raw).get('client_email', '')
        arq = (getattr(st, 'GOOGLE_DRIVE_SA_FILE', '') or '').strip()
        if arq and os.path.exists(arq):
            with open(arq, encoding='utf-8') as fh:
                return json.load(fh).get('client_email', '')
    except Exception:  # noqa: BLE001
        pass
    return ''
