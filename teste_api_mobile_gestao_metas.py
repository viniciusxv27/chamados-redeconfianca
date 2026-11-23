"""API do app — módulo Gestão de metas (/api/mobile/v1/m/gestao_metas/).

- Entrada: a regra do portal (gestao_metas.permissoes.pode_ver): SUPERADMIN ou AcessoGestaoMetas
  (pessoa ou grupo); Editor muda, Visualizador só vê.
- Mudar: as views do portal (salvar, dividir, preencher, fechar) chamadas pela API.
- Competência num mês de 2099 (nenhuma real é tocada) e lojas de teste; o banco de vendas
  (realizado "até agora") e o Quadro de HC (consultores da loja) viram dublê.
- Cache em LocMem (nunca o Redis do dev); tudo numa transação desfeita no fim.
"""
import os
import sys
from datetime import date
from decimal import Decimal
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
import redeconfianca.settings as _cfg  # noqa: E402

_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-gm-{n}'} for n in _cfg.CACHES}
django.setup()

from django.conf import settings  # noqa: E402
from django.core.cache import caches  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402

from api_mobile.modulos import gestao_metas as mod  # noqa: E402
from communications.models import CommunicationGroup  # noqa: E402
from gestao_metas.models import (AcessoGestaoMetas, Competencia, HistoricoMeta, LojaMeta, MetaConsultor,  # noqa: E402
                                 MetaLoja, Realizado)
from users.models import Sector  # noqa: E402

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
M = f'{BASE}/m/gestao_metas'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


for nome in settings.CACHES:
    assert 'locmem' in settings.CACHES[nome]['BACKEND'].lower()
    caches[nome].clear()

# O banco de vendas (MySQL) nunca é consultado aqui.
banco = mock.patch('gestao_metas.importacao.buscar_mes_no_banco', side_effect=AssertionError('não consultar o banco'))
m_banco = banco.start()

marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Loja GM App')

    def novo(apelido, hierarquia='PADRAO'):
        return User.objects.create_user(
            username=f'zzgm.{apelido}', email=f'zzgm.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZGm{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia)

    sa, editor, visual, nada = novo('sa', 'SUPERADMIN'), novo('editor', 'SUPERVISOR'), novo('visual'), novo('nada')
    cn1, cn2 = novo('cn1'), novo('cn2')
    AcessoGestaoMetas.objects.create(user=editor, papel=AcessoGestaoMetas.EDITOR)
    grupo = CommunicationGroup.objects.create(name='ZZ Grupo GM App', created_by=sa)
    grupo.members.add(visual)
    AcessoGestaoMetas.objects.create(grupo=grupo, papel=AcessoGestaoMetas.VISUALIZADOR)

    loja_a = LojaMeta.objects.create(nome='ZZ LOJA GM A', chave='ZZ LOJA GM A', regiao='RJ', ordem=99990, setor=setor)
    loja_b = LojaMeta.objects.create(nome='ZZ LOJA GM B', chave='ZZ LOJA GM B', regiao='ES', ordem=99991)
    comp = Competencia.objects.create(mes=date(2099, 1, 1), criada_por=sa)
    for i, mes in enumerate((date(2098, 9, 1), date(2098, 10, 1), date(2098, 11, 1), date(2098, 12, 1))):
        Realizado.objects.create(loja=loja_a, mes=mes, pilar='movel', valor=Decimal(1000 * (i + 1)))
        Realizado.objects.create(loja=loja_b, mes=mes, pilar='movel', valor=Decimal(500))
        Realizado.objects.create(loja=loja_a, mes=mes, pilar='fixa', valor=Decimal(2000), qtd_tv=4, qtd_fixa=6)

    def entrar(u):
        r = Client().post(f'{BASE}/auth/login/', {'email': u.email, 'senha': 'S3nha!teste', 'dispositivo': 'ZZ'},
                          content_type='application/json')
        return Client(HTTP_AUTHORIZATION=f"Bearer {r.json()['token']}")

    c_sa, c_ed, c_vi, c_na = (entrar(u) for u in (sa, editor, visual, nada))

    def post(c, url, corpo=None):
        return c.post(url, corpo or {}, content_type='application/json')

    print('== ACESSO ==')
    for c, nome_, deve in ((c_sa, 'SUPERADMIN', True), (c_ed, 'editor', True), (c_vi, 'visualizador (pelo grupo)', True),
                           (c_na, 'sem acesso', False)):
        t(f'/modulos/: {nome_} {"vê" if deve else "não vê"} a Gestão de metas',
          ('gestao_metas' in {m['chave'] for m in c.get(f'{BASE}/modulos/').json()}) is deve)
    t('sem acesso: 403 nos endpoints', c_na.get(f'{M}/').status_code == 403 and c_na.get(f'{M}/{comp.codigo}/').status_code == 403)
    t('sem token: 401/403', Client().get(f'{M}/').status_code in (401, 403))
    t('regra de entrada = pode_ver do portal', all(mod.pode_entrar(u) == (u is not nada) for u in (sa, editor, visual, nada)))

    print('\n== COMPETÊNCIAS ==')
    j = c_ed.get(f'{M}/').json()
    t('lista com o papel', j['papel'] == 'EDITOR' and j['pode_editar'] is True
      and any(c['codigo'] == '2099-01' and c['fechada'] is False for c in j['competencias']), j.get('papel'))
    t('visualizador não edita', c_vi.get(f'{M}/').json()['pode_editar'] is False)

    r = c_ed.get(f'{M}/2099-01/', {'pilar': 'movel'})
    j = r.json()
    la = next((x for x in j.get('linhas', []) if x['loja_id'] == loja_a.pk), None)
    t('tela da competência: lojas com o realizado dos 4 meses e a média', r.status_code == 200 and la is not None
      and la['meses'] == [1000.0, 2000.0, 3000.0, 4000.0] and la['media_quad'] == 2500.0 and la['meta'] is None
      and j['rotulos']['meses'] == ['set/98', 'out/98', 'nov/98', 'dez/98'], r.content[:300])
    t('subtotais e rede', j['rede']['titulo'] == 'REDE' and any(g['titulo'] == 'RJ' for g in j['grupos']))
    t('editor edita; links da planilha e do portal', j['pode_editar'] is True and j['pode_fechar'] is True
      and j['pode_reabrir'] is False and '/api/mobile/v1/arquivo/' in j['excel'] and j['url_web'].startswith('/gestao-metas/2099-01/'))
    t('visualizador: sem edição', c_vi.get(f'{M}/2099-01/').json()['pode_editar'] is False)
    t('pilar inválido: 400', c_ed.get(f'{M}/2099-01/', {'pilar': 'xyz'}).status_code == 400)
    t('competência que não existe: 404', c_ed.get(f'{M}/2098-01/').status_code == 404
      and c_ed.get(f'{M}/abc/').status_code == 404)

    print('\n== SALVAR (view salvar do portal) ==')
    url = f'{M}/2099-01/salvar/'
    r = post(c_ed, url, {'loja': loja_a.pk, 'pilar': 'movel', 'campo': 'meta', 'valor': '15.700,50'})
    m = MetaLoja.objects.filter(competencia=comp, loja=loja_a, pilar='movel').first()
    t('editor grava a meta (formato brasileiro)', r.status_code == 200 and m and m.meta == Decimal('15700.50')
      and r.json()['linha']['meta'] == 15700.5 and r.json()['linha']['alvo'] == 15700.5, r.content[:300])
    t('a variação vem recalculada (M×M = meta ÷ último mês − 1)', abs(r.json()['linha']['mxm'] - (15700.5 / 4000 - 1)) < 1e-3)
    t('fica no histórico', HistoricoMeta.objects.filter(competencia=comp, loja=loja_a, campo='meta', por=editor).exists())
    r = post(c_vi, url, {'loja': loja_a.pk, 'pilar': 'movel', 'campo': 'meta', 'valor': '1'})
    m.refresh_from_db()
    t('visualizador: 403 e nada muda', r.status_code == 403 and m.meta == Decimal('15700.50'), r.content[:200])
    t('loja torta: 400', post(c_ed, url, {'loja': 'abc', 'pilar': 'movel', 'campo': 'meta', 'valor': '1'}).status_code == 400)
    t('meta negativa: 400 com a mensagem do portal',
      'negativa' in post(c_ed, url, {'loja': loja_a.pk, 'pilar': 'movel', 'campo': 'meta', 'valor': '-5'}).json().get('erro', ''))
    t('CNs fora da Fixa: 400', post(c_ed, url, {'loja': loja_a.pk, 'pilar': 'movel', 'campo': 'qtd_cns', 'valor': '3'}).status_code == 400)
    post(c_ed, url, {'loja': loja_a.pk, 'pilar': 'fixa', 'campo': 'meta', 'valor': '20000'})
    post(c_ed, url, {'loja': loja_a.pk, 'pilar': 'fixa', 'campo': 'qtd_cns', 'valor': '3'})
    r = post(c_ed, url, {'loja': loja_a.pk, 'pilar': 'fixa', 'campo': 'decisao', 'valor': 'BAIXO'})
    lf = r.json().get('linha') or {}
    t('Fixa: ticket, QTD e a nova meta pela decisão', r.status_code == 200 and lf['ticket'] == 200.0 and lf['qtd'] == 100
      and lf['qtd_cns'] == 3 and lf['decisao'] == 'BAIXO' and lf['nova_meta'] == lf['baixo'] * 3, lf)

    print('\n== SALVAR E DIVIDIR (view dividir + Quadro de HC) ==')
    url = f'{M}/2099-01/dividir/'
    r = post(c_ed, url, {'loja': loja_b.pk, 'pilar': 'movel', 'campos': {'meta': '1000'}})
    t('loja sem setor: grava a meta e avisa que não dividiu', r.status_code == 400 and 'setor' in r.json().get('erro', '')
      and MetaLoja.objects.get(competencia=comp, loja=loja_b, pilar='movel').meta == Decimal('1000'), r.content[:300])
    with mock.patch('quadro_hc.servicos.consultores_da_loja', return_value=[{'user': cn1, 'percentual': 100},
                                                                          {'user': cn2, 'percentual': 80}]):
        r = post(c_ed, url, {'loja': loja_a.pk, 'pilar': 'movel', 'campos': {'meta': '10001'}})
    j = r.json()
    partes = {mc.colaborador_id: mc.meta for mc in MetaConsultor.objects.filter(competencia=comp, loja=loja_a, pilar='movel')}
    t('divide entre os CNs (100% e 80%) e a soma bate', r.status_code == 200 and sum(partes.values()) == Decimal('10001')
      and partes.get(cn2.pk) == Decimal('4000.40') and j['divisao']['consultores'] == 2 and len(j['linha']['divisao']) == 2, r.content[:300])
    t('visualizador não divide: 403', post(c_vi, url, {'loja': loja_a.pk, 'pilar': 'movel', 'campos': {}}).status_code == 403)

    print('\n== PREENCHIMENTO RÁPIDO (view preencher) ==')
    MetaLoja.objects.filter(competencia=comp, loja=loja_b, pilar='movel').update(meta=None)
    r = post(c_ed, f'{M}/2099-01/preencher/', {'pilar': 'movel', 'origem': 'media_quad', 'percentual': '10',
                                              'arredondar': '10', 'somente_vazias': True})
    mb = MetaLoja.objects.get(competencia=comp, loja=loja_b, pilar='movel')
    ma = MetaLoja.objects.get(competencia=comp, loja=loja_a, pilar='movel')
    t('só as vazias: média × 1,1 arredondada', r.status_code == 200 and mb.meta == Decimal('550.00')
      and ma.meta == Decimal('10001.00') and 'preenchida' in r.json()['mensagem'], (r.content[:200], mb.meta, ma.meta))
    r = post(c_ed, f'{M}/2099-01/preencher/', {'pilar': 'movel', 'origem': 'xyz'})
    t('origem desconhecida: 400 com a mensagem', r.status_code == 400 and r.json().get('erro'))
    t('visualizador: 403', post(c_vi, f'{M}/2099-01/preencher/', {'pilar': 'movel', 'origem': 'media_quad'}).status_code == 403)

    print('\n== FECHAR E REABRIR (view fechar) ==')
    t('visualizador não fecha: 403', post(c_vi, f'{M}/2099-01/fechar/').status_code == 403)
    r = post(c_ed, f'{M}/2099-01/fechar/')
    comp.refresh_from_db()
    t('editor fecha', r.status_code == 200 and comp.fechada, r.content[:200])
    r = post(c_ed, f'{M}/2099-01/salvar/', {'loja': loja_a.pk, 'pilar': 'movel', 'campo': 'meta', 'valor': '1'})
    t('fechada não muda: 409', r.status_code == 409, r.status_code)
    j = c_ed.get(f'{M}/2099-01/').json()
    t('fechada: sem edição e sem reabrir para o editor', j['pode_editar'] is False and j['pode_reabrir'] is False)
    r = post(c_ed, f'{M}/2099-01/fechar/', {'acao': 'reabrir'})
    comp.refresh_from_db()
    t('editor não reabre (só SUPERADMIN)', r.status_code == 400 and comp.fechada and 'SUPERADMIN' in r.json().get('erro', ''))
    t('SUPERADMIN vê o reabrir', c_sa.get(f'{M}/2099-01/').json()['pode_reabrir'] is True)
    r = post(c_sa, f'{M}/2099-01/fechar/', {'acao': 'reabrir'})
    comp.refresh_from_db()
    t('SUPERADMIN reabre', r.status_code == 200 and not comp.fechada)

    print('\n== HISTÓRICO, REALIZADO ATÉ AGORA E PLANILHA ==')
    j = c_vi.get(f'{M}/2099-01/historico/', {'pilar': 'movel'}).json()['resultados']
    t('histórico do pilar (visualizador também vê)', j and all(h['pilar'] == 'movel' for h in j)
      and any(h['campo'] == 'divisao_consultores' for h in j), j[:2])
    r = c_vi.get(f'{M}/2099-01/realizado-agora/')
    t('mês que não começou: "futuro", sem ir ao banco', r.status_code == 200 and r.json()['situacao'] == 'futuro'
      and m_banco.call_count == 0, r.content[:200])
    url_excel = c_vi.get(f'{M}/2099-01/').json()['excel']
    r = Client().get(url_excel.replace('http://testserver', ''))
    t('planilha .xlsx pelo link assinado', r.status_code == 200 and 'spreadsheetml' in r['Content-Type']
      and 'METAS 01.2099' in r['Content-Disposition'], (r.status_code, r.get('Content-Type')))
    try:
        mod._excel(nada, '2099-01')
        t('planilha: quem não entra no módulo não abre', False)
    except PermissionError:
        t('planilha: quem não entra no módulo não abre', True)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    banco.stop()
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
