"""Impulso: as metas que contam na pontuação de /impulso/acompanhamento/.

Pedido: "Sobre a pontuação, deve ser contado a quantidade de meta do usuário +
que é colaborador + quantas criou."

Antes, só a meta em que a pessoa era a responsável (`colaborador`) entrava no
CONFIAR. Agora entra a união, sem repetir, de: responsável + participante
(meta compartilhada) + quem criou (`created_by`), com os mesmos filtros de
antes (prazo no mês, só aprovada, regra do corte).

O que este teste cobre:

- meta só como responsável, só como participante, só como criador;
- a mesma meta nos três papéis conta uma vez (e no papel de responsável);
- meta não aprovada e meta fora do mês ficam de fora;
- o corte do mês (criada depois fica fora; entregue/avaliada depois não conta
  como concluída);
- o contador do desempate (metas concluídas) usa o mesmo conjunto;
- a média de qualidade usa o mesmo conjunto;
- a consulta das metas é uma só, não importa quantas metas a pessoa tenha;
- o texto da linha explica de onde vêm as metas e o detalhe lista cada uma com o papel.

Roda dentro de uma transação desfeita.
"""
import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-imp-papeis'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-imp-papeis2'},
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
from django.utils import timezone

from communications.models import CommunicationGroup
from impulso import scoring
from impulso.models import Meta
from impulso.scoring import _nota_metas, calcular_pontuacao, linhas_detalhadas
from impulso.views import _periodo_do_acompanhamento
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


# O ponto não entra neste teste: a assiduidade fica fixa e não fala com a API.
scoring.nota_assiduidade = lambda user, ano, mes: (
    Decimal('0'), Decimal('0'), {'fonte': 'folha', 'sem_folha': True})

marcador = transaction.atomic()
marcador.__enter__()
try:
    inicio, fim, _mes = _periodo_do_acompanhamento()
    prazo = inicio + timedelta(days=5)

    setor = Sector.objects.create(name='ZZ Setor Papéis')
    chefe = User.objects.create_user(
        username='zzpp.chefe', email='zzpp.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True,
        is_staff=True, sector=setor)
    adm, _ = CommunicationGroup.objects.get_or_create(name='ESCRITÓRIO (ADM)',
                                                      defaults={'created_by': chefe})
    chefe.communication_groups.add(adm)
    pessoas = {}
    for nome in ('Ana', 'Bruno', 'Carla', 'Davi'):
        u = User.objects.create_user(
            username=f'zzpp.{nome.lower()}', email=f'zzpp.{nome.lower()}@exemplo-teste.local',
            password='S3nha!teste', first_name=nome, last_name='Papéis',
            hierarchy='PADRAO', sector=setor)
        u.communication_groups.add(adm)
        pessoas[nome] = u
    ana, bruno, carla, davi = (pessoas[n] for n in ('Ana', 'Bruno', 'Carla', 'Davi'))

    def meta(dono, criador=None, participantes=(), concluida=False, notas=None,
             aprovacao=Meta.Aprovacao.APROVADA, quando=None, titulo='ZZ meta'):
        m = Meta.objects.create(
            gestor=chefe, colaborador=dono, created_by=criador, titulo=titulo, descricao='ZZ',
            prazo=quando or prazo, aprovacao=aprovacao,
            status=Meta.Status.CONCLUIDA if concluida else Meta.Status.A_FAZER)
        if participantes:
            m.participantes.add(*participantes)
        if notas:
            Meta.objects.filter(pk=m.pk).update(nota_qualidade=notas[0], nota_prazo=notas[1],
                                                avaliado_em=timezone.now())
        return m

    def det(user, corte=None):
        return _nota_metas(user, inicio, fim, corte)[4]

    print('== CADA PAPEL CONTA ==')
    # Ana: 1 só como responsável (de Bruno criada pelo chefe), concluída.
    m_resp = meta(ana, criador=chefe, concluida=True, notas=(5, 5), titulo='ZZ resp Ana')
    d = det(ana)
    t('só como responsável conta', d['total'] == 1 and d['responsavel'] == 1, d)

    # Bruno: só participante de uma meta da Carla.
    m_part = meta(carla, criador=chefe, participantes=[bruno], concluida=True, notas=(3, 3),
                  titulo='ZZ part Bruno')
    d = det(bruno)
    t('só como participante conta', d['total'] == 1 and d['participando'] == 1
      and d['concluidas'] == 1, d)

    # Davi: só criou uma meta para a Ana (em aberto).
    m_criada = meta(ana, criador=davi, titulo='ZZ criada por Davi')
    d = det(davi)
    t('só como criador conta', d['total'] == 1 and d['criadas'] == 1 and d['concluidas'] == 0, d)

    print('\n== A MESMA META UMA VEZ SÓ ==')
    # Carla: responsável, participante e criadora da mesma meta.
    m_tres = meta(carla, criador=carla, participantes=[carla, bruno], concluida=True,
                  titulo='ZZ três papéis')
    d = det(carla)
    # Carla: m_part (responsável) + m_tres (responsável, criou e participa) = 2.
    t('a meta nos três papéis conta uma vez, como responsável',
      d['total'] == 2 and d['responsavel'] == 2 and d['participando'] == 0 and d['criadas'] == 0, d)
    t('e aparece uma vez na lista', [i['id'] for i in d['itens']].count(m_tres.id) == 1, d['itens'])
    # Bruno participa de m_part e m_tres: dois participantes na mesma meta não duplicam nada.
    d = det(bruno)
    t('participante de duas metas: 2, sem repetição', d['total'] == 2 and d['participando'] == 2, d)
    # Participante e criador ao mesmo tempo: conta como participante.
    m_pc = meta(carla, criador=davi, participantes=[davi], titulo='ZZ part e criou')
    d = det(davi)
    t('participante e criador conta uma vez, como participante',
      d['total'] == 2 and d['participando'] == 1 and d['criadas'] == 1, d)

    print('\n== O QUE FICA DE FORA ==')
    meta(ana, criador=davi, participantes=[bruno], aprovacao=Meta.Aprovacao.PENDENTE,
         titulo='ZZ pendente')
    meta(ana, criador=davi, participantes=[bruno], aprovacao=Meta.Aprovacao.RECUSADA,
         titulo='ZZ recusada')
    meta(ana, criador=davi, participantes=[bruno], quando=fim + timedelta(days=3),
         titulo='ZZ mês seguinte')
    meta(ana, criador=davi, participantes=[bruno], quando=inicio - timedelta(days=3),
         titulo='ZZ mês anterior')
    t('meta pendente/recusada fica de fora (em todos os papéis)',
      (det(ana)['total'], det(bruno)['total'], det(davi)['total']) == (2, 2, 2),
      (det(ana)['total'], det(bruno)['total'], det(davi)['total']))
    t('meta fora do mês fica de fora', all(
        'mês' not in i['titulo'] for u in (ana, bruno, davi) for i in det(u)['itens']))
    meta(chefe, criador=None, titulo='ZZ sem criador')
    t('meta sem criador (created_by vazio) não entra para ninguém além do dono',
      det(davi)['total'] == 2)

    print('\n== QUALIDADE E CONCLUSÃO NO MESMO CONJUNTO ==')
    # Bruno: m_part (3/3, concluída) e m_tres (sem nota, concluída).
    p_q, ap_q, p_c, ap_c, d = _nota_metas(bruno, inicio, fim)
    t('a qualidade usa a meta em que participa', d['avaliadas'] == 1 and d['media_qualidade'] == 3.0
      and p_q == Decimal('6.00'), (p_q, d))
    t('a conclusão usa as duas', d['concluidas'] == 2 and p_c == scoring.pt('metas_conclusao', bruno),
      (p_c, d))

    print('\n== O CORTE DO MÊS ==')
    corte = timezone.make_aware(datetime.combine(prazo, datetime.min.time()))
    eli = User.objects.create_user(
        username='zzpp.eli', email='zzpp.eli@exemplo-teste.local', password='S3nha!teste',
        first_name='Eli', last_name='Papéis', hierarchy='PADRAO', sector=setor)
    antes = corte - timedelta(days=1)
    depois = corte + timedelta(hours=1)
    c1 = meta(ana, criador=eli, concluida=True, notas=(4, 4), titulo='ZZ corte: tudo antes')
    c2 = meta(ana, criador=eli, concluida=True, notas=(5, 5), titulo='ZZ corte: avaliada depois')
    c3 = meta(ana, participantes=[eli], concluida=True, titulo='ZZ corte: entregue depois')
    c4 = meta(ana, participantes=[eli], titulo='ZZ corte: criada depois')
    Meta.objects.filter(pk__in=[c1.pk, c2.pk, c3.pk, c4.pk]).update(created_at=antes)
    Meta.objects.filter(pk=c1.pk).update(avaliado_em=antes, entregue_em=antes)
    Meta.objects.filter(pk=c2.pk).update(avaliado_em=depois)
    Meta.objects.filter(pk=c3.pk).update(entregue_em=depois)
    Meta.objects.filter(pk=c4.pk).update(created_at=depois)
    d = det(eli)
    t('sem corte: as 4', d['total'] == 4 and d['concluidas'] == 3, d)
    d = det(eli, corte)
    t('com corte: a criada depois sai', d['total'] == 3, d)
    t('com corte: entregue/avaliada depois não está concluída', d['concluidas'] == 1, d)
    t('com corte: só a avaliada antes entra na média', d['avaliadas'] == 1
      and d['media_qualidade'] == 4.0, d)
    t('com corte: os papéis seguem certos', (d['criadas'], d['participando']) == (2, 1), d)

    print('\n== O DESEMPATE ==')
    dados_bruno = calcular_pontuacao(bruno, inicio=inicio, fim=fim)
    dados_davi = calcular_pontuacao(davi, inicio=inicio, fim=fim)
    t('metas concluídas do desempate contam participante',
      dados_bruno['desempate']['metas_concluidas'] == 2, dados_bruno['desempate'])
    t('e o criador (o que criou e ainda está aberto não conta como concluída)',
      dados_davi['desempate']['metas_concluidas'] == 0, dados_davi['desempate'])
    Meta.objects.filter(pk=m_criada.pk).update(status=Meta.Status.CONCLUIDA)
    dados_davi = calcular_pontuacao(davi, inicio=inicio, fim=fim)
    t('concluída a que criou, conta no desempate de quem criou',
      dados_davi['desempate']['metas_concluidas'] == 1, dados_davi['desempate'])

    print('\n== CONSULTAS ==')
    def consultas_de_metas(user):
        with CaptureQueriesContext(connection) as ctx:
            _nota_metas(user, inicio, fim)
        return sum(1 for q in ctx.captured_queries if 'impulso_meta' in q['sql'])
    poucas = consultas_de_metas(bruno)
    for i in range(12):
        meta(chefe, criador=bruno if i % 2 else None, participantes=[] if i % 2 else [bruno],
             concluida=bool(i % 3), titulo='ZZ volume %s' % i)
    muitas = consultas_de_metas(bruno)
    t('uma consulta só para as metas, com 2 ou com 14 metas', poucas == 1 and muitas == 1,
      (poucas, muitas))
    t('e as 12 novas entraram', det(bruno)['total'] == 14, det(bruno)['total'])

    print('\n== O TEXTO E A TELA ==')
    linhas = linhas_detalhadas(calcular_pontuacao(davi, inicio=inicio, fim=fim))
    conclusao = next(l for l in linhas if l['item'] == 'Metas — conclusão')
    t('a linha diz de onde vêm as metas', conclusao['info'] == '1 de 2 concluída(s) · inclui 1 em que participa e 1 que criou',
      conclusao['info'])
    linhas = linhas_detalhadas(calcular_pontuacao(ana, inicio=inicio, fim=fim))
    conclusao = next(l for l in linhas if l['item'] == 'Metas — conclusão')
    t('só como responsável, o texto é o de antes', ' · inclui' not in conclusao['info'],
      conclusao['info'])
    t('a linha leva a lista das metas com o papel',
      {i['rotulo_papel'] for i in conclusao['itens_metas']} == {'responsável'}, conclusao['itens_metas'])
    linhas = linhas_detalhadas(calcular_pontuacao(eli, inicio=inicio, fim=fim))
    conclusao = next(l for l in linhas if l['item'] == 'Metas — conclusão')
    t('só criou: "que criou" no texto', conclusao['info'].endswith('inclui 1 em que participa e 2 que criou')
      or conclusao['info'].endswith('inclui 2 em que participa e 2 que criou'), conclusao['info'])
    # Mês fechado antes da mudança não traz os papéis: o texto fica o de antes.
    antigo = {'detalhes': {**calcular_pontuacao(ana, inicio=inicio, fim=fim)['detalhes'],
                           'metas': {'total': 3, 'concluidas': 1, 'avaliadas': 0}}}
    antigo.update({k: v for k, v in calcular_pontuacao(ana, inicio=inicio, fim=fim).items()
                   if k != 'detalhes'})
    conclusao = next(l for l in linhas_detalhadas(antigo) if l['item'] == 'Metas — conclusão')
    t('snapshot antigo (sem papéis) mostra o texto de antes', conclusao['info'] == '1 de 3 concluída(s)'
      and conclusao['itens_metas'] == [], conclusao)

    c = Client(); c.force_login(chefe)
    r = c.get(f'/impulso/acompanhamento/{davi.id}/')
    html = r.content.decode()
    t('o detalhe abre', r.status_code == 200, r.status_code)
    t('o detalhe explica a origem', 'inclui 1 em que participa e 1 que criou' in html)
    t('e lista as metas com o papel', 'data-metas-do-mes' in html and '>participa</span>' in html
      and '>criou</span>' in html and 'ZZ part e criou' in html and 'ZZ criada por Davi' in html)
    t('cada meta leva ao card', f'/impulso/metas/{m_criada.id}/' in html)
    r = c.get('/impulso/acompanhamento/')
    t('o acompanhamento abre', r.status_code == 200, r.status_code)
    linha_davi = next((l for l in r.context['ranking'] if l['user'].id == davi.id), None)
    t('e o ranking usa a conta nova', linha_davi is not None
      and linha_davi['dados']['desempate']['metas_concluidas'] == 1,
      linha_davi and linha_davi['dados']['desempate'])
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
