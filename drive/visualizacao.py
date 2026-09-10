"""Como cada arquivo do Drive abre DENTRO do portal.

Antes só PDF, imagem e Docs do Google abriam; Word, Excel, PowerPoint, texto e
vídeo caíam em "Baixar para abrir". A decisão de como cada tipo é mostrado, e
em que formatos pode ser baixado, fica aqui — num lugar só, para a tela por
setor e o Meu Drive não divergirem.

Word/Excel/PowerPoint viram PDF pelo PRÓPRIO Google Drive (copia como Doc,
Planilha ou Apresentação, exporta PDF e apaga a cópia): não precisa de
LibreOffice no servidor. A conversão demora alguns segundos, então o PDF fica
guardado, cifrado, pela versão do arquivo — a segunda abertura é imediata e uma
versão nova gera outra prévia.
"""
import base64
import hashlib
import logging
import re

from django.conf import settings
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

GOOGLE_DOC = 'application/vnd.google-apps.document'
GOOGLE_PLANILHA = 'application/vnd.google-apps.spreadsheet'
GOOGLE_APRESENTACAO = 'application/vnd.google-apps.presentation'
GOOGLE_DESENHO = 'application/vnd.google-apps.drawing'

PDF = 'application/pdf'

# Office/OpenDocument → tipo nativo do Google para o qual o próprio Drive converte.
CONVERSIVEIS = {
    'application/msword': GOOGLE_DOC,
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': GOOGLE_DOC,
    'application/vnd.oasis.opendocument.text': GOOGLE_DOC,
    'application/rtf': GOOGLE_DOC,
    'text/rtf': GOOGLE_DOC,
    'application/vnd.ms-excel': GOOGLE_PLANILHA,
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': GOOGLE_PLANILHA,
    'application/vnd.oasis.opendocument.spreadsheet': GOOGLE_PLANILHA,
    'application/vnd.ms-powerpoint': GOOGLE_APRESENTACAO,
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': GOOGLE_APRESENTACAO,
    'application/vnd.oasis.opendocument.presentation': GOOGLE_APRESENTACAO,
}

# Formatos em que o Google exporta os arquivos nativos dele: (código, rótulo, mime, extensão).
EXPORTACOES = {
    GOOGLE_DOC: [
        ('pdf', 'PDF', PDF, '.pdf'),
        ('docx', 'Word (.docx)',
         'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.docx'),
        ('odt', 'OpenDocument (.odt)', 'application/vnd.oasis.opendocument.text', '.odt'),
        ('rtf', 'RTF (.rtf)', 'application/rtf', '.rtf'),
        ('txt', 'Texto (.txt)', 'text/plain', '.txt'),
    ],
    GOOGLE_PLANILHA: [
        ('pdf', 'PDF', PDF, '.pdf'),
        ('xlsx', 'Excel (.xlsx)',
         'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'),
        ('ods', 'OpenDocument (.ods)', 'application/vnd.oasis.opendocument.spreadsheet', '.ods'),
        ('csv', 'CSV (1ª aba)', 'text/csv', '.csv'),
    ],
    GOOGLE_APRESENTACAO: [
        ('pdf', 'PDF', PDF, '.pdf'),
        ('pptx', 'PowerPoint (.pptx)',
         'application/vnd.openxmlformats-officedocument.presentationml.presentation', '.pptx'),
        ('odp', 'OpenDocument (.odp)', 'application/vnd.oasis.opendocument.presentation', '.odp'),
    ],
    GOOGLE_DESENHO: [
        ('pdf', 'PDF', PDF, '.pdf'),
        ('png', 'Imagem (.png)', 'image/png', '.png'),
    ],
}

# O formato padrão do "Baixar" de um arquivo nativo do Google: o Office dele.
DOWNLOAD_PADRAO_GOOGLE = {GOOGLE_DOC: 'docx', GOOGLE_PLANILHA: 'xlsx',
                          GOOGLE_APRESENTACAO: 'pptx', GOOGLE_DESENHO: 'pdf'}

# O que o navegador mostra sozinho. HEIC, TIFF, AVI, WMV, MKV… ficam no botão
# de baixar: uma imagem quebrada ou um player mudo na tela é pior que ele.
IMAGENS = {'image/png', 'image/jpeg', 'image/jpg', 'image/pjpeg', 'image/gif', 'image/webp',
           'image/bmp', 'image/svg+xml', 'image/x-icon', 'image/vnd.microsoft.icon', 'image/avif'}
VIDEOS = {'video/mp4', 'video/webm', 'video/ogg', 'video/quicktime'}
AUDIOS = {'audio/mpeg', 'audio/mp3', 'audio/wav', 'audio/x-wav', 'audio/wave', 'audio/ogg',
          'audio/webm', 'audio/aac', 'audio/mp4', 'audio/x-m4a', 'audio/flac', 'audio/x-flac'}

TEXTO_EXTRA = {
    'application/json', 'application/xml', 'application/x-yaml', 'application/yaml',
    'application/sql', 'application/x-sql', 'application/javascript', 'application/x-sh',
}

# O navegador EXECUTARIA estes tipos se servidos com o próprio mime na origem
# do portal (um .html ou .svg enviado ao Drive rodaria script logado como quem
# abriu). Texto sai como text/plain; imagem SVG sai com CSP de sandbox.
EXECUTAVEIS = {'text/html', 'application/xhtml+xml', 'image/svg+xml',
               'text/javascript', 'application/javascript', 'text/xml', 'application/xml'}


def tipo(mime):
    """Como o arquivo aparece no portal, ou None quando não há visualização."""
    m = (mime or '').lower()
    if m == PDF:
        return 'pdf'
    if m in IMAGENS:
        return 'imagem'
    if m in VIDEOS:
        return 'video'
    if m in AUDIOS:
        return 'audio'
    if m in EXPORTACOES:
        return 'google'
    if m in CONVERSIVEIS:
        return 'office'
    if m.startswith('text/') or m in TEXTO_EXTRA:
        return 'texto'
    return None


def vira_pdf(mime):
    """A visualização deste tipo é um PDF gerado (Office ou nativo do Google)?"""
    return tipo(mime) in ('office', 'google')


def _extensao(nome):
    return nome.rsplit('.', 1)[-1].lower() if nome and '.' in nome else ''


def formatos_de_download(meta):
    """[(código, rótulo)] do "Baixar como" deste arquivo."""
    mime = meta.get('mimeType') or ''
    if mime in EXPORTACOES:
        return [(c, r) for c, r, _m, _e in EXPORTACOES[mime]]
    ext = _extensao(meta.get('name', ''))
    saida = [('original', f'Original (.{ext})' if ext else 'Original')]
    if tipo(mime) == 'office':
        saida.append(('pdf', 'PDF'))
    return saida


def exportacao(mime, codigo):
    """(mime de destino, extensão) de uma exportação válida, ou None."""
    for c, _r, alvo, ext in EXPORTACOES.get(mime or '', []):
        if c == codigo:
            return alvo, ext
    return None


def etag(meta, variante=''):
    """Muda quando o arquivo muda — o navegador revalida em vez de baixar de novo."""
    base = '|'.join(str(meta.get(k) or '') for k in ('id', 'md5Checksum', 'version', 'modifiedTime'))
    return '"' + hashlib.sha1(f'{base}|{variante}'.encode()).hexdigest()[:32] + '"'


# ─── Prévia em PDF guardada no servidor ─────────────────────────────────────

PREFIXO = 'drive/previas'
# Sem ponto: nenhum pedaço do caminho vira "..".
_SEGURO = re.compile(r'[^A-Za-z0-9_-]')


def storage_das_previas():
    """Onde a prévia fica: S3 em produção; pasta do servidor fora da mídia sem S3."""
    if getattr(settings, 'USE_S3', False):
        from .storage import DrivePreviewStorage
        return DrivePreviewStorage()
    from django.core.files.storage import FileSystemStorage
    return FileSystemStorage(location=str(settings.BASE_DIR / 'var' / 'drive_previas'))


def _cifra():
    """Fernet com chave derivada da SECRET_KEY.

    A prévia É o conteúdo do documento, e o bucket de mídia do portal é
    público. ACL privada por objeto não é garantia em todo S3 compatível — no
    MinIO, por exemplo, quem decide o acesso é a política do bucket. Cifrada, a
    cópia no storage não serve para nada sem o servidor.
    """
    from cryptography.fernet import Fernet

    chave = hashlib.sha256(f'drive-previas|{settings.SECRET_KEY}'.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(chave))


def caminho_da_previa(meta):
    versao = meta.get('md5Checksum') or f"v{meta.get('version') or 0}-{meta.get('modifiedTime') or ''}"
    return f"{PREFIXO}/{_SEGURO.sub('_', meta['id'])}/{_SEGURO.sub('_', versao)}.enc"


def _limpar_previas(armazenamento, file_id):
    """Tira do storage as prévias deste arquivo antes de guardar a nova.

    Inclusive a de mesmo nome: o storage de disco (sem S3) não sobrescreve —
    salvaria com outro nome e a leitura continuaria achando a antiga.
    """
    pasta = f"{PREFIXO}/{_SEGURO.sub('_', file_id)}"
    try:
        _pastas, arquivos = armazenamento.listdir(pasta)
    except Exception:  # noqa: BLE001 — pasta ainda não existe
        return
    for nome in arquivos:
        try:
            armazenamento.delete(f'{pasta}/{nome}')
        except Exception:  # noqa: BLE001
            pass


def pdf_da_previa(meta):
    """Bytes do PDF que representa o arquivo no portal.

    Lê do storage quando esta versão já foi convertida; senão converte pelo
    Google e guarda, cifrado. Falha do storage não impede a visualização — só
    perde o cache. Falha da conversão sobe como DriveError para a tela explicar.
    """
    from cryptography.fernet import InvalidToken

    from . import gdrive

    armazenamento = storage_das_previas()
    caminho = caminho_da_previa(meta)
    try:
        if armazenamento.exists(caminho):
            with armazenamento.open(caminho, 'rb') as fh:
                return _cifra().decrypt(fh.read())
    except InvalidToken:
        # Guardada com outra SECRET_KEY: converte de novo e substitui.
        logger.info('Prévia do Drive ilegível com a chave atual: %s', caminho)
    except Exception as exc:  # noqa: BLE001
        logger.warning('Prévia do Drive não lida do storage: %s', exc)

    mime = meta.get('mimeType') or ''
    if tipo(mime) == 'google':
        dados = gdrive.exportar(meta['id'], PDF)
    elif tipo(mime) == 'office':
        dados = gdrive.converter_para_pdf(meta['id'], CONVERSIVEIS[mime], meta.get('name', ''))
    else:
        raise ValueError(f'Tipo sem prévia em PDF: {mime}')

    try:
        _limpar_previas(armazenamento, meta['id'])
        armazenamento.save(caminho, ContentFile(_cifra().encrypt(dados)))
    except Exception as exc:  # noqa: BLE001
        logger.warning('Prévia do Drive não guardada: %s', exc)
    return dados
