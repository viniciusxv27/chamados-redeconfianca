"""Formatação leve na descrição e nos comentários da meta (impulso/formatacao.py) — 25/11/2026.

Sem banco: só as regras do texto → HTML seguro e do resumo sem marcação.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
import django  # noqa: E402
django.setup()

from django.template import Context, Template  # noqa: E402

from impulso.formatacao import para_html, sem_marcacao  # noqa: E402

OK = FALHAS = 0


def t(nome, cond, extra=''):
    global OK, FALHAS
    if cond:
        OK += 1
        print(f'  OK  {nome}')
    else:
        FALHAS += 1
        print(f'  XX  {nome} {extra}')


h = lambda s: str(para_html(s))  # noqa: E731
print('== MARCAÇÃO ==')
t('negrito, itálico (* e _), riscado e código',
  h('**a** *b* _c_ ~~d~~ `e`') == '<p><strong>a</strong> <em>b</em> <em>c</em> <s>d</s> <code>e</code></p>', h('**a** *b* _c_ ~~d~~ `e`'))
t('texto antigo, sem marcação, continua igual (quebra de linha vira <br>)', h('linha 1\nlinha 2') == '<p>linha 1<br>linha 2</p>')
t('linha em branco separa parágrafos', h('a\n\nb') == '<p>a</p><p>b</p>')
t('lista com traço, asterisco ou •', h('- um\n* dois\n• três') == '<ul><li>um</li><li>dois</li><li>três</li></ul>')
t('lista numerada', h('1. a\n2) b') == '<ol><li>a</li><li>b</li></ol>')
t('lista e numerada seguidas viram duas listas', h('- a\n1. b').count('<ul>') == 1 and h('- a\n1. b').count('<ol>') == 1)
t('título e citação', h('# Topo\n> dito') == '<p class="fmt-titulo fmt-t1">Topo</p><blockquote>dito</blockquote>', h('# Topo\n> dito'))
t('palavra_com_sublinhado e 2*3*4 não viram itálico', '<em>' not in h('snake_case_var e 2*3*4'))
t('quebra de linha do Windows', h('a\r\nb') == '<p>a<br>b</p>')
t('vazio', h('') == '' and h(None) == '')

print('\n== SEGURANÇA ==')
t('HTML digitado é escapado', h('<script>alert(1)</script>') == '<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>')
t('atributo não fecha aspas', '"' not in h('"onmouseover="x').replace('&quot;', ''))
t('link com javascript: não vira link', '<a' not in h('[x](javascript:alert(1))'))
saida = h('[site](https://ex.com/a?b=1&c=2)')
t('link http(s) com texto, em outra aba e sem referrer', saida == '<p><a href="https://ex.com/a?b=1&amp;c=2" target="_blank" rel="noopener noreferrer">site</a></p>', saida)
saida = h('veja https://ex.com/x.')
t('endereço solto vira link (o ponto final fica fora)', 'href="https://ex.com/x"' in saida and saida.endswith('</a>.</p>'), saida)
t('marcação dentro do link não quebra o endereço', 'href="https://ex.com/a_b_c"' in h('https://ex.com/a_b_c'))
t('código não interpreta marcação', h('`**x**`') == '<p><code>**x**</code></p>')

print('\n== RESUMO SEM MARCAÇÃO ==')
t('tira os sinais', sem_marcacao('# T\n- **a** e [b](https://x.com) ~~c~~') == 'T\na e b c', repr(sem_marcacao('# T\n- **a** e [b](https://x.com) ~~c~~')))

print('\n== FILTROS NO TEMPLATE ==')
tpl = Template('{% load impulso_tags %}{{ d|formatado }}|{{ d|sem_formatacao }}')
r = tpl.render(Context({'d': '**oi** <b>'}))
t('formatado sai como HTML seguro; sem_formatacao continua escapado', r == '<p><strong>oi</strong> &lt;b&gt;</p>|oi &lt;b&gt;', r)

print(f'\n{OK} OK / {FALHAS} falhas')
sys.exit(1 if FALHAS else 0)
