"""Impulso → detalhe da meta: "Cole a imagem aqui" no card de Anexos.

A pessoa clica no campo e dá Ctrl+V: a imagem copiada (print de tela, "copiar
imagem") sobe sozinha como arquivo anexado, e a lista se atualiza sem recarregar
a página. Só entra imagem, com limite de tamanho, e valem as permissões de
sempre. O formulário antigo (link ou arquivo) continua igual.

Os arquivos vão para uma pasta temporária (nada sobe para o storage de verdade)
e o teste roda dentro de uma transação desfeita no fim.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client
from django.utils import timezone

from communications.models import CommunicationGroup
from impulso import views as impulso_views
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, Meta, MetaAnexo
from users.models import Sector

User = get_user_model()
NODE = shutil.which('node')
ok = fail = 0
# Um PNG de 1x1 pixel de verdade.
PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
       b'\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82')
XHR = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def sintaxe_ok(codigo):
    if not NODE:
        return True, 'node ausente'
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as fh:
        fh.write(codigo)
        caminho = fh.name
    try:
        r = subprocess.run([NODE, '--check', caminho], capture_output=True, text=True)
        return r.returncode == 0, r.stderr[-800:]
    finally:
        os.unlink(caminho)


def imagem(nome='imagem-colada-20260911-101500.png', dados=PNG, tipo='image/png'):
    return SimpleUploadedFile(nome, dados, content_type=tipo)


pasta = tempfile.mkdtemp(prefix='zz-colar-imagem-')
campo_arquivo = MetaAnexo._meta.get_field('arquivo')
marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(campo_arquivo, 'storage', FileSystemStorage(location=pasta, base_url='/media-teste/')):
        adm = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
        ges = CommunicationGroup.objects.filter(name__iexact=GRUPO_GESTOR).first()
        assert adm and ges, 'grupos do Impulso não encontrados'
        area = Sector.objects.create(name='ZZ Area Colar Imagem')
        outra = Sector.objects.create(name='ZZ Outra Area Colar Imagem')

        def novo(apelido, setor, grupos):
            u = User.objects.create_user(
                username=f'zzcol.{apelido}', email=f'zzcol.{apelido}@exemplo-teste.local',
                password='S3nha!teste', first_name='ZZ', last_name=apelido.title(), sector=setor)
            for g in grupos:
                u.communication_groups.add(g)
            return u

        gestor = novo('gestor', area, [adm, ges])
        colab = novo('colab', area, [adm])
        estranho = novo('estranho', outra, [adm])
        meta = Meta.objects.create(
            titulo='ZZ Meta com print', descricao='x', colaborador=colab, gestor=gestor,
            prazo=timezone.localdate() + timedelta(days=5), aprovacao=Meta.Aprovacao.APROVADA,
            created_by=gestor)

        c_colab, c_estranho = Client(), Client()
        c_colab.force_login(colab)
        c_estranho.force_login(estranho)
        DETALHE = f'/impulso/metas/{meta.id}/'
        ANEXO = f'/impulso/metas/{meta.id}/anexo/'

        print('== O CAMPO NA TELA ==')
        r = c_colab.get(DETALHE)
        html = r.content.decode()
        t('o detalhe abre (200)', r.status_code == 200, r.status_code)
        t('o card de Anexos tem o "Cole a imagem aqui"', 'id="impColarImagem"' in html and 'Cole a imagem aqui' in html)
        t('que envia para o anexo desta meta', f'data-url="{ANEXO}"' in html)
        t('explica o Ctrl+V', 'Ctrl+V' in html)
        t('a lista de anexos pode ser trocada sem recarregar', 'id="impListaAnexos"' in html)
        t('o formulário antigo continua (link ou arquivo)', 'placeholder="Cole um link…"' in html and 'name="arquivo"' in html)
        t('sem comentário de template vazando', '{#' not in html and '{% comment' not in html)
        blocos = [b for b in re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, flags=re.S)
                  if 'impColarImagem' in b]
        t('um script cuida da colagem', len(blocos) == 1, len(blocos))
        if blocos:
            valido, erro = sintaxe_ok(blocos[0])
            t('o JavaScript da colagem tem sintaxe válida (node --check)', valido, erro)
            t('só aceita imagem da área de transferência', "item.kind === 'file'" in blocos[0] and 'image/png' in blocos[0])
        editar = [b for b in re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, flags=re.S)
                  if 'imp-anexo-editar' in b]
        t('editar anexo funciona também na lista trocada (clique delegado)',
          bool(editar) and "closest('.imp-anexo-editar')" in editar[0])
        if editar:
            valido, erro = sintaxe_ok(editar[0])
            t('e esse script continua com sintaxe válida', valido, erro)

        print('\n== COLAR E SUBIR ==')
        r = c_colab.post(ANEXO, {'arquivo': imagem(), 'colada': '1'}, **XHR)
        j = r.json() if r.status_code < 500 else {}
        anexo = MetaAnexo.objects.filter(meta=meta).order_by('-id').first()
        t('sobe por fetch e responde JSON', r.status_code == 200 and j.get('ok') is True, r.content[:300])
        t('vira anexo de arquivo da meta', anexo is not None and anexo.tipo == MetaAnexo.Tipo.ARQUIVO and bool(anexo.arquivo))
        t('com quem colou', anexo is not None and anexo.enviado_por_id == colab.id)
        t('com um nome legível na lista (não o nome aleatório do arquivo)',
          anexo is not None and anexo.titulo.startswith('Imagem colada em') and j.get('nome') == anexo.titulo,
          anexo.titulo if anexo else '')
        t('o arquivo foi gravado', anexo is not None and os.path.exists(os.path.join(pasta, anexo.arquivo.name)))
        html = c_colab.get(DETALHE).content.decode()
        lista = html.split('id="impListaAnexos"', 1)[1].split('</ul>', 1)[0] if 'id="impListaAnexos"' in html else ''
        t('a lista atualizada mostra a imagem colada', 'Imagem colada em' in lista)

        antes = MetaAnexo.objects.count()
        r = c_colab.post(ANEXO, {'arquivo': imagem('anotacao.txt', b'so texto', 'text/plain'), 'colada': '1'}, **XHR)
        t('texto no lugar de imagem: recusado (400)', r.status_code == 400 and 'só entra imagem' in r.json().get('error', ''),
          r.content[:200])
        r = c_colab.post(ANEXO, {'arquivo': imagem('falsa.png', b'x', 'application/octet-stream'), 'colada': '1'}, **XHR)
        t('extensão de imagem sem ser imagem: recusado', r.status_code == 400)
        with mock.patch.object(impulso_views, 'COLAR_MAX_BYTES', 10):
            r = c_colab.post(ANEXO, {'arquivo': imagem(), 'colada': '1'}, **XHR)
        t('imagem grande demais: recusada', r.status_code == 400 and '10 MB' in r.json().get('error', ''))
        r = c_colab.post(ANEXO, {'colada': '1'}, **XHR)
        t('colagem sem imagem: recusada', r.status_code == 400 and 'Nenhuma imagem' in r.json().get('error', ''))
        r = c_estranho.post(ANEXO, {'arquivo': imagem(), 'colada': '1'}, **XHR)
        t('quem não vê a meta não anexa (403)', r.status_code == 403, r.status_code)
        t('nada foi criado nas recusas', MetaAnexo.objects.count() == antes)
        r = Client().post(ANEXO, {'arquivo': imagem(), 'colada': '1'}, **XHR)
        t('sem login não anexa', r.status_code in (302, 401, 403) and MetaAnexo.objects.count() == antes, r.status_code)

        print('\n== O FORMULÁRIO DE SEMPRE CONTINUA IGUAL ==')
        r = c_colab.post(ANEXO, {'titulo': 'ZZ Contrato', 'arquivo': SimpleUploadedFile(
            'contrato.pdf', b'%PDF-1.4 zz', content_type='application/pdf')}, follow=True)
        t('anexar arquivo pelo formulário volta para o detalhe com o aviso', bool(r.redirect_chain)
          and r.redirect_chain[-1][0] == DETALHE and 'Arquivo anexado.' in r.content.decode(), r.redirect_chain)
        t('e não passa pela regra de "só imagem"', MetaAnexo.objects.filter(meta=meta, titulo='ZZ Contrato').exists())
        r = c_colab.post(ANEXO, {'url': 'https://exemplo-teste.local/doc'}, follow=True)
        t('link pelo formulário continua', 'Link anexado.' in r.content.decode()
          and MetaAnexo.objects.filter(meta=meta, tipo=MetaAnexo.Tipo.LINK).exists())

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    shutil.rmtree(pasta, ignore_errors=True)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
