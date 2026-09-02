"""Voltar do Kanban com os filtros + saldo inicial do mês na contagem de caixa.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
from datetime import date, timedelta
from decimal import Decimal

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
from contagem_caixa.models import ContagemCaixaDia, SaldoInicialMes
from contagem_caixa.servicos import recalcular_saldos, saldo_de_abertura
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, Meta
from users.models import Sector

User = get_user_model()
ok = fail = 0
D = Decimal


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
    adm = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
    ges = CommunicationGroup.objects.filter(name__iexact=GRUPO_GESTOR).first()
    assert adm and ges, 'grupos do Impulso não encontrados'
    area = Sector.objects.create(name='ZZ Area Voltar')

    def novo(username, grupos=(), **kw):
        u = User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local',
            password='S3nha!teste', sector=area,
            first_name=username.split('.')[1].title(), last_name='Teste', **kw)
        for g in grupos:
            u.communication_groups.add(g)
        return u

    gestor = novo('vs.gestor', [adm, ges])
    colab = novo('vs.colab', [adm])
    outro = novo('vs.outro', [adm])

    hoje = timezone.localdate()
    meta = Meta.objects.create(
        titulo='ZZ Meta do voltar', colaborador=colab, gestor=gestor,
        prazo=hoje + timedelta(days=5), aprovacao=Meta.Aprovacao.APROVADA,
        created_by=gestor)

    print('== VOLTAR COM OS FILTROS ==')
    c = Client()
    c.force_login(gestor)

    # sem filtro nenhum: o Voltar é a lista limpa
    c.get('/impulso/metas/')
    html = c.get(f'/impulso/metas/{meta.id}/').content.decode()
    t('sem filtro, volta para a lista limpa',
      'href="/impulso/metas/"' in html, [l for l in html.split('\n') if 'Voltar ao Kanban' in l][:1])

    # com filtro: o Voltar leva o filtro junto
    c.get(f'/impulso/metas/?colaborador={colab.id}')
    html = c.get(f'/impulso/metas/{meta.id}/').content.decode()
    t('com filtro, o Voltar preserva o filtro',
      f'/impulso/metas/?colaborador={colab.id}' in html)

    # trocar o filtro atualiza o Voltar
    c.get(f'/impulso/metas/?colaborador={outro.id}')
    html = c.get(f'/impulso/metas/{meta.id}/').content.decode()
    t('trocar o filtro atualiza o Voltar',
      f'/impulso/metas/?colaborador={outro.id}' in html
      and f'colaborador={colab.id}"' not in html)

    # limpar o filtro volta a limpar o Voltar
    c.get('/impulso/metas/')
    html = c.get(f'/impulso/metas/{meta.id}/').content.decode()
    t('limpar o filtro limpa o Voltar', 'colaborador=' not in html.split('Voltar ao Kanban')[0][-300:])

    # o que não é filtro conhecido não entra na URL
    c.get(f'/impulso/metas/?colaborador={colab.id}&xpto=<script>&page=3')
    html = c.get(f'/impulso/metas/{meta.id}/').content.decode()
    t('parâmetro desconhecido não é carregado', 'xpto' not in html)

    # cada pessoa tem o seu: a sessão é individual
    c2 = Client()
    c2.force_login(colab)
    html2 = c2.get(f'/impulso/metas/{meta.id}/').content.decode()
    t('o filtro de um não vaza para o outro',
      f'colaborador={outro.id}' not in html2)

    print('\n== CONTAGEM DE CAIXA: COM QUANTO O MÊS COMEÇA ==')
    loja = Sector.objects.create(name='Loja ZZ Saldo', adabas='ZZ999')
    chefe = novo('vs.chefe', is_superuser=True, is_staff=True, hierarchy='SUPERADMIN')
    vendedor = novo('vs.vendedor', hierarchy='PADRAO')
    vendedor.sector = loja
    vendedor.save(update_fields=['sector'])

    # dois meses de movimento: junho fecha em 300, julho começa daí
    def dia(d, real, dep=D('0')):
        return ContagemCaixaDia.objects.create(loja=loja, data=d, valor_real=real,
                                               deposito=dep)

    dia(date(2026, 6, 10), D('200.00'))
    dia(date(2026, 6, 20), D('100.00'))
    dia(date(2026, 7, 5), D('50.00'))
    dia(date(2026, 7, 15), D('25.00'))
    recalcular_saldos(loja.id)

    jun20 = ContagemCaixaDia.objects.get(loja=loja, data=date(2026, 6, 20))
    jul15 = ContagemCaixaDia.objects.get(loja=loja, data=date(2026, 7, 15))
    t('junho fecha em 300', jun20.saldo == D('300.00'), jun20.saldo)
    t('julho continua de onde junho parou', jul15.saldo == D('375.00'), jul15.saldo)

    valor, fixado = saldo_de_abertura(loja.id, 2026, 7)
    t('sem linha, a abertura vem do mês anterior', valor == D('300.00') and not fixado,
      (valor, fixado))

    print('\n-- fixando a abertura de julho --')
    SaldoInicialMes.objects.create(loja=loja, ano=2026, mes=7, valor=D('1000.00'),
                                   motivo='abertura do controle', definido_por=chefe)
    recalcular_saldos(loja.id)
    jul15.refresh_from_db(); jun20.refresh_from_db()
    t('julho passa a começar em 1000', jul15.saldo == D('1075.00'), jul15.saldo)
    t('junho não muda', jun20.saldo == D('300.00'), jun20.saldo)

    valor, fixado = saldo_de_abertura(loja.id, 2026, 7)
    t('a tela sabe que a abertura foi fixada', valor == D('1000.00') and fixado, (valor, fixado))

    print('\n-- a corrente segue para os meses seguintes --')
    dia(date(2026, 8, 3), D('10.00'))
    recalcular_saldos(loja.id)
    ago = ContagemCaixaDia.objects.get(loja=loja, data=date(2026, 8, 3))
    t('agosto puxa do julho já corrigido', ago.saldo == D('1085.00'), ago.saldo)

    print('\n-- recálculo no meio do mês não aplica a abertura de novo --')
    d15 = ContagemCaixaDia.objects.get(loja=loja, data=date(2026, 7, 15))
    d15.valor_real = D('25.00'); d15.save()
    recalcular_saldos(loja.id, desde=date(2026, 7, 15))
    d15.refresh_from_db()
    t('o dia 15 continua somando sobre o dia 5', d15.saldo == D('1075.00'), d15.saldo)

    print('\n== PELA TELA ==')
    cc = Client(); cc.force_login(chefe)
    url = f'/contagem-caixa/loja/{loja.id}/saldo-inicial/'

    r = cc.post(url, {'origem': 'fixo', 'valor': '2.000,50', 'motivo': 'acerto',
                      'mes': 7, 'ano': 2026}, follow=True)
    linha = SaldoInicialMes.do_mes(loja.id, 2026, 7)
    t('gestor fixa pela tela', linha and linha.valor == D('2000.50'), linha.valor if linha else None)
    t('guarda o motivo', linha and linha.motivo == 'acerto')
    t('guarda quem definiu', linha and linha.definido_por_id == chefe.id)
    jul15.refresh_from_db()
    t('e o saldo do mês foi refeito', jul15.saldo == D('2075.50'), jul15.saldo)

    r = cc.post(url, {'origem': 'anterior', 'mes': 7, 'ano': 2026}, follow=True)
    t('voltar a puxar do anterior apaga a linha',
      SaldoInicialMes.do_mes(loja.id, 2026, 7) is None)
    jul15.refresh_from_db()
    t('e o saldo volta a encadear', jul15.saldo == D('375.00'), jul15.saldo)
    t('a tela confirma', 'volta a puxar' in r.content.decode())

    r = cc.post(url, {'origem': 'fixo', 'valor': 'abc', 'mes': 7, 'ano': 2026}, follow=True)
    t('valor ilegível é recusado', SaldoInicialMes.do_mes(loja.id, 2026, 7) is None)
    t('e a tela explica', 'não é um valor válido' in r.content.decode())

    r = cc.post(url, {'origem': 'fixo', 'valor': '10', 'mes': 99, 'ano': 2026}, follow=True)
    t('mês inválido é recusado', 'inválido' in r.content.decode())

    r = cc.get(url)
    t('GET não grava (405)', r.status_code == 405, r.status_code)

    print('\n== QUEM PODE ==')
    cv = Client(); cv.force_login(vendedor)
    r = cv.post(url, {'origem': 'fixo', 'valor': '999', 'mes': 7, 'ano': 2026}, follow=True)
    t('quem não é gestão não define',
      SaldoInicialMes.do_mes(loja.id, 2026, 7) is None)
    t('e a tela avisa', 'Só a gestão' in r.content.decode())

    html = cv.get(f'/contagem-caixa/loja/{loja.id}/?mes=7&ano=2026').content.decode()
    t('e nem vê o bloco', 'Com quanto o mês começa' not in html)

    html = cc.get(f'/contagem-caixa/loja/{loja.id}/?mes=7&ano=2026').content.decode()
    t('gestor vê o bloco', 'Com quanto o mês começa' in html)
    t('com as duas opções', 'value="anterior"' in html and 'value="fixo"' in html)
    t('e mostra quanto daria puxando do anterior', 'Hoje isso dá' in html)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
