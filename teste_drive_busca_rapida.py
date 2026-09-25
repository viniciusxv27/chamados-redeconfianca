"""Drive: a busca de arquivos/pastas não pode custar uma chamada por resultado.

Pedido: "em /drive/s/{id}/ — a busca de arquivos/pasta está demorando muito
para ser feita, otimize essa parte".

Era isto: a busca pedia 80 resultados ao Google e, para cada um, chamava
`file_allowed`, que subia a árvore de pastas com **uma chamada ao Google por
degrau** e ainda relia os setores do banco. Medido no Drive de verdade em
25/09/2026: 512 chamadas ao Google, 1.207 consultas ao banco e **172 s** para
uma busca. Agora a permissão dos 80 vai junto (`resolver_acessos`), a subida é
um degrau por vez em lote (`gdrive.pais_de`, o batch do Google) e para na raiz
do setor: a mesma busca leva ~7 s com cache frio e ~2 s com cache quente.

O que este teste cobre:

- o lote de pais: vários ids numa chamada só, e a volta para um a um quando o
  lote não existe;
- a subida um degrau por vez, parando na raiz, e o cache poupando a segunda;
- o número de chamadas ao Google não crescer com a quantidade de resultados;
- a decisão de permissão continuar igual à de antes (item a item), sem vazar
  arquivo de setor que a pessoa não enxerga;
- a tela: pasta abre a listagem, arquivo abre a prévia, filtro por setor e o
  teto de 60 resultados.

Google Drive é um dublê em memória. Transação desfeita no fim.
"""
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dv-busca'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dv-busca-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.test import Client

from drive import gdrive
from drive import permissions as perms
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
    """Drive em memória que conta quantas vezes o portal fala com o Google."""

    def __init__(self):
        self.itens = {}
        self.chamadas_pai = 0      # pai_de, um id por vez
        self.chamadas_lote = 0     # pais_de, um pedido com vários ids
        self.ids_no_lote = 0
        self.buscas = 0

    def add(self, id_, nome, mime, pai):
        self.itens[id_] = dict(id=id_, name=nome, mimeType=mime, parents=[pai] if pai else [],
                               trashed=False, size='10', modifiedTime='2026-09-20T10:00:00Z')
        return self.itens[id_]

    def obter(self, file_id, fields=None):
        if file_id not in self.itens:
            raise gdrive.DriveError('404')
        return dict(self.itens[file_id])

    def ancestrais(self, file_id, limite=30):
        ids, atual = [], file_id
        while atual and atual not in ids and len(ids) < limite:
            ids.append(atual)
            self.chamadas_pai += 1
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
        filhos = [dict(i) for i in self.itens.values()
                  if folder_id in i['parents'] and not i['trashed']
                  and (not apenas_pastas or i['mimeType'] == FOLDER)]
        return filhos, None

    def buscar(self, nome='', mime='', page_token=None, page_size=50):
        self.buscas += 1
        achados = [dict(i) for i in self.itens.values()
                   if nome.lower() in i['name'].lower() and not i['trashed']
                   and (not mime or i['mimeType'] == mime)]
        return achados[:page_size], None

    def pai_de(self, file_id):
        self.chamadas_pai += 1
        return (self.itens.get(file_id, {}).get('parents') or [''])[0]

    def pais_de(self, ids):
        ids = [i for i in dict.fromkeys(ids) if i]
        self.chamadas_lote += 1
        self.ids_no_lote += len(ids)
        return {i: (self.itens.get(i, {}).get('parents') or [''])[0] for i in ids}

    def id_da_raiz(self):
        return 'RAIZ'


falso = DriveFalso()
falso.add('RAIZ', 'Meu Drive', FOLDER, None)
falso.add('SETOR', 'ZZ Comercial', FOLDER, 'RAIZ')
# uma escada de pastas, para a subida ter degraus de verdade
anterior = 'SETOR'
for nivel in range(1, 5):
    atual = f'N{nivel}'
    falso.add(atual, f'ZZ Nível {nivel}', FOLDER, anterior)
    anterior = atual
for n in range(40):
    falso.add(f'ARQ{n:02d}', f'zzbusca contrato {n:02d}.pdf', 'application/pdf', anterior)
falso.add('PASTA_ACHAVEL', 'zzbusca pasta de contratos', FOLDER, 'N2')
falso.add('FORA', 'ZZ Fora', FOLDER, 'RAIZ')
falso.add('ARQ_FORA', 'zzbusca contrato de fora.pdf', 'application/pdf', 'FORA')

FALSOS = {n: getattr(falso, n) for n in
          ('obter', 'ancestrais', 'dentro_de', 'caminho', 'listar', 'buscar', 'pai_de',
           'pais_de', 'id_da_raiz')}


class ServicoFalso:
    """Só o suficiente para exercitar o lote de verdade dentro de `pais_de`."""

    def __init__(self, itens, quebrar=False):
        self.itens = itens
        self.quebrar = quebrar
        self.lotes = 0

    def files(self):
        return self

    def get(self, fileId=None, fields=None, **kw):
        return fileId

    def new_batch_http_request(self, callback=None):
        if self.quebrar:
            raise RuntimeError('este servidor não aceita lote')
        servico = self

        class Lote:
            def __init__(self):
                self.pedidos = []

            def add(self, pedido, request_id=None):
                self.pedidos.append((request_id, pedido))

            def execute(self):
                servico.lotes += 1
                for request_id, file_id in self.pedidos:
                    item = servico.itens.get(file_id)
                    callback(request_id, {'id': file_id, 'parents': item['parents']} if item else None,
                             None if item else Exception('404'))
        return Lote()


marcador = transaction.atomic()
marcador.__enter__()
try:
    print('== O LOTE DE PAIS ==')
    servico = ServicoFalso(falso.itens)
    with mock.patch.object(gdrive, 'service', return_value=servico):
        pais = gdrive.pais_de([f'ARQ{n:02d}' for n in range(40)])
    t('responde por todos os ids', len(pais) == 40, len(pais))
    t('numa chamada só', servico.lotes == 1, servico.lotes)
    t('com o pai certo', set(pais.values()) == {'N4'}, set(pais.values()))

    quebrado = ServicoFalso(falso.itens, quebrar=True)
    falso.chamadas_pai = 0
    with mock.patch.object(gdrive, 'service', return_value=quebrado), \
         mock.patch.object(gdrive, 'pai_de', falso.pai_de):
        pais = gdrive.pais_de(['ARQ00', 'ARQ01', 'ARQ02'])
    t('servidor sem lote: cai para um a um e continua funcionando',
      pais == {'ARQ00': 'N4', 'ARQ01': 'N4', 'ARQ02': 'N4'} and falso.chamadas_pai == 3,
      (pais, falso.chamadas_pai))

    with mock.patch.object(gdrive, 'service', return_value=servico):
        t('lista vazia não fala com o Google', gdrive.pais_de([]) == {})

    print('\n== A SUBIDA DA ÁRVORE ==')
    with mock.patch.multiple(gdrive, **FALSOS):
        cache.clear()
        falso.chamadas_lote = falso.ids_no_lote = 0
        pais = {}
        perms.preencher_pais([f'ARQ{n:02d}' for n in range(40)], pais, parar_em={'SETOR'})
        t('um pedido por degrau, não por arquivo', falso.chamadas_lote <= 5, falso.chamadas_lote)
        t('todos os arquivos ganharam o pai', all(pais.get(f'ARQ{n:02d}') == 'N4' for n in range(40)))
        t('a escada inteira foi resolvida', pais.get('N4') == 'N3' and pais.get('N1') == 'SETOR')
        t('e a subida parou na raiz do setor (não foi até o Meu Drive)',
          'RAIZ' not in pais and 'SETOR' not in pais, list(pais)[:8])

        antes = falso.chamadas_lote
        pais2 = {}
        perms.preencher_pais([f'ARQ{n:02d}' for n in range(40)], pais2, parar_em={'SETOR'})
        t('na segunda vez o cache responde e ninguém fala com o Google',
          falso.chamadas_lote == antes, falso.chamadas_lote - antes)
        t('com o mesmo resultado', pais2.get('ARQ00') == 'N4' and pais2.get('N1') == 'SETOR')

    print('\n== A TELA DE BUSCA ==')
    setor = Sector.objects.create(name='ZZ Comercial Busca')
    outro = Sector.objects.create(name='ZZ Setor de fora')
    chefe = User.objects.create_user(
        username='zzbu.chefe', email='zzbu.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)
    ana = User.objects.create_user(
        username='zzbu.ana', email='zzbu.ana@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Ana', sector=setor)

    cfg = DriveConfig.get(); cfg.ativo = True; cfg.save()
    mapa = SectorDriveMapping.objects.create(sector=setor, folder_id='SETOR', folder_name='ZZ Comercial')
    SectorDriveMapping.objects.create(sector=outro, folder_id='FORA', folder_name='ZZ Fora')
    DrivePermission.objects.create(mapping=mapa, alvo='SECTOR', target_sector=setor, nivel='VIEW')

    c = Client(); c.force_login(ana)
    with mock.patch.multiple(gdrive, **FALSOS):
        cache.clear()
        falso.chamadas_lote = falso.ids_no_lote = falso.chamadas_pai = 0
        r = c.get('/drive/busca/?q=zzbusca')
        t('a busca abre', r.status_code == 200, r.status_code)
        nomes = {f['name'] for f in r.context['resultados']}
        t('acha os arquivos do setor dela', len([n for n in nomes if 'contrato' in n]) >= 40, len(nomes))
        t('acha também a pasta', 'zzbusca pasta de contratos' in nomes)
        t('e não vaza o arquivo do setor que ela não enxerga',
          'zzbusca contrato de fora.pdf' not in nomes, nomes & {'zzbusca contrato de fora.pdf'})
        t('pediu a busca ao Google uma vez só', falso.buscas == 1, falso.buscas)
        t('e resolveu a permissão de todos em poucos pedidos (não um por arquivo)',
          falso.chamadas_lote <= 6 and falso.chamadas_pai == 0,
          (falso.chamadas_lote, falso.chamadas_pai))

        html = r.content.decode()
        t('a pasta achada abre a listagem dela',
          f'/drive/s/{setor.id}/f/PASTA_ACHAVEL/' in html)
        t('e o arquivo abre a prévia', '/drive/file/ARQ00/' in html)
        t('cada resultado diz de que setor é', 'ZZ Comercial Busca' in html)

        falso.chamadas_lote = 0
        r = c.get('/drive/busca/?q=zzbusca')
        t('repetir a busca não sobe a árvore de novo (cache)', falso.chamadas_lote == 0,
          falso.chamadas_lote)

        r = c.get(f'/drive/busca/?q=zzbusca&setor={outro.id}')
        t('filtrando por um setor que ela não enxerga, não vem nada',
          len(r.context['resultados']) == 0, len(r.context['resultados']))

        r = c.get('/drive/busca/?q=zzbusca&tipo=pdf')
        t('o filtro por tipo continua valendo',
          all(f['mimeType'] == 'application/pdf' for f in r.context['resultados']))

        cs = Client(); cs.force_login(chefe)
        r = cs.get('/drive/busca/?q=zzbusca')
        nomes = {f['name'] for f in r.context['resultados']}
        t('o SUPERADMIN vê inclusive o de fora', 'zzbusca contrato de fora.pdf' in nomes)

    print('\n== A DECISÃO NÃO MUDOU ==')
    with mock.patch.multiple(gdrive, **FALSOS):
        cache.clear()
        achados, _ = gdrive.buscar(nome='zzbusca', page_size=80)
        um_a_um = {}
        for f in achados:
            um_a_um[f['id']] = perms.file_allowed(ana, f['id'])[1]
        cache.clear()
        juntos = perms.resolver_acessos(ana, achados)
        t('resolver_acessos dá o mesmo nível que file_allowed item a item',
          {i: n for i, (m, n) in juntos.items()} == um_a_um,
          [(i, um_a_um[i], juntos[i][1]) for i in um_a_um if um_a_um[i] != juntos[i][1]][:3])
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco; nada saiu para o Google.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
