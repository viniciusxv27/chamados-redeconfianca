"""Campos do resgate em /prizes/create/, /prizes/<id>/edit/ e /prizes (27/11/2026).

Pedido: quem cadastra o prêmio pode criar campos à vontade ("Numeração", "Observação"...)
e o Mercadinho pede esses campos na hora de resgatar.

Roda dentro de uma transação desfeita no fim (nada fica no banco) e com cache em memória.
Nada sai para fora (o resgate só grava SystemLog).
"""
import io
import os
import sys
from decimal import Decimal

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
import redeconfianca.settings as _cfg  # noqa: E402
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'} for n in _cfg.CACHES}
django.setup()

from django.conf import settings  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model  # noqa: E402
from django.db import transaction  # noqa: E402
from django.http import QueryDict  # noqa: E402
from django.test import Client  # noqa: E402

from prizes.models import (Prize, Redemption, campos_do_formulario,  # noqa: E402
                           normalizar_campos, respostas_do_resgate)
from users.models import Sector  # noqa: E402

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


print('== REGRA (sem banco) ==')
q = QueryDict(mutable=True)
q.setlist('campo_nome', ['Numeração', '  ', 'Observação', 'numeração'])
q.setlist('campo_obrigatorio', ['1', '1', '0', '1'])
campos = campos_do_formulario(q)
t('linha vazia e nome repetido saem', [c['nome'] for c in campos] == ['Numeração', 'Observação'], campos)
t('obrigatório/opcional na ordem certa', [c['obrigatorio'] for c in campos] == [True, False])
t('texto solto vira campo obrigatório', normalizar_campos(['Cor']) == [{'nome': 'Cor', 'obrigatorio': True}])
t('no máximo 10 campos', len(normalizar_campos([f'C{i}' for i in range(15)])) == 10)
resp, erro = respostas_do_resgate(campos, {'resposta_0': '', 'resposta_1': 'x'})
t('obrigatório vazio é recusado', resp is None and erro == 'Preencha: Numeração.', erro)
resp, erro = respostas_do_resgate(campos, {'resposta_0': ' 42 ', 'resposta_1': ''})
t('opcional pode ficar vazio', erro is None and resp == [{'nome': 'Numeração', 'valor': '42'},
                                                          {'nome': 'Observação', 'valor': ''}], resp)
t('sem campos, nada a pedir', respostas_do_resgate([], {}) == ([], None))

marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Setor Campos Teste')

    def novo(apelido, hierarquia='PADRAO', saldo='0'):
        return User.objects.create_user(
            username=f'zzcampos.{apelido}', email=f'zzcampos.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZ{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia,
            balance_cs=Decimal(saldo))

    gestor = novo('gestor', 'SUPERADMIN')
    ana = novo('ana', saldo='5000')
    cg = Client(); cg.force_login(gestor)
    ca = Client(); ca.force_login(ana)

    print('\n== CRIAR E EDITAR ==')
    t('tela de criar tem o editor de campos', 'data-campos-resgate' in cg.get('/prizes/create/').content.decode())
    cg.post('/prizes/create/', {
        'name': 'ZZ Tênis Campos', 'description': 'tênis', 'value_cs': '100', 'stock': '5',
        'priority': 'NORMAL', 'campo_nome': ['Numeração', 'Observação'], 'campo_obrigatorio': ['1', '0'],
    })
    p = Prize.objects.get(name='ZZ Tênis Campos')
    t('criar grava os campos', p.campos == [{'nome': 'Numeração', 'obrigatorio': True},
                                            {'nome': 'Observação', 'obrigatorio': False}], p.campos_resgate)
    tela = cg.get(f'/prizes/{p.pk}/edit/').content.decode()
    t('editar mostra os campos gravados', 'value="Numeração"' in tela and 'value="Observação"' in tela)
    cg.post(f'/prizes/{p.pk}/edit/', {
        'name': p.name, 'description': 'tênis', 'value_cs': '100', 'estoque_disponivel': '5',
        'priority': 'NORMAL', 'is_active': 'on',
        'campo_nome': ['Numeração', 'Cor', 'Observação'], 'campo_obrigatorio': ['1', '1', '0'],
    })
    p.refresh_from_db()
    t('editar troca os campos', [c['nome'] for c in p.campos] == ['Numeração', 'Cor', 'Observação'], p.campos)

    print('\n== RESGATE EM /prizes ==')
    tela = ca.get('/prizes/', {'search': 'ZZ Tênis Campos'}).content.decode()
    import json, re
    m = re.search(r'<script id="camposPorPremio" type="application/json">(.*?)</script>', tela, re.S)
    mapa = json.loads(m.group(1)) if m else {}
    t('Mercadinho leva os campos para o modal',
      [c['nome'] for c in mapa.get(str(p.pk), [])] == ['Numeração', 'Cor', 'Observação'], mapa)
    t('o modal tem onde montar os campos', 'id="redeemCampos"' in tela)
    r = ca.post(f'/prizes/redeem/{p.pk}/', {'resposta_0': '42'})
    t('sem a Cor, recusa', r.json() == {'success': False, 'error': 'Preencha: Cor.'}, r.content)
    ana.refresh_from_db()
    t('e não debita nada', ana.balance_cs == Decimal('5000'), ana.balance_cs)
    r = ca.post(f'/prizes/redeem/{p.pk}/', {'resposta_0': '42', 'resposta_1': 'Preto'})
    t('com os obrigatórios, resgata', r.json().get('success') is True, r.content)
    red = Redemption.objects.filter(user=ana, prize=p).latest('pk')
    t('respostas ficam no resgate', red.respostas == [{'nome': 'Numeração', 'valor': '42'},
                                                      {'nome': 'Cor', 'valor': 'Preto'},
                                                      {'nome': 'Observação', 'valor': ''}], red.respostas)
    t('texto para a gestão pula o vazio', red.respostas_texto == 'Numeração: 42 · Cor: Preto', red.respostas_texto)

    print('\n== QUEM GERENCIA VÊ ==')
    t('/prizes/redemptions/ mostra', 'Numeração:</strong> 42' in cg.get('/prizes/redemptions/').content.decode())
    t('o histórico de quem resgatou mostra', 'Cor:</strong> Preto' in ca.get('/prizes/my-redemptions/').content.decode())
    t('os últimos resgates do editar mostram', 'Cor:</strong> Preto' in cg.get(f'/prizes/{p.pk}/edit/').content.decode())
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(cg.get('/prizes/redemptions/export/').content))
    ws = wb.active
    cab = [c.value for c in ws[1]]
    col = cab.index('Informações do resgate') if 'Informações do resgate' in cab else None
    linha = next((r for r in ws.iter_rows(min_row=2, values_only=True) if r[0] == red.pk), None)
    t('Excel tem a coluna com as respostas', col is not None and linha and linha[col] == 'Numeração: 42 · Cor: Preto', linha)

    print('\n== APP ==')
    BASE = '/api/mobile/v1'
    r = Client().post(f'{BASE}/auth/login/', {'email': ana.email, 'senha': 'S3nha!teste',
                                              'plataforma': 'ios', 'dispositivo': 'ZZ'},
                      content_type='application/json')
    app = Client(HTTP_AUTHORIZATION=f'Bearer {r.json()["token"]}')
    M = f'{BASE}/m/mercadinho'
    d = app.get(f'{M}/premios/{p.pk}/').json()
    t('detalhe traz os campos', [c['nome'] for c in d.get('campos', [])] == ['Numeração', 'Cor', 'Observação'], d.get('campos'))
    r = app.post(f'{M}/premios/{p.pk}/resgatar/', {}, content_type='application/json')
    t('app sem respostas: 400 com o que falta', r.status_code == 400 and 'Numeração' in r.json().get('erro', ''), r.content)
    r = app.post(f'{M}/premios/{p.pk}/resgatar/', {'respostas': ['40', 'Azul', 'presente']},
                 content_type='application/json')
    t('app com respostas: resgata', r.status_code == 201, r.content)
    info = (r.json().get('resgate') or {}).get('informacoes')
    t('e devolve as informações', info and info[0] == {'nome': 'Numeração', 'valor': '40'}, info)

    print('\n== PRÊMIO SEM CAMPOS CONTINUA IGUAL ==')
    simples = Prize.objects.create(name='ZZ Caneca Campos', description='x', value_cs=Decimal('10'),
                                   stock=5, created_by=gestor)
    t('campos_resgate nasce vazio', simples.campos == [])
    t('resgata sem pedir nada', ca.post(f'/prizes/redeem/{simples.pk}/').json().get('success') is True)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
