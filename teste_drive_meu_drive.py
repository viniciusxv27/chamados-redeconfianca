"""Drive → Meu Drive: abrir pastas dava 500; todas as funções de pastas e arquivos.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
Não fala com o Google: a primeira parte usa o cliente REAL montando as
requisições contra a especificação da API (sem rede); o resto usa um Drive
falso em memória para exercitar cada tela.
"""
import io
import json
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import httplib2
from django.contrib.auth import get_user_model
from django.core.cache.backends.locmem import LocMemCache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client

from drive import gdrive
from drive.models import DriveAuditLog, DriveConfig, DriveFavorite

User = get_user_model()
FOLDER = gdrive.FOLDER_MIME
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


class FakeHttp:
    """Transporte falso: a montagem das requisições é a real, só não sai rede."""

    def request(self, uri, method='GET', body=None, headers=None, redirections=5, connection_type=None):
        if 'uploadType=resumable' in uri and 'upload_id' not in uri:
            return httplib2.Response({'status': '200', 'location': uri + '&upload_id=x'}), b''
        if 'alt=media' in uri or '/export' in uri:
            return httplib2.Response({'status': '200', 'content-length': '3',
                                      'content-range': 'bytes 0-2/3'}), b'abc'
        corpo = {'id': 'PASTAX', 'name': 'ZZ Pasta real', 'mimeType': FOLDER,
                 'files': [{'id': 'F1', 'name': 'ZZ filho.pdf', 'mimeType': 'application/pdf'}],
                 'revisions': []}
        return httplib2.Response({'status': '200'}), json.dumps(corpo).encode()


ROOT = 'RAIZ0000'


class DriveFalso:
    """Um Meu Drive em memória, com a mesma forma de resposta do Google."""

    def __init__(self):
        self.itens, self.seq, self.versoes = {}, 0, []
        self._add(ROOT, 'Meu Drive', FOLDER, None)

    def _add(self, id_, nome, mime, pai, **extra):
        self.itens[id_] = dict(
            id=id_, name=nome, mimeType=mime, parents=[pai] if pai else [], trashed=False,
            size='2048', modifiedTime='2026-09-10T10:00:00Z', createdTime='2026-09-01T10:00:00Z',
            webViewLink=f'https://drive.google.com/file/d/{id_}/view', **extra)
        return self.itens[id_]

    def _r(self, id_):
        return ROOT if id_ == 'root' else id_

    def listar(self, folder_id, page_token=None, page_size=100, trashed=False, apenas_pastas=False, order=''):
        fid = self._r(folder_id)
        filhos = [dict(i) for i in self.itens.values() if fid in i['parents'] and i['trashed'] == trashed]
        if apenas_pastas:
            filhos = [i for i in filhos if i['mimeType'] == FOLDER]
        filhos.sort(key=lambda i: (i['mimeType'] != FOLDER, i['name']))
        ini = int(page_token or 0)
        prox = str(ini + page_size) if ini + page_size < len(filhos) else None
        return filhos[ini:ini + page_size], prox

    def obter(self, file_id, fields=None):
        fid = self._r(file_id)
        if fid not in self.itens:
            raise gdrive.DriveError('Google Drive respondeu 404: File not found')
        return dict(self.itens[fid])

    def ancestrais(self, file_id, limite=30):
        ids, atual = [], self._r(file_id)
        while atual and atual not in ids and len(ids) < limite:
            ids.append(atual)
            pais = self.itens.get(atual, {}).get('parents') or []
            atual = pais[0] if pais else None
        return ids

    def dentro_de(self, file_id, root_id):
        return bool(file_id and root_id) and self._r(root_id) in self.ancestrais(file_id)

    def caminho(self, file_id, ate_root=None, limite=30):
        return [(i, self.itens[i]['name']) for i in reversed(self.ancestrais(file_id)) if i in self.itens]

    def id_da_raiz(self):
        return ROOT

    def criar_pasta(self, nome, parent_id):
        self.seq += 1
        return dict(self._add(f'NOVAPASTA{self.seq}', nome, FOLDER, self._r(parent_id)))

    def enviar(self, nome, mimetype, stream, parent_id):
        self.seq += 1
        return dict(self._add(f'NOVOARQ{self.seq}', nome, mimetype or 'application/octet-stream', self._r(parent_id)))

    def nova_versao(self, file_id, stream, mimetype=None):
        self.versoes.append(file_id)
        return self.obter(file_id)

    def renomear(self, file_id, novo_nome):
        self.itens[file_id]['name'] = novo_nome
        return self.obter(file_id)

    def mover(self, file_id, novo_parent, parent_atual=None):
        self.itens[file_id]['parents'] = [self._r(novo_parent)]
        return self.obter(file_id)

    def para_lixeira(self, file_id, trashed=True):
        self.itens[file_id]['trashed'] = trashed
        return self.obter(file_id)

    def listar_lixeira(self, page_token=None, page_size=100):
        return [dict(i) for i in self.itens.values() if i['trashed']], None

    def revisoes(self, file_id):
        return [{'id': 'REV1', 'modifiedTime': '2026-09-01T10:00:00Z', 'size': '100'},
                {'id': 'REV2', 'modifiedTime': '2026-09-10T10:00:00Z', 'size': '120'}]

    def baixar(self, file_id, preview=False):
        meta = self.obter(file_id)
        return io.BytesIO(b'%PDF-zz'), meta['name'], meta['mimeType']


marcador = transaction.atomic()
marcador.__enter__()
try:
    chefe = User.objects.create_user(
        username='zzmd.chefe', email='zzmd.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', is_superuser=True, is_staff=True, hierarchy='SUPERADMIN')
    gente = User.objects.create_user(
        username='zzmd.gente', email='zzmd.gente@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Gente')

    cfg = DriveConfig.get()
    cfg.modo = DriveConfig.Modo.OAUTH
    cfg.oauth_client_id = '123-zz.apps.googleusercontent.com'
    cfg.oauth_client_secret = 'GOCSPX-zz'
    cfg.oauth_refresh_token = 'rt-zz'
    cfg.oauth_email = 'dono@exemplo-teste.local'
    cfg.allowed_extensions = 'pdf,txt,png,xlsx'
    cfg.max_file_mb = 5
    cfg.save()

    c = Client(); c.force_login(chefe)
    cg = Client(); cg.force_login(gente)

    print('== A CAUSA DO 500: PARÂMETRO INVÁLIDO NA API ==')
    from googleapiclient.discovery import build
    svc = build('drive', 'v3', http=FakeHttp(), cache_discovery=False, static_discovery=True)
    P_LISTA = gdrive._params_lista()
    t('só files.list recebe includeItemsFromAllDrives', 'includeItemsFromAllDrives' in P_LISTA)
    t('as outras chamadas NÃO recebem', 'includeItemsFromAllDrives' not in gdrive._params())

    casos = {
        'listar': lambda: gdrive.listar('root'),
        'obter': lambda: gdrive.obter('x'),
        'caminho': lambda: gdrive.caminho('x'),
        'ancestrais': lambda: gdrive.ancestrais('x'),
        'baixar': lambda: gdrive.baixar('x'),
        'criar_pasta': lambda: gdrive.criar_pasta('N', 'root'),
        'enviar': lambda: gdrive.enviar('a.pdf', 'application/pdf', io.BytesIO(b'%PDF'), 'root'),
        'nova_versao': lambda: gdrive.nova_versao('x', io.BytesIO(b'%PDF'), 'application/pdf'),
        'renomear': lambda: gdrive.renomear('x', 'n'),
        'mover': lambda: gdrive.mover('x', 'y'),
        'para_lixeira': lambda: gdrive.para_lixeira('x'),
        'excluir_definitivo': lambda: gdrive.excluir_definitivo('x'),
        'revisoes': lambda: gdrive.revisoes('x'),
        'buscar': lambda: gdrive.buscar('zz'),
        'listar_lixeira': lambda: gdrive.listar_lixeira(),
        # Abrir no portal: export_media NÃO aceita supportsAllDrives.
        'exportar': lambda: gdrive.exportar('x', 'application/pdf'),
        'converter_para_pdf': lambda: gdrive.converter_para_pdf(
            'x', 'application/vnd.google-apps.document', 'a.docx'),
        'pai_de': lambda: gdrive.pai_de('x'),
        'baixar_trecho': lambda: gdrive.baixar_trecho('x', 0, 1),
    }
    gdrive._raizes.clear()
    with mock.patch.object(gdrive, 'service', return_value=svc):
        for nome, fn in casos.items():
            try:
                fn()
                t(f'{nome}: monta e executa contra a especificação real', True)
            except Exception as e:  # noqa: BLE001
                t(f'{nome}: monta e executa contra a especificação real', False, f'{type(e).__name__}: {e}')

        # O cenário exato do chamado: abrir uma pasta, com o cliente real.
        r = c.get('/drive/meu-drive/f/PASTAX/')
        t('ABRIR PASTA não dá mais 500', r.status_code == 200, r.status_code)
        t('e lista o que tem dentro', 'ZZ filho.pdf' in r.content.decode())
        r = c.get('/drive/meu-drive/a/F1/')
        t('abrir arquivo com o cliente real também responde', r.status_code in (200, 302), r.status_code)
    gdrive._raizes.clear()

    falso = DriveFalso()
    falso._add('PASTA_A', 'ZZ Contratos', FOLDER, ROOT)
    falso._add('PASTA_B', 'ZZ 2026', FOLDER, 'PASTA_A')
    falso._add('ARQ_1', 'ZZ contrato.pdf', 'application/pdf', 'PASTA_B')
    falso._add('ARQ_2', 'ZZ planilha.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'PASTA_A')
    falso._add('ARQ_3', 'ZZ foto.png', 'image/png', ROOT)
    falso._add('DOC_G', 'ZZ Doc Google', 'application/vnd.google-apps.document', ROOT)
    falso._add('ATALHO_P', 'ZZ Atalho pasta', gdrive.SHORTCUT_MIME, ROOT,
               shortcutDetails={'targetId': 'PASTA_B', 'targetMimeType': FOLDER})
    falso._add('ATALHO_A', 'ZZ Atalho arquivo', gdrive.SHORTCUT_MIME, ROOT,
               shortcutDetails={'targetId': 'ARQ_1', 'targetMimeType': 'application/pdf'})

    nomes = ('listar', 'obter', 'ancestrais', 'dentro_de', 'caminho', 'id_da_raiz', 'criar_pasta',
             'enviar', 'nova_versao', 'renomear', 'mover', 'para_lixeira', 'listar_lixeira',
             'revisoes', 'baixar')
    with mock.patch.multiple(gdrive, **{n: getattr(falso, n) for n in nomes}):

        print('\n== NAVEGAR PELAS PASTAS ==')
        html = c.get('/drive/meu-drive/').content.decode()
        t('a raiz lista as pastas', 'ZZ Contratos' in html)
        t('e os arquivos', 'ZZ foto.png' in html)
        r = c.get('/drive/meu-drive/f/PASTA_A/')
        html = r.content.decode()
        t('abrir pasta: 200', r.status_code == 200, r.status_code)
        t('mostra o que tem dentro', 'ZZ 2026' in html and 'ZZ planilha.xlsx' in html)
        t('não mostra o que é de outra pasta', 'ZZ foto.png' not in html)
        r = c.get('/drive/meu-drive/f/PASTA_B/')
        html = r.content.decode()
        t('abrir subpasta: 200', r.status_code == 200, r.status_code)
        t('a trilha mostra o caminho', 'ZZ Contratos' in html and 'ZZ 2026' in html)
        # "Meu Drive" aparece em vários lugares da página (aba, subtítulo, trilha);
        # o que não pode é a trilha trazer a própria raiz como uma pasta a mais.
        t('a trilha não repete a raiz como pasta',
          f'/drive/meu-drive/f/{ROOT}/' not in html)
        t('tem o botão de subir uma pasta', 'Pasta de cima' in html)
        t('pasta inexistente não vira 500',
          c.get('/drive/meu-drive/f/NAOEXISTE/').status_code in (200, 302))
        t('id com caractere inválido é recusado (404)',
          c.get('/drive/meu-drive/f/a%24b/').status_code == 404)

        print('\n== ATALHOS ==')
        html = c.get('/drive/meu-drive/').content.decode()
        t('atalho de pasta abre a pasta de destino', '/drive/meu-drive/f/PASTA_B/' in html)
        t('atalho de arquivo abre o arquivo de destino', '/drive/meu-drive/a/ARQ_1/' in html)
        r = c.get('/drive/meu-drive/a/ATALHO_P/')
        t('abrir o atalho direto leva à pasta', r.status_code == 302 and 'PASTA_B' in r['Location'])

        print('\n== CARREGAR MAIS ==')
        for n in range(65):
            falso._add(f'MUITO{n:02d}', f'ZZ muitos {n:02d}.txt', 'text/plain', 'PASTA_A')
        html = c.get('/drive/meu-drive/f/PASTA_A/').content.decode()
        t('pasta grande oferece "Carregar mais"', 'Carregar mais' in html)
        r = c.get('/drive/meu-drive/f/PASTA_A/?t=60', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        corpo = r.content.decode()
        t('o AJAX devolve só os itens (sem a página inteira)', r.status_code == 200 and '<html' not in corpo.lower())
        t('com os que faltavam', 'ZZ muitos' in corpo)
        for n in range(65):
            falso.itens.pop(f'MUITO{n:02d}')

        print('\n== ARQUIVO: ABRIR, VER, BAIXAR ==')
        r = c.get('/drive/meu-drive/a/ARQ_1/')
        html = r.content.decode()
        t('abrir arquivo: 200', r.status_code == 200, r.status_code)
        t('PDF abre no preview', '<iframe' in html)
        t('volta para a pasta do arquivo', '/drive/meu-drive/f/PASTA_B/' in html)
        for acao in ('Baixar', 'Renomear', 'Mover', 'Nova versão', 'Versões', 'Excluir', 'Abrir no Drive'):
            t(f'a página do arquivo tem "{acao}"', acao in html)
        html = c.get('/drive/meu-drive/a/ARQ_2/').content.decode()
        # Desde que Word/Excel/PowerPoint abrem no portal (convertidos em PDF
        # pelo Google), a planilha também vai para o visualizador.
        t('planilha agora abre no portal (convertida em PDF)',
          '<iframe' in html and 'Preparando a visualização' in html)
        html = c.get('/drive/meu-drive/a/DOC_G/').content.decode()
        t('Doc do Google não oferece "Nova versão" de arquivo', 'Nova versão' not in html)
        r = c.get('/drive/meu-drive/a/PASTA_A/')
        t('abrir uma pasta pela rota de arquivo redireciona para a pasta',
          r.status_code == 302 and '/f/PASTA_A/' in r['Location'])
        r = c.get('/drive/meu-drive/a/ARQ_1/conteudo/?dl=1')
        t('baixar: 200 como anexo', r.status_code == 200 and 'attachment' in r['Content-Disposition'])
        r = c.get('/drive/meu-drive/a/ARQ_1/conteudo/')
        t('preview: inline', 'inline' in r['Content-Disposition'])
        t('arquivo inexistente: 404', c.get('/drive/meu-drive/a/SUMIU/').status_code == 404)

        print('\n== NOVA PASTA E ENVIO ==')
        r = c.post('/drive/meu-drive/nova-pasta/', {'folder_id': 'PASTA_A', 'nome': 'ZZ Nova'})
        t('cria pasta e volta para a pasta', r.status_code == 302 and '/f/PASTA_A/' in r['Location'])
        t('a pasta existe dentro da certa',
          any(i['name'] == 'ZZ Nova' and 'PASTA_A' in i['parents'] for i in falso.itens.values()))
        r = c.post('/drive/meu-drive/nova-pasta/', {'folder_id': 'PASTA_A', 'nome': '   '}, follow=True)
        t('nome vazio é recusado', 'Informe o nome da pasta' in r.content.decode())

        r = c.post('/drive/meu-drive/enviar/', {
            'folder_id': 'PASTA_A',
            'arquivos': [SimpleUploadedFile('zz_um.pdf', b'%PDF', content_type='application/pdf'),
                         SimpleUploadedFile('zz_dois.txt', b'oi', content_type='text/plain'),
                         SimpleUploadedFile('zz_virus.exe', b'MZ', content_type='application/octet-stream')],
        }, follow=True)
        html = r.content.decode()
        t('envia vários de uma vez', 'zz_um.pdf' in html and 'zz_dois.txt' in html)
        t('recusa a extensão não permitida e diz por quê', '.exe não permitida' in html)
        t('conta os enviados', '2 arquivo(s) enviado(s)' in html)

        print('\n== RENOMEAR, MOVER, NOVA VERSÃO ==')
        r = c.post('/drive/meu-drive/a/ARQ_2/renomear/', {'nome': 'ZZ planilha final.xlsx', 'folder_id': 'PASTA_A'})
        t('renomeia arquivo', falso.itens['ARQ_2']['name'] == 'ZZ planilha final.xlsx')
        c.post('/drive/meu-drive/a/PASTA_B/renomear/', {'nome': 'ZZ Ano 2026', 'folder_id': 'PASTA_A'})
        t('renomeia pasta', falso.itens['PASTA_B']['name'] == 'ZZ Ano 2026')

        r = c.post('/drive/meu-drive/a/ARQ_3/mover/', {'destino': 'PASTA_B', 'folder_id': 'root'})
        t('move arquivo', falso.itens['ARQ_3']['parents'] == ['PASTA_B'])
        t('e vai para a pasta de destino', '/f/PASTA_B/' in r['Location'])
        c.post('/drive/meu-drive/a/ARQ_3/mover/', {'destino': 'root', 'folder_id': 'PASTA_B'})
        t('move de volta para a raiz', falso.itens['ARQ_3']['parents'] == [ROOT])

        r = c.post('/drive/meu-drive/a/PASTA_A/mover/', {'destino': 'PASTA_B', 'folder_id': 'root'}, follow=True)
        t('recusa pasta para dentro da própria subpasta', falso.itens['PASTA_A']['parents'] == [ROOT])
        t('e explica', 'não pode ir para dentro dela mesma' in r.content.decode())
        r = c.post('/drive/meu-drive/a/PASTA_A/mover/', {'destino': 'PASTA_A', 'folder_id': 'root'}, follow=True)
        t('recusa pasta para dentro de si mesma', 'não pode ir para dentro dela mesma' in r.content.decode())

        dados = json.loads(c.get('/drive/meu-drive/pastas/?pai=PASTA_A').content)
        t('o seletor de destino lista as subpastas', any(p['nome'] == 'ZZ Ano 2026' for p in dados['pastas']))
        t('e só pastas', all(p['id'] not in ('ARQ_2',) for p in dados['pastas']))
        t('sabe voltar para a raiz', dados['acima'] == 'root')
        t('o seletor recusa id inválido', c.get('/drive/meu-drive/pastas/?pai=a$b').status_code == 400)

        r = c.post('/drive/meu-drive/a/ARQ_1/nova-versao/', {
            'arquivo': SimpleUploadedFile('zz_v2.pdf', b'%PDF v2', content_type='application/pdf')})
        t('envia nova versão', falso.versoes == ['ARQ_1'])
        t('e volta para o arquivo', '/a/ARQ_1/' in r['Location'])

        print('\n== VERSÕES ==')
        html = c.get('/drive/meu-drive/a/ARQ_1/versoes/').content.decode()
        t('lista o histórico', 'Versão 1' in html and 'Versão 2' in html)
        t('marca a atual', 'atual' in html)
        baixador = mock.MagicMock()
        baixador.return_value.next_chunk.return_value = (None, True)
        falso_svc = mock.MagicMock()
        with mock.patch('googleapiclient.http.MediaIoBaseDownload', baixador), \
             mock.patch.object(gdrive, 'service', return_value=falso_svc):
            r = c.post('/drive/meu-drive/a/ARQ_1/versoes/restaurar/', {'rev': 'REV1'}, follow=True)
        t('restaura uma versão antiga', 'Versão restaurada' in r.content.decode())
        r = c.post('/drive/meu-drive/a/ARQ_1/versoes/restaurar/', {'rev': '../x'}, follow=True)
        t('recusa id de versão inválido', 'Versão inválida' in r.content.decode())

        print('\n== FAVORITOS ==')
        c.post('/drive/meu-drive/a/ARQ_1/favoritar/', {'voltar': 'arquivo'})
        fav = DriveFavorite.objects.filter(user=chefe, file_id='ARQ_1').first()
        t('favorita', fav is not None)
        t('sem setor (é do Meu Drive)', fav and fav.sector_id is None)
        html = c.get('/drive/favoritos/').content.decode()
        t('a tela de favoritos abre pelo Meu Drive', '/drive/meu-drive/a/ARQ_1/' in html)
        t('e marca de onde é', 'Meu Drive' in html)
        c.post('/drive/meu-drive/a/ARQ_1/favoritar/', {'voltar': 'favoritos'})
        t('desfavorita', not DriveFavorite.objects.filter(user=chefe, file_id='ARQ_1').exists())

        print('\n== EXCLUIR E RESTAURAR ==')
        r = c.post('/drive/meu-drive/a/ARQ_2/excluir/', {'folder_id': 'PASTA_A'})
        t('manda para a lixeira (não apaga de vez)', falso.itens['ARQ_2']['trashed'] is True)
        t('volta para a pasta', '/f/PASTA_A/' in r['Location'])
        r = c.post('/drive/meu-drive/a/PASTA_B/excluir/', {'folder_id': 'PASTA_B'})
        t('excluir a pasta que se está vendo volta para a de cima', '/f/PASTA_A/' in r['Location'])
        # A lixeira carrega em partes: os itens vêm de ?parte=1, não da página.
        # Cache em memória: os pais das pastas falsas não vão para o Redis.
        with mock.patch('django.core.cache.cache', LocMemCache('zz-drive-pais', {})):
            html = c.get('/drive/lixeira/?parte=1').content.decode()
        t('o que foi excluído aparece na lixeira do portal', 'ZZ planilha final.xlsx' in html)
        t('marcado como Meu Drive', 'Meu Drive' in html)
        c.post('/drive/lixeira/ARQ_2/restaurar/')
        t('e dá para restaurar por lá', falso.itens['ARQ_2']['trashed'] is False)
        falso.itens['PASTA_B']['trashed'] = False

        print('\n== AUDITORIA ==')
        for acao in ('UPLOAD', 'MKDIR', 'RENAME', 'MOVE', 'VERSION', 'DELETE', 'RESTORE', 'DOWNLOAD'):
            t(f'{acao} fica registrado',
              DriveAuditLog.objects.filter(user=chefe, acao=acao).exists())

        print('\n== QUEM NÃO É SUPERADMIN NÃO ENTRA EM NADA ==')
        r = cg.get('/drive/meu-drive/', follow=True)
        t('lista: barrado', 'ZZ Contratos' not in r.content.decode())
        r = cg.get('/drive/meu-drive/f/PASTA_A/', follow=True)
        t('pasta: barrado', 'ZZ planilha' not in r.content.decode())
        for url in ('/drive/meu-drive/a/ARQ_1/', '/drive/meu-drive/a/ARQ_1/conteudo/',
                    '/drive/meu-drive/a/ARQ_1/versoes/', '/drive/meu-drive/pastas/'):
            t(f'GET {url}: 403', cg.get(url).status_code == 403, cg.get(url).status_code)
        posts = {
            '/drive/meu-drive/enviar/': {'folder_id': 'root'},
            '/drive/meu-drive/nova-pasta/': {'nome': 'x'},
            '/drive/meu-drive/a/ARQ_1/renomear/': {'nome': 'x'},
            '/drive/meu-drive/a/ARQ_1/mover/': {'destino': 'root'},
            '/drive/meu-drive/a/ARQ_1/nova-versao/': {},
            '/drive/meu-drive/a/ARQ_1/excluir/': {},
            '/drive/meu-drive/a/ARQ_1/favoritar/': {},
            '/drive/meu-drive/a/ARQ_1/versoes/restaurar/': {'rev': 'REV1'},
        }
        antes = dict(falso.itens['ARQ_1'])
        for url, dados in posts.items():
            t(f'POST {url}: 403', cg.post(url, dados).status_code == 403)
        t('e nada mudou no Drive', falso.itens['ARQ_1'] == antes)
        t('as recusas vão para a auditoria', DriveAuditLog.objects.filter(user=gente, acao='DENY').exists())

        print('\n== SÓ POST ONDE ALTERA ==')
        for url in posts:
            t(f'GET {url}: 405', c.get(url).status_code == 405, c.get(url).status_code)

        print('\n== SEM CONTA CONECTADA, FECHA ==')
        cfg.oauth_refresh_token = ''
        cfg.save()
        t('arquivo: 403', c.get('/drive/meu-drive/a/ARQ_1/').status_code == 403)
        r = c.get('/drive/meu-drive/', follow=True)
        t('lista manda conectar a conta', 'Conecte a sua conta Google' in r.content.decode())
        html = c.get('/drive/').content.decode()
        t('a aba "Meu Drive" some', '/drive/meu-drive/' not in html)
        cfg.oauth_refresh_token = 'rt-zz'
        cfg.save()
        html = c.get('/drive/').content.decode()
        t('com a conta conectada a aba aparece', '/drive/meu-drive/' in html)

        print('\n== NADA DE COMENTÁRIO VAZANDO ==')
        for url in ('/drive/meu-drive/', '/drive/meu-drive/a/ARQ_1/', '/drive/meu-drive/a/ARQ_1/versoes/'):
            corpo = c.get(url).content.decode()
            t(f'{url}: sem {{# #}} nem comment cru', '{#' not in corpo and '{% comment' not in corpo)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    gdrive.resetar()
    gdrive._raizes.clear()
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
