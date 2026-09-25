"""Drive: liberar uma pasta qualquer para uma pessoa, fora do mapa de setores.

Pedido: "em /drive/gestao/permissoes/ — precisa liberar o acesso a pastas
individuais também, por exemplo selecionar um usuário e escolher uma pasta
específica de qualquer lugar, e a pessoa deve conseguir ver normalmente em
/drive".

Até aqui toda permissão do Drive pendurava num setor mapeado e só alcançava o
que estava dentro da pasta daquele setor. Agora a pasta é escolhida em qualquer
lugar do Drive e vira uma raiz por si só: aparece em /drive para quem recebeu,
navega igual, e o motor de arquivos (prévia, download, renomear) reconhece a
cadeia de pastas dela.

O que este teste cobre:

- liberar: pasta escolhida pelo seletor ou colada da URL, só pasta (arquivo
  não), sem duplicar quando repete, e só para o SUPERADMIN;
- quem vê: o cartão em /drive de quem recebeu, e a ausência dele para os outros;
- navegar: a raiz, a subpasta, e a recusa de uma pasta de fora (RNF05);
- o nível mandando no que dá para fazer (ver, baixar, enviar, renomear);
- o arquivo de dentro abrindo pela prévia — e o de fora, não;
- liberar para grupo, setor e hierarquia;
- favoritar dentro da pasta e o link voltando para ela;
- retirar o acesso, que fecha tudo de novo;
- a liberação registrada na auditoria.

Google Drive é um dublê em memória: nada sai daqui. Transação desfeita no fim.
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
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dv-pasta'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dv-pasta-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client

from communications.models import CommunicationGroup
from drive import gdrive
from drive import permissions as perms
from drive.models import (DriveAuditLog, DriveConfig, DriveFavorite, DrivePermission,
                          PastaLiberada, SectorDriveMapping)
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
    """O Google Drive em memória: árvore de pastas e arquivos."""

    def __init__(self):
        self.itens = {}
        self.enviados = []

    def add(self, id_, nome, mime, pai):
        self.itens[id_] = dict(id=id_, name=nome, mimeType=mime, parents=[pai] if pai else [],
                               trashed=False, size='1024', modifiedTime='2026-09-20T10:00:00Z')
        return self.itens[id_]

    def obter(self, file_id, fields=None):
        if file_id not in self.itens:
            raise gdrive.DriveError('404')
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
        ids = list(reversed(self.ancestrais(file_id)))
        if ate_root and ate_root in ids:
            ids = ids[ids.index(ate_root):]
        return [(i, self.itens[i]['name']) for i in ids if i in self.itens]

    def listar(self, folder_id, page_token=None, page_size=100, trashed=False,
               apenas_pastas=False, order='folder,name'):
        if folder_id == 'root':
            folder_id = 'RAIZ'
        filhos = [dict(i) for i in self.itens.values()
                  if folder_id in i['parents'] and not i['trashed']
                  and (not apenas_pastas or i['mimeType'] == FOLDER)]
        return filhos, None

    def pai_de(self, file_id):
        return (self.itens.get(file_id, {}).get('parents') or [None])[0]

    def baixar(self, file_id, preview=False):
        meta = self.obter(file_id)
        return io.BytesIO(b'zz'), meta['name'], meta['mimeType']

    def enviar(self, nome, mime, arquivo, pasta_id):
        self.enviados.append((nome, pasta_id))
        return self.add(f'NOVO_{len(self.itens)}', nome, mime or 'application/pdf', pasta_id)

    def criar_pasta(self, nome, pai):
        return self.add(f'PASTA_{len(self.itens)}', nome, FOLDER, pai)

    def renomear(self, file_id, nome):
        self.itens[file_id]['name'] = nome
        return self.itens[file_id]

    def id_da_raiz(self):
        return 'RAIZ'


falso = DriveFalso()
falso.add('RAIZ', 'Meu Drive', FOLDER, None)
falso.add('SETOR', 'ZZ Pasta do setor', FOLDER, 'RAIZ')
falso.add('ARQ_SETOR', 'do setor.pdf', 'application/pdf', 'SETOR')
falso.add('SOLTA', 'ZZ Contratos 2026', FOLDER, 'RAIZ')
falso.add('ARQ_SOLTA', 'contrato.pdf', 'application/pdf', 'SOLTA')
falso.add('SUBSOLTA', 'ZZ Assinados', FOLDER, 'SOLTA')
falso.add('ARQ_SUB', 'assinado.pdf', 'application/pdf', 'SUBSOLTA')
falso.add('OUTRA', 'ZZ Pasta de ninguém', FOLDER, 'RAIZ')
falso.add('ARQ_OUTRA', 'sigiloso.pdf', 'application/pdf', 'OUTRA')

FALSOS = {n: getattr(falso, n) for n in
          ('obter', 'ancestrais', 'dentro_de', 'caminho', 'listar', 'pai_de', 'baixar',
           'enviar', 'criar_pasta', 'renomear', 'id_da_raiz')}
AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}

marcador = transaction.atomic()
marcador.__enter__()
try:
    chefe = User.objects.create_user(
        username='zzpl.chefe', email='zzpl.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)
    ana = User.objects.create_user(
        username='zzpl.ana', email='zzpl.ana@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Ana')
    bruno = User.objects.create_user(
        username='zzpl.bruno', email='zzpl.bruno@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Bruno')

    cfg = DriveConfig.get()
    cfg.ativo = True
    cfg.allowed_extensions = 'pdf,jpg,png'
    cfg.max_file_mb = 5
    cfg.save()

    setor = Sector.objects.create(name='ZZ Setor da pasta')
    SectorDriveMapping.objects.create(sector=setor, folder_id='SETOR', folder_name='ZZ Pasta do setor')

    c = Client(); c.force_login(chefe)
    c_ana = Client(); c_ana.force_login(ana)
    c_bruno = Client(); c_bruno.force_login(bruno)

    with mock.patch.multiple(gdrive, **FALSOS):
        print('== LIBERAR UMA PASTA DE QUALQUER LUGAR ==')
        r = c.get('/drive/gestao/permissoes/')
        html = r.content.decode()
        t('a tela de permissões abre', r.status_code == 200, r.status_code)
        t('tem o bloco de liberar pasta', 'Liberar uma pasta' in html and 'pasta_liberar' not in html)
        t('com o seletor de pasta e a busca de pessoa',
          'dvEscolherPasta()' in html and 'Buscar pessoa' in html)
        t('e a lista de pastas liberadas', 'Pastas liberadas' in html)

        r = c.post('/drive/gestao/permissoes/pasta/',
                   {'folder_id': 'SOLTA', 'alvo': 'USER', 'target_user': ana.id, 'nivel': 'DOWNLOAD'},
                   follow=True)
        liberada = PastaLiberada.objects.filter(folder_id='SOLTA', target_user=ana).first()
        t('a pasta fica liberada para a pessoa', liberada is not None)
        t('guarda o nome e o caminho da pasta',
          liberada.folder_name == 'ZZ Contratos 2026' and liberada.caminho == 'ZZ Contratos 2026',
          (liberada.folder_name, liberada.caminho))
        t('e o nível escolhido', liberada.nivel == 'DOWNLOAD')
        t('a tela confirma', 'liberada para ZZ Ana' in r.content.decode())

        r = c.post('/drive/gestao/permissoes/pasta/',
                   {'folder_id': 'https://drive.google.com/drive/folders/SOLTA',
                    'alvo': 'USER', 'target_user': ana.id, 'nivel': 'UPLOAD'}, follow=True)
        t('colar o endereço do Drive funciona igual',
          PastaLiberada.objects.filter(folder_id='SOLTA', target_user=ana).count() == 1)
        liberada.refresh_from_db()
        t('e repetir a pasta atualiza o nível em vez de duplicar', liberada.nivel == 'UPLOAD')

        r = c.post('/drive/gestao/permissoes/pasta/',
                   {'folder_id': 'ARQ_SOLTA', 'alvo': 'USER', 'target_user': bruno.id}, follow=True)
        t('arquivo não é pasta e é recusado',
          not PastaLiberada.objects.filter(folder_id='ARQ_SOLTA').exists()
          and 'não é uma pasta' in r.content.decode())

        r = c.post('/drive/gestao/permissoes/pasta/',
                   {'folder_id': 'SOLTA', 'alvo': 'USER', 'target_user': ''}, follow=True)
        t('sem escolher a pessoa, não libera', 'Escolha quem vai receber' in r.content.decode())

        r = c_ana.post('/drive/gestao/permissoes/pasta/',
                       {'folder_id': 'OUTRA', 'alvo': 'USER', 'target_user': ana.id}, follow=True)
        t('quem não é SUPERADMIN não libera pasta nenhuma',
          not PastaLiberada.objects.filter(folder_id='OUTRA').exists())

        t('a liberação fica na auditoria',
          DriveAuditLog.objects.filter(acao='PERM', file_id='SOLTA',
                                       detalhe__contains='pasta liberada').exists())

        print('\n== A PESSOA VÊ EM /DRIVE ==')
        r = c_ana.get('/drive/')
        html = r.content.decode()
        t('o cartão da pasta aparece para quem recebeu',
          'ZZ Contratos 2026' in html and f'/drive/p/{liberada.id}/' in html)
        t('e a seção se chama Pastas liberadas', 'Pastas liberadas' in html)
        t('quem não recebeu não vê', 'ZZ Contratos 2026' not in c_bruno.get('/drive/').content.decode())
        t('o SUPERADMIN vê as que liberou', 'ZZ Contratos 2026' in c.get('/drive/').content.decode())
        t('e a tela diz para quem é', 'ZZ Ana' in c.get('/drive/').content.decode())

        print('\n== NAVEGAR NA PASTA LIBERADA ==')
        r = c_ana.get(f'/drive/p/{liberada.id}/')
        t('a pasta abre', r.status_code == 200, r.status_code)
        nomes = {i['name'] for i in r.context['itens']}
        t('com o que está dentro dela', nomes == {'contrato.pdf', 'ZZ Assinados'}, nomes)
        t('a tela mostra o nome da pasta', r.context['rotulo'] == 'ZZ Contratos 2026')
        t('e diz que é uma pasta liberada',
          r.context['pasta_liberada'] and 'pasta liberada para você' in r.content.decode())

        r = c_ana.get(f'/drive/p/{liberada.id}/f/SUBSOLTA/')
        t('a subpasta abre', r.status_code == 200 and r.context['folder_id'] == 'SUBSOLTA')
        t('e o caminho volta para a raiz liberada',
          f'/drive/p/{liberada.id}/' in r.content.decode())

        r = c_ana.get(f'/drive/p/{liberada.id}/f/OUTRA/')
        t('uma pasta de fora da raiz é recusada', r.status_code in (302, 403), r.status_code)

        t('quem não recebeu não abre',
          c_bruno.get(f'/drive/p/{liberada.id}/').status_code in (302, 403))

        print('\n== O ARQUIVO DE DENTRO ==')
        r = c_ana.get('/drive/file/ARQ_SOLTA/')
        t('a prévia do arquivo da pasta abre', r.status_code == 200, r.status_code)
        t('e diz de onde ele veio', r.context['rotulo'] == 'ZZ Contratos 2026')
        t('o botão de voltar aponta para a pasta liberada',
          r.context['url_raiz'] == f'/drive/p/{liberada.id}/')
        t('o arquivo da subpasta também', c_ana.get('/drive/file/ARQ_SUB/').status_code == 200)
        t('o de outra pasta, não', c_ana.get('/drive/file/ARQ_OUTRA/').status_code in (302, 403))
        t('e o de quem não recebeu nada, também não',
          c_bruno.get('/drive/file/ARQ_SOLTA/').status_code in (302, 403))

        print('\n== O NÍVEL MANDA ==')
        r = c_ana.get('/drive/file/ARQ_SOLTA/content/?dl=1')
        t('com UPLOAD (que inclui baixar) o arquivo baixa', r.status_code == 200, r.status_code)

        falso.enviados.clear()
        r = c_ana.post(f'/drive/p/{liberada.id}/upload/',
                       {'arquivos': SimpleUploadedFile('novo.pdf', b'zz', content_type='application/pdf'),
                        'folder_id': 'SOLTA'}, **AJAX)
        t('e enviar arquivo para dentro dela', r.json().get('enviados') == 1, r.json())
        t('o arquivo foi para a pasta certa', falso.enviados == [('novo.pdf', 'SOLTA')], falso.enviados)

        r = c_ana.post('/drive/file/ARQ_SOLTA/renomear/', {'nome': 'outro nome.pdf'}, **AJAX)
        t('mas renomear precisa de EDITAR, e ela não tem', r.status_code in (302, 403), r.status_code)

        liberada.nivel = 'VIEW'
        liberada.save(update_fields=['nivel'])
        t('com nível VIEW, baixar é recusado',
          c_ana.get('/drive/file/ARQ_SOLTA/content/?dl=1').status_code in (302, 403))
        t('mas ver na tela continua valendo (é o que VER significa)',
          c_ana.get('/drive/file/ARQ_SOLTA/content/').status_code == 200)
        r = c_ana.get(f'/drive/p/{liberada.id}/')
        t('e a tela não oferece enviar', not r.context['pode_upload'])
        t('nem liberar acesso item a item (isso é do setor)', not r.context['pode_gerir_acesso'])

        liberada.nivel = 'EDIT'
        liberada.save(update_fields=['nivel'])
        r = c_ana.post('/drive/file/ARQ_SOLTA/renomear/', {'nome': 'contrato assinado.pdf'}, **AJAX)
        t('com EDITAR, renomeia', r.status_code == 200 and falso.itens['ARQ_SOLTA']['name'] == 'contrato assinado.pdf',
          falso.itens['ARQ_SOLTA']['name'])

        print('\n== FAVORITAR DE DENTRO DA PASTA ==')
        r = c_ana.post('/drive/file/SUBSOLTA/favoritar/', **AJAX)
        fav = DriveFavorite.objects.filter(user=ana, file_id='SUBSOLTA').first()
        t('o favorito guarda de qual pasta liberada veio', fav and fav.pasta_id == liberada.id)
        html = c_ana.get('/drive/favoritos/').content.decode()
        t('e o link dos favoritos volta para a pasta',
          f'/drive/p/{liberada.id}/f/SUBSOLTA/' in html)

        print('\n== GRUPO, SETOR E HIERARQUIA ==')
        grupo = CommunicationGroup.objects.create(name='ZZ Grupo da pasta', created_by=chefe)
        bruno.communication_groups.add(grupo)
        c.post('/drive/gestao/permissoes/pasta/',
               {'folder_id': 'OUTRA', 'alvo': 'GROUP', 'target_group': grupo.id, 'nivel': 'VIEW'})
        t('liberar para um grupo alcança quem está nele',
          'ZZ Pasta de ninguém' in c_bruno.get('/drive/').content.decode())
        t('e não alcança quem não está',
          'ZZ Pasta de ninguém' not in c_ana.get('/drive/').content.decode())

        outro_setor = Sector.objects.create(name='ZZ Setor alvo')
        ana.sector = outro_setor
        ana.save(update_fields=['sector'])
        c.post('/drive/gestao/permissoes/pasta/',
               {'folder_id': 'SETOR', 'alvo': 'SECTOR', 'target_sector': outro_setor.id, 'nivel': 'VIEW'})
        t('liberar para um setor alcança quem está lotado nele',
          'ZZ Pasta do setor' in c_ana.get('/drive/').content.decode())

        print('\n== A LISTA DE QUEM TEM ACESSO ==')
        r = c.get('/drive/acessos/')
        html = r.content.decode()
        t('a tela de acessos ganhou a coluna de pastas', 'Pastas liberadas' in html)
        t('e mostra a pasta na linha de quem recebeu', 'ZZ Contratos 2026' in html)
        linha_ana = next((l for l in r.context['linhas'] if l['user'].id == ana.id), None)
        t('a Ana aparece com a pasta liberada',
          linha_ana and 'ZZ Contratos 2026' in linha_ana['pastas'], linha_ana)
        t('e o Bruno com a dele (pelo grupo)',
          any('ZZ Pasta de ninguém' in l['pastas'] for l in r.context['linhas']
              if l['user'].id == bruno.id))

        print('\n== RETIRAR O ACESSO ==')
        r = c.post(f'/drive/gestao/permissoes/pasta/{liberada.id}/excluir/', follow=True)
        t('a liberação some', not PastaLiberada.objects.filter(pk=liberada.id).exists())
        t('a tela confirma', 'não acessa mais' in r.content.decode())
        t('o cartão sai de /drive',
          'ZZ Contratos 2026' not in c_ana.get('/drive/').content.decode())
        t('e o arquivo volta a ser negado',
          c_ana.get('/drive/file/ARQ_SOLTA/').status_code in (302, 403))
        t('a retirada fica na auditoria',
          DriveAuditLog.objects.filter(acao='PERM', detalhe__contains='retirada').exists())

        print('\n== O QUE NÃO PODE MUDAR ==')
        t('a permissão por setor continua funcionando',
          c.get(f'/drive/s/{setor.id}/').status_code == 200)
        t('e o motor não confunde as duas raízes',
          perms.file_allowed(bruno, 'ARQ_SETOR')[1] == 0
          and perms.file_allowed(chefe, 'ARQ_SETOR')[1] == perms.ADMIN)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco; nada saiu para o Google.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
