"""/fibras (27/11/2026): B2B no módulo, "3.6 ENCERRADA" = Problema, ajuste manual com prioridade
em toda importação/cruzamento e o "por quê" de cada venda na lista.

Transação desfeita no fim; cache em memória (o Redis de verdade não é tocado); datas de 2031;
o MySQL do Vivo GO e o aviso ao vendedor não são chamados.
"""
import io
import os
import sys
from datetime import date, datetime
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-b2b'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-b2b2'}}
django.setup()
from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import pandas as pd
import requests
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client

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


D = date(2031, 3, 10)


def analitico(linhas):
    """ANALÍTICO DE VENDAS com as colunas do arquivo real: (ordem, status, agenda)."""
    cols = ['ANOMES', 'DATA_CRIACAO', 'DATA_MOVIMENTO', 'DATA_AGENDA', 'PERIODO_AGENDA', 'SLA AGENDA',
            'ETAPA INSTALACAO', 'STATUS INSTALACAO', 'MOTIVO', 'FONTE', 'ORDEM', 'CPF', 'ORIGEM', 'SEGMENTO_CLIENTE',
            'SERVICO', 'PRODUTO', 'CANAL INSTALACAO', 'ADABAS', 'REGIONAL', 'TERRITORIO', 'UF', 'CIDADE_CLIENTE',
            'ARMARIO', 'LOGRADOURO', 'CEP', 'NUMERO', 'LOGIN', 'NOME VENDEDOR', 'RESULTADO VISITA', 'TIPO PAGAMENTO',
            'ENVIO PAGAMENTO', 'ATUALIZACAO CONTATO', 'CONTATO OK ', 'QUALIDADE CONTATO ', 'VENDA COMISSIONADA']
    etapa = {'1': '1. INSTALADA', '2': '2. CANCELADA', '3': '3. PENDENTE'}
    rows = []
    for ordem, status, agenda in linhas:
        r = dict.fromkeys(cols)
        r.update({'ANOMES': 203103, 'DATA_CRIACAO': pd.Timestamp(D), 'DATA_MOVIMENTO': pd.Timestamp(D),
                  'DATA_AGENDA': pd.Timestamp(agenda) if agenda else None, 'ETAPA INSTALACAO': etapa[status[0]],
                  'STATUS INSTALACAO': status, 'ORDEM': int(ordem), 'SEGMENTO_CLIENTE': 'B2C',
                  'PRODUTO': 'VIVO TOTAL ESSENCIAL 600MEGA', 'ADABAS': 'ZZB001', 'NOME VENDEDOR': 'ZZ VENDEDOR',
                  'VENDA COMISSIONADA': 'S'})
        rows.append(r)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        pd.DataFrame(rows, columns=cols).to_excel(w, index=False, sheet_name='Export')
    return buf.getvalue()


marcador = transaction.atomic()
marcador.__enter__()
try:
    from fibras import cruzamento as cz
    from fibras import quadro, services
    from fibras.models import AcessoFibras, Fibra
    from fibras.permissoes import CHAVES, completar
    from users.models import Sector

    print('== Códigos da Esteira ==')
    m = services._map_planilha_status
    t('3.6 ENCERRADA → Problema', m('3.6 ENCERRADA') == Fibra.STATUS_PROBLEMA)
    t('3.7 CANCELADA → Cancelado', m('3.7 CANCELADA') == Fibra.STATUS_CANCELADO)
    t('3.0 PENDENTE INSTALAÇÃO → Pendente', m('3.0 PENDENTE INSTALAÇÃO') == Fibra.STATUS_PENDENTE)
    t('2.6 SEM IDENTIFICAÇÃO → Cancelado', m('2.6 SEM IDENTIFICAÇÃO') == Fibra.STATUS_CANCELADO)
    t('1.1 INSTALADA → Instalado', m('1.1 INSTALADA') == Fibra.STATUS_INSTALADO)
    t('3.5 AGENDADA → Agendado', m('3.5 AGENDADA') == Fibra.STATUS_AGENDADO)

    print('== B2B ==')
    c = services._campos_do_mysql({'cpf': None, 'documento': '01.834.552/0001-68', 'numero_protocolo': 'S000007529271',
                                   'data_da_venda': D})
    t('sync guarda o CNPJ do Documento_do_cliente quando o CPF vem vazio', c['cpf'] == '01.834.552/0001-68', c['cpf'])
    c = services._campos_do_mysql({'cpf': '123.456.789-09', 'documento': 'x', 'data_da_venda': D})
    t('com CPF, o CPF continua', c['cpf'] == '123.456.789-09')
    t('CNPJ + protocolo S0000… é B2B', cz.eh_b2b('S000007529271', '01.834.552/0001-68'))
    t('CNPJ + protocolo numérico fora do padrão é B2B', cz.eh_b2b('123456', '01834552000168'))
    t('CNPJ + ORDEM 8… cruza normal', not cz.eh_b2b('8091032161', '01834552000168'))
    t('CNPJ + ORDEM 7… cruza normal', not cz.eh_b2b('7100000001', '01834552000168'))
    t('CPF + protocolo alfanumérico não é B2B', not cz.eh_b2b('S000007529271', '12345678909'))
    t('ORDEM com 8 é "Padrão ORDEM"', cz.formato_protocolo('8091032161') == 'Padrão ORDEM')

    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede), \
            mock.patch.object(services, 'sync_fibras', return_value={'created': 0, 'updated': 0, 'total_in_source': 0}), \
            mock.patch.object(services, '_notify_vendor', lambda *a, **k: None):
        Sector.objects.create(name='ZZ LOJA B2B', adabas='ZZB001')
        ilha = User.objects.create_user(username='zzb2b.ilha', email='zzb2b.ilha@exemplo-teste.local', password='x',
                                        first_name='ZZ', last_name='Ilha', hierarchy='SUPERVISOR')
        AcessoFibras.objects.create(user=ilha, permissoes=completar(CHAVES))
        ci = Client()
        ci.force_login(ilha)

        def fibra(venda, prot, cpf='12345678909', **kw):
            return Fibra.objects.create(numero_da_venda=venda, numero_protocolo=prot, pdv='ZZ LOJA B2B', data_da_venda=D,
                                        cpf=cpf, cliente=f'Cliente {venda}', vendedor='ZZ VENDEDOR', pilar='FIXA',
                                        servico_tecnico='Alta Banda Larga', valor=100,
                                        status=kw.pop('status', Fibra.STATUS_AGENDADO), **kw)

        f_enc = fibra('ZZB1', '8100000001')                                   # Esteira: 3.6 ENCERRADA
        f_st = fibra('ZZB2', '8100000002', status=Fibra.STATUS_PENDENTE)      # Esteira: 1.1 INSTALADA, mas à mão
        f_ag = fibra('ZZB3', '8100000003')                                    # agenda à mão ≠ da Esteira
        f_vinc = fibra('ZZB4', '9999')                                        # ORDEM vinculada à mão, fora da Esteira
        f_b2b = fibra('ZZB5', 'S000007529271', cpf='01.834.552/0001-68')       # B2B
        f_nl = fibra('ZZB6', '8100000099')                                    # sem ORDEM: Não localizado
        services.change_status(f_st, Fibra.STATUS_PENDENTE, changed_by=ilha)
        services.mudar_agendamento(f_ag, date(2031, 3, 20), changed_by=ilha)
        Fibra.objects.filter(pk=f_vinc.pk).update(ordem_manual='8100000077')

        arq = analitico([('8100000001', '3.6 ENCERRADA', None), ('8100000002', '1.1 INSTALADA', None),
                         ('8100000003', '3.5 AGENDADA', '2031-03-25 08:30')])
        for rodada in (1, 2):
            print(f'== Importação {rodada} da Esteira ==')
            r = ci.post('/fibras/importar/', {'planilha': SimpleUploadedFile('ANALITICO DE VENDAS.xlsx', arq),
                                              'voltar': '/fibras/?periodo=todos'})
            t('importou', r.status_code == 302, r.status_code)
            for f in (f_enc, f_st, f_ag, f_vinc, f_b2b, f_nl):
                f.refresh_from_db()
            t('3.6 ENCERRADA virou Problema', f_enc.status == Fibra.STATUS_PROBLEMA, f_enc.status)
            t('status posto à mão não mudou (Esteira diz instalada)', f_st.status == Fibra.STATUS_PENDENTE
              and f_st.status_manual, f_st.status)
            t('agendamento posto à mão não mudou', f_ag.data_agendamento == date(2031, 3, 20) and f_ag.agendamento_manual,
              f_ag.data_agendamento)
            t('vínculo à mão mantido com a ORDEM fora da Esteira', f_vinc.ordem_vinculada == '8100000077'
              and f_vinc.assertividade == 'Manual' and 'mantido' in f_vinc.motivo_cruzamento,
              (f_vinc.ordem_vinculada, f_vinc.assertividade, f_vinc.motivo_cruzamento))
            t('B2B marcada como Venda B2B', f_b2b.status_cruzamento == 'Venda B2B', f_b2b.status_cruzamento)
            t('sem ORDEM: Não localizada', f_nl.status_cruzamento == 'Não localizada', f_nl.status_cruzamento)

        print('== A lista ==')
        r = ci.get('/fibras/', {'periodo': 'todos', 'pdv': 'ZZ LOJA B2B'})
        col = {f.numero_da_venda: f.coluna for c in r.context['colunas'] for f in c['items']}
        t('manual fica no status dele, não em Não localizado', col['ZZB2'] == Fibra.STATUS_PENDENTE, col['ZZB2'])
        t('vínculo à mão não vai para Não localizado', col['ZZB4'] != quadro.NAO_LOCALIZADO, col['ZZB4'])
        t('B2B fica no status dela, não em Não localizado', col['ZZB5'] == Fibra.STATUS_AGENDADO, col['ZZB5'])
        t('venda sem ORDEM vai para Não localizado', col['ZZB6'] == quadro.NAO_LOCALIZADO, col['ZZB6'])
        h = r.content.decode()
        t('coluna "Por quê" com a frase e os detalhes', '>Por quê</th>' in h and 'ver detalhes' in h)
        t('por quê do manual diz quem pôs à mão', 'Pendente posto à mão por ZZ Ilha' in h)
        t('por quê da ENCERRADA cita a Esteira', '3.6 ENCERRADA' in h and 'Vendas com problema: a ORDEM 8100000001' in h)
        t('por quê da B2B', 'venda B2B (cliente com CNPJ)' in h)
        t('por quê do Não localizado', 'Não localizado: a venda nunca casou' in h)
        t('por quê do vínculo à mão', 'vinculada à mão na Validação' in h)
        t('selo B2B na linha', 'class="fb-tag b2b"' in h)
        r = ci.get('/fibras/', {'periodo': 'todos', 'b2b': '1'})
        t('filtro B2B traz a venda com CNPJ', [f.numero_da_venda for c in r.context['colunas'] for f in c['items']
                                              if f.numero_da_venda.startswith('ZZB')] == ['ZZB5'])
finally:
    marcador.__exit__(Exception, Exception('desfaz'), None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
