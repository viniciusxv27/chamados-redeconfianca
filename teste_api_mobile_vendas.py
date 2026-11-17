"""Módulo Vendas no app (/api/mobile/v1/m/vendas/): porta, recorte, tabela, clientes e orçamento.

Roda dentro de uma transação desfeita no fim: não grava nada no banco. O orçamento
nunca sai pelo servidor (o envio do Evolution fica vigiado e tem de ficar parado).
"""
import os
import sys
from decimal import Decimal
from unittest import mock

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

from users.models import Sector
from vendas.models import (AcessoVendas, Cliente, ItemPreco, Orcamento, PagamentoVenda, Venda, VendaProduto,
                           VendaServico)

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
M = f'{BASE}/m/vendas'


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

envio = mock.patch('vendas.orcamentos.evolution.enviar_texto', return_value=(True, 'zz'))
enviar_texto = envio.start()
marcador = transaction.atomic()
marcador.__enter__()
try:
    loja = Sector.objects.create(name='Loja ZZ App Vendas')
    outra = Sector.objects.create(name='Loja ZZ App Outra')
    escritorio = Sector.objects.create(name='ZZ Escritório App')

    def novo(apelido, setor_=loja, hierarquia='PADRAO', **kw):
        return User.objects.create_user(
            username=f'zzvd.{apelido}', email=f'zzvd.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZ{apelido.title()}', last_name='Teste', sector=setor_, hierarchy=hierarquia, **kw)

    def liberar(user, *chaves):
        AcessoVendas.objects.create(user=user, permissoes=list(chaves))

    vera = novo('vera')                          # vendedora: só "Ver módulo"
    liberar(vera, 'modulo.ver')
    vito = novo('vito', outra)                   # outro vendedor, outra loja
    liberar(vito, 'modulo.ver')
    gil = novo('gil', escritorio)                # gestor: vê a rede e consulta clientes
    liberar(gil, 'rede.ver', 'clientes.ver')
    caio = novo('caio')                          # sem AcessoVendas: fora
    desligado = novo('desligado')                # acesso inativo: fora
    AcessoVendas.objects.create(user=desligado, permissoes=['modulo.ver'], ativo=False)
    chefe = novo('chefe', escritorio, 'SUPERADMIN')

    def entrar(user):
        r = Client().post(f'{BASE}/auth/login/', {'email': user.email, 'senha': 'S3nha!teste'},
                          content_type='application/json')
        return Client(HTTP_AUTHORIZATION=f'Bearer {r.json().get("token", "")}')

    cv, cvi, cg, cc, cd, cs = (entrar(u) for u in (vera, vito, gil, caio, desligado, chefe))

    print('== PORTA DO MÓDULO ==')

    def chaves(c):
        r = c.get(f'{BASE}/modulos/')
        return {m['chave'] for m in r.json()} if r.status_code == 200 else {'erro', r.status_code}

    t('com "Ver módulo" vê Vendas na lista de módulos', 'vendas' in chaves(cv), chaves(cv))
    t('SUPERADMIN vê Vendas', 'vendas' in chaves(cs))
    t('sem AcessoVendas não vê Vendas', 'vendas' not in chaves(cc))
    t('acesso inativo não vê Vendas', 'vendas' not in chaves(cd))
    for rota in ('', 'vendas/', 'precos/', 'orcamentos/', 'clientes/', 'vendas/1/', 'precos/1/'):
        r = cc.get(f'{M}/{rota}')
        t(f'sem acesso: 403 em /{rota}', r.status_code == 403, r.status_code)
    t('acesso inativo: 403', cd.get(f'{M}/').status_code == 403)
    t('sem token: 401', Client().get(f'{M}/').status_code == 401)

    print('\n== VENDAS (RECORTE) ==')
    agora = timezone.now()

    def venda(vendedor, loja_, cliente, valor, created_by=None, **kw):
        v = Venda.objects.create(loja=loja_, pdv_nome=loja_.name, vendedor=vendedor, created_by=created_by or vendedor,
                                 data_venda=agora, cliente_nome=cliente, cliente_cpf='529.982.247-25',
                                 forma_pagamento='PIX', tipo_venda='Produto', **kw)
        VendaProduto.objects.create(venda=v, nome_produto=f'ZZ Aparelho {cliente}', valor_venda=Decimal(valor),
                                    sku='ZZSKU1', cor='Preto', serial='356938035643809')
        PagamentoVenda.objects.create(venda=v, forma='PIX', valor=Decimal(valor))
        return v

    da_vera = venda(vera, loja, 'ZZ Cliente Vera', '1000.00')
    lancada_pela_vera = venda(vito, outra, 'ZZ Lançada pela Vera', '50.00', created_by=vera)
    do_vito = venda(vito, outra, 'ZZ Cliente Vito', '300.00')
    VendaServico.objects.create(venda=do_vito, servico='Alta', tipo_servico='ALTA', plano_novo='ZZ Plano',
                                valor_plano=Decimal('99.90'))

    r = cv.get(f'{M}/vendas/', {'periodo': 'todos', 'busca': 'ZZ'})
    ids = {v['id'] for v in r.json().get('resultados', [])}
    t('lista: a que vendeu e a que lançou', {da_vera.pk, lancada_pela_vera.pk} <= ids, (r.status_code, ids))
    t('lista: a de outro vendedor não', do_vito.pk not in ids)
    r = cv.get(f'{M}/vendas/', {'periodo': 'todos', 'busca': 'ZZ', 'escopo': 'rede'})
    t('quem não vê a rede não alarga o recorte com ?escopo=rede',
      do_vito.pk not in {v['id'] for v in r.json()['resultados']})
    t('detalhe da própria venda', cv.get(f'{M}/vendas/{da_vera.pk}/').status_code == 200)
    t('detalhe da venda alheia: 404', cv.get(f'{M}/vendas/{do_vito.pk}/').status_code == 404)
    r = cv.get(f'{M}/vendas/{da_vera.pk}/')
    j = r.json()
    t('detalhe traz produto com SKU, cor e pagamento', j['produtos'][0]['sku'] == 'ZZSKU1'
      and j['produtos'][0]['cor'] == 'Preto' and j['pagamentos'][0]['forma'] == 'Pix' and j['total'] == 1000.0, j)
    t('vendedora vê o histórico da própria venda', j['historico'] == [])

    r = cg.get(f'{M}/vendas/', {'periodo': 'todos', 'busca': 'ZZ'})
    ids = {v['id'] for v in r.json()['resultados']}
    t('quem vê a rede vê todas', {da_vera.pk, lancada_pela_vera.pk, do_vito.pk} <= ids, ids)
    r = cg.get(f'{M}/vendas/{do_vito.pk}/')
    t('quem vê a rede abre a venda de outro (com serviço e vendedor)', r.status_code == 200
      and r.json()['servicos'][0]['plano'] == 'ZZ Plano' and r.json()['vendedor'] == 'ZZVito Teste', r.content[:200])
    r = cs.get(f'{M}/vendas/', {'periodo': 'todos', 'busca': 'ZZ', 'escopo': 'minhas'})
    t('rede escolhendo "minhas" fica só com as dela', r.json()['escopo'] == 'minhas'
      and not {v['id'] for v in r.json()['resultados']} & {da_vera.pk, do_vito.pk})
    r = cs.get(f'{M}/vendas/', {'periodo': 'hoje', 'busca': 'ZZ'})
    t('período de hoje', {da_vera.pk, do_vito.pk} <= {v['id'] for v in r.json()['resultados']})

    print('\n== RESUMO ==')
    r = cv.get(f'{M}/', {'periodo': 'todos'})
    j = r.json()
    t('resumo responde com o que a pessoa pode', r.status_code == 200 and j['pode']['rede'] is False
      and j['pode']['clientes'] is False and j['pode']['lancar'] is False and j['pode']['orcamentos'] is True,
      r.content[:300])
    t('indicadores no recorte da vendedora', j['indicadores']['quantidade'] >= 2
      and j['indicadores']['hoje'] >= 2 and 'por_loja' not in j, j.get('indicadores'))
    r = cg.get(f'{M}/', {'periodo': 'mes'})
    t('quem vê a rede tem ranking por loja e vendedor', 'por_loja' in r.json() and 'por_vendedor' in r.json())
    t('série dos últimos 14 dias', len(r.json()['serie']) == 14)

    print('\n== TABELA DE PREÇOS ==')
    aparelho = ItemPreco.objects.create(
        categoria='SMARTPHONES', nome='ZZ Galaxy Teste 256GB', cod_sap='ZZ000', valor=Decimal('1999.00'),
        extra={'FABRICANTE': 'ZZMarca', 'CATEGORIA': 'Smartphone',
               'skus': [{'sku': 'ZZSKUAZUL1', 'cor': 'Azul'}, {'sku': 'ZZSKUPRETO1', 'cor': 'Preto'}],
               'grupos': ['PRÉ', 'POS'],
               'tabela': {'PRÉ': {'Preço': '1999,00', 'PIX e Vivo Pay': '1899,00', '10x': '199,90'},
                          'POS': {'Preço': '1799,00', 'PIX e Vivo Pay': '1699,00'}}})
    inativo = ItemPreco.objects.create(categoria='SMARTPHONES', nome='ZZ Galaxy Antigo', valor=Decimal('10'), ativo=False)
    r = cv.get(f'{M}/precos/', {'busca': 'zz galaxy teste'})
    j = r.json()
    achado = next((p for p in j.get('resultados', []) if p['id'] == aparelho.pk), None)
    t('busca acha o aparelho com "a partir de" e PIX', achado is not None and achado['a_partir'] == 1799.0
      and achado['pix'] == 1699.0 and achado['cores'] == 2, (r.status_code, achado))
    t('abas do catálogo com contagem', any(a['valor'] == 'SMARTPHONES' and a['n'] >= 1 for a in j.get('abas', [])))
    r = cv.get(f'{M}/precos/', {'busca': 'ZZSKUPRETO1'})
    achado = next((p for p in r.json()['resultados'] if p['id'] == aparelho.pk), None)
    t('SKU digitado aponta a cor', achado is not None and achado['sku_digitado'] == {'sku': 'ZZSKUPRETO1', 'cor': 'Preto'},
      achado)
    r = cv.get(f'{M}/precos/{aparelho.pk}/')
    j = r.json()
    t('detalhe: SKU por cor e matriz grupo × condição', r.status_code == 200 and len(j['skus']) == 2
      and j['sku'] is None and [l['grupo'] for l in j['linhas']] == ['PRÉ', 'POS']
      and j['colunas'][0]['chave'] == 'Preço' and j['linhas'][1]['celulas'][0] == {'valor': 1799.0, 'menor': True}, j)
    t('produto inativo: 404', cv.get(f'{M}/precos/{inativo.pk}/').status_code == 404)

    print('\n== CLIENTES ==')
    cli_loja = Cliente.objects.create(cpf='90000000001', nome='ZZ Cliente da Loja', telefone='27999990000')
    da_vera.cliente = cli_loja
    da_vera.save(update_fields=['cliente'])
    cli_outra = Cliente.objects.create(cpf='90000000002', nome='ZZ Cliente de Fora')
    do_vito.cliente = cli_outra
    do_vito.save(update_fields=['cliente'])
    t('sem "Consultar clientes": 403', cv.get(f'{M}/clientes/').status_code == 403)
    t('sem "Consultar clientes": ficha 403', cv.get(f'{M}/clientes/{cli_loja.cpf}/').status_code == 403)
    AcessoVendas.objects.filter(user=vera).update(permissoes=['modulo.ver', 'clientes.ver'])
    vera = User.objects.get(pk=vera.pk)
    r = cv.get(f'{M}/clientes/', {'busca': 'ZZ Cliente'})
    nomes = {c['nome'] for c in r.json().get('resultados', [])}
    t('vendedora vê o cliente da loja dela', 'ZZ Cliente da Loja' in nomes, (r.status_code, nomes))
    t('e não o de outra loja', 'ZZ Cliente de Fora' not in nomes)
    t('ficha de cliente de outra loja: 404', cv.get(f'{M}/clientes/{cli_outra.cpf}/').status_code == 404)
    r = cv.get(f'{M}/clientes/{cli_loja.cpf}/')
    t('ficha do cliente com as vendas do portal', r.status_code == 200
      and any(v['id'] == da_vera.pk and v['pode_abrir'] for v in r.json()['vendas_portal']), r.content[:300])
    r = cg.get(f'{M}/clientes/{cli_outra.cpf}/')
    t('quem vê a rede abre qualquer ficha', r.status_code == 200)

    print('\n== ORÇAMENTO ==')
    r = cv.get(f'{M}/orcamentos/')
    j = r.json()
    t('modelo com vendedora e loja preenchidos', r.status_code == 200 and 'Zzvera Teste' in j['modelo']
      and 'Loja ZZ App Vendas' in j['modelo'] and '{nome}' in j['modelo'], r.content[:300])
    r = cv.post(f'{M}/orcamentos/', {'cliente_nome': '', 'pilar': 'X', 'numero': '123', 'texto': ''},
                content_type='application/json')
    t('campos errados: 400 com todos os motivos', r.status_code == 400 and len(r.json().get('erros', [])) == 4,
      r.content[:300])
    corpo = {'cliente_nome': 'ZZ Maria', 'pilar': 'SMARTPHONES', 'numero': '(27) 99876-5432',
             'texto': 'ZZ Galaxy 256GB por R$ 1.799,00 no cartão'}
    r = cv.post(f'{M}/orcamentos/', corpo, content_type='application/json')
    j = r.json()
    o = Orcamento.objects.filter(vendedor=vera, cliente_nome='ZZ Maria').first()
    t('orçamento salvo como ABERTO, na loja dela', r.status_code == 201 and o is not None and o.status == 'ABERTO'
      and o.loja_id == loja.pk, r.content[:300])
    t('devolve o link do WhatsApp do aparelho (wa.me com a mensagem)',
      j.get('link_whatsapp', '').startswith(f'https://wa.me/{o.numero}?text=') and 'R%24%201.799%2C00' in j['link_whatsapp'],
      j.get('link_whatsapp'))
    t('mensagem montada pelo modelo do portal', 'ZZ Maria' in j['mensagem'] and 'Smartphones' in j['mensagem'])
    r = cv.post(f'{M}/orcamentos/', corpo, content_type='application/json')
    t('clique duplo não duplica', r.json().get('id') == o.pk
      and Orcamento.objects.filter(vendedor=vera, cliente_nome='ZZ Maria').count() == 1)
    t('o servidor não mandou nada pelo WhatsApp', not enviar_texto.called)
    r = cv.get(f'{M}/orcamentos/')
    t('lista traz o orçamento dela', [x['id'] for x in r.json()['resultados']][:1] == [o.pk] and r.json()['hoje'] >= 1)
    r = cg.get(f'{M}/orcamentos/')
    t('não lista o orçamento de outra pessoa', o.pk not in {x['id'] for x in r.json().get('resultados', [])})
    t('fora de loja e sem liberação de Orçamentos: 403', r.status_code == 403, r.status_code)
    from orcamentos.models import LiberacaoOrcamentos
    LiberacaoOrcamentos.objects.create(user=gil)
    r = cg.get(f'{M}/orcamentos/')
    t('com a liberação do /orcamentos/ entra, e não vê o da vendedora', r.status_code == 200
      and o.pk not in {x['id'] for x in r.json()['resultados']})
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    envio.stop()
    for nome in settings.CACHES:
        caches[nome].clear()
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
