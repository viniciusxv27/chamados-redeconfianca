"""/vendas/precos/importar/ (06/11/2026): tabela pelo e-mail todo dia às 6h e o "Sim Card 5g" que
não pode sair na importação. Banco descartável; a caixa de e-mail é simulada (nada conecta no
servidor de verdade) e o WhatsApp é dublado.
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

# Banco e arquivos descartáveis: a tabela das entregas e a coluna
# `exige_entrega` só existem no Postgres compartilhado depois do migrate, e o
# migrate é dele. O schema aqui sai direto dos models (`--run-syncdb`), como em
# qualquer outra sessão — nada toca o banco de verdade nem o MinIO.
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-tabmail-'))
(PASTA / 'zz_tabmail_settings.py').write_text(f"""
from redeconfianca.settings import *              # noqa: F401,F403

DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3',
                          'NAME': r'{PASTA}/banco.sqlite3'}}}}


class SemMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = SemMigrations()

USE_S3 = False
MEDIA_ROOT = r'{PASTA}/media'
MEDIA_URL = '/media/'
STORAGES = {{
    'default': {{'BACKEND': 'django.core.files.storage.FileSystemStorage'}},
    'staticfiles': {{'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
}}
DEBUG = False
OPENAI_API_KEY = ''
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
CACHES = {{
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-iacomp'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-iacomp-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_tabmail_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

from datetime import time
from email.message import EmailMessage
from io import BytesIO

import openpyxl
import requests

from core import agendador_diario, evolution
from portal_popups.models import PortalPopup
from vendas import tabela_email
from vendas.models import ConfiguracaoVendas, ImportacaoPrecos, ItemPreco
from vendas.services import importar_tabela_precos
from webmail.seguranca import cifrar, decifrar

User = get_user_model()
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {str(extra)[:500]}')


for nome_m in ('get', 'post', 'request'):
    mock.patch.object(requests, nome_m, lambda *a, **k: (_ for _ in ()).throw(AssertionError('rede'))).start()
enviados = []
mock.patch.object(evolution, 'enviar_texto', lambda numero, texto, **k: (enviados.append(numero) or (True, 'ok'))).start()


def planilha(preco=1000):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for aba in ('SMARTPHONES', 'ELETRÔNICOS_LP_Conectados'):
        ws = wb.create_sheet(aba)
        ws.append([None] * 4 + ['TITULO'])
        ws.append([None] * 4 + ['Vigência', 'TABELA REGULAR', None, None, 'TABELA RENOVA'])
        ws.append(['PORTFÓLIO', 'CATEGORIA', 'MARCA', 'OFERTA', 'Nome Comercial', 'PRÉ', 'PIX\ne\nVivo Pay', '2x', 'X'])
        ws.append(['In', 'Smartphone', 'Zz', 'O', f'ZZ {aba[:5]} Phone', preco, preco * 0.9, preco, 0])
    ws = wb.create_sheet('ELETRÔNICOS_LP_Não Conectados')
    ws.append([None, None, None, 'TITULO'])
    ws.append([None, None, None, 'Vigência'])
    ws.append(['PORTFÓLIO', 'CATEGORIA', 'MARCA', 'Nome Comercial', 'PVP Base'])
    ws.append(['In', 'Essenciais', 'Zz', 'ZZ Capa', 50])
    b = BytesIO(); wb.save(b)
    return b.getvalue()


print('== SIM CARD 5G FICA ==')
sim = ItemPreco.objects.create(categoria='SimCard', nome='Sim Card 5g', valor=13)
sim2 = ItemPreco.objects.create(categoria='SimCard', nome='SIM CARD 5G ', valor=13)     # outra grafia
avulso = ItemPreco.objects.create(categoria='Outro', nome='Produto avulso', valor=5)
r = importar_tabela_precos(BytesIO(planilha()))
t('o Sim Card 5g continua, ativo', ItemPreco.objects.filter(pk=sim.pk, ativo=True).exists())
t('sem olhar maiúscula nem espaço', ItemPreco.objects.filter(pk=sim2.pk, ativo=True).exists())
t('o resto que não está na planilha sai', not ItemPreco.objects.filter(pk=avulso.pk).exists())
t('o resumo diz o que foi mantido', {n.strip() for n in r['mantidos']} == {'Sim Card 5g', 'SIM CARD 5G'}, r.get('mantidos'))
importar_tabela_precos(BytesIO(planilha(1100)))
t('e na importação seguinte também', ItemPreco.objects.filter(pk=sim.pk, ativo=True).exists())
ItemPreco.objects.filter(pk=sim2.pk).delete()


class CaixaFalsa:
    """O pedaço do imaplib que a busca usa: select, SEARCH e FETCH por UID."""
    def __init__(self, mensagens):
        self.mensagens = mensagens          # {uid: bytes}
        self.selecionada = None
        self.comandos = []

    def select(self, pasta, readonly=False):
        self.selecionada = (pasta, readonly)
        return 'OK', [b'1']

    def uid(self, comando, *args):
        self.comandos.append((comando, args))
        if comando == 'SEARCH':
            return 'OK', [b' '.join(str(u).encode() for u in sorted(self.mensagens))]
        uids = [int(u) for u in args[0].split(',')]
        resp = []
        for u in uids:
            bruto = self.mensagens[u]
            corpo = bruto.split(b'\r\n\r\n', 1)[0] + b'\r\n\r\n' if 'HEADER' in args[1] else bruto
            resp += [(f'{u} (UID {u} BODY[] {{{len(corpo)}}}'.encode(), corpo), b')']
        return 'OK', resp

    def logout(self):
        pass


def mensagem(assunto, anexos, mid):
    m = EmailMessage()
    m['Subject'] = assunto
    m['Message-ID'] = mid
    m['Date'] = 'Fri, 06 Nov 2026 05:10:00 -0300'
    m.set_content('segue a tabela')
    for nome, dados in anexos:
        m.add_attachment(dados, maintype='application', subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename=nome)
    return m.as_bytes().replace(b'\n', b'\r\n') if b'\r\n' not in m.as_bytes() else m.as_bytes()


XLSX = planilha(1200)
caixa = CaixaFalsa({
    10: mensagem('Re: outra coisa', [], '<a@x>'),
    11: mensagem('Tabela de Preços >> 01/11', [('Tabela Demais Canais.xlsx', planilha(900))], '<velha@x>'),
    12: mensagem('Fwd: Tabela de Preços >> Vigência 06/11', [('Tabela Lojas Próprias.xlsx', b'x'), ('TABELA DE PREÇOS - DEMAIS CANAIS 06.11.xlsx', XLSX)], '<nova@x>'),
})

print('\n== A BUSCA NA CAIXA ==')
achado = tabela_email.procurar(caixa)
t('pega a mais nova com o assunto', achado['uid'] == 12 and achado['message_id'] == '<nova@x>', achado and achado['uid'])
t('e o anexo "Demais Canais", sem olhar acento/maiúscula', achado['anexo_nome'] == 'TABELA DE PREÇOS - DEMAIS CANAIS 06.11.xlsx' and achado['conteudo'] == XLSX)
t('a caixa abre só para leitura e nada é marcado como lido', caixa.selecionada == ('INBOX', True)
  and all('PEEK' in a[1] for c, a in caixa.comandos if c == 'FETCH'))
t('assunto sem o ">>" não serve', not tabela_email.assunto_confere('Tabela de Preços 06/11'))
t('anexo que não é .xlsx não serve', not tabela_email.anexo_confere('Demais Canais.pdf'))
t('caixa sem o e-mail: None', tabela_email.procurar(CaixaFalsa({1: mensagem('Oi', [], '<o@x>')})) is None)

print('\n== TODO DIA ÀS 6H ==')
tarefa = next(x for x in agendador_diario.TAREFAS if x[0] == 'vendas_tabela_email')
t('a tarefa está no agendador às 6h', tarefa[1] == time(6, 0) and tarefa[2] == 'vendas.tabela_email.tarefa_diaria')
t('desligada, não faz nada', 'desligada' in tabela_email.tarefa_diaria())
boss = User.objects.create_user(username='zzt.boss', email='zzt.boss@exemplo-teste.local', password='S3nha!teste',
                                first_name='Boss', last_name='ZZ', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)
fone = User.objects.create_user(username='zzt.fone', email='zzt.fone@exemplo-teste.local', password='S3nha!teste',
                                first_name='Fone', last_name='ZZ', phone='27998765432')
config = ConfiguracaoVendas.atual()
config.tabela_email, config.tabela_email_senha, config.tabela_email_ativo = 'alcides@exemplo-teste.local', cifrar('segredo'), True
config.save()
config.aviso_precos_destinatarios.set([fone])
caixa.comandos.clear()
with mock.patch.object(tabela_email, '_entrar', return_value=caixa) as entrar:
    linha = tabela_email.tarefa_diaria()
    config.refresh_from_db()
    imp = ImportacaoPrecos.objects.order_by('-id').first()
    t('entra com a caixa e a senha guardadas', entrar.call_args[0] == ('alcides@exemplo-teste.local', 'segredo', 'imap-vip.uni5.net'))
    t('importa a tabela do e-mail', linha.startswith('importada') and imp.origem == 'email' and imp.usuario is None
      and imp.arquivo.startswith('TABELA DE PREÇOS - DEMAIS CANAIS') and not imp.erro, (linha, imp.erro))
    t('o preço é o do anexo', ItemPreco.objects.get(nome='ZZ SMART Phone').valor == 1200)
    t('o Sim Card continua', ItemPreco.objects.filter(pk=sim.pk, ativo=True).exists())
    t('avisa quem está marcado e publica o popup', enviados == ['5527998765432'] and imp.popup_id
      and PortalPopup.objects.filter(pk=imp.popup_id, is_active=True).exists(), enviados)
    t('guarda o que importou', config.tabela_email_estado['ultimo_message_id'] == '<nova@x>' and config.tabela_email_estado['situacao'] == 'importada')
    n = ImportacaoPrecos.objects.count()
    linha = tabela_email.tarefa_diaria()
    t('no dia seguinte, o mesmo e-mail não entra de novo', linha.startswith('ja_importada') and ImportacaoPrecos.objects.count() == n, linha)

    caixa.mensagens[13] = mensagem('Tabela de Preços >> 07/11', [('Lojas Próprias.xlsx', b'x')], '<sem@x>')
    linha = tabela_email.tarefa_diaria()
    t('e-mail sem o anexo certo: avisa quais vieram', linha.startswith('sem_anexo') and 'Lojas Próprias.xlsx' in linha, linha)
    caixa.mensagens[14] = mensagem('Tabela de Preços >> 08/11', [('Demais Canais.xlsx', b'nao e planilha')], '<ruim@x>')
    linha = tabela_email.tarefa_diaria()
    config.refresh_from_db()
    t('planilha com erro: registra a falha e tenta de novo depois', linha.startswith('erro_importacao')
      and config.tabela_email_estado['ultimo_message_id'] == '<nova@x>'
      and ImportacaoPrecos.objects.order_by('-id').first().erro, linha)
    del caixa.mensagens[13], caixa.mensagens[14]

config.tabela_email_avisar = False
config.tabela_email_estado = {}
config.save()
enviados.clear()
with mock.patch.object(tabela_email, '_entrar', return_value=caixa):
    tabela_email.tarefa_diaria()
t('com o aviso desligado, importa sem WhatsApp nem popup', not enviados and not ImportacaoPrecos.objects.order_by('-id').first().popup_id)
with mock.patch.object(tabela_email, '_entrar', side_effect=OSError('servidor fora')):
    linha = tabela_email.tarefa_diaria()
t('servidor fora: registra e não quebra', linha.startswith('erro') and 'servidor fora' in linha, linha)

print('\n== SERVIDOR IMAP ==')
t('padrão é a Uni5', ConfiguracaoVendas.atual().tabela_email_servidor == 'imap-vip.uni5.net')
t('separa host e porta', tabela_email._separar('imaps://imap-vip.uni5.net:993/') == ('imap-vip.uni5.net', 993)
  and tabela_email._separar('imap-vip.uni5.net') == ('imap-vip.uni5.net', 993))
falso = mock.MagicMock(capabilities=('AUTH=PLAIN',))
with mock.patch('imaplib.IMAP4_SSL', return_value=falso) as abre:
    tabela_email.testar_conexao('a@b.c', 'x', 'imap-vip.uni5.net')
t('conecta no servidor configurado com certificado verificado', abre.call_args[0][:2] == ('imap-vip.uni5.net', 993)
  and abre.call_args[1]['ssl_context'].check_hostname and falso.authenticate.called and falso.logout.called)
import ssl as _ssl
from webmail.cliente import ServidorFora
with mock.patch('imaplib.IMAP4_SSL', side_effect=_ssl.SSLCertVerificationError('x')):
    try:
        tabela_email.testar_conexao('a@b.c', 'x', 'imap.exemplo'); deu = None
    except ServidorFora as exc:
        deu = str(exc)
t('certificado errado vira aviso legível', deu and 'certificado' in deu)
with mock.patch('webmail.cliente._conectar_imap', return_value=falso) as webm:
    tabela_email.testar_conexao('a@b.c', 'x', '')
t('em branco usa o servidor do /email/', webm.called)

print('\n== TELA (SUPERADMIN) ==')
cb = Client(); cb.force_login(boss)
h = cb.get('/vendas/precos/importar/').content.decode()
t('a seção aparece com a caixa e sem a senha', 'id="vdTabelaEmail"' in h and 'alcides@exemplo-teste.local' in h and 'segredo' not in h
  and 'guardada' in h)
with mock.patch.object(tabela_email, 'testar_conexao') as testar:
    r = cb.post('/vendas/precos/importar/email/', {'acao': 'salvar', 'conta': 'Alcides@Exemplo-Teste.local', 'senha': 'nova-senha', 'servidor': 'imap-vip.uni5.net', 'ativo': 'on', 'avisar': 'on'}, follow=True)
config.refresh_from_db()
t('salva a senha cifrada e testa o login', decifrar(config.tabela_email_senha) == 'nova-senha' and 'nova-senha' not in config.tabela_email_senha
  and testar.call_args[0] == ('alcides@exemplo-teste.local', 'nova-senha', 'imap-vip.uni5.net') and 'o login funcionou' in r.content.decode())
with mock.patch.object(tabela_email, 'testar_conexao'):
    cb.post('/vendas/precos/importar/email/', {'acao': 'salvar', 'conta': 'alcides@exemplo-teste.local', 'senha': '', 'ativo': 'on'})
config.refresh_from_db()
t('senha em branco mantém a guardada', decifrar(config.tabela_email_senha) == 'nova-senha' and config.tabela_email_ativo and not config.tabela_email_avisar)
from webmail.cliente import SenhaRecusada
with mock.patch.object(tabela_email, 'testar_conexao', side_effect=SenhaRecusada('O servidor recusou o e-mail ou a senha.')):
    r = cb.post('/vendas/precos/importar/email/', {'acao': 'salvar', 'conta': 'alcides@exemplo-teste.local', 'senha': 'errada', 'ativo': 'on'}, follow=True)
t('login errado: salva e avisa', 'o login não funcionou' in r.content.decode())
config.tabela_email_estado = {}; config.save()
with mock.patch.object(tabela_email, '_entrar', return_value=caixa):
    r = cb.post('/vendas/precos/importar/email/', {'acao': 'buscar'}, follow=True)
t('"Buscar no e-mail agora" importa e mostra', 'produto(s)' in r.content.decode() and ImportacaoPrecos.objects.order_by('-id').first().usuario == boss)
comum = User.objects.create_user(username='zzt.comum', email='zzt.comum@exemplo-teste.local', password='S3nha!teste', first_name='C', last_name='ZZ')
cc = Client(); cc.force_login(comum)
r = cc.post('/vendas/precos/importar/email/', {'acao': 'salvar', 'conta': 'x@y.z', 'senha': 'a'})
config.refresh_from_db()
t('quem não é SUPERADMIN não mexe', config.tabela_email == 'alcides@exemplo-teste.local')

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
