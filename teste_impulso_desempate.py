"""Impulso: o critério de desempate do ranking de /impulso/acompanhamento/.

Pedido: "O critério de desempate da tabela deve ser: 1- Assiduidade (menos
ajustes de ponto), 2- maior quantidade de metas concluídas do CONFIAR e
3- maior quantidade de ideias aprovadas".

Antes, o ranking ordenava só pelo percentual e quem empatava caía na ordem em
que o banco devolveu — a mesma tela podia trocar duas pessoas de lugar entre
dois carregamentos.

O que este teste cobre:

- a ordem dos três critérios, um a um e combinados;
- quem não tem ponto sincronizado (ajustes desconhecidos) não é punido: vale
  zero ajuste e o desempate segue para o critério seguinte;
- a ordem é estável — tudo empatado, decide o nome;
- os números do desempate saem do mesmo cálculo da pontuação (metas
  concluídas do Confiar e ideias aprovadas do Inovar);
- a tela mostra a coluna e explica a regra no rodapé.

Roda dentro de uma transação desfeita.
"""
import os
import pathlib
import sys
from datetime import timedelta
from decimal import Decimal

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-imp-emp'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-imp-emp2'},
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
from impulso import scoring
from impulso.models import Ideia, Meta
from impulso.scoring import calcular_pontuacao, chave_do_ranking, ordenar_ranking
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


def linha(nome, percentual, ajustes=None, metas=0, ideias=0):
    return {'nome': nome,
            'dados': {'percentual': Decimal(str(percentual)),
                      'desempate': {'ajustes': ajustes, 'metas_concluidas': metas,
                                    'ideias_aprovadas': ideias}}}


def ordem(linhas):
    postas = ordenar_ranking(linhas, dados=lambda l: l['dados'], nome=lambda l: l['nome'])
    return [l['nome'] for l in postas]


marcador = transaction.atomic()
marcador.__enter__()
try:
    print('== A ORDEM DOS TRÊS CRITÉRIOS ==')
    t('a pontuação vem antes de tudo',
      ordem([linha('B', 80, ajustes=0, metas=9, ideias=9),
             linha('A', 90, ajustes=9, metas=0, ideias=0)]) == ['A', 'B'])

    t('1º empate: quem fez menos ajustes de ponto fica na frente',
      ordem([linha('Muitos ajustes', 85, ajustes=4, metas=9, ideias=9),
             linha('Poucos ajustes', 85, ajustes=1, metas=0, ideias=0)])
      == ['Poucos ajustes', 'Muitos ajustes'])

    t('2º empate: com os mesmos ajustes, mais metas concluídas',
      ordem([linha('Menos metas', 85, ajustes=2, metas=1, ideias=9),
             linha('Mais metas', 85, ajustes=2, metas=4, ideias=0)])
      == ['Mais metas', 'Menos metas'])

    t('3º empate: mesmos ajustes e mesmas metas, mais ideias aprovadas',
      ordem([linha('Menos ideias', 85, ajustes=2, metas=3, ideias=0),
             linha('Mais ideias', 85, ajustes=2, metas=3, ideias=2)])
      == ['Mais ideias', 'Menos ideias'])

    t('os três juntos, em ordem',
      ordem([linha('Quarta', 85, ajustes=3, metas=5, ideias=5),
             linha('Terceira', 85, ajustes=1, metas=1, ideias=0),
             linha('Segunda', 85, ajustes=1, metas=2, ideias=0),
             linha('Primeira', 85, ajustes=1, metas=2, ideias=1)])
      == ['Primeira', 'Segunda', 'Terceira', 'Quarta'],
      ordem([linha('Quarta', 85, ajustes=3, metas=5, ideias=5),
             linha('Terceira', 85, ajustes=1, metas=1, ideias=0),
             linha('Segunda', 85, ajustes=1, metas=2, ideias=0),
             linha('Primeira', 85, ajustes=1, metas=2, ideias=1)]))

    t('sem ponto sincronizado vale zero ajuste — não é punição',
      ordem([linha('Com 2 ajustes', 85, ajustes=2, metas=9, ideias=9),
             linha('Sem ponto', 85, ajustes=None, metas=0, ideias=0)])
      == ['Sem ponto', 'Com 2 ajustes'])
    t('e quem não tem ponto empata com quem tem zero ajuste (decide a meta)',
      ordem([linha('Sem ponto', 85, ajustes=None, metas=1, ideias=0),
             linha('Zero ajustes', 85, ajustes=0, metas=3, ideias=0)])
      == ['Zero ajustes', 'Sem ponto'])

    t('tudo igual: decide o nome, e a ordem não muda entre carregamentos',
      ordem([linha('Zeca', 85, ajustes=1, metas=2, ideias=1),
             linha('Ana', 85, ajustes=1, metas=2, ideias=1)]) == ['Ana', 'Zeca'])

    marcadas = ordenar_ranking(
        [linha('A', 85, ajustes=1), linha('B', 85, ajustes=2), linha('C', 70, ajustes=0)],
        dados=lambda l: l['dados'], nome=lambda l: l['nome'])
    t('quem empatou na pontuação fica marcado para a tela explicar',
      [l['empatou'] for l in marcadas] == [True, True, False],
      [(l['nome'], l['empatou']) for l in marcadas])

    print('\n== OS NÚMEROS SAEM DO PRÓPRIO CÁLCULO ==')
    setor = Sector.objects.create(name='ZZ Setor Desempate')
    chefe = User.objects.create_user(
        username='zzde.chefe', email='zzde.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True,
        is_staff=True, sector=setor)
    adm, _ = CommunicationGroup.objects.get_or_create(name='ESCRITÓRIO (ADM)',
                                                      defaults={'created_by': chefe})
    pessoas = {}
    for nome in ('Ana', 'Bruno', 'Carla'):
        u = User.objects.create_user(
            username=f'zzde.{nome.lower()}', email=f'zzde.{nome.lower()}@exemplo-teste.local',
            password='S3nha!teste', first_name=nome, last_name='Desempate',
            hierarchy='PADRAO', sector=setor)
        u.communication_groups.add(adm)
        pessoas[nome] = u
    chefe.communication_groups.add(adm)

    hoje = timezone.localdate()
    prazo = hoje.replace(day=min(hoje.day, 28))

    def meta(dono, concluida):
        return Meta.objects.create(
            gestor=chefe, colaborador=dono, titulo='ZZ meta do teste', descricao='ZZ',
            prazo=prazo, aprovacao=Meta.Aprovacao.APROVADA,
            status=Meta.Status.CONCLUIDA if concluida else Meta.Status.A_FAZER)

    def ideia(dono, aprovada):
        return Ideia.objects.create(
            autor=dono, descricao='ZZ ideia do teste', setor_impacto='ZZ Setor',
            motivo='ZZ motivo', status=Ideia.Status.APROVADA if aprovada else Ideia.Status.NOVA)

    meta(pessoas['Ana'], True); meta(pessoas['Ana'], True); meta(pessoas['Ana'], False)
    meta(pessoas['Bruno'], True)
    ideia(pessoas['Ana'], True)
    ideia(pessoas['Bruno'], True); ideia(pessoas['Bruno'], True)
    ideia(pessoas['Carla'], False)

    d_ana = calcular_pontuacao(pessoas['Ana'])['desempate']
    d_bruno = calcular_pontuacao(pessoas['Bruno'])['desempate']
    d_carla = calcular_pontuacao(pessoas['Carla'])['desempate']
    t('metas concluídas do Confiar', (d_ana['metas_concluidas'], d_bruno['metas_concluidas'],
                                      d_carla['metas_concluidas']) == (2, 1, 0),
      (d_ana, d_bruno, d_carla))
    t('ideias aprovadas do Inovar', (d_ana['ideias_aprovadas'], d_bruno['ideias_aprovadas'],
                                     d_carla['ideias_aprovadas']) == (1, 2, 0))
    t('sem ponto sincronizado, os ajustes vêm como desconhecidos',
      d_ana['ajustes'] is None and d_carla['ajustes'] is None, d_ana)

    # Com o ponto respondendo, o número do mês entra no desempate.
    original = scoring.nota_assiduidade
    try:
        def falso_ponto(user, ano, mes):
            ajustes = {'Ana': 3, 'Bruno': 1, 'Carla': 0}.get(user.first_name, 0)
            return (Decimal('10'), Decimal('10'),
                    {'fonte': 'ponto', 'total_ajustes': ajustes, 'dias_uteis': 20,
                     'dias_completos': 20, 'motivo': 'ZZ teste'})
        scoring.nota_assiduidade = falso_ponto
        d = {nome: calcular_pontuacao(u)['desempate'] for nome, u in pessoas.items()}
        t('o ajuste de ponto do mês entra no desempate',
          (d['Ana']['ajustes'], d['Bruno']['ajustes'], d['Carla']['ajustes']) == (3, 1, 0),
          d)
    finally:
        scoring.nota_assiduidade = original

    print('\n== A TELA ==')
    c = Client(); c.force_login(chefe)
    r = c.get('/impulso/acompanhamento/')
    t('a tela abre', r.status_code == 200, r.status_code)
    ranking = r.context['ranking']
    t('a ordem da tela é a do critério',
      [l['user'].id for l in ranking]
      == [l['user'].id for l in sorted(
          ranking, key=lambda l: chave_do_ranking(l['dados'], l['user'].get_full_name()))])
    t('cada linha leva os números do desempate',
      all('desempate' in l['dados'] and 'empatou' in l for l in ranking))

    html = r.content.decode()
    t('a tabela tem a coluna do desempate', '>Desempate<' in html)
    t('mostrando ajustes, metas e ideias', ' aj.' in html and 'meta' in html and 'ideia' in html)
    t('e o rodapé explica a ordem dos três critérios',
      'menos ajustes de ponto no mês' in html
      and 'mais metas do Confiar concluídas' in html
      and 'mais ideias aprovadas no Inovar' in html)
    # A tabela cheia não mostra a linha do "nenhum colaborador": a conferência
    # do colspan é no próprio template.
    fonte = pathlib.Path('templates/impulso/acompanhamento.html').read_text()
    t('a linha vazia continua cobrindo a tabela inteira (9 colunas)',
      'colspan="9"' in fonte and 'colspan="8"' not in fonte)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
