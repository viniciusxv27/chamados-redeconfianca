"""Meta do simulador multiplicada por 100 pela formatação de vírgula.

O defeito: a função que converte "1.000,00" (tela) para "1000.00" (backend)
reescrevia o próprio campo visível. Ao voltar pelo histórico o navegador
restaura esse valor já convertido sem o DOMContentLoaded rodar para remascarar
— e o envio seguinte lia o ponto decimal como separador de milhar, mandando
100000. A cada envio, mais um x100.

Este arquivo reimplementa as funções do template em Python e cobra que a
conversão seja idempotente. Não toca no banco.
"""
import os
import re
import sys

ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


CAMINHO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'templates', 'simulator', 'dashboard.html')
with open(CAMINHO, encoding='utf-8') as f:
    TEMPLATE = f.read()


def format_brl(valor):
    """Espelha formatBRL do template: máscara de centavos."""
    d = re.sub(r'\D', '', str(valor or ''))
    if not d:
        return ''
    d = re.sub(r'^0+(?=\d)', '', d)
    while len(d) < 3:
        d = '0' + d
    return re.sub(r'\B(?=(\d{3})+(?!\d))', '.', d[:-2]) + ',' + d[-2:]


def to_float_str(valor):
    """Espelha toFloatStr do template — a função corrigida."""
    if valor == 0:
        return '0'
    if not valor:
        return ''
    s = str(valor).strip().replace(' ', '')
    if ',' in s:
        return s.replace('.', '').replace(',', '.')
    if re.fullmatch(r'-?\d+\.\d{1,2}', s):
        return s
    return s.replace('.', '')


print('== O QUE A PESSOA DIGITA VIRA O QUE ELA QUIS ==')
casos = [
    ('100000', '1.000,00', '1000.00', 'mil reais'),
    ('50000', '500,00', '500.00', 'quinhentos'),
    ('1234567', '12.345,67', '12345.67', 'doze mil e pouco'),
    ('5', '0,05', '0.05', 'cinco centavos'),
    ('0', '0,00', '0.00', 'zero digitado sozinho'),
    ('', '', '', 'campo vazio'),
]
for digitado, na_tela, enviado, rotulo in casos:
    tela = format_brl(digitado)
    t(f'{rotulo}: aparece {na_tela!r}', tela == na_tela, tela)
    t(f'{rotulo}: envia {enviado!r}', to_float_str(tela) == enviado, to_float_str(tela))

print('\n== CONVERTER DUAS VEZES NÃO PODE MUDAR O VALOR ==')
# Era exatamente aqui que estava o x100.
for valor in ['1.000,00', '12.345,67', '500,00', '0,05', '1000.00', '1000.5',
              '1000', '1.000', '', '50,5']:
    uma = to_float_str(valor)
    duas = to_float_str(uma)
    tres = to_float_str(duas)
    t(f'{valor!r} estável em três envios', uma == duas == tres,
      f'{uma!r} -> {duas!r} -> {tres!r}')

print('\n== A FALHA ANTIGA, PARA NÃO VOLTAR ==')
def antiga(v):
    if not v:
        return ''
    return str(v).replace('.', '').replace(',', '.')

t('a versão antiga de fato multiplicava por 100',
  antiga(antiga('1.000,00')) == '100000', antiga(antiga('1.000,00')))
t('a versão nova não', to_float_str(to_float_str('1.000,00')) == '1000.00')

print('\n== O TEMPLATE TEM A CORREÇÃO ==')
t('toFloatStr trata o ponto decimal à parte',
  r'/^-?\d+\.\d{1,2}$/.test(s)' in TEMPLATE)
t('a regra brasileira só entra quando há vírgula',
  "s.indexOf(',') >= 0" in TEMPLATE)
t('a dica de meta-diária usa a mesma conversão',
  'parseFloat(toFloatStr(el.value))' in TEMPLATE)
t('não sobrou cópia da regra antiga',
  TEMPLATE.count(r"replace(/\./g, '').replace(',', '.')") == 1,
  TEMPLATE.count(r"replace(/\./g, '').replace(',', '.')"))
t('volta pelo histórico remascara o campo',
  "addEventListener('pageshow'" in TEMPLATE and 'e.persisted' in TEMPLATE)

print('\n== O BACKEND CONTINUA ACEITANDO OS DOIS FORMATOS ==')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
import django  # noqa: E402
django.setup()
from simulator.services import to_float  # noqa: E402

for entrada, esperado in [('1000.00', 1000.0), ('1.000,00', 1000.0), ('1000', 1000.0),
                          ('R$ 1.000,00', 1000.0), ('', 0.0), (None, 0.0)]:
    t(f'to_float({entrada!r}) = {esperado}', to_float(entrada) == esperado, to_float(entrada))

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
