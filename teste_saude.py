"""Teste do /saude/ e do vigia de requisições presas (core/saude.py) — 25/11/2026.

Não usa banco: monta o middleware com um get_response de mentira.
"""
import logging
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
import django  # noqa: E402
django.setup()

from django.test import RequestFactory  # noqa: E402

from core import saude  # noqa: E402

OK = FALHAS = 0


def t(nome, cond, extra=''):
    global OK, FALHAS
    if cond:
        OK += 1
        print(f'  OK  {nome}')
    else:
        FALHAS += 1
        print(f'  XX  {nome} {extra}')


class Captura(logging.Handler):
    def __init__(self):
        super().__init__()
        self.msgs = []

    def emit(self, record):
        self.msgs.append(record.getMessage())


captura = Captura()
logging.getLogger('core.saude').addHandler(captura)
rf = RequestFactory()

print('== /saude/ ==')
chamou = []
mw = saude.SaudeMiddleware(lambda r: chamou.append(r) or 'resposta')
r = mw(rf.get('/saude/', HTTP_HOST='qualquer.coisa'))
t('responde ok sem passar adiante', r.status_code == 200 and r.content == b'ok' and not chamou)
t('outra página passa adiante', mw(rf.get('/home/')) == 'resposta' and len(chamou) == 1)
t('nada fica anotado depois que a página termina', saude.em_andamento() == [])

print('\n== VIGIA ==')
saude.PRESA_APOS, saude.OLHAR_A_CADA = 1, 0.3
saude._vigia = None


def lenta(request):
    time.sleep(2.2)
    return 'devagar'


mw_lenta = saude.SaudeMiddleware(lenta)
th = threading.Thread(target=lambda: mw_lenta(rf.get('/ponto/')))
th.start()
time.sleep(0.5)
andando = saude.em_andamento()
t('a requisição aparece em andamento', any(c[2] == '/ponto/' for c in andando), andando)
th.join()
presas = [m for m in captura.msgs if 'REQUISIÇÃO PRESA' in m]
t('passou do limite: vai para o log com a pilha', len(presas) == 1 and '/ponto/' in presas[0] and 'lenta' in presas[0],
  presas[:1])
t('avisa uma vez só por requisição', len(presas) == 1)
t('terminou: sai da anotação', saude.em_andamento() == [])

captura.msgs.clear()
mw_proposito = saude.SaudeMiddleware(lenta)
mw_proposito(rf.get('/sap/atualizar/'))
time.sleep(0.5)
t('caminho que demora de propósito não vira alarme', not any('PRESA' in m for m in captura.msgs))

print(f'\n{OK} OK / {FALHAS} falhas')
sys.exit(1 if FALHAS else 0)
