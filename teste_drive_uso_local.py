"""Drive: "Usar localmente" — a cópia de trabalho no computador e a devolução.

Pedido: "em /drive/s/{id}/ — adicione ao menu a função 'Usar localmente', que
cria uma pasta de arquivos temporários em /Downloads no computador do usuário,
ele manuseia os arquivos por lá e, quando finalizar, volta e seleciona
'Finalizar Uso' (podendo tanto ser pasta ou arquivos)".

O que este teste cobre (o lado do portal; o do navegador está no harness de
tela do scratchpad):

- o manifesto de uma pasta: cada arquivo com o caminho dentro dela, as
  subpastas com o id do Drive e o que fica de fora (arquivo nativo do Google);
- o manifesto de um arquivo só — a mesma ação serve para os dois;
- quem só VÊ não leva cópia nenhuma (403 e registro de acesso negado);
- os limites (arquivos, bytes e profundidade) avisando em vez de baixar o setor
  inteiro sem querer;
- o "Finalizar uso" registrando na auditoria o que subiu;
- o menu da listagem trazendo as duas opções para quem pode baixar;
- "nova pasta" devolvendo o id, que é como o navegador manda o arquivo novo
  para dentro da subpasta criada no computador.

Roda numa transação desfeita no fim. Não fala com o Google (Drive falso em
memória) e os caches ficam em memória.
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
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-uso-local'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-uso-local-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from drive import gdrive, uso_local
from drive.models import DriveAuditLog, DriveConfig, DrivePermission, SectorDriveMapping
from users.models import Sector

User = get_user_model()
FOLDER = gdrive.FOLDER_MIME
GOOGLE_DOC = 'application/vnd.google-apps.document'
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

    def add(self, id_, nome, mime, pai, tamanho='1024'):
        self.itens[id_] = dict(
            id=id_, name=nome, mimeType=mime, parents=[pai] if pai else [], trashed=False,
            size=tamanho, modifiedTime='2026-09-10T10:00:00Z', createdTime='2026-09-01T10:00:00Z',
            md5Checksum='zzmd5' + id_)
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

    def criar_pasta(self, nome, parent_id):
        novo = f'NOVA_{len(self.itens)}'
        return self.add(novo, nome, FOLDER, parent_id)


falso = DriveFalso()
falso.add('RAIZ_GOOGLE', 'Meu Drive', FOLDER, None)
falso.add('SETOR_RAIZ', 'ZZ Raiz do setor', FOLDER, 'RAIZ_GOOGLE')
falso.add('PASTA_OBRA', 'ZZ Obra 2026', FOLDER, 'SETOR_RAIZ')
falso.add('ARQ_PLANILHA', 'ZZ medicao.xlsx',
          'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'PASTA_OBRA', '2048')
falso.add('ARQ_PDF', 'ZZ contrato.pdf', 'application/pdf', 'PASTA_OBRA')
falso.add('ARQ_GOOGLE', 'ZZ ata (Google)', GOOGLE_DOC, 'PASTA_OBRA')
falso.add('SUB_FOTOS', 'ZZ Fotos', FOLDER, 'PASTA_OBRA')
falso.add('ARQ_FOTO', 'ZZ fachada.jpg', 'image/jpeg', 'SUB_FOTOS')
falso.add('PASTA_VAZIA', 'ZZ Sem nada', FOLDER, 'SETOR_RAIZ')

FALSOS = {n: getattr(falso, n) for n in
          ('obter', 'ancestrais', 'dentro_de', 'caminho', 'listar', 'pai_de', 'baixar', 'criar_pasta')}

AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}
marcador = transaction.atomic()
marcador.__enter__()
try:
    def novo(username, hierarquia='PADRAO'):
        return User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local', password='S3nha!teste',
            first_name='ZZ', last_name=username.split('.')[1].title(), hierarchy=hierarquia)

    chefe = novo('zzul.chefe', 'SUPERADMIN')
    chefe.is_superuser = chefe.is_staff = True
    chefe.save()
    so_ve = novo('zzul.sove')
    baixa = novo('zzul.baixa')

    cfg = DriveConfig.get()
    cfg.ativo = True
    cfg.save()

    setor = Sector.objects.create(name='ZZ Setor Uso Local')
    mapa = SectorDriveMapping.objects.create(sector=setor, folder_id='SETOR_RAIZ',
                                             folder_name='ZZ Raiz do setor')
    DrivePermission.objects.create(mapping=mapa, alvo='USER', target_user=so_ve, nivel='VIEW')
    DrivePermission.objects.create(mapping=mapa, alvo='USER', target_user=baixa, nivel='DOWNLOAD')

    def cliente(u):
        c = Client()
        c.force_login(u)
        return c

    c_chefe, c_ve, c_baixa = cliente(chefe), cliente(so_ve), cliente(baixa)
    URL = '/drive/file/{}/uso-local/'
    FIM = '/drive/file/{}/uso-local/finalizar/'

    with mock.patch.multiple(gdrive, **FALSOS):

        print('== O MANIFESTO DE UMA PASTA ==')
        r = c_chefe.get(URL.format('PASTA_OBRA'))
        t('responde 200 para quem administra', r.status_code == 200, r.status_code)
        dados = r.json()
        caminhos = sorted(a['caminho'] for a in dados['arquivos'])
        t('traz os arquivos com o caminho dentro da pasta',
          caminhos == ['ZZ Fotos/ZZ fachada.jpg', 'ZZ contrato.pdf', 'ZZ medicao.xlsx'], caminhos)
        t('cada arquivo leva id, tamanho e o endereço para baixar',
          all(a['id'] and a['url'].endswith('?dl=1') for a in dados['arquivos'])
          and any(a['tamanho'] == 2048 for a in dados['arquivos']), dados['arquivos'][:1])
        t('a subpasta vai com o id do Drive (é para onde sobe arquivo novo)',
          dados['pastas'] == [{'id': 'SUB_FOTOS', 'caminho': 'ZZ Fotos'}], dados['pastas'])
        t('o arquivo nativo do Google fica de fora, e a tela sabe disso',
          dados['ignorados'] == ['ZZ ata (Google)'], dados['ignorados'])
        t('diz que é pasta, de qual setor e o que a pessoa pode fazer',
          dados['pasta'] is True and dados['sector_id'] == setor.id
          and dados['pode_enviar'] is True and dados['pode_criar'] is True, dados.get('sector_id'))
        t('e soma os bytes da cópia', dados['bytes'] == 2048 + 1024 + 1024, dados['bytes'])

        print('\n== O MANIFESTO DE UM ARQUIVO SÓ ==')
        dados = c_chefe.get(URL.format('ARQ_PDF')).json()
        t('um arquivo também pode ser usado localmente',
          dados['pasta'] is False and len(dados['arquivos']) == 1
          and dados['arquivos'][0]['caminho'] == 'ZZ contrato.pdf', dados['arquivos'])
        t('e ele sabe de qual pasta veio (para o arquivo novo ter destino)',
          dados['arquivos'][0]['pasta_id'] == 'PASTA_OBRA' and dados['pasta_id'] == 'PASTA_OBRA')

        dados = c_chefe.get(URL.format('ARQ_GOOGLE')).json()
        t('arquivo do Google sozinho avisa que se edita no próprio Google',
          not dados['arquivos'] and dados['ignorados'] == ['ZZ ata (Google)']
          and 'Google' in dados['aviso'], dados.get('aviso'))

        dados = c_chefe.get(URL.format('PASTA_VAZIA')).json()
        t('pasta vazia avisa que não há o que copiar',
          not dados['arquivos'] and 'Não há arquivo' in dados['aviso'], dados.get('aviso'))

        print('\n== QUEM PODE ==')
        r = c_baixa.get(URL.format('PASTA_OBRA'))
        t('quem pode baixar leva a cópia', r.status_code == 200)
        t('mas a tela já avisa que ele não envia alteração',
          r.json()['pode_enviar'] is False and r.json()['pode_criar'] is False, r.json().get('pode_enviar'))
        antes = DriveAuditLog.objects.filter(acao='DENY').count()
        r = c_ve.get(URL.format('PASTA_OBRA'))
        t('quem só VÊ não leva nada (403)', r.status_code == 403 and 'não pode baixar' in r.json()['erro'],
          r.status_code)
        t('e a recusa fica na auditoria',
          DriveAuditLog.objects.filter(acao='DENY').count() == antes + 1)
        r = c_chefe.get(URL.format('ARQ_FORA_DO_SETOR'))
        t('item que não existe no setor também é recusado', r.status_code == 403, r.status_code)

        print('\n== OS LIMITES ==')
        with mock.patch.object(uso_local, 'LIMITE_ARQUIVOS', 2):
            dados = c_chefe.get(URL.format('PASTA_OBRA')).json()
            t('passando do limite de arquivos, a tela manda abrir uma subpasta',
              dados['excedeu'] is True and dados['limites']['arquivos'] == 2, dados['excedeu'])
        with mock.patch.object(uso_local, 'LIMITE_PROFUNDIDADE', 1):
            dados = c_chefe.get(URL.format('PASTA_OBRA')).json()
            t('e a subpasta funda demais fica de fora, com o nome anotado',
              dados['fundo_demais'] == ['ZZ Fotos']
              and all('/' not in a['caminho'] for a in dados['arquivos']), dados['fundo_demais'])

        print('\n== FINALIZAR O USO ==')
        r = c_chefe.get(FIM.format('PASTA_OBRA'))
        t('GET não finaliza (405)', r.status_code == 405, r.status_code)
        r = c_chefe.post(FIM.format('PASTA_OBRA'), {'nome': 'ZZ Obra 2026', 'versoes': 2,
                                                    'novos': 1, 'apagados': 3, 'falhas': 0})
        t('o fim responde com o resumo', r.status_code == 200 and r.json()['ok'] is True, r.status_code)
        registro = DriveAuditLog.objects.filter(acao='USO_LOCAL', file_id='PASTA_OBRA').order_by('-id').first()
        t('e a auditoria guarda o que subiu',
          registro and '2 nova(s) versão(ões)' in registro.detalhe
          and '1 novo(s)' in registro.detalhe and '3 apagado(s)' in registro.detalhe,
          registro.detalhe if registro else None)
        comecos = list(DriveAuditLog.objects.filter(acao='USO_LOCAL', detalhe__startswith='início')
                       .order_by('id').values_list('detalhe', flat=True))
        t('o começo também ficou registrado, com quantos arquivos',
          comecos and '3 arquivo(s)' in comecos[0], comecos[:2])

        print('\n== O MENU DA LISTAGEM ==')
        html = c_chefe.get(f'/drive/s/{setor.id}/').content.decode()
        t('o menu do item oferece "Usar localmente"', 'Usar localmente' in html)
        t('e "Finalizar uso" (a página escolhe qual mostrar)', 'Finalizar uso' in html)
        t('os dois nascem escondidos, com o id do item',
          'data-uso-local="PASTA_OBRA"' in html and 'data-uso-papel="finalizar"' in html)
        t('e a página carrega o módulo do uso local', 'rc-uso-local.js' in html)
        html = c_ve.get(f'/drive/s/{setor.id}/').content.decode()
        t('quem não pode baixar não vê a opção', 'Usar localmente' not in html)

        print('\n== NOVA PASTA DEVOLVE O ID ==')
        r = c_chefe.post(f'/drive/s/{setor.id}/mkdir/',
                         {'folder_id': 'PASTA_OBRA', 'nome': 'ZZ Medições'}, **AJAX)
        t('criar pasta responde o id, que é para onde o arquivo novo vai',
          r.status_code == 200 and r.json()['ok'] is True and r.json()['file_id'].startswith('NOVA_'),
          r.content[:120])
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco; nada saiu para o Google.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
