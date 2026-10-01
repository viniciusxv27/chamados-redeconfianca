"""Impulso/Inovar: decidir várias ideias de uma vez e filtrar a lista.

Pedidos:

1. "Ter a possibilidade de selecionar se a ideia vai seguir e de quem vai ser o
   ponto focal e no final de tudo aprovar conforme selecionado, não fazer de
   uma em uma";
2. "Possibilidade de filtro nas ideias".

O que este teste cobre:

- decidir em lote: aprova com ponto focal e prazo, recusa, e deixa em branco o
  que não foi decidido;
- a ideia aprovada no lote abre a atividade do executor, igual à decisão uma a
  uma (é a mesma função por baixo);
- o que não passa na conferência não derruba as outras decisões, e a tela diz o
  que faltou;
- filtro por situação, por setor de impacto e pela ordem;
- o filtro não devolve a autoria — não há busca por nome aqui.

Roda dentro de uma transação desfeita.
"""
import os
import sys
from datetime import timedelta

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-inv-lote'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-inv-lote2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from communications.models import CommunicationGroup
from impulso.models import Ideia, Meta
from users.models import Sector

User = get_user_model()
ok = fail = 0
LOTE = '/impulso/inovar/decidir-lote/'
LISTA = '/impulso/inovar/'


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
    setor = Sector.objects.create(name='ZZ Setor Inovar Lote')
    chefe = User.objects.create_user(
        username='zzil.chefe', email='zzil.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True,
        sector=setor)
    adm, _ = CommunicationGroup.objects.get_or_create(name='ESCRITÓRIO (ADM)',
                                                      defaults={'created_by': chefe})
    gestores, _ = CommunicationGroup.objects.get_or_create(name='GESTORES (IMPULSO)',
                                                           defaults={'created_by': chefe})
    chefe.communication_groups.add(adm, gestores)

    def pessoa(nome):
        u = User.objects.create_user(
            username=f'zzil.{nome}', email=f'zzil.{nome}@exemplo-teste.local',
            password='S3nha!teste', first_name=nome.capitalize(), last_name='Inovar',
            hierarchy='PADRAO', sector=setor)
        u.communication_groups.add(adm)
        return u

    ana, bruno, carla = pessoa('ana'), pessoa('bruno'), pessoa('carla')

    def ideia(autor, impacto, status=Ideia.Status.NOVA):
        return Ideia.objects.create(autor=autor, descricao=f'ZZ ideia de {autor.first_name}',
                                    setor_impacto=impacto, motivo='ZZ motivo', status=status)

    i1 = ideia(ana, 'Loja Centro')
    i2 = ideia(bruno, 'Loja Centro')
    i3 = ideia(carla, 'Financeiro')
    i4 = ideia(ana, 'Financeiro')
    i5 = ideia(bruno, 'Logística', status=Ideia.Status.APROVADA)

    c = Client(); c.force_login(chefe)
    prazo = (timezone.localdate() + timedelta(days=20)).strftime('%Y-%m-%d')

    print('== DECIDIR EM LOTE ==')
    r = c.post(LOTE, {
        'ideia': [str(i1.id), str(i2.id), str(i3.id)],
        f'decisao_{i1.id}': 'segue', f'executor_{i1.id}': str(ana.id), f'prazo_{i1.id}': prazo,
        f'decisao_{i2.id}': 'nao', f'resposta_{i2.id}': 'ZZ não faz sentido agora',
        f'decisao_{i3.id}': 'segue', f'executor_{i3.id}': str(bruno.id), f'prazo_{i3.id}': prazo,
    })
    for i in (i1, i2, i3, i4):
        i.refresh_from_db()
    t('o lote responde', r.status_code == 302)
    t('aprova as marcadas como "vai seguir"',
      i1.status == Ideia.Status.APROVADA and i3.status == Ideia.Status.APROVADA,
      (i1.status, i3.status))
    t('arquiva a marcada como "não vai seguir"', i2.status == Ideia.Status.ARQUIVADA)
    t('guardando o retorno ao autor', i2.resposta_gestor == 'ZZ não faz sentido agora')
    t('e não mexe na que ficou de fora', i4.status == Ideia.Status.NOVA)

    t('o ponto focal vai para a ideia', i1.executor_id == ana.id and i3.executor_id == bruno.id)
    t('e a atividade do executor é criada',
      i1.meta_gerada_id and i3.meta_gerada_id
      and Meta.objects.filter(id=i1.meta_gerada_id, colaborador=ana,
                              aprovacao=Meta.Aprovacao.APROVADA).exists())
    t('com o prazo escolhido',
      Meta.objects.get(id=i3.meta_gerada_id).prazo.strftime('%Y-%m-%d') == prazo)

    print('\n== O QUE NÃO PASSA NÃO DERRUBA O RESTO ==')
    i6, i7 = ideia(carla, 'Atendimento'), ideia(ana, 'Atendimento')
    r = c.post(LOTE, {
        'ideia': [str(i6.id), str(i7.id)],
        f'decisao_{i6.id}': 'segue',                      # sem executor nem prazo
        f'decisao_{i7.id}': 'segue', f'executor_{i7.id}': str(carla.id), f'prazo_{i7.id}': prazo,
    })
    i6.refresh_from_db(); i7.refresh_from_db()
    t('a que faltou executor fica como estava', i6.status == Ideia.Status.NOVA)
    t('e a completa é aprovada assim mesmo', i7.status == Ideia.Status.APROVADA)
    recado = ' '.join(str(m) for m in r.wsgi_request._messages) if hasattr(r.wsgi_request, '_messages') else ''
    html = c.get(LISTA).content.decode()
    t('a tela avisa o que ficou sem decidir', 'Ficaram sem decidir' in html, html[:0])

    i8 = ideia(carla, 'Atendimento')
    passado = (timezone.localdate() - timedelta(days=5)).strftime('%Y-%m-%d')
    c.post(LOTE, {'ideia': [str(i8.id)], f'decisao_{i8.id}': 'segue',
                  f'executor_{i8.id}': str(ana.id), f'prazo_{i8.id}': passado})
    i8.refresh_from_db()
    t('prazo no passado é recusado', i8.status == Ideia.Status.NOVA)

    c.post(LOTE, {'ideia': [str(i4.id)]})
    i4.refresh_from_db()
    t('marcar sem escolher a decisão não decide nada', i4.status == Ideia.Status.NOVA)

    print('\n== FILTROS ==')
    # O banco de dev tem as ideias reais junto: o teste olha só as suas.
    minhas = {i1.id, i2.id, i3.id, i4.id, i5.id, i6.id, i7.id, i8.id}

    def ids(resposta):
        return {i.id for i in resposta.context['ideias']} & minhas

    def ordem(resposta):
        return [i.id for i in resposta.context['ideias'] if i.id in minhas]

    todas = ids(c.get(LISTA))
    t('sem filtro, vêm todas', {i1.id, i2.id, i3.id, i4.id, i5.id} <= todas)
    t('por situação: só as novas',
      all(i.status == Ideia.Status.NOVA for i in c.get(LISTA + '?status=NOVA').context['ideias']))
    t('por situação: só as aprovadas',
      {i.id for i in c.get(LISTA + '?status=APROVADA').context['ideias']} >= {i1.id, i3.id, i5.id})
    t('por setor de impacto', ids(c.get(LISTA + '?impacto=Financeiro')) == {i3.id, i4.id},
      ids(c.get(LISTA + '?impacto=Financeiro')))
    t('o impacto não precisa ser exato',
      ids(c.get(LISTA + '?impacto=financ')) == {i3.id, i4.id})
    t('status inválido não quebra nem filtra',
      ids(c.get(LISTA + '?status=BOBAGEM')) == todas)

    recentes = ordem(c.get(LISTA))
    antigas = ordem(c.get(LISTA + '?ordem=antigas'))
    t('a ordem inverte quando pedida', recentes == list(reversed(antigas)),
      (recentes[:3], antigas[:3]))

    html = c.get(LISTA + '?status=NOVA').content.decode()
    t('a tela mostra o filtro escolhido', 'name="status"' in html and 'name="impacto"' in html)
    t('e oferece limpar', 'Limpar' in html)
    t('os setores já usados viram sugestão', 'impactosUsados' in html and 'Financeiro' in html)
    t('não há busca por nome (a avaliação é sem autoria)',
      'name="nome"' not in html and 'name="q"' not in html)

    print('\n== QUEM PODE ==')
    ca = Client(); ca.force_login(ana)
    r = ca.post(LOTE, {'ideia': [str(i4.id)], f'decisao_{i4.id}': 'segue',
                       f'executor_{i4.id}': str(ana.id), f'prazo_{i4.id}': prazo})
    i4.refresh_from_db()
    t('quem não é gestor não decide em lote', i4.status == Ideia.Status.NOVA)
    t('e não vê a barra do lote', 'formLote' not in ca.get(LISTA).content.decode())
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
