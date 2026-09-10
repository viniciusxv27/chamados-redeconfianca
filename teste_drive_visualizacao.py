"""Drive: abrir arquivos no portal, escolher o formato, e lixeira em partes.

Pedido: não dava para abrir os arquivos pelo portal (só baixando), nem
escolher o formato; e o botão da lixeira era lento.

- Word, Excel e PowerPoint abrem em PDF convertido pelo próprio Google; a
  prévia fica guardada (cifrada) por versão e a 2ª abertura não converte.
- Docs do Google e Office baixam no formato escolhido ("Baixar como").
- Texto abre como texto puro: um .html do Drive não roda script no portal.
- Vídeo e áudio tocam em trechos (Range), como o Safari exige.
- O navegador guarda o que já recebeu (ETag → 304).
- A lixeira abre sem esperar o Google e traz os itens em partes.

Roda numa transação desfeita no fim. Não fala com o Google (Drive falso em
memória) nem com o S3 (prévias numa pasta temporária, apagada no fim).
"""
import io
import os
import shutil
import sys
import tempfile
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.cache.backends.locmem import LocMemCache
from django.core.files.storage import FileSystemStorage
from django.db import transaction
from django.test import Client
from django.test.utils import override_settings
from django.urls import reverse

from drive import gdrive
from drive import views as dviews
from drive import visualizacao as vis
from drive.models import DriveAuditLog, DriveConfig, DrivePermission, SectorDriveMapping
from users.models import Sector

User = get_user_model()
ok = fail = 0

DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
PPTX = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
GDOC, GPLAN = vis.GOOGLE_DOC, vis.GOOGLE_PLANILHA
FOLDER = gdrive.FOLDER_MIME


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


class DriveFalso:
    """Drive em memória com a forma de resposta do Google, contando as chamadas."""

    def __init__(self):
        self.itens, self.conteudo = {}, {}
        self.baixados, self.pais = 0, 0
        self.exportados, self.trechos, self.lixeira = [], [], []

    def add(self, id_, nome, mime, pai, dados=b'', trashed=False):
        self.itens[id_] = dict(
            id=id_, name=nome, mimeType=mime, parents=[pai] if pai else [], trashed=trashed,
            size=str(len(dados)), version='1', md5Checksum=f'md5-{id_}',
            modifiedTime='2026-09-10T10:00:00Z', createdTime='2026-09-01T10:00:00Z',
            webViewLink=f'https://drive.google.com/file/d/{id_}/view')
        self.conteudo[id_] = dados
        return self.itens[id_]

    def obter(self, file_id, fields=None):
        if fields == 'id,parents':
            self.pais += 1
        if file_id not in self.itens:
            raise gdrive.DriveError('Google Drive respondeu 404: File not found')
        return dict(self.itens[file_id])

    def ancestrais(self, file_id, limite=30):
        ids, atual = [], file_id
        while atual and atual not in ids and len(ids) < limite:
            ids.append(atual)
            atual = (self.itens.get(atual, {}).get('parents') or [None])[0]
        return ids

    def baixar(self, file_id, preview=False):
        self.baixados += 1
        meta = self.obter(file_id)
        return io.BytesIO(self.conteudo[file_id]), meta['name'], meta['mimeType']

    def exportar(self, file_id, mime_alvo):
        self.exportados.append((file_id, mime_alvo))
        return f'EXPORTADO {mime_alvo}'.encode()

    def baixar_trecho(self, file_id, inicio, fim):
        self.trechos.append((inicio, fim))
        return self.conteudo[file_id][inicio:fim + 1]

    def listar_lixeira(self, page_token=None, page_size=100):
        self.lixeira.append((page_token, page_size))
        todos = [dict(i) for i in self.itens.values() if i['trashed']]
        ini = int(page_token or 0)
        prox = str(ini + page_size) if ini + page_size < len(todos) else None
        return todos[ini:ini + page_size], prox

    def para_lixeira(self, file_id, trashed=True):
        self.itens[file_id]['trashed'] = trashed
        return dict(self.itens[file_id])


print('== COMO CADA TIPO ABRE NO PORTAL ==')
TIPOS = [
    ('application/pdf', 'pdf'), ('APPLICATION/PDF', 'pdf'),
    ('image/png', 'imagem'), ('image/jpeg', 'imagem'), ('image/svg+xml', 'imagem'),
    ('image/heic', None), ('image/tiff', None),
    ('video/mp4', 'video'), ('video/quicktime', 'video'), ('video/x-msvideo', None), ('video/x-matroska', None),
    ('audio/mpeg', 'audio'), ('audio/x-m4a', 'audio'),
    (DOCX, 'office'), (XLSX, 'office'), (PPTX, 'office'), ('application/msword', 'office'),
    ('application/vnd.ms-excel', 'office'), ('application/vnd.oasis.opendocument.text', 'office'),
    (GDOC, 'google'), (GPLAN, 'google'), (vis.GOOGLE_APRESENTACAO, 'google'), (vis.GOOGLE_DESENHO, 'google'),
    ('application/vnd.google-apps.form', None), (FOLDER, None), (gdrive.SHORTCUT_MIME, None),
    ('text/plain', 'texto'), ('text/csv', 'texto'), ('text/html', 'texto'), ('application/json', 'texto'),
    ('application/zip', None), ('application/octet-stream', None), ('', None), (None, None),
]
for mime, esperado in TIPOS:
    t(f'{mime!r} → {esperado}', vis.tipo(mime) == esperado, vis.tipo(mime))
t('a lista de arquivos segue a mesma regra',
  dviews._previewavel(DOCX) and dviews._previewavel('video/mp4') and not dviews._previewavel('application/zip'))
t('Office e Docs do Google viram PDF; PDF e imagem não',
  vis.vira_pdf(XLSX) and vis.vira_pdf(GDOC) and not vis.vira_pdf('application/pdf') and not vis.vira_pdf('image/png'))

print('\n== "BAIXAR COMO": OS FORMATOS DE CADA TIPO ==')
f = vis.formatos_de_download({'name': 'Contrato.DOCX', 'mimeType': DOCX})
t('Word: original e PDF', [cod for cod, _ in f] == ['original', 'pdf'], f)
t('o original mostra a extensão', f[0] == ('original', 'Original (.docx)'), f)
f = vis.formatos_de_download({'name': 'foto.png', 'mimeType': 'image/png'})
t('imagem: só o original', f == [('original', 'Original (.png)')], f)
f = vis.formatos_de_download({'name': 'LEIAME', 'mimeType': 'text/plain'})
t('nome sem extensão: "Original"', f == [('original', 'Original')], f)
t('Doc do Google: PDF, Word, ODT, RTF, TXT',
  [cod for cod, _ in vis.formatos_de_download({'name': 'Ata', 'mimeType': GDOC})] == ['pdf', 'docx', 'odt', 'rtf', 'txt'])
t('Planilha do Google: PDF, Excel, ODS, CSV',
  [cod for cod, _ in vis.formatos_de_download({'name': 'P', 'mimeType': GPLAN})] == ['pdf', 'xlsx', 'ods', 'csv'])
t('Apresentação do Google: PDF, PowerPoint, ODP',
  [cod for cod, _ in vis.formatos_de_download({'name': 'A', 'mimeType': vis.GOOGLE_APRESENTACAO})] == ['pdf', 'pptx', 'odp'])
t('exportação válida', vis.exportacao(GDOC, 'docx') == (DOCX, '.docx'))
t('formato de outro tipo é recusado', vis.exportacao(GDOC, 'xlsx') is None)
t('arquivo que não é do Google não exporta', vis.exportacao(DOCX, 'pdf') is None)

print('\n== O NAVEGADOR SABE QUANDO O ARQUIVO MUDOU (ETag) ==')
base = {'id': 'A1', 'md5Checksum': 'm1', 'version': '3', 'modifiedTime': '2026-09-10T10:00:00Z'}
e1 = vis.etag(base, 'ver:')
t('ETag forte, entre aspas', e1.startswith('"') and e1.endswith('"') and len(e1) == 34, e1)
t('mesma versão, mesma ETag', vis.etag(dict(base), 'ver:') == e1)
t('conteúdo novo, ETag nova', vis.etag({**base, 'md5Checksum': 'm2'}, 'ver:') != e1)
sem_md5 = {**base, 'md5Checksum': None}
t('Doc do Google (sem md5) muda pela versão', vis.etag({**sem_md5, 'version': '4'}, 'ver:') != vis.etag(sem_md5, 'ver:'))
t('ver e baixar não se confundem', vis.etag(base, 'dl:') != e1)
t('nem formatos diferentes', vis.etag(base, 'dl:pdf') != vis.etag(base, 'dl:docx'))

print('\n== ONDE A PRÉVIA FICA ==')
caminho = vis.caminho_da_previa({'id': 'A1', 'md5Checksum': 'm1'})
t('caminho pela md5', caminho == 'drive/previas/A1/m1.enc', caminho)
caminho = vis.caminho_da_previa({'id': 'G1', 'version': '7', 'modifiedTime': '2026-09-10T10:00:00.000Z'})
t('sem md5, pela versão e data', caminho == 'drive/previas/G1/v7-2026-09-10T10_00_00_000Z.enc', caminho)
caminho = vis.caminho_da_previa({'id': '../../etc', 'md5Checksum': '../x'})
t('id com "../" não sai da pasta das prévias', caminho == 'drive/previas/______etc/___x.enc', caminho)
with override_settings(USE_S3=True):
    st = vis.storage_das_previas()
    t('com S3: storage próprio, privado e com link assinado',
      type(st).__name__ == 'DrivePreviewStorage' and st.default_acl == 'private' and st.querystring_auth is True)
with override_settings(USE_S3=False):
    st = vis.storage_das_previas()
    t('sem S3: pasta do servidor, fora da mídia', st.location.endswith(os.path.join('var', 'drive_previas')), st.location)

pasta_tmp = tempfile.mkdtemp(prefix='zz_drive_previas_')
marcador = transaction.atomic()
marcador.__enter__()
try:
    print('\n== A PRÉVIA GUARDADA: CIFRADA E POR VERSÃO ==')
    disco = FileSystemStorage(location=os.path.join(pasta_tmp, 'unidade'))
    conv = mock.Mock(return_value=b'%PDF-convertido')
    exp = mock.Mock(return_value=b'%PDF-exportado')
    with mock.patch.object(vis, 'storage_das_previas', return_value=disco), \
         mock.patch.object(gdrive, 'converter_para_pdf', conv), \
         mock.patch.object(gdrive, 'exportar', exp):
        word = {'id': 'W1', 'name': 'Contrato.docx', 'mimeType': DOCX, 'md5Checksum': 'aaa'}
        t('1ª abertura converte pelo Google', vis.pdf_da_previa(word) == b'%PDF-convertido' and conv.call_count == 1)
        t('Word vira Doc do Google', conv.call_args.args == ('W1', GDOC, 'Contrato.docx'), conv.call_args)
        t('e a prévia fica guardada', disco.exists('drive/previas/W1/aaa.enc'))
        with disco.open('drive/previas/W1/aaa.enc', 'rb') as fh:
            bruto = fh.read()
        t('cifrada: quem pegar o arquivo do storage não lê o PDF', b'%PDF' not in bruto and len(bruto) > 20)
        t('2ª abertura vem do servidor, sem converter',
          vis.pdf_da_previa(dict(word)) == b'%PDF-convertido' and conv.call_count == 1, conv.call_count)
        with override_settings(SECRET_KEY='zz-outra-chave-so-no-teste'):
            conv.return_value = b'%PDF-nova-chave'
            t('chave trocada: não devolve lixo, converte de novo',
              vis.pdf_da_previa(dict(word)) == b'%PDF-nova-chave' and conv.call_count == 2, conv.call_count)
            t('e substitui a guardada, sem duplicar no disco',
              disco.listdir('drive/previas/W1')[1] == ['aaa.enc'], disco.listdir('drive/previas/W1'))
            t('que a chave nova lê', vis.pdf_da_previa(dict(word)) == b'%PDF-nova-chave' and conv.call_count == 2)
        conv.return_value = b'%PDF-v2'
        t('versão nova do arquivo converte de novo',
          vis.pdf_da_previa({**word, 'md5Checksum': 'bbb'}) == b'%PDF-v2' and conv.call_count == 3, conv.call_count)
        t('e a prévia da versão velha sai',
          disco.listdir('drive/previas/W1')[1] == ['bbb.enc'], disco.listdir('drive/previas/W1'))
        vis.pdf_da_previa({'id': 'X1', 'name': 'a.xlsx', 'mimeType': XLSX, 'md5Checksum': 'c'})
        t('Excel vira Planilha do Google', conv.call_args.args[1] == GPLAN)
        vis.pdf_da_previa({'id': 'P1', 'name': 'a.pptx', 'mimeType': PPTX, 'md5Checksum': 'c'})
        t('PowerPoint vira Apresentação do Google', conv.call_args.args[1] == vis.GOOGLE_APRESENTACAO)
        doc = {'id': 'G1', 'name': 'Ata', 'mimeType': GDOC, 'version': '5', 'modifiedTime': '2026-09-10T10:00:00Z'}
        t('Doc do Google só exporta, sem cópia',
          vis.pdf_da_previa(doc) == b'%PDF-exportado' and exp.call_args.args == ('G1', vis.PDF) and conv.call_count == 5)
        try:
            vis.pdf_da_previa({'id': 'Z1', 'name': 'a.zip', 'mimeType': 'application/zip'})
            t('tipo sem prévia em PDF é recusado', False)
        except ValueError:
            t('tipo sem prévia em PDF é recusado', True)
        quebrado = mock.Mock()
        for metodo in ('exists', 'open', 'save', 'listdir', 'delete'):
            getattr(quebrado, metodo).side_effect = OSError('S3 fora do ar')
        with mock.patch.object(vis, 'storage_das_previas', return_value=quebrado):
            t('storage fora do ar não impede ver o arquivo',
              vis.pdf_da_previa({**word, 'md5Checksum': 'ddd'}) == b'%PDF-v2')
        conv.side_effect = gdrive.DriveError('Google Drive respondeu 403: exportSizeLimitExceeded')
        try:
            vis.pdf_da_previa({**word, 'md5Checksum': 'eee'})
            t('falha da conversão sobe como DriveError (a tela explica)', False)
        except gdrive.DriveError:
            t('falha da conversão sobe como DriveError (a tela explica)', True)
        t('e nada é guardado', not disco.exists('drive/previas/W1/eee.enc'))

    chefe = User.objects.create_user(
        username='zzvis.chefe', email='zzvis.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', is_superuser=True, is_staff=True, hierarchy='SUPERADMIN')
    leitor = User.objects.create_user(
        username='zzvis.leitor', email='zzvis.leitor@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Leitor')
    faxina = User.objects.create_user(
        username='zzvis.faxina', email='zzvis.faxina@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Faxina')
    gente = User.objects.create_user(
        username='zzvis.gente', email='zzvis.gente@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Gente')

    cfg = DriveConfig.get()
    cfg.modo = DriveConfig.Modo.OAUTH
    cfg.oauth_client_id = '123-zz.apps.googleusercontent.com'
    cfg.oauth_client_secret = 'GOCSPX-zz'
    cfg.oauth_refresh_token = 'rt-zz'
    cfg.oauth_email = 'dono@exemplo-teste.local'
    cfg.save()

    setor = Sector.objects.create(name='ZZ Setor Visualização')
    mapa = SectorDriveMapping.objects.create(sector=setor, folder_id='SETOR_RAIZ', folder_name='ZZ Raiz')
    DrivePermission.objects.create(mapping=mapa, alvo='USER', target_user=leitor, nivel='VIEW')
    DrivePermission.objects.create(mapping=mapa, alvo='USER', target_user=faxina, nivel='DELETE')

    c = Client()
    c.force_login(chefe)
    cl = Client()
    cl.force_login(leitor)
    cf = Client()
    cf.force_login(faxina)
    cg = Client()
    cg.force_login(gente)

    print('\n== A CONVERSÃO: A CÓPIA TEMPORÁRIA SEMPRE SAI ==')
    svc = mock.MagicMock()
    svc.files.return_value.copy.return_value.execute.return_value = {'id': 'COPIA1'}
    copiar = svc.files.return_value.copy
    with mock.patch.object(gdrive, 'service', return_value=svc), \
         mock.patch.object(gdrive, 'exportar', return_value=b'%PDF-x') as exportar, \
         mock.patch.object(gdrive, 'excluir_definitivo') as apagar, \
         mock.patch.object(gdrive, 'para_lixeira') as para_lixo:
        t('devolve o PDF', gdrive.converter_para_pdf('ORIG', GDOC, 'Contrato.docx') == b'%PDF-x')
        corpo = copiar.call_args.kwargs['body']
        t('copia o original como Doc do Google',
          copiar.call_args.kwargs['fileId'] == 'ORIG' and corpo['mimeType'] == GDOC, copiar.call_args)
        t('conta própria: a cópia vai para a raiz, longe da pasta da equipe', corpo.get('parents') == ['root'], corpo)
        t('com um nome que diz o que é',
          corpo['name'] == 'Visualização temporária do portal — Contrato.docx', corpo['name'])
        t('exporta a CÓPIA em PDF', exportar.call_args.args == ('COPIA1', 'application/pdf'), exportar.call_args)
        t('e apaga a cópia', apagar.call_args.args == ('COPIA1',) and para_lixo.call_count == 0)
        exportar.side_effect = gdrive.DriveError('export falhou')
        try:
            gdrive.converter_para_pdf('ORIG', GDOC, 'x.docx')
            t('falha na exportação sobe', False)
        except gdrive.DriveError:
            t('falha na exportação sobe', True)
        t('e a cópia sai mesmo assim', apagar.call_count == 2, apagar.call_count)
        exportar.side_effect = None
        apagar.side_effect = gdrive.DriveError('sem permissão para apagar')
        gdrive.converter_para_pdf('ORIG', GDOC, 'x.docx')
        t('não deu para apagar: vai para a lixeira', para_lixo.call_args.args == ('COPIA1',), para_lixo.call_args)
        apagar.side_effect = None
        DriveConfig.objects.filter(pk=cfg.pk).update(modo=DriveConfig.Modo.SA)
        gdrive.converter_para_pdf('ORIG', GDOC, 'x.docx')
        t('conta de serviço: a cópia herda a pasta do original (sem cota na raiz)',
          'parents' not in copiar.call_args.kwargs['body'], copiar.call_args.kwargs['body'])
        DriveConfig.objects.filter(pk=cfg.pk).update(modo=DriveConfig.Modo.OAUTH)

    falso = DriveFalso()
    falso.add('RAIZ_MEU', 'Meu Drive', FOLDER, None)
    falso.add('SETOR_RAIZ', 'ZZ Raiz', FOLDER, None)
    falso.add('SUB1', 'ZZ Contratos', FOLDER, 'SETOR_RAIZ')
    falso.add('SUB2', 'ZZ 2026', FOLDER, 'SUB1')
    falso.add('WORD1', 'Contrato.docx', DOCX, 'SUB2', b'PK-docx')
    falso.add('XLSX1', 'Planilha.xlsx', XLSX, 'SUB2', b'PK-xlsx')
    falso.add('PPTX1', 'Slides.pptx', PPTX, 'SUB2', b'PK-pptx')
    falso.add('GDOC1', 'Ata 10.09', GDOC, 'SUB2')
    falso.add('HTML1', 'pagina.html', 'text/html', 'SUB2', b'<script>alert(1)</script><p>oi</p>')
    falso.add('LAT1', 'nota.txt', 'text/plain', 'SUB2', 'Situação: aprovação'.encode('latin-1'))
    falso.add('SVG1', 'logo.svg', 'image/svg+xml', 'SUB2',
              b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    falso.add('PDF1', 'manual.pdf', 'application/pdf', 'SUB2', b'%PDF-1.4 manual')
    falso.add('ZIP1', 'pacote.zip', 'application/zip', 'SUB2', b'PK\x03\x04')
    VIDEO = bytes(range(256)) * 40
    falso.add('VID1', 'treino.mp4', 'video/mp4', 'SUB2', VIDEO)
    falso.add('AUD1', 'aviso.mp3', 'audio/mpeg', 'SUB2', b'ID3' * 100)
    falso.add('AVI1', 'antigo.avi', 'video/x-msvideo', 'SUB2', b'RIFF....')
    falso.add('FORA1', 'fora.pdf', 'application/pdf', 'RAIZ_MEU', b'%PDF fora')

    conversor = mock.Mock(return_value=b'%PDF-convertido')
    telas = FileSystemStorage(location=os.path.join(pasta_tmp, 'telas'))
    cache_teste = LocMemCache('zz-vis-pais', {})
    falsos = {n: getattr(falso, n) for n in ('obter', 'ancestrais', 'baixar', 'exportar', 'baixar_trecho',
                                            'listar_lixeira', 'para_lixeira')}
    with mock.patch.multiple(gdrive, converter_para_pdf=conversor, **falsos), \
         mock.patch.object(vis, 'storage_das_previas', return_value=telas), \
         mock.patch('django.core.cache.cache', cache_teste):

        print('\n== WORD, EXCEL E POWERPOINT ABREM NO PORTAL ==')
        url = '/drive/meu-drive/a/WORD1/conteudo/'
        r = c.get(url)
        t('Word abre: 200', r.status_code == 200, r.status_code)
        t('como PDF', r['Content-Type'] == 'application/pdf', r['Content-Type'])
        t('inline, com o nome em .pdf',
          r['Content-Disposition'] == "inline; filename*=UTF-8''Contrato.pdf", r['Content-Disposition'])
        t('é o PDF convertido pelo Google', r.content == b'%PDF-convertido' and conversor.call_count == 1)
        t('fica só no navegador de quem abriu (cache privado)', r['Cache-Control'] == 'private, max-age=300')
        t('nosniff', r['X-Content-Type-Options'] == 'nosniff')
        guardadas = telas.listdir('drive/previas/WORD1')[1]
        t('a prévia fica guardada no servidor', guardadas == ['md5-WORD1.enc'], guardadas)
        marca = r['ETag']
        r = c.get(url)
        t('2ª abertura não converte de novo',
          r.content == b'%PDF-convertido' and conversor.call_count == 1, conversor.call_count)
        with mock.patch.object(vis, 'pdf_da_previa', wraps=vis.pdf_da_previa) as espiao:
            r = c.get(url, HTTP_IF_NONE_MATCH=marca)
            t('navegador já tem esta versão: 304', r.status_code == 304, r.status_code)
            t('sem corpo e sem nem ler a prévia', r.content == b'' and espiao.call_count == 0, espiao.call_count)
            t('ETag enfraquecida por proxy (W/) também vale',
              c.get(url, HTTP_IF_NONE_MATCH=f'W/{marca}').status_code == 304)
            t('lista de ETags também', c.get(url, HTTP_IF_NONE_MATCH=f'"outra", {marca}').status_code == 304)
            t('ETag de outra versão não vale', c.get(url, HTTP_IF_NONE_MATCH='"velha"').status_code == 200)
        falso.itens['WORD1']['md5Checksum'] = 'md5-WORD1-v2'
        conversor.return_value = b'%PDF-v2'
        r = c.get(url, HTTP_IF_NONE_MATCH=marca)
        t('arquivo mudou no Drive: manda a versão nova', r.status_code == 200 and r.content == b'%PDF-v2')
        t('convertendo a versão nova', conversor.call_count == 2, conversor.call_count)
        t('e troca a prévia guardada', telas.listdir('drive/previas/WORD1')[1] == ['md5-WORD1-v2.enc'],
          telas.listdir('drive/previas/WORD1'))
        r = c.get('/drive/meu-drive/a/XLSX1/conteudo/')
        t('Excel abre como PDF',
          r['Content-Type'] == 'application/pdf' and conversor.call_args.args[:2] == ('XLSX1', GPLAN))
        r = c.get('/drive/meu-drive/a/PPTX1/conteudo/')
        t('PowerPoint abre como PDF',
          r['Content-Type'] == 'application/pdf' and conversor.call_args.args[:2] == ('PPTX1', vis.GOOGLE_APRESENTACAO))

        print('\n== BAIXAR NO FORMATO ESCOLHIDO ==')
        r = c.get(url + '?dl=1')
        t('Word: o original (.docx) por padrão',
          r.status_code == 200 and r.content == b'PK-docx'
          and r['Content-Disposition'] == "attachment; filename*=UTF-8''Contrato.docx", r.get('Content-Disposition'))
        t('"original" explícito', c.get(url + '?dl=1&formato=original').content == b'PK-docx')
        antes = conversor.call_count
        r = c.get(url + '?dl=1&formato=pdf')
        t('Word como PDF',
          r['Content-Type'] == 'application/pdf'
          and r['Content-Disposition'] == "attachment; filename*=UTF-8''Contrato.pdf", r.get('Content-Disposition'))
        t('reaproveita a prévia já convertida', conversor.call_count == antes, conversor.call_count - antes)
        t('formato que não existe para Word: 400', c.get(url + '?dl=1&formato=xlsx').status_code == 400)

        gurl = '/drive/meu-drive/a/GDOC1/conteudo/'
        antes = conversor.call_count
        r = c.get(gurl)
        t('Doc do Google abre como PDF',
          r['Content-Type'] == 'application/pdf' and r.content == b'EXPORTADO application/pdf', r.content[:40])
        t('sem cópia: o Google exporta direto', conversor.call_count == antes)
        t('o nome ganha .pdf sem cortar o "10.09"',
          r['Content-Disposition'] == "inline; filename*=UTF-8''Ata%2010.09.pdf", r['Content-Disposition'])
        r = c.get(gurl + '?dl=1')
        t('baixar Doc: Word por padrão',
          r['Content-Type'] == DOCX and r['Content-Disposition'].endswith('Ata%2010.09.docx'), r.get('Content-Type'))
        for codigo, mime_alvo in (('odt', 'application/vnd.oasis.opendocument.text'), ('rtf', 'application/rtf'),
                                  ('txt', 'text/plain'), ('pdf', 'application/pdf')):
            r = c.get(f'{gurl}?dl=1&formato={codigo}')
            t(f'Doc como .{codigo}',
              r.status_code == 200 and r['Content-Type'] == mime_alvo
              and r['Content-Disposition'].endswith(f'10.09.{codigo}'),
              (r.status_code, r.get('Content-Type'), r.get('Content-Disposition')))
        t('Doc como .xlsx: 400', c.get(gurl + '?dl=1&formato=xlsx').status_code == 400)
        t('formato inventado: 400', c.get(gurl + '?dl=1&formato=../../etc/passwd').status_code == 400)

        print('\n== TEXTO, IMAGEM, PDF E O QUE NÃO ABRE ==')
        r = c.get('/drive/meu-drive/a/HTML1/conteudo/')
        t('.html abre como texto puro', r['Content-Type'] == 'text/plain; charset=utf-8', r['Content-Type'])
        t('mostrando o código, sem executar', '<script>alert(1)</script>' in r.content.decode())
        r = c.get('/drive/meu-drive/a/LAT1/conteudo/')
        t('texto em latin-1 aparece com acento', 'Situação: aprovação' in r.content.decode('utf-8'))
        r = c.get('/drive/meu-drive/a/SVG1/conteudo/')
        t('SVG sai com sandbox: script dentro dele não roda',
          r['Content-Type'] == 'image/svg+xml' and r.get('Content-Security-Policy', '').startswith('sandbox'),
          r.get('Content-Security-Policy'))
        baixados = falso.baixados
        r = c.get('/drive/meu-drive/a/PDF1/conteudo/')
        t('PDF abre como é',
          r['Content-Type'] == 'application/pdf' and r.content == b'%PDF-1.4 manual'
          and not r.has_header('Content-Security-Policy'))
        r2 = c.get('/drive/meu-drive/a/PDF1/conteudo/', HTTP_IF_NONE_MATCH=r['ETag'])
        t('PDF já no navegador: 304 sem baixar do Google de novo',
          r2.status_code == 304 and falso.baixados == baixados + 1, falso.baixados - baixados)
        t('zip não abre no navegador: 415', c.get('/drive/meu-drive/a/ZIP1/conteudo/').status_code == 415)
        r = c.get('/drive/meu-drive/a/ZIP1/conteudo/?dl=1')
        t('mas baixa normalmente', r.status_code == 200 and r.content == b'PK\x03\x04')
        t('zip "como PDF": 400', c.get('/drive/meu-drive/a/ZIP1/conteudo/?dl=1&formato=pdf').status_code == 400)
        t('arquivo que não existe: 404', c.get('/drive/meu-drive/a/SUMIU/conteudo/').status_code == 404)

        print('\n== QUANDO A CONVERSÃO FALHA ==')
        conversor.side_effect = gdrive.DriveError('Google Drive respondeu 403: exportSizeLimitExceeded')
        falso.itens['XLSX1']['md5Checksum'] = 'md5-XLSX1-v2'
        r = c.get('/drive/meu-drive/a/XLSX1/conteudo/')
        html = r.content.decode()
        t('explica dentro do visualizador, em vez de erro 500',
          r.status_code == 200 and 'Não foi possível gerar a visualização' in html, r.status_code)
        t('e oferece baixar o original', '/drive/meu-drive/a/XLSX1/conteudo/?dl=1' in html)
        r = c.get('/drive/meu-drive/a/XLSX1/conteudo/?dl=1&formato=pdf')
        t('"baixar como PDF" que falha volta para o arquivo',
          r.status_code == 302 and r['Location'].endswith('/drive/meu-drive/a/XLSX1/'), r.get('Location'))
        r = c.get('/drive/meu-drive/a/XLSX1/conteudo/?dl=1&formato=pdf', follow=True)
        t('com o aviso', 'Não foi possível gerar o arquivo neste formato' in r.content.decode())
        conversor.side_effect = None

        print('\n== VÍDEO E ÁUDIO TOCAM EM TRECHOS ==')
        vurl = '/drive/meu-drive/a/VID1/conteudo/'
        vistos = DriveAuditLog.objects.filter(user=chefe, acao='VIEW', file_id='VID1').count()
        baixados = falso.baixados
        with mock.patch.object(dviews, 'TRECHO_MIDIA', 4096):
            r = c.get(vurl, HTTP_RANGE='bytes=0-1')
            t('Safari testa com 2 bytes: 206', r.status_code == 206, r.status_code)
            t('Content-Range certo', r.get('Content-Range') == 'bytes 0-1/10240', r.get('Content-Range'))
            t('só os 2 bytes', r.content == VIDEO[:2])
            t('avisa que aceita trechos', r.get('Accept-Ranges') == 'bytes')
            t('com o tipo do vídeo', r['Content-Type'] == 'video/mp4')
            etag_video = r['ETag']
            r = c.get(vurl, HTTP_RANGE='bytes=0-')
            t('pedido aberto vem num trecho limitado',
              r.status_code == 206 and r.get('Content-Range') == 'bytes 0-4095/10240' and r.content == VIDEO[:4096],
              r.get('Content-Range'))
            r = c.get(vurl, HTTP_RANGE='bytes=8000-')
            t('avançar o vídeo busca do meio',
              r.get('Content-Range') == 'bytes 8000-10239/10240' and r.content == VIDEO[8000:], r.get('Content-Range'))
            r = c.get(vurl, HTTP_RANGE='bytes=-100')
            t('os últimos 100 bytes',
              r.get('Content-Range') == 'bytes 10140-10239/10240' and r.content == VIDEO[-100:], r.get('Content-Range'))
            t('trecho não vira 304',
              c.get(vurl, HTTP_RANGE='bytes=0-1', HTTP_IF_NONE_MATCH=etag_video).status_code == 206)
            r = c.get(vurl, HTTP_RANGE='bytes=20000-')
            t('além do fim: 416', r.status_code == 416 and r.get('Content-Range') == 'bytes */10240', r.status_code)
            t('unidade estranha: 416', c.get(vurl, HTTP_RANGE='linhas=0-1').status_code == 416)
            t('número inválido: 416', c.get(vurl, HTTP_RANGE='bytes=a-b').status_code == 416)
            falso.trechos.clear()
            r = c.get(vurl)
            corpo = b''.join(r.streaming_content)
            t('sem Range: 200 em streaming com o vídeo inteiro',
              r.status_code == 200 and r.streaming and corpo == VIDEO, (r.status_code, len(corpo)))
            t('Content-Length do arquivo', r.get('Content-Length') == '10240')
            t('buscado do Google em trechos', falso.trechos == [(0, 4095), (4096, 8191), (8192, 10239)], falso.trechos)
            t('nunca baixado inteiro para a memória', falso.baixados == baixados)
            novos = DriveAuditLog.objects.filter(user=chefe, acao='VIEW', file_id='VID1').count() - vistos
            t('na auditoria entra cada abertura, não cada trecho', novos == 4, novos)
            with mock.patch.object(gdrive, 'baixar_trecho', side_effect=gdrive.DriveError('Google fora')):
                t('Google falhou no meio do vídeo: 502', c.get(vurl, HTTP_RANGE='bytes=0-1').status_code == 502)
        r = c.get('/drive/meu-drive/a/AUD1/conteudo/', HTTP_RANGE='bytes=0-2')
        t('áudio também em trechos',
          r.status_code == 206 and r.content == b'ID3' and r['Content-Type'] == 'audio/mpeg', r.status_code)
        r = c.get(vurl + '?dl=1')
        t('baixar o vídeo continua sendo o arquivo inteiro',
          r.status_code == 200 and r.content == VIDEO and 'attachment' in r['Content-Disposition'])

        print('\n== A PÁGINA DO ARQUIVO ==')
        html = c.get('/drive/meu-drive/a/WORD1/').content.decode()
        t('Word: visualizador no portal', '<iframe' in html and '/drive/meu-drive/a/WORD1/conteudo/' in html)
        t('avisa que a 1ª abertura converte', 'Na primeira abertura o arquivo é convertido' in html)
        t('"Baixar como PDF"',
          'Baixar como PDF' in html and '/drive/meu-drive/a/WORD1/conteudo/?dl=1&amp;formato=pdf' in html)
        t('"Baixar como Original (.docx)"', 'Baixar como Original (.docx)' in html)
        t('"Abrir em nova aba"', 'Abrir em nova aba' in html)
        t('diz que o arquivo baixado abre no programa do computador', 'abre no programa do seu computador' in html)
        html = c.get('/drive/meu-drive/a/GDOC1/').content.decode()
        for rotulo in ('PDF', 'Word (.docx)', 'OpenDocument (.odt)', 'RTF (.rtf)', 'Texto (.txt)'):
            t(f'Doc do Google: "Baixar como {rotulo}"', f'Baixar como {rotulo}' in html)
        t('Doc do Google não fala em conversão demorada', 'Na primeira abertura' not in html)
        html = c.get('/drive/meu-drive/a/VID1/').content.decode()
        t('vídeo: player que toca no iPhone (playsinline)',
          '<video src="/drive/meu-drive/a/VID1/conteudo/"' in html and 'playsinline' in html)
        html = c.get('/drive/meu-drive/a/AUD1/').content.decode()
        t('áudio: player', '<audio' in html)
        html = c.get('/drive/meu-drive/a/PDF1/').content.decode()
        t('PDF: um formato só, sem lista repetindo o "Baixar"', 'Baixar como' not in html and 'Abrir em nova aba' in html)
        t('se o navegador não conseguir mostrar, há para onde cair', 'data-falha hidden' in html and 'dvFalhou' in html)
        html = c.get('/drive/meu-drive/a/AVI1/').content.decode()
        # O base.html tem um <video em JS (anexos do chat); o que importa é o visualizador.
        t('AVI: não finge que toca',
          '<video src="/drive/meu-drive/a/AVI1/conteudo/"' not in html
          and 'Este tipo de arquivo não abre no navegador' in html)
        t('e oferece baixar', '/drive/meu-drive/a/AVI1/conteudo/?dl=1' in html)
        for u in ('/drive/meu-drive/a/WORD1/', '/drive/meu-drive/a/VID1/', '/drive/meu-drive/a/AVI1/'):
            corpo = c.get(u).content.decode()
            t(f'{u}: sem comentário de template vazando', '{#' not in corpo and '{% comment' not in corpo)

        print('\n== PELO SETOR: A PERMISSÃO VALE PARA ABRIR E PARA BAIXAR ==')
        curl = reverse('drive:file_content', args=['WORD1'])
        r = cl.get(curl)
        t('quem só visualiza abre o Word no portal',
          r.status_code == 200 and r['Content-Type'] == 'application/pdf', r.status_code)
        t('mas não baixa o original: 403', cl.get(curl + '?dl=1').status_code == 403)
        t('nem como PDF: 403', cl.get(curl + '?dl=1&formato=pdf').status_code == 403)
        html = cl.get(reverse('drive:file_preview', args=['WORD1'])).content.decode()
        t('a página não oferece "Baixar como" a quem não pode baixar', 'Baixar como' not in html)
        t('mas deixa abrir em nova aba', 'Abrir em nova aba' in html)
        html = c.get(reverse('drive:file_preview', args=['WORD1'])).content.decode()
        t('pelo setor, quem pode baixar vê os formatos', 'Baixar como PDF' in html and '<iframe' in html)
        conversor.side_effect = gdrive.DriveError('falhou')
        falso.itens['PPTX1']['md5Checksum'] = 'md5-PPTX1-v2'
        html = cl.get(reverse('drive:file_content', args=['PPTX1'])).content.decode()
        t('conversão falhou para quem não baixa: explica sem link de download',
          'Não foi possível gerar a visualização' in html and 'Baixar arquivo' not in html)
        conversor.side_effect = None
        t('arquivo fora do setor: 403', cl.get(reverse('drive:file_content', args=['FORA1'])).status_code == 403)
        t('sem login: vai para o login', Client().get(curl).status_code == 302)
        r = cf.get(curl + '?dl=1&formato=pdf')
        t('quem tem download no setor baixa como PDF',
          r.status_code == 200 and r['Content-Type'] == 'application/pdf', r.status_code)

        print('\n== LIXEIRA: ABRE NA HORA E TRAZ OS ITENS EM PARTES ==')
        for n in range(10):
            falso.add(f'LIXM{n}', f'ZZ lixo meu {n}', 'application/pdf', 'RAIZ_MEU', b'x', trashed=True)
        for n in range(60):
            falso.add(f'LIXS{n:02d}', f'ZZ lixo setor {n:02d}', 'application/pdf', 'SUB2', b'x', trashed=True)

        falso.lixeira.clear()
        falso.pais = 0
        r = c.get('/drive/lixeira/')
        html = r.content.decode()
        t('a página abre sem esperar o Google',
          r.status_code == 200 and falso.lixeira == [] and falso.pais == 0, (r.status_code, falso.lixeira, falso.pais))
        t('e busca os itens em partes', '?parte=1' in html and 'id="dv-lixeira"' in html and 'Mostrar mais' in html)
        t('nenhum item vem junto com a página', 'ZZ lixo' not in html)

        r = c.get('/drive/lixeira/?parte=1')
        html = r.content.decode()
        t('1ª parte: 200', r.status_code == 200, r.status_code)
        t('com uma porção dos itens (50 de 70)', html.count('dv-lixeira-item') == 50, html.count('dv-lixeira-item'))
        t('pedindo UMA página ao Google', falso.lixeira == [(None, 50)], falso.lixeira)
        t('e dizendo de onde continuar', 'data-prox="50"' in html)
        t('só o pedaço, sem a página em volta', '<html' not in html.lower() and 'id="dv-lixeira"' not in html)
        t('itens do Meu Drive marcados', 'Meu Drive' in html)
        t('itens do setor com o nome do setor', 'ZZ Setor Visualização' in html)
        t('restaurar e excluir definitivo (superadmin)',
          'Restaurar' in html and 'Excluir DEFINITIVAMENTE' in html and 'csrfmiddlewaretoken' in html)
        t('sobe a árvore uma vez por pasta, não por item (4 consultas para 50 itens)', falso.pais == 4, falso.pais)

        falso.pais = 0
        r = c.get('/drive/lixeira/?parte=1&t=50')
        html = r.content.decode()
        t('"Mostrar mais" traz o resto (20)', html.count('dv-lixeira-item') == 20, html.count('dv-lixeira-item'))
        t('e avisa que acabou', 'data-prox=""' in html)
        t('pastas já vistas vêm do cache: nenhuma consulta nova', falso.pais == 0, falso.pais)

        partes = cf.get('/drive/lixeira/?parte=1').content.decode()
        t('quem exclui no setor: 1ª parte com os do setor',
          partes.count('dv-lixeira-item') == 40 and 'data-prox="50"' in partes, partes.count('dv-lixeira-item'))
        partes += cf.get('/drive/lixeira/?parte=1&t=50').content.decode()
        t('somando as partes, os 60 do setor', partes.count('dv-lixeira-item') == 60, partes.count('dv-lixeira-item'))
        t('nenhum do Meu Drive', 'ZZ lixo meu' not in partes)
        t('e sem excluir definitivo', 'Excluir DEFINITIVAMENTE' not in partes)

        with mock.patch.object(dviews, 'LIXEIRA_TAMANHO_PAGINA_DRIVE', 10):
            falso.lixeira.clear()
            html = cl.get('/drive/lixeira/?parte=1').content.decode()
            t('quem só visualiza não vê itens da lixeira', 'dv-lixeira-item' not in html)
            t('e a busca para em 4 páginas do Google por vez', len(falso.lixeira) == 4, falso.lixeira)
            t('deixando de onde continuar', 'data-prox="40"' in html)

        falso.lixeira.clear()
        html = cg.get('/drive/lixeira/').content.decode()
        t('sem setor nenhum: "lixeira vazia" direto', 'A lixeira está vazia' in html and '?parte=1' not in html)
        html = cg.get('/drive/lixeira/?parte=1').content.decode()
        t('e a parte nem consulta o Google', falso.lixeira == [] and 'dv-lixeira-item' not in html, falso.lixeira)

        cache_teste.clear()
        sub1 = falso.itens.pop('SUB1')
        html = cf.get('/drive/lixeira/?parte=1').content.decode()
        t('Google não devolveu uma pasta: os itens dela ficam de fora', 'dv-lixeira-item' not in html)
        t('a falha não vai para o cache',
          cache_teste.get('drive:pai:SUB1') is None and cache_teste.get('drive:pai:SUB2') == 'SUB1')
        falso.itens['SUB1'] = sub1
        html = cf.get('/drive/lixeira/?parte=1').content.decode()
        t('com o Google de volta, aparecem na hora', html.count('dv-lixeira-item') == 40, html.count('dv-lixeira-item'))

        with mock.patch.object(gdrive, 'listar_lixeira', side_effect=gdrive.DriveError('Google Drive respondeu 500')):
            r = c.get('/drive/lixeira/?parte=1')
            html = r.content.decode()
            t('Google fora do ar: 502 com aviso',
              r.status_code == 502 and 'Não foi possível carregar a lixeira agora' in html, r.status_code)
            t('sem continuação', 'data-prox=""' in html)
        with mock.patch.object(gdrive, 'listar_lixeira', side_effect=gdrive.DriveNaoConfigurado('sem credencial')):
            r = c.get('/drive/lixeira/?parte=1')
            t('Drive desconectado: 503 com aviso',
              r.status_code == 503 and 'não está conectado' in r.content.decode(), r.status_code)

        print('\n== RESTAURAR CONFERE A PERMISSÃO SEM CACHE ==')
        falso.itens['LIXS00']['parents'] = ['RAIZ_MEU']
        cache_teste.set('drive:pai:LIXS00', 'SUB2')
        r = cf.post('/drive/lixeira/LIXS00/restaurar/')
        t('item que saiu do setor não é restaurado por quem é do setor (403)',
          r.status_code == 403 and falso.itens['LIXS00']['trashed'] is True, r.status_code)
        r = cf.post('/drive/lixeira/LIXS01/restaurar/')
        t('item do setor é restaurado', r.status_code == 302 and falso.itens['LIXS01']['trashed'] is False, r.status_code)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    shutil.rmtree(pasta_tmp, ignore_errors=True)
    gdrive.resetar()
    print('\nrollback: nada deste teste foi gravado no banco; prévias temporárias apagadas.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
