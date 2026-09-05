"""Cliente do Google Drive (service account).

Degrada com elegância: sem credencial configurada tudo levanta
``DriveNaoConfigurado`` e as telas mostram o guia de configuração em vez de
quebrar (mesmo padrão do resto do portal com serviços externos).

Autenticação por **service account** (server-to-server, sem login de usuário).
A empresa compartilha a(s) pasta(s)/Drive Compartilhado com o e-mail da service
account. Config em settings (lidas do .env):

    GOOGLE_DRIVE_SA_FILE   caminho do JSON da chave da service account, OU
    GOOGLE_DRIVE_SA_JSON   o próprio JSON (conteúdo) da chave
    GOOGLE_DRIVE_IMPERSONATE  (opcional) e-mail para delegação em todo o domínio
"""
import io
import json
import logging
import os
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive']
FOLDER_MIME = 'application/vnd.google-apps.folder'

# Campos pedidos à API em cada arquivo/pasta.
FIELDS = ('id,name,mimeType,size,modifiedTime,createdTime,iconLink,thumbnailLink,'
          'webViewLink,webContentLink,parents,trashed,version,'
          'lastModifyingUser(displayName,emailAddress),owners(displayName)')

_lock = threading.Lock()
_service = None


class DriveError(Exception):
    """Falha genérica ao falar com o Google Drive."""


class DriveNaoConfigurado(DriveError):
    """Credencial do Google Drive ausente/ inválida."""


# ─── Autenticação ────────────────────────────────────────────────────────────

def configurado() -> bool:
    """Há credencial apontada em settings?"""
    return bool(getattr(settings, 'GOOGLE_DRIVE_SA_JSON', '')
                or getattr(settings, 'GOOGLE_DRIVE_SA_FILE', ''))


def _credenciais():
    from google.oauth2 import service_account

    raw = (getattr(settings, 'GOOGLE_DRIVE_SA_JSON', '') or '').strip()
    arquivo = (getattr(settings, 'GOOGLE_DRIVE_SA_FILE', '') or '').strip()

    if raw.startswith('{'):
        cred = service_account.Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    elif arquivo and os.path.exists(arquivo):
        cred = service_account.Credentials.from_service_account_file(arquivo, scopes=SCOPES)
    elif raw and os.path.exists(raw):
        cred = service_account.Credentials.from_service_account_file(raw, scopes=SCOPES)
    else:
        raise DriveNaoConfigurado('Credencial do Google Drive não configurada.')

    subject = (getattr(settings, 'GOOGLE_DRIVE_IMPERSONATE', '') or '').strip()
    if subject:
        cred = cred.with_subject(subject)
    return cred


def service():
    """Cliente da API v3, cacheado no processo."""
    global _service
    if not configurado():
        raise DriveNaoConfigurado('Credencial do Google Drive não configurada.')
    if _service is None:
        with _lock:
            if _service is None:
                from googleapiclient.discovery import build
                _service = build('drive', 'v3', credentials=_credenciais(), cache_discovery=False)
    return _service


def testar_conexao():
    """(ok, mensagem) — usada na tela de configuração."""
    if not configurado():
        return False, 'Credencial não configurada. Siga o guia do Google Cloud Console.'
    try:
        sobre = service().about().get(fields='user(emailAddress),storageQuota').execute()
        email = (sobre.get('user') or {}).get('emailAddress', '?')
        return True, f'Conectado como {email}.'
    except DriveError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f'Falha ao conectar: {exc}'


def _params():
    """Parâmetros para enxergar Drives Compartilhados."""
    return {'supportsAllDrives': True, 'includeItemsFromAllDrives': True}


def _executar(req):
    """Executa uma chamada, traduzindo erros da API para DriveError."""
    try:
        return req.execute()
    except DriveNaoConfigurado:
        raise
    except Exception as exc:  # noqa: BLE001
        from googleapiclient.errors import HttpError
        if isinstance(exc, HttpError):
            raise DriveError(f'Google Drive respondeu {exc.resp.status}: {exc._get_reason()}') from exc
        raise DriveError(str(exc)) from exc


# ─── Leitura ─────────────────────────────────────────────────────────────────

def listar(folder_id, page_token=None, page_size=100, trashed=False, apenas_pastas=False, order='folder,name'):
    """Filhos diretos de uma pasta (pastas primeiro por padrão). Paginado."""
    q = f"'{folder_id}' in parents and trashed={str(trashed).lower()}"
    if apenas_pastas:
        q += f" and mimeType='{FOLDER_MIME}'"
    resp = _executar(service().files().list(
        q=q, pageSize=page_size, pageToken=page_token, orderBy=order,
        fields=f'nextPageToken, files({FIELDS})', **_params()))
    return resp.get('files', []), resp.get('nextPageToken')


def obter(file_id, fields=FIELDS):
    return _executar(service().files().get(fileId=file_id, fields=fields, **_params()))


def ancestrais(file_id, limite=30):
    """Cadeia de ids do próprio arquivo subindo pelos ``parents`` até o topo.

    Base do controle contra URL direta (RNF05): valida a que setor um arquivo
    pertence. Limitado em profundidade para nunca virar loop.
    """
    ids, atual, visto = [], file_id, set()
    for _ in range(limite):
        if not atual or atual in visto:
            break
        visto.add(atual)
        ids.append(atual)
        try:
            meta = _executar(service().files().get(fileId=atual, fields='id,parents', **_params()))
        except DriveError:
            break
        pais = meta.get('parents') or []
        atual = pais[0] if pais else None
    return ids


def dentro_de(file_id, root_id):
    """``file_id`` é, ou está abaixo de, ``root_id``?"""
    if not file_id or not root_id:
        return False
    if file_id == root_id:
        return True
    return root_id in ancestrais(file_id)


def caminho(file_id, ate_root=None, limite=30):
    """Lista [(id, nome), ...] do root (ou topo) até o item — para breadcrumbs."""
    trilha, atual, visto = [], file_id, set()
    for _ in range(limite):
        if not atual or atual in visto:
            break
        visto.add(atual)
        try:
            meta = _executar(service().files().get(fileId=atual, fields='id,name,parents', **_params()))
        except DriveError:
            break
        trilha.append((meta['id'], meta.get('name', '')))
        if ate_root and meta['id'] == ate_root:
            break
        pais = meta.get('parents') or []
        atual = pais[0] if pais else None
    return list(reversed(trilha))


def baixar(file_id, preview=False):
    """(BytesIO, nome, mimetype).

    Google Docs/Sheets/Slides são exportados: para ``preview`` vira PDF (bom para
    ver no navegador); para download vira o Office equivalente (docx/xlsx/pptx).
    """
    from googleapiclient.http import MediaIoBaseDownload

    meta = obter(file_id, fields='id,name,mimeType')
    mime = meta.get('mimeType', '')
    svc = service()
    if mime.startswith('application/vnd.google-apps'):
        if preview:
            alvo_mime, ext = 'application/pdf', '.pdf'
        else:
            export = {
                'application/vnd.google-apps.document':
                    ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.docx'),
                'application/vnd.google-apps.spreadsheet':
                    ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'),
                'application/vnd.google-apps.presentation':
                    ('application/vnd.openxmlformats-officedocument.presentationml.presentation', '.pptx'),
            }
            alvo_mime, ext = export.get(mime, ('application/pdf', '.pdf'))
        req = svc.files().export_media(fileId=file_id, mimeType=alvo_mime)
        nome = meta['name'] + ext
    else:
        req = svc.files().get_media(fileId=file_id, **_params())
        nome, alvo_mime = meta['name'], mime or 'application/octet-stream'

    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        try:
            _, done = downloader.next_chunk()
        except Exception as exc:  # noqa: BLE001
            raise DriveError(f'Falha no download: {exc}') from exc
    buf.seek(0)
    return buf, nome, alvo_mime


def revisoes(file_id):
    resp = _executar(service().revisions().list(
        fileId=file_id,
        fields='revisions(id,modifiedTime,size,keepForever,lastModifyingUser(displayName))'))
    return resp.get('revisions', [])


def buscar(nome='', mime='', page_token=None, page_size=50):
    """Busca por nome (contains) opcionalmente filtrada por tipo. O recorte por
    setor/pasta é feito no back (por descendência), nunca só aqui."""
    partes = ['trashed=false']
    if nome:
        seguro = nome.replace("'", "\\'")
        partes.append(f"name contains '{seguro}'")
    if mime:
        partes.append(f"mimeType='{mime}'")
    resp = _executar(service().files().list(
        q=' and '.join(partes), pageSize=page_size, pageToken=page_token,
        orderBy='modifiedTime desc', fields=f'nextPageToken, files({FIELDS})', **_params()))
    return resp.get('files', []), resp.get('nextPageToken')


def listar_lixeira(page_token=None, page_size=100):
    resp = _executar(service().files().list(
        q='trashed=true', pageSize=page_size, pageToken=page_token,
        orderBy='modifiedTime desc', fields=f'nextPageToken, files({FIELDS})', **_params()))
    return resp.get('files', []), resp.get('nextPageToken')


# ─── Escrita ─────────────────────────────────────────────────────────────────

def criar_pasta(nome, parent_id):
    body = {'name': nome, 'mimeType': FOLDER_MIME, 'parents': [parent_id]}
    return _executar(service().files().create(body=body, fields=FIELDS, **_params()))


def enviar(nome, mimetype, stream, parent_id):
    from googleapiclient.http import MediaIoBaseUpload
    media = MediaIoBaseUpload(stream, mimetype=mimetype or 'application/octet-stream', resumable=True)
    body = {'name': nome, 'parents': [parent_id]}
    return _executar(service().files().create(body=body, media_body=media, fields=FIELDS, **_params()))


def nova_versao(file_id, stream, mimetype=None):
    """Substitui o conteúdo — o Google guarda a anterior como revisão (RF20)."""
    from googleapiclient.http import MediaIoBaseUpload
    media = MediaIoBaseUpload(stream, mimetype=mimetype or 'application/octet-stream', resumable=True)
    return _executar(service().files().update(fileId=file_id, media_body=media, fields=FIELDS, **_params()))


def renomear(file_id, novo_nome):
    return _executar(service().files().update(
        fileId=file_id, body={'name': novo_nome}, fields=FIELDS, **_params()))


def mover(file_id, novo_parent, parent_atual=None):
    if not parent_atual:
        meta = obter(file_id, fields='parents')
        parent_atual = ','.join(meta.get('parents') or [])
    return _executar(service().files().update(
        fileId=file_id, addParents=novo_parent, removeParents=parent_atual,
        fields=FIELDS, **_params()))


def para_lixeira(file_id, trashed=True):
    return _executar(service().files().update(
        fileId=file_id, body={'trashed': trashed}, fields=FIELDS, **_params()))


def excluir_definitivo(file_id):
    return _executar(service().files().delete(fileId=file_id, **_params()))
