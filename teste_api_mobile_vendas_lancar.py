"""Lançar venda pelo app (/api/mobile/v1/m/vendas/lancar/): o mesmo caminho da tela /vendas/nova/.

Roda dentro de uma transação desfeita no fim: não grava nada no banco. Envio externo
(Evolution e Z-API) fica vigiado e tem de ficar parado.
"""
import json
import os
import random
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

from users.models import Sector
from vendas.models import AcessoVendas, Cliente, ItemPreco, Plano, Venda, VendaProduto

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
L = f'{BASE}/m/vendas/lancar'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def cpf_novo():
    """Um CPF válido que ninguém tem no cadastro."""
    while True:
        base = [random.randint(0, 9) for _ in range(9)]
        for n in (9, 10):
            s = sum(d * (n + 1 - i) for i, d in enumerate(base[:n]))
            base.append(s * 10 % 11 % 10)
        cpf = ''.join(map(str, base))
        if len(set(cpf)) > 1 and not Cliente.objects.filter(cpf=cpf).exists():
            return cpf


def imei_novo():
    """15 números com o dígito de Luhn, que nenhuma venda usou."""
    while True:
        corpo = '35' + ''.join(str(random.randint(0, 9)) for _ in range(12))
        soma = 0
        for i, c in enumerate(corpo):
            d = int(c)
            if i % 2:
                d *= 2
                if d > 9:
                    d -= 9
            soma += d
        imei = corpo + str((10 - soma % 10) % 10)
        if not VendaProduto.objects.filter(serial=imei).exists():
            return imei


for nome in settings.CACHES:
    caches[nome].clear()

vigias = [mock.patch('core.evolution.enviar_texto', return_value=(True, 'zz')),
          mock.patch('core.zapi.send_whatsapp_message', return_value=(True, 'zz'))]
enviados = [v.start() for v in vigias]
marcador = transaction.atomic()
marcador.__enter__()
try:
    loja = Sector.objects.create(name='Loja ZZ Lançar App')
    outra = Sector.objects.create(name='Loja ZZ Lançar Outra')

    def novo(apelido, setor_=loja, *chaves):
        u = User.objects.create_user(
            username=f'zzlv.{apelido}', email=f'zzlv.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZ{apelido.title()}', last_name='Teste', sector=setor_, hierarchy='PADRAO')
        AcessoVendas.objects.create(user=u, permissoes=list(chaves))
        return u

    vera = novo('vera', loja, 'modulo.ver')                                   # vê, não lança
    lia = novo('lia', loja, 'modulo.ver', 'vendas.lancar')                    # lança na loja dela
    gil = novo('gil', outra, 'modulo.ver', 'vendas.lancar', 'vendas.lancar_outros')  # lança por outra loja/vendedor

    def entrar(user):
        r = Client().post(f'{BASE}/auth/login/', {'email': user.email, 'senha': 'S3nha!teste'},
                          content_type='application/json')
        return Client(HTTP_AUTHORIZATION=f'Bearer {r.json().get("token", "")}')

    cv, cl, cg = entrar(vera), entrar(lia), entrar(gil)

    aparelho = ItemPreco.objects.create(
        categoria='SMARTPHONES', nome='ZZ Lança Phone 128GB', cod_sap='ZZL000', valor=Decimal('1500.00'),
        extra={'FABRICANTE': 'ZZMarca', 'CATEGORIA': 'Smartphone',
               'skus': [{'sku': 'ZZLAZUL', 'cor': 'Azul'}, {'sku': 'ZZLPRETO', 'cor': 'Preto'}],
               'grupos': ['PRÉ', 'POS'],
               'tabela': {'PRÉ': {'Preço': '1500,00', 'PIX e Vivo Pay': '1400,00', '10x': '160,00'},
                          'POS': {'Preço': '1300,00', 'PIX e Vivo Pay': '1250,00'}}})
    capa = ItemPreco.objects.create(categoria='PRODUTOS', nome='ZZ Lança Capa', cod_sap='ZZLCAPA', valor=Decimal('80.00'),
                                    extra={'CATEGORIA': 'Capa'})
    plano = Plano.objects.create(nome='ZZ Plano Lança 50GB', segmentacao='POS', valor=Decimal('99.90'))

    print('== QUEM LANÇA ==')
    for rota in ('', 'cliente/?cpf=1', 'produtos/?q=zz', 'renova/?codigo=1', 'planos-fora/?q=zz'):
        r = cv.get(f'{L}/{rota}')
        t(f'sem "Lançar venda": 403 em lancar/{rota}', r.status_code == 403, r.status_code)
    r = cv.post(f'{L}/', {}, content_type='application/json')
    t('sem "Lançar venda": 403 no POST', r.status_code == 403)
    r = cv.get(f'{BASE}/m/vendas/')
    t('o resumo diz que não lança', r.json()['pode']['lancar'] is False)

    print('\n== FORMULÁRIO ==')
    r = cl.get(f'{L}/')
    f = r.json()
    t('formulário da vendedora: PDV do cadastro, sem escolher outro', r.status_code == 200 and f['loja']['id'] == loja.pk
      and f['pode_outros'] is False and f['lojas'] == [] and f['vendedores'] == [] and f['sem_pdv'] is False, r.content[:300])
    t('formulário traz planos, tipos de serviço com campos, formas e Vivo+',
      any(p['id'] == plano.pk for p in f['planos']) and any(x['valor'] == 'ALTA' and 'plano' in x['campos'] for x in f['tipos_servico'])
      and any(x['valor'] == 'CARTAO' and x['nsu'] and x['parcelas'] for x in f['formas']) and 'vivo_mais' in f)
    r = cg.get(f'{L}/')
    f = r.json()
    t('quem lança por outros escolhe PDV e vendedor', f['pode_outros'] is True and any(x['id'] == outra.pk for x in f['lojas'])
      and any(x['id'] == lia.pk for x in f['vendedores']))

    print('\n== CONSULTAS DA TELA ==')
    r = cl.get(f'{L}/cliente/', {'cpf': '123.456.789-00'})
    t('CPF inválido: o aviso da web', r.status_code == 200 and r.json()['ok'] is False and 'CPF' in r.json()['erro'], r.content[:200])
    cpf = cpf_novo()
    r = cl.get(f'{L}/cliente/', {'cpf': cpf})
    t('CPF novo: não encontrado, para cadastrar', r.json().get('ok') is True and r.json().get('encontrado') is False)
    r = cl.get(f'{L}/produtos/', {'q': 'zz lança phone', 'segmentacao': 'PRE'})
    achado = next((x for x in r.json().get('results', []) if x['id'] == aparelho.pk), None)
    t('busca de produto com preço do grupo PRÉ e as cores', achado is not None and achado['valor_sugerido'] == '1500.00'
      and achado['grupo_padrao'] == 'PRÉ' and len(achado['skus']) == 2, achado)
    r = cl.get(f'{L}/renova/', {'codigo': 'RN-999999999'})
    t('Renova que não existe', r.json().get('ok') is False)

    print('\n== VALIDAÇÃO POR ETAPA ==')
    r = cl.post(f'{L}/', {}, content_type='application/json')
    e = r.json()
    t('vazio: 400 com cliente e itens', r.status_code == 400 and 'cliente' in e['etapas'] and 'produtos' in e['etapas'], e)
    ruim = {'cliente': {'cpf': cpf, 'nome': 'ZZ Cliente Lança'},
            'produtos': [{'preco_id': aparelho.pk, 'imei': '123', 'grupamento': 'PRÉ', 'condicao': 'Preço'}],
            'servicos': [{'tipo': 'ALTA', 'plano_id': plano.pk}], 'linhas': [],
            'pagamentos': [{'forma': 'CARTAO', 'valor': '1500,00', 'nsu': '', 'parcelas': ''}]}
    r = cl.post(f'{L}/', ruim, content_type='application/json')
    e = r.json().get('etapas', {})
    t('IMEI inválido cai em produtos', any('IMEI' in x for x in e.get('produtos', [])), e)
    t('cartão sem NSU e parcelas cai em pagamento', any('NSU' in x for x in e.get('pagamento', []))
      and any('parcelas' in x for x in e.get('pagamento', [])), e)
    t('serviço sem linha cai em serviços', any('linha' in x for x in e.get('servicos', [])), e)
    t('nada foi gravado', not Venda.objects.filter(cliente_cpf__contains=cpf[:3], cliente_nome='ZZ Cliente Lança').exists())

    def payload(imei):
        return {
            'cliente': {'cpf': cpf, 'nome': 'ZZ Cliente Lança', 'telefone': '(27) 99876-1234', 'cep': '29100-000',
                        'logradouro': 'Rua ZZ', 'numero': '10', 'complemento': '', 'bairro': 'Centro', 'cidade': 'Vila Velha',
                        'uf': 'ES'},
            'linhas': ['27998761234'], 'observacao': 'ZZ pelo teste', 'vivo_mais': False, 'desconto_pix': '50,00',
            'pagamentos': [{'forma': 'PIX', 'valor': '1000,00', 'nsu': '', 'parcelas': ''},
                           {'forma': 'CARTAO', 'valor': '430,00', 'nsu': 'ab-12 34', 'parcelas': 3}],
            'plano_anterior': {'id': '', 'segmentacao': 'PRE', 'valor': '', 'nome': ''},
            'produtos': [
                {'preco_id': aparelho.pk, 'nome': aparelho.nome, 'sku': 'ZZLPRETO', 'imei': imei, 'prateleira_infinita': False,
                 'grupamento': 'PRÉ', 'condicao': 'Preço', 'valor_sugerido': 1500,
                 'renova_vini': False, 'renova_alied': True, 'renova_alied_valor': '100,00', 'renova_alied_pedido': 'ZZ123',
                 'renova_assurant': False, 'valor_final': None},
                {'preco_id': capa.pk, 'nome': capa.nome, 'sku': '', 'imei': '', 'prateleira_infinita': False,
                 'grupamento': '', 'condicao': '', 'valor_sugerido': 80, 'valor_final': None},
            ],
            'servicos': [{'tipo': 'ALTA', 'plano_id': plano.pk, 'portabilidade': True, 'operadora_origem': 'TIM'}],
        }

    print('\n== PDV E VENDEDOR (HIERARQUIA) ==')
    fora = dict(payload(imei_novo()), loja_id=outra.pk)
    r = cl.post(f'{L}/', fora, content_type='application/json')
    t('vendedora pedindo outro PDV: 400, nada gravado', r.status_code == 400 and 'cliente' in r.json()['etapas']
      and not Venda.objects.filter(created_by=lia).exists(), r.content[:300])
    r = cl.post(f'{L}/', dict(payload(imei_novo()), vendedor_id=gil.pk), content_type='application/json')
    t('vendedora lançando em nome de outro: 400', r.status_code == 400 and not Venda.objects.filter(created_by=lia).exists())

    print('\n== VENDA VÁLIDA (IGUAL À WEB) ==')
    imei_app, imei_web = imei_novo(), imei_novo()
    while imei_web == imei_app:
        imei_web = imei_novo()
    r = cl.post(f'{L}/', payload(imei_app), content_type='application/json')
    t('venda gravada: 201 com o id', r.status_code == 201 and r.json().get('id'), r.content[:500])
    v_app = Venda.objects.filter(pk=r.json().get('id')).first()

    web = Client()
    web.force_login(lia)
    rw = web.post('/vendas/nova/', json.dumps(payload(imei_web)), content_type='application/json',
                  HTTP_ACCEPT='application/json')
    jw = rw.json() if rw.headers.get('Content-Type', '').startswith('application/json') else {}
    t('a mesma venda pela tela da web grava', jw.get('ok') is True, (rw.status_code, rw.content[:300]))
    v_web = Venda.objects.filter(pk=jw.get('id')).first()

    if v_app and v_web:
        campos = ['loja_id', 'pdv_nome', 'vendedor_id', 'created_by_id', 'cliente_id', 'cliente_nome', 'cliente_cpf',
                  'cliente_telefone', 'tipo_venda', 'forma_pagamento', 'desconto_pix', 'vivo_mais', 'vivo_mais_percentual',
                  'nsu', 'parcelas', 'plano_anterior_id', 'plano_anterior_nome', 'segmentacao_anterior', 'valor_anterior',
                  'numero_fake', 'observacao', 'comprovante_fiscal']
        dif = {c: (getattr(v_app, c), getattr(v_web, c)) for c in campos if getattr(v_app, c) != getattr(v_web, c)}
        t('cabeçalho igual ao da web', not dif, dif)
        t('PDV e vendedor do cadastro', v_app.loja_id == loja.pk and v_app.vendedor_id == lia.pk and v_app.created_by_id == lia.pk)

        def produtos(v):
            return [(p.preco_id, p.nome_produto, p.sku, p.cor, p.categoria_slv, p.valor_sugerido, p.regra_preco,
                     p.renova_alied, p.renova_alied_valor, p.renova_alied_pedido, p.desconto_pix, p.desconto_vivo_mais,
                     p.valor_calculado, p.valor_venda, p.valor_editado) for p in v.produtos.order_by('id')]

        def servicos(v):
            return [(s.tipo_servico, s.plano_id, s.valor_plano, s.delta, s.linhas, s.numero_acesso, s.portabilidade,
                     s.operadora_origem) for s in v.servicos.order_by('id')]

        def pagamentos(v):
            return [(p.ordem, p.forma, p.valor, p.nsu, p.parcelas) for p in v.pagamentos.order_by('ordem')]

        t('produtos iguais aos da web', produtos(v_app) == produtos(v_web), (produtos(v_app), produtos(v_web)))
        t('serviços iguais aos da web', servicos(v_app) == servicos(v_web), (servicos(v_app), servicos(v_web)))
        t('pagamentos iguais aos da web', pagamentos(v_app) == pagamentos(v_web), (pagamentos(v_app), pagamentos(v_web)))
        aparelho_app = v_app.produtos.get(preco=aparelho)
        t('SKU/cor escolhidos e IMEI gravados', aparelho_app.sku == 'ZZLPRETO' and aparelho_app.cor == 'Preto'
          and aparelho_app.serial == imei_app)
        t('desconto no Pix repartido e NSU limpo', v_app.desconto_pix == Decimal('50.00')
          and sum(p.desconto_pix for p in v_app.produtos.all()) == Decimal('50.00') and v_app.nsu == 'AB1234'
          and v_app.forma_pagamento == 'MISTO')
        t('detalhe abre pelo app', cl.get(f'{BASE}/m/vendas/vendas/{v_app.pk}/').status_code == 200)

    r = cl.post(f'{L}/', payload(imei_app), content_type='application/json')
    t('IMEI já vendido: 400 em produtos', r.status_code == 400 and any('já foi vendido' in x for x in r.json()['etapas'].get('produtos', [])),
      r.content[:300])

    print('\n== POR OUTRA LOJA (lancar_outros) ==')
    r = cg.post(f'{L}/', dict(payload(imei_novo()), loja_id=loja.pk, vendedor_id=lia.pk), content_type='application/json')
    v = Venda.objects.filter(pk=r.json().get('id')).first()
    t('lança no PDV e no vendedor escolhidos', r.status_code == 201 and v is not None and v.loja_id == loja.pk
      and v.vendedor_id == lia.pk and v.created_by_id == gil.pk, r.content[:300])

    t('nenhum envio externo', not any(e.called for e in enviados))
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    for v in vigias:
        v.stop()
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
