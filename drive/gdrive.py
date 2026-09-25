"""Cliente do Google Drive — dois modos de autenticação.

Degrada com elegância: sem credencial configurada tudo levanta
``DriveNaoConfigurado`` e as telas mostram o guia de configuração em vez de
quebrar (mesmo padrão do resto do portal com serviços externos).

**Conta de serviço** (padrão, server-to-server): a empresa compartilha as
pastas com o e-mail da conta de serviço. Ela só enxerga o que foi compartilhado
com ela. Para ver TODOS os arquivos de uma conta existe a delegação em todo o
domínio — que exige Google Workspace (Admin console). Config em settings:

    GOOGLE_DRIVE_SA_FILE   caminho do JSON da chave da service account, OU
    GOOGLE_DRIVE_SA_JSON   o próprio JSON (conteúdo) da chave
    GOOGLE_DRIVE_IMPERSONATE  (opcional) e-mail para delegação em todo o domínio

**Conta própria (OAuth)**: para quem NÃO tem Workspace. O dono da conta
autoriza o portal uma vez na tela de consentimento do Google e o portal guarda
um refresh token; a partir daí age como ele e enxerga o Meu Drive inteiro, sem
precisar compartilhar pasta nenhuma. Configurado em /drive/configuracao/.

O fluxo OAuth é feito com HTTP direto (montar a URL de consentimento e trocar o
código por token são duas chamadas simples). Assim o portal não ganha a
dependência `google-auth-oauthlib` só para isso — `google-auth`, que já está no
requirements, dá conta de renovar o token sozinho a partir do refresh token.
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

# Endpoints do OAuth do Google (fluxo de código de autorização).
OAUTH_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
OAUTH_TOKEN_URL = 'https://oauth2.googleapis.com/token'
OAUTH_REVOKE_URL = 'https://oauth2.googleapis.com/revoke'

# A raiz do "Meu Drive" na API é o id literal 'root'.
RAIZ_MEU_DRIVE = 'root'

# Campos pedidos à API em cada arquivo/pasta.
FIELDS = ('id,name,mimeType,size,modifiedTime,createdTime,iconLink,thumbnailLink,'
          'webViewLink,webContentLink,parents,trashed,version,'
          'lastModifyingUser(displayName,emailAddress),owners(displayName),md5Checksum,'
          'shortcutDetails(targetId,targetMimeType)')

SHORTCUT_MIME = 'application/vnd.google-apps.shortcut'

_lock = threading.Lock()
_service = None
_fingerprint = None


class DriveError(Exception):
    """Falha genérica ao falar com o Google Drive."""


class DriveNaoConfigurado(DriveError):
    """Credencial do Google Drive ausente/ inválida."""


# ─── Autenticação ────────────────────────────────────────────────────────────

def _config():
    """DriveConfig (id=1), sem quebrar se a tabela ainda não existir.

    É lida duas vezes por chamada ao Google (credencial configurada? mudou?).
    Isso só pesava porque a subida da árvore fazia uma chamada por arquivo;
    com os pais vindo em lote (`pais_de`), sobram poucas chamadas por tela e
    a leitura pode continuar direta — assim a troca de credencial pela tela
    vale na hora, sem janela de configuração velha.
    """
    try:
        from .models import DriveConfig
        return DriveConfig.objects.filter(pk=1).first()
    except Exception:
        return None


def configurado() -> bool:
    """Há credencial — conta própria conectada, JSON na tela ou no .env?"""
    cfg = _config()
    if cfg and cfg.usa_conta_propria:
        return cfg.oauth_pronto
    if cfg and cfg.sa_json:
        return True
    return bool((getattr(settings, 'GOOGLE_DRIVE_SA_JSON', '') or '').strip()
                or (getattr(settings, 'GOOGLE_DRIVE_SA_FILE', '') or '').strip())


def _marca():
    """Impressão digital BARATA da credencial vigente (não lê o arquivo).

    Serve para refazer o cliente quando o SUPERADMIN troca a chave pela tela —
    mesmo em outro worker do gunicorn, que só vê a mudança pelo banco.
    """
    cfg = _config()
    if cfg and cfg.usa_conta_propria:
        ts = cfg.oauth_conectado_em.isoformat() if cfg.oauth_conectado_em else ''
        # O refresh token não entra na marca: ela vai para log/erro e nada que
        # identifique o segredo pode passar por aí. Conta + data já mudam
        # sempre que a conexão é refeita.
        return f'oauth:{cfg.oauth_client_id}:{cfg.oauth_email}:{ts}'
    if cfg and cfg.sa_json:
        ts = cfg.atualizado_em.isoformat() if cfg.atualizado_em else ''
        return f'db:{cfg.sa_json.name}:{cfg.impersonate_email}:{ts}'
    return 'env:' + '|'.join([
        (getattr(settings, 'GOOGLE_DRIVE_SA_JSON', '') or '')[:24],
        getattr(settings, 'GOOGLE_DRIVE_SA_FILE', '') or '',
        getattr(settings, 'GOOGLE_DRIVE_IMPERSONATE', '') or '',
    ])


def _credenciais():
    """Credencial vigente: conta própria (OAuth) ou conta de serviço."""
    cfg = _config()
    if cfg and cfg.usa_conta_propria:
        return _credenciais_oauth(cfg)

    from google.oauth2 import service_account

    info, arquivo, subject = None, '', ''
    if cfg and cfg.sa_json:
        try:
            cfg.sa_json.open('rb')
            dados = cfg.sa_json.read()
            cfg.sa_json.close()
            info = json.loads(dados.decode('utf-8'))
        except Exception as exc:  # noqa: BLE001
            raise DriveNaoConfigurado(f'Credencial enviada inválida: {exc}')
        subject = (cfg.impersonate_email or '').strip()
    else:
        raw = (getattr(settings, 'GOOGLE_DRIVE_SA_JSON', '') or '').strip()
        arquivo = (getattr(settings, 'GOOGLE_DRIVE_SA_FILE', '') or '').strip()
        subject = (getattr(settings, 'GOOGLE_DRIVE_IMPERSONATE', '') or '').strip()
        if raw.startswith('{'):
            info = json.loads(raw)
        elif raw and not arquivo and os.path.exists(raw):
            arquivo = raw

    if info is not None:
        cred = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    elif arquivo and os.path.exists(arquivo):
        cred = service_account.Credentials.from_service_account_file(arquivo, scopes=SCOPES)
    else:
        raise DriveNaoConfigurado('Credencial do Google Drive não configurada.')

    if subject:
        cred = cred.with_subject(subject)
    return cred


def _credenciais_oauth(cfg):
    """Credencial de usuário a partir do refresh token guardado.

    `google-auth` renova o access token sozinho quando ele vence — só precisa
    do refresh token, do client id e do segredo.
    """
    from google.oauth2.credentials import Credentials

    if not cfg.oauth_pronto:
        raise DriveNaoConfigurado(
            'Conta Google não conectada. Vá em Drive → Configuração e clique '
            'em "Conectar minha conta Google".')
    return Credentials(
        token=None,
        refresh_token=cfg.oauth_refresh_token,
        client_id=cfg.oauth_client_id,
        client_secret=cfg.oauth_client_secret,
        token_uri=OAUTH_TOKEN_URL,
        scopes=SCOPES,
    )


# ─── OAuth: consentimento e troca de código ──────────────────────────────────

def url_de_consentimento(client_id, redirect_uri, state):
    """A URL para onde mandar o dono da conta autorizar o portal.

    `access_type=offline` + `prompt=consent` são o que garantem o refresh
    token: sem os dois, uma segunda autorização volta sem refresh token e a
    conexão morre quando o access token vence (uma hora depois).
    """
    from urllib.parse import urlencode

    return OAUTH_AUTH_URL + '?' + urlencode({
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': ' '.join(SCOPES),
        'access_type': 'offline',
        'prompt': 'consent',
        'include_granted_scopes': 'true',
        'state': state,
    })


def trocar_codigo(codigo, client_id, client_secret, redirect_uri):
    """Troca o código do callback por tokens. Devolve o dict do Google."""
    import requests

    try:
        resp = requests.post(OAUTH_TOKEN_URL, timeout=30, data={
            'code': codigo,
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
        })
    except Exception as exc:  # noqa: BLE001
        raise DriveError(f'Falha ao falar com o Google: {exc}') from exc

    dados = {}
    try:
        dados = resp.json()
    except Exception:  # noqa: BLE001
        pass
    if resp.status_code != 200:
        # `error_description` do Google é texto de diagnóstico, não segredo.
        motivo = dados.get('error_description') or dados.get('error') or resp.text[:200]
        raise DriveError(f'O Google recusou a autorização: {motivo}')
    if not dados.get('refresh_token'):
        raise DriveError(
            'O Google não devolveu o refresh token. Isso acontece quando a conta '
            'já havia autorizado o app antes: remova o acesso em '
            'myaccount.google.com/permissions e conecte de novo.')
    return dados


def revogar(refresh_token):
    """Desfaz a autorização no lado do Google. Falha em silêncio."""
    import requests

    try:
        requests.post(OAUTH_REVOKE_URL, timeout=15,
                      data={'token': refresh_token})
    except Exception:  # noqa: BLE001
        pass


def service():
    """Cliente da API v3, cacheado — refeito se a credencial mudar."""
    global _service, _fingerprint
    if not configurado():
        raise DriveNaoConfigurado('Credencial do Google Drive não configurada.')
    marca = _marca()
    if _service is None or _fingerprint != marca:
        with _lock:
            if _service is None or _fingerprint != marca:
                from googleapiclient.discovery import build
                _service = build('drive', 'v3', credentials=_credenciais(), cache_discovery=False)
                _fingerprint = marca
    return _service


def resetar():
    """Esquece o cliente cacheado (chamado após trocar a credencial pela tela)."""
    global _service, _fingerprint
    with _lock:
        _service = None
        _fingerprint = None


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
    """Parâmetro aceito por TODA chamada de arquivo (get, update, create…).

    `includeItemsFromAllDrives` ficava aqui junto e só existe em `files.list`.
    Nas outras, a biblioteca do Google recusa o parâmetro ao MONTAR a
    requisição (TypeError), antes de `_executar` poder traduzir o erro — e
    abrir pasta, abrir arquivo ou baixar virava erro 500.
    """
    return {'supportsAllDrives': True}


def _params_lista():
    """Parâmetros de `files.list`: aí sim cabe incluir os Drives Compartilhados."""
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
        fields=f'nextPageToken, files({FIELDS})', **_params_lista()))
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

    return _baixar_para_memoria(req), nome, alvo_mime


def _baixar_para_memoria(req):
    from googleapiclient.http import MediaIoBaseDownload

    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        try:
            _, done = downloader.next_chunk()
        except Exception as exc:  # noqa: BLE001
            raise DriveError(f'Falha no download: {exc}') from exc
    buf.seek(0)
    return buf


def exportar(file_id, mime_alvo):
    """Bytes de um arquivo nativo do Google (Doc, Planilha…) exportado para `mime_alvo`.

    `export_media` não aceita `supportsAllDrives`: passar o parâmetro quebra ao
    montar a requisição — o mesmo tipo de erro que já derrubou o Meu Drive.
    """
    req = service().files().export_media(fileId=file_id, mimeType=mime_alvo)
    return _baixar_para_memoria(req).read()


def converter_para_pdf(file_id, mime_google, nome=''):
    """PDF de um Word/Excel/PowerPoint, convertido pelo próprio Google Drive.

    Copia o arquivo como Doc/Planilha/Apresentação, exporta a cópia em PDF e
    apaga a cópia — que sai mesmo quando a exportação falha. Com a conta
    própria, a cópia vai para a raiz, e não para a pasta da equipe, onde
    apareceria por alguns segundos. Conta de serviço não tem cota para guardar
    a cópia na raiz dela: aí a cópia herda a pasta do original.
    """
    corpo = {'name': f'Visualização temporária do portal — {nome}'[:200], 'mimeType': mime_google}
    cfg = _config()
    if cfg and cfg.usa_conta_propria:
        corpo['parents'] = [RAIZ_MEU_DRIVE]
    copia = _executar(service().files().copy(fileId=file_id, fields='id', body=corpo, **_params()))
    try:
        return exportar(copia['id'], 'application/pdf')
    finally:
        try:
            excluir_definitivo(copia['id'])
        except DriveError:
            try:
                para_lixeira(copia['id'])
            except DriveError as exc:
                logger.warning('Cópia temporária de visualização não removida (%s): %s',
                               copia['id'], exc)


def pai_de(file_id):
    """Primeira pasta-pai de um item: '' no topo, None quando o Google não respondeu.

    A diferença importa para o cache da lixeira: o topo pode ser guardado; uma
    falha passageira, não — esconderia os itens daquela pasta por minutos.
    """
    try:
        meta = obter(file_id, fields='id,parents')
    except DriveError:
        return None
    return (meta.get('parents') or [''])[0]


# O `batch` do Google aceita até 100 sub-pedidos por chamada.
LOTE_MAXIMO = 100


def pais_de(ids):
    """{id: pasta-pai} de vários itens de uma vez, pelo `batch` do Google.

    Mesma resposta de `pai_de` para cada item ('' no topo), só que sem pagar um
    ida-e-volta por arquivo: 60 itens levavam ~21 s um a um e levam ~1,2 s num
    lote só. O id que o Google não responder fica de fora do dicionário — quem
    chama decide (o cache da subida, por exemplo, não guarda falha).
    """
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return {}
    achados = {}

    def guardar(request_id, resposta, excecao):
        if excecao is None and resposta is not None:
            achados[request_id] = (resposta.get('parents') or [''])[0]

    for inicio in range(0, len(ids), LOTE_MAXIMO):
        pedaco = ids[inicio:inicio + LOTE_MAXIMO]
        try:
            servico = service()
            lote = servico.new_batch_http_request(callback=guardar)
            for file_id in pedaco:
                lote.add(servico.files().get(fileId=file_id, fields='id,parents', **_params()),
                         request_id=file_id)
            lote.execute()
        except Exception as exc:  # noqa: BLE001
            # Sem lote (proxy, versão da API, dublê de teste): vai um a um, que
            # é lento mas continua funcionando.
            logger.debug('Lote de pais indisponível (%s); indo um a um.', exc)
            for file_id in pedaco:
                pai = pai_de(file_id)
                if pai is not None:
                    achados[file_id] = pai
    return achados


def baixar_trecho(file_id, inicio, fim):
    """Bytes de `inicio` a `fim` (inclusive): vídeo e áudio tocam sem baixar o arquivo todo."""
    req = service().files().get_media(fileId=file_id, **_params())
    req.headers['Range'] = f'bytes={inicio}-{fim}'
    return _executar(req)


_raizes = {}


def id_da_raiz():
    """O id real do "Meu Drive" da conta conectada.

    A API aceita o apelido 'root' para listar, mas os `parents` dos arquivos
    trazem o id verdadeiro. Sem ele a trilha de navegação mostrava "Meu Drive"
    duas vezes. Guardado por credencial: trocar de conta troca a raiz.
    """
    marca = _marca()
    if marca not in _raizes:
        try:
            _raizes[marca] = obter(RAIZ_MEU_DRIVE, fields='id').get('id', '')
        except DriveError:
            return ''
    return _raizes[marca]


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
        orderBy='modifiedTime desc', fields=f'nextPageToken, files({FIELDS})', **_params_lista()))
    return resp.get('files', []), resp.get('nextPageToken')


def listar_lixeira(page_token=None, page_size=100):
    resp = _executar(service().files().list(
        q='trashed=true', pageSize=page_size, pageToken=page_token,
        orderBy='modifiedTime desc', fields=f'nextPageToken, files({FIELDS})', **_params_lista()))
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
