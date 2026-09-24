"""Drive: visão em lista, ordem, nome inteiro, envio de vários arquivos e a gestão.

Pedidos:

- "em /drive/s/{id}/ — permita alterar a visualização (adicionando em lista),
  podendo alterar ordem; permita expandir a visualização padrão para ler o nome
  todo";
- "o upload de fotos está com alguma dificuldade, ou upload de múltiplos
  arquivos, corrija e garanta o envio";
- "em /drive/gestao/ — o campo 'Gestores do setor' precisa ser um select
  multiple mais interativo com todos os usuários; e selecionar a pasta precisa
  ser algo bem mais simples do que ficar copiando o id".

O que este teste cobre:

- a troca de visão e de ordem, o que vai para o Google em cada ordem e a
  escolha ficando guardada na sessão;
- o envio um a um pelo AJAX: o que entra, o que é recusado com o motivo, e o
  campo `enviados` que antes vinha misturado com o `ok`;
- a lista de extensões e o limite aparecendo na tela de quem envia;
- o endereço que lista pastas para o seletor da gestão, e o id saindo de uma
  URL colada.

Drive falso em memória, caches em memória, transação desfeita no fim.
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
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dv-visao'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dv-visao-2'},
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

from drive import gdrive, views as dviews
from drive.models import DriveConfig, SectorDriveMapping
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
    def __init__(self):
        self.itens = {}
        self.ordens = []
        self.enviados = []

    def add(self, id_, nome, mime, pai):
        self.itens[id_] = dict(id=id_, name=nome, mimeType=mime, parents=[pai] if pai else [],
                               trashed=False, size='1024', modifiedTime='2026-09-10T10:00:00Z')
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
        return [(i, self.itens[i]['name']) for i in reversed(self.ancestrais(file_id)) if i in self.itens]

    def listar(self, folder_id, page_token=None, page_size=100, trashed=False, apenas_pastas=False,
               order='folder,name'):
        self.ordens.append((folder_id, order, apenas_pastas))
        if folder_id == 'root':
            folder_id = 'RAIZ'          # o apelido que a API do Google entende
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
        return self.add(f'NOVO_{len(self.itens)}', nome, mime or 'application/octet-stream', pasta_id)

    def id_da_raiz(self):
        return 'RAIZ'


falso = DriveFalso()
falso.add('RAIZ', 'Meu Drive', FOLDER, None)
falso.add('SETOR', 'ZZ Raiz', FOLDER, 'RAIZ')
falso.add('SUB', 'ZZ Subpasta', FOLDER, 'SETOR')
falso.add('ARQ', 'ZZ um arquivo com nome bem comprido para caber na tela.pdf', 'application/pdf', 'SETOR')

FALSOS = {n: getattr(falso, n) for n in
          ('obter', 'ancestrais', 'dentro_de', 'caminho', 'listar', 'pai_de', 'baixar', 'enviar',
           'id_da_raiz')}
AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}

marcador = transaction.atomic()
marcador.__enter__()
try:
    chefe = User.objects.create_user(
        username='zzdv.chefe', email='zzdv.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)
    comum = User.objects.create_user(
        username='zzdv.comum', email='zzdv.comum@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Comum')

    cfg = DriveConfig.get()
    cfg.ativo = True
    cfg.allowed_extensions = 'pdf,jpg,png'
    cfg.max_file_mb = 1
    cfg.save()

    setor = Sector.objects.create(name='ZZ Setor Visão')
    SectorDriveMapping.objects.create(sector=setor, folder_id='SETOR', folder_name='ZZ Raiz')

    c = Client(); c.force_login(chefe)
    c_comum = Client(); c_comum.force_login(comum)
    BROWSE = f'/drive/s/{setor.id}/'

    with mock.patch.multiple(gdrive, **FALSOS):

        print('== VISÃO E ORDEM ==')
        r = c.get(BROWSE)
        t('a visão padrão é a de cartões', r.context['visao'] == 'cartoes', r.context['visao'])
        t('e a ordem padrão é por nome', r.context['ordem'] == 'nome')
        html = r.content.decode()
        t('a barra traz os dois botões de visão e o seletor de ordem',
          'Ver em lista' in html and 'Ver em cartões' in html and 'Ordenar por' in html)
        t('e o botão de mostrar o nome inteiro', 'dv-btn-nomes' in html and 'Mostrar o nome inteiro' in html)

        falso.ordens.clear()
        r = c.get(BROWSE + '?v=lista&o=recente')
        t('a visão em lista chega na tela', r.context['visao'] == 'lista'
          and 'dv-visao-lista' in r.content.decode())
        t('e a ordem escolhida vai para o Google do jeito que ele entende',
          falso.ordens and falso.ordens[0][1] == 'folder,modifiedTime desc', falso.ordens)

        falso.ordens.clear()
        r = c.get(BROWSE)
        t('a escolha fica guardada: a próxima visita abre em lista e por data',
          r.context['visao'] == 'lista' and r.context['ordem'] == 'recente'
          and falso.ordens[0][1] == 'folder,modifiedTime desc', (r.context['visao'], falso.ordens))

        r = c.get(BROWSE + '?v=inventada&o=tanto_faz')
        t('visão e ordem inventadas caem no padrão',
          r.context['visao'] == 'cartoes' and r.context['ordem'] == 'nome')
        falso.ordens.clear()
        c.get(BROWSE + '?o=maior')
        t('"maiores" ordena pelo tamanho no Google',
          falso.ordens[0][1] == 'folder,quotaBytesUsed desc', falso.ordens)

        print('\n== O QUE A TELA DIZ SOBRE O ENVIO ==')
        html = c.get(BROWSE).content.decode()
        t('a tela mostra o limite por arquivo', 'Até 1 MB por arquivo' in html, )
        t('e quais tipos são aceitos', 'aceita: jpg, pdf, png' in html)
        t('o painel de envio está na página', 'dv-envio-lista' in html and 'dvEnviarArquivos' in html)

        print('\n== ENVIO, UM ARQUIVO POR VEZ ==')
        foto = SimpleUploadedFile('ZZ foto.jpg', b'\xff\xd8\xff' + b'z' * 50, content_type='image/jpeg')
        r = c.post(BROWSE + 'upload/', {'folder_id': 'SETOR', 'arquivos': foto}, **AJAX)
        dados = r.json()
        t('a foto entra (a lista de extensões agora aceita imagem)',
          r.status_code == 200 and dados['enviados'] == 1, (r.status_code, dados))
        t('e o "ok" da resposta continua sendo o sim/não',
          dados['ok'] is True and ('ZZ foto.jpg', 'SETOR') in falso.enviados, dados)

        ruim = SimpleUploadedFile('ZZ video.mov', b'zz', content_type='video/quicktime')
        r = c.post(BROWSE + 'upload/', {'folder_id': 'SETOR', 'arquivos': ruim}, **AJAX)
        dados = r.json()
        t('tipo fora da lista é recusado com o motivo, e não em silêncio',
          r.status_code == 400 and dados['enviados'] == 0
          and 'extensão .mov não permitida' in dados['erros'][0], dados)

        grande = SimpleUploadedFile('ZZ grande.pdf', b'z' * (1024 * 1024 + 10), content_type='application/pdf')
        r = c.post(BROWSE + 'upload/', {'folder_id': 'SETOR', 'arquivos': grande}, **AJAX)
        t('acima do limite também', r.status_code == 400 and 'limite de 1 MB' in r.json()['erros'][0],
          r.json())

        antes = len(falso.enviados)
        r = c_comum.post(BROWSE + 'upload/', {'folder_id': 'SETOR', 'arquivos':
                                              SimpleUploadedFile('ZZ x.pdf', b'z')}, **AJAX)
        t('quem não pode enviar leva 403 e nada entra',
          r.status_code in (403, 302) and len(falso.enviados) == antes, r.status_code)

        print('\n== A GESTÃO: ESCOLHER A PASTA SEM COPIAR ID ==')
        t('o id sai da URL do Drive',
          dviews.id_de_pasta('https://drive.google.com/drive/folders/1A2b3C4d5E6f?usp=sharing') == '1A2b3C4d5E6f')
        t('e de "?id="', dviews.id_de_pasta('https://drive.google.com/open?id=1A2b3C4d5E6f') == '1A2b3C4d5E6f')
        t('id digitado continua valendo', dviews.id_de_pasta('1A2b3C4d5E6f') == '1A2b3C4d5E6f')
        t('e o vazio não vira lixo', dviews.id_de_pasta('') == '' and dviews.id_de_pasta(None) == '')

        r = c.get('/drive/gestao/pastas/')
        dados = r.json()
        t('o seletor lista as pastas da raiz', r.status_code == 200 and dados['ok'] is True
          and [p['nome'] for p in dados['pastas']] == ['ZZ Raiz'], dados)
        t('e pede só pastas ao Google', falso.ordens[-1][2] is True, falso.ordens[-1])
        r = c.get('/drive/gestao/pastas/?pai=SETOR')
        dados = r.json()
        t('descendo uma pasta, vêm as subpastas dela',
          [p['nome'] for p in dados['pastas']] == ['ZZ Subpasta'] and dados['nome'] == 'ZZ Raiz', dados)
        t('com o caminho de volta', dados['acima'] == 'root', dados)
        r = c.get('/drive/gestao/pastas/?pai=' + 'https%3A%2F%2Fdrive.google.com%2Fdrive%2Ffolders%2FSETOR')
        t('o endereço colado também é aceito no seletor', r.status_code == 200
          and r.json()['pai'] == 'SETOR', r.json())
        r = c_comum.get('/drive/gestao/pastas/')
        t('e só o SUPERADMIN lista', r.status_code == 403, r.status_code)

        r = c.post('/drive/gestao/', {'sector': setor.id, 'ativo': 'on',
                                      'folder_id': 'https://drive.google.com/drive/folders/SUB'},
                   follow=True)
        mapa = SectorDriveMapping.objects.get(sector=setor)
        t('salvar a gestão com a URL colada guarda só o id',
          mapa.folder_id == 'SUB' and mapa.folder_name == 'ZZ Subpasta', (mapa.folder_id, mapa.folder_name))

        print('\n== A GESTÃO: ESCOLHER GESTORES ==')
        html = c.get('/drive/gestao/').content.decode()
        t('a busca de pessoas está na tela', 'f-managers-busca' in html and 'Buscar pessoa' in html)
        t('os escolhidos aparecem como etiquetas', 'f-managers-chips' in html)
        t('o select continua lá, escondido, para o POST não mudar',
          'name="managers"' in html and 'id="f-managers" multiple class="hidden"' in html)
        t('cada pessoa leva nome e e-mail para a busca achar', 'data-busca="zz chefe' in html.lower())
        t('e o seletor de pasta virou botão', 'dvEscolherPasta()' in html
          and 'Escolher pasta…' in html)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco; nada saiu para o Google.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
