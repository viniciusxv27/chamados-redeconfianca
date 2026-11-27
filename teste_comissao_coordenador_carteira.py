"""ATING CARTEIRA do coordenador em /users/commission/?user=<coordenador>.

A aba COO/SNP da planilha não tem ATING_CART_{PILAR}: a carteira do coordenador
é o %ATING_{PILAR}_1 (mês de referência), o mesmo número que a aba do gerente
copia no ATING_CART_{PILAR}. O processamento do coordenador não montava a chave
'carteira' e o cartão do pilar caía no |default:0 → 0,0% em todos os pilares.

Não toca no banco.
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from users.commission_views import process_coordenador_commission_data

ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


# Linha no formato da aba "REMUNERAÇÃO COO e SNP" (09/2026, valores do ARIEL).
linha = {'NOME': 'ARIEL', 'H_SEGURO': 'H3'}
for key in ('MOVEL', 'FIXA', 'SMART', 'ELETRO', 'ESSEN', 'SEG', 'SVA'):
    linha[f'%ATING_{key}_3'] = 0.9635
    linha[f'%ATING_{key}_2'] = 0.9236
    linha[f'%ATING_{key}_1'] = 0.9341
linha['%ATING_SVA_1'] = None  # célula vazia
linha.update({
    'COM_MÓVEL': 1000.0, 'COM_FIXA': 200.0, 'COM_SMARTPHONE': 300.0,
    'COM_ELETRONICOS - A': 10.0, 'COM_ELETRONICOS - B': 90.0,
    'COM_ESSENCIAIS - A': 20.0, 'COM_ESSENCIAIS - B': 5.0,
    'COM_SEGURO': 70.0, 'COM_SVA': 15.0,
    'HUNTER_ELETRONICOS': 40.0, 'HUNTER_SVA': 2.5,
    'IQ_ACELADOR MÓVEL': 50.0, 'IQ_DEFLATOR FIXA': -20.0,
    'REMUNERÇÃO COM IQ': 1782.5,
})

p = process_coordenador_commission_data(linha)
pil = {x['nome']: x for x in p['pilares']}

print('== ATING CARTEIRA VEM DO %ATING_{PILAR}_1 ==')
t('Móvel 93,41%', abs(pil['Móvel']['carteira'] - 93.41) < 0.01, pil['Móvel'].get('carteira'))
t('Seguro também', abs(pil['Seguro']['carteira'] - 93.41) < 0.01, pil['Seguro'].get('carteira'))
t('e é o mesmo número do mês de referência', pil['Móvel']['carteira'] == pil['Móvel']['pct_1'])
t('com dado não vira "sem dado"', pil['Móvel']['carteira_sem_dado'] is False)

print('\n== CÉLULA VAZIA CONTINUA "SEM DADO" (NÃO 0%) ==')
t('SVA vazio marca sem dado', pil['SVA']['carteira_sem_dado'] is True)

print('\n== HUNTER APARECE NO CARTÃO ==')
t('Seguro mostra H3', pil['Seguro']['hunter'] == 'H3', pil['Seguro'].get('hunter'))
t('Móvel sem Hunter', not pil['Móvel']['hunter'])

print('\n== COLUNA TOTAL DO DETALHAMENTO POR PILAR ==')
t('Móvel = comissão + acelerador de IQ', pil['Móvel']['soma_com_iq'] == 1050.0, pil['Móvel'].get('soma_com_iq'))
t('Fixa = comissão - deflator (sinal da planilha não importa)', pil['Fixa']['soma_com_iq'] == 180.0, pil['Fixa'].get('soma_com_iq'))
t('Eletrônicos = A + B + Hunter', pil['Eletrônicos']['soma_com_iq'] == 140.0, pil['Eletrônicos'].get('soma_com_iq'))
t('SVA soma o Hunter', pil['SVA']['soma_com_iq'] == 17.5)
t('IQ da Móvel vira multiplicador 1,05', abs(pil['Móvel']['iq_multiplicador'] - 105) < 1e-9)
t('subtotal bate com REMUNERÇÃO COM IQ',
  abs(p['totais']['total_pilares_com_iq'] - 1782.5) < 0.01, p['totais'].get('total_pilares_com_iq'))

print('\n== A TELA SEM A BARRA ==')
with open('templates/users/commission.html', encoding='utf-8') as f:
    tela = f.read()
t('rótulo ATING CARTEIRA continua', 'ATING CARTEIRA:' in tela)
t('barra do atingimento saiu', 'carteira_barra' not in tela)

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
