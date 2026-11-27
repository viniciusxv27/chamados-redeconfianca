"""/fibras: parecer manual x nova Esteira, data de agendamento, CPF sem pontos e seta de voltar (16/11/2026).

Transação desfeita no fim; datas de 2031; o MySQL do Vivo GO não é chamado.
"""
import io
import json
import os
import sys
from datetime import date
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
# Cache em memória ANTES do setup: com o Redis de verdade, o quadro do /fibras guardava o estado de
# dentro da transação deste teste e a tela real mostrava esses dados por 5 min (27/11/2026).
from django.conf import settings as _s
_s.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-teste'},
             'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-teste2'}}
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import pandas as pd
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
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


def planilha(linhas):
    """ANALÍTICO DE VENDAS com uma linha de título antes do cabeçalho."""
    cols = ['DATA_CRIAÇÃO', 'ETAPA INSTALACAO', 'STATUS INSTALACAO', 'ORDEM', 'PRODUTO', 'ADABAS', 'LOGRADOURO',
            'CEP', 'NUMERO', 'LOGIN', 'NOME VENDEDOR', 'VENDA COMISSIONADA', 'SLA AGENDA', 'MOTIVO', 'DATA_AGENDA']
    df = pd.DataFrame(linhas, columns=cols)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        pd.DataFrame([['RELATÓRIO ANALÍTICO DE VENDAS']]).to_excel(w, index=False, header=False, startrow=0)
        df.to_excel(w, index=False, startrow=1)
    return buf.getvalue()


marcador = transaction.atomic()
marcador.__enter__()
try:
    from fibras import services
    from fibras.models import EsteiraVenda, Fibra, FibraStatusHistory
    from fibras.permissoes import CHAVES
    from fibras.models import AcessoFibras
    from users.models import Sector

    Sector.objects.create(name='ZZ LOJA PARECER', adabas='ZZP001')
    ilha = User.objects.create_user(username='zzpar.ilha', email='zzpar.ilha@exemplo-teste.local', password='S3nha!teste',
                                    first_name='ZZ', last_name='Ilha Parecer', hierarchy='SUPERADMIN')
    D = date(2031, 4, 7)

    def fibra(venda, prot, status=Fibra.STATUS_PENDENTE, cpf='090.260.267-57'):
        return Fibra.objects.create(numero_da_venda=venda, numero_protocolo=prot, pdv='ZZ LOJA PARECER', data_da_venda=D,
                                    cpf=cpf, cliente=f'Cliente {venda}', vendedor='ZZ VENDEDOR UM', pilar='FIXA',
                                    servico_tecnico='Alta Banda Larga', valor=100, status=status)

    fm = fibra('ZZP1', '7311111111')   # vai ter parecer manual
    fa = fibra('ZZP2', '7322222222')   # segue a Esteira

    def linha(ordem, status, agenda=''):
        return ['07/04/2031', 'AGENDADA', status, ordem, 'BANDA LARGA', 'ZZP001', '', '', '', 'zz', 'ZZ VENDEDOR UM',
                'SIM', '', '', agenda]

    def importar(*linhas):
        with mock.patch.object(services, 'sync_fibras', return_value={'created': 0, 'updated': 0, 'total_in_source': 0}):
            r = ci.post('/fibras/validacao/api/importar/', {'arquivo': SimpleUploadedFile('ANALITICO DE VENDAS.xlsx', planilha(list(linhas)))})
        return r

    ci = Client()
    ci.force_login(ilha)

    print('== PARECER MANUAL x NOVA BASE DA ESTEIRA (16/11/2026) ==')
    r = ci.post(f'/fibras/{fm.pk}/status/ajax/', {'status': Fibra.STATUS_PROBLEMA})
    fm.refresh_from_db()
    t('mudar o status à mão marca parecer manual', r.status_code == 200 and fm.status == Fibra.STATUS_PROBLEMA
      and fm.status_manual and fm.status_manual_por == ilha and fm.status_manual_em is not None, (r.status_code, fm.status))
    r = importar(linha(7311111111, '1.1 INSTALADA', '09/04/2031'), linha(7322222222, '1.1 INSTALADA', '2031-04-10'))
    t('a importação roda', r.status_code == 200, r.content[:300])
    fm.refresh_from_db(); fa.refresh_from_db()
    t('a nova Esteira NÃO troca o status manual', fm.status == Fibra.STATUS_PROBLEMA and fm.status_manual, fm.status)
    t('mas atualiza o resto (o que a Esteira diz fica guardado)', fm.status_planilha_raw == '1.1 INSTALADA'
      and fm.status_esteira == Fibra.STATUS_INSTALADO, fm.status_planilha_raw)
    t('quem não tem parecer manual segue a Esteira', fa.status == Fibra.STATUS_INSTALADO and not fa.status_manual, fa.status)
    t('nenhum histórico "Esteira Vivo" na venda manual', not FibraStatusHistory.objects.filter(
        fibra=fm, retorno__startswith='Esteira Vivo:').exists())
    importar(linha(7311111111, '3.8 CANCELADA'), linha(7322222222, '1.1 INSTALADA'))
    fm.refresh_from_db()
    t('segunda importação também respeita', fm.status == Fibra.STATUS_PROBLEMA)

    print('\n== DATA DE AGENDAMENTO (DATA_AGENDA da Esteira) ==')
    importar(linha(7311111111, '3.5 AGENDADA', '09/04/2031'), linha(7322222222, '3.5 AGENDADA', '2031-04-10 14:00:00'))
    fm.refresh_from_db(); fa.refresh_from_db()
    t('lê dd/mm/aaaa e aaaa-mm-dd', fm.data_agendamento == date(2031, 4, 9) and fa.data_agendamento == date(2031, 4, 10),
      (fm.data_agendamento, fa.data_agendamento))
    t('guardada também na Esteira', EsteiraVenda.objects.get(ordem='7311111111').data_agenda == date(2031, 4, 9))
    lista = ci.get('/fibras/?de=2031-04-01&ate=2031-04-30').content.decode()
    t('lista tem a coluna Agendamento com a data', 'data-ord="agenda"' in lista and '09/04/2031' in lista
      and 'data-agenda="2031-04-09"' in lista)
    t('detalhe mostra a data de agendamento', '09/04/2031' in ci.get(f'/fibras/{fm.pk}/').content.decode())

    print('\n== CPF SEM PONTOS ==')
    t('cpf_digitos', fm.cpf_digitos == '09026026757')
    t('lista mostra o CPF sem pontos, com botão de copiar', '09026026757' in lista and 'data-copiar="09026026757"' in lista
      and '090.260.267-57</td>' not in lista)
    k = ci.get('/fibras/?de=2031-04-01&ate=2031-04-30').content.decode()
    t('/fibras/ (lista) mostra o CPF sem pontos e o selo manual', '09026026757' in k and 'fb-manual' in k)
    det = ci.get(f'/fibras/{fm.pk}/').content.decode()
    t('detalhe: CPF sem pontos e aviso do status manual com o que a Esteira diz', 'CPF 09026026757' in det
      and 'data-status-manual' in det and 'A Esteira diz: <b>3.5 AGENDADA</b>' in det, det[det.find('data-status-manual'):det.find('data-status-manual') + 400])

    print('\n== VOLTAR A SEGUIR A ESTEIRA ==')
    r = ci.post(f'/fibras/{fm.pk}/seguir-esteira/')
    fm.refresh_from_db()
    t('desliga o manual e já aplica o que a Esteira diz', r.status_code == 302 and not fm.status_manual
      and fm.status == Fibra.STATUS_AGENDADO, fm.status)
    t('fica no histórico', FibraStatusHistory.objects.filter(fibra=fm, retorno__startswith='Voltou a seguir a Esteira').exists())
    importar(linha(7311111111, '1.1 INSTALADA'), linha(7322222222, '1.1 INSTALADA'))
    fm.refresh_from_db()
    t('e a próxima Esteira volta a mandar', fm.status == Fibra.STATUS_INSTALADO, fm.status)

    print('\n== VIVO GO: CANCELAMENTO NÃO PASSA POR CIMA DO MANUAL ==')
    ci.post(f'/fibras/{fa.pk}/status/ajax/', {'status': Fibra.STATUS_PENDENTE})
    linha_go = {'numero_da_venda': 'ZZP2', 'venda_ativa': '0', 'cpf': '090.260.267-57', 'data_da_venda': D}
    with mock.patch.object(services, 'fetch_fibras_from_mysql', return_value=[linha_go]):
        services.sync_fibras(year=2031, month=4)
    fa.refresh_from_db()
    t('venda manual não vira Cancelado pela sincronização', fa.status == Fibra.STATUS_PENDENTE and fa.status_manual, fa.status)

    print('\n== SETA DE VOLTAR ==')
    for url in ('/fibras/', '/fibras/relatorio/'):
        t(f'{url}: seta de voltar ao lado das abas', 'class="fb-seta-voltar" data-fb-voltar' in ci.get(url).content.decode())
    t('validação: seta de voltar', 'vv-voltar' in ci.get('/fibras/validacao/').content.decode())
    t('detalhe: "Voltar" usa o histórico', 'data-fb-voltar' in det)
    vazou = [u for u in ('/fibras/', f'/fibras/{fm.pk}/') if '{#' in ci.get(u).content.decode()]
    t('nenhum comentário vazando', not vazou, vazou)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)

print(f'\n{ok} OK / {fail} falhas — rollback: nada ficou no banco.')
sys.exit(1 if fail else 0)
