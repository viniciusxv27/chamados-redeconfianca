"""Drive: abrir pasta, voltar e enviar — sem recarregar e sem repetir o Google.

Pedido (29/09/2026): "otimizar a abertura e interação de pastas, retorno de
páginas, inserção de arquivos, assim otimizando o fluxo de envio".

O que este teste cobre:

- o fragmento da pasta (`?frag=pasta`) devolve grade, trilha e o que muda de
  pasta para pasta — é ele que troca a tela sem recarregar;
- o "carregar mais" continua recebendo só a grade;
- quem não pode ver aquela pasta não recebe fragmento nenhum;
- a mesma pasta aberta duas vezes fala com o Google **uma** vez (é o "voltar"
  instantâneo), e a subida da árvore não se repete dentro da requisição;
- enviar, criar pasta e renomear **esquecem** a listagem: o que acabou de
  entrar aparece na hora, sem esperar o cache vencer;
- a tela oferece a navegação sem recarregar (links marcados, envio em paralelo).

Google Drive é um dublê em memória. Transação desfeita no fim.
"""
import io
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dv-nav'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dv-nav-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.test import Client

from drive import gdrive
from drive.models import DriveConfig, DrivePermission, SectorDriveMapping
from users.models import Sector

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


class DriveFalso:
    """Conta o que o portal pede ao Google — é o que este teste mede."""

    def __init__(self):
        self.itens = {}
        self.listagens, self.metadados = [], []

    def add(self, id_, nome, mime, pai, conteudo=b''):
        self.itens[id_] = dict(id=id_, name=nome, mimeType=mime, parents=[pai] if pai else [],
                               trashed=False, size=str(len(conteudo)),
                               modifiedTime='2026-09-20T10:00:00Z', _bytes=conteudo)
        return self.itens[id_]

    def obter(self, file_id, fields=None):
        self.metadados.append(file_id)
        if file_id not in self.itens:
            raise gdrive.DriveError('404')
        return {k: v for k, v in self.itens[file_id].items() if k != '_bytes'}

    def listar(self, folder_id, page_token=None, page_size=100, trashed=False,
               apenas_pastas=False, order='folder,name'):
        self.listagens.append(folder_id)
        filhos = [{k: v for k, v in i.items() if k != '_bytes'} for i in self.itens.values()
                  if folder_id in i['parents'] and not i['trashed']]
        return filhos, None

    def criar_pasta(self, nome, parent_id):
        novo = self.add(f'NOVA{len(self.itens)}', nome, FOLDER, parent_id)
        gdrive.esquecer(parent_id)
        return {k: v for k, v in novo.items() if k != '_bytes'}

    def enviar(self, nome, mimetype, stream, parent_id):
        dados = stream.read() if hasattr(stream, 'read') else b''
        novo = self.add(f'ENV{len(self.itens)}', nome, mimetype or 'application/pdf', parent_id, dados)
        gdrive.esquecer(parent_id)
        return {k: v for k, v in novo.items() if k != '_bytes'}

    def renomear(self, file_id, novo_nome):
        self.itens[file_id]['name'] = novo_nome
        gdrive.esquecer(file_id, (self.itens[file_id]['parents'] or [''])[0])
        return {k: v for k, v in self.itens[file_id].items() if k != '_bytes'}

    def id_da_raiz(self):
        return 'RAIZ'


falso = DriveFalso()
falso.add('RAIZ', 'Meu Drive', FOLDER, None)
falso.add('SETOR', 'ZZ Comercial', FOLDER, 'RAIZ')
falso.add('CONTRATOS', 'Contratos', FOLDER, 'SETOR')
falso.add('ASSINADOS', 'Assinados', FOLDER, 'CONTRATOS')
falso.add('A1', 'contrato.pdf', 'application/pdf', 'CONTRATOS', b'zz' * 10)
falso.add('A2', 'aditivo.pdf', 'application/pdf', 'ASSINADOS', b'zz' * 10)
falso.add('FORA', 'Outro setor', FOLDER, 'RAIZ')
falso.add('A3', 'sigiloso.pdf', 'application/pdf', 'FORA', b'zz')

# `obter` e `listar` são o que o portal usa para tudo: cadeia, trilha e grade
# saem daí (é assim que o cache novo é medido).
FALSOS = {n: getattr(falso, n) for n in ('obter', 'listar', 'criar_pasta', 'enviar', 'renomear',
                                         'id_da_raiz')}

marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Setor da Navegação')
    outro = Sector.objects.create(name='ZZ Setor de fora')
    ana = User.objects.create_user(
        username='zzn.ana', email='zzn.ana@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZN', last_name='Ana', sector=setor)
    so_ver = User.objects.create_user(
        username='zzn.sover', email='zzn.sover@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZN', last_name='Sóver', sector=setor)

    cfg = DriveConfig.get(); cfg.ativo = True; cfg.save()
    mapa = SectorDriveMapping.objects.create(sector=setor, folder_id='SETOR', folder_name='ZZ Comercial')
    SectorDriveMapping.objects.create(sector=outro, folder_id='FORA', folder_name='Outro setor')
    DrivePermission.objects.create(mapping=mapa, alvo='USER', target_user=ana, nivel='DELETE')
    DrivePermission.objects.create(mapping=mapa, alvo='USER', target_user=so_ver, nivel='VIEW')

    c = Client(); c.force_login(ana)
    cv = Client(); cv.force_login(so_ver)
    raiz = f'/drive/s/{setor.id}/'

    def frag(cliente, url):
        r = cliente.get(url + ('&' if '?' in url else '?') + 'frag=pasta',
                        HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        try:
            return r.status_code, json.loads(r.content)
        except Exception:
            return r.status_code, {}

    with mock.patch.multiple(gdrive, **FALSOS):
        print('== O FRAGMENTO DA PASTA ==')
        cache.clear()
        status, dado = frag(c, raiz + 'f/CONTRATOS/')
        t('responde em JSON', status == 200 and dado.get('ok'), (status, list(dado)))
        t('com a grade da pasta', 'contrato.pdf' in dado.get('lista', ''))
        t('e as subpastas dela', 'Assinados' in dado['lista'])
        t('com a trilha até ali (raiz do setor e a pasta aberta)',
          'ZZ Setor da Navegação' in dado.get('trilha', '') and 'Contratos' in dado['trilha'],
          repr(dado.get('trilha', ''))[:300])
        t('e os degraus da trilha também navegam sem recarregar',
          'data-dv-ir' in dado['trilha'])
        t('dizendo qual pasta está aberta', dado.get('folder_id') == 'CONTRATOS', dado.get('folder_id'))
        t('e para onde aponta o ZIP desta pasta',
          dado.get('url_zip') == '/drive/file/CONTRATOS/zip/', dado.get('url_zip'))
        t('com o que a pessoa pode fazer aqui',
          dado.get('pode_upload') and dado.get('pode_editar'))
        t('os links de pasta sabem navegar sem recarregar', 'data-dv-ir' in dado['lista'])

        r = c.get(raiz + 'f/CONTRATOS/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        html = r.content.decode()
        t('sem frag, continua vindo só a grade (o "carregar mais")',
          'contrato.pdf' in html and '{' != html[:1] and 'dv-trilha' not in html)

        status, dado = frag(cv, raiz + 'f/CONTRATOS/')
        t('quem só vê não pode enviar por aqui', not dado.get('pode_upload'), dado.get('pode_upload'))
        r = cv.get(f'/drive/s/{outro.id}/f/FORA/?frag=pasta', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        t('e pasta de outro setor não abre', r.status_code in (302, 403), r.status_code)

        print('\n== O GOOGLE NÃO É CHAMADO DUAS VEZES ==')
        cache.clear()
        falso.listagens.clear(); falso.metadados.clear()
        c.get(raiz + 'f/ASSINADOS/')
        primeira = (len(falso.listagens), len(falso.metadados))
        t('abrir uma pasta funda pede a listagem uma vez',
          falso.listagens == ['ASSINADOS'], falso.listagens)
        t('e sobe a árvore sem repetir o mesmo id',
          len(falso.metadados) == len(set(falso.metadados)), falso.metadados)

        falso.listagens.clear(); falso.metadados.clear()
        c.get(raiz + 'f/ASSINADOS/')
        t('voltar para ela não sobe a árvore de novo',
          falso.metadados == [], falso.metadados)

        falso.listagens.clear(); falso.metadados.clear()
        c.get(raiz + 'f/CONTRATOS/')
        t('a pasta de cima também já está de graça (veio na descida)',
          falso.metadados == [], falso.metadados)

        print('\n== A LISTAGEM TAMBÉM FICA GUARDADA ==')
    # Fora do `patch` de `listar`: aqui quem responde é a função de verdade,
    # com o cache — o Google é dublado um degrau abaixo, em `_executar`.
    cache.clear()
    idas = []

    class ServicoFalso:
        def files(self):
            return self

        def list(self, **kwargs):
            return ('lista', kwargs)

        def get(self, **kwargs):
            return ('get', kwargs)

    def executar_falso(req):
        idas.append(req[0])
        return {'files': [{'id': 'X1', 'name': 'zz.pdf', 'mimeType': 'application/pdf',
                           'parents': ['ASSINADOS']}], 'nextPageToken': None}

    with mock.patch.object(gdrive, 'service', return_value=ServicoFalso()), \
         mock.patch.object(gdrive, '_executar', executar_falso):
        itens, prox = gdrive.listar('ASSINADOS', page_size=60)
        t('a primeira listagem vai ao Google', idas == ['lista'] and len(itens) == 1, idas)
        itens2, _ = gdrive.listar('ASSINADOS', page_size=60)
        t('a segunda vem do cache (é o "voltar" instantâneo)',
          idas == ['lista'] and itens2 == itens, idas)
        t('e os filhos listados já ficam prontos para a descida',
          gdrive.meta_leve('X1') is not None and idas == ['lista'], idas)
        gdrive.esquecer('ASSINADOS')
        gdrive.listar('ASSINADOS', page_size=60)
        t('depois de esquecer, pergunta de novo', idas == ['lista', 'lista'], idas)
        # Página seguinte nunca entra no cache: tem token, é sempre nova.
        gdrive.listar('ASSINADOS', page_size=60, page_token='t2')
        gdrive.listar('ASSINADOS', page_size=60, page_token='t2')
        t('página seguinte não é guardada', idas.count('lista') == 4, idas)

    with mock.patch.multiple(gdrive, **FALSOS):
        print('\n== O QUE MUDA APARECE NA HORA ==')
        falso.listagens.clear()
        r = c.post(raiz + 'mkdir/', {'folder_id': 'CONTRATOS', 'nome': 'ZZ Pasta nova'},
                   HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        t('criar pasta responde em JSON', r.status_code == 200 and json.loads(r.content)['ok'],
          r.status_code)
        status, dado = frag(c, raiz + 'f/CONTRATOS/')
        t('e ela já está na grade', 'ZZ Pasta nova' in dado['lista'])
        t('porque a listagem daquela pasta foi esquecida',
          falso.listagens.count('CONTRATOS') >= 1, falso.listagens)

        envio = io.BytesIO(b'zz-arquivo-novo')
        envio.name = 'novo.pdf'
        r = c.post(raiz + 'upload/', {'folder_id': 'CONTRATOS', 'arquivos': envio},
                   HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        t('enviar responde em JSON', r.status_code == 200 and json.loads(r.content)['enviados'] == 1,
          r.content[:120])
        status, dado = frag(c, raiz + 'f/CONTRATOS/')
        t('e o arquivo aparece sem esperar o cache vencer', 'novo.pdf' in dado['lista'])

        r = c.post('/drive/file/A1/renomear/', {'nome': 'contrato-assinado.pdf', 'folder_id': 'CONTRATOS'},
                   HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        status, dado = frag(c, raiz + 'f/CONTRATOS/')
        t('renomear também aparece na hora',
          'contrato-assinado.pdf' in dado['lista'] and 'contrato.pdf' not in dado['lista'])

        print('\n== A TELA ==')
        html = c.get(raiz + 'f/CONTRATOS/').content.decode()
        t('a página traz a navegação sem recarregar',
          'dvIrPara' in html and 'popstate' in html and 'dv-trilha' in html)
        t('guarda o que já viu para o voltar ser instantâneo', 'DV_CACHE' in html)
        t('envia em paralelo', 'POR_VEZ' in html and 'Promise.all' in html)
        t('e não recarrega a página no fim do envio',
          'location.reload()' not in html and 'dvRecarregarPasta' in html)
        t('criar pasta e renomear vão por fetch', 'dvPost' in html)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado; nada saiu para o Google.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
