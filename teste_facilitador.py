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
    print('\n== QR CODE DO ARQUIVO ==')
    import re
    from unittest import mock
    from django.core import signing
    from facilitador import views as fviews
    html = cc.get(f'/facilitador/{arq.pk}/').content.decode()
    achado = re.search(r'id="fac-qr" data-url="([^"]+)"', html)
    qr = achado.group(1).replace('&amp;', '&') if achado else ''
    t('a tela do arquivo tem QR code com link absoluto de download', qr.startswith('http://testserver/facilitador/qr/')
      and 'qrcode.min.js' in html and 'baixar o arquivo direto nele' in html, qr)
    anon = Client()
    r = anon.get(qr.replace('http://testserver', ''))
    t('o celular baixa pelo QR sem login', r.status_code == 200 and b''.join(r.streaming_content) == pdf
      and 'attachment' in r['Content-Disposition'], r.status_code)
    t('e conta como download', Recurso.objects.get(pk=arq.pk).acessos == 2, Recurso.objects.get(pk=arq.pk).acessos)
    caminho = qr.replace('http://testserver', '')
    r = anon.get(caminho[:-3] + 'xyz/')
    t('QR adulterado não baixa', r.status_code == 404 and 'Arquivo indisponível' in r.content.decode())
    with mock.patch.object(fviews, 'QR_VALIDADE_HORAS', -1):
        r = anon.get(caminho)
    t('QR vencido avisa que expirou', r.status_code == 410 and 'expirou' in r.content.decode())
    token_outro = signing.dumps({'r': arq.pk, 'a': 'facilitador/outro.pdf'}, salt='facilitador.qr', compress=True)
    t('QR de um arquivo que foi trocado deixa de valer', anon.get(f'/facilitador/qr/{token_outro}/').status_code == 404)
    Recurso.objects.filter(pk=arq.pk).update(ativo=False)
    t('item oculto não baixa pelo QR', anon.get(caminho).status_code == 404)
    html = ca.get(f'/facilitador/{arq.pk}/').content.decode()
    t('oculto: a tela explica que o QR volta quando ficar visível', 'id="fac-qr"' not in html
      and 'visível para todos' in html)
    Recurso.objects.filter(pk=arq.pk).update(ativo=True)

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

    print('\n== PÁGINA HTML ==')
    html_bytes = '<!doctype html><html><head><meta charset="utf-8"><title>Campanha</title></head><body><h1>Promoção de Natal ção</h1><script>document.title="ok"</script></body></html>'.encode()
    r = ca.post('/facilitador/novo/', {'tipo': 'ARQUIVO', 'titulo': 'ZZ Campanha Natal', 'ordem': '0', 'ativo': 'on',
                                       'arquivo': SimpleUploadedFile('campanha.html', html_bytes, content_type='text/html')}, follow=True)
    pag = Recurso.objects.get(titulo='ZZ Campanha Natal')
    t('subir HTML gera o link da página na hora', pag.e_html and len(pag.token_pagina) >= 20
      and 'já tem link próprio' in r.content.decode() and f'/facilitador/p/{pag.token_pagina}/' in r.content.decode())
    t('o card vira "código"', pag.icone == 'fa-file-code')
    h = cc.get(f'/facilitador/{pag.pk}/').content.decode()
    t('detalhe: abrir, copiar, baixar, prévia e QR da página', 'Abrir página' in h and 'Copiar link' in h and 'Baixar HTML' in h
      and f'/facilitador/{pag.pk}/pagina/?previa=1' in h and f'data-url="http://testserver/facilitador/p/{pag.token_pagina}/"' in h
      and 'abrir a página' in h)
    t('quem não gere não vê "gerar novo link"', 'Gerar novo link' not in h and 'Gerar novo link' in ca.get(f'/facilitador/{pag.pk}/').content.decode())
    anonimo = Client()
    r = anonimo.get(f'/facilitador/p/{pag.token_pagina}/')
    t('o link abre a página sem login, como HTML', r.status_code == 200 and r['Content-Type'] == 'text/html; charset=utf-8'
      and 'Promoção de Natal ção' in r.content.decode())
    t('a página roda isolada do portal', r['Content-Security-Policy'].startswith('sandbox allow-scripts')
      and 'allow-same-origin' not in r['Content-Security-Policy'] and r['X-Content-Type-Options'] == 'nosniff')
    pag.refresh_from_db()
    acessos = pag.acessos
    t('abrir pelo link conta acesso; a prévia não', acessos == 1
      and cc.get(f'/facilitador/{pag.pk}/pagina/?previa=1').status_code == 200 and Recurso.objects.get(pk=pag.pk).acessos == 1)
    t('a prévia pode aparecer dentro do portal', cc.get(f'/facilitador/{pag.pk}/pagina/?previa=1').get('X-Frame-Options') == 'SAMEORIGIN')
    t('link inventado não abre', anonimo.get('/facilitador/p/inventado123/').status_code == 404)
    t('a página interna pede login', anonimo.get(f'/facilitador/{pag.pk}/pagina/').status_code == 302)
    latin = '<html><head><meta charset="iso-8859-1"></head><body>Promoção</body></html>'.encode('latin-1')
    token = pag.token_pagina
    ca.post(f'/facilitador/{pag.pk}/editar/', {'tipo': 'ARQUIVO', 'titulo': 'ZZ Campanha Natal', 'ordem': '0', 'ativo': 'on',
                                                'arquivo': SimpleUploadedFile('campanha2.htm', latin, content_type='text/html')})
    pag.refresh_from_db()
    r = anonimo.get(f'/facilitador/p/{token}/')
    t('trocar por outro HTML mantém o link e mostra o novo', pag.token_pagina == token and r.status_code == 200
      and r.content == latin and r['Content-Type'] == 'text/html')
    ca.post(f'/facilitador/{pag.pk}/visibilidade/')
    t('oculto: o link para de abrir', anonimo.get(f'/facilitador/p/{token}/').status_code == 404)
    ca.post(f'/facilitador/{pag.pk}/visibilidade/')
    t('quem não é SUPERADMIN não troca o link', cc.post(f'/facilitador/{pag.pk}/novo-link/').status_code == 403)
    ca.post(f'/facilitador/{pag.pk}/novo-link/')
    pag.refresh_from_db()
    t('gerar novo link derruba o antigo', pag.token_pagina != token and anonimo.get(f'/facilitador/p/{token}/').status_code == 404
      and anonimo.get(f'/facilitador/p/{pag.token_pagina}/').status_code == 200)
    ca.post(f'/facilitador/{pag.pk}/editar/', {'tipo': 'ARQUIVO', 'titulo': 'ZZ Campanha Natal', 'ordem': '0', 'ativo': 'on',
                                                'arquivo': SimpleUploadedFile('regras.pdf', b'%PDF-1.4', content_type='application/pdf')})
    pag.refresh_from_db()
    t('virou PDF: some o link da página', pag.token_pagina == '' and cc.get(f'/facilitador/{pag.pk}/pagina/').status_code == 404)
    pdf = Recurso.objects.filter(nome_original__endswith='.pdf').exclude(pk=pag.pk).first()
    t('arquivo que não é HTML não tem página', pdf is None or cc.get(f'/facilitador/{pdf.pk}/pagina/').status_code == 404)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
