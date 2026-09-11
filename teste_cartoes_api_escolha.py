"""/cartoes/api/gasto/: quem tem mais de um cartão recebe a lista para escolher.

Pedido: com mais de um cartão, mandar ao cliente pelo WhatsApp (Evolution API)
"Escolha o cartão: [ 01 ] Final 0000 Visa / [ 02 ] Final 0001 Mastercard" e
responder 200 "Mensagem enviada ao cliente". A escolha volta na mesma API.

Nada sai daqui: o envio da Evolution, a IA, o download da foto e a abertura do
chamado são trocados por dublês — e a trava central bloqueia qualquer envio sob
teste. Roda dentro de uma transação desfeita no fim.
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
from django.test import Client
from django.test.utils import override_settings

import core.evolution as evolution
from cartoes import views as cartoes_views
from cartoes.models import Cartao, Gasto

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


TOKEN = 'token-de-teste-cartoes'
CONFIG = dict(CARTOES_API_TOKEN=TOKEN, EVOLUTION_API_URL='https://evolution.exemplo',
              EVOLUTION_API_KEY='chave-falsa', EVOLUTION_INSTANCE='alex')
TELEFONE = '(00) 90000-0101'
JID = '5500900000101@s.whatsapp.net'
IA = {'valor': '42,50', 'estabelecimento': 'ZZ Posto', 'categoria': 'Combustível', 'data': '',
      'descricao': 'ZZ abastecimento'}

enviados = []
resposta_do_envio = [(True, '{"key": {"id": "falso"}}')]


def envio_falso(numero, texto, **kw):
    enviados.append((numero, texto))
    return resposta_do_envio[0]


marcador = transaction.atomic()
marcador.__enter__()
try:
    ja_usam = [uid for uid, fone in User.objects.filter(is_active=True).exclude(phone='').values_list('id', 'phone')
               if cartoes_views._norm_br_phone(fone) == '00900000101']
    assert not ja_usam, 'um usuário do banco já usa o telefone do teste'

    with override_settings(**CONFIG), \
            mock.patch.object(cartoes_views, 'enviar_texto', side_effect=envio_falso), \
            mock.patch.object(cartoes_views, 'analyze_expense', return_value=IA), \
            mock.patch.object(cartoes_views, '_download_image', return_value=(None, None)), \
            mock.patch.object(cartoes_views, 'abrir_chamado_do_gasto', return_value=None):

        dono = User.objects.create_user(username='zzcart.dono', email='zzcart.dono@exemplo-teste.local',
                                        password='S3nha!teste', first_name='ZZ', last_name='Cartoes',
                                        phone=TELEFONE)
        visa = Cartao.objects.create(first4='4111', last4='0000', responsavel=dono,
                                     validade_mes=12, validade_ano=2030, bandeira='VISA')
        master = Cartao.objects.create(first4='5555', last4='0001', responsavel=dono,
                                       validade_mes=11, validade_ano=2031, bandeira='MASTERCARD')
        Cartao.objects.create(first4='3782', last4='0002', responsavel=dono, validade_mes=1,
                              validade_ano=2029, bandeira='AMEX', ativo=False)

        c = Client()

        def lancar(**dados):
            corpo = {'telefone': TELEFONE, 'descricao': 'ZZ abastecimento'}
            corpo.update(dados)
            return c.post('/cartoes/api/gasto/', data=json.dumps(corpo), content_type='application/json',
                          HTTP_AUTHORIZATION=f'Bearer {TOKEN}')

        print('== MAIS DE UM CARTÃO: O CLIENTE ESCOLHE ==')
        antes = Gasto.objects.count()
        r = lancar()
        j = r.json() if r.status_code < 500 else {}
        t('responde 200', r.status_code == 200, r.content[:300])
        t('com "Mensagem enviada ao cliente"',
          j.get('mensagem') == 'Mensagem enviada ao cliente' and j.get('status') == 200, j)
        t('e marca que aguarda a escolha', j.get('aguardando_escolha') is True)
        t('manda uma mensagem', len(enviados) == 1, enviados)
        t('para o número de quem mandou o gasto', bool(enviados) and enviados[0][0] == TELEFONE)
        t('com a lista dos cartões ativos', bool(enviados) and enviados[0][1] ==
          'Escolha o cartão:\n\n[ 01 ] Final 0000 Visa\n[ 02 ] Final 0001 Mastercard',
          enviados[0][1] if enviados else '')
        t('cartão inativo fica fora da lista', bool(enviados) and '0002' not in enviados[0][1])
        t('a resposta traz as opções', j.get('cartoes') == [
            {'opcao': '01', 'last4': '0000', 'bandeira': 'Visa'},
            {'opcao': '02', 'last4': '0001', 'bandeira': 'Mastercard'}], j.get('cartoes'))
        t('nenhum gasto é lançado antes da escolha', Gasto.objects.count() == antes)

        r = lancar(telefone=JID)
        t('com o JID do WhatsApp também acha a pessoa e responde para o JID',
          r.status_code == 200 and enviados[-1][0] == JID, r.content[:200])

        print('\n== A ESCOLHA VOLTA NA MESMA API ==')
        n_envios = len(enviados)
        r = lancar(cartao_opcao='02')
        j = r.json() if r.status_code < 500 else {}
        t('opção 02: lança no Mastercard final 0001', r.status_code == 200 and j.get('success')
          and j.get('cartao') == '••••0001' and Gasto.objects.filter(cartao=master).exists(), r.content[:300])
        t('sem mandar mensagem de novo', len(enviados) == n_envios)
        r = lancar(cartao_opcao=1)
        t('opção como número (1): Visa', r.status_code == 200 and r.json().get('cartao') == '••••0000'
          and Gasto.objects.filter(cartao=visa).exists(), r.content[:300])
        r = lancar(cartao_last4='0001')
        t('o desempate pelo final continua valendo', r.status_code == 200 and r.json().get('cartao') == '••••0001')
        antes = Gasto.objects.count()
        r = lancar(cartao_opcao='05')
        t('opção fora da lista: 400 e nada lançado', r.status_code == 400
          and 'escolha de 01 a 02' in r.json().get('error', '') and Gasto.objects.count() == antes, r.content[:200])

        print('\n== FALHA NO ENVIO ==')
        resposta_do_envio[0] = (False, 'HTTP 500: instância desconectada')
        r = lancar()
        t('não finge que mandou (502, sem a mensagem de sucesso)',
          r.status_code == 502 and 'mensagem' not in r.json(), r.content[:300])
        resposta_do_envio[0] = (True, '{}')

        print('\n== UM CARTÃO SÓ: SEGUE DIRETO ==')
        master.ativo = False
        master.save(update_fields=['ativo'])
        n_envios = len(enviados)
        r = lancar()
        t('lança direto no único cartão ativo, sem mensagem', r.status_code == 200
          and r.json().get('cartao') == '••••0000' and len(enviados) == n_envios, r.content[:300])
        r = c.post('/cartoes/api/gasto/', data=json.dumps({'telefone': TELEFONE, 'descricao': 'x'}),
                   content_type='application/json')
        t('sem o token: 401', r.status_code == 401, r.status_code)

    print('\n== O CLIENTE DA EVOLUTION ==')
    t('sob teste, bloqueia sem tocar a rede',
      evolution.enviar_texto(JID, 'oi') == (False, 'Envio bloqueado: processo de teste.'))
    t('o JID vai como está', evolution.normalizar_numero(JID) == JID)
    t('telefone ganha o 55', evolution.normalizar_numero('(27) 99999-0000') == '5527999990000')
    t('telefone que já tem 55 fica igual', evolution.normalizar_numero('+55 27 99999-0000') == '5527999990000')

    class RespostaFalsa:
        status = 201

        def read(self):
            return b'{"key": {"id": "abc"}}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    pedidos = []

    def urlopen_falso(req, timeout=10):
        pedidos.append(req)
        return RespostaFalsa()

    # Simula o servidor (fora de teste) com a rede trocada por um dublê: nada sai.
    with mock.patch.object(sys, 'argv', ['manage.py', 'runserver']), \
            mock.patch.object(evolution.urlrequest, 'urlopen', side_effect=urlopen_falso), \
            override_settings(EVOLUTION_API_URL='https://evolution.exemplo/', EVOLUTION_API_KEY='chave-falsa',
                              EVOLUTION_INSTANCE='alex'):
        resultado = evolution.enviar_texto(JID, 'Escolha o cartão:')
        pedido = pedidos[0] if pedidos else None
        t('fora de teste, envia', resultado[0] is True, resultado)
        t('no sendText da instância', pedido is not None
          and pedido.full_url == 'https://evolution.exemplo/message/sendText/alex', pedido and pedido.full_url)
        t('com a chave no header apikey', pedido is not None and pedido.get_header('Apikey') == 'chave-falsa')
        t('e o corpo number/text', pedido is not None
          and json.loads(pedido.data) == {'number': JID, 'text': 'Escolha o cartão:'})
    with mock.patch.object(sys, 'argv', ['manage.py', 'runserver']), \
            mock.patch.object(evolution.urlrequest, 'urlopen', side_effect=AssertionError('tocou a rede')), \
            override_settings(EVOLUTION_API_KEY=''):
        t('sem configuração, nem tenta', evolution.enviar_texto(JID, 'x')[0] is False)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
