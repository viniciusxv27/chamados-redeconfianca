"""ATING LOJA: "sem dado" não pode virar 0%.

A planilha de 07/2026 traz o Hunter "H3" dentro de ATING_CART_SEG e o bloco de
SVA vazio. O safe_float devolvia 0 nos dois casos e a tela desenhava 0,00% de
atingimento — indistinguível de um zero real.

Não toca no banco.
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from users.commission_views import convert_percentage, e_numero, safe_float

ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


print('== O QUE É NÚMERO E O QUE NÃO É ==')
for valor, esperado, rotulo in [
    (0.5236, True, 'fração da planilha'),
    (0, True, 'zero de verdade'),
    (0.0, True, 'zero decimal'),
    ('1.5', True, 'número em texto'),
    ('H3', False, 'Hunter no lugar do atingimento'),
    ('H2', False, 'Hunter do SVA'),
    (None, False, 'célula vazia'),
    ('', False, 'texto vazio'),
    (float('nan'), False, 'NaN do pandas'),
    ('n/a', False, 'texto qualquer'),
]:
    t(f'{rotulo}: {valor!r}', e_numero(valor) is esperado, e_numero(valor))

print('\n== O DEFEITO QUE ISSO EVITA ==')
t('safe_float("H3") continua devolvendo 0', safe_float('H3') == 0)
t('e 0 vira 0% na tela', convert_percentage(safe_float('H3')) == 0)
t('mas agora dá para saber que não é atingimento real',
  e_numero('H3') is False and e_numero(0) is True)

print('\n== ZERO DE VERDADE CONTINUA SENDO ZERO ==')
t('planilha com 0 não vira "sem dado"', e_numero(0) is True)
t('nem 0.0', e_numero(0.0) is True)

print('\n== O CÓDIGO E A TELA ==')
with open('users/commission_views.py', encoding='utf-8') as f:
    codigo = f.read()
t('o pilar carrega a marca de "sem dado"', "'carteira_sem_dado'" in codigo)
t('e ela olha as duas colunas possíveis',
  "e_numero(data.get(f'ATING_CART_{key}')) or e_numero(data.get(cart_key))" in codigo)

with open('templates/users/commission.html', encoding='utf-8') as f:
    tela = f.read()
t('a tela mostra "sem dado" em vez de 0%', 'sem dado' in tela)
t('com aviso visível', 'fa-triangle-exclamation' in tela)
t('e não desenha barra vermelha de 0%', 'pilar.carteira_sem_dado' in tela)

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
