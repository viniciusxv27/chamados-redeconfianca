"""Drive: baixar uma cópia zipada da pasta (ou do arquivo).

Pedido: "precisa criar uma função dentro do drive de criar uma cópia dos
arquivos zipados".

O que este teste cobre:

- o ZIP de uma pasta traz os arquivos com o caminho das subpastas;
- arquivo nativo do Google (Documento/Planilha) fica de fora — não tem bytes;
- o ZIP de um arquivo só também funciona;
- quem não pode baixar dali não baixa, e pasta de outro setor não passa;
- os limites (arquivos e tamanho) recusam com mensagem clara, em vez de o
  servidor tentar zipar o setor inteiro;
- pasta sem nada para zipar avisa;
- arquivo que o Google não devolve não derruba o ZIP: ele sai com um aviso
  dentro;
- fica registrado na auditoria;
- a tela oferece a opção no menu do item e na barra da pasta.

Google Drive é um dublê em memória. Transação desfeita no fim.
"""
import io
import os
import sys
import zipfile
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dv-zip'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dv-zip-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from drive import gdrive, zip_copia
from drive.models import DriveAuditLog, DriveConfig, DrivePermission, SectorDriveMapping
from users.models import Sector

User = get_user_model()
FOLDER = gdrive.FOLDER_MIME
GDOC = 'application/vnd.google-apps.document'
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
    def __init__(self):
        self.itens = {}
        self.quebrados = set()

    def add(self, id_, nome, mime, pai, conteudo=b''):
        self.itens[id_] = dict(id=id_, name=nome, mimeType=mime, parents=[pai] if pai else [],
                               trashed=False, size=str(len(conteudo)),
                               modifiedTime='2026-09-20T10:00:00Z', _bytes=conteudo)
        return self.itens[id_]

    def obter(self, file_id, fields=None):
        if file_id not in self.itens:
            raise gdrive.DriveError('404')
        return {k: v for k, v in self.itens[file_id].items() if k != '_bytes'}

    def ancestrais(self, file_id, limite=30):
        ids, atual = [], file_id
        while atual and atual not in ids and len(ids) < limite:
            ids.append(atual)
            pais = self.itens.get(atual, {}).get('parents') or []
            atual = pais[0] if pais else None
        return ids

    def dentro_de(self, file_id, root_id):
        return bool(file_id and root_id) and root_id in self.ancestrais(file_id)

    def caminho(self, file_id, ate_root=None, limite=30):
        ids = list(reversed(self.ancestrais(file_id)))
        return [(i, self.itens[i]['name']) for i in ids if i in self.itens]

    def listar(self, folder_id, page_token=None, page_size=100, trashed=False,
               apenas_pastas=False, order='folder,name'):
        filhos = [{k: v for k, v in i.items() if k != '_bytes'} for i in self.itens.values()
                  if folder_id in i['parents'] and not i['trashed']
                  and (not apenas_pastas or i['mimeType'] == FOLDER)]
        return filhos, None

    def pai_de(self, file_id):
        return (self.itens.get(file_id, {}).get('parents') or [''])[0]

    def pais_de(self, ids):
        return {i: (self.itens.get(i, {}).get('parents') or [''])[0] for i in ids if i}

    def baixar(self, file_id, preview=False):
        if file_id in self.quebrados:
            raise gdrive.DriveError('o Google não respondeu')
        meta = self.itens[file_id]
        return io.BytesIO(meta['_bytes']), meta['name'], meta['mimeType']

    def id_da_raiz(self):
        return 'RAIZ'


falso = DriveFalso()
falso.add('RAIZ', 'Meu Drive', FOLDER, None)
falso.add('SETOR', 'ZZ Comercial', FOLDER, 'RAIZ')
falso.add('CONTRATOS', 'Contratos', FOLDER, 'SETOR')
falso.add('A1', 'contrato.pdf', 'application/pdf', 'CONTRATOS', b'zz-contrato' * 10)
falso.add('A2', 'foto.jpg', 'image/jpeg', 'CONTRATOS', b'zz-foto' * 20)
falso.add('ASSINADOS', 'Assinados', FOLDER, 'CONTRATOS')
falso.add('A3', 'assinado.pdf', 'application/pdf', 'ASSINADOS', b'zz-assinado' * 5)
falso.add('DOC', 'planilha do google', GDOC, 'CONTRATOS')
falso.add('VAZIA', 'Pasta vazia', FOLDER, 'SETOR')
falso.add('FORA', 'Outro setor', FOLDER, 'RAIZ')
falso.add('A4', 'sigiloso.pdf', 'application/pdf', 'FORA', b'zz-sigiloso')

FALSOS = {n: getattr(falso, n) for n in
          ('obter', 'ancestrais', 'dentro_de', 'caminho', 'listar', 'pai_de', 'pais_de',
           'baixar', 'id_da_raiz')}

marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Setor do ZIP')
    outro = Sector.objects.create(name='ZZ Setor de fora')
    chefe = User.objects.create_user(
        username='zzz.chefe', email='zzz.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)
    ana = User.objects.create_user(
        username='zzz.ana', email='zzz.ana@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Ana', sector=setor)
    so_ver = User.objects.create_user(
        username='zzz.sover', email='zzz.sover@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Sóver', sector=setor)

    cfg = DriveConfig.get(); cfg.ativo = True; cfg.save()
    mapa = SectorDriveMapping.objects.create(sector=setor, folder_id='SETOR', folder_name='ZZ Comercial')
    SectorDriveMapping.objects.create(sector=outro, folder_id='FORA', folder_name='Outro setor')
    DrivePermission.objects.create(mapping=mapa, alvo='USER', target_user=ana, nivel='DOWNLOAD')
    DrivePermission.objects.create(mapping=mapa, alvo='USER', target_user=so_ver, nivel='VIEW')

    c = Client(); c.force_login(ana)
    cv = Client(); cv.force_login(so_ver)
    cc = Client(); cc.force_login(chefe)

    def baixar(cliente, file_id):
        return cliente.get(f'/drive/file/{file_id}/zip/')

    def abrir_zip(resposta):
        dados = b''.join(resposta.streaming_content)
        return zipfile.ZipFile(io.BytesIO(dados))

    with mock.patch.multiple(gdrive, **FALSOS):
        print('== O ZIP DA PASTA ==')
        r = baixar(c, 'CONTRATOS')
        t('a pasta baixa em zip', r.status_code == 200
          and r['Content-Type'] == 'application/zip', (r.status_code, r.get('Content-Type')))
        t('com o nome da pasta no arquivo', 'Contratos.zip' in r['Content-Disposition'],
          r.get('Content-Disposition'))
        pacote = abrir_zip(r)
        nomes = sorted(pacote.namelist())
        t('traz os arquivos da pasta e da subpasta',
          nomes == ['Assinados/assinado.pdf', 'contrato.pdf', 'foto.jpg'], nomes)
        t('com o conteúdo certo', pacote.read('contrato.pdf') == b'zz-contrato' * 10
          and pacote.read('Assinados/assinado.pdf') == b'zz-assinado' * 5)
        t('e sem o arquivo nativo do Google', 'planilha do google' not in ' '.join(nomes))
        t('o zip abre sem erro', pacote.testzip() is None)

        print('\n== O ZIP DE UM ARQUIVO SÓ ==')
        r = baixar(c, 'A1')
        pacote = abrir_zip(r)
        t('um arquivo também vira zip', r.status_code == 200 and pacote.namelist() == ['contrato.pdf'],
          pacote.namelist())
        t('com o nome dele', 'contrato.pdf.zip' in r['Content-Disposition'], r.get('Content-Disposition'))

        print('\n== QUEM PODE ==')
        t('sem nível de download, não baixa', baixar(cv, 'CONTRATOS').status_code in (302, 403),
          baixar(cv, 'CONTRATOS').status_code)
        t('pasta de outro setor não passa', baixar(c, 'FORA').status_code in (302, 403))
        t('o SUPERADMIN baixa', baixar(cc, 'CONTRATOS').status_code == 200)

        print('\n== OS LIMITES ==')
        r = baixar(c, 'VAZIA')
        t('pasta vazia avisa em vez de mandar zip vazio',
          r.status_code == 400 and 'Não há arquivos' in r.json()['msg'], r.content[:120])

        with mock.patch.object(zip_copia, 'LIMITE_ARQUIVOS', 2):
            r = baixar(c, 'CONTRATOS')
            t('passando do limite de arquivos, recusa com mensagem',
              r.status_code == 400 and 'passa do limite' in r.json()['msg'], r.content[:160])
            t('e manda abrir uma subpasta', 'subpasta' in r.json()['msg'])
        with mock.patch.object(zip_copia, 'LIMITE_BYTES', 10):
            r = baixar(c, 'CONTRATOS')
            t('passando do limite de tamanho, idem', r.status_code == 400)

        print('\n== QUANDO UM ARQUIVO FALHA ==')
        falso.quebrados.add('A2')
        r = baixar(c, 'CONTRATOS')
        pacote = abrir_zip(r)
        nomes = sorted(pacote.namelist())
        t('o zip vem assim mesmo, sem o que falhou',
          'foto.jpg' not in nomes and 'contrato.pdf' in nomes, nomes)
        t('e diz lá dentro o que não entrou',
          'ARQUIVOS-QUE-NAO-ENTRARAM.txt' in nomes
          and b'foto.jpg' in pacote.read('ARQUIVOS-QUE-NAO-ENTRARAM.txt'), nomes)
        falso.quebrados.clear()

        print('\n== A AUDITORIA ==')
        DriveAuditLog.objects.filter(user=ana).delete()
        baixar(c, 'CONTRATOS')
        registro = DriveAuditLog.objects.filter(user=ana, acao='DOWNLOAD').first()
        t('o download do zip fica registrado', registro is not None and 'zip' in registro.detalhe,
          registro.detalhe if registro else None)
        t('com quantos arquivos foram', '3 arquivo' in registro.detalhe, registro.detalhe)

        print('\n== A TELA ==')
        html = c.get(f'/drive/s/{setor.id}/f/CONTRATOS/').content.decode()
        t('a barra da pasta oferece baixar em ZIP',
          '/drive/file/CONTRATOS/zip/' in html and 'Baixar em ZIP' in html)
        t('e o menu de cada item também', '/drive/file/A1/zip/' in html)
        t('com aviso de que está preparando', 'dvAvisarZip' in html and 'Preparando o ZIP' in html)
        html = cv.get(f'/drive/s/{setor.id}/f/CONTRATOS/').content.decode()
        t('quem só vê não recebe a opção', 'zip/' not in html)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco; nada saiu para o Google.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
