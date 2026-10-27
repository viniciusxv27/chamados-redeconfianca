"""/fibras (21/10/2026): abre no mês, mostra a mesma validação da /fibras/validacao e
aceita a Base analítica – FIXA como Esteira (no kanban e na validação).

Transação desfeita no fim; datas de 2031; o MySQL do Vivo GO e o aviso ao vendedor
não são chamados.
"""
import io
import os
import sys
from datetime import date
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fx'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fx2'}}
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
from django.utils import timezone

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


def base_fixa(linhas):
    cols = ['ANO_MES', 'DT_MVMT', 'CD_CRDN', 'CANAL', 'CD_SGMT', 'DS_INDC_CMRC', 'DS_SGMT_PRDT', 'LOGIN_VENDEDOR',
            'MATRICULA', 'NOME', 'NM_FNTS', 'NU_ORDM_SRVC', 'TECNOLOGIA', 'CD_RPON', 'QTD', 'RCTA_BRTA_DELTA_BSC']
    df = pd.DataFrame([[203103, pd.Timestamp(d), adabas, 'REVENDA', 'B2C', ind, 'BANDA LARGA', 'zzlogin', None, nome,
                        None, ordem, 'FTTH', None, 1 if ind == 'VENDAS BRUTAS' else -1, None]
                       for d, adabas, ind, ordem, nome in linhas], columns=cols)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        df.to_excel(w, index=False, sheet_name='Export')
    return buf.getvalue()


marcador = transaction.atomic()
marcador.__enter__()
try:
    from fibras import services
    from fibras import validacao as val
    from fibras.models import EsteiraVenda, Fibra
    from users.models import Sector

    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede), \
            mock.patch.object(services, 'sync_fibras', return_value={'created': 0, 'updated': 0, 'total_in_source': 0}), \
            mock.patch.object(services, '_notify_vendor', lambda *a, **k: None):
        Sector.objects.create(name='ZZ LOJA FIXA', adabas='ZZF001')
        ilha = User.objects.create_user(username='zzfx.ilha', email='zzfx.ilha@exemplo-teste.local', password='x',
                                        first_name='ZZ', last_name='Ilha', hierarchy='SUPERVISOR')
        comum = User.objects.create_user(username='zzfx.adm', email='zzfx.adm@exemplo-teste.local', password='x',
                                         first_name='ZZ', last_name='Coord', hierarchy='COORDENADOR')
        # Desde 27/10/2026 o módulo é do SUPERADMIN e de quem ele libera em /fibras/acessos/.
        from fibras.models import AcessoFibras
        from fibras.permissoes import CHAVES, completar
        AcessoFibras.objects.create(user=ilha, permissoes=completar(CHAVES))
        AcessoFibras.objects.create(user=comum, permissoes=completar(['rede.ver']))
        ci, cc = Client(), Client()
        ci.force_login(ilha)
        cc.force_login(comum)
        D = date(2031, 3, 10)

        def fibra(venda, prot):
            return Fibra.objects.create(numero_da_venda=venda, numero_protocolo=prot, pdv='ZZ LOJA FIXA', data_da_venda=D,
                                        cpf='12345678909', cliente=f'Cliente {venda}', vendedor='ZZ VENDEDOR',
                                        pilar='FIXA', servico_tecnico='Alta Banda Larga', valor=100,
                                        status=Fibra.STATUS_PENDENTE)

        f_bruta = fibra('ZZFX1', '7100000001')
        f_cancel = fibra('ZZFX2', '7100000002')
        f_fora = fibra('ZZFX3', '7100000099')

        print('— Abre no mês')
        r = ci.get('/fibras/')
        hoje = timezone.localdate()
        t('sem período na URL: mês corrente', r.context['filtro_de'] == hoje.replace(day=1).isoformat()
          and r.context['filtro_ate'][:7] == hoje.isoformat()[:7], (r.context['filtro_de'], r.context['filtro_ate']))
        t('rótulo do mês inteiro', r.context['periodo_rotulo'].endswith(f'de {hoje.year}'), r.context['periodo_rotulo'])
        t('o "x" do período leva a todos', 'periodo=todos' in r.context['sem_periodo_url'])
        t('?periodo=todos mostra tudo', not ci.get('/fibras/?periodo=todos').context['periodo_ativo'])
        t('datas limpas no filtro mostram tudo', not ci.get('/fibras/?de=&ate=').context['periodo_ativo'])
        t('mês de 2031 sem a venda no mês corrente', 'ZZFX1' not in r.content.decode())

        print('— Mesma conferência do arquivo nas duas telas')
        r = ci.post('/fibras/importar/', {'planilha': SimpleUploadedFile('base.xls', b'x')}, follow=True)
        msg_k = ' '.join(str(m) for m in r.context['messages'])
        r2 = ci.post('/fibras/validacao/api/importar/', {'arquivo': SimpleUploadedFile('base.xls', b'x')})
        t('.xls recusado nas duas com a mesma mensagem', r2.status_code == 400 and r2.json()['erro'] in msg_k
          and '.xlsx' in msg_k, (msg_k, r2.content[:200]))

        print('— Base analítica FIXA pelo kanban')
        arquivo = base_fixa([
            ('2031-03-10', 'ZZF001', 'VENDAS BRUTAS', '7100000001', 'ZZ VENDEDOR'),
            ('2031-03-10', 'ZZF001', 'VENDAS BRUTAS', '7100000002', 'ZZ VENDEDOR'),
            ('2031-03-11', 'ZZF001', 'CANCELAMENTOS', '7100000002', None),
            ('2031-03-12', 'ZZF001', 'VENDAS BRUTAS', '7100000050', 'ZZ OUTRO'),
        ])
        voltar = '/fibras/?de=2031-03-01&ate=2031-03-31'
        r = ci.post('/fibras/importar/', {'planilha': SimpleUploadedFile('Base analítica - FIXA.xlsx', arquivo),
                                          'voltar': voltar})
        t('volta para o mesmo filtro', r.status_code == 302 and r['Location'] == voltar, r.get('Location'))
        msgs = ' '.join(str(m) for m in ci.get(voltar).context['messages'])
        t('mensagem diz o formato', 'Base analítica – FIXA importada (3 ORDEM(s))' in msgs, msgs[:300])
        es = {e.ordem: e for e in EsteiraVenda.objects.all()}
        t('uma linha por ORDEM', set(es) == {'7100000001', '7100000002', '7100000050'}, list(es))
        t('venda bruta + cancelamento = cancelada', es['7100000002'].status_instalacao == val.STATUS_FIXA_CANCELADA
          and es['7100000002'].data_criacao == D and 'cancelamento' in es['7100000002'].motivo)
        t('loja, vendedor e produto vêm da Base', es['7100000001'].adabas == 'ZZF001'
          and es['7100000001'].nome_vendedor == 'ZZ VENDEDOR' and es['7100000001'].produto == 'BANDA LARGA FTTH')
        for f in (f_bruta, f_cancel, f_fora):
            f.refresh_from_db()
        t('cruzou: protocolo exato → Alta', f_bruta.assertividade == 'Alta' and f_bruta.ordem_vinculada == '7100000001',
          (f_bruta.assertividade, f_bruta.ordem_vinculada))
        t('venda bruta não muda o status', f_bruta.status == Fibra.STATUS_PENDENTE, f_bruta.status)
        t('cancelamento leva a Cancelado', f_cancel.status == Fibra.STATUS_CANCELADO, f_cancel.status)
        t('sem par na Base analítica → Não localizada', f_fora.status_cruzamento == 'Não localizada', f_fora.status_cruzamento)

        print('— A validação no kanban')
        r = ci.get(voltar)
        h = r.content.decode()
        v = r.context['validacao']
        t('mesmos indicadores da validação', [c['rotulo'] for c in v['cards']][:6]
          == ['Alta', 'Média', 'Muito baixa', 'Manual', 'Não localizadas', 'Vendas B2B'])
        n = {c['chave']: c['n'] for c in v['cards']}
        t('contados nas vendas do quadro', v['k']['pedidos'] == 3 and n['alta'] >= 1 and n['nao_localizada'] == 1, v['k'])
        t('não lançadas do período (Ilha)', v['nao_lancada'] == 1, v['nao_lancada'])
        t('cards levam o resultado para o filtro', 'data-assert="Alta"' in h and 'data-cruz="Não localizada"' in h)
        t('botão de validar o período e link da validação completa', 'id="fbValCruzar"' in h
          and 'data-ini="2031-03-01"' in h and '/fibras/validacao/' in h)
        r = ci.post('/fibras/validacao/api/cruzar/', data='{"ini": "2031-03-01", "fim": "2031-03-31"}',
                    content_type='application/json')
        t('validar o período usa a mesma API', r.status_code == 200 and r.json()['kpis']['pedidos'] >= 2, r.content[:200])
        h = cc.get(voltar).content.decode()
        t('coordenação vê os indicadores, sem validar nem as não lançadas',
          'id="fbVal"' in h and 'id="fbValCruzar"' not in h and 'na Esteira, sem Base de Vendas' not in h)

        print('— A mesma Base pela validação')
        r = ci.post('/fibras/validacao/api/importar/', {'arquivo': SimpleUploadedFile('Base analítica - FIXA.xlsx', arquivo)})
        t('validação aceita a Base analítica', r.status_code == 200 and 'Base analítica' in r.json().get('msg', ''),
          r.content[:300])
        t('ANALÍTICO continua sendo lido', not val.e_base_fixa(pd.DataFrame(columns=['ORDEM', 'ADABAS', 'DATA_CRIAÇÃO'])))
finally:
    marcador.__exit__(Exception, Exception('desfaz'), None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
