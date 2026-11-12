"""Acess (12/11/2026): XML do pedido de criação do perfil GEFAC na Etapa 1.
Anexar (só .xml bem-formado, sem DOCTYPE/ENTITY, até 5 MB), histórico na ficha, baixar só pelo portal,
remover, permissões e aviso ao concluir o GEFAC sem XML.
Transação desfeita no fim; os arquivos vão para uma pasta temporária (nada no MinIO); nada sai para a rede.
"""
import os
import sys
import tempfile
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings  # noqa: E402

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-axg'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-axg2'}}
django.setup()
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import requests  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.core.files.storage import FileSystemStorage  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402

from acess import servicos as sv  # noqa: E402
from acess.models import AcessoAcess, LogAcess, XmlGefac  # noqa: E402
from acess.permissoes import CHAVES  # noqa: E402
from users.models import Sector  # noqa: E402

User = get_user_model()
ok = fail = 0
PASTA = tempfile.mkdtemp(prefix='zz-xml-gefac-')
XmlGefac._meta.get_field('arquivo').storage = FileSystemStorage(location=PASTA)


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def sem_rede(*a, **k):
    raise AssertionError('o teste tentou sair para a rede')


XML = b'<?xml version="1.0" encoding="UTF-8"?><pedido><perfil>CONSULTOR</perfil><nome>ZZ Fulana</nome></pedido>'


def arquivo(nome='pedido.xml', conteudo=XML):
    return SimpleUploadedFile(nome, conteudo, content_type='application/xml')


marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede):
        loja = Sector.objects.create(name='Loja ZZ Xml', adabas='ESZZX01-001')
        op = User.objects.create_user(username='zz.axg.op', email='zz.axg.op@exemplo-teste.local', password='x',
                                      first_name='ZZ', last_name='Operadora', hierarchy='ADMIN')
        AcessoAcess.objects.create(user=op, permissoes=sorted(CHAVES))
        leitor = User.objects.create_user(username='zz.axg.leitor', email='zz.axg.leitor@exemplo-teste.local', password='x',
                                          first_name='ZZ', last_name='Leitor', hierarchy='ADMIN')
        AcessoAcess.objects.create(user=leitor, permissoes=['colaboradores.ver'])
        fora = User.objects.create_user(username='zz.axg.fora', email='zz.axg.fora@exemplo-teste.local', password='x',
                                        first_name='ZZ', last_name='Fora', hierarchy='PADRAO')
        c = sv.criar_colaborador({'nome': 'ZZ Fulana Xml', 'cpf': '52998224725', 'nascimento': '1990-05-07',
                                  'email': 'zz.fulana@exemplo-teste.local', 'telefone': '27 99999-1111', 'cargo': 'consultor'},
                                 loja, usuario=op)
        co, cl, cf = Client(), Client(), Client()
        co.force_login(op); cl.force_login(leitor); cf.force_login(fora)
        anexar = f'/acess/colaborador/{c.id}/xml-gefac/'

        print('— Etapa 1: anexar o XML')
        t('a pessoa está na Etapa 1 (Criação do GEFAC)', c.etapa_calculada == 1, c.etapa_calculada)
        html = co.get('/acess/pendentes/?etapa=1&q=ZZ+Fulana+Xml').content.decode()
        t('Pendentes mostra "sem XML" e o botão de anexar', 'XML do perfil GEFAC' in html and 'sem XML' in html and 'Anexar XML' in html)
        t('concluir sem XML avisa na confirmação', 'Ainda não há XML do pedido anexado' in html)
        r = co.post(anexar, {'xml': arquivo(), 'voltar': '/acess/pendentes/?etapa=1'})
        x = XmlGefac.objects.filter(colaborador=c).first()
        t('anexa e volta para a tela de onde veio', r.status_code == 302 and r['Location'] == '/acess/pendentes/?etapa=1' and x is not None)
        t('guarda nome, tamanho e quem enviou', x.nome_original == 'pedido.xml' and x.tamanho == len(XML) and x.enviado_por == op)
        t('arquivo com nome impossível de adivinhar', x.arquivo.name.startswith('acess/gefac/') and 'pedido' not in x.arquivo.name, x.arquivo.name)
        t('fica no histórico do Acess', LogAcess.objects.filter(colaborador=c, acao='XML_GEFAC_ANEXADO').exists())
        html = co.get('/acess/pendentes/?etapa=1&q=ZZ+Fulana+Xml').content.decode()
        t('Pendentes mostra o XML e "Trocar XML"', 'pedido.xml' in html and 'Trocar XML' in html and 'Ainda não há XML' not in html)

        print('— Só XML de verdade')
        for nome, conteudo, motivo in (('pedido.pdf', b'%PDF-1.4', 'extensão'), ('vazio.xml', b'', 'vazio'),
                                       ('torto.xml', b'<pedido><perfil>', 'mal-formado'),
                                       ('ataque.xml', b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>', 'DOCTYPE/ENTITY'),
                                       ('grande.xml', b'<a>' + b'x' * (sv.XML_GEFAC_TAMANHO_MAX + 10) + b'</a>', 'tamanho')):
            antes = XmlGefac.objects.count()
            r = co.post(anexar, {'xml': arquivo(nome, conteudo)}, follow=True)
            erros = [str(m) for m in r.context['messages']] if r.context else []
            t(f'recusa {motivo}', XmlGefac.objects.count() == antes and erros, erros)
        r = co.post(anexar, {}, follow=True)
        t('sem arquivo: pede o arquivo', XmlGefac.objects.count() == 1)

        print('— Ficha: histórico, baixar, ver, remover')
        co.post(anexar, {'xml': arquivo('pedido-v2.xml', XML.replace(b'CONSULTOR', b'GERENTE'))})
        html = co.get(f'/acess/colaborador/{c.id}/').content.decode()
        t('ficha lista os dois, o mais novo como atual', html.index('pedido-v2.xml') < html.index('pedido.xml') and 'atual' in html
          and 'Anexar nova versão' in html)
        novo = XmlGefac.objects.filter(colaborador=c).first()
        r = cl.get(f'/acess/colaborador/{c.id}/xml-gefac/{novo.id}/')
        t('quem só vê colaboradores baixa o XML pelo portal', r.status_code == 200 and b''.join(r.streaming_content).count(b'GERENTE') == 1
          and 'attachment' in r['Content-Disposition'] and 'pedido-v2.xml' in r['Content-Disposition'] and r['Cache-Control'] == 'private, no-store')
        r = cl.get(f'/acess/colaborador/{c.id}/xml-gefac/{novo.id}/?ver=1')
        t('"ver" abre no navegador, sem adivinhar o tipo', r.status_code == 200 and 'attachment' not in r.get('Content-Disposition', '')
          and r['X-Content-Type-Options'] == 'nosniff')
        t('XML de outra ficha pelo número errado: 404', co.get(f'/acess/colaborador/{c.id + 99999}/xml-gefac/{novo.id}/').status_code == 404)
        t('quem não é do Acess não baixa', cf.get(f'/acess/colaborador/{c.id}/xml-gefac/{novo.id}/').status_code in (302, 403))
        html = cl.get(f'/acess/colaborador/{c.id}/').content.decode()
        t('quem só vê não tem anexar nem remover', 'Anexar nova versão' not in html and 'xml-gefac/' in html and '/remover/' not in html)
        cl.post(anexar, {'xml': arquivo('leitor.xml')})
        t('quem só vê não consegue anexar pelo POST', not XmlGefac.objects.filter(nome_original='leitor.xml').exists())
        cl.post(f'/acess/colaborador/{c.id}/xml-gefac/{novo.id}/remover/')
        t('nem remover', XmlGefac.objects.filter(pk=novo.pk).exists())
        caminho = novo.arquivo.name
        co.post(f'/acess/colaborador/{c.id}/xml-gefac/{novo.id}/remover/', {'voltar': f'/acess/colaborador/{c.id}/#acXmlGefac'})
        t('remover tira o registro e o arquivo, e registra', not XmlGefac.objects.filter(pk=novo.pk).exists()
          and not XmlGefac._meta.get_field('arquivo').storage.exists(caminho)
          and LogAcess.objects.filter(colaborador=c, acao='XML_GEFAC_REMOVIDO').exists())
        t('o anterior volta a ser o atual', XmlGefac.objects.filter(colaborador=c).first().nome_original == 'pedido.xml')

        print('— Concluir o GEFAC segue igual')
        c.refresh_from_db()
        r = co.post(f'/acess/colaborador/{c.id}/esteira/', {'acao': 'gefac', 'versao': c.versao})
        c.refresh_from_db()
        t('concluir leva à Etapa 2, com o XML guardado', c.etapa_calculada == 2 and XmlGefac.objects.filter(colaborador=c).exists())
finally:
    marcador.__exit__(Exception, Exception('desfaz'), None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
