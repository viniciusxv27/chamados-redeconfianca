"""Impulso: o SUPERADMIN ajusta à mão os pontos de um mês fechado (quem ficou por pouco).

Pedido: em /impulso/acompanhamento/<pessoa>/?mes=<mês>, o SUPERADMIN muda os pontos
de cada item; total, percentual, faixa e C$ do mês são recalculados, o motivo fica
registrado, dá para desfazer e o ajuste sobrevive a reabrir e fechar o mês.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
from decimal import Decimal
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from impulso import ciclos
from impulso.models import Ciclo, CicloMes, PontuacaoMensal
from impulso.utils import get_colaboradores

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
    hoje = timezone.localdate()
    chefe = User.objects.create_user(username='zzaj.chefe', email='zzaj.chefe@exemplo-teste.local',
                                     password='S3nha!teste', first_name='Chefe', hierarchy='SUPERADMIN')
    comum = User.objects.create_user(username='zzaj.comum', email='zzaj.comum@exemplo-teste.local',
                                     password='S3nha!teste', first_name='Comum')
    alvo = get_colaboradores().exclude(pk__in=[chefe.pk, comum.pk]).first()
    assert alvo, 'nenhum colaborador do Impulso no banco'
    ciclo = Ciclo.objects.create(nome='ZZ Ciclo ajuste', inicio=hoje.replace(day=1), fim=hoje, criado_por=chefe)
    mes = CicloMes.objects.create(ciclo=ciclo, referencia=hoje.replace(day=1), status=CicloMes.Status.FECHADO)
    dez = Decimal('10')
    pont = PontuacaoMensal.objects.create(
        mes=mes, user=alvo, p_metas_qualidade=dez, p_metas_conclusao=dez, p_feedback=dez,
        p_assiduidade=Decimal('9.75'), p_curso=dez, p_videos_pops=dez, p_projeto_foco=Decimal('20'),
        p_ideias=dez, p_ideia_aprovada=dez, total=Decimal('99.75'), pontos_aplicaveis=Decimal('100'),
        percentual=Decimal('99.75'), faixa='OURO', confiancas_previstas=100, detalhes={'metas': {}})
    url = f'/impulso/acompanhamento/{alvo.pk}/?mes={mes.pk}'
    url_ajuste = f'/impulso/acompanhamento/{alvo.pk}/mes/{mes.pk}/ajustar/'
    campos = {c: str(getattr(pont, c)) for c, _, _ in ciclos.CAMPOS_PONTOS}

    print('== TELA ==')
    c = Client()
    c.force_login(chefe)
    html = c.get(url).content.decode()
    t('SUPERADMIN abre o mês fechado com o formulário de ajuste', 'Ajustar pontos manualmente' in html
      and f'action="{url_ajuste}"' in html)
    t('o máximo de cada item vem dos pesos (projeto foco /20)', 'max="20' in html)
    cc = Client()
    cc.force_login(comum)
    r = cc.post(url_ajuste, {**campos, 'p_assiduidade': '10', 'motivo': 'tentando'})
    pont.refresh_from_db()
    t('quem não é SUPERADMIN não ajusta', pont.p_assiduidade == Decimal('9.75'))

    print('\n== AJUSTAR ==')
    r = c.post(url_ajuste, {**campos, 'p_assiduidade': '10', 'motivo': ''}, follow=True)
    pont.refresh_from_db()
    t('sem motivo, nada muda', pont.p_assiduidade == Decimal('9.75') and 'motivo' in r.content.decode())
    r = c.post(url_ajuste, {**campos, 'p_assiduidade': '11', 'motivo': 'x'}, follow=True)
    pont.refresh_from_db()
    t('acima do peso do item é recusado', pont.p_assiduidade == Decimal('9.75') and 'de 0 a 10' in r.content.decode())
    r = c.post(url_ajuste, {**campos, 'p_assiduidade': '10', 'motivo': 'Ficou a 0,25 do Impulso por um ajuste de ponto justificado.'},
               follow=True)
    pont.refresh_from_db()
    t('ajustado: 100 pts, 100%, faixa Impulso', (pont.total, pont.percentual, pont.faixa) ==
      (Decimal('100.00'), Decimal('100.00'), 'IMPULSO'), (pont.total, pont.percentual, pont.faixa))
    t('C$ do mês continuam reservadas', pont.confiancas_previstas == 100)
    ajuste = pont.detalhes.get('ajuste_manual') or {}
    t('motivo, autor e o calculado ficam registrados', ajuste.get('motivo', '').startswith('Ficou a 0,25')
      and ajuste.get('por_id') == chefe.pk and ajuste['calculado']['p_assiduidade'] == 9.75
      and ajuste['calculado_resumo']['faixa'] == 'OURO')
    html = r.content.decode()
    t('a tela mostra o aviso de ajuste e o valor calculado riscado', 'data-ajuste-manual' in html
      and 'line-through' in html and 'Desfazer ajuste' in html)
    r = c.post(url_ajuste, {**campos, 'p_assiduidade': '10', 'p_curso': '0', 'motivo': 'Curso não contava.'})
    pont.refresh_from_db()
    t('baixar pontos também funciona (Ouro perde as C$ se cair para Prata)',
      pont.total == Decimal('90.00') and pont.faixa == 'PRATA' and pont.confiancas_previstas == 0, (pont.total, pont.faixa))
    t('o calculado continua o do fechamento', pont.detalhes['ajuste_manual']['calculado']['p_curso'] == 10.0
      and len(pont.detalhes['ajuste_manual']['historico']) == 2)
    c.post(url_ajuste, {**campos, 'p_assiduidade': '10', 'motivo': 'Volta o curso.'})

    print('\n== REFECHAR O MÊS NÃO APAGA O AJUSTE ==')
    calculado = {c_: Decimal('5') for c_, _, _ in ciclos.CAMPOS_PONTOS}
    dados = {**calculado, 'p_projeto_foco': Decimal('20'), 'total': Decimal('60'), 'aplicavel': Decimal('100'),
             'percentual': Decimal('60'), 'faixa': 'BRONZE', 'detalhes': {}}
    with mock.patch('impulso.ciclos.get_colaboradores', return_value=[alvo]), \
            mock.patch('impulso.ciclos.calcular_pontuacao', return_value=dados):
        ciclos.reabrir_mes(mes)
        ciclos.fechar_mes(mes, chefe)
    pont.refresh_from_db()
    t('os pontos digitados continuam depois de fechar de novo', pont.total == Decimal('100.00')
      and pont.faixa == 'IMPULSO', (pont.total, pont.faixa))
    t('e o calculado passa a ser o do fechamento novo', pont.detalhes['ajuste_manual']['calculado']['p_curso'] == 5.0)

    print('\n== DESFAZER ==')
    r = c.post(url_ajuste, {'acao': 'desfazer'})
    pont.refresh_from_db()
    t('desfazer volta ao calculado', pont.total == Decimal('60.00') and pont.faixa == 'BRONZE'
      and 'ajuste_manual' not in pont.detalhes and pont.detalhes.get('ajustes_desfeitos'), (pont.total, pont.faixa))

    print('\n== CICLO JÁ PAGO ==')
    Ciclo.objects.filter(pk=ciclo.pk).update(confiancas_creditadas=True, status=Ciclo.Status.ENCERRADO)
    r = c.post(url_ajuste, {**campos, 'motivo': 'tarde demais'}, follow=True)
    pont.refresh_from_db()
    t('ciclo encerrado: não ajusta mais', pont.total == Decimal('60.00') and 'já foi encerrado' in r.content.decode())
    t('e o formulário some', 'Ajustar pontos manualmente' not in c.get(url).content.decode())
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
