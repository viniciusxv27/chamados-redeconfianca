"""Telas do módulo Drive — espelho do Google Drive da empresa.

Toda view valida a permissão NO SERVIDOR antes de qualquer leitura/escrita
(RNF01/02). O acesso por id resolve a cadeia de pastas até um setor autorizado
(RNF05): a interface só esconde botões; quem decide é o motor de permissões.
"""
import io
import logging
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse, Http404
from django.shortcuts import redirect, render, get_object_or_404
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from communications.models import CommunicationGroup
from users.models import Sector

from . import audit
from . import gdrive
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
    mime = mime or ''
    return (mime == 'application/pdf' or mime.startswith('image/')
            or mime.startswith('application/vnd.google-apps.'))


def _enriquecer(f):
    f['is_folder'] = f.get('mimeType') == FOLDER_MIME
    f['size_h'] = '' if f['is_folder'] else humano_bytes(f.get('size'))
    f['previewavel'] = (not f['is_folder']) and _previewavel(f.get('mimeType', ''))
    return f


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
    try:
        buf, nome, mime = gdrive.baixar(file_id, preview=not anexo)
    except gdrive.DriveNaoConfigurado as e:
        return _drive_off(request, e)
    except gdrive.DriveError as e:
        raise Http404(str(e))

    audit.registrar(request.user, 'DOWNLOAD' if anexo else 'VIEW', request=request,
                    file_id=file_id, file_name=nome, sector=mapping.sector)
    resp = HttpResponse(buf.read(), content_type=mime)
    resp['Content-Disposition'] = f"{'attachment' if anexo else 'inline'}; filename*=UTF-8''{quote(nome)}"
    resp['X-Content-Type-Options'] = 'nosniff'
    return resp


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

@login_required
def lixeira(request):
    setores = perms.sectors_visible(request.user)
    if not setores:
        return render(request, 'drive/lixeira.html', {'itens': [], 'is_superadmin': perms.is_superadmin(request.user)})
    try:
        achados, _ = gdrive.listar_lixeira(page_size=200)
    except gdrive.DriveNaoConfigurado as e:
        return _drive_off(request, e)
    except gdrive.DriveError as e:
        messages.error(request, f'Google Drive: {e}')
        achados = []
    itens = []
    for f in achados:
        m, nivel = perms.file_allowed(request.user, f['id'])
        if not m or nivel < ORDEM['DELETE']:
            continue
        f = _enriquecer(f)
        f['setor_nome'] = m.sector.name
        itens.append(f)
    return render(request, 'drive/lixeira.html', {
        'itens': itens, 'retencao': DriveConfig.get().trash_retention_days,
        'is_superadmin': perms.is_superadmin(request.user)})


@login_required
@require_POST
def lixeira_restaurar(request, file_id):
    mapping, nivel = perms.file_allowed(request.user, file_id)
    if not mapping or nivel < ORDEM['DELETE']:
        _deny(request, file_id=file_id, detalhe='restaurar lixeira')
    try:
        meta = gdrive.obter(file_id, fields='id,name')
        gdrive.para_lixeira(file_id, False)
    except gdrive.DriveError as e:
        messages.error(request, str(e))
        return redirect('drive:lixeira')
    audit.registrar(request.user, 'RESTORE', request=request, file_id=file_id,
                    file_name=meta.get('name', ''), sector=mapping.sector, detalhe='restaurado da lixeira')
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
        'sa_email': cfg.sa_client_email or _sa_email(), 'is_superadmin': True})


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
