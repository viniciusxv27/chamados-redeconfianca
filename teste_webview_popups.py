"""Popup travando o app, comentário vazado no quadro de cursos e envio do comprovante.

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
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.template.loader import get_template, render_to_string
from django.test import Client

from cursos.models import ConfiguracaoCursos, Comprovante, Curso

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


def ler(caminho):
    with open(caminho, encoding='utf-8') as f:
        return f.read()


marcador = transaction.atomic()
marcador.__enter__()
try:
    print('== COMENTÁRIO VAZANDO NA TELA ==')
    # A regex do Django é r"({%.*?%}|{{.*?}}|{#.*?#})" SEM re.DOTALL: um {# #}
    # que abre numa linha e fecha noutra não é reconhecido e sai impresso.
    vazando = []
    for raiz, _, arquivos in os.walk('templates'):
        for nome in arquivos:
            if not nome.endswith('.html'):
                continue
            caminho = os.path.join(raiz, nome)
            texto = ler(caminho)
            for m in re.finditer(r'\{#', texto):
                linha = texto[m.start():].split('\n', 1)[0]
                if '#}' not in linha:
                    vazando.append(f'{caminho}:{texto[:m.start()].count(chr(10)) + 1}')
    t('nenhum {# #} atravessa linha em templates/', not vazando, vazando[:5])

    row = ler('templates/cursos/_pessoa_row.html')
    t('a linha do quadro usa {% comment %}', '{% comment %}' in row)
    # A linha sozinha não renderiza fora de contexto (filtro com argumento que
    # falta levanta VariableDoesNotExist), então confere na tela de verdade.
    chefe = User.objects.filter(is_superuser=True, is_active=True).first()
    cg = Client()
    cg.force_login(chefe)
    quadro = cg.get('/cursos/gestao/')
    t('o quadro de gestão abre', quadro.status_code == 200, quadro.status_code)
    t('e o texto do comentário não aparece na tela',
      'Linha de uma pessoa no quadro' not in quadro.content.decode())

    print('\n== POPUP NÃO PODE DEPENDER DE UM QUADRO ==')
    home = ler('templates/home.html')
    base = ler('templates/base.html')

    t('o estilo do popup saiu do extra_head (que o base.html não renderiza)',
      home.index('{% block extra_css %}') < home.index('<style>'))
    t('e o base.html realmente renderiza esse bloco', '{% block extra_css %}' in base)

    t('o popup não nasce mais em opacity: 0',
      '#experience-window-modal {\n        opacity: 0;' not in home)
    t('a animação de entrada só vale com .rc-anima',
      '#experience-window-modal.rc-anima:not(.hidden)' in home)
    t('abrir é só tirar o hidden',
      "experienceModal.classList.remove('hidden');" in home)
    t('o rAF passou a ligar a animação, não a visibilidade',
      "experienceModal.classList.add('rc-anima')" in home
      and "classList.add('is-visible')" not in home)
    t('fechar solta o toque na hora (pointer-events fora do timer)',
      '#experience-window-modal.esta-fechando' in home
      and 'pointer-events: none;' in home)
    t('o mesmo vale para o popup de trilhas',
      "mandatoryTrailsModal.classList.add('rc-anima')" in home
      and '#mandatory-trails-modal.esta-fechando' in home)

    print('\n== O CONTEÚDO DA PÁGINA NUNCA NASCE INVISÍVEL ==')
    css = ler('static/css/transicoes.css')
    js = ler('static/js/transicoes.js')
    corpo_css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    t('não há animação nenhuma no #rc-conteudo', '#rc-conteudo' not in corpo_css,
      [l for l in corpo_css.splitlines() if '#rc-conteudo' in l])
    t('e o JS não marca mais rc-fallback', 'rc-fallback' not in js)
    t('a barrinha continua sem capturar toque', 'pointer-events: none;' in css)
    t('a transição entre documentos continua ligada',
      '@view-transition { navigation: auto; }' in css)
    t('as chaves do CSS estão balanceadas', css.count('{') == css.count('}'))

    print('\n== SISTEMA COMPARTILHADO .rc-modal (outros 4 popups) ==')
    custom = ler('static/css/custom.css')
    t('.rc-modal não nasce em opacity: 0',
      '.rc-modal {\n    opacity: 0;' not in custom)
    t('a entrada é animação e só com .rc-anima',
      '.rc-modal.rc-anima:not(.hidden)' in custom)
    t('fechando, para de bloquear a tela na hora',
      '.rc-modal.esta-fechando' in custom and 'pointer-events: none;' in custom)

    for arq in ['templates/portal_popups/_popup_gate.html',
                'templates/users/_photo_request_popup.html',
                'templates/prizes/_pickup_popup.html',
                'templates/feedback/_reminder_popup.html']:
        texto = ler(arq)
        curto = arq.split('/')[-1]
        t(f'{curto}: abre sem depender do rAF',
          "classList.add('is-open')" not in texto)
        t(f'{curto}: o rAF só liga a animação',
          "classList.add('rc-anima')" in texto)
        t(f'{curto}: fechar solta o toque',
          "classList.add('esta-fechando')" in texto)

    print('\n== O CELULAR PRECISA RECEBER A CORREÇÃO ==')
    sw = ler('sw.js')
    t('o custom.css é pré-cacheado cache-first', "'/static/css/custom.css'" in sw)
    t('por isso o nome do cache mudou (senão o celular fica no CSS velho)',
      "CACHE_NAME = 'rede-confianca-v3'" in sw)
    t('HTML continua network-first', 'Network-first para páginas HTML' in sw)

    print('\n== COMPROVANTE: ENVIO INTERROMPIDO ==')
    get_template('400.html')
    pagina400 = render_to_string('400.html')
    t('existe uma tela de 400 em português', 'O envio não chegou inteiro' in pagina400)
    t('e ela explica o que fazer', 'Wi-Fi' in pagina400)

    encolhedor = ler('static/js/upload-imagem.js')
    t('o encolhedor só mexe em imagem que o canvas abre',
      "image\\/(jpeg|png|webp)" in encolhedor or 'image\\/(jpeg|png|webp)' in encolhedor)
    t('arquivo pequeno passa direto', 'A_PARTIR_DE' in encolhedor)
    t('erro ao decodificar segue com o original (HEIC do iPhone)',
      'img.onerror' in encolhedor and 'resolve(arquivo)' in encolhedor)
    t('tem relógio de segurança se a decodificação travar', 'setTimeout' in encolhedor)
    t('não repete a submissão', "dataset.encolhido === '1'" in encolhedor)

    cfg = ConfiguracaoCursos.get()
    curso = Curso.objects.filter(publicado=True).order_by('-id').first()
    t('há curso publicado para testar', curso is not None)

    if curso:
        alvo = next((u for u in User.objects.filter(is_active=True)[:800]
                     if curso.alcanca(u, cfg)), None)
        t('e alguém que ele alcança', alvo is not None)

        if alvo:
            c = Client()
            c.force_login(alvo)
            html = c.get('/cursos/').content.decode()
            t('o formulário pede o encolhimento', 'data-encolher-imagem' in html)
            t('o script vai junto', 'upload-imagem.js' in html)
            t('e continua multipart', 'multipart/form-data' in html)

            # Os dois caminhos de recusa não encostam no storage: dá para
            # testar sem sujar o MinIO.
            antes = Comprovante.objects.count()
            r = c.post(f'/cursos/{curso.id}/comprovante/',
                       {'arquivo': SimpleUploadedFile('virus.exe', b'x' * 10,
                                                      'application/octet-stream')},
                       follow=True)
            t('formato não aceito é recusado', Comprovante.objects.count() == antes)
            t('e a tela explica', 'Formato não aceito' in r.content.decode())

            r = c.post(f'/cursos/{curso.id}/comprovante/', {}, follow=True)
            t('sem arquivo, recusa', Comprovante.objects.count() == antes)
            t('e pede o anexo', 'Anexe o comprovante' in r.content.decode())

            r = c.get(f'/cursos/{curso.id}/comprovante/')
            t('GET não grava nada (405)', r.status_code == 405, r.status_code)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
