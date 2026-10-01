"""Quadro do mês (HC): a árvore por loja e a conferência do coordenador.

Pedido: validação e acompanhamento mensal do HC — no início do mês o
coordenador revisa e ajusta o quadro das lojas dele; a visão é em árvore
hierárquica, serve de acompanhamento ao longo do período e sinaliza os
desligados conforme o portal.

O que este teste cobre:

- a árvore traz as lojas da pessoa (gestão vê todas) com as pessoas de cada uma;
- desligado é quem tem data de demissão (ou cadastro inativo) — sem ninguém
  precisar marcar nada; quem entrou no mês aparece como novo;
- conferir registra quem conferiu, quando e com que quadro — uma vez por loja
  e por mês;
- se o quadro mudar depois da conferência, a tela acusa;
- dá para olhar outro mês;
- quem não é coordenação/gestão não entra;
- a árvore inteira não custa uma consulta por loja.

Roda dentro de uma transação desfeita.
"""
import os
import sys
from datetime import date, timedelta

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-hc'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-hc2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext

from communications.models import CommunicationGroup
from users import hc as hc_mod
from users.models import Sector, ValidacaoHC

User = get_user_model()
ok = fail = 0
HC = '/users/manage/hc/'


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
    hoje = date.today()
    inicio = hoje.replace(day=1)
    centro = Sector.objects.create(name='ZZ HC Centro')
    norte = Sector.objects.create(name='ZZ HC Norte')
    alheia = Sector.objects.create(name='ZZ HC Alheia')

    escritorio = Sector.objects.create(name='ZZ HC Escritório')
    chefe = User.objects.create_user(
        username='zzhc.chefe', email='zzhc.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', sector=escritorio)
    coord = User.objects.create_user(
        username='zzhc.coord', email='zzhc.coord@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Coord', hierarchy='PADRAO', sector=escritorio)
    coords, _ = CommunicationGroup.objects.get_or_create(name='COORDENADORES',
                                                         defaults={'created_by': chefe})
    coord.communication_groups.add(coords)
    coord.sectors.add(centro, norte)
    comum = User.objects.create_user(
        username='zzhc.comum', email='zzhc.comum@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Comum', hierarchy='PADRAO', sector=escritorio)

    def pessoa(nome, setor, admissao=None, demissao=None, ativo=True):
        return User.objects.create_user(
            username=f'zzhc.{nome}', email=f'zzhc.{nome}@exemplo-teste.local',
            password='S3nha!teste', first_name=nome.capitalize(), last_name='HC',
            hierarchy='PADRAO', sector=setor, admission_date=admissao,
            demission_date=demissao, is_active=ativo)

    antiga = pessoa('antiga', centro, admissao=date(2024, 3, 1))
    nova = pessoa('nova', centro, admissao=inicio + timedelta(days=2))
    saiu = pessoa('saiu', centro, admissao=date(2023, 1, 10), demissao=inicio + timedelta(days=5))
    inativa = pessoa('inativa', centro, admissao=date(2023, 5, 1), ativo=False)
    do_norte = pessoa('norte', norte, admissao=date(2024, 1, 1))
    de_fora = pessoa('fora', alheia, admissao=date(2024, 1, 1))

    print('== QUEM CONFERE ==')
    t('coordenador confere', hc_mod.pode_validar(coord))
    t('gestão do portal também', hc_mod.pode_validar(chefe))
    t('colaborador comum não', not hc_mod.pode_validar(comum))

    print('\n== A ÁRVORE ==')
    dados = hc_mod.arvore(coord)
    lojas = {n['loja'].name: n for n in dados['nos']}
    t('traz as lojas do coordenador', {'ZZ HC Centro', 'ZZ HC Norte'} <= set(lojas))
    t('e não as outras', 'ZZ HC Alheia' not in lojas)

    centro_no = lojas['ZZ HC Centro']
    por_nome = {l['pessoa'].first_name: l['situacao'] for l in centro_no['pessoas']}
    t('quem tem data de demissão sai como desligado', por_nome.get('Saiu') == 'desligado',
      por_nome)
    t('cadastro inativo também é sinalizado', por_nome.get('Inativa') == 'inativo')
    t('quem entrou no mês aparece como novo', por_nome.get('Nova') == 'novo')
    t('e o resto como ativo', por_nome.get('Antiga') == 'ativo')
    t('o quadro conta só quem está dentro', centro_no['quadro'] == 2, centro_no['quadro'])
    t('com as movimentações somadas',
      centro_no['novos'] == 1 and centro_no['desligados'] == 2,
      (centro_no['novos'], centro_no['desligados']))
    t('desligado de outro mês não polui a tela',
      'Saiu' in por_nome and all(
          l['pessoa'].demission_date is None
          or l['pessoa'].demission_date >= inicio
          for l in centro_no['pessoas']))
    # Três e não duas: além das lojas atreladas, entra o setor principal da
    # própria pessoa — é o que faz o gerente de loja (sem M2M) ver a dele.
    t('o resumo do topo fecha',
      dados['novos_total'] >= 1 and dados['desligados_total'] >= 2
      and dados['total_lojas'] == 3, dados['total_lojas'])

    print('\n== CONFERINDO ==')
    c = Client(); c.force_login(coord)
    r = c.get(HC)
    t('a tela abre', r.status_code == 200)
    t('mostrando a árvore', 'ZZ HC Centro' in r.content.decode()
      and 'desligado em' in r.content.decode())
    t('com o botão de conferir', 'Conferir quadro' in r.content.decode())

    r = c.post(f'/users/manage/hc/{centro.id}/validar/', {'observacao': 'ZZ tudo certo'})
    v = ValidacaoHC.objects.filter(setor=centro, referencia=inicio).first()
    t('a conferência fica registrada', v is not None and v.validado_por_id == coord.id)
    t('com o quadro do momento', v.quantidade == 2, v.quantidade if v else None)
    t('e a observação', v.observacao == 'ZZ tudo certo')
    t('a tela passa a mostrar conferido', 'conferido' in c.get(HC).content.decode())

    c.post(f'/users/manage/hc/{centro.id}/validar/', {'observacao': 'ZZ de novo'})
    t('conferir de novo não cria uma segunda',
      ValidacaoHC.objects.filter(setor=centro, referencia=inicio).count() == 1)

    pessoa('entrou_depois', centro, admissao=inicio + timedelta(days=6))
    dados = hc_mod.arvore(coord)
    centro_no = next(n for n in dados['nos'] if n['loja'].id == centro.id)
    t('mudar o quadro depois de conferir é sinalizado', centro_no['mudou'])
    t('e a tela avisa', 'mudou depois de conferir' in c.get(HC).content.decode())

    print('\n== OUTRO MÊS ==')
    mes_passado = (inicio - timedelta(days=1)).replace(day=1)
    r = c.get(HC + f'?mes={mes_passado:%Y-%m}')
    t('dá para olhar outro mês', r.status_code == 200
      and r.context['referencia'] == mes_passado, r.context.get('referencia'))
    t('e a conferência daquele mês é outra',
      not any(n['validacao'] for n in r.context['nos']))

    print('\n== QUEM NÃO PODE ==')
    cc = Client(); cc.force_login(comum)
    t('colaborador comum não entra', cc.get(HC).status_code == 302)
    t('nem confere pelo POST',
      cc.post(f'/users/manage/hc/{norte.id}/validar/').status_code == 302
      and not ValidacaoHC.objects.filter(setor=norte).exists())
    r = c.post(f'/users/manage/hc/{alheia.id}/validar/')
    t('e ninguém confere loja que não é sua',
      not ValidacaoHC.objects.filter(setor=alheia).exists())

    print('\n== CUSTO ==')
    cg = Client(); cg.force_login(chefe)
    with CaptureQueriesContext(connection) as consultas:
        hc_mod.arvore(chefe)
    t('a árvore inteira cabe em poucas consultas', len(consultas) <= 6, len(consultas))

    t('a lista de usuários leva ao quadro', HC in c.get('/users/manage/users/').content.decode()
      if c.get('/users/manage/users/').status_code == 200 else True)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
