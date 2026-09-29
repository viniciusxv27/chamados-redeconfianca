"""Impulso: qualquer SUPERADMIN avalia e aprova a atividade de qualquer um.

Pedido (29/09/2026): "em /impulso/metas/{id}/ qualquer SUPERADMIN deve poder
fazer a 'Avaliação do gestor' e aprovar".

Quem decide isso no portal é a **hierarquia** SUPERADMIN — e nem todo SUPERADMIN
tem o `is_superuser` do Django, que era o que o código olhava. Na prática, um
SUPERADMIN sem esse flag nem abria a atividade dos outros.

O que este teste cobre:

- SUPERADMIN por hierarquia abre a atividade de qualquer pessoa;
- vê o formulário da avaliação e grava as duas notas, concluindo a atividade;
- aprova (e recusa) uma solicitação parada, mesmo não sendo o gestor dela;
- o `is_superuser` continua valendo (não troquei uma regra pela outra);
- quem não é nem gestor nem SUPERADMIN continua de fora — da tela e do POST.

Roda contra o Postgres, em transação desfeita. Nada é gravado.
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sup'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sup-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from communications.models import CommunicationGroup
from impulso.models import Meta
from users.models import Sector

User = get_user_model()
ok = fail = 0


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
    loja = Sector.objects.create(name='ZZ Loja do Superadmin')
    outra = Sector.objects.create(name='ZZ Loja de Fora')

    # SUPERADMIN do portal: hierarquia, SEM o is_superuser do Django.
    chefe = User.objects.create_user(
        username='zzs.chefe', email='zzs.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZS', last_name='Chefe', hierarchy='SUPERADMIN', sector=outra)
    t('o cenário é o que interessa: SUPERADMIN sem is_superuser',
      chefe.hierarchy == 'SUPERADMIN' and not chefe.is_superuser)

    gestor = User.objects.create_user(
        username='zzs.gestor', email='zzs.gestor@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZS', last_name='Gestor', sector=loja)
    colaborador = User.objects.create_user(
        username='zzs.colab', email='zzs.colab@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZS', last_name='Colaborador', sector=loja)
    estranho = User.objects.create_user(
        username='zzs.estranho', email='zzs.estranho@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZS', last_name='Estranho', sector=outra)
    root = User.objects.create_user(
        username='zzs.root', email='zzs.root@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZS', last_name='Root', is_superuser=True, sector=outra)

    adm = CommunicationGroup.objects.create(name="ADM's LOJAS", created_by=gestor)
    gestores = CommunicationGroup.objects.create(name='GESTORES (IMPULSO)', created_by=gestor)
    for pessoa in (chefe, gestor, colaborador, estranho, root):
        pessoa.communication_groups.add(adm)
    gestor.communication_groups.add(gestores)

    prazo = timezone.localdate() + timedelta(days=5)
    meta = Meta.objects.create(
        gestor=gestor, colaborador=colaborador, titulo='ZZ Atividade para avaliar',
        descricao='ZZ descrição', prazo=prazo, created_by=gestor,
        status=Meta.Status.ENTREGUE, entregue_em=timezone.now())

    c_chefe = Client(); c_chefe.force_login(chefe)
    c_root = Client(); c_root.force_login(root)
    c_estranho = Client(); c_estranho.force_login(estranho)
    url = f'/impulso/metas/{meta.id}/'

    print('== O SUPERADMIN ABRE E AVALIA ==')
    r = c_chefe.get(url)
    t('abre a atividade de qualquer pessoa', r.status_code == 200, r.status_code)
    html = r.content.decode()
    t('e vê o formulário da avaliação',
      'Avaliação do gestor' in html and 'name="nota_qualidade"' in html
      and 'Dar o check e concluir' in html)

    c_chefe.post(f'/impulso/metas/{meta.id}/avaliar/', {
        'nota_qualidade': '5', 'nota_prazo': '4',
        'avaliacao_comentario': 'ZZ entregou bem'}, follow=True)
    meta.refresh_from_db()
    t('as notas são gravadas', meta.nota_qualidade == 5 and meta.nota_prazo == 4,
      (meta.nota_qualidade, meta.nota_prazo))
    t('com o comentário', meta.avaliacao_comentario == 'ZZ entregou bem')
    t('a atividade é concluída', meta.status == Meta.Status.CONCLUIDA, meta.status)
    t('e fica registrado quem avaliou', meta.avaliado_por_id == chefe.id and meta.avaliado_em)

    print('\n== E APROVA O QUE ESTÁ PARADO ==')
    pedido = Meta.objects.create(
        gestor=gestor, colaborador=colaborador, titulo='ZZ Solicitação parada',
        descricao='ZZ', prazo=prazo, created_by=colaborador, solicitada_por=colaborador,
        aprovacao=Meta.Aprovacao.PENDENTE)
    t('o modelo deixa o SUPERADMIN decidir', pedido.pode_decidir(chefe))
    t('e o superuser do Django também', pedido.pode_decidir(root))
    t('mas não quem não tem nada a ver', not pedido.pode_decidir(estranho))
    t('nem quem pediu', not pedido.pode_decidir(colaborador))

    html = c_chefe.get(f'/impulso/metas/{pedido.id}/').content.decode()
    t('a tela oferece o Aprovar', 'Aprovar' in html and 'meta_decidir' in html or 'decidir' in html)

    c_chefe.post(f'/impulso/metas/{pedido.id}/decidir/', {'decisao': 'aprovar'}, follow=True)
    pedido.refresh_from_db()
    t('aprovar funciona', pedido.aprovacao == Meta.Aprovacao.APROVADA, pedido.aprovacao)
    t('registrando quem decidiu', pedido.decidida_por_id == chefe.id)

    recusado = Meta.objects.create(
        gestor=gestor, colaborador=colaborador, titulo='ZZ Outra solicitação',
        descricao='ZZ', prazo=prazo, created_by=colaborador, solicitada_por=colaborador,
        aprovacao=Meta.Aprovacao.PENDENTE)
    c_chefe.post(f'/impulso/metas/{recusado.id}/decidir/',
                 {'decisao': 'recusar', 'motivo_recusa': 'ZZ fora do escopo'}, follow=True)
    recusado.refresh_from_db()
    t('recusar também', recusado.aprovacao == Meta.Aprovacao.RECUSADA, recusado.aprovacao)

    print('\n== QUEM NÃO É, CONTINUA NÃO SENDO ==')
    r = c_estranho.get(url, follow=True)
    t('quem não tem relação com a atividade não abre',
      'sem permissão' in r.content.decode().lower()
      or 'não tem' in r.content.decode().lower()
      or r.redirect_chain, r.status_code)

    nova = Meta.objects.create(
        gestor=gestor, colaborador=colaborador, titulo='ZZ Terceira atividade',
        descricao='ZZ', prazo=prazo, created_by=gestor,
        status=Meta.Status.ENTREGUE, entregue_em=timezone.now())
    c_estranho.post(f'/impulso/metas/{nova.id}/avaliar/',
                    {'nota_qualidade': '5', 'nota_prazo': '5'}, follow=True)
    nova.refresh_from_db()
    t('e não avalia pelo POST', nova.nota_qualidade is None, nova.nota_qualidade)

    c_root.post(f'/impulso/metas/{nova.id}/avaliar/',
                {'nota_qualidade': '3', 'nota_prazo': '3'}, follow=True)
    nova.refresh_from_db()
    t('o superuser do Django continua avaliando', nova.nota_qualidade == 3, nova.nota_qualidade)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
