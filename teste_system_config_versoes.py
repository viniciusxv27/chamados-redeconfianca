"""/users/manage/system-config/: padrão de exibição com fase, tela dinâmica e cache.

Pedido (05/10/2026):
- "Padrão de Exibição para Usuários" precisa ter a Fase de Contestação;
- a tela deve reagir na hora ao lançar o próximo mês (a versão nova já entra
  como opção de exibição);
- layout mais moderno, com pré-visualização;
- sair o botão "Limpar Cache": salvar a versão já limpa.

Tudo numa transação desfeita. O histórico de usuários (que baixa a planilha do
OneDrive) e a limpeza do Redis são trocados por dublês.
"""
import json
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client, RequestFactory

import users.views as uviews
from users import commission_config
from users.commission_views import resolve_commission_reference_from_request
from users.models import CommissionSpreadsheetVersion as V
from users.models import SystemConfig

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


LINKS = {
    'excel_comissao_url': 'https://1drv.ms/x/c/zz/COMISSAO',
    'excel_vendas_url': 'https://1drv.ms/x/c/zz/VENDAS',
    'excel_base_pagamento_url': 'https://1drv.ms/x/c/zz/PAGAMENTO',
    'excel_base_exclusao_url': 'https://1drv.ms/x/c/zz/EXCLUSAO',
    'excel_contestacao_base_exclusao_url': 'https://1drv.ms/x/c/zz/CONT_EXCLUSAO',
    'excel_contestacao_base_pagamento_url': '',
}

limpezas = []
marcador = transaction.atomic()
marcador.__enter__()
try:
    adm = User.objects.create_user(username='zz.sc.teste', email='zz.sc.teste@exemplo-teste.local',
                                   password='x', hierarchy='SUPERADMIN', first_name='Adm')
    c = Client()
    c.force_login(adm)
    cfg = SystemConfig.get_config()

    print('== A TELA ==')
    html = c.get('/users/manage/system-config/').content.decode()
    t('abre para o SUPERADMIN', 'id="sc-form"' in html)
    t('o botão "Limpar Cache" saiu', 'Limpar Cache' not in html and 'commission_refresh' not in html
      and '/commission/refresh' not in html)
    t('o padrão de exibição tem a fase (campo único AAAA-MM-fase)', 'name="display_ref"' in html)
    t('a fase da versão é escolhida na própria tela (Antes/Pós)',
      'name="contestacao_phase" value="antes"' in html and 'name="contestacao_phase" value="pos"' in html)
    dados = json.loads(html.split('id="sc-versoes" type="application/json">')[1].split('</script>')[0])
    t('as versões vão para o JavaScript com os links (troca sem recarregar)',
      len(dados) == V.objects.count() and all('excel_comissao_url' in d and 'phase' in d for d in dados))
    t('a pré-visualização tem rota própria', '/users/manage/system-config/previa-planilha/' in html)

    print('\n== LANÇAR O PRÓXIMO MÊS JÁ COMO PADRÃO ==')
    ultimo = V.objects.order_by('-year', '-month').first()
    ano, mes = (ultimo.year, ultimo.month) if ultimo else (2026, 9)
    ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    post = dict(LINKS, version_month=mes, version_year=ano, contestacao_phase='antes',
                display_ref=f'{ano}-{mes}-antes')
    with mock.patch.object(uviews, '_snapshot_commission_users_for_reference', return_value=0), \
            mock.patch.object(commission_config, 'limpar_cache_comissionamento',
                              side_effect=lambda: limpezas.append(1) or 0):
        r = c.post('/users/manage/system-config/', post)
    nova = V.objects.filter(year=ano, month=mes, contestacao_phase='antes').first()
    t('a versão nova é criada como rascunho', nova and nova.status == V.STATUS_DRAFT)
    t('e já pode ser o padrão de exibição, com a fase',
      (cfg.__class__.get_config().display_reference_year, cfg.__class__.get_config().display_reference_month,
       cfg.__class__.get_config().display_reference_phase) == (ano, mes, 'antes'))
    t('salvar limpa o cache (o que o botão fazia)', limpezas == [1], limpezas)
    t('volta para a tela com a versão salva aberta', r.status_code == 302 and f'versao={nova.pk}' in r['Location'],
      r.get('Location'))
    html = c.get(r['Location']).content.decode()
    import re
    t('a tela reabre editando a versão salva (mês e fase marcados)',
      re.search(rf'<option value="{mes}"\s+selected>', html)
      and re.search(r'value="antes" class="sr-only"\s+checked', html))

    print('\n== FASE QUE NÃO EXISTE NÃO VIRA PADRÃO ==')
    post = dict(LINKS, version_month=mes, version_year=ano, contestacao_phase='antes',
                display_ref=f'{ano}-{mes}-pos')   # a Pós desse mês não existe
    with mock.patch.object(uviews, '_snapshot_commission_users_for_reference', return_value=0), \
            mock.patch.object(commission_config, 'limpar_cache_comissionamento', return_value=0):
        c.post('/users/manage/system-config/', post)
    t('recusa e mantém o padrão anterior',
      SystemConfig.get_config().display_reference_phase == 'antes')

    print('\n== O /users/commission ABRE NA FASE ESCOLHIDA ==')
    rf = RequestFactory()
    req = rf.get('/users/commission/')
    req.user = adm
    with mock.patch('users.commission_liberacao.versoes_liberadas',
                    return_value=[V(year=ano, month=mes, contestacao_phase='antes'),
                                  V(year=ano, month=mes, contestacao_phase='pos')]):
        ref = resolve_commission_reference_from_request(req)
        t('com Antes como padrão, abre na Antes', (ref['year'], ref['month'], ref['phase']) == (ano, mes, 'antes'), ref)
        SystemConfig.objects.filter(pk=1).update(display_reference_phase='')
        ref = resolve_commission_reference_from_request(req)
        t('sem fase escolhida, continua abrindo na Pós', ref['phase'] == 'pos', ref)
        SystemConfig.objects.filter(pk=1).update(display_reference_phase='antes')
        req2 = rf.get('/users/commission/', {'phase': 'pos'})
        req2.user = adm
        t('quem escolhe a fase na URL continua mandando',
          resolve_commission_reference_from_request(req2)['phase'] == 'pos')
    with mock.patch('users.commission_liberacao.versoes_liberadas',
                    return_value=[V(year=ano, month=mes, contestacao_phase='pos')]):
        ref = resolve_commission_reference_from_request(req)
        t('fase padrão não liberada para a pessoa: cai na que está', ref['phase'] == 'pos', ref)

    print('\n== LIMPEZA DE CACHE ==')
    falso = mock.MagicMock()
    falso.delete_pattern.return_value = 2
    with mock.patch.object(commission_config, 'cache', falso):
        n = commission_config.limpar_cache_comissionamento()
    padroes = [ch.args[0] for ch in falso.delete_pattern.call_args_list]
    t('apaga por padrão o arquivo baixado (prefixo_hash_file_content)',
      'comissao_*' in padroes and 'base_pagamento*' in padroes and 'vendas_*' in padroes, padroes)
    t('apaga os dados já lidos', 'commission_all_users_*' in padroes and 'metas_pilar*' in padroes)
    t('e as planilhas da contestação', 'contestacao_base_*' in padroes)
    t('conta as chaves apagadas', n == 2 * len(padroes), n)
    sem_padrao = mock.MagicMock(spec=['delete_many'])
    with mock.patch.object(commission_config, 'cache', sem_padrao):
        commission_config.limpar_cache_comissionamento()
    t('sem Redis, apaga ao menos as chaves fixas', sem_padrao.delete_many.called)

    print('\n== PRÉ-VISUALIZAÇÃO ==')
    from io import BytesIO

    from openpyxl import Workbook
    livro = Workbook()
    ws = livro.active
    ws.title = 'Planilha1'
    ws.append(['Filial', 'Vendedor', 'Receita', 'PILAR'])
    ws.append(['GLÓRIA', 'ANA', 10.5, 'MÓVEL'])
    ws.append(['ICONHA', 'BIA', 20, 'FIXA'])
    buf = BytesIO()
    livro.save(buf)
    with mock.patch('users.commission_views.download_excel_file', return_value=(BytesIO(buf.getvalue()), None)):
        r = c.post('/users/manage/system-config/previa-planilha/',
                   {'url': 'https://1drv.ms/x/zz', 'tipo': 'excel_base_pagamento_url'}).json()
    aba = r['abas'][0] if r.get('abas') else {}
    t('mostra as abas, o cabeçalho e as linhas', r['ok'] and aba.get('cabecalho') == ['Filial', 'Vendedor', 'Receita', 'PILAR']
      and aba.get('linhas') == 2 and aba.get('amostra', [[]])[1][:2] == ['ICONHA', 'BIA'], r)
    with mock.patch('users.commission_views.download_excel_file', return_value=(BytesIO(buf.getvalue()), None)):
        r = c.post('/users/manage/system-config/previa-planilha/',
                   {'url': 'https://1drv.ms/x/zz', 'tipo': 'excel_comissao_url'}).json()
    t('aponta as abas que faltam (link errado no lugar errado)',
      not r['ok'] and 'REMUNERAÇÃO CN' in r['faltando'], r['faltando'])
    with mock.patch('users.commission_views.download_excel_file', return_value=(BytesIO(buf.getvalue()), None)):
        r = c.post('/users/manage/system-config/previa-planilha/',
                   {'url': 'https://1drv.ms/x/zz', 'tipo': 'excel_contestacao_base_pagamento_url'}).json()
    t('aponta as colunas que faltam (RECEBIDO na contestação de valores)', r['colunas_faltando'] == ['RECEBIDO'],
      r['colunas_faltando'])
    with mock.patch('users.commission_views.download_excel_file', return_value=(None, 'HTTP 403')):
        r = c.post('/users/manage/system-config/previa-planilha/',
                   {'url': 'https://1drv.ms/x/zz', 'tipo': 'excel_vendas_url'}).json()
    t('link que não baixa explica o motivo', not r['ok'] and 'HTTP 403' in r['erro'] and 'Qualquer pessoa' in r['erro'])
    comum = Client()
    comum.force_login(User.objects.create_user(username='zz.sc.comum', email='zz.sc.c@exemplo-teste.local', password='x'))
    t('só o SUPERADMIN usa a pré-visualização',
      comum.post('/users/manage/system-config/previa-planilha/', {'url': 'https://x'}).status_code == 403)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)

print(f'\n{ok} OK / {fail} falhas — rollback: nada deste teste ficou no banco.')
sys.exit(1 if fail else 0)
