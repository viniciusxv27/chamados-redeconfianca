"""Despesas recorrentes do Financeiro (04/11/2026). Banco descartável; nada sai para a rede."""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-finrec-'))
(PASTA / 'zz_finrec_settings.py').write_text(f"""
from redeconfianca.settings import *              # noqa: F401,F403

DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3',
                          'NAME': r'{PASTA}/banco.sqlite3'}}}}


class SemMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = SemMigrations()

USE_S3 = False
MEDIA_ROOT = r'{PASTA}/media'
MEDIA_URL = '/media/'
STORAGES = {{
    'default': {{'BACKEND': 'django.core.files.storage.FileSystemStorage'}},
    'staticfiles': {{'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
}}
DEBUG = False
OPENAI_API_KEY = ''
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
CACHES = {{
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-finrec'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-finrec-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_finrec_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

import io
from datetime import date, timedelta
from decimal import Decimal as D

import openpyxl
import requests
from django.utils import timezone

from financeiro import recorrentes as regra, servicos
from financeiro.models import (AcessoFinanceiro, ContaBancaria, DespesaMes, DespesaMesTitulo, DespesaRecorrente,
                               LogFinanceiro, PlanoConta, Titulo)
from financeiro.permissoes import CHAVES, ROTULO_AREA
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


def sem_rede(*a, **k):
    raise AssertionError('o teste tentou sair para a rede')


HOJE = timezone.localdate()
ESTE_MES = HOJE.replace(day=1)
URL = '/financeiro/recorrentes/'


def mes_atras(n):
    return servicos.somar_meses(ESTE_MES, -n)


def despesa(nome='Água Loja A', loja=None, valor='100.00', dia=10, inicio=None, **extra):
    campos = dict(nome=nome, tipo=extra.pop('tipo', 'Água'), loja=loja, valor_esperado=D(valor), dia_vencimento=dia,
                  inicio=inicio or ESTE_MES, **extra)
    return servicos.criar_despesa(campos)


def titulo(descricao, venc, valor='100.00', loja=None, doc='', nome='', plano=None):
    return servicos.criar_titulos({'tipo': 'PAGAR', 'descricao': descricao, 'vencimento': venc, 'valor': D(valor),
                                   'loja': loja, 'pessoa_documento': doc, 'pessoa_nome': nome, 'plano': plano})[0]


with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede):
    loja_a = Sector.objects.create(name='Loja ZZ A', adabas='ZZA1')
    loja_b = Sector.objects.create(name='Loja ZZ B', adabas='ZZB1')
    def pessoa(apelido, **extra):
        return User.objects.create_user(username=f'zzz.{apelido}', email=f'zzz.{apelido}@exemplo-teste.local',
                                        password='S3nha!teste', first_name='ZZ', last_name=apelido, sector=loja_a, **extra)
    chefe = pessoa('chefe', hierarchy='SUPERADMIN')
    so_ve = pessoa('ve')
    editor_a = pessoa('editor')
    sem_pagar = pessoa('sempagar')
    ninguem = pessoa('ninguem')
    AcessoFinanceiro.objects.create(user=so_ve, permissoes=['recorrentes.ver'])
    acesso = AcessoFinanceiro.objects.create(user=editor_a, permissoes=['recorrentes.editar', 'pagar.ver'])
    acesso.lojas.set([loja_a])
    AcessoFinanceiro.objects.create(user=sem_pagar, permissoes=['recorrentes.editar'])
    conta = ContaBancaria.objects.create(nome='Conta ZZ')

    def cliente(u):
        c = Client()
        c.force_login(u)
        return c

    print('Área de permissão')
    t('área nova nas chaves', {'recorrentes.ver', 'recorrentes.editar'} <= CHAVES)
    t('rótulo da área', ROTULO_AREA.get('recorrentes') == 'Despesas recorrentes')
    r = cliente(chefe).get('/financeiro/acessos/')
    t('tela de acessos lista a área', b'Despesas recorrentes' in r.content and b'recorrentes.editar' in r.content)
    r = cliente(ninguem).get(URL)
    t('sem liberação não entra', r.status_code == 302 and '/financeiro' not in r['Location'], r.status_code)
    r = cliente(acesso.user).get('/financeiro/pagar/')
    t('aba nova na navegação', 'financeiro/recorrentes/'.encode() in r.content)
    r = cliente(ninguem).post(URL + 'nova/', {'nome': 'x'})
    t('sem liberação não cadastra', DespesaRecorrente.objects.count() == 0 and r.status_code == 302)

    print('Cadastro')
    c = cliente(chefe)
    dados = {'nome': 'Água Loja A', 'tipo': 'Água', 'loja': loja_a.id, 'fornecedor': 'CESAN', 'fornecedor_documento': '12.345.678/0001-90',
             'valor_esperado': '1.000,50', 'dia_vencimento': '10', 'inicio': mes_atras(1).strftime('%Y-%m'), 'ativa': 'on'}
    r = c.post(URL + 'nova/', dados)
    d = DespesaRecorrente.objects.get()
    t('cria pela tela', d.valor_esperado == D('1000.50') and d.fornecedor_documento == '12345678000190' and d.inicio == mes_atras(1))
    log = LogFinanceiro.objects.filter(area='recorrentes', acao='CRIAR', objeto_id=str(d.pk)).first()
    t('cadastro gera log com loja', log is not None and log.loja_id == loja_a.id and log.usuario_id == chefe.id)
    c.post(URL + 'nova/', dados)
    t('não duplica nome e loja ativos', DespesaRecorrente.objects.count() == 1)
    c.post(URL + 'nova/', dict(dados, nome='Outra', tipo='  agua '))
    outra = DespesaRecorrente.objects.get(nome='Outra')
    t('tipo com outra grafia vira o existente', outra.tipo == 'Água', outra.tipo)
    c.post(URL + 'nova/', dict(dados, nome='Dia ruim', dia_vencimento='32'))
    t('dia fora de 1 a 31 recusado', not DespesaRecorrente.objects.filter(nome='Dia ruim').exists())
    c.post(URL + 'nova/', dict(dados, nome='Valor ruim', valor_esperado='0'))
    t('valor zero recusado', not DespesaRecorrente.objects.filter(nome='Valor ruim').exists())
    c.post(f'{URL}{outra.pk}/editar/', dict(dados, nome='Outra', valor_esperado='200,00', loja=''))
    outra.refresh_from_db()
    t('edita e guarda', outra.valor_esperado == D('200.00') and outra.loja_id is None)
    t('edição tem antes e depois', LogFinanceiro.objects.filter(acao='EDITAR', objeto_id=str(outra.pk), objeto_tipo='despesarecorrente',
                                                                antes__valor_esperado='1000.50').exists())
    r = c.get(f'{URL}{outra.pk}/editar/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    t('formulário de edição vem como fragmento', r.status_code == 200 and b'<html' not in r.content.lower() and b'Editar despesa' in r.content)
    r = c.get(f'{URL}{outra.pk}/editar/')
    t('sem JS abre como página', r.status_code == 200 and b'fin-app' in r.content)
    pk_outra = outra.pk
    c.post(f'{URL}{outra.pk}/excluir/')
    t('sem nada anotado pode excluir', not DespesaRecorrente.objects.filter(pk=pk_outra).exists())
    t('exclusão gera log', LogFinanceiro.objects.filter(acao='EXCLUIR', objeto_id=str(pk_outra), area='recorrentes').exists())

    print('Recorte por loja')
    ed = cliente(editor_a)
    da_b = despesa('Internet Loja B', loja=loja_b, tipo='Internet')
    do_escritorio = despesa('Aluguel Escritório', tipo='Aluguel')
    r = ed.get(URL)
    t('editor vê a loja dele', b'gua Loja A' in r.content)
    t('não vê outra loja nem escritório', b'Internet Loja B' not in r.content and b'Aluguel Escrit' not in r.content)
    t('mês de outra loja dá 404', ed.get(f'{URL}{da_b.pk}/mes/{HOJE.year}/{HOJE.month}/').status_code == 404)
    t('editar de outra loja dá 404', ed.post(f'{URL}{da_b.pk}/editar/', dados).status_code == 404)
    ed.post(URL + 'nova/', dict(dados, nome='Fora', loja=loja_b.id))
    t('não cadastra em loja fora do recorte', not DespesaRecorrente.objects.filter(nome='Fora').exists())
    ed.post(URL + 'nova/', dict(dados, nome='Sem loja', loja=''))
    t('recorte exige loja', not DespesaRecorrente.objects.filter(nome='Sem loja').exists())
    r = cliente(so_ve).post(URL + 'nova/', dict(dados, nome='Só vê'))
    t('quem só vê não cadastra', not DespesaRecorrente.objects.filter(nome='Só vê').exists())
    r = cliente(so_ve).post(f'{URL}{d.pk}/mes/{HOJE.year}/{HOJE.month}/', {'valor': '10,00'})
    t('quem só vê não anota', not DespesaMes.objects.filter(despesa=d).exists())
    r = cliente(so_ve).get(URL)
    t('quem só vê entra', r.status_code == 200)
    t('quem só vê não tem botão de nova', b'recNova' not in r.content)
    da_b.delete(); do_escritorio.delete()

    print('Situação da célula')
    hoje_d = date(2026, 6, 15)
    def desp(valor='100.00', dia=10, inicio=date(2026, 1, 1), ativa=True):
        return DespesaRecorrente(nome='X', tipo='T', valor_esperado=D(valor), dia_vencimento=dia, inicio=inicio, ativa=ativa)
    x = desp()
    t('mês antes do início fica de fora', regra.montar_celula(x, date(2025, 12, 1), None, hoje_d).situacao == 'FORA')
    t('inativa sem dado fica de fora', regra.montar_celula(desp(ativa=False), date(2026, 6, 1), None, hoje_d).situacao == 'FORA')
    cel = regra.montar_celula(x, date(2026, 6, 1), None, hoje_d)
    t('passou do dia sem conta = vencida estimada', cel.situacao == 'VENCIDO' and cel.estimado and cel.valor == D('100.00'))
    cel = regra.montar_celula(desp(dia=20), date(2026, 6, 1), None, hoje_d)
    t('vence em 5 dias = a vencer', cel.situacao == 'A_VENCER' and cel.saldo == D('100.00'))
    cel = regra.montar_celula(desp(dia=23), date(2026, 6, 1), None, hoje_d)
    t('8 dias já é prevista', cel.situacao == 'PREVISTO', cel.situacao)
    cel = regra.montar_celula(desp(dia=22), date(2026, 6, 1), None, hoje_d)
    t('7 dias ainda é a vencer', cel.situacao == 'A_VENCER')
    t('dia 31 em fevereiro cai no último dia', desp(dia=31).vencimento_em(date(2026, 2, 1)) == date(2026, 2, 28))
    cel = regra.montar_celula(desp(dia=15), date(2026, 6, 1), None, hoje_d)
    t('vence hoje não é vencida', cel.situacao == 'A_VENCER')

    print('Variação')
    def serie(valores, limite=D('10'), esperado='100.00'):
        dd = desp(valor=esperado)
        cels = []
        for i, v in enumerate(valores):
            comp = date(2026, i + 1, 1)
            if v is None:
                cels.append(regra.montar_celula(dd, comp, None, date(2030, 1, 1)))
            else:
                reg = DespesaMes(despesa=dd, competencia=comp, valor=D(v), pago_em=date(2026, i + 1, 5), valor_pago=D(v))
                cels.append(_cel(dd, comp, reg))
        regra.calcular_variacoes(cels, limite)
        return cels

    class _SemVinculos:
        def all(self):
            return []

    def _cel(dd, comp, reg):
        # registro em memória: sem consultar vínculos no banco
        falso = mock.Mock(spec=['vinculos', 'vencimento', 'sem_cobranca', 'pago_em', 'valor', 'valor_pago'])
        falso.vinculos = _SemVinculos()
        falso.vencimento, falso.sem_cobranca, falso.pago_em, falso.valor, falso.valor_pago = None, False, reg.pago_em, reg.valor, reg.valor_pago
        return regra.montar_celula(dd, comp, falso, date(2030, 1, 1))

    s = serie(['100', '100', '100', '130'])
    t('estável não acende', not any(c.alerta for c in s[:3]))
    t('salto acima do limite acende', s[3].alerta == 'ALTA' and s[3].variacao == D('30.0'), s[3].variacao)
    t('média dos meses anteriores', s[3].media == D('100'))
    s = serie(['100', '100', '100', '130', '130', '130'])
    t('reajuste que fica avisa uma vez só', [c.alerta for c in s] == ['', '', '', 'ALTA', '', ''], [c.alerta for c in s])
    s = serie(['100', '100', '100', '100', '100', '100', '200', '205'], limite=D('20'))
    t('janela: mudou 1% ou mais volta a avaliar', s[7].alerta == 'ALTA', s[7].variacao)
    s = serie(['100', '100', '70'])
    t('queda acima do limite', s[2].alerta == 'QUEDA' and s[2].variacao == D('-30.0'))
    s = serie(['100', '100', '105'])
    t('abaixo do limite não acende', s[2].alerta == '' and s[2].variacao == D('5.0'))
    s = serie(['100', '100', '105'], limite=D('5'))
    t('limite é configurável', s[2].alerta == 'ALTA')
    s = serie(['150'])
    t('sem histórico compara com o esperado', s[0].alerta == 'ALTA' and s[0].base_esperado and s[0].media == D('100.00'))
    s = serie(['100', None, '100', '100', '150'])
    t('mês só estimado não entra na média', s[1].valor_real is None and s[4].media == D('100'))
    t('estimativa não ganha variação', s[1].variacao is None)
    s = serie(['100'] * 2 + ['10'] * 7)
    t('só entram 6 meses na média', s[8].media == D('10'), s[8].media)
    t('aumentos seguidos', regra.aumentos_seguidos([D(100), D(110), D(120), D(130)]) == 3)
    t('sequência quebra na queda', regra.aumentos_seguidos([D(100), D(130), D(120), D(125)]) == 1)
    t('valor igual não conta como aumento', regra.aumentos_seguidos([D(100), D(100)]) == 0)
    t('um valor só não tem sequência', regra.aumentos_seguidos([D(100)]) == 0)

    print('Título vinculado')
    venc = ESTE_MES.replace(day=10)
    plano = PlanoConta.objects.create(codigo='2.37.01', nome='AGUA', tipo='DESPESA')
    tit = titulo('AGUA LOJA A', venc, '1100.50', loja_a, '12345678000190', 'CESAN', plano)
    n_antes = Titulo.objects.count()
    sv = servicos.vincular_titulo(d, ESTE_MES, tit, usuario=chefe)
    t('vincular não cria título', Titulo.objects.count() == n_antes)
    t('vincular gera log', LogFinanceiro.objects.filter(area='recorrentes', descricao__startswith='Vinculou', loja=loja_a).exists())
    reg = regra.carregar_registros([d])[d.id][ESTE_MES]
    cel = regra.montar_celula(d, ESTE_MES, reg, HOJE)
    t('valor vem do título', cel.valor == D('1100.50') and not cel.estimado)
    t('aberto não é pago', cel.situacao != 'PAGO')
    baixa = servicos.baixar(tit, HOJE, D('500.00'), conta, 'PIX', usuario=chefe)
    reg = regra.carregar_registros([d])[d.id][ESTE_MES]
    cel = regra.montar_celula(d, ESTE_MES, reg, date(2000, 1, 1))
    t('baixa parcial = parcial com saldo', cel.situacao == 'PARCIAL' and cel.saldo == D('600.50') and cel.parcial, cel.situacao)
    servicos.baixar(tit, HOJE, D('600.50'), conta, 'PIX', juros=D('10.00'), usuario=chefe)
    reg = regra.carregar_registros([d])[d.id][ESTE_MES]
    cel = regra.montar_celula(d, ESTE_MES, reg, HOJE)
    t('título quitado marca pago', cel.situacao == 'PAGO' and cel.pago_em == HOJE)
    t('pago traz juros; valor da conta não', cel.pago_valor == D('1110.50') and cel.valor == D('1100.50'))
    servicos.estornar_baixa(baixa, 'teste de estorno', usuario=chefe)
    reg = regra.carregar_registros([d])[d.id][ESTE_MES]
    cel = regra.montar_celula(d, ESTE_MES, reg, date(2000, 1, 1))
    t('estornar a baixa tira o pago', cel.situacao == 'PARCIAL', cel.situacao)
    d2 = despesa('Segunda', loja=loja_a)
    try:
        servicos.vincular_titulo(d2, ESTE_MES, tit)
        t('título não vai em dois meses', False)
    except servicos.RegraFinanceira as exc:
        t('título não vai em dois meses', 'Água Loja A' in str(exc))
    tit2 = titulo('AGUA 2', venc, '50.00', loja_a, '12345678000190', 'CESAN')
    servicos.vincular_titulo(d, ESTE_MES, tit2, usuario=chefe)
    reg = regra.carregar_registros([d])[d.id][ESTE_MES]
    cel = regra.montar_celula(d, ESTE_MES, reg, HOJE)
    t('dois títulos no mês somam', cel.valor == D('1150.50'))
    servicos.cancelar_titulo(tit2, 'cancelado no teste', usuario=chefe)
    reg = regra.carregar_registros([d])[d.id][ESTE_MES]
    cel = regra.montar_celula(d, ESTE_MES, reg, HOJE)
    t('título cancelado sai da conta', cel.valor == D('1100.50'))
    receber = servicos.criar_titulos({'tipo': 'RECEBER', 'descricao': 'R', 'vencimento': venc, 'valor': D('5')})[0]
    try:
        servicos.vincular_titulo(d, ESTE_MES, receber)
        t('título a receber recusado', False)
    except servicos.RegraFinanceira:
        t('título a receber recusado', True)
    try:
        servicos.salvar_mes_despesa(d, ESTE_MES, valor=D('1.00'))
        t('com título, valor à mão é recusado', False)
    except servicos.RegraFinanceira:
        t('com título, valor à mão é recusado', True)
    servicos.salvar_mes_despesa(d, ESTE_MES, observacao='anotação', usuario=chefe)
    t('com título, observação pode', DespesaMes.objects.get(despesa=d, competencia=ESTE_MES).observacao == 'anotação')
    tit3 = titulo('Excluível', venc, '20.00', loja_a)
    v3 = servicos.vincular_titulo(d, ESTE_MES, tit3, usuario=chefe)
    servicos.excluir_titulo(tit3, usuario=chefe)
    t('excluir título vinculado não quebra e solta o vínculo', not DespesaMesTitulo.objects.filter(pk=v3.pk).exists())
    try:
        servicos.excluir_despesa(d)
        t('com histórico não exclui', False)
    except servicos.RegraFinanceira as exc:
        t('com histórico não exclui', 'desative' in str(exc))
    v = DespesaMesTitulo.objects.get(titulo=tit)
    r = c.post(f'{URL}vinculo/{v.pk}/desvincular/')
    t('desvincular mantém o título', Titulo.objects.filter(pk=tit.pk).exists() and not DespesaMesTitulo.objects.filter(titulo=tit).exists())
    t('desvincular gera log', LogFinanceiro.objects.filter(descricao__startswith='Desvinculou').exists())

    print('Anotar à mão')
    outro_mes = mes_atras(1)
    n_tit, n_mov = Titulo.objects.count(), conta.movimentos.count()
    url_mes = f'{URL}{d.pk}/mes/{outro_mes.year}/{outro_mes.month}/'
    c.post(url_mes, {'valor': '900,00', 'pago_em': outro_mes.replace(day=12).isoformat(), 'observacao': 'boleto em papel'})
    reg = DespesaMes.objects.get(despesa=d, competencia=outro_mes)
    t('anotação grava valor e pago', reg.valor == D('900.00') and reg.pago_em == outro_mes.replace(day=12) and reg.valor_pago == D('900.00'))
    t('anotar não cria título nem mexe no banco', Titulo.objects.count() == n_tit and conta.movimentos.count() == n_mov)
    t('anotação gera log', LogFinanceiro.objects.filter(objeto_tipo='despesames', objeto_id=str(reg.pk), depois__valor='900.00').exists())
    cel = regra.montar_celula(d, outro_mes, regra.carregar_registros([d])[d.id][outro_mes], HOJE)
    t('pago depois do vencimento conta os dias', cel.situacao == 'PAGO' and cel.dias_atraso_pago == 2, cel.dias_atraso_pago)
    amanha = HOJE + timedelta(days=1)
    c.post(url_mes, {'valor': '900,00', 'pago_em': amanha.isoformat()})
    reg.refresh_from_db()
    t('pagamento no futuro recusado', reg.pago_em == outro_mes.replace(day=12))
    d3 = despesa('Terceira', loja=loja_a, inicio=mes_atras(6))
    c.post(f'{URL}{d3.pk}/mes/{outro_mes.year}/{outro_mes.month}/', {'pago_em': outro_mes.isoformat()})
    t('pago sem valor recusado', not DespesaMes.objects.filter(despesa=d3, pago_em__isnull=False).exists())
    c.post(f'{URL}{d3.pk}/mes/{outro_mes.year}/{outro_mes.month}/', {'valor': '-5'})
    t('valor negativo recusado', not DespesaMes.objects.filter(despesa=d3, valor__isnull=False).exists())
    c.post(f'{URL}{d3.pk}/mes/{outro_mes.year}/{outro_mes.month}/', {'sem_cobranca': 'on'})
    reg3 = DespesaMes.objects.get(despesa=d3, competencia=outro_mes)
    cel = regra.montar_celula(d3, outro_mes, regra.carregar_registros([d3])[d3.id][outro_mes], HOJE)
    t('sem cobrança para de cobrar', cel.situacao == 'SEM_COBRANCA' and cel.valor is None and cel.valor_real is None)
    c.post(f'{URL}{d3.pk}/mes/{outro_mes.year}/{outro_mes.month}/', {'limpar': '1', 'sem_cobranca': 'on', 'valor': '5'})
    reg3.refresh_from_db()
    t('limpar apaga a anotação', not reg3.sem_cobranca and reg3.valor is None)
    t('mês inválido dá 404', c.get(f'{URL}{d.pk}/mes/2026/13/').status_code == 404)
    t('ano fora da faixa dá 404', c.get(f'{URL}{d.pk}/mes/1/1/').status_code == 404
      and c.get(f'{URL}{d.pk}/mes/99999999999999/1/').status_code == 404)
    r = c.get(f'{URL}{d.pk}/mes/{HOJE.year}/{HOJE.month}/?voltar=javascript:alert(1)')
    t('voltar não aceita endereço de fora', b'javascript:alert' not in r.content)
    r = c.post(url_mes + '?x=1', {'valor': '900,00', 'pago_em': outro_mes.replace(day=12).isoformat(),
                                     'voltar': 'https://exemplo-teste.local/'})
    t('redirecionamento fica no Financeiro', r['Location'].startswith('/financeiro/'))

    print('Sugestão de título')
    def casa(dd, tt):
        return regra.titulo_casa(dd, tt)
    forn = DespesaRecorrente(nome='I', tipo='I', valor_esperado=D('99.99'), dia_vencimento=5, inicio=ESTE_MES,
                             fornecedor_documento='02558157000162', loja=None, termo_titulo='sala 101')
    tt = Titulo(tipo='PAGAR', descricao='INTERNET ESCRITÓRIO SALA 101', pessoa_documento='02558157012345', valor=D('99.99'), vencimento=venc)
    t('mesma raiz do CNPJ e palavra casam', casa(forn, tt))
    tt2 = Titulo(tipo='PAGAR', descricao='INTERNET ESCRITÓRIO SALA 201', pessoa_documento='02558157012345', valor=D('89.99'), vencimento=venc)
    t('outra palavra não casa', not casa(forn, tt2))
    tt3 = Titulo(tipo='PAGAR', descricao='SALA 101', pessoa_documento='02558157012345', loja=loja_a, valor=D('99.99'), vencimento=venc)
    t('outra loja não casa', not casa(forn, tt3))
    vazio = DespesaRecorrente(nome='I', tipo='I', valor_esperado=D('1'), dia_vencimento=5, inicio=ESTE_MES)
    t('sem fornecedor, plano nem palavra nada casa', not casa(vazio, tt))
    t('nome de plano igual em grupo diferente casa', regra.titulo_casa(
        DespesaRecorrente(nome='I', tipo='I', valor_esperado=D('1'), dia_vencimento=5, inicio=ESTE_MES,
                          plano=PlanoConta.objects.create(codigo='9.01', nome='Água', tipo='DESPESA')),
        Titulo(tipo='PAGAR', descricao='x', valor=D('1'), vencimento=venc, plano=plano)))
    t('um candidato vira sugestão', regra._sugestao(forn, [tt]) is tt)
    t('dois candidatos sem valor exato: sem sugestão', regra._sugestao(forn, [tt2, Titulo(valor=D('50'))]) is None)
    t('valor exato único desempata', regra._sugestao(forn, [tt2, tt]) is tt)

    print('Tela e KPIs')
    a = despesa('Luz Loja A', loja=loja_a, tipo='Energia', valor='200.00', dia=28, inicio=mes_atras(3),
                fornecedor_documento='98765432000100')
    for n, valor in ((3, '200,00'), (2, '200,00'), (1, '200,00')):
        m = mes_atras(n)
        c.post(f'{URL}{a.pk}/mes/{m.year}/{m.month}/', {'valor': valor, 'pago_em': m.replace(day=5).isoformat()})
    c.post(f'{URL}{a.pk}/mes/{ESTE_MES.year}/{ESTE_MES.month}/', {'valor': '300,00', 'vencimento': HOJE.isoformat()})
    atrasada = despesa('Aluguel Loja A', loja=loja_a, tipo='Aluguel', valor='1000.00', dia=1, inicio=mes_atras(2))
    r = c.get(URL + f'?ano={ESTE_MES.year}&mes={ESTE_MES.month}&loja={loja_a.id}')
    g = r.context['grade']
    t('tela abre', r.status_code == 200)
    t('variação do mês listada', any(x.despesa.pk == a.pk and x.alerta == 'ALTA' and x.variacao == D('50.0') for x in g.variacoes))
    t('a vencer conta', g.kpi['a_vencer']['n'] >= 1 and any(x.despesa.pk == a.pk for x in g.atencao))
    venc_atr = [x for x in g.atencao if x.despesa.pk == atrasada.pk]
    t('vencida inclui meses sem conta desde o início', len(venc_atr) >= 2 and all(x.situacao == 'VENCIDO' for x in venc_atr), len(venc_atr))
    t('total do mês vs anterior', g.kpi['total_mes'] > 0 and g.kpi['total_anterior'] > 0 and g.kpi['delta'] == g.kpi['total_mes'] - g.kpi['total_anterior'])
    t('filtro de loja', all(ln['despesa'].loja_id == loja_a.id for ln in g.linhas))
    r = c.get(URL + f'?tipo=Energia&mes={ESTE_MES.month}&ano={ESTE_MES.year}')
    t('filtro de tipo', [ln['despesa'].tipo for ln in r.context['grade'].linhas] == ['Energia'])
    r = c.get(URL + '?so=atraso')
    t('filtro só atrasadas', all(any(x.situacao == 'VENCIDO' for x in ln['todas'].values()) for ln in r.context['grade'].linhas))
    r = c.get(URL + f'?so=alerta&ano={ESTE_MES.year}&mes={ESTE_MES.month}')
    t('filtro só variações', [ln['despesa'].pk for ln in r.context['grade'].linhas] == [a.pk])
    r = c.get(URL + f'?limite=60&ano={ESTE_MES.year}&mes={ESTE_MES.month}&tipo=Energia')
    t('limite maior apaga o aviso', r.context['grade'].kpi['alertas'] == 0)
    for ruim in ('abc', 'NaN', 'Infinity', '-5'):
        t(f'limite {ruim} não derruba', c.get(URL + f'?limite={ruim}').status_code == 200)
    t('ano inválido não derruba', c.get(URL + '?ano=99999&mes=99').status_code == 200)
    r = c.get(URL + f'?ano={ESTE_MES.year - 1}')
    t('outro ano abre e mantém vencidas', r.status_code == 200 and r.context['grade'].kpi['vencidas']['n'] >= 2)
    r = c.get(URL + '?inativas=1')
    t('tela com inativas', r.status_code == 200)
    html = c.get(URL).content.decode()
    t('sem comentário de template vazando', '{#' not in html and '{%' not in html)
    frag = c.get(f'{URL}{a.pk}/mes/{ESTE_MES.year}/{ESTE_MES.month}/?voltar=/financeiro/recorrentes/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    t('mês vem como fragmento', frag.status_code == 200 and b'<html' not in frag.content.lower() and b'Anotar' in frag.content)
    t('mês mostra a variação', '+50,0%'.encode() in frag.content)
    t('mês em página sem JS', c.get(f'{URL}{a.pk}/mes/{ESTE_MES.year}/{ESTE_MES.month}/').status_code == 200)
    from django.test.utils import CaptureQueriesContext
    from django.db import connection
    with CaptureQueriesContext(connection) as q1:
        c.get(URL)
    n1 = len(q1)
    for i in range(6):
        despesa(f'Extra {i}', loja=loja_a, tipo='Software', inicio=mes_atras(1))
    with CaptureQueriesContext(connection) as q2:
        c.get(URL)
    t('consultas não crescem por despesa', len(q2) - n1 <= 2, (n1, len(q2)))

    print('Vincular sugestões e lançar')
    forn_doc = '11222333000144'
    alvo = despesa('Internet Loja A', loja=loja_a, tipo='Internet', valor='150.00', fornecedor_documento=forn_doc, inicio=ESTE_MES)
    tit_s = titulo('INTERNET', ESTE_MES.replace(day=5), '155.00', loja_a, forn_doc, 'VIVO')
    r = c.get(URL + f'?tipo=Internet&ano={ESTE_MES.year}&mes={ESTE_MES.month}')
    t('sugestão aparece', [x.sugestao.pk for x in r.context['grade'].sugestoes] == [tit_s.pk])
    t('botão de vincular', 'Vincular todos' in r.content.decode())
    t('sugerir não vincula sozinho', not DespesaMesTitulo.objects.filter(titulo=tit_s).exists())
    t('sugerir não lança título', Titulo.objects.filter(pessoa_documento=forn_doc).count() == 1)
    gemea = despesa('Internet Gêmea', loja=loja_a, tipo='Internet', valor='150.00', fornecedor_documento=forn_doc, inicio=ESTE_MES)
    r = c.get(URL + f'?tipo=Internet&ano={ESTE_MES.year}&mes={ESTE_MES.month}')
    t('duas despesas no mesmo título: sem sugestão', r.context['grade'].sugestoes == [])
    gemea.valor_esperado = D('155.00'); gemea.save()
    r = c.get(URL + f'?tipo=Internet&ano={ESTE_MES.year}&mes={ESTE_MES.month}')
    t('no empate leva a do valor exato', [x.despesa.pk for x in r.context['grade'].sugestoes] == [gemea.pk])
    gemea.ativa = False; gemea.save()
    rr = cliente(sem_pagar).get(URL + f'?tipo=Internet&ano={ESTE_MES.year}&mes={ESTE_MES.month}')
    t('sem Contas a Pagar não vê sugestão', rr.context['grade'].sugestoes == [] and b'Vincular todos' not in rr.content)
    rr = cliente(sem_pagar).post(f'{URL}{alvo.pk}/mes/{ESTE_MES.year}/{ESTE_MES.month}/vincular/', {'titulo': tit_s.pk})
    t('sem Contas a Pagar não vincula', not DespesaMesTitulo.objects.filter(titulo=tit_s).exists())
    rr = cliente(sem_pagar).get(f'{URL}{alvo.pk}/mes/{ESTE_MES.year}/{ESTE_MES.month}/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    t('sem Contas a Pagar o mês não lista títulos', b'Vincular t' not in rr.content and b'Lan\xc3\xa7ar em Contas' not in rr.content)
    outro_tit = titulo('Título da loja B', ESTE_MES.replace(day=6), '10.00', loja_b, forn_doc, 'VIVO')
    ed.post(f'{URL}{alvo.pk}/mes/{ESTE_MES.year}/{ESTE_MES.month}/vincular/', {'titulo': outro_tit.pk})
    t('recorte impede vincular título de outra loja', not DespesaMesTitulo.objects.filter(titulo=outro_tit).exists())
    frag = c.get(f'{URL}{alvo.pk}/mes/{ESTE_MES.year}/{ESTE_MES.month}/?buscar=loja B', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    t('busca acha outro título', 'Título da loja B'.encode() in frag.content)
    frag = ed.get(f'{URL}{alvo.pk}/mes/{ESTE_MES.year}/{ESTE_MES.month}/?buscar=loja B', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    t('busca respeita o recorte', 'Título da loja B'.encode() not in frag.content)
    r = c.post(URL + f'vincular-sugestoes/?tipo=Internet&ano={ESTE_MES.year}&mes={ESTE_MES.month}')
    t('vincular todos liga a sugestão', DespesaMesTitulo.objects.filter(titulo=tit_s, mes__despesa=alvo).exists())
    d_lanc = despesa('Condomínio Loja A', loja=loja_a, tipo='Condomínio', valor='700.00', fornecedor='Condomínio X',
                     fornecedor_documento='55666777000188', plano=plano, inicio=ESTE_MES)
    n = Titulo.objects.count()
    cliente(sem_pagar).post(f'{URL}{d_lanc.pk}/mes/{ESTE_MES.year}/{ESTE_MES.month}/lancar/', {'valor': '700,00', 'vencimento': venc.isoformat()})
    t('sem pagar.editar não lança', Titulo.objects.count() == n)
    so_ver_pagar = cliente(editor_a).post(f'{URL}{d_lanc.pk}/mes/{ESTE_MES.year}/{ESTE_MES.month}/lancar/', {'valor': '700,00', 'vencimento': venc.isoformat()})
    t('só pagar.ver não lança', Titulo.objects.count() == n)
    c.get(f'{URL}{d_lanc.pk}/mes/{ESTE_MES.year}/{ESTE_MES.month}/')
    c.get(URL)
    t('abrir telas não lança nada', Titulo.objects.count() == n)
    c.post(f'{URL}{d_lanc.pk}/mes/{ESTE_MES.year}/{ESTE_MES.month}/lancar/', {'valor': '720,00', 'vencimento': venc.isoformat()})
    novo = Titulo.objects.filter(descricao__startswith='Condomínio Loja A').first()
    t('lançar cria um título a pagar', Titulo.objects.count() == n + 1 and novo.valor == D('720.00') and novo.tipo == 'PAGAR')
    t('título herda loja, plano, fornecedor e mês', novo.loja_id == loja_a.id and novo.plano_id == plano.id
      and novo.pessoa_documento == '55666777000188' and novo.competencia == ESTE_MES)
    t('título lançado já vem vinculado', DespesaMesTitulo.objects.filter(titulo=novo, mes__despesa=d_lanc).exists())
    t('lançamento fica no log de contas a pagar', LogFinanceiro.objects.filter(area='pagar', acao='CRIAR', objeto_id=str(novo.pk), usuario=chefe).exists())
    c.post(f'{URL}{d_lanc.pk}/mes/{ESTE_MES.year}/{ESTE_MES.month}/lancar/', {'valor': '720,00', 'vencimento': venc.isoformat()})
    t('não lança duas vezes o mesmo mês', Titulo.objects.count() == n + 1)
    c.post(url_mes + 'lancar/', {'valor': '10,00', 'vencimento': venc.isoformat()})
    t('mês pago à mão não lança', Titulo.objects.count() == n + 1)
    c.post(url_mes + 'vincular/', {'titulo': outro_tit.pk})
    t('mês pago à mão não vincula', not DespesaMesTitulo.objects.filter(titulo=outro_tit).exists())
    frag = c.get(f'{URL}{d_lanc.pk}/mes/{ESTE_MES.year}/{ESTE_MES.month}/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    t('com título o mês oferece desvincular', b'Desvincular' in frag.content and 'Lançar em Contas'.encode() not in frag.content)

    print('Excel')
    r = c.get(URL + f'excel/?ano={ESTE_MES.year}&loja={loja_a.id}')
    t('baixa xlsx', r.status_code == 200 and r['Content-Type'].startswith('application/vnd.openxmlformats'))
    livro = openpyxl.load_workbook(io.BytesIO(r.content))
    folha = livro.worksheets[0]
    t('duas abas', len(livro.worksheets) == 2)
    t('cabeçalho com os meses', folha.cell(1, 7).value == f'Jan/{ESTE_MES.year}' and folha.cell(1, 18).value == f'Dez/{ESTE_MES.year}')
    linha_luz = next(row for row in folha.iter_rows(min_row=2) if row[0].value == 'Luz Loja A')
    t('valor do mês na coluna certa', linha_luz[6 + ESTE_MES.month - 1].value == 300)
    t('exportar gera log', LogFinanceiro.objects.filter(acao='EXPORTAR', area='recorrentes').exists())
    t('quem só vê exporta', cliente(so_ve).get(URL + 'excel/').status_code == 200)
    r = ed.get(URL + 'excel/')
    nomes = [row[0].value for row in openpyxl.load_workbook(io.BytesIO(r.content)).worksheets[0].iter_rows(min_row=2)]
    t('Excel respeita o recorte', 'Internet Loja B' not in nomes)
    t('nome com = não vira fórmula', regra._texto_seguro('=SOMA(A1)') == "'=SOMA(A1)")
    g_vazia = regra.montar_grade([], ESTE_MES.year, ESTE_MES.month, HOJE)
    t('planilha sem linhas não quebra', bool(regra.planilha(g_vazia)))
    t('tela sem nada não quebra', cliente(so_ve).get(URL + '?tipo=Nada').status_code == 200)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
