"""API do app — módulo Quadro de HC (/api/mobile/v1/m/quadro_hc/).

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
from datetime import timedelta

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
# Cache em memória: o `default` é o Redis remoto do dev e limpar ele seria um FLUSHDB do ambiente todo.
import redeconfianca.settings as _cfg  # noqa: E402
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'} for n in _cfg.CACHES}
django.setup()

from django.conf import settings
from django.core.cache import caches

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from communications.models import CommunicationGroup
from quadro_hc import servicos
from quadro_hc.models import AcessoQuadroHC, AjusteHC, Carteira, HCPrevisto
from users.models import Sector

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
M = f'{BASE}/m/quadro_hc'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


for nome in settings.CACHES:
    caches[nome].clear()

hoje = timezone.localdate()
marcador = transaction.atomic()
marcador.__enter__()
try:
    loja_a = Sector.objects.create(name='Loja ZZ HC App Alfa')
    loja_b = Sector.objects.create(name='Loja ZZ HC App Beta')
    vazia = Sector.objects.create(name='Loja ZZ HC App Vazia')
    escritorio = Sector.objects.create(name='ZZ Escritorio HC App')
    carteira = Carteira.objects.create(nome='ZZ Carteira HC App', coordenador_nome='ZZ Coord', ordem=999)
    carteira.lojas.add(loja_a)

    def novo(apelido, setor=escritorio, hierarquia='PADRAO', **kw):
        return User.objects.create_user(
            username=f'zzhc.{apelido}', email=f'zzhc.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZHc{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia, **kw)

    chefe = novo('chefe', hierarquia='SUPERADMIN')
    vendo = novo('ver')
    gestor = novo('gerenciar')
    padrao = novo('padrao')
    AcessoQuadroHC.objects.create(user=vendo, papel=AcessoQuadroHC.VER, liberado_por=chefe)
    AcessoQuadroHC.objects.create(user=gestor, papel=AcessoQuadroHC.GERENCIAR, liberado_por=chefe)

    # Loja Alfa: 1 gerente, 2 consultores (um com demissão antiga e status ATIVO, um em férias), 1 novo.
    gerente_a = novo('gerentea', loja_a, job_title='GERENTE DE LOJA', admission_date=hoje - timedelta(days=400))
    cons_dem = novo('consdem', loja_a, job_title='CONSULTOR DE VENDAS', admission_date=hoje - timedelta(days=500),
                    demission_date=hoje - timedelta(days=300), status='ATIVO', city='Serra', login_code='99887766001')
    cons_fer = novo('consferias', loja_a, job_title='CONSULTOR DE VENDAS', admission_date=hoje - timedelta(days=200),
                    status='FERIAS', vacation_start_date=hoje - timedelta(days=3), vacation_end_date=hoje + timedelta(days=10))
    novato = novo('novato', loja_a, job_title='RECEPCIONISTA', admission_date=hoje - timedelta(days=10))
    # Loja Beta: consultor no grupo GERENTES (gerente pelo grupo) + 3 consultores.
    grupo = (CommunicationGroup.objects.filter(name__iexact='GERENTES').first()
             or CommunicationGroup.objects.create(name='GERENTES', created_by=chefe))
    gerente_b = novo('gerenteb', loja_b, job_title='CONSULTOR DE VENDAS')
    grupo.members.add(gerente_b)
    cons_b = [novo(f'consb{i}', loja_b, job_title='CONSULTOR DE VENDAS') for i in range(3)]
    desligado = novo('desligado', loja_a, job_title='CONSULTOR DE VENDAS', is_active=False,
                     demission_date=hoje - timedelta(days=10), status='INATIVO')

    # Previsto: Alfa quer 5 consultores (tem 2 → falta 3) e 1 gerente; Beta quer 1 consultor (tem 3 → sobra 2).
    for setor, funcao, n in [(loja_a, 'Consultor', 5), (loja_a, 'Gerente', 1), (loja_b, 'Consultor', 1), (loja_b, 'Gerente', 1)]:
        HCPrevisto.objects.create(setor=setor, funcao=funcao, quantidade=n, atualizado_por=chefe)

    def entrar(user):
        r = Client().post(f'{BASE}/auth/login/', {'email': user.email, 'senha': 'S3nha!teste', 'plataforma': 'android',
                                                   'dispositivo': 'ZZ Pixel'}, content_type='application/json')
        assert r.status_code == 200, r.content[:300]
        return Client(HTTP_AUTHORIZATION=f'Bearer {r.json()["token"]}')

    cc, cv, cg, cp = entrar(chefe), entrar(vendo), entrar(gestor), entrar(padrao)

    print('== ACESSO ==')
    for nome, user, cli, esperado in [('SUPERADMIN', chefe, cc, True), ('AcessoQuadroHC Ver', vendo, cv, True),
                                      ('AcessoQuadroHC Gerenciar', gestor, cg, True), ('PADRÃO sem acesso', padrao, cp, False)]:
        t(f'{nome}: regra do portal (pode_acessar) = {esperado}', servicos.pode_acessar(User.objects.get(pk=user.pk)) is esperado)
        chaves = {m['chave'] for m in cli.get(f'{BASE}/modulos/').json()}
        t(f'{nome}: módulo na lista = {esperado}', ('quadro_hc' in chaves) is esperado, chaves)
        codigo = 200 if esperado else 403
        for rota in ('', 'opcoes/', f'loja/{loja_a.pk}/', f'pessoa/{cons_dem.pk}/'):
            r = cli.get(f'{M}/{rota}')
            t(f'{nome}: GET /{rota} = {codigo}', r.status_code == codigo, r.status_code)
        if not esperado:
            for rota in ('ajuste/', f'loja/{loja_a.pk}/previsto/'):
                r = cli.post(f'{M}/{rota}', {'colaborador': cons_dem.pk, 'situacao': 'EXCLUIR', 'motivo': 'x',
                                             'quantidades': {'Consultor': 1}}, content_type='application/json')
                t(f'{nome}: POST /{rota} = 403', r.status_code == 403, r.status_code)
            t(f'{nome}: nada gravado', not AjusteHC.objects.filter(colaborador=cons_dem).exists()
              and HCPrevisto.objects.get(setor=loja_a, funcao='Consultor').quantidade == 5)
    t('sem token: 401/403', Client().get(f'{M}/').status_code in (401, 403))

    print('\n== PAPEL ==')
    t('papel do SUPERADMIN', cc.get(f'{M}/').json()['papel'] == 'SUPERADMIN')
    rv = cv.get(f'{M}/').json()
    t('Ver: papel VER e não gerencia', rv['papel'] == 'VER' and rv['pode_gerenciar'] is False)
    t('Gerenciar: pode_gerenciar', cg.get(f'{M}/').json()['pode_gerenciar'] is True)

    print('\n== NÚMEROS = montar_quadro ==')
    q = servicos.montar_quadro(hoje)
    blocos = {b['setor'].id: (c, b) for c in q['carteiras'] for b in c['lojas']}
    r = cc.get(f'{M}/').json()
    lojas = {lj['id']: lj for c in r['carteiras'] for lj in c['lojas']}
    t('KPIs = montar_quadro', all(r['kpis'][k] == q[k] for k in ('total', 'lojas', 'vagas', 'sem_gerente',
                                                                   'exp_proximas', 'demissao_antiga', 'ajustes')), r['kpis'])
    a, b = lojas.get(loja_a.pk), lojas.get(loja_b.pk)
    t('Alfa e Beta na árvore; loja vazia e escritório sem gente fora', a and b and vazia.pk not in lojas)
    t('Alfa na carteira certa', a['carteira_id'] == carteira.pk and a['carteira'] == carteira.nome, a and a['carteira'])
    t('Beta em "Lojas sem carteira"', b['carteira_id'] == 0, b and b['carteira_id'])
    ba = blocos[loja_a.pk][1]
    t('Alfa: total/previsto/falta = bloco', a['total'] == ba['total'] == 4 and a['total_previsto'] == ba['total_previsto'] == 6
      and a['falta'] == ba['falta'] == 3, (a['total'], a['total_previsto'], a['falta']))
    t('Alfa: situação falta e diferença -2', a['situacao_hc'] == 'falta' and a['diferenca'] == -2, a)
    cmp_a = {c['grupo']: c for c in a['comparacao']}
    t('Alfa: comparação por função', cmp_a['Consultor']['real'] == 2 and cmp_a['Consultor']['previsto'] == 5
      and cmp_a['Consultor']['diferenca'] == -3 and cmp_a['Gerente']['diferenca'] == 0, cmp_a)
    t('Beta: gerente pelo grupo conta como gerente', not b['sem_gerente']
      and {g['grupo']: g['n'] for g in b['resumo']}.get('Gerente') == 1, b['resumo'])
    t('Beta: sobra 2 consultores', b['situacao_hc'] == 'sobra' and b['sobra'] == 2 and b['falta'] == 0, b)
    t('Alfa: marcas (demissão, ajustado, novo)', a['marcas']['demissao'] == 1 and a['marcas']['novos'] == 1, a['marcas'])
    t('Beta: ajustado (gerente pelo grupo)', b['marcas']['ajustados'] == 1, b['marcas'])
    t('desligado não entra no quadro', desligado.pk not in {p['id'] for _c, bb in blocos.values() for p in bb['pessoas']})

    print('\n== FILTROS ==')
    def lojas_de(cli, **params):
        resp = cli.get(f'{M}/', params).json()
        return {lj['id']: lj for c in resp['carteiras'] for lj in c['lojas']}, resp

    ls, resp = lojas_de(cc, busca='zzhcconsferias')
    t('busca por nome: só a loja dela, 1 resultado', set(ls) == {loja_a.pk} and ls[loja_a.pk]['encontrados'] == 1
      and resp['resultados'] == 1, (set(ls), resp.get('resultados')))
    ls, _ = lojas_de(cc, busca='99887766001')
    t('busca por login', set(ls) == {loja_a.pk})
    ls, _ = lojas_de(cc, busca='serra')
    t('busca por cidade (sem acento/caixa)', loja_a.pk in ls)
    ls, _ = lojas_de(cc, rapido='demissao', carteira=carteira.pk)
    t('rápido demissão + carteira', set(ls) == {loja_a.pk} and ls[loja_a.pk]['encontrados'] == 1, ls.keys())
    ls, _ = lojas_de(cc, rapido='vaga', loja=loja_a.pk)
    t('rápido vagas: as 3 vagas pelo previsto da Alfa', ls[loja_a.pk]['encontrados'] == 3, ls.get(loja_a.pk))
    ls, _ = lojas_de(cc, rapido='ajustado', loja=loja_b.pk)
    t('rápido ajustados (Beta)', ls[loja_b.pk]['encontrados'] == 1)
    ls, _ = lojas_de(cc, cargo='RECEPCIONISTA', loja=loja_a.pk)
    t('filtro de cargo (exato, como a tela)', ls[loja_a.pk]['encontrados'] == 1)
    ls, _ = lojas_de(cc, hc='falta')
    t('hc=falta: Alfa sim, Beta não', loja_a.pk in ls and loja_b.pk not in ls)
    ls, _ = lojas_de(cc, hc='sobra')
    t('hc=sobra: Beta sim, Alfa não', loja_b.pk in ls and loja_a.pk not in ls)
    ls, _ = lojas_de(cc, sem_gerente=1)
    t('sem_gerente: nenhuma das duas', loja_a.pk not in ls and loja_b.pk not in ls)
    ls, resp = lojas_de(cc, busca='zz-nada-zz-hc')
    t('busca sem resultado: árvore vazia', not ls and resp['resultados'] == 0)
    t('pílulas de carteira continuam todas com filtro', any(c['id'] == carteira.pk for c in resp['todas_carteiras'])
      and len(resp['todas_carteiras']) == len(q['carteiras']))

    print('\n== LOJA ==')
    d = cv.get(f'{M}/loja/{loja_a.pk}/').json()
    ids = {p['id'] for p in d['pessoas']}
    t('pessoas da Alfa', ids == {gerente_a.pk, cons_dem.pk, cons_fer.pk, novato.pk}, ids)
    pf = next(p for p in d['pessoas'] if p['id'] == cons_fer.pk)
    t('férias com datas', pf['situacao'] == 'FERIAS' and pf['status'] == 'Férias'
      and pf['ferias_fim'] == (hoje + timedelta(days=10)).isoformat(), pf)
    pd = next(p for p in d['pessoas'] if p['id'] == cons_dem.pk)
    t('demissão antiga marcada', pd['demissao_antiga'] == (hoje - timedelta(days=300)).isoformat() and pd['login'] == '99887766001')
    t('3 vagas pelo previsto', len(d['vagas_previstas']) == 3 and all(v['grupo'] == 'Consultor' for v in d['vagas_previstas']))
    prev = {x['funcao']: x['quantidade'] for x in d['previsto']}
    t('previsto com todas as funções', prev['Consultor'] == 5 and prev['Gerente'] == 1 and prev['Recepção'] == 0 and len(prev) == 9)
    d2 = cv.get(f'{M}/loja/{loja_a.pk}/', {'rapido': 'novo'}).json()
    t('filtro dentro da loja', [p['id'] for p in d2['pessoas']] == [novato.pk] and not d2['vagas_previstas'] and d2['filtrando'])
    t('loja fora do quadro: 404', cv.get(f'{M}/loja/{vazia.pk}/').status_code == 404)
    t('loja inexistente: 404', cv.get(f'{M}/loja/999999999/').status_code == 404)

    print('\n== PESSOA ==')
    p = cv.get(f'{M}/pessoa/{cons_dem.pk}/').json()
    t('pessoa do quadro: loja e carteira', p['loja']['id'] == loja_a.pk and p['carteira'] == carteira.nome and 'historico' in p)
    t('quem não está no quadro (desligado): 404', cv.get(f'{M}/pessoa/{desligado.pk}/').status_code == 404)
    t('id inexistente: 404', cv.get(f'{M}/pessoa/999999999/').status_code == 404)
    texto = cv.get(f'{M}/pessoa/{cons_dem.pk}/').content.decode()
    t('nada de senha/hash/CPF', cons_dem.password not in texto and '"cpf"' not in texto and '"password"' not in texto)

    print('\n== AJUSTE (servicos.salvar_ajuste) ==')
    r = cv.post(f'{M}/ajuste/', {'colaborador': cons_dem.pk, 'situacao': 'EXCLUIR', 'motivo': 'teste'},
                content_type='application/json')
    t('Ver: ajuste recusado (403, frase da web)', r.status_code == 403 and 'só para ver' in r.json().get('erro', ''), r.content[:200])
    t('Ver: nada gravado', not AjusteHC.objects.filter(colaborador=cons_dem).exists())
    r = cg.post(f'{M}/ajuste/', {'colaborador': cons_dem.pk, 'situacao': 'EXCLUIR'}, content_type='application/json')
    t('sem motivo: 400 com a mensagem do portal', r.status_code == 400 and 'motivo' in r.json().get('erro', ''), r.content[:200])
    r = cg.post(f'{M}/ajuste/', {'colaborador': cons_dem.pk, 'situacao': 'XYZ', 'motivo': 'a'}, content_type='application/json')
    t('situação inválida: 400', r.status_code == 400)
    r = cg.post(f'{M}/ajuste/', {'colaborador': cons_dem.pk, 'situacao': 'EXCLUIR', 'motivo': 'Saiu de verdade'},
                content_type='application/json')
    t('Gerenciar: tira do quadro', r.status_code == 200 and r.json()['ok'], r.content[:200])
    resp = cg.get(f'{M}/').json()
    t('aparece em "fora"', any(f['id'] == cons_dem.pk and f['motivo'] == 'Saiu de verdade' for f in resp['fora']))
    pf = cg.get(f'{M}/pessoa/{cons_dem.pk}/').json()
    t('pessoa fora abre com o ajuste', pf['fora'] is True and pf['ajuste']['situacao'] == 'EXCLUIR', pf)
    la = {lj['id']: lj for c in resp['carteiras'] for lj in c['lojas']}[loja_a.pk]
    t('Alfa: falta subiu para 4', la['total'] == 3 and la['falta'] == 4, (la['total'], la['falta']))
    r = cg.post(f'{M}/ajuste/', {'colaborador': cons_dem.pk, 'situacao': 'AUTO'}, content_type='application/json')
    t('voltar ao cadastro apaga o ajuste', r.status_code == 200 and not AjusteHC.objects.filter(colaborador=cons_dem).exists())
    r = cc.post(f'{M}/ajuste/', {'colaborador': novato.pk, 'funcao': 'Consultor', 'setor': loja_b.pk, 'motivo': 'Cobre a Beta'},
                content_type='application/json')
    t('SUPERADMIN: função e loja no quadro', r.status_code == 200, r.content[:200])
    d = cc.get(f'{M}/loja/{loja_b.pk}/').json()
    pn = next((p for p in d['pessoas'] if p['id'] == novato.pk), None)
    t('novato conta na Beta como Consultor (ajustado)', pn and pn['grupo'] == 'Consultor' and pn['ajustado']
      and pn['ajuste']['setor_nome'] == loja_b.name, pn)
    t('GET no ajuste não é aceito', cg.get(f'{M}/ajuste/').status_code == 405)

    print('\n== HC PREVISTO (servicos.salvar_previsto) ==')
    r = cv.post(f'{M}/loja/{loja_a.pk}/previsto/', {'quantidades': {'Consultor': 2}}, content_type='application/json')
    t('Ver: previsto recusado (403)', r.status_code == 403)
    t('Ver: previsto intacto', HCPrevisto.objects.get(setor=loja_a, funcao='Consultor').quantidade == 5)
    r = cg.post(f'{M}/loja/{loja_a.pk}/previsto/', {'quantidades': {'Consultor': 'abc'}}, content_type='application/json')
    t('quantidade inválida: 400', r.status_code == 400, r.content[:200])
    r = cg.post(f'{M}/loja/{loja_a.pk}/previsto/', {'quantidades': {'Consultor': 2, 'Gerente': 1, 'Recepção': 0}},
                content_type='application/json')
    j = r.json()
    t('Gerenciar: previsto salvo e devolve a loja', r.status_code == 200 and j['loja']['loja']['total_previsto'] == 3
      and j['loja']['loja']['situacao_hc'] == 'ok', j.get('loja', {}).get('loja'))
    t('gravado em HCPrevisto (zero apaga a linha)', not HCPrevisto.objects.filter(setor=loja_a, funcao='Recepção').exists()
      and HCPrevisto.objects.get(setor=loja_a, funcao='Consultor').quantidade == 2)
    r = cg.post(f'{M}/loja/{vazia.pk}/previsto/', {'quantidades': {'Consultor': 1}}, content_type='application/json')
    t('previsto em loja sem gente: grava e volta a loja na árvore', r.status_code == 200
      and r.json()['loja']['loja']['situacao_hc'] == 'falta', r.content[:200])
    r = cg.post(f'{M}/loja/{vazia.pk}/previsto/', {'quantidades': {'Consultor': 0}}, content_type='application/json')
    t('zerar previsto', r.status_code == 200 and r.json()['msg'] == 'HC previsto zerado.' and r.json()['loja'] is None, r.content[:200])
    t('setor inexistente: 400', cg.post(f'{M}/loja/999999999/previsto/', {'quantidades': {'Consultor': 1}},
                                       content_type='application/json').status_code == 400)

    print('\n== OPÇÕES ==')
    o = cv.get(f'{M}/opcoes/').json()
    t('funções, situações, setores e cargos', len(o['funcoes']) == 9 and {s['valor'] for s in o['situacoes_ajuste']} == {'AUTO', 'INCLUIR', 'EXCLUIR'}
      and any(s['id'] == loja_b.pk for s in o['setores']) and 'RECEPCIONISTA' in o['cargos'])
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)

print(f'\n{ok} OK, {fail} falhas')
sys.exit(1 if fail else 0)
