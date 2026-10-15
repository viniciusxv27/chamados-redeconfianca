"""Meu Drive: baixar pasta ou arquivos (/drive/meu-drive/f/<id>/).

Pedido: "Permita baixar pasta ou arquivos".

- a pasta aberta e cada subpasta baixam em ZIP;
- vários itens marcados (pastas e arquivos misturados) saem num ZIP só;
- Documento/Planilha do Google entram convertidos (Word/Excel);
- limite, pasta vazia e id inválido voltam para a pasta com aviso;
- só o SUPERADMIN com a conta conectada; fica na auditoria;
- o zip do setor e o "usar localmente" continuam sem os nativos do Google.

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
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-md-zip'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-md-zip-2'},
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
from drive.models import DriveAuditLog, DriveConfig

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
        if meta['mimeType'] == GDOC:          # o Google exporta o Doc como Word
            return io.BytesIO(b'zz-docx'), meta['name'] + '.docx', 'application/docx'
        return io.BytesIO(meta['_bytes']), meta['name'], meta['mimeType']

    def id_da_raiz(self):
        return 'RAIZ'


falso = DriveFalso()
falso.add('RAIZ', 'Meu Drive', FOLDER, None)
falso.add('PROJETOS', 'Projetos', FOLDER, 'RAIZ')
falso.add('A1', 'orcamento.pdf', 'application/pdf', 'PROJETOS', b'zz-orcamento' * 10)
falso.add('SUB', 'Fotos', FOLDER, 'PROJETOS')
falso.add('A2', 'loja.jpg', 'image/jpeg', 'SUB', b'zz-loja' * 20)
falso.add('DOC', 'Ata da reunião', GDOC, 'PROJETOS')
falso.add('FORM', 'Pesquisa', 'application/vnd.google-apps.form', 'PROJETOS')
falso.add('A3', 'solto.txt', 'text/plain', 'RAIZ', b'zz-solto')
falso.add('VAZIA', 'Vazia', FOLDER, 'RAIZ')

FALSOS = {n: getattr(falso, n) for n in
          ('obter', 'ancestrais', 'dentro_de', 'caminho', 'listar', 'pai_de', 'pais_de',
           'baixar', 'id_da_raiz')}

marcador = transaction.atomic()
marcador.__enter__()
try:
    chefe = User.objects.create_user(
        username='zzz.mdchefe', email='zzz.mdchefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)
    comum = User.objects.create_user(
        username='zzz.mdcomum', email='zzz.mdcomum@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Comum')

    cfg = DriveConfig.get()
    cfg.ativo = True
    cfg.modo = DriveConfig.Modo.OAUTH
    cfg.oauth_client_id = '123-zz.apps.googleusercontent.com'
    cfg.oauth_client_secret = 'GOCSPX-zz'
    cfg.oauth_refresh_token = 'rt-zz'
    cfg.oauth_email = 'dono@exemplo-teste.local'
    cfg.save()

    cc = Client(); cc.force_login(chefe)
    cu = Client(); cu.force_login(comum)

    def baixar(cliente, *ids, pasta='', extra=''):
        q = '&'.join(f'id={i}' for i in ids)
        return cliente.get(f'/drive/meu-drive/zip/?{q}&pasta={pasta}{extra}')

    def abrir_zip(resposta):
        return zipfile.ZipFile(io.BytesIO(b''.join(resposta.streaming_content)))

    with mock.patch.multiple(gdrive, **FALSOS):
        print('== A PASTA EM ZIP ==')
        r = baixar(cc, 'PROJETOS', pasta='PROJETOS')
        t('a pasta baixa em zip', r.status_code == 200 and r['Content-Type'] == 'application/zip',
          (r.status_code, r.get('Content-Type')))
        t('com o nome da pasta', 'Projetos.zip' in r['Content-Disposition'], r.get('Content-Disposition'))
        nomes = sorted(abrir_zip(r).namelist())
        t('traz arquivos e subpastas', 'orcamento.pdf' in nomes and 'Fotos/loja.jpg' in nomes, nomes)
        t('o Doc do Google entra convertido para Word', 'Ata da reunião.docx' in nomes, nomes)
        t('o que não tem conversão (formulário) fica de fora', not any('Pesquisa' in n for n in nomes), nomes)

        print('\n== VÁRIOS ITENS SELECIONADOS ==')
        r = baixar(cc, 'SUB', 'A3', 'DOC', pasta='RAIZ')
        pacote = abrir_zip(r)
        nomes = sorted(pacote.namelist())
        t('pastas e arquivos juntos num zip só',
          nomes == ['Ata da reunião.docx', 'Fotos/loja.jpg', 'solto.txt'], nomes)
        t('com o conteúdo certo', pacote.read('solto.txt') == b'zz-solto'
          and pacote.read('Fotos/loja.jpg') == b'zz-loja' * 20)
        t('o nome diz quantos itens', '3 itens.zip' in r['Content-Disposition'], r.get('Content-Disposition'))
        r = baixar(cc, 'A3', 'A3', pasta='')
        t('id repetido conta uma vez só', abrir_zip(r).namelist() == ['solto.txt'])

        print('\n== O AVISO DE "PREPARANDO" ==')
        r = baixar(cc, 'A3', pasta='', extra='&t=abc123xyz')
        t('o zip devolve a marca no cookie', r.cookies.get('md_zip_pronto') is not None
          and r.cookies['md_zip_pronto'].value == 'abc123xyz')
        r = baixar(cc, 'A3', pasta='', extra='&t=<script>')
        t('marca estranha não vira cookie', 'md_zip_pronto' not in r.cookies)

        print('\n== QUANDO NÃO DÁ ==')
        r = baixar(cc, 'VAZIA', pasta='VAZIA')
        t('pasta vazia volta para a pasta com aviso',
          r.status_code == 302 and r['Location'].endswith('/drive/meu-drive/f/VAZIA/'), (r.status_code, r.get('Location')))
        msgs = [str(m) for m in cc.get(r['Location']).context['messages']]
        t('com a mensagem na tela', any('Não há arquivos' in m for m in msgs), msgs)
        with mock.patch.object(zip_copia, 'LIMITE_ARQUIVOS', 1):
            r = baixar(cc, 'PROJETOS', pasta='PROJETOS', extra='')
            r2 = cc.get('/drive/meu-drive/zip/?id=PROJETOS&id=A3', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        t('passou do limite: volta com aviso', r.status_code == 302)
        t('por AJAX vem JSON com o motivo', r2.status_code == 400 and 'limite' in r2.json()['msg'], r2.content[:150])
        r = cc.get('/drive/meu-drive/zip/?pasta=RAIZ')
        t('sem nada escolhido não quebra', r.status_code == 302)
        r = cc.get('/drive/meu-drive/zip/?id=../../etc')
        t('id inválido é recusado', r.status_code == 302)
        falso.quebrados.add('A2')
        nomes = abrir_zip(baixar(cc, 'PROJETOS')).namelist()
        t('arquivo que o Google não devolve não derruba o zip',
          'ARQUIVOS-QUE-NAO-ENTRARAM.txt' in nomes and 'orcamento.pdf' in nomes, nomes)
        falso.quebrados.clear()

        print('\n== QUEM PODE ==')
        r = baixar(cu, 'PROJETOS')
        t('quem não é SUPERADMIN não baixa', r.status_code in (302, 403), r.status_code)
        cfg.oauth_refresh_token = ''; cfg.save()
        t('sem a conta conectada, ninguém baixa', baixar(cc, 'PROJETOS').status_code in (302, 403))
        cfg.oauth_refresh_token = 'rt-zz'; cfg.save()

        print('\n== A AUDITORIA ==')
        DriveAuditLog.objects.filter(user=chefe).delete()
        baixar(cc, 'SUB', 'A3', pasta='RAIZ')
        reg = DriveAuditLog.objects.filter(user=chefe, acao='DOWNLOAD').first()
        t('o download fica registrado', reg is not None and 'Meu Drive · zip' in reg.detalhe,
          reg.detalhe if reg else None)
        t('com itens e arquivos', reg and '2 item' in reg.detalhe and '2 arquivo' in reg.detalhe,
          reg.detalhe if reg else None)

        print('\n== A TELA ==')
        html = cc.get('/drive/meu-drive/f/PROJETOS/').content.decode()
        t('a barra oferece baixar a pasta', '/drive/meu-drive/zip/?id=PROJETOS&amp;pasta=PROJETOS' in html
          and 'Baixar pasta' in html)
        t('o menu da subpasta oferece ZIP', '/drive/meu-drive/zip/?id=SUB&amp;pasta=PROJETOS' in html)
        t('o arquivo continua com o Baixar direto', '/drive/meu-drive/a/A1/conteudo/?dl=1' in html)
        t('cada item tem a caixinha de seleção', html.count('class="md-check') == 4, html.count('class="md-check'))
        t('e a barra de seleção com Baixar', 'mdBaixarSelecionados' in html and 'id="md-selecao"' in html)
        html = cc.get('/drive/meu-drive/').content.decode()
        t('na raiz não há "baixar pasta" (seria o Drive inteiro)', 'Baixar pasta' not in html)

    print('\n== O ZIP DO SETOR NÃO MUDOU ==')
    from drive import uso_local
    with mock.patch.multiple(gdrive, **FALSOS):
        m = uso_local.montar('PROJETOS')
    t('sem incluir_google, o Doc continua de fora (usar localmente / zip do setor)',
      'Ata da reunião' in m['ignorados'] and all(not a['google'] for a in m['arquivos']), m['ignorados'])
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco; nada saiu para o Google.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
