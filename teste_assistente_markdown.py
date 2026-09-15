"""Assistente: as respostas aparecem formatadas (Markdown) — e seguras.

Pedido: ajustar a formatação da mensagem, os *, o markdown em geral, para
mostrar de forma agradável. O texto do Claude passa por static/js/rc-markdown.js,
que escapa tudo antes de formatar: HTML escrito pelo modelo (ou por uma pauta
que ele leu) nunca vira tag.

O renderizador roda de verdade no node; a tela é conferida com o Client do Django.
Roda dentro de uma transação desfeita no fim.
"""
import json
import os
import shutil
import subprocess
import sys

import django

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from assistente import claude_client
from assistente.models import AssistenteMensagem

User = get_user_model()
NODE = shutil.which('node')
JS = os.path.join(BASE, 'static', 'js', 'rc-markdown.js')
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def renderizar(textos):
    with open(JS, encoding='utf-8') as fh:
        codigo = fh.read()
    script = ("globalThis.location = {origin: 'https://portal.exemplo'};\n" + codigo +
              '\nconst entradas = ' + json.dumps(textos) + ';\n'
              'process.stdout.write(JSON.stringify(entradas.map((x) => globalThis.RCMarkdown.render(x))));')
    r = subprocess.run([NODE, '-e', script], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-800:])
    return json.loads(r.stdout)


CASOS = {
    'negrito': '**Atenção:** faltam *2* dias',
    'html': '<script>alert(1)</script> <img src=x onerror=alert(1)>',
    'lista': '- um\n- dois\n  - sub\n- três',
    'numerada': '1. primeiro\n\n2. segundo',
    'tabela': '| Loja | Meta |\n|---|---:|\n| Centro | 10 |\n| Norte | 20 |',
    'link_interno': '[Abrir a meta](/impulso/metas/3/)',
    'link_js': '[clique](javascript:alert(1))',
    'url_solta': 'veja https://exemplo.com/a_b_c.',
    'aspas_na_url': '[x](https://exemplo.com/"onmouseover="alert(1))',
    'codigo': 'use `**sem negrito**` aqui',
    'bloco': '```\n<b>x</b>\n```',
    'titulo': '### Resumo\ntexto',
    'citacao': '> citação',
    'quebra': 'linha 1\nlinha 2',
    'regua': '---',
    'snake': 'o campo nota_qualidade fica',
}

print('== O RENDERIZADOR (node) ==')
if not NODE:
    t('node disponível para rodar o renderizador', False, 'node não encontrado')
else:
    saida = dict(zip(CASOS, renderizar(list(CASOS.values()))))
    t('negrito e itálico', saida['negrito'] == '<p><strong>Atenção:</strong> faltam <em>2</em> dias</p>', saida['negrito'])
    t('HTML do texto não vira tag', '<script' not in saida['html'] and '<img' not in saida['html']
      and '&lt;script&gt;' in saida['html'], saida['html'])
    t('lista com subitem', saida['lista'] == '<ul><li>um</li><li>dois<ul><li>sub</li></ul></li><li>três</li></ul>', saida['lista'])
    t('lista numerada separada por linha em branco continua a contagem',
      saida['numerada'] == '<ol><li>primeiro</li></ol><ol start="2"><li>segundo</li></ol>', saida['numerada'])
    t('tabela com cabeçalho e alinhamento', '<table>' in saida['tabela'] and '<th>Loja</th>' in saida['tabela']
      and '<td style="text-align:right">20</td>' in saida['tabela'] and 'rc-md-tabela' in saida['tabela'], saida['tabela'])
    t('link do portal abre na mesma aba', saida['link_interno'] == '<p><a href="/impulso/metas/3/">Abrir a meta</a></p>',
      saida['link_interno'])
    t('link javascript: não vira link', 'href' not in saida['link_js'], saida['link_js'])
    t('URL solta vira link externo, sem levar o ponto final nem virar itálico',
      saida['url_solta'] == '<p>veja <a href="https://exemplo.com/a_b_c" target="_blank" '
                            'rel="noopener noreferrer">https://exemplo.com/a_b_c</a>.</p>', saida['url_solta'])
    t('aspas numa URL não fogem do atributo', 'onmouseover="' not in saida['aspas_na_url'], saida['aspas_na_url'])
    t('código em linha não é interpretado', '<code>**sem negrito**</code>' in saida['codigo'], saida['codigo'])
    t('bloco de código escapado', saida['bloco'] == '<pre><code>&lt;b&gt;x&lt;/b&gt;</code></pre>', saida['bloco'])
    t('título discreto para a bolha', saida['titulo'] == '<h5>Resumo</h5><p>texto</p>', saida['titulo'])
    t('citação', saida['citacao'] == '<blockquote><p>citação</p></blockquote>', saida['citacao'])
    t('quebra de linha dentro do parágrafo', saida['quebra'] == '<p>linha 1<br>linha 2</p>', saida['quebra'])
    t('régua', saida['regua'] == '<hr>', saida['regua'])
    t('nome_com_sublinhado não vira itálico', '<em>' not in saida['snake'], saida['snake'])

print('\n== A TELA DO CHAT ==')
t('o SYSTEM pede Markdown simples', 'Markdown simples' in claude_client.SYSTEM)
marcador = transaction.atomic()
marcador.__enter__()
try:
    pessoa = User.objects.create_user(username='zzmd.pessoa', email='zzmd.pessoa@exemplo-teste.local',
                                      password='S3nha!teste', first_name='ZZ', last_name='Markdown')
    AssistenteMensagem.objects.create(user=pessoa, papel='user', conteudo='quais **metas**?')
    AssistenteMensagem.objects.create(user=pessoa, papel='assistant', conteudo='### Metas\n- **Vendas** <b>x</b>')
    c = Client()
    c.force_login(pessoa)
    r = c.get('/assistente/')
    html = r.content.decode()
    t('abre (200)', r.status_code == 200, r.status_code)
    t('carrega o renderizador', 'js/rc-markdown.js' in html)
    t('a resposta do histórico é marcada para formatar', 'data-markdown>### Metas' in html)
    t('e chega escapada até o JavaScript formatar', '&lt;b&gt;x&lt;/b&gt;' in html and '<b>x</b>' not in html)
    t('a fala do usuário continua texto puro', 'whitespace-pre-wrap bg-primary text-white">quais **metas**?' in html)
    t('respostas novas também passam pelo renderizador', 'window.RCMarkdown.render(texto)' in html)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
