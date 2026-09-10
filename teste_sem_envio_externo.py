"""Nenhum teste manda WhatsApp ou e-mail de verdade.

Os testes rodam contra o banco de dev, que tem colaboradores reais com
telefone e e-mail. Um teste de comunicado "para todos" sem mock já disparou
WhatsApp real. Aqui se prova a trava central: sob teste os dois canais
bloqueiam sem tocar a rede; simulando produção, os dois tentariam enviar — com
a rede trocada por um dublê, para nenhuma mensagem sair nem nessa simulação.
"""
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.test.utils import override_settings

import core.zapi as zapi
import users.resend_email as resend
from core.utils import processo_de_teste

ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


class TocouARede(Exception):
    """O dublê da rede levanta isto: se aparecer, o envio foi tentado."""


rede_whatsapp = mock.Mock(side_effect=TocouARede('urlopen chamado'))
rede_email = mock.Mock(side_effect=TocouARede('requests.post chamado'))

CONFIG = dict(ZAPI_INSTANCE_ID='instancia-falsa', ZAPI_TOKEN='token-falso',
              ZAPI_CLIENT_TOKEN='', RESEND_API_KEY='re_chave_falsa')

with mock.patch.object(zapi.urlrequest, 'urlopen', rede_whatsapp), \
     mock.patch.object(resend.requests, 'post', rede_email), \
     override_settings(**CONFIG):

    print('== SOB TESTE, NADA SAI ==')
    t('este processo é reconhecido como teste', processo_de_teste())

    resultado = zapi.send_whatsapp_message('+55 27 99999-0000', 'mensagem de teste')
    t('WhatsApp devolve bloqueado', resultado == (False, 'Envio bloqueado: processo de teste.'), resultado)
    t('sem tocar a rede', rede_whatsapp.call_count == 0, rede_whatsapp.call_count)

    resultado = resend.enviar('alguem@exemplo-teste.local', 'Assunto', '<p>oi</p>')
    t('e-mail devolve bloqueado', resultado == (False, 'Envio bloqueado: processo de teste.'), resultado)
    t('sem tocar a rede', rede_email.call_count == 0, rede_email.call_count)

    from communications.whatsapp import enviar_whatsapp_comunicado

    class Pessoa:
        phone = '+55 27 98888-0000'
        first_name = 'Ana'
        username = 'ana'

    enfileiradas = enviar_whatsapp_comunicado('ZZ Aviso', [Pessoa()], 'https://portal/x')
    import time
    time.sleep(0.5)                       # a thread de envio roda e volta
    t('o comunicado monta a mensagem normalmente', enfileiradas == 1, enfileiradas)
    t('mas a thread de envio não toca a rede', rede_whatsapp.call_count == 0, rede_whatsapp.call_count)

    print('\n== EM PRODUÇÃO A TRAVA NÃO ATRAPALHA ==')
    # Simula o processo do servidor. A rede continua sendo o dublê, então a
    # "tentativa de envio" é só a chamada contada — nada sai daqui.
    with mock.patch.object(sys, 'argv', ['manage.py', 'runserver']):
        t('manage.py não é processo de teste', not processo_de_teste())

        try:
            zapi.send_whatsapp_message('+55 27 99999-0000', 'mensagem')
        except TocouARede:
            pass
        t('o WhatsApp tentaria enviar', rede_whatsapp.call_count == 1, rede_whatsapp.call_count)

        try:
            resend.enviar('alguem@exemplo-teste.local', 'Assunto', '<p>oi</p>')
        except TocouARede:
            pass
        t('o e-mail tentaria enviar', rede_email.call_count == 1, rede_email.call_count)

    for nome in ('gunicorn', 'teste.py', 'meu_teste_x.py', 'teste_sem_envio_externo.pyc'):
        with mock.patch.object(sys, 'argv', [f'/usr/bin/{nome}']):
            t(f'"{nome}" não é confundido com teste', not processo_de_teste())

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
