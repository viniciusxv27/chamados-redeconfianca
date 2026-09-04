"""Impulso: a régua é a mesma para todo mundo — 40 Confiar, 40 Conectar, 20 Inovar.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
from datetime import timedelta

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from communications.models import CommunicationGroup
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, Ideia, Meta
from impulso.scoring import (maximos,
                             blocos_resumo, calcular_pontuacao,
                             linhas_detalhadas, periodo_do_mes)
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
    print('== OS PESOS SÃO OS PEDIDOS ==')
    MAX_CONFIAR, MAX_CONECTAR, MAX_INOVAR, MAX_TOTAL = maximos()
    t('CONFIAR vale 40', MAX_CONFIAR == 40, MAX_CONFIAR)
    t('CONECTAR vale 40', MAX_CONECTAR == 40, MAX_CONECTAR)
    t('INOVAR vale 20', MAX_INOVAR == 20, MAX_INOVAR)
    t('e o total fecha em 100', MAX_TOTAL == 100, MAX_TOTAL)

    adm = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
    ges = CommunicationGroup.objects.filter(name__iexact=GRUPO_GESTOR).first()
    assert adm and ges, 'grupos do Impulso não encontrados'
    area = Sector.objects.create(name='ZZ Area Pontuacao')
    inicio, fim = periodo_do_mes()
    hoje = timezone.localdate()
    prazo = min(max(hoje, inicio), fim)

    def novo(username, grupos=(adm,)):
        u = User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local',
            first_name=username.split('.')[1].title(), last_name='Teste',
            password='S3nha!teste', sector=area)
        for g in grupos:
            u.communication_groups.add(g)
        return u

    gestor = novo('pi.gestor', (adm, ges))
    vazio = novo('pi.vazio')          # nenhum item no mês
    cheio = novo('pi.cheio')          # metas e ideias

    print('\n== TODO MUNDO É MEDIDO NA MESMA RÉGUA ==')
    d_vazio = calcular_pontuacao(vazio)
    d_cheio = calcular_pontuacao(cheio)
    for rotulo, d in (('quem não teve nenhum item', d_vazio),
                      ('quem teve itens', d_cheio)):
        t(f'{rotulo}: total possível 100', float(d['aplicavel']) == 100, d['aplicavel'])
        t(f'{rotulo}: CONFIAR sobre 40', float(d['confiar_max']) == 40, d['confiar_max'])
        t(f'{rotulo}: CONECTAR sobre 40', float(d['conectar_max']) == 40, d['conectar_max'])
        t(f'{rotulo}: INOVAR sobre 20', float(d['inovar_max']) == 20, d['inovar_max'])

    print('\n== ITEM QUE NÃO EXISTIU VALE ZERO, NÃO SAI DA CONTA ==')
    t('sem nenhum item, o percentual é zero',
      float(d_vazio['percentual']) == 0, d_vazio['percentual'])
    t('e o portal diz quantos pontos ficaram sem item publicado',
      float(d_vazio['pontos_sem_oportunidade']) > 0, d_vazio['pontos_sem_oportunidade'])
    t('o percentual é o próprio total, porque a régua é 100',
      float(d_vazio['percentual']) == float(d_vazio['total']))

    print('\n== DUAS PESSOAS COM ENTREGAS DIFERENTES NÃO EMPATAM ==')
    # Antes: quem tinha só 1 item entregue de 1 possível dava 100%, igual a
    # quem entregou 9 de 9. O denominador variável apagava a diferença.
    poucos = novo('pi.poucos')
    muitos = novo('pi.muitos')
    Meta.objects.create(titulo='ZZ meta única', colaborador=poucos, gestor=gestor,
                        prazo=prazo, aprovacao=Meta.Aprovacao.APROVADA,
                        status=Meta.Status.CONCLUIDA, created_by=gestor,
                        nota_qualidade=5, nota_prazo=5)
    for i in range(4):
        Meta.objects.create(titulo=f'ZZ meta {i}', colaborador=muitos, gestor=gestor,
                            prazo=prazo, aprovacao=Meta.Aprovacao.APROVADA,
                            status=Meta.Status.CONCLUIDA, created_by=gestor,
                            nota_qualidade=5, nota_prazo=5)
    for i in range(3):
        Ideia.objects.create(descricao=f'ZZ ideia {i}', autor=muitos)

    d_p = calcular_pontuacao(poucos)
    d_m = calcular_pontuacao(muitos)
    t('os dois são medidos sobre 100',
      float(d_p['aplicavel']) == float(d_m['aplicavel']) == 100)
    t('quem entregou mais tem percentual maior',
      float(d_m['percentual']) > float(d_p['percentual']),
      f"{d_p['percentual']} vs {d_m['percentual']}")
    t('metas perfeitas dão os 20 pontos de meta',
      float(d_p['p_metas_qualidade']) == 10 and float(d_p['p_metas_conclusao']) == 10,
      (d_p['p_metas_qualidade'], d_p['p_metas_conclusao']))
    t('3 ideias dão os 10 pontos de propor', float(d_m['p_ideias']) == 10, d_m['p_ideias'])
    t('nenhuma aprovada, nenhum ponto de aprovação',
      float(d_m['p_ideia_aprovada']) == 0)

    print('\n== O TETO CONTINUA SENDO 100 ==')
    t('ninguém passa de 100', float(d_m['total']) <= 100, d_m['total'])
    t('nem de 100%', float(d_m['percentual']) <= 100, d_m['percentual'])
    soma = sum(float(d_m[k]) for k in ('confiar', 'conectar', 'inovar'))
    t('os três blocos somam o total', abs(soma - float(d_m['total'])) < 0.01,
      (soma, d_m['total']))

    print('\n== AS BARRAS DA TELA USAM A MESMA RÉGUA ==')
    blocos = blocos_resumo(d_vazio)
    t('três blocos', len(blocos) == 3)
    t('CONFIAR mostra /40', float(blocos[0]['max']) == 40, blocos[0]['max'])
    t('CONECTAR mostra /40', float(blocos[1]['max']) == 40, blocos[1]['max'])
    t('INOVAR mostra /20', float(blocos[2]['max']) == 20, blocos[2]['max'])
    t('sem entrega, as barras ficam em 0%',
      all(b['pct'] == 0 for b in blocos), [b['pct'] for b in blocos])
    blocos_m = blocos_resumo(d_m)
    t('a barra do CONFIAR de quem entregou não vai a 100% só por ter metas',
      blocos_m[0]['pct'] < 100, blocos_m[0]['pct'])

    print('\n== O DETALHAMENTO EXPLICA CADA ZERO ==')
    linhas = linhas_detalhadas(d_vazio)
    t('nove itens', len(linhas) == 9, len(linhas))
    t('a soma dos máximos é 100',
      sum(float(l['max']) for l in linhas) == 100,
      sum(float(l['max']) for l in linhas))
    faltas = [l for l in linhas if float(l['pontos']) == 0]
    t('todo item zerado traz o motivo escrito',
      all(l['info'] and l['info'] != '—' for l in faltas),
      [l['item'] for l in faltas if not l['info'] or l['info'] == '—'])
    t('e o texto diz que não houve item, não que a pessoa falhou',
      any('Nenhum' in l['info'] or 'Sem' in l['info'] for l in faltas))

    print('\n== AS TELAS ==')
    c = Client()
    c.force_login(poucos)
    r = c.get('/impulso/acompanhamento/')
    t('acompanhamento abre', r.status_code == 200, r.status_code)
    html = r.content.decode()
    t('mostra o total sobre 100', '/ 100 pts' in html or '/ 100' in html)
    t('avisa sobre os pontos sem item publicado',
      'sem item publicado' in html or 'não tiveram item publicado' in html)
    t('e repete a régua na explicação', '40 Confiar, 40 Conectar' in html)

    cg = Client(); cg.force_login(gestor)
    r = cg.get(f'/impulso/acompanhamento/{poucos.id}/')
    t('o gestor abre o detalhe do colaborador', r.status_code == 200, r.status_code)
    t('com a mesma régua', '/ 100' in r.content.decode())

    print('\n== A BASE DE VERDADE ==')
    reais = list(User.objects.filter(is_active=True, communication_groups=adm)
                 .exclude(username__startswith='pi.').distinct()[:40])
    denominadores = {float(calcular_pontuacao(u)['aplicavel']) for u in reais}
    t(f'as {len(reais)} pessoas do Impulso são medidas sobre o mesmo total',
      denominadores == {100.0}, denominadores)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
