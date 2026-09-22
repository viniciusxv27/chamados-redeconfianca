"""Assinatura digital no tema escuro — contracheque e folha de ponto.

Pedido: "em /folha-ponto e /contracheque: Ajuste a parte de assinatura para o
tema escuro".

O quadro de assinatura é um <canvas> transparente com tinta escura (#1a1a2e).
No escuro, o `bg-white` do quadro virava superfície escura e a tinta sumia; a
prévia da assinatura já registrada (o mesmo PNG transparente) também ficava
escuro sobre escuro. A correção mantém o quadro e a prévia como folha clara no
tema escuro (classes `assinatura-quadro` e `assinatura-previa` em
static/css/tema-escuro.css) e NÃO mexe na tinta: a imagem que o canvas exporta
é a que vai para o PDF assinado, em papel branco.

Roda numa transação desfeita no fim, com cache em memória e PDFs em
InMemoryStorage (nada vai ao Redis nem ao MinIO). Assinar não dispara envio
nenhum (a view só grava a imagem e o hash).
"""
import json
import os
import re
import subprocess
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.files.storage import InMemoryStorage
from django.db import transaction
from django.test import Client
from django.test.utils import override_settings
from django.utils import timezone

from contracheque.models import Payslip
from folhaponto.models import FolhaPonto

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


def script_da_assinatura(html):
    """O último <script> do template: o do quadro (desenho + envio)."""
    inicio = html.rfind('<script>')
    return html[inicio:html.find('</script>', inicio)] if inicio >= 0 else ''


def no_head(caminho):
    """O arquivo como está no último commit (None se o git não responder)."""
    try:
        return subprocess.run(['git', 'show', f'HEAD:{caminho}'], cwd=BASE, capture_output=True,
                              text=True, check=True, timeout=30).stdout
    except Exception:
        return None


TEMPLATES = {
    'contracheque': 'templates/contracheque/payslip_detail.html',
    'folha de ponto': 'templates/folhaponto/folha_detail.html',
}
CLASSES_ORIGINAIS_QUADRO = 'border-2 border-dashed border-gray-300 rounded-lg bg-white relative'
CLASSES_ORIGINAIS_PREVIA = 'mt-3 bg-white rounded border border-emerald-200 p-2 inline-block'
# PNG 1x1 transparente: é o formato que o canvas manda (data:image/png;base64,...).
PNG = ('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4'
       '2mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')

print('== A FOLHA DO TEMA ==')
css = ler('static/css/tema-escuro.css')
sem_comentarios = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
t('chaves balanceadas (a folha não quebrou)', sem_comentarios.count('{') == sem_comentarios.count('}'),
  (sem_comentarios.count('{'), sem_comentarios.count('}')))
regras = re.findall(r'([^{}]+)\{([^}]*)\}', sem_comentarios)
da_assinatura = [(sel.strip(), corpo) for sel, corpo in regras if 'assinatura-' in sel]
t('existem regras para o quadro, a prévia e o cartão pendente',
  all(any(c in sel for sel, _ in da_assinatura) for c in ('.assinatura-quadro', '.assinatura-previa',
                                                           '.assinatura-pendente')), [s for s, _ in da_assinatura])
seletores = [s.strip() for sel, _ in da_assinatura for s in sel.split(',')]
t('toda regra nova vale só no escuro (o claro fica como estava)',
  seletores and all(s.startswith('html.dark ') for s in seletores), seletores)
papel = next((corpo for sel, corpo in da_assinatura
              if '.assinatura-quadro' in sel and '.assinatura-previa' in sel), '')
fundo = re.search(r'background-color:\s*(#[0-9a-fA-F]{6})', papel)
claro = fundo and sum(int(fundo.group(1)[i:i + 2], 16) for i in (1, 3, 5)) / 3 > 230
t('quadro e prévia ficam com fundo de papel claro no escuro', bool(claro), papel)
t('nada de filtro/inversão na tinta (a imagem exportada não pode mudar)',
  not any('filter' in corpo or 'invert' in corpo for _, corpo in da_assinatura))
t('a regra geral que deixa canvas e imagens sem filtro continua lá',
  'html.dark img, html.dark canvas, html.dark svg image { filter: none; }' in css)

print('\n== O QUE O CANVAS EXPORTA NÃO MUDOU ==')
for modulo, caminho in TEMPLATES.items():
    html = ler(caminho)
    script = script_da_assinatura(html)
    t(f'{modulo}: exporta o PNG do próprio canvas', script.count("canvas.toDataURL('image/png')") == 1)
    t(f'{modulo}: tinta escura fixa (#1a1a2e)', "ctx.strokeStyle = '#1a1a2e';" in script)
    t(f'{modulo}: o canvas não ganha fundo pintado nem depende do tema',
      not re.search(r'fillStyle|fillRect|matchMedia|dark|tema', script), re.findall(r'fillStyle|fillRect|matchMedia|dark|tema', script))
    t(f'{modulo}: o quadro guarda as classes de antes (claro idêntico) e ganha o gancho do escuro',
      f'class="assinatura-quadro {CLASSES_ORIGINAIS_QUADRO}" id="signature-wrapper"' in html)
    t(f'{modulo}: a prévia guarda as classes de antes e ganha o gancho do escuro',
      f'class="assinatura-previa {CLASSES_ORIGINAIS_PREVIA}"' in html)
    anterior = no_head(caminho)
    if anterior is None:
        print(f'  (git indisponível: comparação do script de {modulo} com o commit pulada)')
    else:
        t(f'{modulo}: o <script> do quadro é idêntico ao do último commit',
          script_da_assinatura(anterior) == script)

memoria = InMemoryStorage()
marcador = transaction.atomic()
marcador.__enter__()
try:
    with override_settings(CACHES={
        'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-assin-tema'},
        'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-assin-tema-local'},
    }), mock.patch.object(Payslip._meta.get_field('pdf_file'), 'storage', memoria), \
            mock.patch.object(FolhaPonto._meta.get_field('pdf_file'), 'storage', memoria):
        agora = timezone.now()
        clientes = {}
        docs = {}
        for tema in ('dark', 'light'):
            dono = User.objects.create_user(
                username=f'zzate.{tema}', email=f'zzate.{tema}@exemplo-teste.local', password='S3nha!teste',
                first_name='ZZAte', last_name=tema.title(), hierarchy='PADRAO', theme=tema)
            pendente = Payslip.objects.create(user=dono, month=7, year=2026, pdf_file='zz/cc-p.pdf')
            assinado = Payslip.objects.create(user=dono, month=6, year=2026, pdf_file='zz/cc-a.pdf',
                                              signed_at=agora, signature_image=PNG, signature_ip='10.0.0.7',
                                              signature_hash='ab' * 32)
            # As duas mensais de propósito: sem o override, a mais recente seria semanal (sem assinatura).
            f_pendente = FolhaPonto.objects.create(user=dono, month=7, year=2026, pdf_file='zz/fp-p.pdf',
                                                   periodicity_override='mensal')
            f_assinada = FolhaPonto.objects.create(user=dono, month=6, year=2026, pdf_file='zz/fp-a.pdf',
                                                   periodicity_override='mensal', signed_at=agora,
                                                   signature_image=PNG, signature_ip='10.0.0.7',
                                                   signature_hash='cd' * 32)
            c = Client()
            c.force_login(dono)
            clientes[tema] = c
            docs[tema] = {
                'contracheque': (f'/contracheque/{pendente.pk}/', f'/contracheque/{assinado.pk}/'),
                'folha de ponto': (f'/folha-ponto/{f_pendente.pk}/', f'/folha-ponto/{f_assinada.pk}/'),
                'cc_pendente': pendente, 'fp_pendente': f_pendente,
            }

        for tema, rotulo in (('dark', 'escuro'), ('light', 'claro')):
            print(f'\n== AS TELAS NO {rotulo.upper()} ==')
            c = clientes[tema]
            for modulo in TEMPLATES:
                url_pendente, url_assinado = docs[tema][modulo]
                r = c.get(url_pendente)
                html = r.content.decode()
                t(f'{modulo} pendente: abre (200)', r.status_code == 200, r.status_code)
                if tema == 'dark':
                    t(f'{modulo} pendente: <html class="dark"> e a folha do tema carregada',
                      '<html lang="pt-br" class="dark">' in html and 'css/tema-escuro.css' in html)
                else:
                    t(f'{modulo} pendente: sem classe dark', 'class="dark"' not in html)
                t(f'{modulo} pendente: quadro com o gancho do escuro e o canvas dentro',
                  f'class="assinatura-quadro {CLASSES_ORIGINAIS_QUADRO}" id="signature-wrapper"' in html
                  and 'id="signature-canvas"' in html and 'Desenhe sua assinatura aqui' in html)
                t(f'{modulo} pendente: o envio continua mandando o PNG do canvas',
                  "canvas.toDataURL('image/png')" in html and "ctx.strokeStyle = '#1a1a2e';" in html)

                r = c.get(url_assinado)
                html = r.content.decode()
                t(f'{modulo} assinado: abre (200)', r.status_code == 200, r.status_code)
                t(f'{modulo} assinado: prévia sobre o papel, com a imagem gravada intacta',
                  f'class="assinatura-previa {CLASSES_ORIGINAIS_PREVIA}"' in html
                  and f'<img src="{PNG}" alt="Assinatura"' in html)
                t(f'{modulo} assinado: sem quadro de desenho', 'id="signature-canvas"' not in html)

            html = c.get('/contracheque/').content.decode()
            t('lista de contracheques: só o pendente leva o contorno de pendência',
              html.count('ring-1 ring-red-200 assinatura-pendente') == 1, html.count('assinatura-pendente'))

        print('\n== ASSINAR NO ESCURO GRAVA EXATAMENTE O QUE O CANVAS MANDOU ==')
        c = clientes['dark']
        pendente = docs['dark']['cc_pendente']
        r = c.post(f'/contracheque/api/assinar/{pendente.pk}/', data=json.dumps({'signature': PNG}),
                   content_type='application/json')
        pendente.refresh_from_db()
        t('contracheque assinado (200) com a imagem byte a byte', r.status_code == 200
          and pendente.is_signed and pendente.signature_image == PNG, (r.status_code, r.content[:120]))
        f_pendente = docs['dark']['fp_pendente']
        r = c.post(f'/folha-ponto/api/assinar/{f_pendente.pk}/', data=json.dumps({'signature': PNG}),
                   content_type='application/json')
        f_pendente.refresh_from_db()
        t('folha de ponto assinada (200) com a imagem byte a byte', r.status_code == 200
          and f_pendente.is_signed and f_pendente.signature_image == PNG, (r.status_code, r.content[:120]))
        html = c.get(f'/contracheque/{pendente.pk}/').content.decode()
        t('depois de assinar, a tela mostra a prévia no papel', 'assinatura-previa' in html
          and 'id="signature-canvas"' not in html)

        print('\n== TELAS DE ADMINISTRAÇÃO NO ESCURO ==')
        admin = User.objects.create_user(
            username='zzate.admin', email='zzate.admin@exemplo-teste.local', password='S3nha!teste',
            first_name='ZZAteAdmin', last_name='Admin', hierarchy='SUPERADMIN', theme='dark')
        ca = Client()
        ca.force_login(admin)
        for url in ('/contracheque/admin/?q=ZZAte', '/folha-ponto/admin/?q=ZZAte'):
            r = ca.get(url)
            html = r.content.decode()
            t(f'{url}: abre no escuro com as etiquetas de assinatura', r.status_code == 200
              and 'class="dark"' in html and 'Assinado' in html and 'Pendente' in html, r.status_code)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
