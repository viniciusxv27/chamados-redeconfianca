"""Envio de documentos no pré-cadastro pelo celular.

Sintoma relatado: "Não foi possível acessar seu arquivo" no celular. A
mensagem é do Chrome no Android, não do portal — aparece quando o `accept` do
campo lista só extensões, sem tipo MIME: o seletor devolve um arquivo que o
WebView não consegue abrir.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.db import transaction

from users.views import DOCUMENT_ALLOWED_EXTENSIONS, _document_is_valid

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


class Falso:
    """Arquivo enviado, só com o que a validação lê."""
    def __init__(self, nome, tamanho=1024):
        self.name = nome
        self.size = tamanho


marcador = transaction.atomic()
marcador.__enter__()
try:
    PAGINA = ler('templates/users/pre_register_complete.html')

    print('== O ACCEPT PRECISA TER TIPO MIME ==')
    # Era só extensão, e é isso que quebra o seletor do Android.
    t('tem image/* (abre Câmera e Galeria)', 'accept="image/*' in PAGINA)
    t('tem application/pdf', 'application/pdf' in PAGINA)
    t('tem os tipos do Word',
      'application/msword' in PAGINA
      and 'wordprocessingml.document' in PAGINA)
    t('mantém as extensões para o desktop',
      '.pdf,.jpg,.jpeg,.png,.webp' in PAGINA)
    t('não sobrou campo só com extensão',
      'accept=".pdf,.jpg,.jpeg,.png,.webp,.doc,.docx"' not in PAGINA)

    print('\n== FOTO DE IPHONE (HEIC) ==')
    t('o campo aceita heic', '.heic' in PAGINA and '.heif' in PAGINA)
    t('o servidor também', '.heic' in DOCUMENT_ALLOWED_EXTENSIONS
      and '.heif' in DOCUMENT_ALLOWED_EXTENSIONS)
    valido, erro = _document_is_valid(Falso('IMG_4821.HEIC'))
    t('foto do iPhone passa na validação', valido, erro)
    valido, erro = _document_is_valid(Falso('documento.HEIF'))
    t('heif também', valido, erro)

    print('\n== O QUE CONTINUA RECUSADO ==')
    for nome in ('virus.exe', 'planilha.xlsx', 'script.sh', 'arquivo.zip'):
        valido, erro = _document_is_valid(Falso(nome))
        t(f'{nome} continua fora', not valido)
    valido, erro = _document_is_valid(Falso('foto.jpg', 25 * 1024 * 1024))
    t('acima de 20MB continua recusado', not valido)
    t('e diz o limite', '20MB' in erro, erro)

    print('\n== FOTO GRANDE ENCOLHE ANTES DE SUBIR ==')
    t('o formulário pede o encolhimento', 'data-encolher-imagem' in PAGINA)
    t('o script vai junto', 'js/upload-imagem.js' in PAGINA)
    t('tem onde mostrar o aviso', 'data-encolher-aviso' in PAGINA)
    t('a página carrega o static', '{% load static %}' in PAGINA)

    JS = ler('static/js/upload-imagem.js')
    t('o encolhedor trata VÁRIOS campos (um por documento)',
      'camposComImagem' in JS and 'Promise.all' in JS)
    t('e não só o primeiro',
      "form.querySelector('input[type=\"file\"]')" not in JS)
    t('PDF e Word passam intactos', "image\\/(jpeg|png|webp)" in JS)
    t('erro de decodificação segue com o original', 'img.onerror' in JS)
    t('não reenvia em loop', "dataset.encolhido === '1'" in JS)

    print('\n== A TELA EXPLICA ==')
    t('o texto cita HEIC', 'HEIC' in PAGINA)
    t('e avisa que a foto é reduzida', 'reduzidas automaticamente' in PAGINA)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
