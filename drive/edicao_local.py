"""Editar arquivos do Drive no programa do computador — e salvar de volta no Drive.

Pedido: abrir planilhas, Word, PDF, Power BI… direto no computador da pessoa, usando
uma cópia temporária, e mandar o arquivo editado de volta para o Drive, respeitando as
permissões.

Dois caminhos, conforme o arquivo:

1. Word, Excel, PowerPoint e Visio (e o que eles abrem: .odt, .csv…): o portal chama o
   próprio Office pelo protocolo dele (`ms-excel:ofe|u|<endereço>`). O endereço é uma
   "pasta" WebDAV do portal com um token: o Office baixa o arquivo para a cópia
   temporária dele, trava (LOCK) enquanto está aberto e, a cada "Salvar", manda o
   arquivo de volta (PUT) — que sobe como nova versão no Google Drive. É o mesmo
   mecanismo do SharePoint; não precisa instalar nada além do Office.
2. Os demais (PDF, Power BI, imagens…): a página grava uma cópia de trabalho numa pasta
   que a pessoa escolhe (File System Access, no Chrome e no Edge), acompanha essa cópia
   e, quando o programa salva, envia como nova versão (`enviar_versao`). Sem esse
   recurso no navegador, baixa o arquivo e oferece "Enviar versão editada".
   Ver static/js/rc-edicao-local.js.

Segurança: o token vale para UM arquivo e UMA pessoa, expira (12 h, prorrogável até
24 h com uso), fica guardado só como hash e dá no máximo o que a pessoa pode — quem só
baixa abre em modo leitura. Salvar confere a permissão na hora: quem perdeu o acesso de
edição no meio não salva. E salvar por cima da alteração de outra pessoa não
sobrescreve nada: vira uma cópia "conflito de edição" na mesma pasta.
"""
import email.utils
import hashlib
import logging
import re
import secrets
import shutil
import tempfile
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from datetime import timezone as fuso
from pathlib import Path
from urllib.parse import unquote, urlparse
from xml.sax.saxutils import escape

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.http import FileResponse, HttpResponse, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import audit, gdrive
from . import permissions as perms
from .models import DriveConfig, EdicaoLocal
from .permissions import ORDEM

logger = logging.getLogger('drive.edicao_local')

CAMPOS = 'id,name,mimeType,size,modifiedTime,md5Checksum,version,parents,trashed'
TOKEN_VALIDADE = timedelta(hours=12)
TOKEN_VALIDADE_MAXIMA = timedelta(hours=24)
PROLONGA_AO_USAR = timedelta(hours=2)
TRAVA_SEGUNDOS = 3600
PERMISSAO_VALE = timedelta(minutes=5)
MAX_AUXILIARES = 20
MAX_CORPO_XML = 64 * 1024
PASTA_AUXILIAR = Path(settings.BASE_DIR) / 'var' / 'drive_edicao'

# Extensão → protocolo do programa do Office que abre e salva o arquivo.
ESQUEMAS_OFFICE = {
    'ms-word': ('doc', 'docx', 'docm', 'dot', 'dotx', 'dotm', 'rtf', 'odt'),
    'ms-excel': ('xls', 'xlsx', 'xlsm', 'xlsb', 'xlt', 'xltx', 'xltm', 'csv', 'ods'),
    'ms-powerpoint': ('ppt', 'pptx', 'pptm', 'pps', 'ppsx', 'pot', 'potx', 'odp'),
    'ms-visio': ('vsd', 'vsdx'),
}
PROGRAMAS = {'ms-word': 'Word', 'ms-excel': 'Excel', 'ms-powerpoint': 'PowerPoint', 'ms-visio': 'Visio'}

CABECALHOS_DAV = {
    'DAV': '1,2',
    'MS-Author-Via': 'DAV',
    'Allow': 'OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, PROPPATCH, LOCK, UNLOCK, MOVE',
}
# Métodos que mexem em algo: só para quem pode salvar.
METODOS_QUE_GRAVAM = ('PUT', 'LOCK', 'UNLOCK', 'MOVE', 'DELETE', 'PROPPATCH')
LINK_VENCIDO = 'Este link de edição venceu. Abra o arquivo de novo pelo portal.'

# Válidos no nome de um arquivo do Drive, mas quebram o endereço (barra, "#", "%")
# ou o protocolo do Office, que separa as partes com "|".
_PROIBIDOS = re.compile(r'[\\/:*?"<>|#%\x00-\x1f]')


class ArquivoIndisponivel(Exception):
    """O arquivo não pode ser aberto nem receber versão (foi para a lixeira)."""


# ─── o que dá para editar e como ─────────────────────────────────────────────

def extensao(nome):
    return nome.rsplit('.', 1)[-1].lower() if nome and '.' in nome else ''


def esquema_office(nome):
    ext = extensao(nome)
    return next((esquema for esquema, extensoes in ESQUEMAS_OFFICE.items() if ext in extensoes), '')


def modo_de_edicao(meta):
    """'office', 'arquivo' ou '' (pastas e Docs do Google não se editam no computador)."""
    mime = meta.get('mimeType') or ''
    if not meta.get('id') or mime.startswith('application/vnd.google-apps.'):
        return ''
    return 'office' if esquema_office(meta.get('name', '')) else 'arquivo'


def contexto_da_tela(meta, pode_download, pode_editar, url_iniciar):
    """O cartão "Editar no computador" da pré-visualização — ou None."""
    modo = modo_de_edicao(meta)
    # Cópia sem poder salvar de volta é só um download: o botão Baixar já faz.
    if not modo or not pode_download or (modo == 'arquivo' and not pode_editar):
        return None
    return {
        'modo': modo,
        'programa': PROGRAMAS.get(esquema_office(meta.get('name', '')), ''),
        'pode_salvar': bool(pode_editar),
        'url_iniciar': url_iniciar,
    }


def nome_no_endereco(nome):
    """O nome do arquivo como vai no endereço WebDAV (é o nome que o Office mostra)."""
    limpo = _PROIBIDOS.sub('_', unicodedata.normalize('NFC', nome or '')).strip().rstrip('. ')
    if len(limpo) > 150:
        base, ponto, ext = limpo.rpartition('.')
        limpo = f'{base[:149 - len(ext)]}.{ext}' if ponto and base and len(ext) <= 10 else limpo[:150]
    return limpo or 'arquivo'


def _mesmo_nome(a, b):
    return nome_no_endereco(a).casefold() == nome_no_endereco(b).casefold()


# ─── sessões (tokens) ────────────────────────────────────────────────────────

def _hash(token):
    return hashlib.sha256((token or '').encode('utf-8')).hexdigest()


def pasta_auxiliar(sessao):
    return PASTA_AUXILIAR / str(sessao.pk)


def limpar_sessoes_vencidas():
    """Tira do disco os temporários de edições que acabaram e esquece as antigas."""
    agora = timezone.now()
    try:
        if PASTA_AUXILIAR.exists():
            vivas = {str(pk) for pk in EdicaoLocal.objects.filter(
                expira_em__gt=agora, encerrado_em__isnull=True).values_list('pk', flat=True)}
            for entrada in PASTA_AUXILIAR.iterdir():
                if entrada.is_dir() and entrada.name not in vivas:
                    shutil.rmtree(entrada, ignore_errors=True)
        EdicaoLocal.objects.filter(criado_em__lt=agora - timedelta(days=30)).delete()
    except Exception as exc:                                        # noqa: BLE001
        logger.warning('Limpeza das edições no computador falhou: %s', exc)


def criar_sessao(user, meta, modo, pode_salvar, sector=None, meu_drive=False, anterior=None):
    limpar_sessoes_vencidas()
    agora = timezone.now()
    token = secrets.token_urlsafe(32)
    # Abrir de novo (o programa fechou sem avisar, o computador desligou) não
    # pode esbarrar na trava que a própria pessoa deixou para trás.
    EdicaoLocal.objects.filter(user=user, file_id=meta['id']).exclude(lock_token='').update(
        lock_token='', lock_expira_em=None)
    if anterior is not None:
        EdicaoLocal.objects.filter(pk=anterior.pk, encerrado_em__isnull=True).update(encerrado_em=agora)
    sessao = EdicaoLocal.objects.create(
        token_hash=_hash(token),
        user=user,
        file_id=meta['id'],
        file_name=(meta.get('name') or 'arquivo')[:255],
        mime_type=(meta.get('mimeType') or '')[:120],
        sector=sector,
        meu_drive=meu_drive,
        modo=modo,
        pode_salvar=pode_salvar,
        # Continuação de uma cópia aberta antes (a edição venceu no meio): vale a
        # versão que ela abriu — senão o envio passaria por cima do que outra
        # pessoa salvou nesse meio-tempo.
        md5_base=anterior.md5_base if anterior is not None else (meta.get('md5Checksum') or ''),
        conflito_file_id=anterior.conflito_file_id if anterior is not None else '',
        ultimo_conflito_nome=anterior.ultimo_conflito_nome if anterior is not None else '',
        ultimo_conflito_em=anterior.ultimo_conflito_em if anterior is not None else None,
        expira_em=agora + TOKEN_VALIDADE,
        permissao_conferida_em=agora,
    )
    return sessao, token


def sessao_do_token(token, user=None):
    """A sessão válida desse token (e dessa pessoa, se informada), ou None."""
    if not token or len(token) > 100:
        return None
    filtro = {'token_hash': _hash(token)}
    if user is not None:
        filtro['user'] = user
    sessao = EdicaoLocal.objects.select_related('user', 'sector').filter(**filtro).first()
    if (sessao is None or sessao.encerrado_em or sessao.expira_em <= timezone.now()
            or not sessao.user.is_active):
        return None
    return sessao


def _prolongar(sessao, agora):
    return min(sessao.criado_em + TOKEN_VALIDADE_MAXIMA, max(sessao.expira_em, agora + PROLONGA_AO_USAR))


def conferir_permissao(sessao, gravar=False, na_hora=False):
    """A pessoa ainda pode abrir (e, com `gravar`, mexer em) este arquivo?

    Salvar a versão confere sempre (`na_hora`): quem perdeu a permissão de edição
    no meio não salva mais. O resto — o Office faz dezenas de pedidos seguidos —
    aproveita a conferência dos últimos 5 minutos.
    """
    if gravar and not sessao.pode_salvar:
        return False
    agora = timezone.now()
    if (not na_hora and sessao.permissao_conferida_em
            and agora - sessao.permissao_conferida_em < PERMISSAO_VALE):
        return True
    user = sessao.user
    if not user.is_active:
        return False
    if sessao.meu_drive:
        cfg = DriveConfig.get()
        pode_ler = pode_salvar = bool(
            perms.is_superadmin(user) and cfg.usa_conta_propria and cfg.oauth_refresh_token)
    else:
        mapping, nivel = perms.file_allowed(user, sessao.file_id)
        pode_ler = bool(mapping) and nivel >= ORDEM['DOWNLOAD']
        pode_salvar = bool(mapping) and nivel >= ORDEM['EDIT']
    if not pode_ler:
        return False
    campos = {'permissao_conferida_em': agora}
    if sessao.pode_salvar and not pode_salvar:
        # Perdeu a edição, mas ainda pode baixar: segue só em leitura.
        campos['pode_salvar'] = False
        sessao.pode_salvar = False
    EdicaoLocal.objects.filter(pk=sessao.pk).update(**campos)
    sessao.permissao_conferida_em = agora
    return pode_salvar or not gravar


# ─── salvar no Drive ─────────────────────────────────────────────────────────

def _quem(user):
    return user.get_full_name() or user.email or user.get_username()


def _nome_de_conflito(nome, user, agora):
    base, ponto, ext = nome.rpartition('.')
    if not ponto or not base:
        base, ext = nome, ''
    sufixo = f' (conflito de edição — {_quem(user)[:40]} — {timezone.localtime(agora):%d-%m-%Y %Hh%M})'
    final = f'.{ext}' if ext else ''
    return base[:max(1, 255 - len(sufixo) - len(final))] + sufixo + final


def _avisar_setor(sessao):
    if not sessao.sector_id:
        return
    try:
        from .views import _notificar

        mapping = perms.mapping_por_setor(sessao.sector_id)
        if mapping:
            _notificar(mapping, DriveConfig.get(), novo=False, ator=sessao.user)
    except Exception as exc:                                        # noqa: BLE001
        logger.warning('Aviso de edição no computador não enviado: %s', exc)


def salvar_versao(sessao, arquivo, request=None, origem='Office'):
    """Sobe o conteúdo como nova versão do arquivo — ou na cópia de conflito."""
    meta = gdrive.obter(sessao.file_id, fields=CAMPOS)
    if meta.get('trashed'):
        raise ArquivoIndisponivel('O arquivo foi para a lixeira do Drive.')
    agora = timezone.now()
    mime = meta.get('mimeType') or 'application/octet-stream'
    atual = meta.get('md5Checksum') or ''
    if sessao.conflito_file_id or (sessao.md5_base and atual and atual != sessao.md5_base):
        return _salvar_na_copia_de_conflito(sessao, arquivo, meta, mime, agora, request, origem)

    novo = gdrive.nova_versao(sessao.file_id, arquivo, mimetype=mime)
    primeira = not sessao.salvamentos
    EdicaoLocal.objects.filter(pk=sessao.pk).update(
        md5_base=novo.get('md5Checksum') or '', salvamentos=F('salvamentos') + 1,
        ultimo_salvamento_em=agora, expira_em=_prolongar(sessao, agora))
    sessao.refresh_from_db()
    audit.registrar(sessao.user, 'VERSION', request=request, file_id=sessao.file_id,
                    file_name=sessao.file_name, sector=sessao.sector,
                    detalhe=f'editado no computador ({origem})')
    if primeira:
        # Um aviso por edição: cada Ctrl+S de quem edita não vira um aviso para o setor.
        _avisar_setor(sessao)
    return {'conflito': False, 'versao': str(novo.get('version') or '')}


def _salvar_na_copia_de_conflito(sessao, arquivo, meta, mime, agora, request, origem):
    """Outra pessoa mudou o arquivo depois que esta cópia foi aberta.

    Sobrescrever apagaria o trabalho dela: a versão desta pessoa vai para uma
    cópia na mesma pasta, com o nome dizendo o que houve. Os salvamentos
    seguintes desta edição atualizam essa mesma cópia, sem criar outras.
    """
    copia_id, nome = sessao.conflito_file_id, sessao.ultimo_conflito_nome
    if copia_id:
        try:
            if gdrive.obter(copia_id, fields='id,trashed').get('trashed'):
                copia_id = ''
        except gdrive.DriveError:
            copia_id = ''
    if copia_id:
        gdrive.nova_versao(copia_id, arquivo, mimetype=mime)
        acao, detalhe = 'VERSION', f'cópia de conflito atualizada no computador ({origem})'
    else:
        nome = _nome_de_conflito(sessao.file_name, sessao.user, agora)
        pai = (meta.get('parents') or [gdrive.RAIZ_MEU_DRIVE])[0]
        copia_id = gdrive.enviar(nome, mime, arquivo, pai).get('id', '')
        acao, detalhe = 'UPLOAD', f'conflito de edição no computador ({origem})'
        logger.info('Edição no computador de %s virou a cópia de conflito %s', sessao.file_id, copia_id)
    EdicaoLocal.objects.filter(pk=sessao.pk).update(
        conflito_file_id=copia_id, ultimo_conflito_nome=nome, ultimo_conflito_em=agora,
        salvamentos=F('salvamentos') + 1, ultimo_salvamento_em=agora, expira_em=_prolongar(sessao, agora))
    sessao.refresh_from_db()
    audit.registrar(sessao.user, acao, request=request, file_id=copia_id, file_name=nome,
                    sector=sessao.sector, detalhe=detalhe)
    return {'conflito': True, 'nome': nome, 'file_id': copia_id}


# ─── WebDAV: respostas ───────────────────────────────────────────────────────

def _resposta(status, conteudo=b'', tipo='text/plain; charset=utf-8', cabecalhos=None):
    if isinstance(conteudo, str):
        conteudo = conteudo.encode('utf-8')
    resposta = HttpResponse(conteudo, status=status, content_type=tipo)
    for chave, valor in (cabecalhos or {}).items():
        resposta[chave] = valor
    return resposta


def _com_dav(resposta):
    for chave, valor in CABECALHOS_DAV.items():
        resposta[chave] = valor
    return resposta


def _data_http(dt):
    return email.utils.format_datetime(dt.astimezone(fuso.utc), usegmt=True)


def _data_iso(dt):
    return dt.astimezone(fuso.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _modificado(meta):
    return parse_datetime(meta.get('modifiedTime') or '') or timezone.now()


def _etag(meta):
    return '"' + (meta.get('md5Checksum') or f"v{meta.get('version') or 0}") + '"'


def _href_colecao(token):
    return reverse('drive:dav_colecao', kwargs={'token': token}).rstrip('/') + '/'


def _href_arquivo(token, nome):
    return reverse('drive:dav_arquivo', kwargs={'token': token, 'nome': nome})


def _fragmento(href, props):
    return (f'<D:response><D:href>{escape(href)}</D:href><D:propstat><D:prop>{"".join(props)}</D:prop>'
            '<D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>')


def _props_basicas(nome, modificado):
    return [f'<D:displayname>{escape(nome)}</D:displayname>',
            f'<D:getlastmodified>{_data_http(modificado)}</D:getlastmodified>',
            f'<D:creationdate>{_data_iso(modificado)}</D:creationdate>']


def _props_de_arquivo(tamanho, tipo, etag, lock_xml=''):
    return [f'<D:getcontentlength>{int(tamanho or 0)}</D:getcontentlength>',
            f'<D:getcontenttype>{escape(tipo or "application/octet-stream")}</D:getcontenttype>',
            f'<D:getetag>{escape(etag)}</D:getetag>',
            '<D:resourcetype/>',
            '<D:supportedlock><D:lockentry><D:lockscope><D:exclusive/></D:lockscope>'
            '<D:locktype><D:write/></D:locktype></D:lockentry></D:supportedlock>',
            f'<D:lockdiscovery>{lock_xml}</D:lockdiscovery>']


def _multistatus(fragmentos):
    corpo = ('<?xml version="1.0" encoding="utf-8"?>\n<D:multistatus xmlns:D="DAV:">'
             + ''.join(fragmentos) + '</D:multistatus>')
    return _resposta(207, corpo, 'application/xml; charset=utf-8')


def _trava_ativa(sessao, agora=None):
    agora = agora or timezone.now()
    return bool(sessao.lock_token and sessao.lock_expira_em and sessao.lock_expira_em > agora)


def _trava_de_outra_sessao(sessao, agora):
    return (EdicaoLocal.objects
            .filter(file_id=sessao.file_id, encerrado_em__isnull=True, expira_em__gt=agora, lock_expira_em__gt=agora)
            .exclude(pk=sessao.pk).exclude(lock_token='').select_related('user').first())


def _activelock(dono, href, lock_token, expira_em):
    segundos = max(0, int((expira_em - timezone.now()).total_seconds()))
    return ('<D:activelock><D:locktype><D:write/></D:locktype><D:lockscope><D:exclusive/></D:lockscope>'
            f'<D:depth>0</D:depth><D:owner>{escape(_quem(dono))}</D:owner><D:timeout>Second-{segundos}</D:timeout>'
            f'<D:locktoken><D:href>{escape(lock_token)}</D:href></D:locktoken>'
            f'<D:lockroot><D:href>{escape(href)}</D:href></D:lockroot></D:activelock>')


def _timeout(cabecalho):
    achado = re.search(r'Second-(\d+)', cabecalho or '')
    return max(60, min(TRAVA_SEGUNDOS, int(achado.group(1)))) if achado else TRAVA_SEGUNDOS


def _pedido_traz_trava(request, sessao):
    return bool(sessao.lock_token) and sessao.lock_token in (request.headers.get('If') or '')


def _e_principal(sessao, nome):
    return _mesmo_nome(nome, sessao.file_name)


def _caminho_auxiliar(sessao, nome):
    return pasta_auxiliar(sessao) / nome_no_endereco(nome)


def _auxiliares(sessao):
    pasta = pasta_auxiliar(sessao)
    return sorted(p.name for p in pasta.iterdir() if p.is_file()) if pasta.is_dir() else []


def _corpo_pequeno(request):
    """O corpo de um pedido XML (LOCK, PROPPATCH) — None se for grande demais."""
    try:
        declarado = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        declarado = 0
    return None if declarado > MAX_CORPO_XML else (request.body or b'')


def _receber_corpo(request, limite):
    """O corpo do PUT num temporário, lido aos pedaços: (arquivo, bytes) ou (None, bytes)."""
    arquivo = tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024)
    total = 0
    while True:
        bloco = request.read(1024 * 1024)
        if not bloco:
            break
        total += len(bloco)
        if total > limite:
            arquivo.close()
            return None, total
        arquivo.write(bloco)
    arquivo.seek(0)
    return arquivo, total


def _meta_principal(sessao):
    meta = gdrive.obter(sessao.file_id, fields=CAMPOS)
    if meta.get('trashed'):
        raise ArquivoIndisponivel('O arquivo está na lixeira do Drive.')
    return meta


def _fragmento_principal(sessao, token, agora=None):
    agora = agora or timezone.now()
    meta = _meta_principal(sessao)
    nome = nome_no_endereco(sessao.file_name)
    href = _href_arquivo(token, nome)
    lock_xml = ''
    if _trava_ativa(sessao, agora):
        lock_xml = _activelock(sessao.user, href, sessao.lock_token, sessao.lock_expira_em)
    else:
        outra = _trava_de_outra_sessao(sessao, agora)
        if outra:
            # Mostra quem está com o arquivo aberto: o Office oferece o modo leitura
            # dizendo o nome, em vez de deixar editar e falhar ao salvar.
            lock_xml = _activelock(outra.user, href, outra.lock_token, outra.lock_expira_em)
    return _fragmento(href, _props_basicas(nome, _modificado(meta))
                      + _props_de_arquivo(meta.get('size'), meta.get('mimeType'), _etag(meta), lock_xml))


def _fragmento_auxiliar(sessao, token, nome):
    caminho = _caminho_auxiliar(sessao, nome)
    if not caminho.is_file():
        return None
    info = caminho.stat()
    modificado = datetime.fromtimestamp(info.st_mtime, tz=fuso.utc)
    return _fragmento(_href_arquivo(token, caminho.name), _props_basicas(caminho.name, modificado)
                      + _props_de_arquivo(info.st_size, 'application/octet-stream',
                                          f'"{int(info.st_mtime)}-{info.st_size}"'))


# ─── WebDAV: métodos ─────────────────────────────────────────────────────────

def _dav_get(request, sessao, token, nome, principal):
    if not principal:
        caminho = _caminho_auxiliar(sessao, nome)
        if not caminho.is_file():
            return _resposta(404, 'Não encontrado.')
        if request.method == 'HEAD':
            return _resposta(200, b'', 'application/octet-stream', {'Content-Length': str(caminho.stat().st_size)})
        return FileResponse(open(caminho, 'rb'), content_type='application/octet-stream')

    meta = _meta_principal(sessao)
    tipo = meta.get('mimeType') or 'application/octet-stream'
    cabecalhos = {'ETag': _etag(meta), 'Last-Modified': _data_http(_modificado(meta))}
    if request.method == 'HEAD':
        cabecalhos['Content-Length'] = str(int(meta.get('size') or 0))
        return _resposta(200, b'', tipo, cabecalhos)

    buf, _nome, _tipo = gdrive.baixar(sessao.file_id)
    dados = buf.read()
    # O que o programa acabou de baixar é a base para saber, no próximo
    # "Salvar", se outra pessoa mudou o arquivo nesse meio-tempo.
    campos = {'md5_base': meta.get('md5Checksum') or '', 'conflito_file_id': ''}
    if not sessao.aberto_em:
        campos['aberto_em'] = timezone.now()
        audit.registrar(sessao.user, 'DOWNLOAD', request=request, file_id=sessao.file_id,
                        file_name=sessao.file_name, sector=sessao.sector, detalhe='aberto no computador')
    EdicaoLocal.objects.filter(pk=sessao.pk).update(**campos)
    return _resposta(200, dados, tipo, cabecalhos)


def _dav_propfind(request, sessao, token, nome, principal):
    if principal:
        return _multistatus([_fragmento_principal(sessao, token)])
    fragmento = _fragmento_auxiliar(sessao, token, nome)
    return _multistatus([fragmento]) if fragmento else _resposta(404, 'Não encontrado.')


def _dav_proppatch(request, sessao, token, nome, principal):
    # O Office grava atributos do Windows (Win32LastModifiedTime…). Nada disso vai
    # para o Drive: só confirmamos, senão ele acusa erro depois de salvar.
    corpo = _corpo_pequeno(request)
    if corpo is None:
        return _resposta(413, 'Pedido grande demais.')
    props = []
    try:
        for prop in ET.fromstring(corpo or b'<vazio/>').iter('{DAV:}prop'):
            props.extend(list(prop))
    except ET.ParseError:
        props = []
    itens = []
    for i, prop in enumerate(props):
        if prop.tag.startswith('{'):
            ns, _, local = prop.tag[1:].partition('}')
            itens.append(f'<n{i}:{local} xmlns:n{i}="{escape(ns, {chr(34): "&quot;"})}"/>')
        else:
            itens.append(f'<{prop.tag}/>')
    return _multistatus([_fragmento(_href_arquivo(token, nome_no_endereco(nome)), itens)])


def _dav_lock(request, sessao, token, nome, principal):
    corpo = _corpo_pequeno(request)
    if corpo is None:
        return _resposta(413, 'Pedido grande demais.')
    agora = timezone.now()
    href = _href_arquivo(token, nome_no_endereco(nome))
    expira = agora + timedelta(seconds=_timeout(request.headers.get('Timeout')))

    if not principal:
        # Travas nos temporários do programa: aceitas, sem efeito no Drive.
        lock_token = f'opaquelocktoken:{uuid.uuid4()}'
    else:
        outra = _trava_de_outra_sessao(sessao, agora)
        if outra:
            return _resposta(423, f'Em edição por {_quem(outra.user)}.')
        if not corpo.strip():
            # Renovação: o Office manda no cabeçalho If a trava que tem. Vale mesmo
            # vencida (o computador dormiu), desde que ninguém tenha travado depois.
            if not _pedido_traz_trava(request, sessao):
                return _resposta(412, 'Trava não encontrada.')
            lock_token = sessao.lock_token
        else:
            lock_token = sessao.lock_token if _trava_ativa(sessao, agora) else f'opaquelocktoken:{uuid.uuid4()}'
        EdicaoLocal.objects.filter(pk=sessao.pk).update(
            lock_token=lock_token, lock_expira_em=expira, expira_em=_prolongar(sessao, agora))

    corpo_resposta = ('<?xml version="1.0" encoding="utf-8"?>\n<D:prop xmlns:D="DAV:"><D:lockdiscovery>'
                      + _activelock(sessao.user, href, lock_token, expira) + '</D:lockdiscovery></D:prop>')
    return _resposta(200, corpo_resposta, 'application/xml; charset=utf-8', {'Lock-Token': f'<{lock_token}>'})


def _dav_unlock(request, sessao, token, nome, principal):
    if not principal:
        return _resposta(204)
    pedido = (request.headers.get('Lock-Token') or '').strip().strip('<>')
    if not sessao.lock_token or pedido == sessao.lock_token:
        EdicaoLocal.objects.filter(pk=sessao.pk).update(lock_token='', lock_expira_em=None)
        return _resposta(204)
    return _resposta(409, 'Trava não confere.')


def _gravar_auxiliar(sessao, nome, corpo):
    caminho = _caminho_auxiliar(sessao, nome)
    existia = caminho.is_file()
    if not existia and len(_auxiliares(sessao)) >= MAX_AUXILIARES:
        return _resposta(507, 'Arquivos temporários demais nesta edição.')
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with open(caminho, 'wb') as destino:
            shutil.copyfileobj(corpo, destino)
    except OSError as exc:
        logger.warning('Temporário da edição no computador não gravado (%s): %s', sessao.pk, exc)
        return _resposta(507, 'Sem espaço para o arquivo temporário.')
    return _resposta(204 if existia else 201)


def _dav_put(request, sessao, token, nome, principal):
    cfg = DriveConfig.get()
    try:
        declarado = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        declarado = 0
    if declarado > cfg.max_file_bytes:
        return _resposta(413, f'O arquivo passa do limite de {cfg.max_file_mb} MB.')
    if principal:
        outra = _trava_de_outra_sessao(sessao, timezone.now())
        if outra:
            return _resposta(423, f'Em edição por {_quem(outra.user)}.')
    corpo, total = _receber_corpo(request, cfg.max_file_bytes)
    if corpo is None:
        return _resposta(413, f'O arquivo passa do limite de {cfg.max_file_mb} MB.')
    try:
        if not principal:
            return _gravar_auxiliar(sessao, nome, corpo)
        if total == 0:
            # Alguns clientes WebDAV criam o arquivo vazio antes de mandar o
            # conteúdo. Uma versão vazia apagaria o documento: ignora.
            return _resposta(204)
        salvar_versao(sessao, corpo, request=request, origem='Office')
        return _resposta(204)
    finally:
        corpo.close()


def _dav_delete(request, sessao, token, nome, principal):
    if principal:
        return _resposta(403, 'O arquivo do Drive não é excluído por aqui.')
    caminho = _caminho_auxiliar(sessao, nome)
    if not caminho.is_file():
        return _resposta(404, 'Não encontrado.')
    caminho.unlink()
    return _resposta(204)


def _dav_move(request, sessao, token, nome, principal):
    prefixo = _href_colecao(token)
    destino = unquote(urlparse(request.headers.get('Destination') or '').path)
    nome_destino = destino[len(prefixo):] if destino.startswith(prefixo) else ''
    if not nome_destino or '/' in nome_destino:
        return _resposta(502, 'Destino fora da pasta de edição.')
    if principal:
        return _resposta(403, 'O arquivo do Drive não é movido nem renomeado por aqui.')
    origem = _caminho_auxiliar(sessao, nome)
    if not origem.is_file():
        return _resposta(404, 'Não encontrado.')

    if _e_principal(sessao, nome_destino):
        # "Salvar" por temporário + renomear (LibreOffice, alguns programas no Mac).
        if not conferir_permissao(sessao, gravar=True, na_hora=True):
            return _resposta(403, 'Sem permissão para salvar este arquivo.')
        outra = _trava_de_outra_sessao(sessao, timezone.now())
        if outra:
            return _resposta(423, f'Em edição por {_quem(outra.user)}.')
        if origem.stat().st_size:
            with open(origem, 'rb') as conteudo:
                salvar_versao(sessao, conteudo, request=request, origem='WebDAV')
        origem.unlink()
        return _resposta(204)

    alvo = _caminho_auxiliar(sessao, nome_destino)
    existia = alvo.is_file()
    if existia and (request.headers.get('Overwrite') or 'T').upper() == 'F':
        return _resposta(412, 'O destino já existe.')
    origem.replace(alvo)
    return _resposta(204 if existia else 201)


_METODOS = {
    'GET': _dav_get, 'HEAD': _dav_get, 'PROPFIND': _dav_propfind, 'PROPPATCH': _dav_proppatch,
    'LOCK': _dav_lock, 'UNLOCK': _dav_unlock, 'PUT': _dav_put, 'DELETE': _dav_delete, 'MOVE': _dav_move,
}


def _sessao_para_dav(token):
    sessao = sessao_do_token(token)
    # O endereço WebDAV é só do Office; a cópia no computador envia pela página
    # (com login), então o token dela não abre nada por aqui.
    return sessao if sessao is not None and sessao.modo == EdicaoLocal.Modo.OFFICE else None


@csrf_exempt
def dav_raiz(request):
    """A descoberta de protocolo do Office pergunta (OPTIONS) nas pastas de cima."""
    if request.method == 'OPTIONS':
        return _com_dav(_resposta(200))
    return _resposta(404, 'Não encontrado.')


@csrf_exempt
def dav_colecao(request, token):
    if request.method == 'OPTIONS':
        return _com_dav(_resposta(200))
    sessao = _sessao_para_dav(token)
    if sessao is None:
        return _resposta(403, LINK_VENCIDO)
    if not conferir_permissao(sessao):
        return _resposta(403, 'Sem acesso a este arquivo.')
    if request.method == 'PROPFIND':
        fragmentos = [_fragmento(_href_colecao(token), _props_basicas('Edição no portal', sessao.criado_em)
                                 + ['<D:resourcetype><D:collection/></D:resourcetype>'])]
        if request.headers.get('Depth', '1') != '0':
            try:
                fragmentos.append(_fragmento_principal(sessao, token))
            except (gdrive.DriveError, ArquivoIndisponivel) as exc:
                logger.warning('Edição no computador: pasta sem o arquivo %s (%s)', sessao.file_id, exc)
            fragmentos.extend(filter(None, (_fragmento_auxiliar(sessao, token, n) for n in _auxiliares(sessao))))
        return _multistatus(fragmentos)
    if request.method in ('GET', 'HEAD'):
        return _resposta(200, 'Pasta de edição do portal.')
    return _com_dav(_resposta(405, 'Método não permitido.'))


@csrf_exempt
def dav_arquivo(request, token, nome):
    metodo = request.method
    if metodo == 'OPTIONS':
        return _com_dav(_resposta(200))
    sessao = _sessao_para_dav(token)
    if sessao is None:
        return _resposta(403, LINK_VENCIDO)
    principal = _e_principal(sessao, nome)
    # LOCK recusado faz o Office abrir em modo leitura — o certo para quem só baixa.
    if not conferir_permissao(sessao, gravar=metodo in METODOS_QUE_GRAVAM, na_hora=metodo == 'PUT' and principal):
        return _resposta(403, 'Sem permissão para esta operação.')
    tratar = _METODOS.get(metodo)
    if tratar is None:
        return _com_dav(_resposta(405, 'Método não permitido.'))
    try:
        return tratar(request, sessao, token, nome, principal)
    except ArquivoIndisponivel as exc:
        return _resposta(404 if metodo in ('GET', 'HEAD', 'PROPFIND') else 409, str(exc))
    except gdrive.DriveNaoConfigurado:
        return _resposta(503, 'O Google Drive não está conectado.')
    except gdrive.DriveError as exc:
        logger.warning('Edição no computador (%s %s): %s', metodo, sessao.file_id, exc)
        return _resposta(502, 'O Google Drive não respondeu. Tente de novo.')


# ─── telas do portal ─────────────────────────────────────────────────────────

def _url_absoluta(request, caminho):
    from .views import _e_local

    url = request.build_absolute_uri(caminho)
    # O proxy termina o TLS e o Django vê http: fora da máquina local o Office
    # recusa (ou pede senha para) endereço sem https.
    if url.startswith('http://') and not _e_local(request.get_host()):
        url = 'https://' + url[len('http://'):]
    return url


def _iniciar(request, file_id, meu_drive):
    from .views import _deny, _exige_meu_drive, _id_valido

    if not _id_valido(file_id):
        return JsonResponse({'error': 'Arquivo inválido.'}, status=404)
    mapping = None
    if meu_drive:
        if not _exige_meu_drive(request):
            _deny(request, file_id=file_id, detalhe='meu drive: editar no computador')
        pode_salvar = True
    else:
        mapping, nivel = perms.file_allowed(request.user, file_id)
        if not mapping or nivel < ORDEM['DOWNLOAD']:
            _deny(request, file_id=file_id, detalhe='editar no computador')
        pode_salvar = nivel >= ORDEM['EDIT']

    try:
        meta = gdrive.obter(file_id, fields=CAMPOS)
    except gdrive.DriveNaoConfigurado:
        return JsonResponse({'error': 'O Google Drive não está conectado.'}, status=503)
    except gdrive.DriveError:
        return JsonResponse({'error': 'Arquivo não encontrado no Drive.'}, status=404)
    if meta.get('trashed'):
        return JsonResponse({'error': 'O arquivo está na lixeira do Drive.'}, status=400)
    modo = modo_de_edicao(meta)
    if not modo:
        return JsonResponse({'error': 'Pastas e documentos do Google (Docs, Planilhas, Apresentações) não são '
                                      'editados no computador: abra no Google Drive.'}, status=400)
    if request.POST.get('modo') == 'arquivo':
        modo = 'arquivo'
    if modo == 'arquivo' and not pode_salvar:
        return JsonResponse({'error': 'Você pode baixar este arquivo, mas não tem permissão para salvar '
                                      'alterações nele.'}, status=403)

    anterior = None
    if request.POST.get('anterior'):
        anterior = EdicaoLocal.objects.filter(
            token_hash=_hash(request.POST['anterior']), user=request.user, file_id=file_id).first()
    sessao, token = criar_sessao(request.user, meta, modo, pode_salvar, sector=mapping.sector if mapping else None,
                                 meu_drive=meu_drive, anterior=anterior)
    conteudo = reverse('drive:meu_drive_conteudo' if meu_drive else 'drive:file_content', args=[file_id])
    dados = {
        'ok': True,
        'token': token,
        'modo': modo,
        'nome': sessao.file_name,
        'somente_leitura': not pode_salvar,
        'expira_em': sessao.expira_em.isoformat(),
        'url_conteudo': conteudo + '?dl=1',
        'url_status': reverse('drive:edicao_local_status', args=[token]),
        'url_enviar': reverse('drive:edicao_local_enviar', args=[token]),
        'url_encerrar': reverse('drive:edicao_local_encerrar', args=[token]),
    }
    if modo == 'office':
        esquema = esquema_office(sessao.file_name)
        url_dav = _url_absoluta(request, _href_arquivo(token, nome_no_endereco(sessao.file_name)))
        dados.update(programa=PROGRAMAS.get(esquema, 'Office'), url_dav=url_dav,
                     uri_office=f"{esquema}:{'ofe' if pode_salvar else 'ofv'}|u|{url_dav}")
    audit.registrar(request.user, 'VIEW', request=request, file_id=file_id, file_name=sessao.file_name,
                    sector=sessao.sector,
                    detalhe=('editar' if pode_salvar else 'abrir em modo leitura') + ' no computador')
    return JsonResponse(dados)


@login_required
@require_POST
def iniciar_edicao(request, file_id):
    return _iniciar(request, file_id, meu_drive=False)


@login_required
@require_POST
def iniciar_edicao_meu_drive(request, file_id):
    return _iniciar(request, file_id, meu_drive=True)


def _sessao_da_pessoa(request, token):
    if not token or len(token) > 100:
        return None
    return (EdicaoLocal.objects.select_related('user', 'sector')
            .filter(token_hash=_hash(token), user=request.user).first())


@login_required
@require_GET
def status_edicao(request, token):
    sessao = _sessao_da_pessoa(request, token)
    if sessao is None:
        return JsonResponse({'error': 'Edição não encontrada.'}, status=404)
    agora = timezone.now()
    return JsonResponse({
        'ok': True,
        'modo': sessao.modo,
        'somente_leitura': not sessao.pode_salvar,
        'aberto_no_programa': _trava_ativa(sessao, agora),
        'baixado': bool(sessao.aberto_em),
        'salvamentos': sessao.salvamentos,
        'ultimo_salvamento_em': sessao.ultimo_salvamento_em.isoformat() if sessao.ultimo_salvamento_em else None,
        'conflito': ({'nome': sessao.ultimo_conflito_nome, 'em': sessao.ultimo_conflito_em.isoformat()}
                     if sessao.ultimo_conflito_em else None),
        'expirada': bool(sessao.encerrado_em) or sessao.expira_em <= agora,
        'expira_em': sessao.expira_em.isoformat(),
    })


@login_required
@require_POST
def encerrar_edicao(request, token):
    sessao = _sessao_da_pessoa(request, token)
    if sessao is None:
        return JsonResponse({'error': 'Edição não encontrada.'}, status=404)
    EdicaoLocal.objects.filter(pk=sessao.pk).update(
        encerrado_em=sessao.encerrado_em or timezone.now(), lock_token='', lock_expira_em=None)
    shutil.rmtree(pasta_auxiliar(sessao), ignore_errors=True)
    return JsonResponse({'ok': True})


@login_required
@require_POST
def enviar_versao(request, token):
    """A versão editada na cópia do computador (PDF, Power BI…) chega aqui."""
    sessao = sessao_do_token(token, user=request.user)
    if sessao is None:
        return JsonResponse({'error': 'Esta edição venceu. Abra o arquivo de novo pelo portal.',
                             'expirada': True}, status=410)
    if not conferir_permissao(sessao, gravar=True, na_hora=True):
        return JsonResponse({'error': 'Você não tem permissão para salvar alterações neste arquivo.'}, status=403)
    arquivo = request.FILES.get('arquivo')
    if not arquivo:
        return JsonResponse({'error': 'Envie o arquivo editado.'}, status=400)
    if not arquivo.size:
        return JsonResponse({'error': 'O arquivo enviado está vazio.'}, status=400)
    cfg = DriveConfig.get()
    if arquivo.size > cfg.max_file_bytes:
        return JsonResponse({'error': f'O arquivo passa do limite de {cfg.max_file_mb} MB.'}, status=413)
    esperada = extensao(sessao.file_name)
    if esperada and extensao(arquivo.name) != esperada:
        return JsonResponse({'error': f'Envie o próprio arquivo editado (.{esperada}).'}, status=400)
    outra = _trava_de_outra_sessao(sessao, timezone.now())
    if outra:
        return JsonResponse({'error': f'{_quem(outra.user)} está com este arquivo aberto no Office. '
                                      'Sua cópia continua guardada; o envio é tentado de novo.',
                             'travado': True}, status=423)
    try:
        resultado = salvar_versao(sessao, arquivo, request=request, origem='cópia no computador')
    except ArquivoIndisponivel as exc:
        return JsonResponse({'error': str(exc)}, status=409)
    except gdrive.DriveNaoConfigurado:
        return JsonResponse({'error': 'O Google Drive não está conectado.'}, status=503)
    except gdrive.DriveError as exc:
        logger.warning('Versão do computador não subiu (%s): %s', sessao.file_id, exc)
        return JsonResponse({'error': 'O Google Drive não respondeu. Tente de novo em instantes.'}, status=502)
    return JsonResponse({'ok': True, 'salvo_em': timezone.now().isoformat(), **resultado})
