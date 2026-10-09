"""Drive: o "voltar" do arquivo leva à pasta em que ele está (/drive/file/{id}/).

Pedido (09/10/2026): "ao tentar voltar para a pasta que o usuário estava ele
volta para o início de tudo, faça a volta bonitinha para a pasta que ele estava".

O que este teste cobre:

- arquivo numa subpasta: o "voltar" vai para a subpasta, com o nome dela,
  ``?destaque=<arquivo>`` e a trilha da raiz até ali (cada nível clicável);
- arquivo na raiz do setor: volta para a raiz;
- quem só pode ver o arquivo (liberado avulso) e não a pasta: volta para a raiz,
  sem link para a pasta que não pode abrir;
- a pasta, aberta com ``?destaque``, traz o código que rola até o arquivo e o
  destaca (e tira o parâmetro do endereço).

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
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dv-volta'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dv-volta-2'},
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
    setor = Sector.objects.create(name='ZZ Setor da Volta')
    ana = User.objects.create_user(
        username='zzv.ana', email='zzv.ana@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZV', last_name='Ana', sector=setor)
    avulso = User.objects.create_user(
        username='zzv.avulso', email='zzv.avulso@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZV', last_name='Avulso', sector=setor)
    cfg = DriveConfig.get(); cfg.ativo = True; cfg.save()
    mapa = SectorDriveMapping.objects.create(sector=setor, folder_id='SETOR', folder_name='ZZ Comercial')
    DrivePermission.objects.create(mapping=mapa, alvo='USER', target_user=ana, nivel='VIEW')
    # Liberado só o arquivo A2 (escopo no próprio item), não a pasta dele.
    DrivePermission.objects.create(mapping=mapa, alvo='USER', target_user=avulso, nivel='VIEW', folder_id='A2')
    falso.add('A0', 'regimento.pdf', 'application/pdf', 'SETOR', b'zz')
    c = Client(); c.force_login(ana)
    cav = Client(); cav.force_login(avulso)
    raiz = f'/drive/s/{setor.id}/'

    with mock.patch.multiple(gdrive, **FALSOS):
        print('== ARQUIVO NUMA SUBPASTA ==')
        cache.clear()
        r = c.get('/drive/file/A2/')
        t('a página do arquivo abre', r.status_code == 200, r.status_code)
        t('voltar leva à subpasta do arquivo, destacando-o', r.context['url_voltar'] == raiz + 'f/ASSINADOS/?destaque=A2',
          r.context['url_voltar'])
        t('com o nome da pasta no botão', r.context['nome_voltar'] == 'Assinados' and 'Voltar a <span' in r.content.decode())
        t('e a trilha da raiz até a pasta', r.context['trilha_arquivo'] == [('Contratos', raiz + 'f/CONTRATOS/'),
                                                                            ('Assinados', raiz + 'f/ASSINADOS/')],
          r.context['trilha_arquivo'])
        html = r.content.decode()
        t('trilha clicável na tela', f'href="{raiz}f/CONTRATOS/"' in html and f'href="{raiz}f/ASSINADOS/?destaque=A2"' in html)

        print('== ARQUIVO NA RAIZ ==')
        r = c.get('/drive/file/A0/')
        t('arquivo na raiz do setor volta para a raiz', r.context['url_voltar'] == raiz + '?destaque=A0'
          and r.context['nome_voltar'] == 'ZZ Setor da Volta' and r.context['trilha_arquivo'] == [],
          (r.context['url_voltar'], r.context['nome_voltar']))

        print('== LIBERADO SÓ O ARQUIVO ==')
        r = cav.get('/drive/file/A2/')
        t('quem só vê o arquivo abre a página', r.status_code == 200, r.status_code)
        t('mas volta para a raiz (não pode abrir a pasta)', r.context['url_voltar'] == raiz + '?destaque=A2'
          and f'href="{raiz}f/ASSINADOS/' not in r.content.decode(), r.context['url_voltar'])

        print('== A PASTA DESTACA O ARQUIVO ==')
        r = c.get(raiz + 'f/ASSINADOS/?destaque=A2')
        html = r.content.decode()
        t('a pasta abre com o destaque', r.status_code == 200 and 'data-id="A2"' in html, r.status_code)
        t('com o código que rola até o arquivo e tira o parâmetro', "params.get('destaque')" in html
          and 'dv-destaque' in html and 'scrollIntoView' in html)
finally:
    marcador.__exit__(Exception, Exception('rollback'), None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
