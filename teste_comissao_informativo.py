"""Comissionamento: o informativo "Como funciona o Comissionamento?".

Pedido: "Permita a pessoa subir um informativo, seja pdf, video, link ou imagem
de como funciona o comissionamento, e deve aparecer em todas as visões de
/comission, como um informativo e a pessoa acessa, abrindo uma nova guia".

O que este teste cobre:

- o SUPERADMIN publica por `/users/manage/system-config/` — arquivo ou link;
- o tipo sai do próprio arquivo (PDF, imagem, vídeo) e vira o ícone do botão;
- o botão aparece **nas cinco visões** de /commission e abre em outra guia;
- trocar o material não perde o anterior, e tirar do ar some com o botão;
- só o SUPERADMIN publica;
- sem informativo, nenhuma tela mostra botão nenhum.

Roda dentro de uma transação desfeita. O que subir para o MinIO é apagado.
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-inf-com'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-inf-com2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.template import Context, Template
from django.test import Client

from users.models import InformativoComissao
from users.templatetags.comissao_tags import limpar_cache
from users.models import Sector

User = get_user_model()
ok = fail = 0
subidos = []
CONFIG = '/users/manage/system-config/'
PUBLICAR = '/users/manage/system-config/informativo-comissao/'
VISOES = ('users/commission.html', 'users/commission_aparte.html',
          'users/commission_superadmin.html', 'users/commission_gerente.html',
          'users/commission_coordenador.html')


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def botao():
    """O que o partial do botão desenha agora (é o mesmo nas cinco visões)."""
    limpar_cache()
    return Template('{% include "users/_informativo_comissao.html" %}').render(Context({}))


marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Setor Informativo')
    chefe = User.objects.create_user(
        username='zzic.chefe', email='zzic.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', sector=setor)
    comum = User.objects.create_user(
        username='zzic.comum', email='zzic.comum@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Comum', hierarchy='PADRAO', sector=setor)

    c = Client(); c.force_login(chefe)
    cc = Client(); cc.force_login(comum)

    print('== SEM INFORMATIVO ==')
    t('nenhum botão aparece', botao().strip() == '')
    t('e a tela de configuração abre assim mesmo', c.get(CONFIG).status_code == 200)

    print('\n== PUBLICANDO UM PDF ==')
    pdf = SimpleUploadedFile('comissionamento.pdf', b'%PDF-1.4 zz informativo' + bytes(200),
                             content_type='application/pdf')
    r = c.post(PUBLICAR, {'titulo': 'Como funciona o Comissionamento?',
                          'descricao': 'Regras e pilares', 'arquivo': pdf})
    vigente = InformativoComissao.vigente()
    if vigente and vigente.arquivo:
        subidos.append(vigente.arquivo.name)
    t('publica', r.status_code == 302 and vigente is not None)
    t('reconhecendo que é PDF', vigente.tipo == InformativoComissao.Tipo.PDF, vigente.tipo)
    t('e guardando quem publicou', vigente.atualizado_por_id == chefe.id)

    html = botao()
    t('o botão aparece', 'Como funciona o Comissionamento?' in html)
    t('abrindo em outra guia', 'target="_blank"' in html and 'rel="noopener"' in html)
    t('com o ícone de PDF', 'fa-file-pdf' in html)
    t('e apontando para o arquivo', vigente.destino and vigente.destino in html)

    print('\n== NAS CINCO VISÕES ==')
    faltando = [v for v in VISOES
                if '_informativo_comissao.html' not in open('templates/' + v).read()]
    t('todas as visões incluem o informativo', not faltando, faltando)

    print('\n== TROCANDO POR UM LINK ==')
    r = c.post(PUBLICAR, {'titulo': 'Vídeo: como é calculada sua comissão',
                          'link': 'https://exemplo-teste.local/video'})
    vigente = InformativoComissao.vigente()
    t('o link entra no ar', vigente.tipo == InformativoComissao.Tipo.LINK
      and vigente.destino == 'https://exemplo-teste.local/video', vigente.destino)
    t('o anterior fica guardado, desligado',
      InformativoComissao.objects.count() == 2
      and InformativoComissao.objects.filter(ativo=True).count() == 1)
    html = botao()
    t('o botão mostra o texto novo', 'Vídeo: como é calculada sua comissão' in html)
    t('com o ícone de link', 'fa-up-right-from-square' in html)

    print('\n== IMAGEM E VÍDEO ==')
    img = SimpleUploadedFile('tabela.png', b'\x89PNG\r\n\x1a\n' + bytes(300), content_type='image/png')
    c.post(PUBLICAR, {'titulo': 'Tabela de comissão', 'arquivo': img})
    vigente = InformativoComissao.vigente()
    if vigente.arquivo:
        subidos.append(vigente.arquivo.name)
    t('imagem é reconhecida', vigente.tipo == InformativoComissao.Tipo.IMAGEM)
    t('e o ícone muda', 'fa-image' in botao())

    video = SimpleUploadedFile('explicacao.mp4', bytes(400), content_type='video/mp4')
    c.post(PUBLICAR, {'titulo': 'Explicação em vídeo', 'arquivo': video})
    vigente = InformativoComissao.vigente()
    if vigente.arquivo:
        subidos.append(vigente.arquivo.name)
    t('vídeo é reconhecido', vigente.tipo == InformativoComissao.Tipo.VIDEO)
    t('e o ícone também', 'fa-circle-play' in botao())

    print('\n== QUEM PODE ==')
    r = cc.post(PUBLICAR, {'titulo': 'ZZ não deveria', 'link': 'https://exemplo-teste.local/x'})
    t('quem não é SUPERADMIN não publica',
      r.status_code == 302 and not InformativoComissao.objects.filter(
          titulo='ZZ não deveria').exists())
    t('e nem vê a tela de configuração', cc.get(CONFIG).status_code == 302)

    print('\n== TIRANDO DO AR ==')
    c.post(PUBLICAR, {'acao': 'remover'})
    t('o informativo sai', InformativoComissao.vigente() is None)
    t('e o botão some das telas', botao().strip() == '')
    t('sem apagar o histórico', InformativoComissao.objects.count() == 4)

    c.post(PUBLICAR, {'titulo': 'ZZ sem nada'})
    t('publicar sem arquivo e sem link é recusado',
      not InformativoComissao.objects.filter(titulo='ZZ sem nada').exists())
finally:
    apagados = 0
    from django.core.files.storage import default_storage
    for nome in subidos:
        try:
            if default_storage.exists(nome):
                default_storage.delete(nome)
                apagados += 1
        except Exception as exc:                                  # noqa: BLE001
            print(f'  ATENÇÃO: não deu para apagar {nome}: {exc}')
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print(f'\nrollback: nada gravado no banco; {apagados} arquivo(s) apagados do MinIO.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
