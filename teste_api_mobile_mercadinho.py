"""App mobile — módulo Mercadinho (/api/mobile/v1/m/mercadinho/).

Roda dentro de uma transação desfeita no fim: prêmios, resgates, saldos e sugestões
criados aqui somem. Os prêmios de teste não têm imagem (nada sobe para o MinIO).
O resgate pelo app é comparado com o resgate pela tela da web (/prizes/redeem/).
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

from prizes.models import CSTransaction, Prize, PrizeCategory, Redemption, SugestaoProduto
from users.models import Sector

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
M = f'{BASE}/m/mercadinho'


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

marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Setor Mercadinho App')

    def novo(apelido, hierarquia='PADRAO', saldo='0', **kw):
        return User.objects.create_user(
            username=f'zzmerc.{apelido}', email=f'zzmerc.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZ{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia,
            balance_cs=Decimal(saldo), **kw)

    ana = novo('ana', saldo='100')                 # PADRÃO: a home não mostra saldo, o /prizes/ mostra
    web_user = novo('web', saldo='100')            # faz o mesmo resgate pela tela da web
    beto = novo('beto', saldo='50')
    gestor = novo('gestor', 'ADMINISTRATIVO')

    cat_a = PrizeCategory.objects.create(name='ZZ Eletrônicos App')
    cat_b = PrizeCategory.objects.create(name='ZZ Vale App')
    cat_off = PrizeCategory.objects.create(name='ZZ Categoria desligada', active=False)

    def premio(nome, valor, cat=cat_a, **kw):
        kw.setdefault('stock', 5)
        return Prize.objects.create(name=nome, description=f'{nome} descrição', category=cat,
                                    value_cs=Decimal(valor), **kw)

    p1 = premio('ZZ Fone App', '30')
    p_desc = premio('ZZ Caneca App', '50', cat_b, has_discount=True, discount_percentage=Decimal('20'),
                    discounted_value=Decimal('40'))
    p_caro = premio('ZZ TV App', '1000')
    p_esgotado = premio('ZZ Esgotado App', '10', stock=0)
    p_inativo = premio('ZZ Inativo App', '10', is_active=False)
    p_ilimitado = premio('ZZ Ilimitado App', '5', unlimited_stock=True, stock=0)

    def entrar(user):
        r = Client().post(f'{BASE}/auth/login/', {'email': user.email, 'senha': 'S3nha!teste',
                                                  'plataforma': 'ios', 'dispositivo': 'ZZ'},
                          content_type='application/json')
        return Client(HTTP_AUTHORIZATION=f'Bearer {r.json()["token"]}')

    ca, cb, cg = entrar(ana), entrar(beto), entrar(gestor)

    print('== ENTRADA ==')
    t('aparece em /modulos/ para todo logado (menu da web sem condição)',
      'mercadinho' in [m['chave'] for m in ca.get(f'{BASE}/modulos/').json()])
    t('sem token: 401/403', Client().get(f'{M}/').status_code in (401, 403))
    from api_mobile.modulos import mercadinho as mod
    with mock.patch.dict(mod.MODULO, {'pode_entrar': lambda u: False}):
        t('gate fechado: 403 no módulo', ca.get(f'{M}/').status_code == 403)
        t('gate fechado: 403 no resgate', ca.post(f'{M}/premios/{p1.pk}/resgatar/').status_code == 403)
        t('gate fechado: some de /modulos/', 'mercadinho' not in [m['chave'] for m in ca.get(f'{BASE}/modulos/').json()])
    inativo = novo('inativo', is_active=False)
    t('pode_entrar: inativo não entra', mod.pode_entrar(inativo) is False and mod.pode_entrar(ana) is True)

    print('\n== VITRINE ==')
    r = ca.get(f'{M}/?busca=ZZ')
    js = r.json()
    nomes = {p['nome'] for p in js.get('resultados', [])}
    t('vitrine: 200', r.status_code == 200, r.content[:300])
    t('saldo aparece para PADRÃO (como no /prizes/)', js.get('saldo') == 100.0, js.get('saldo'))
    t('só prêmio ativo', 'ZZ Inativo App' not in nomes and {'ZZ Fone App', 'ZZ TV App', 'ZZ Esgotado App'} <= nomes, nomes)
    cats = [c['nome'] for c in js.get('categorias', [])]
    t('categorias ativas', 'ZZ Eletrônicos App' in cats and 'ZZ Categoria desligada' not in cats)
    t('gestor: ana não gerencia', js.get('pode_gerenciar') is False)
    t('gestor: ADMINISTRATIVO gerencia', cg.get(f'{M}/?busca=ZZ').json().get('pode_gerenciar') is True)
    por_nome = {p['nome']: p for p in js['resultados']}
    t('esgotado: indisponível e 0 restantes', por_nome['ZZ Esgotado App']['disponivel'] is False
      and por_nome['ZZ Esgotado App']['restantes'] == 0, por_nome['ZZ Esgotado App'])
    t('ilimitado: restantes None e disponível', por_nome['ZZ Ilimitado App']['restantes'] is None
      and por_nome['ZZ Ilimitado App']['disponivel'] is True)
    t('caro: saldo insuficiente', por_nome['ZZ TV App']['saldo_suficiente'] is False
      and por_nome['ZZ Fone App']['saldo_suficiente'] is True)
    t('desconto: valor final 40 e 20%', por_nome['ZZ Caneca App']['valor_final'] == 40.0
      and por_nome['ZZ Caneca App']['desconto_percentual'] == 20.0, por_nome['ZZ Caneca App'])
    r = ca.get(f'{M}/?busca=ZZ&categoria={cat_b.pk}')
    t('filtro de categoria', [p['nome'] for p in r.json()['resultados']] == ['ZZ Caneca App'], r.json()['resultados'])
    r = ca.get(f'{M}/?busca=ZZ Fone')
    t('busca por nome', [p['nome'] for p in r.json()['resultados']] == ['ZZ Fone App'])
    r = ca.get(f'{M}/?busca=ZZ&ordem=maior_valor')
    t('ordem maior valor', r.json()['resultados'][0]['nome'] == 'ZZ TV App')

    print('\n== DETALHE ==')
    r = ca.get(f'{M}/premios/{p1.pk}/')
    t('detalhe: 200 com descrição', r.status_code == 200 and r.json()['descricao'] == 'ZZ Fone App descrição', r.content[:200])
    t('detalhe de prêmio inativo: 404', ca.get(f'{M}/premios/{p_inativo.pk}/').status_code == 404)
    t('detalhe inexistente: 404', ca.get(f'{M}/premios/999999999/').status_code == 404)

    print('\n== RESGATAR (igual à web) ==')
    r = ca.post(f'{M}/premios/{p1.pk}/resgatar/')
    t('app: resgate 201', r.status_code == 201, r.content[:300])
    t('app: devolve saldo novo 70', r.json().get('saldo') == 70.0, r.json())
    cw = Client()
    cw.force_login(web_user)
    rw = cw.post(f'/prizes/redeem/{p1.pk}/')
    t('web: resgate ok', rw.status_code == 200 and rw.json().get('success'), rw.content[:200])
    ana.refresh_from_db()
    web_user.refresh_from_db()
    t('saldo debitou igual à web (100 → 70)', ana.balance_cs == web_user.balance_cs == Decimal('70'),
      (ana.balance_cs, web_user.balance_cs))
    ra = Redemption.objects.get(user=ana, prize=p1)
    rwb = Redemption.objects.get(user=web_user, prize=p1)
    t('resgate igual à web (PENDENTE, valores)', (ra.status, ra.original_value, ra.discount_value, ra.final_value)
      == (rwb.status, rwb.original_value, rwb.discount_value, rwb.final_value) == ('PENDENTE', Decimal('30'), Decimal('0'), Decimal('30')),
      (ra.status, ra.final_value, rwb.final_value))
    ta = CSTransaction.objects.get(user=ana, related_redemption=ra)
    tw = CSTransaction.objects.get(user=web_user, related_redemption=rwb)
    t('movimentação de C$ igual à web', (ta.amount, ta.transaction_type, ta.status, ta.description)
      == (tw.amount, tw.transaction_type, tw.status, tw.description) == (Decimal('-30'), 'REDEMPTION', 'APPROVED', 'Resgate: ZZ Fone App')
      and ta.created_by_id == ana.pk, (ta.amount, ta.transaction_type))

    r = ca.post(f'{M}/premios/{p_desc.pk}/resgatar/')
    ana.refresh_from_db()
    t('com desconto debita o valor final (70 → 30)', r.status_code == 201 and ana.balance_cs == Decimal('30'), (r.status_code, ana.balance_cs))

    antes = Redemption.objects.filter(user=ana).count()
    r = ca.post(f'{M}/premios/{p_caro.pk}/resgatar/')
    ana.refresh_from_db()
    t('saldo insuficiente: 400 com a mensagem da web', r.status_code == 400 and r.json().get('erro') == 'Saldo insuficiente', r.content[:200])
    t('saldo insuficiente: não debita nem cria resgate', ana.balance_cs == Decimal('30')
      and Redemption.objects.filter(user=ana).count() == antes)
    r = ca.post(f'{M}/premios/{p_esgotado.pk}/resgatar/')
    ana.refresh_from_db()
    t('estoque zero: 400 "Prêmio não disponível"', r.status_code == 400 and r.json().get('erro') == 'Prêmio não disponível', r.content[:200])
    t('estoque zero: não debita', ana.balance_cs == Decimal('30') and Redemption.objects.filter(user=ana).count() == antes)
    t('prêmio inativo: 404', ca.post(f'{M}/premios/{p_inativo.pk}/resgatar/').status_code == 404)
    t('resgatar por GET: 405', ca.get(f'{M}/premios/{p1.pk}/resgatar/').status_code == 405)

    # Estoque se esgota com aprovados/entregues (Prize.available).
    p_ultimo = premio('ZZ Último App', '1', stock=1)
    Redemption.objects.create(user=beto, prize=p_ultimo, status='APROVADO', final_value=Decimal('1'))
    r = ca.post(f'{M}/premios/{p_ultimo.pk}/resgatar/')
    t('estoque tomado por aprovado: recusa', r.status_code == 400, r.content[:200])

    print('\n== MEUS RESGATES ==')
    rb = Redemption.objects.create(user=beto, prize=p1, status='PENDENTE', final_value=Decimal('30'))
    r = ca.get(f'{M}/resgates/')
    js = r.json()
    ids = {x['id'] for x in js['resultados']}
    t('lista: só os meus', r.status_code == 200 and ids == {ra.pk, Redemption.objects.get(user=ana, prize=p_desc).pk}, ids)
    t('contagem por status', js['contagem']['PENDENTE'] == 2, js.get('contagem'))
    t('pendente pode cancelar', all(x['pode_cancelar'] for x in js['resultados']))
    r = ca.get(f'{M}/resgates/?situacao=ENTREGUE')
    t('filtro por status', r.json()['resultados'] == [])

    # Aguardando retirada na SEDE
    from django.utils import timezone
    from datetime import timedelta
    rb.status = 'APROVADO'
    rb.pickup_deadline = timezone.localdate() + timedelta(days=5)
    rb.pickup_notified_at = timezone.now()
    rb.save()
    r = cb.get(f'{M}/')
    t('vitrine: aguardando retirada com prazo', [x['id'] for x in r.json()['aguardando_retirada']] == [rb.pk]
      and r.json()['aguardando_retirada'][0]['retirar_ate'] == rb.pickup_deadline.isoformat(), r.json().get('aguardando_retirada'))
    item = next(x for x in cb.get(f'{M}/resgates/').json()['resultados'] if x['id'] == rb.pk)
    t('resgate aprovado: aguardando retirada e sem cancelar', item['aguardando_retirada'] is True and item['pode_cancelar'] is False, item)

    print('\n== CANCELAR ==')
    t('cancelar resgate de outra pessoa: 404', ca.post(f'{M}/resgates/{rb.pk}/cancelar/').status_code == 404)
    t('cancelar aprovado (web só oferece no pendente): 400', cb.post(f'{M}/resgates/{rb.pk}/cancelar/').status_code == 400)
    r = ca.post(f'{M}/resgates/{ra.pk}/cancelar/')
    ana.refresh_from_db()
    ra.refresh_from_db()
    t('cancelar o meu pendente: 200 e devolve C$ (30 → 60)', r.status_code == 200 and ana.balance_cs == Decimal('60')
      and ra.status == 'CANCELADO' and r.json().get('saldo') == 60.0, (r.status_code, ana.balance_cs, r.content[:200]))
    t('cancelar: movimentação REFUND da web', CSTransaction.objects.filter(user=ana, related_redemption=ra,
                                                                           transaction_type='REFUND', amount=Decimal('30')).exists())
    t('cancelar de novo: 400', ca.post(f'{M}/resgates/{ra.pk}/cancelar/').status_code == 400)

    print('\n== SUGERIR PRODUTO ==')
    r = ca.post(f'{M}/sugerir/', {'link': 'javascript:alert(1)'}, content_type='application/json')
    t('link inválido: 400 com a mensagem da web', r.status_code == 400 and 'https://' in r.json().get('erro', ''), r.content[:200])
    r = ca.post(f'{M}/sugerir/', {'link': 'https://exemplo-teste.local/produto', 'nome': 'ZZ Garrafa',
                                  'observacao': 'boa'}, content_type='application/json')
    t('sugestão válida: 201', r.status_code == 201, r.content[:200])
    s = SugestaoProduto.objects.filter(user=ana).first()
    t('sugestão gravada', s and s.nome == 'ZZ Garrafa' and s.link == 'https://exemplo-teste.local/produto')
    for i in range(4):
        ca.post(f'{M}/sugerir/', {'link': f'https://exemplo-teste.local/p{i}'}, content_type='application/json')
    r = ca.post(f'{M}/sugerir/', {'link': 'https://exemplo-teste.local/sexta'}, content_type='application/json')
    t('limite de 5 em análise: 400', r.status_code == 400 and SugestaoProduto.objects.filter(user=ana).count() == 5, r.content[:200])
    t('API não deixa mensagem para a próxima tela da web (sem cookie de messages)',
      'messages' not in r.cookies, list(r.cookies.keys()))
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
