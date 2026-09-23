"""Drive: botão direito num arquivo/pasta libera o acesso para UMA pessoa.

Pedido: "em /drive — na parte de pastas e arquivos, devo poder clicar com o
botão direito naquela pasta ou arquivo especifico e permitir o acesso por um
usuário especifico".

- o menu do item (botão direito, "⋯" ou toque longo) traz "Dar acesso a uma
  pessoa" só para quem pode ADMINISTRAR ali — SUPERADMIN, gestor do setor ou
  quem recebeu ADMINISTRAR naquela pasta; para os outros o item não aparece e a
  API responde 403 (o mesmo "sem acesso" das outras ações do módulo);
- liberar uma PASTA dá acesso a ela e a tudo abaixo — e a mais nada do setor;
- liberar um ARQUIVO dá acesso só àquele arquivo, nem à pasta que o contém;
- a janela lista quem já foi liberado NAQUELE item (as regras do setor inteiro
  ficam de fora) e tira a liberação;
- pessoa que não existe/inativa, nível inventado e item de fora do setor são
  recusados; liberar de novo troca o nível em vez de duplicar a regra.

Roda numa transação desfeita no fim. Não fala com o Google (Drive falso em
memória) e todos os caches ficam em memória — nada no Redis compartilhado.
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

# Nada deste teste vai para o Redis compartilhado: todos os caches em memória.
settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-drive-perm'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-drive-perm-local'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()            # o Client guarda o contexto das telas
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from drive import gdrive
from drive import permissions as perms
from drive.models import DriveAuditLog, DriveConfig, DrivePermission, SectorDriveMapping
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
    """Árvore em memória com a forma de resposta do Google."""

    def __init__(self):
        self.itens = {}

    def add(self, id_, nome, mime, pai):
        self.itens[id_] = dict(
            id=id_, name=nome, mimeType=mime, parents=[pai] if pai else [], trashed=False,
            size='1024', modifiedTime='2026-09-10T10:00:00Z', createdTime='2026-09-01T10:00:00Z',
            webViewLink=f'https://drive.google.com/file/d/{id_}/view')
        return self.itens[id_]

    def obter(self, file_id, fields=None):
        if file_id not in self.itens:
            raise gdrive.DriveError('Google Drive respondeu 404: File not found')
        return dict(self.itens[file_id])

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
        return [(i, self.itens[i]['name']) for i in reversed(self.ancestrais(file_id)) if i in self.itens]

    def listar(self, folder_id, page_token=None, page_size=100, trashed=False, apenas_pastas=False, order=''):
        filhos = [dict(i) for i in self.itens.values()
                  if folder_id in i['parents'] and i['trashed'] == trashed]
        filhos.sort(key=lambda i: (i['mimeType'] != FOLDER, i['name']))
        return filhos, None

    def pai_de(self, file_id):
        return (self.itens.get(file_id, {}).get('parents') or [None])[0]

    def baixar(self, file_id, preview=False):
        meta = self.obter(file_id)
        return io.BytesIO(b'%PDF-zz'), meta['name'], meta['mimeType']


falso = DriveFalso()
falso.add('RAIZ_GOOGLE', 'Meu Drive', FOLDER, None)
falso.add('SETOR_RAIZ', 'ZZ Raiz do setor', FOLDER, 'RAIZ_GOOGLE')
falso.add('PASTA_RH', 'ZZ RH', FOLDER, 'SETOR_RAIZ')
falso.add('SUB_RH', 'ZZ RH 2026', FOLDER, 'PASTA_RH')
falso.add('ARQ_RH', 'ZZ folha.pdf', 'application/pdf', 'PASTA_RH')
falso.add('ARQ_SUB', 'ZZ ficha.pdf', 'application/pdf', 'SUB_RH')
falso.add('PASTA_FIN', 'ZZ Financeiro', FOLDER, 'SETOR_RAIZ')
falso.add('ARQ_FIN', 'ZZ balanco.pdf', 'application/pdf', 'PASTA_FIN')
falso.add('ARQ_FORA', 'ZZ de fora.pdf', 'application/pdf', 'RAIZ_GOOGLE')

FALSOS = {n: getattr(falso, n) for n in
          ('obter', 'ancestrais', 'dentro_de', 'caminho', 'listar', 'pai_de', 'baixar')}

MENU = 'Dar acesso a uma pessoa'
marcador = transaction.atomic()
marcador.__enter__()
try:
    def novo(username, hierarquia='PADRAO'):
        return User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local', password='S3nha!teste',
            first_name='ZZ', last_name=username.split('.')[1].title(), hierarchy=hierarquia)

    chefe = novo('zzpi.chefe', 'SUPERADMIN')
    chefe.is_superuser = chefe.is_staff = True
    chefe.save()
    gestor = novo('zzpi.gestor')
    dono_rh = novo('zzpi.donorh')       # ADMINISTRAR só na pasta do RH
    leitor = novo('zzpi.leitor')        # VER no setor inteiro, mas não libera ninguém
    forasteiro = novo('zzpi.forasteiro')
    alvo = novo('zzpi.alvo')            # quem recebe o acesso à pasta
    alvo_arq = novo('zzpi.alvoarq')     # quem recebe o acesso a um arquivo só
    desligado = novo('zzpi.desligado')
    desligado.is_active = False
    desligado.save()

    cfg = DriveConfig.get()
    cfg.ativo = True
    cfg.save()

    setor = Sector.objects.create(name='ZZ Setor Permissão por Item')
    outro = Sector.objects.create(name='ZZ Setor Vizinho')
    mapa = SectorDriveMapping.objects.create(sector=setor, folder_id='SETOR_RAIZ', folder_name='ZZ Raiz do setor')
    mapa_vizinho = SectorDriveMapping.objects.create(sector=outro, folder_id='OUTRA_RAIZ', folder_name='ZZ Vizinho')
    mapa.managers.add(gestor)
    DrivePermission.objects.create(mapping=mapa, alvo='USER', target_user=leitor, nivel='VIEW')
    DrivePermission.objects.create(mapping=mapa, alvo='USER', target_user=dono_rh, nivel='ADMIN',
                                   folder_id='PASTA_RH', folder_name='ZZ RH')

    def cliente(u):
        c = Client()
        c.force_login(u)
        return c

    c_chefe, c_gestor, c_rh = cliente(chefe), cliente(gestor), cliente(dono_rh)
    c_leitor, c_fora = cliente(leitor), cliente(forasteiro)
    c_alvo, c_alvo_arq = cliente(alvo), cliente(alvo_arq)

    URL_ACESSO = '/drive/file/{}/acesso/'
    URL_REMOVER = '/drive/file/{}/acesso/remover/'
    AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}

    with mock.patch.multiple(gdrive, **FALSOS):

        print('== A OPÇÃO SÓ APARECE PARA QUEM PODE LIBERAR ==')
        r = c_chefe.get(f'/drive/s/{setor.id}/')
        t('o SUPERADMIN abre o setor', r.status_code == 200, r.status_code)
        t('e a listagem oferece "Dar acesso a uma pessoa"',
          r.context['pode_gerir_acesso'] is True and MENU in r.content.decode())
        t('com a janela da liberação na tela', 'dv-modal-acesso' in r.content.decode())
        t('e o menu abre no botão direito do item', 'contextmenu' in r.content.decode()
          and 'dv-item' in r.content.decode())
        t('cada item leva o id e o nome para o menu',
          'data-id="PASTA_RH"' in r.content.decode() and 'data-nome="ZZ RH"' in r.content.decode())

        r = c_gestor.get(f'/drive/s/{setor.id}/')
        t('o gestor do setor também vê a opção', r.context['pode_gerir_acesso'] is True and MENU in r.content.decode())

        r = c_leitor.get(f'/drive/s/{setor.id}/')
        t('quem só VÊ o setor não recebe a opção',
          r.status_code == 200 and r.context['pode_gerir_acesso'] is False and MENU not in r.content.decode())
        t('nem a janela da liberação vai na página', 'dv-modal-acesso' not in r.content.decode())

        r = c_rh.get(f'/drive/s/{setor.id}/f/PASTA_RH/')
        t('quem tem ADMINISTRAR na pasta vê a opção lá dentro',
          r.status_code == 200 and r.context['pode_gerir_acesso'] is True and MENU in r.content.decode())

        print('\n== A API RECUSA QUEM NÃO PODE ==')
        t('SUPERADMIN: a janela abre', c_chefe.get(URL_ACESSO.format('PASTA_RH'), **AJAX).status_code == 200)
        t('gestor do setor: abre', c_gestor.get(URL_ACESSO.format('PASTA_RH'), **AJAX).status_code == 200)
        t('ADMINISTRAR na pasta: abre ali', c_rh.get(URL_ACESSO.format('PASTA_RH'), **AJAX).status_code == 200)
        t('e também no que está dentro dela', c_rh.get(URL_ACESSO.format('ARQ_SUB'), **AJAX).status_code == 200)
        t('mas NÃO na pasta do lado', c_rh.get(URL_ACESSO.format('PASTA_FIN'), **AJAX).status_code == 403,
          c_rh.get(URL_ACESSO.format('PASTA_FIN'), **AJAX).status_code)
        t('quem só VÊ: 403', c_leitor.get(URL_ACESSO.format('PASTA_RH'), **AJAX).status_code == 403)
        t('quem não tem nada no setor: 403', c_fora.get(URL_ACESSO.format('PASTA_RH'), **AJAX).status_code == 403)
        t('item de fora do setor: 403 mesmo para o SUPERADMIN',
          c_chefe.get(URL_ACESSO.format('ARQ_FORA'), **AJAX).status_code == 403)
        t('id que não existe: 403', c_chefe.get(URL_ACESSO.format('NAO_EXISTE'), **AJAX).status_code == 403)

        antes = DriveAuditLog.objects.filter(acao='DENY').count()
        r = c_leitor.post(URL_ACESSO.format('PASTA_RH'), {'usuario': alvo.id, 'nivel': 'VIEW'}, **AJAX)
        t('liberar sem poder: 403 e nada criado',
          r.status_code == 403 and not DrivePermission.objects.filter(target_user=alvo).exists())
        t('e a recusa fica na auditoria (DENY)',
          DriveAuditLog.objects.filter(acao='DENY').count() == antes + 1)

        print('\n== A JANELA: QUEM JÁ TEM ACESSO E PARA QUEM DÁ PARA LIBERAR ==')
        d = c_chefe.get(URL_ACESSO.format('PASTA_RH'), **AJAX).json()
        t('diz o nome do item e o setor', d['nome'] == 'ZZ RH' and d['setor'] == setor.name, d)
        t('e que é uma pasta', d['pasta'] is True)
        t('traz os níveis, começando por Visualizar',
          d['niveis'][0] == {'valor': 'VIEW', 'rotulo': 'Visualizar'}, d['niveis'][:1])
        nomes = [p['nome'] for p in d['pessoas']]
        t('lista as pessoas ativas para escolher', alvo.full_name in nomes and leitor.full_name in nomes)
        t('e não oferece quem está desligado', desligado.full_name not in nomes)
        t('a lista já traz a única liberação que existe neste item (o ADMINISTRAR do dono do RH)',
          [a['nome'] for a in d['acessos']] == [dono_rh.full_name], d['acessos'])
        d_arq = c_chefe.get(URL_ACESSO.format('ARQ_FIN'), **AJAX).json()
        t('para um arquivo a janela também abre', d_arq['nome'] == 'ZZ balanco.pdf' and d_arq['pasta'] is False)

        print('\n== LIBERAR UMA PASTA ==')
        r = c_chefe.post(URL_ACESSO.format('PASTA_RH'), {'usuario': alvo.id, 'nivel': 'VIEW'}, **AJAX)
        corpo = r.json()
        t('a liberação responde OK', r.status_code == 200 and corpo['ok'] is True, corpo)
        t('com a frase do que aconteceu', 'ZZ RH' in corpo['msg'] and alvo.full_name in corpo['msg'], corpo['msg'])
        p = DrivePermission.objects.filter(target_user=alvo).first()
        t('a regra nasce naquele item', p and p.folder_id == 'PASTA_RH' and p.folder_name == 'ZZ RH', p)
        t('no setor da listagem, para aquele usuário, em Visualizar',
          p.mapping_id == mapa.id and p.alvo == 'USER' and p.nivel == 'VIEW')
        t('e com quem liberou registrado', p.criado_por_id == chefe.id)
        t('a janela já devolve a lista atualizada, com a mais nova em cima',
          [a['nome'] for a in corpo['acessos']] == [alvo.full_name, dono_rh.full_name], corpo['acessos'])
        t('com o nível e quem liberou',
          corpo['acessos'][0]['nivel'] == 'Visualizar' and corpo['acessos'][0]['por'] == chefe.full_name)
        log = DriveAuditLog.objects.filter(acao='PERM', file_id='PASTA_RH').order_by('-criado_em').first()
        t('e a auditoria registra a permissão (PERM)',
          log and log.user_id == chefe.id and alvo.full_name in log.detalhe and log.sector_id == setor.id, log)

        print('\n== O QUE A PESSOA PASSA A ENXERGAR (E O QUE NÃO) ==')
        t('a pasta liberada abre', perms.level_for_folder(alvo, mapa, 'PASTA_RH') >= perms.ORDEM['VIEW'])
        t('o arquivo dentro dela também', perms.file_allowed(alvo, 'ARQ_RH')[1] >= perms.ORDEM['VIEW'])
        t('e o que está numa subpasta', perms.file_allowed(alvo, 'ARQ_SUB')[1] >= perms.ORDEM['VIEW'])
        t('o resto do setor continua fechado', perms.file_allowed(alvo, 'ARQ_FIN') == (None, 0),
          perms.file_allowed(alvo, 'ARQ_FIN'))
        t('e a raiz do setor não abre sozinha', perms.level_for_folder(alvo, mapa, None) == 0)
        t('pela tela: a pasta liberada abre',
          c_alvo.get(f'/drive/s/{setor.id}/f/PASTA_RH/').status_code == 200)
        t('e mostra o que tem dentro',
          'ZZ folha.pdf' in c_alvo.get(f'/drive/s/{setor.id}/f/PASTA_RH/').content.decode())
        t('a subpasta também', c_alvo.get(f'/drive/s/{setor.id}/f/SUB_RH/').status_code == 200)
        t('o arquivo de dentro abre', c_alvo.get('/drive/file/ARQ_RH/').status_code == 200)
        t('a pasta do lado, não', c_alvo.get(f'/drive/s/{setor.id}/f/PASTA_FIN/').status_code == 403)
        t('nem o arquivo dela', c_alvo.get('/drive/file/ARQ_FIN/').status_code == 403)
        t('e quem recebeu o acesso não pode repassá-lo',
          c_alvo.get(URL_ACESSO.format('PASTA_RH'), **AJAX).status_code == 403)

        print('\n== LIBERAR UM ARQUIVO SÓ ==')
        r = c_chefe.post(URL_ACESSO.format('ARQ_FIN'), {'usuario': alvo_arq.id, 'nivel': 'DOWNLOAD'}, **AJAX)
        t('a liberação responde OK', r.status_code == 200 and r.json()['ok'] is True, r.json())
        p_arq = DrivePermission.objects.filter(target_user=alvo_arq).first()
        t('a regra fica no id do arquivo',
          p_arq and p_arq.folder_id == 'ARQ_FIN' and p_arq.nivel == 'DOWNLOAD', p_arq)
        t('o arquivo abre para ela', perms.file_allowed(alvo_arq, 'ARQ_FIN')[1] == perms.ORDEM['DOWNLOAD'])
        t('mas a pasta que o contém, não', perms.level_for_folder(alvo_arq, mapa, 'PASTA_FIN') == 0)
        t('nem qualquer outro arquivo do setor', perms.file_allowed(alvo_arq, 'ARQ_RH') == (None, 0))
        t('pela tela: o arquivo abre', c_alvo_arq.get('/drive/file/ARQ_FIN/').status_code == 200)
        t('e baixa, que foi o nível liberado',
          c_alvo_arq.get('/drive/file/ARQ_FIN/content/?dl=1').status_code == 200)
        t('a pasta que o contém continua fechada na tela',
          c_alvo_arq.get(f'/drive/s/{setor.id}/f/PASTA_FIN/').status_code == 403)
        t('outro arquivo do setor continua fechado', c_alvo_arq.get('/drive/file/ARQ_RH/').status_code == 403)

        print('\n== A LISTA MOSTRA SÓ AS LIBERAÇÕES DAQUELE ITEM ==')
        d = c_chefe.get(URL_ACESSO.format('PASTA_RH'), **AJAX).json()
        t('a pasta mostra quem foi liberado nela',
          {a['nome'] for a in d['acessos']} == {alvo.full_name, dono_rh.full_name}, d['acessos'])
        t('e não quem já enxergava o setor inteiro', leitor.full_name not in [a['nome'] for a in d['acessos']])
        t('nem quem foi liberado em outro item', alvo_arq.full_name not in [a['nome'] for a in d['acessos']])
        d = c_chefe.get(URL_ACESSO.format('ARQ_FIN'), **AJAX).json()
        t('o arquivo mostra a liberação dele', [a['nome'] for a in d['acessos']] == [alvo_arq.full_name])

        print('\n== LIBERAR DE NOVO TROCA O NÍVEL, NÃO DUPLICA ==')
        r = c_chefe.post(URL_ACESSO.format('PASTA_RH'), {'usuario': alvo.id, 'nivel': 'EDIT'}, **AJAX)
        t('responde OK', r.status_code == 200 and r.json()['ok'] is True)
        t('continua uma regra só',
          DrivePermission.objects.filter(mapping=mapa, folder_id='PASTA_RH', target_user=alvo).count() == 1)
        t('com o nível novo', DrivePermission.objects.get(target_user=alvo, folder_id='PASTA_RH').nivel == 'EDIT')
        t('e a pessoa passa a poder editar lá', perms.level_for_folder(alvo, mapa, 'PASTA_RH') == perms.ORDEM['EDIT'])

        print('\n== O QUE É RECUSADO ==')
        casos = [
            ('sem escolher ninguém', {'nivel': 'VIEW'}, 'pessoa'),
            ('usuário que não é número', {'usuario': 'abc', 'nivel': 'VIEW'}, 'pessoa'),
            ('usuário que não existe', {'usuario': 999999, 'nivel': 'VIEW'}, 'pessoa'),
            ('usuário desligado', {'usuario': desligado.id, 'nivel': 'VIEW'}, 'pessoa'),
            ('nível inventado', {'usuario': alvo_arq.id, 'nivel': 'DONO'}, 'Nível'),
        ]
        quantas = DrivePermission.objects.count()
        for nome, dados, trecho in casos:
            r = c_chefe.post(URL_ACESSO.format('SUB_RH'), dados, **AJAX)
            t(f'{nome}: 400 com a explicação',
              r.status_code == 400 and trecho in r.json()['msg'], f'{r.status_code} {r.content[:120]}')
        t('e nenhuma regra foi criada por engano', DrivePermission.objects.count() == quantas)
        t('liberar num item de fora do setor: 403',
          c_chefe.post(URL_ACESSO.format('ARQ_FORA'), {'usuario': alvo.id, 'nivel': 'VIEW'}, **AJAX).status_code == 403)

        print('\n== TIRAR A LIBERAÇÃO ==')
        pk_pasta = DrivePermission.objects.get(target_user=alvo, folder_id='PASTA_RH').id
        pk_arq = DrivePermission.objects.get(target_user=alvo_arq, folder_id='ARQ_FIN').id
        r = c_chefe.post(URL_REMOVER.format('PASTA_RH'), {'permissao': pk_arq}, **AJAX)
        t('a liberação de OUTRO item não some por aqui: 400',
          r.status_code == 400 and DrivePermission.objects.filter(pk=pk_arq).exists(), r.status_code)
        r = c_chefe.post(URL_REMOVER.format('PASTA_RH'), {'permissao': 'abc'}, **AJAX)
        t('pk que não é número: 400', r.status_code == 400)
        r = c_leitor.post(URL_REMOVER.format('PASTA_RH'), {'permissao': pk_pasta}, **AJAX)
        t('quem não pode administrar nem tenta: 403',
          r.status_code == 403 and DrivePermission.objects.filter(pk=pk_pasta).exists())

        r = c_chefe.post(URL_REMOVER.format('PASTA_RH'), {'permissao': pk_pasta}, **AJAX)
        corpo = r.json()
        t('quem pode: responde OK', r.status_code == 200 and corpo['ok'] is True, corpo)
        t('a regra sai do banco', not DrivePermission.objects.filter(pk=pk_pasta).exists())
        t('e a lista fica só com quem não foi tirado',
          [a['nome'] for a in corpo['acessos']] == [dono_rh.full_name], corpo['acessos'])
        t('a pessoa perde o acesso à pasta', perms.level_for_folder(alvo, mapa, 'PASTA_RH') == 0)
        t('e ao que estava dentro', perms.file_allowed(alvo, 'ARQ_SUB') == (None, 0))
        t('pela tela: 403 de novo', c_alvo.get(f'/drive/s/{setor.id}/f/PASTA_RH/').status_code == 403)
        t('e o arquivo também', c_alvo.get('/drive/file/ARQ_RH/').status_code == 403)
        log = DriveAuditLog.objects.filter(acao='PERM', file_id='PASTA_RH').order_by('-criado_em').first()
        t('a retirada fica na auditoria', log and 'retirado' in log.detalhe, log)

        t('a liberação do arquivo continua de pé (uma não mexe na outra)',
          perms.file_allowed(alvo_arq, 'ARQ_FIN')[1] == perms.ORDEM['DOWNLOAD'])
        r = c_chefe.post(URL_REMOVER.format('ARQ_FIN'), {'permissao': pk_arq}, **AJAX)
        t('e sai quando é pedida no item dela',
          r.status_code == 200 and perms.file_allowed(alvo_arq, 'ARQ_FIN') == (None, 0))
        t('as regras do setor inteiro ficaram intactas',
          DrivePermission.objects.filter(mapping=mapa, folder_id='', target_user=leitor).count() == 1)
        t('e as de outros setores também', SectorDriveMapping.objects.filter(pk=mapa_vizinho.pk).exists())
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
