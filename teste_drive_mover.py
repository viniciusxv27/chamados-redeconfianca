"""Drive: mover arquivo ou pasta para dentro de outra pasta (/drive/s/{id}/).

Pedido (13/10/2026): "Adicione a parte para mover uma pasta para dentro de
outra pasta, ou um arquivo para dentro de uma pasta".

Cobre a tela (menu "Mover para…", cartões arrastáveis, trilha que recebe o que
se solta), o mover em si e o que ele recusa (laço de pasta, fora do setor,
destino que não é pasta, sem permissão no destino), e o seletor de pastas.

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
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dv-mover'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dv-mover-2'},
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
        self.listagens, self.metadados, self.movidos = [], [], []

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
                  if folder_id in i['parents'] and not i['trashed']
                  and (not apenas_pastas or i['mimeType'] == FOLDER)]
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

    def mover(self, file_id, novo_parent, parent_atual=None):
        self.movidos.append((file_id, novo_parent))
        antigos = self.itens[file_id]['parents']
        self.itens[file_id]['parents'] = [novo_parent]
        gdrive.esquecer(file_id, novo_parent, *antigos)
        return {k: v for k, v in self.itens[file_id].items() if k != '_bytes'}

    def id_da_raiz(self):
        return 'RAIZ'


falso = DriveFalso()
falso.add('RAIZ', 'Meu Drive', FOLDER, None)
falso.add('SETOR', 'ZZ Comercial', FOLDER, 'RAIZ')
falso.add('CONTRATOS', 'Contratos', FOLDER, 'SETOR')
falso.add('ASSINADOS', 'Assinados', FOLDER, 'CONTRATOS')
falso.add('ANTIGOS', 'Antigos', FOLDER, 'ASSINADOS')
falso.add('RH', 'RH', FOLDER, 'SETOR')
falso.add('A1', 'contrato.pdf', 'application/pdf', 'CONTRATOS', b'zz' * 10)
falso.add('A2', 'aditivo.pdf', 'application/pdf', 'ASSINADOS', b'zz' * 10)
falso.add('A0', 'regimento.pdf', 'application/pdf', 'SETOR', b'zz')
falso.add('FORA', 'Outro setor', FOLDER, 'RAIZ')

FALSOS = {n: getattr(falso, n) for n in ('obter', 'listar', 'criar_pasta', 'enviar', 'renomear', 'mover',
                                         'id_da_raiz')}


def pai(id_):
    return falso.itens[id_]['parents'][0]


marcador = transaction.atomic()
marcador.__enter__()
try:
    from drive.models import DriveAuditLog
    setor = Sector.objects.create(name='ZZ Setor do Mover')
    edita = User.objects.create_user(username='zzm.edita', email='zzm.edita@exemplo-teste.local',
                                     password='S3nha!teste', first_name='ZZM', last_name='Edita', sector=setor)
    ve = User.objects.create_user(username='zzm.ve', email='zzm.ve@exemplo-teste.local',
                                  password='S3nha!teste', first_name='ZZM', last_name='Ve', sector=setor)
    parcial = User.objects.create_user(username='zzm.parcial', email='zzm.parcial@exemplo-teste.local',
                                       password='S3nha!teste', first_name='ZZM', last_name='Parcial', sector=setor)
    cfg = DriveConfig.get(); cfg.ativo = True; cfg.save()
    mapa = SectorDriveMapping.objects.create(sector=setor, folder_id='SETOR', folder_name='ZZ Comercial')
    DrivePermission.objects.create(mapping=mapa, alvo='USER', target_user=edita, nivel='EDIT')
    DrivePermission.objects.create(mapping=mapa, alvo='USER', target_user=ve, nivel='VIEW')
    # Pode editar só dentro de Contratos; no resto do setor, só vê.
    DrivePermission.objects.create(mapping=mapa, alvo='USER', target_user=parcial, nivel='VIEW')
    DrivePermission.objects.create(mapping=mapa, alvo='USER', target_user=parcial, nivel='EDIT', folder_id='CONTRATOS')
    c = Client(); c.force_login(edita)
    cv = Client(); cv.force_login(ve)
    cp = Client(); cp.force_login(parcial)
    raiz = f'/drive/s/{setor.id}/'
    AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}

    def mover(cli, item, destino):
        cache.clear()
        r = cli.post(f'/drive/file/{item}/mover/', {'destino': destino, 'folder_id': 'SETOR'}, **AJAX)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {}

    with mock.patch.multiple(gdrive, **FALSOS):
        print('== A TELA ==')
        cache.clear()
        html = c.get(raiz + 'f/CONTRATOS/').content.decode()
        t('quem edita vê "Mover para…" no menu', "dvMoverPara('A1','contrato.pdf')" in html and 'id="dv-mover"' in html)
        t('e os cartões são arrastáveis', 'data-id="A1"' in html and 'draggable="true"' in html)
        t('a trilha aceita soltar (id da pasta em cada nível)', 'data-pasta="SETOR"' in html and 'data-pasta="CONTRATOS"' in html)
        html = cv.get(raiz + 'f/CONTRATOS/').content.decode()
        t('quem só vê não tem mover nem arrastar', 'dvMoverPara(' not in html and 'draggable="true"' not in html)

        print('\n== MOVER ==')
        st, d = mover(c, 'A1', 'ASSINADOS')
        t('arquivo para dentro de uma pasta', st == 200 and d.get('ok') and pai('A1') == 'ASSINADOS', (st, d))
        t('com recado dizendo para onde foi', d.get('msg') == '"contrato.pdf" movido para "Assinados".' and d.get('destino') == 'ASSINADOS', d)
        t('fica na auditoria', DriveAuditLog.objects.filter(file_id='A1', acao='MOVE').exists())
        st, d = mover(c, 'RH', 'CONTRATOS')
        t('pasta para dentro de outra pasta', st == 200 and pai('RH') == 'CONTRATOS', (st, d))
        falso.itens['RH']['parents'] = ['SETOR']
        st, d = mover(c, 'ASSINADOS', 'SETOR')
        t('pasta subindo para a raiz (soltar na trilha)', st == 200 and pai('ASSINADOS') == 'SETOR', (st, d))
        t('a raiz aparece com o nome do setor no portal', d.get('msg') == '"Assinados" movido para "ZZ Setor do Mover".', d)
        falso.itens['ASSINADOS']['parents'] = ['CONTRATOS']; cache.clear()

        print('\n== O QUE NÃO PODE ==')
        antes = len(falso.movidos)
        st, d = mover(c, 'CONTRATOS', 'ANTIGOS')
        t('pasta para dentro de uma subpasta dela', st == 400 and 'dentro dela mesma' in d.get('msg', '') and pai('CONTRATOS') == 'SETOR', d)
        st, d = mover(c, 'CONTRATOS', 'CONTRATOS')
        t('pasta para dentro dela mesma', st == 400 and 'dentro dela mesma' in d.get('msg', ''), d)
        st, d = mover(c, 'A0', 'FORA')
        t('para fora do setor', st == 400 and 'mesmo setor' in d.get('msg', '') and pai('A0') == 'SETOR', d)
        st, d = mover(c, 'A0', 'A2')
        t('para dentro de um arquivo', st == 400 and 'precisa ser uma pasta' in d.get('msg', ''), d)
        st, d = mover(c, 'A2', 'ASSINADOS')
        t('para onde já está', st == 400 and 'já está' in d.get('msg', ''), d)
        st, d = mover(c, 'SETOR', 'CONTRATOS')
        t('a pasta principal do setor', st == 400 and 'pasta principal' in d.get('msg', ''), d)
        st, d = mover(c, 'A0', '../x')
        t('destino que não é id do Drive', st == 400, d)
        st, d = mover(cv, 'A0', 'CONTRATOS')
        t('quem só vê não move (403)', st == 403 and pai('A0') == 'SETOR', st)
        st, d = mover(cp, 'A2', 'SETOR')
        t('quem edita só em Contratos não põe nada na raiz', st == 400 and 'permissão' in d.get('msg', '') and pai('A2') == 'ASSINADOS', d)
        st, d = mover(cp, 'A0', 'CONTRATOS')
        t('nem tira da raiz o que lá só pode ver', st == 403 and pai('A0') == 'SETOR', st)
        st, d = mover(cp, 'A2', 'CONTRATOS')
        t('mas move dentro de Contratos', st == 200 and pai('A2') == 'CONTRATOS', d)
        falso.itens['A2']['parents'] = ['ASSINADOS']
        t('nenhuma recusa chegou ao Google', len(falso.movidos) == antes + 1, falso.movidos[antes:])

        print('\n== O SELETOR DE PASTAS ==')
        cache.clear()
        d = c.get('/drive/file/CONTRATOS/mover/pastas/', **AJAX).json()
        t('começa na raiz, com o nome do setor', d['ok'] and d['pai'] == 'SETOR' and d['trilha'] == [{'id': 'SETOR', 'nome': 'ZZ Setor do Mover'}], d)
        t('lista as pastas sem o próprio item', [p['id'] for p in d['pastas']] == ['RH'], d['pastas'])
        t('e diz que o item já está ali', d['ja_esta_aqui'] and d['pode_aqui'])
        r = c.get('/drive/file/CONTRATOS/mover/pastas/?pai=ASSINADOS', **AJAX)
        t('não deixa entrar numa subpasta do próprio item', r.status_code == 400)
        r = c.get('/drive/file/A0/mover/pastas/?pai=FORA', **AJAX)
        t('nem sair do setor', r.status_code == 400)
        d = c.get('/drive/file/A0/mover/pastas/?pai=ASSINADOS', **AJAX).json()
        t('trilha com o caminho e o "voltar"', [x['nome'] for x in d['trilha']] == ['ZZ Setor do Mover', 'Contratos', 'Assinados']
          and d['acima'] == 'CONTRATOS' and not d['ja_esta_aqui'], d)
        d = cp.get('/drive/file/A2/mover/pastas/', **AJAX).json()
        t('quem edita só em Contratos vê a raiz sem poder pôr nada nela', d['ok'] and not d['pode_aqui'], d)
        d = cp.get('/drive/file/A2/mover/pastas/?pai=CONTRATOS', **AJAX).json()
        t('e pode em Contratos', d['pode_aqui'], d)
        r = cv.get('/drive/file/A0/mover/pastas/', **AJAX)
        t('quem só vê não abre o seletor', r.status_code == 403)
finally:
    marcador.__exit__(Exception, Exception('rollback'), None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
