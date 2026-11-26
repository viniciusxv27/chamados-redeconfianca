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

    def lido(r):
        # Como o navegador: o zip sai em pedaços e é lido até o fim (isso devolve a vez).
        if getattr(r, 'streaming', False):
            r.zip_bytes = b''.join(r.streaming_content)
        return r

    def baixar(cliente, file_id):
        return lido(cliente.get(f'/drive/file/{file_id}/zip/'))

    def abrir_zip(resposta):
        return zipfile.ZipFile(io.BytesIO(resposta.zip_bytes))

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
        t('nem a seleção', 'class="dv-check"' not in html and 'id="dv-btn-sel"' not in html and 'id="dv-selecao"' not in html)

        print('\n== SELEÇÃO: VÁRIAS PASTAS NUM ZIP SÓ (26/11/2026) ==')
        falso.add('PROPOSTAS', 'Propostas', FOLDER, 'SETOR')
        falso.add('P1', 'proposta.pdf', 'application/pdf', 'PROPOSTAS', b'zz-proposta' * 3)
        falso.add('SOLTO', 'leia-me.txt', 'text/plain', 'SETOR', b'zz-leia')
        volta = f'/drive/s/{setor.id}/'

        def selecao(cliente, pasta, ids, **extra):
            q = [('pasta', pasta)] + [('id', i) for i in ids] + [('volta', volta)] + list(extra.items())
            return lido(cliente.get('/drive/zip/selecao/', q))

        html = c.get(f'/drive/s/{setor.id}/').content.decode()
        t('a pasta mostra a caixinha de seleção em cada item e o botão Selecionar',
          'class="dv-check" value="CONTRATOS"' in html and 'value="PROPOSTAS"' in html
          and 'dv-btn-sel' in html and 'Baixar selecionados' in html)
        r = selecao(c, 'SETOR', ['CONTRATOS', 'PROPOSTAS', 'SOLTO'], t='marca123')
        t('duas pastas e um arquivo marcados descem num zip só', r.status_code == 200
          and r['Content-Type'] == 'application/zip', (r.status_code, r.get('Content-Type')))
        nomes = sorted(abrir_zip(r).namelist())
        t('cada pasta vira uma pasta dentro do zip, o arquivo fica na raiz',
          nomes == ['Contratos/Assinados/assinado.pdf', 'Contratos/contrato.pdf', 'Contratos/foto.jpg',
                    'Propostas/proposta.pdf', 'leia-me.txt'], nomes)
        t('o nome diz de onde e quantos', 'ZZ Comercial - 3 itens.zip' in r['Content-Disposition'],
          r.get('Content-Disposition'))
        t('a tela sabe quando o download começou (cookie)', r.cookies.get('dv_zip_pronto').value == 'marca123')
        t('e fica na auditoria', DriveAuditLog.objects.filter(
            user=ana, acao='DOWNLOAD', detalhe__startswith='zip seleção · 3 item').exists())
        r = selecao(c, 'SETOR', ['PROPOSTAS'])
        t('uma pasta só abre direto no conteúdo dela', sorted(abrir_zip(r).namelist()) == ['proposta.pdf'])

        print('  -- quem pode --')
        r = selecao(cv, 'SETOR', ['CONTRATOS', 'PROPOSTAS'])
        t('sem nível de download, não baixa', r.status_code in (302, 403) and 'application/zip' not in r.get('Content-Type', ''),
          r.status_code)
        r = selecao(c, 'SETOR', ['CONTRATOS', 'FORA'])
        t('item de fora da pasta aberta (URL montada) é negado', r.status_code == 403, r.status_code)
        t('e registrado', DriveAuditLog.objects.filter(user=ana, acao='DENY',
                                                        detalhe='zip seleção fora da pasta').exists())
        r = selecao(c, 'FORA', ['A4'])
        t('pasta de outro setor não passa', r.status_code == 403, r.status_code)
        r = selecao(c, 'SETOR', ['ASSINADOS'])
        t('subpasta de outra pasta (não está na aberta) não passa', r.status_code == 403, r.status_code)
        r = selecao(cc, 'SETOR', ['CONTRATOS', 'PROPOSTAS'])
        t('o SUPERADMIN baixa', r.status_code == 200)

        print('  -- recusas voltam para a pasta com a mensagem --')
        r = selecao(c, 'SETOR', [], )
        t('nada marcado volta para a pasta', r.status_code == 302 and r['Location'] == volta, r.get('Location'))
        r = lido(c.get('/drive/zip/selecao/', [('pasta', 'SETOR'), ('id', 'CONTRATOS'), ('id', 'PROPOSTAS'),
                                               ('volta', 'https://golpe.exemplo/x')]))
        t('volta para fora do portal é ignorada', r.status_code == 200
          or (r.status_code == 302 and r['Location'].startswith('/drive/')), r.get('Location'))
        with mock.patch.object(zip_copia, 'LIMITE_ARQUIVOS', 2):
            r = selecao(c, 'SETOR', ['CONTRATOS', 'PROPOSTAS'], )
            msgs = [str(m) for m in r.wsgi_request._messages] if r.status_code == 302 else []
            t('seleção grande demais volta com a mensagem do limite', r.status_code == 302
              and any('passa do limite' in m and 'seleção' in m.lower() for m in msgs), (r.status_code, msgs))
        r = selecao(c, 'SETOR', ['VAZIA'])
        t('pasta vazia marcada avisa', r.status_code == 302)
        r = c.get('/drive/zip/selecao/', [('pasta', 'SETOR')] + [('id', f'X{i}') for i in range(zip_copia.MAX_SELECAO + 1)]
                  + [('volta', volta)])
        t(f'mais de {zip_copia.MAX_SELECAO} itens é recusado antes de ir ao Google', r.status_code == 302)
        r = c.get('/drive/zip/selecao/', [('pasta', 'SETOR'), ('id', '../etc'), ('volta', volta)])
        t('id inválido é recusado', r.status_code == 302)
        lo = zip_copia._vez
        pegas = sum(lo.acquire(timeout=5) for _ in range(zip_copia.ZIPS_AO_MESMO_TEMPO))
        t('nenhum zip anterior ficou segurando a vez', pegas == zip_copia.ZIPS_AO_MESMO_TEMPO, pegas)
        try:
            with mock.patch.object(zip_copia, 'ESPERA_PELA_VEZ', 0.05):
                r = selecao(c, 'SETOR', ['CONTRATOS', 'PROPOSTAS'])
            msgs = [str(m) for m in r.wsgi_request._messages] if r.status_code == 302 else []
            t('outro zip montando: espera a vez e, passou do tempo, pede para tentar de novo',
              r.status_code == 302 and any('Outro ZIP' in m for m in msgs), (r.status_code, msgs))
        finally:
            for _ in range(pegas):
                lo.release()

        print('\n== 5 GB E O ZIP SAINDO EM PEDAÇOS (26/11/2026) ==')
        t('o limite é de 5 GB', zip_copia.LIMITE_BYTES == 5 * 1024 ** 3, zip_copia.LIMITE_BYTES)
        t('e de 3000 arquivos', zip_copia.LIMITE_ARQUIVOS == 3000, zip_copia.LIMITE_ARQUIVOS)
        with mock.patch.object(zip_copia, 'LIMITE_BYTES', 10):
            r = selecao(c, 'SETOR', ['CONTRATOS', 'PROPOSTAS'])
            msgs = [str(m) for m in r.wsgi_request._messages] if r.status_code == 302 else []
        t('a mensagem do limite fala em GB quando passa de 1 GB', zip_copia._tamanho_h(5 * 1024 ** 3) == '5 GB'
          and zip_copia._tamanho_h(500 * 1024 ** 2) == '500 MB', zip_copia._tamanho_h(5 * 1024 ** 3))
        grande = dict(falso.itens['A1'], size=str(4 * 1024 ** 3))
        with mock.patch.dict(falso.itens, {'A1': grande}):
            r = baixar(c, 'CONTRATOS')
        t('pasta de 4 GB passa do limite antigo e baixa', r.status_code == 200
          and sorted(abrir_zip(r).namelist()) == ['Assinados/assinado.pdf', 'contrato.pdf', 'foto.jpg'],
          (r.status_code, r.content[:120] if not r.streaming else ''))
        grande = dict(falso.itens['A1'], size=str(6 * 1024 ** 3))
        with mock.patch.dict(falso.itens, {'A1': grande}):
            r = baixar(c, 'CONTRATOS')
        t('passando de 5 GB, recusa e diz o limite', r.status_code == 400 and '5 GB' in r.json()['msg'],
          r.content[:200])

        r = baixar(c, 'CONTRATOS')
        t('o download começa sem esperar o zip ficar pronto (sai em pedaços, sem tamanho)',
          r.streaming and 'Content-Length' not in r and r['Content-Type'] == 'application/zip')
        t('e o proxy é avisado para não segurar', r.get('X-Accel-Buffering') == 'no')

        # O navegador desiste no meio: o servidor fecha a resposta, que fecha a transmissão.
        transmissao, nome_zip, _resumo = zip_copia.preparar_zip(['CONTRATOS'])
        resposta = zip_copia.resposta_zip(transmissao, nome_zip)
        t('a resposta fecha a transmissão quando o servidor a fecha',
          transmissao.close in resposta._resource_closers)
        livres = zip_copia._vez._value
        t('enquanto sai, o zip segura uma vez', livres == zip_copia.ZIPS_AO_MESMO_TEMPO - 1, livres)
        primeiro = next(iter(transmissao))
        t('o primeiro pedaço já é o começo do zip', primeiro[:2] == b'PK', primeiro[:4])
        transmissao.close()
        t('desistiu no meio: a vez volta', zip_copia._vez._value == zip_copia.ZIPS_AO_MESMO_TEMPO,
          zip_copia._vez._value)
        transmissao.close()
        t('fechar de novo não devolve a vez duas vezes', zip_copia._vez._value == zip_copia.ZIPS_AO_MESMO_TEMPO)
        r = baixar(c, 'CONTRATOS')
        t('e o próximo zip baixa normalmente', r.status_code == 200 and abrir_zip(r).testzip() is None)
        t('lido até o fim, a vez também volta', zip_copia._vez._value == zip_copia.ZIPS_AO_MESMO_TEMPO)

        with mock.patch.object(zip_copia, 'ZIP64_A_PARTIR_DE', 1):
            r = baixar(c, 'CONTRATOS')
        pacote = abrir_zip(r)
        t('arquivo grande entra em ZIP64 e o zip abre certo', pacote.testzip() is None
          and pacote.read('contrato.pdf') == b'zz-contrato' * 10, pacote.namelist())

        print('  -- pasta liberada --')
        from drive.models import PastaLiberada
        try:
            liberada = PastaLiberada.objects.create(folder_id='PROPOSTAS', folder_name='Propostas', alvo='USER',
                                                    target_user=so_ver, nivel='DOWNLOAD')
        except Exception as exc:  # noqa: BLE001
            liberada = None
            print('   (pasta liberada não criada:', exc, ')')
        if liberada:
            falso.add('P2', 'outra.pdf', 'application/pdf', 'PROPOSTAS', b'zz-outra')
            r = selecao(cv, 'PROPOSTAS', ['P1', 'P2'])
            t('na pasta liberada também (pelo nível dela)', r.status_code == 200
              and sorted(abrir_zip(r).namelist()) == ['outra.pdf', 'proposta.pdf'], r.status_code)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco; nada saiu para o Google.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
