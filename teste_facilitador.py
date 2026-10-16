"""Facilitador: grade de arquivos e links (todos veem; só o SUPERADMIN gerencia).

Tudo roda dentro de uma transação desfeita no fim; os arquivos ficam em memória
(nada vai para o S3).
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

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import InMemoryStorage
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client

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
    executor = MigrationExecutor(connection)
    ultima = executor.loader.graph.leaf_nodes('facilitador')
    if executor.migration_plan(ultima):
        executor.migrate(ultima)
    from facilitador.models import Recurso
    Recurso._meta.get_field('arquivo').storage = InMemoryStorage()

    def novo(username, hierarchy):
        return User.objects.create_user(username=username, email=f'{username}@exemplo-teste.local',
                                        password='S3nha!teste', first_name='ZZ', last_name=username,
                                        hierarchy=hierarchy)

    admin = novo('zzfac.super', 'SUPERADMIN')
    comum = novo('zzfac.comum', 'PADRAO')
    ca, cc = Client(), Client()
    ca.force_login(admin)
    cc.force_login(comum)

    print('== MENU E ACESSO ==')
    r = cc.get('/facilitador/')
    t('qualquer pessoa abre o Facilitador', r.status_code == 200, r.status_code)
    html = r.content.decode()
    t('o menu mostra o Facilitador para todos', 'href="/facilitador/"' in html)
    t('quem não é SUPERADMIN não vê os botões de cadastro', 'Novo arquivo' not in html)
    t('e não abre o cadastro', cc.get('/facilitador/novo/').status_code == 403)
    t('o SUPERADMIN vê os botões', 'Novo arquivo' in ca.get('/facilitador/').content.decode())
    t('sem login vai para o login', Client().get('/facilitador/').status_code == 302)

    print('\n== ARQUIVO ==')
    pdf = b'%PDF-1.4 manual de teste'
    r = ca.post('/facilitador/novo/', {
        'tipo': 'ARQUIVO', 'titulo': 'ZZ Manual do caixa', 'descricao': 'Passo a passo do fechamento.\nVer https://exemplo.local',
        'categoria': 'Financeiro', 'ordem': '1', 'ativo': 'on',
        'arquivo': SimpleUploadedFile('manual caixa.pdf', pdf, content_type='application/pdf')})
    arq = Recurso.objects.filter(titulo='ZZ Manual do caixa').first()
    t('o SUPERADMIN publica um arquivo', r.status_code == 302 and arq and arq.arquivo, r.status_code)
    t('guarda nome, tipo e tamanho originais', arq and arq.nome_original == 'manual caixa.pdf'
      and arq.content_type == 'application/pdf' and arq.tamanho == len(pdf))
    t('ícone e cor pela extensão', arq and arq.icone == 'fa-file-pdf' and arq.cor == 'red')
    html = cc.get('/facilitador/').content.decode()
    t('o card aparece na grade de todos', 'ZZ Manual do caixa' in html and f'/facilitador/{arq.pk}/' in html)
    html = cc.get(f'/facilitador/{arq.pk}/').content.decode()
    t('o card abre a descrição e o botão de baixar', 'Passo a passo do fechamento.' in html
      and 'Baixar arquivo' in html and f'/facilitador/{arq.pk}/baixar/' in html)
    t('PDF também pode ser visualizado', '?ver=1' in html)
    t('quem não gerencia não vê editar/excluir', 'Excluir' not in html and '/editar/' not in html)
    r = cc.get(f'/facilitador/{arq.pk}/baixar/')
    corpo = b''.join(r.streaming_content)
    t('o download entrega o arquivo com o nome original', r.status_code == 200 and corpo == pdf
      and 'attachment' in r['Content-Disposition'] and 'manual' in r['Content-Disposition'], r.get('Content-Disposition'))
    arq.refresh_from_db()
    t('e conta o download', arq.acessos == 1, arq.acessos)
    r = cc.get(f'/facilitador/{arq.pk}/baixar/?ver=1')
    t('visualizar abre no navegador (inline) e não conta', 'inline' in r['Content-Disposition']
      and Recurso.objects.get(pk=arq.pk).acessos == 1)
    r = ca.post('/facilitador/novo/', {'tipo': 'ARQUIVO', 'titulo': 'ZZ sem arquivo', 'ordem': '0'})
    t('arquivo é obrigatório', r.status_code == 200 and 'Escolha o arquivo.' in r.content.decode()
      and not Recurso.objects.filter(titulo='ZZ sem arquivo').exists())

    print('\n== LINK ==')
    r = ca.post('/facilitador/novo/', {
        'tipo': 'LINK', 'titulo': 'ZZ Portal da Vivo', 'descricao': 'Acesse com o seu login de rede.',
        'categoria': 'Sistemas', 'url': 'https://www.exemplo.com.br/portal?x=1', 'ordem': '0', 'ativo': 'on'})
    lnk = Recurso.objects.filter(titulo='ZZ Portal da Vivo').first()
    t('o SUPERADMIN publica um link', r.status_code == 302 and lnk and lnk.e_link and not lnk.arquivo)
    t('mostra o domínio no card', lnk and lnk.dominio == 'exemplo.com.br', lnk and lnk.dominio)
    html = cc.get(f'/facilitador/{lnk.pk}/').content.decode()
    t('o card abre a explicação, o botão do link e o QR code', 'Acesse com o seu login de rede.' in html
      and 'Ir para o link' in html and 'id="fac-qr"' in html and 'qrcode.min.js' in html
      and 'data-url="https://www.exemplo.com.br/portal?x=1"' in html)
    r = cc.get(f'/facilitador/{lnk.pk}/ir/')
    t('"Ir para o link" redireciona e conta o clique', r.status_code == 302
      and r['Location'] == 'https://www.exemplo.com.br/portal?x=1' and Recurso.objects.get(pk=lnk.pk).acessos == 1)
    r = ca.post('/facilitador/novo/', {'tipo': 'LINK', 'titulo': 'ZZ link perigoso', 'url': 'javascript:alert(1)', 'ordem': '0'})
    t('link que não é http/https é recusado', not Recurso.objects.filter(titulo='ZZ link perigoso').exists())
    r = ca.post('/facilitador/novo/', {'tipo': 'LINK', 'titulo': 'ZZ link vazio', 'ordem': '0'})
    t('link é obrigatório', 'Informe o link.' in r.content.decode())
    t('baixar num link dá 404', cc.get(f'/facilitador/{lnk.pk}/baixar/').status_code == 404)

    print('\n== FILTROS ==')
    html = cc.get('/facilitador/?tipo=LINK').content.decode()
    t('filtra só links', 'ZZ Portal da Vivo' in html and 'ZZ Manual do caixa' not in html)
    html = cc.get('/facilitador/?categoria=financeiro').content.decode()
    t('filtra por categoria', 'ZZ Manual do caixa' in html and 'ZZ Portal da Vivo' not in html)
    html = cc.get('/facilitador/?q=fechamento').content.decode()
    t('a busca olha a descrição', 'ZZ Manual do caixa' in html and 'ZZ Portal da Vivo' not in html)

    print('\n== GESTÃO ==')
    t('quem não é SUPERADMIN não edita, oculta nem exclui',
      cc.get(f'/facilitador/{arq.pk}/editar/').status_code == 403
      and cc.post(f'/facilitador/{arq.pk}/visibilidade/').status_code == 403
      and cc.post(f'/facilitador/{arq.pk}/excluir/').status_code == 403
      and Recurso.objects.filter(pk=arq.pk, ativo=True).exists())
    nome_antigo = Recurso.objects.get(pk=arq.pk).arquivo.name
    r = ca.post(f'/facilitador/{arq.pk}/editar/', {
        'tipo': 'ARQUIVO', 'titulo': 'ZZ Manual do caixa v2', 'descricao': 'nova', 'categoria': 'Financeiro',
        'ordem': '1', 'ativo': 'on'})
    arq.refresh_from_db()
    t('editar sem novo arquivo mantém o atual', r.status_code == 302 and arq.titulo == 'ZZ Manual do caixa v2'
      and arq.arquivo.name == nome_antigo and arq.nome_original == 'manual caixa.pdf')
    r = ca.post(f'/facilitador/{arq.pk}/editar/', {
        'tipo': 'ARQUIVO', 'titulo': 'ZZ Manual do caixa v2', 'ordem': '1', 'ativo': 'on',
        'arquivo': SimpleUploadedFile('tabela.xlsx', b'PK planilha', content_type='application/vnd.ms-excel')})
    arq.refresh_from_db()
    storage = Recurso._meta.get_field('arquivo').storage
    t('trocar o arquivo atualiza os dados e apaga o antigo', arq.nome_original == 'tabela.xlsx'
      and arq.icone == 'fa-file-excel' and not storage.exists(nome_antigo))
    ca.post(f'/facilitador/{arq.pk}/visibilidade/')
    t('ocultar tira da grade de todos', 'ZZ Manual do caixa v2' not in cc.get('/facilitador/').content.decode()
      and cc.get(f'/facilitador/{arq.pk}/').status_code == 404
      and cc.get(f'/facilitador/{arq.pk}/baixar/').status_code == 404)
    t('o SUPERADMIN continua vendo, marcado como oculto',
      'ZZ Manual do caixa v2' in (h := ca.get('/facilitador/').content.decode()) and 'Oculto' in h)
    nome = arq.arquivo.name
    r = ca.post(f'/facilitador/{arq.pk}/excluir/')
    t('excluir remove o card e o arquivo', r.status_code == 302 and not Recurso.objects.filter(pk=arq.pk).exists()
      and not storage.exists(nome))
    t('excluir só por POST', ca.get(f'/facilitador/{lnk.pk}/excluir/').status_code == 405)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
