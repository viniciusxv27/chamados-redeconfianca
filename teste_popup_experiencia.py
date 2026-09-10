"""Home: o painel de janela de experiência aparece para quem deve — inclusive no app.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
from datetime import timedelta

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
from django.utils import timezone

from communications.views import (SETORES_PAINEL_EXPERIENCIA,
                                  _ve_painel_de_experiencia)
from users.models import Sector

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


marcador = transaction.atomic()
marcador.__enter__()
try:
    outro = Sector.objects.create(name='ZZ Setor qualquer')
    dp = Sector.objects.get(id=SETORES_PAINEL_EXPERIENCIA[3])   # Departamento Pessoal

    def novo(u, setor, hierarquia='PADRAO', **kw):
        return User.objects.create_user(
            username=u, email=f'{u}@exemplo-teste.local', password='S3nha!teste',
            sector=setor, first_name=u.split('.')[1].title(), last_name='T',
            hierarchy=hierarquia, **kw)

    # Alguém em janela, para o painel ter conteúdo.
    novato = novo('je.novato', outro, admission_date=timezone.localdate() - timedelta(days=20),
                  has_experience_window=True)

    print('== QUEM VÊ ==')
    do_dp = novo('je.dp', dp)
    t('gente do Departamento Pessoal vê', _ve_painel_de_experiencia(do_dp))
    adm_fora = novo('je.adm', outro, 'ADMIN')
    t('ADMINISTRAÇÃO lotada em outro setor também vê (administra pessoal)',
      _ve_painel_de_experiencia(adm_fora))
    vinculado = novo('je.vinc', outro)
    vinculado.sectors.add(dp)
    t('setor vinculado (não só o principal) conta', _ve_painel_de_experiencia(vinculado))
    comum = novo('je.comum', outro)
    t('PADRÃO de outro setor não vê', not _ve_painel_de_experiencia(comum))
    t('ninguém anônimo', not _ve_painel_de_experiencia(None))

    print('\n== A HOME ==')
    c = Client(); c.force_login(do_dp)
    r = c.get('/')
    html = r.content.decode()
    t('a home abre', r.status_code == 200, r.status_code)
    t('o modal está na página', 'id="experience-window-modal"' in html)
    t('com a pessoa em janela', 'Novato T' in html)
    # O app do Windows é o PWA (display-mode standalone). A detecção do WebView
    # móvel não pode olhar isso — foi exatamente o que escondia o popup no Windows.
    ini = html.index('function rcWebViewMovel()')
    fim = html.index('if (experienceModal) {', ini)
    detector = html[ini:fim]
    t('a detecção do WebView móvel existe', 'rcWebViewMovel' in html)
    t('e NÃO olha o display-mode standalone (app do Windows)', 'standalone' not in detector)
    t('bloqueia o WebView do Android (user-agent "wv")', 'wv' in detector and 'Android' in detector)
    t('e o WebView do iOS (iPhone/iPad sem Safari)', 'iPhone' in detector and 'Safari' in detector)
    t('fora do WebView móvel, abre de fato', 'openExperienceModal();' in html)
    t('no WebView móvel some sem bloquear (só hidden, sem fundo escuro)',
      "if (rcWebViewMovel()) {" in html and "experienceModal.classList.add('hidden');" in html)
    t('fecha pelo X, pelo Entendi, pelo fundo e pelo Esc',
      "experienceClose.addEventListener" in html and "experienceOk.addEventListener" in html
      and "experienceBackdrop.addEventListener" in html and "e.key === 'Escape'" in html)
    t('o rAF só liga a animação (à prova de falha)',
      "experienceModal.classList.remove('hidden');" in html
      and "requestAnimationFrame(function () {\n            experienceModal.classList.add('rc-anima');" in html)

    c2 = Client(); c2.force_login(comum)
    html = c2.get('/').content.decode()
    # O CSS do modal fica no <style> da página sempre; o que não pode ir é o elemento.
    t('para quem não deve ver, o modal nem vai na página', 'id="experience-window-modal"' not in html)

    print('\n== SEM LIXO NO LOG ==')
    import io, contextlib
    from communications.views import _get_experience_window_alerts_for_dp
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _get_experience_window_alerts_for_dp()
    t('a função de alertas não imprime DEBUG', 'DEBUG' not in buf.getvalue())

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
