"""Financeiro (/financeiro/): acessos, títulos, baixas, bancos, conciliações, plano e rastreabilidade.

Pedido de 16/10/2026: módulo financeiro no menu Financeiro, só SUPERADMIN com
acesso — e quem ele liberar, parte por parte e ação por ação.

O que este teste cobre:

- só o SUPERADMIN entra; quem ele libera entra só nas partes e lojas liberadas;
- plano de contas padrão e contas novas;
- conta bancária e saldo;
- título com parcelas (centavos na última), baixa parcial, juros, quitação,
  estorno, edição de baixa, cancelamento e exclusão — com as travas;
- baixa em lote;
- extrato OFX e CSV: importa sem repetir, concilia sozinho o par certo,
  lança tarifa do extrato, desfaz;
- conciliação de cartões pelo NSU (conciliado, divergente, sem par);
- rastreabilidade: quem, o quê, quando, IP, dispositivo, antes/depois;
- todas as telas abrem.

Transação desfeita no fim; nada sai para a rede.
"""
import os
import sys
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fin'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fin-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import requests
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client
from django.utils import timezone

from financeiro import extrato, servicos
from financeiro.models import (AcessoFinanceiro, Baixa, ContaBancaria, ExtratoLinha, LogFinanceiro,
                               MovimentoBancario, PlanoConta, RecebivelCartao, Titulo)
from users.models import Sector

User = get_user_model()
D = Decimal
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def sem_rede(*a, **k):
    raise AssertionError('o teste tentou sair para a rede')


HOJE = timezone.localdate()
CABECALHOS = {'HTTP_USER_AGENT': 'ZZ-Navegador/1.0 (teste)', 'REMOTE_ADDR': '10.9.8.7'}

marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede):
        loja_a = Sector.objects.create(name='Loja ZZ Fin A', adabas='ZZA1')
        loja_b = Sector.objects.create(name='Loja ZZ Fin B', adabas='ZZB1')
        chefe = User.objects.create_user(username='zzz.finchefe', email='zzz.finchefe@exemplo-teste.local',
                                         password='S3nha!teste', first_name='ZZ', last_name='Chefe',
                                         hierarchy='SUPERADMIN')
        ana = User.objects.create_user(username='zzz.finana', email='zzz.finana@exemplo-teste.local',
                                       password='S3nha!teste', first_name='ZZ', last_name='Ana', sector=loja_a,
                                       hierarchy='ADMINISTRATIVO')
        beto = User.objects.create_user(username='zzz.finbeto', email='zzz.finbeto@exemplo-teste.local',
                                        password='S3nha!teste', first_name='ZZ', last_name='Beto', sector=loja_b,
                                        hierarchy='ADMINISTRATIVO')
        cc, ca, cb = Client(**CABECALHOS), Client(**CABECALHOS), Client(**CABECALHOS)
        cc.force_login(chefe); ca.force_login(ana); cb.force_login(beto)

        print('== QUEM ENTRA ==')
        r = cc.get('/financeiro/')
        t('o SUPERADMIN entra', r.status_code == 200, r.status_code)
        t('e vê o item no menu', '/financeiro/' in cc.get('/').content.decode())
        r = ca.get('/financeiro/')
        t('quem não foi liberado não entra', r.status_code == 302, r.status_code)
        t('nem vê o item no menu', 'href="/financeiro/"' not in ca.get('/').content.decode())
        t('e não abre a tela de acessos', ca.get('/financeiro/acessos/').status_code == 302)

        r = cc.post('/financeiro/acessos/', {'user': ana.id, 'permissoes': ['pagar.ver', 'baixas.baixar', 'caixa.ver'],
                                             'lojas': [loja_a.id], 'observacao': 'teste', 'ativo': 'on'})
        acesso = AcessoFinanceiro.objects.filter(user=ana).first()
        t('o SUPERADMIN libera parte por parte',
          acesso is not None and set(acesso.permissoes) == {'pagar.ver', 'baixas.baixar', 'caixa.ver'}, getattr(acesso, 'permissoes', None))
        t('e recorta as lojas', acesso and list(acesso.lojas.values_list('id', flat=True)) == [loja_a.id])
        t('a mudança de acesso fica registrada', LogFinanceiro.objects.filter(acao='ACESSO', usuario=chefe).exists())
        ca = Client(**CABECALHOS); ca.force_login(ana)
        t('liberada, Ana entra', ca.get('/financeiro/').status_code == 200)
        t('e vê contas a pagar', ca.get('/financeiro/pagar/').status_code == 200)
        t('mas não contas a receber', ca.get('/financeiro/receber/').status_code == 302)
        t('nem bancos', ca.get('/financeiro/bancos/').status_code == 302)
        t('nem lança título (só ver)', ca.post('/financeiro/pagar/novo/', {}).status_code == 302
          and not Titulo.objects.exists())

        print('\n== PLANO DE CONTAS ==')
        cc.post('/financeiro/plano/padrao/')
        n = PlanoConta.objects.count()
        t('o plano padrão é criado num plano vazio', n > 40, n)
        cc.post('/financeiro/plano/padrao/')
        t('e não duplica', PlanoConta.objects.count() == n)
        aluguel = PlanoConta.objects.get(codigo='2.2.01')
        t('conta analítica recebe lançamento', aluguel.analitica and aluguel.tipo == 'DESPESA')
        cc.post('/financeiro/plano/nova/', {'codigo': '2.2.07', 'nome': 'Seguro predial'})
        t('subconta nova entra no grupo certo', PlanoConta.objects.filter(codigo='2.2.07', pai__codigo='2.2').exists())
        cc.post('/financeiro/plano/nova/', {'codigo': '7.1', 'nome': 'Sem pai'})
        t('sem a conta superior, não cria', not PlanoConta.objects.filter(codigo='7.1').exists())

        print('\n== CONTA BANCÁRIA ==')
        cc.post('/financeiro/bancos/nova/', {'nome': 'ZZ Itaú', 'tipo': 'CORRENTE', 'banco_codigo': '341',
                                            'saldo_inicial': '1.000,00', 'data_saldo_inicial': (HOJE - timedelta(days=60)).isoformat()})
        conta = ContaBancaria.objects.get(nome='ZZ Itaú')
        t('conta cadastrada com saldo inicial', conta.saldo() == D('1000.00'), conta.saldo())
        cc.post('/financeiro/bancos/nova/', {'nome': 'ZZ Caixa', 'tipo': 'CAIXA', 'saldo_inicial': '-50,00'})
        caixinha = ContaBancaria.objects.get(nome='ZZ Caixa')
        t('saldo inicial pode ser negativo', caixinha.saldo_inicial == D('-50.00'), caixinha.saldo_inicial)

        print('\n== TÍTULO COM PARCELAS ==')
        venc = HOJE + timedelta(days=5)
        r = cc.post('/financeiro/pagar/novo/', {
            'descricao': 'ZZ Aluguel', 'pessoa_nome': 'ZZ Imobiliária', 'pessoa_documento': '11.222.333/0001-81',
            'vencimento': venc.isoformat(), 'valor': '100,00', 'parcelas': '3', 'intervalo': 'MENSAL',
            'plano': aluguel.id, 'loja': loja_a.id, 'forma': 'BOLETO'})
        parcelas = list(Titulo.objects.filter(descricao='ZZ Aluguel').order_by('parcela'))
        t('três parcelas', len(parcelas) == 3, len(parcelas))
        t('os centavos vão para a última', [p.valor for p in parcelas] == [D('33.33'), D('33.33'), D('33.34')],
          [p.valor for p in parcelas])
        t('de mês em mês', parcelas[1].vencimento.month == (venc.month % 12) + 1)
        t('mesmo grupo', len({p.grupo for p in parcelas}) == 1 and parcelas[2].rotulo_parcela == '3/3')
        cc.post('/financeiro/pagar/novo/', {'descricao': 'ZZ Internet', 'vencimento': venc.isoformat(), 'valor': '50',
                                           'parcelas': '2', 'valor_modo': 'parcela', 'loja': loja_b.id})
        t('"valor de cada parcela" multiplica', list(Titulo.objects.filter(descricao='ZZ Internet')
                                                     .values_list('valor', flat=True)) == [D('50.00'), D('50.00')])
        cc.post('/financeiro/pagar/novo/', {'descricao': 'ZZ Ruim', 'vencimento': venc.isoformat(), 'valor': 'abc'})
        t('valor ilegível não lança', not Titulo.objects.filter(descricao='ZZ Ruim').exists())

        print('\n== RECORTE POR LOJA ==')
        da_b = Titulo.objects.filter(descricao='ZZ Internet').first()
        html = ca.get('/financeiro/pagar/').content.decode()
        t('Ana vê o título da loja dela', 'ZZ Aluguel' in html)
        t('e não o da outra loja', 'ZZ Internet' not in html)
        t('nem abre pelo endereço', ca.get(f'/financeiro/titulo/{da_b.id}/').status_code == 404)

        print('\n== BAIXA ==')
        p1 = parcelas[0]
        r = cc.post(f'/financeiro/titulo/{p1.id}/baixar/', {'data': HOJE.isoformat(), 'valor': '20,00', 'conta': conta.id,
                                                           'forma': 'PIX'})
        p1.refresh_from_db()
        t('baixa parcial', p1.status == 'PARCIAL' and p1.saldo == D('13.33'), (p1.status, p1.saldo))
        mov = MovimentoBancario.objects.get(baixa__titulo=p1)
        t('o pagamento sai da conta (negativo)', mov.valor == D('-20.00'), mov.valor)
        r = cc.post(f'/financeiro/titulo/{p1.id}/baixar/', {'data': HOJE.isoformat(), 'valor': '50', 'conta': conta.id,
                                                           'forma': 'PIX'})
        t('não baixa mais que o saldo', Baixa.objects.filter(titulo=p1).count() == 1)
        r = cc.post(f'/financeiro/titulo/{p1.id}/baixar/', {'data': (HOJE + timedelta(days=9)).isoformat(), 'valor': '13,33',
                                                           'conta': conta.id, 'forma': 'PIX'})
        t('nem com data no futuro', Baixa.objects.filter(titulo=p1).count() == 1)
        cc.post(f'/financeiro/titulo/{p1.id}/baixar/', {'data': HOJE.isoformat(), 'valor': '13,33', 'juros': '1,00',
                                                       'multa': '0,50', 'conta': conta.id, 'forma': 'BOLETO'})
        p1.refresh_from_db()
        t('quitou', p1.status == 'QUITADO' and p1.saldo == D('0'), (p1.status, p1.saldo))
        segunda = Baixa.objects.filter(titulo=p1, estornada=False).order_by('-id').first()
        t('juros e multa saem da conta junto', segunda.total == D('14.83') and segunda.movimento.valor == D('-14.83'))
        t('saldo da conta', conta.saldo() == D('1000.00') - D('20.00') - D('14.83'), conta.saldo())
        r = cc.post(f'/financeiro/titulo/{p1.id}/baixar/', {'data': HOJE.isoformat(), 'valor': '1', 'conta': conta.id, 'forma': 'PIX'})
        t('título quitado não recebe baixa', Baixa.objects.filter(titulo=p1).count() == 2)

        print('\n== CONTAS PAGAS: CORRIGIR E ESTORNAR ==')
        cc.post(f'/financeiro/baixa/{segunda.id}/editar/', {'data': HOJE.isoformat(), 'valor': '13,33', 'juros': '2,00',
                                                           'conta': conta.id, 'forma': 'BOLETO'})
        segunda.refresh_from_db()
        t('corrigir a baixa ajusta o movimento', segunda.juros == D('2.00') and segunda.multa == D('0')
          and segunda.movimento.valor == D('-15.33'), (segunda.juros, segunda.movimento.valor))
        r = cc.post(f'/financeiro/baixa/{segunda.id}/estornar/', {'motivo': 'ok'})
        segunda.refresh_from_db()
        t('estorno exige motivo', not segunda.estornada)
        cc.post(f'/financeiro/baixa/{segunda.id}/estornar/', {'motivo': 'pago na conta errada'})
        segunda.refresh_from_db(); p1.refresh_from_db()
        t('estornada, o título volta a parcial', segunda.estornada and p1.status == 'PARCIAL', p1.status)
        t('e o movimento sai do saldo', segunda.movimento.estornado and conta.saldo() == D('980.00'), conta.saldo())
        t('a baixa estornada fica no histórico', Baixa.objects.filter(titulo=p1).count() == 2)
        html = cc.get('/financeiro/pagas/').content.decode()
        t('contas pagas lista a baixa válida', 'ZZ Aluguel' in html)

        print('\n== CANCELAR E EXCLUIR ==')
        cc.post(f'/financeiro/titulo/{p1.id}/cancelar/', {'motivo': 'não devia existir'})
        p1.refresh_from_db()
        t('com baixa válida, não cancela', p1.status == 'PARCIAL')
        p3 = parcelas[2]
        cc.post(f'/financeiro/titulo/{p3.id}/cancelar/', {'motivo': 'contrato encerrado'})
        p3.refresh_from_db()
        t('sem baixa, cancela com motivo', p3.status == 'CANCELADO' and p3.motivo_cancelamento == 'contrato encerrado')
        cc.post(f'/financeiro/titulo/{p1.id}/excluir/')
        t('quem já teve baixa não se exclui', Titulo.objects.filter(pk=p1.pk).exists())
        p2 = parcelas[1]
        cc.post(f'/financeiro/titulo/{p2.id}/excluir/')
        t('lançado por engano, exclui', not Titulo.objects.filter(pk=p2.pk).exists())
        t('e a exclusão fica registrada com o que era',
          LogFinanceiro.objects.filter(acao='EXCLUIR', objeto_id=str(p2.pk), antes__descricao='ZZ Aluguel').exists())

        print('\n== BAIXA EM LOTE ==')
        rec = servicos.criar_titulos({'tipo': 'RECEBER', 'descricao': 'ZZ Repasse', 'vencimento': HOJE, 'valor': D('300'),
                                      'loja': loja_a}, usuario=chefe, parcelas=2)
        r = cc.post('/financeiro/baixas/lote/', {'titulos': [x.id for x in rec], 'data': HOJE.isoformat(),
                                                 'conta': conta.id, 'forma': 'TED'})
        t('as duas baixadas pelo saldo', all(Titulo.objects.get(pk=x.pk).status == 'QUITADO' for x in rec))
        t('recebimento entra na conta (positivo)',
          set(MovimentoBancario.objects.filter(baixa__titulo__in=rec).values_list('valor', flat=True)) == {D('150.00')})
        t('Ana (só "dar baixa") abre a tela de baixa de títulos', ca.get('/financeiro/baixas/').status_code == 200)

        print('\n== CONCILIAÇÃO BANCÁRIA ==')
        mov_rec = MovimentoBancario.objects.filter(baixa__titulo=rec[0]).first()
        ofx = f"""OFXHEADER:100
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKTRANLIST>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>{HOJE:%Y%m%d}120000<TRNAMT>150.00<FITID>ZZ001<MEMO>TED RECEBIDA
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>{HOJE:%Y%m%d}<TRNAMT>-20.00<FITID>ZZ002<MEMO>PIX ENVIADO</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>{HOJE:%Y%m%d}<TRNAMT>-12.90<FITID>ZZ003<MEMO>TARIFA PACOTE</STMTTRN>
</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>""".encode('latin-1')
        cc.post(f'/financeiro/conciliacao/{conta.id}/importar/', {'arquivo': SimpleUploadedFile('extrato.ofx', ofx)})
        linhas = ExtratoLinha.objects.filter(conta=conta)
        t('OFX importado', linhas.count() == 3, linhas.count())
        pix = linhas.get(documento='ZZ002')
        t('o PIX de 20 casou sozinho com o pagamento', pix.movimento_id is not None and pix.movimento.valor == D('-20.00'))
        ted = linhas.get(documento='ZZ001')
        t('as duas TEDs de 150 são candidatas: não casa sozinho', ted.movimento_id is None)
        cc.post(f'/financeiro/conciliacao/{conta.id}/importar/', {'arquivo': SimpleUploadedFile('extrato.ofx', ofx)})
        t('importar de novo não repete', ExtratoLinha.objects.filter(conta=conta).count() == 3)
        cc.post(f'/financeiro/conciliacao/linha/{ted.id}/conciliar/', {'movimento': mov_rec.id})
        ted.refresh_from_db()
        t('conciliar à mão', ted.movimento_id == mov_rec.id and MovimentoBancario.objects.get(pk=mov_rec.pk).conciliado)
        tarifa = linhas.get(documento='ZZ003')
        cc.post(f'/financeiro/conciliacao/linha/{tarifa.id}/lancar/', {'plano': PlanoConta.objects.get(codigo='2.5.01').id})
        tarifa.refresh_from_db()
        t('a tarifa do extrato vira lançamento já conciliado',
          tarifa.movimento is not None and tarifa.movimento.valor == D('-12.90') and tarifa.movimento.origem == 'AJUSTE')
        cc.post(f'/financeiro/conciliacao/linha/{ted.id}/desfazer/')
        ted.refresh_from_db()
        t('desfazer solta o par', ted.movimento_id is None and not MovimentoBancario.objects.get(pk=mov_rec.pk).conciliado)
        baixa_pix = pix.movimento.baixa
        servicos.estornar_baixa(baixa_pix, 'teste de estorno conciliado', usuario=chefe)
        pix.refresh_from_db()
        t('estornar baixa conciliada desfaz a conciliação', pix.movimento_id is None)

        csv = ('Data;Histórico;Crédito;Débito;Saldo\n'
               f'{HOJE:%d/%m/%Y};RENDIMENTO;3,21;;\n'
               f'{HOJE:%d/%m/%Y};SALDO DO DIA;;;1000,00\n'
               f'{HOJE:%d/%m/%Y};DOC ENVIADO;;1.234,56;\n').encode('utf-8')
        cc.post(f'/financeiro/conciliacao/{caixinha.id}/importar/', {'arquivo': SimpleUploadedFile('extrato.csv', csv)})
        valores = sorted(ExtratoLinha.objects.filter(conta=caixinha).values_list('valor', flat=True))
        t('CSV com crédito e débito (sem a linha de saldo)', valores == [D('-1234.56'), D('3.21')], valores)
        t('valor brasileiro lido', extrato.valor_br('R$ 1.234,56 D') == D('-1234.56') and extrato.valor_br('-1,5') == D('-1.50'))

        print('\n== CONCILIAÇÃO DE CARTÕES ==')
        from vendas.models import Venda, VendaProduto
        v1 = Venda.objects.create(loja=loja_a, data_venda=timezone.now(), forma_pagamento='CARTAO', nsu='000123', parcelas=2)
        VendaProduto.objects.create(venda=v1, nome_produto='ZZ Celular', valor_venda=D('500.00'))
        v2 = Venda.objects.create(loja=loja_a, data_venda=timezone.now(), forma_pagamento='CARTAO', nsu='A9', parcelas=1)
        VendaProduto.objects.create(venda=v2, nome_produto='ZZ Capa', valor_venda=D('80.00'))
        Venda.objects.create(loja=loja_a, data_venda=timezone.now(), forma_pagamento='CARTAO', nsu='777', parcelas=1)
        relatorio = ('Data da venda;NSU/DOC;Bandeira;Parcelas;Valor bruto;Valor líquido;Previsão de pagamento\n'
                     f'{HOJE:%d/%m/%Y};123;Visa;2;500,00;485,00;{HOJE + timedelta(days=30):%d/%m/%Y}\n'
                     f'{HOJE:%d/%m/%Y};A9;Master;1;79,00;76,00;\n'
                     f'{HOJE:%d/%m/%Y};999;Elo;1;10,00;9,70;\n').encode('utf-8')
        cc.post('/financeiro/cartoes/importar/', {'adquirente': 'ZZ Rede', 'loja': loja_a.id,
                                                  'arquivo': SimpleUploadedFile('vendas.csv', relatorio)})
        rec = {r.nsu: r for r in RecebivelCartao.objects.filter(adquirente='ZZ Rede')}
        t('importou as três', len(rec) == 3, list(rec))
        t('NSU com zeros à esquerda casa e concilia', rec['123'].venda_id == v1.id and rec['123'].status == 'CONCILIADO')
        t('valor diferente fica divergente', rec['A9'].venda_id == v2.id and rec['A9'].status == 'DIVERGENTE')
        t('sem venda no portal fica sem par', rec['999'].status == 'PENDENTE')
        t('taxa calculada do líquido', rec['123'].taxa == D('15.00'), rec['123'].taxa)
        html = cc.get('/financeiro/cartoes/').content.decode()
        t('a venda do portal sem par aparece na tela', 'NSU <strong class="tabular">777</strong>' in html)
        cc.post(f'/financeiro/cartoes/{rec["999"].id}/marcar/', {'status': 'CONCILIADO'})
        t('conciliar sem venda exige observação', RecebivelCartao.objects.get(pk=rec['999'].pk).status == 'PENDENTE')
        cc.post(f'/financeiro/cartoes/{rec["A9"].id}/marcar/', {'status': 'CONCILIADO', 'observacao': 'desconto no balcão'})
        t('aceitar a divergência com observação', RecebivelCartao.objects.get(pk=rec['A9'].pk).status == 'CONCILIADO')

        print('\n== RASTREABILIDADE ==')
        reg = LogFinanceiro.objects.filter(acao='BAIXAR', usuario=chefe).first()
        t('a baixa registra quem', reg is not None and reg.usuario_nome == 'ZZ Chefe')
        t('de onde (IP e dispositivo)', reg.ip == '10.9.8.7' and 'ZZ-Navegador' in reg.dispositivo, (reg.ip, reg.dispositivo))
        t('a tela e a loja', reg.rota.startswith('/financeiro/') and reg.loja_id == loja_a.id)
        ed = LogFinanceiro.objects.filter(acao='EDITAR_BAIXA').first()
        t('a correção guarda antes e depois', ed and ed.antes.get('juros') == '1.00' and ed.depois.get('juros') == '2.00',
          (ed.antes, ed.depois) if ed else None)
        t('o estorno guarda o motivo', LogFinanceiro.objects.filter(acao='ESTORNAR', depois__motivo='pago na conta errada').exists())
        html = cc.get('/financeiro/auditoria/?q=ZZ Aluguel').content.decode()
        t('a tela de rastreabilidade mostra', 'ZZ Aluguel' in html and '10.9.8.7' in html)
        t('Ana não vê a rastreabilidade (não liberada)', ca.get('/financeiro/auditoria/').status_code == 302)

        print('\n== AS TELAS ==')
        conta_url = f'/financeiro/bancos/{conta.id}/'
        for url in ('/financeiro/', f'/financeiro/caixa/?loja={loja_a.id}', '/financeiro/pagar/', '/financeiro/receber/',
                    '/financeiro/pagar/?situacao=todos', '/financeiro/baixas/', '/financeiro/pagas/', '/financeiro/recebidas/',
                    '/financeiro/bancos/', conta_url, f'/financeiro/conciliacao/?conta={conta.id}',
                    '/financeiro/conciliacao/?situacao=conciliadas', '/financeiro/cartoes/', '/financeiro/plano/',
                    '/financeiro/auditoria/', '/financeiro/acessos/', f'/financeiro/titulo/{p1.id}/'):
            r = cc.get(url)
            t(f'abre {url}', r.status_code == 200, r.status_code)
        html = cc.get(f'/financeiro/caixa/?loja={loja_a.id}').content.decode()
        t('o caixa da loja tem os cinco cartões',
          all(x in html for x in ('Conciliação de Cartões', 'Contas a Pagar', 'Contas a Receber', 'Contas Pagas', 'Contas Recebidas')))
        html = ca.get(f'/financeiro/caixa/?loja={loja_b.id}').content.decode()
        t('Ana não abre o caixa da outra loja', 'Loja ZZ Fin B</option>' not in html)
        cc.post('/financeiro/acessos/', {'user': ana.id, 'remover': '1'})
        ca = Client(**CABECALHOS); ca.force_login(ana)
        t('tirado o acesso, Ana não entra mais', ca.get('/financeiro/').status_code == 302)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
