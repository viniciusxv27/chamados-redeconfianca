"""Tema escuro na agenda e nas transcrições.

Pedido: ajustar o tema escuro em /agenda, /agenda/transcricoes/, a nova
transcrição e o detalhe. As molduras dessas telas são classes próprias com
degradê quase branco; no escuro sobravam retângulos brancos com texto claro
por cima, linhas brancas nos modais e hovers que apagavam o texto.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import re
import sys

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

from agenda.models import MeetingTranscription

User = get_user_model()
BASE = os.path.dirname(os.path.abspath(__file__))
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def ler(caminho):
    with open(os.path.join(BASE, caminho), encoding='utf-8') as fh:
        return fh.read()


print('== A FOLHA DO TEMA ==')
css = ler('static/css/tema-escuro.css')
sem_comentarios = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
t('chaves balanceadas (a folha não quebrou)', sem_comentarios.count('{') == sem_comentarios.count('}'),
  (sem_comentarios.count('{'), sem_comentarios.count('}')))
for trecho, porque in (
    ('.agenda-shell', 'moldura da agenda'),
    ('.trans-list-shell', 'moldura da lista de transcrições'),
    ('.trans-detail-shell', 'moldura do detalhe'),
    ('.trans-shell', 'moldura da nova transcrição'),
    ('.trans-card', 'cartões da nova transcrição'),
    ('.trans-panel', 'painéis do detalhe'),
    ('.mode-card-active', 'método de entrada escolhido (texto branco sobre branco)'),
    ('.role-pill', 'pílulas de papel'),
    ('.status-completed', 'etiquetas de status da lista'),
    ('.status-recording', 'etiqueta "gravando"'),
    (r'.hover\:bg-orange-50:hover', 'hover pastel que apagava o texto'),
    (r'.hover\:bg-orange-200:hover', 'hover do botão Adicionar papel'),
    (r'.hover\:text-gray-600:hover', 'X de fechar dos modais'),
    ('[class*="hover:bg-orange-50/"]:hover', 'hover das seções do detalhe'),
    (':where(html.dark) :is(', 'linhas brancas de border-b/border-t sem cor'),
    ('[class*="divide-"]', 'divisórias pastel'),
    ('.text-orange-900', 'título marrom sobre fundo escuro'),
    ('.from-rose-50', 'cabeçalho de riscos em degradê rosa'),
    ('.from-orange-200', 'ícone do resumo e avatares'),
    ('.fc .fc-daygrid-dot-event:hover', 'hover dos eventos do calendário'),
    ('.color-option.selected', 'cor escolhida no modal'),
    ('.trans-header [class*="bg-white/"]', 'chips do cabeçalho laranja'),
    ('.trans-header .bg-white', 'botão Compartilhar'),
    ('.trans-header .bg-gray-300', 'etiqueta "gravando" no cabeçalho'),
):
    t(f'regra para {porque}', trecho in css, trecho)
t('o cinza 300 não foi remapeado no portal inteiro (usado com outro papel em 20+ telas)',
  'html.dark .bg-gray-300 {' not in css)
t('borda com cor explícita continua vencendo (a regra geral tem especificidade de elemento)',
  ':where(html.dark) :is(div' in css)

print('\n== OS TEMPLATES ==')
t('calendar.html não pede mais o CSS inexistente do FullCalendar (404)',
  'index.global.min.css' not in ler('templates/agenda/calendar.html'))

marcador = transaction.atomic()
marcador.__enter__()
try:
    escuro = User.objects.create_user(username='zzte.escuro', email='zzte.escuro@exemplo-teste.local',
                                      password='S3nha!teste', first_name='ZZ', last_name='Escuro', theme='dark')
    claro = User.objects.create_user(username='zzte.claro', email='zzte.claro@exemplo-teste.local',
                                     password='S3nha!teste', first_name='ZZ', last_name='Claro', theme='light')
    concluida = MeetingTranscription.objects.create(owner=escuro, title='ZZTE Concluída', status='completed',
                                                    summary='Resumo', formatted_transcription='Texto')
    gravando = MeetingTranscription.objects.create(owner=escuro, title='ZZTE Gravando', status='recording')

    ce = Client()
    ce.force_login(escuro)
    cl = Client()
    cl.force_login(claro)

    paginas = ['/agenda/', '/agenda/transcricoes/', '/agenda/transcricoes/nova/',
               f'/agenda/transcricoes/{concluida.pk}/', f'/agenda/transcricoes/{gravando.pk}/']
    for url in paginas:
        r = ce.get(url)
        html = r.content.decode()
        t(f'{url}: abre no escuro (200)', r.status_code == 200, r.status_code)
        t(f'{url}: <html class="dark">', '<html lang="pt-br" class="dark">' in html)
        t(f'{url}: carrega a folha do tema', 'css/tema-escuro.css' in html)
        t(f'{url}: sem comentário de template vazando', '{#' not in html and '{% comment' not in html)
    r = cl.get('/agenda/')
    t('no claro, nada de classe dark', r.status_code == 200 and 'class="dark"' not in r.content.decode())
    html = ce.get('/agenda/').content.decode()
    t('a agenda usa a moldura coberta pelo tema', 'agenda-shell' in html)
    html = ce.get('/agenda/transcricoes/').content.decode()
    t('a lista usa as classes cobertas pelo tema', 'trans-list-shell' in html and 'status-pill' in html)
    html = ce.get(f'/agenda/transcricoes/{concluida.pk}/').content.decode()
    t('o detalhe usa as classes cobertas pelo tema', 'trans-detail-shell' in html and 'trans-header' in html)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
