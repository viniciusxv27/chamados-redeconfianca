"""Impulso/Inovar: a tela escondida que libera quem vê a autoria das ideias.

Pedido: "em /impulso/inovar/ — deve criar um /impulso/inovar/adm (escondido),
onde SUPERADMIN deve poder dar acesso a quem pode ver quem deu cada ideia".

A ideia é avaliada sem autor de propósito: o gestor lê o que foi escrito, não
quem escreveu. Agora o SUPERADMIN pode abrir a autoria para uma pessoa de cada
vez, numa tela que não aparece em menu nenhum — e quem não é SUPERADMIN nem
descobre que ela existe (404, não "sem permissão").

O que este teste cobre:

- quem abre a tela: SUPERADMIN entra; gestor do Impulso e colaborador comum
  recebem 404, e um POST deles não muda nada;
- a tela não é citada em link nenhum do módulo;
- liberar com motivo, a lista dizendo quem liberou e quando, liberar de novo
  atualizando em vez de duplicar, e o aviso de que SUPERADMIN já vê;
- o efeito no /impulso/inovar/: quem foi liberado passa a ver todas as ideias
  COM o nome de quem enviou (e quem participou); o gestor comum continua com
  "Autor não identificado" e o colaborador continua vendo só as dele;
- tirar o acesso fecha tudo de novo.

Roda dentro de uma transação desfeita: não grava nada.
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-inv-adm'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-inv-adm-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from communications.models import CommunicationGroup
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, AcessoAutoriaIdeia, Ideia
from users.models import Sector

User = get_user_model()
ok = fail = 0
ADM = '/impulso/inovar/adm/'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Loja das ideias')
    chefe = User.objects.create_user(
        username='zzia.chefe', email='zzia.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)
    adm, _ = CommunicationGroup.objects.get_or_create(name=GRUPO_ADM, defaults={'created_by': chefe})
    ges, _ = CommunicationGroup.objects.get_or_create(name=GRUPO_GESTOR, defaults={'created_by': chefe})

    def novo(apelido, grupos=(), **kw):
        pessoa = User.objects.create_user(
            username=f'zzia.{apelido}', email=f'zzia.{apelido}@exemplo-teste.local',
            password='S3nha!teste', first_name='ZZ', last_name=apelido.title(),
            sector=setor, **kw)
        for grupo in grupos:
            pessoa.communication_groups.add(grupo)
        return pessoa

    gestor = novo('gestor', [ges])
    rh = novo('rh', [adm])          # do módulo, mas não gestor
    autora = novo('autora', [adm])
    colega = novo('colega', [adm])

    ideia = Ideia.objects.create(
        autor=autora, descricao='ZZ Trocar a etiqueta da vitrine',
        setor_impacto='Loja', motivo='ZZ para vender mais')
    ideia.participantes.add(colega)
    Ideia.objects.create(autor=colega, descricao='ZZ Outra ideia',
                         setor_impacto='Estoque', motivo='ZZ organizar')

    cc = Client(); cc.force_login(chefe)
    cg = Client(); cg.force_login(gestor)
    cr = Client(); cr.force_login(rh)
    ca = Client(); ca.force_login(autora)

    print('== A TELA É ESCONDIDA ==')
    r = cc.get(ADM)
    t('o SUPERADMIN abre', r.status_code == 200, r.status_code)
    t('gestor do Impulso não abre (404, não "sem permissão")',
      cg.get(ADM).status_code == 404, cg.get(ADM).status_code)
    t('colaborador do módulo também não', cr.get(ADM).status_code == 404)
    t('e nem a autora', ca.get(ADM).status_code == 404)

    r = cg.post(ADM, {'pessoa': gestor.id})
    t('POST de quem não é SUPERADMIN não passa', r.status_code == 404, r.status_code)
    t('e não libera ninguém', not AcessoAutoriaIdeia.objects.exists())

    html = cg.get('/impulso/inovar/').content.decode()
    t('a tela do Inovar não tem link para a escondida', '/impulso/inovar/adm' not in html)
    t('nem a tela do SUPERADMIN', '/impulso/inovar/adm' not in cc.get('/impulso/inovar/').content.decode())

    print('\n== ANTES DE LIBERAR ==')
    r = cg.get('/impulso/inovar/')
    html = r.content.decode()
    t('o gestor vê as duas ideias', len(r.context['ideias']) == 2, len(r.context['ideias']))
    t('mas sem o nome de quem enviou', 'Autor não identificado' in html)
    t('e o nome da autora não aparece', autora.full_name not in html, autora.full_name)

    r = ca.get('/impulso/inovar/')
    t('a autora vê só a dela e a que participa',
      len(r.context['ideias']) == 1, len(r.context['ideias']))

    print('\n== O SUPERADMIN LIBERA ==')
    r = cc.post(ADM, {'pessoa': rh.id, 'motivo': 'premiação do trimestre'}, follow=True)
    acesso = AcessoAutoriaIdeia.objects.filter(user=rh).first()
    t('a pessoa fica liberada', acesso is not None)
    t('com o motivo', acesso.motivo == 'premiação do trimestre', acesso.motivo if acesso else None)
    t('e o registro de quem liberou', acesso.liberado_por_id == chefe.id)
    t('a tela confirma', 'passa a ver de quem é cada ideia' in r.content.decode())
    html = r.content.decode()
    t('a lista mostra a pessoa e o motivo', rh.full_name in html and 'premiação do trimestre' in html)
    t('e quem liberou', chefe.full_name in html)

    r = cc.post(ADM, {'pessoa': rh.id, 'motivo': 'apuração da campanha'}, follow=True)
    acesso.refresh_from_db()
    t('liberar de novo atualiza em vez de duplicar',
      AcessoAutoriaIdeia.objects.filter(user=rh).count() == 1
      and acesso.motivo == 'apuração da campanha', acesso.motivo)

    r = cc.post(ADM, {'pessoa': chefe.id}, follow=True)
    t('SUPERADMIN não precisa de liberação',
      not AcessoAutoriaIdeia.objects.filter(user=chefe).exists()
      and 'já vê a autoria' in r.content.decode())

    r = cc.post(ADM, {'pessoa': ''}, follow=True)
    t('sem escolher pessoa, avisa', 'Escolha uma pessoa ativa' in r.content.decode())

    print('\n== O EFEITO NO INOVAR ==')
    r = cr.get('/impulso/inovar/')
    html = r.content.decode()
    t('quem foi liberado passa a ver todas as ideias',
      len(r.context['ideias']) == 2, len(r.context['ideias']))
    t('com o nome de quem enviou', autora.full_name in html and colega.full_name in html)
    t('a tela diz que é com identificação', 'com identificação' in html)
    t('e mostra quem mais participou da ideia', 'Com ZZ' in html or colega.first_name in html)
    t('o contexto marca que ela vê a autoria', r.context['ve_autoria'] is True)

    html = cg.get('/impulso/inovar/').content.decode()
    t('o gestor que não foi liberado continua sem o nome',
      'Autor não identificado' in html and autora.full_name not in html)

    html = ca.get('/impulso/inovar/').content.decode()
    t('a autora continua vendo só a dela', len(ca.get('/impulso/inovar/').context['ideias']) == 1)

    print('\n== TIRAR O ACESSO ==')
    r = cc.post(ADM, {'acao': 'remover', 'acesso': acesso.id}, follow=True)
    t('o acesso some', not AcessoAutoriaIdeia.objects.filter(user=rh).exists())
    t('a tela confirma', 'não vê mais de quem são as ideias' in r.content.decode())
    r = cr.get('/impulso/inovar/')
    html = r.content.decode()
    t('e o nome some da tela dela', autora.full_name not in html, autora.full_name)
    t('ela volta a ver só as ideias dela (que são nenhuma)',
      len(r.context['ideias']) == 0 and r.context['ve_autoria'] is False,
      len(r.context['ideias']))
    html = cg.get('/impulso/inovar/').content.decode()
    t('e o gestor segue vendo as ideias sem autor',
      'Autor não identificado' in html and autora.full_name not in html)

    r = cc.post(ADM, {'acao': 'remover', 'acesso': 999999}, follow=True)
    t('remover o que não existe não quebra', 'já não existe' in r.content.decode())

    print('\n== O SUPERADMIN VÊ SEMPRE ==')
    r = cc.get('/impulso/inovar/')
    html = r.content.decode()
    t('sem precisar de linha nenhuma na lista',
      r.context['ve_autoria'] is True and autora.full_name in html)
    t('e a regra do modelo concorda',
      AcessoAutoriaIdeia.pode_ver(chefe) and not AcessoAutoriaIdeia.pode_ver(gestor))
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
