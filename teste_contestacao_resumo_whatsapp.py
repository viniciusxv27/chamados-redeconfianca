"""Resumo do ciclo de contestação no WhatsApp. Banco descartável, envio por dublê, nada sai para a rede."""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-resumo-ciclo-'))
(PASTA / 'zz_resumo_ciclo_settings.py').write_text(f"""
from redeconfianca.settings import *              # noqa: F401,F403

DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3',
                          'NAME': r'{PASTA}/banco.sqlite3',
                          'OPTIONS': {{'timeout': 60, 'transaction_mode': 'IMMEDIATE'}}}}}}


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
BASE_URL = 'https://portal.exemplo-teste.local'
CACHES = {{
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-iacomp'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-iacomp-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_resumo_ciclo_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

import datetime as dt
import io
import socket
import threading
import time
import urllib.request

import pdfplumber
import requests
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from contestacao import resumo_pdf, resumo_whatsapp as resumo
from contestacao.models import (Contestation, ContestationHistory, DestinatarioResumoCiclo, EnvioResumoCiclo,
                                EnvioResumoDestinatario as Linha, ExclusionRecord, ExclusionSyncBatch,
                                LiberacaoContestacao, TipoBase)
from core import agendador_diario, evolution
from users.models import Sector, SystemConfig

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


def sem_rede(*a, **k):
    raise AssertionError('teste tentou sair para a rede')


for nome in ('get', 'post', 'request'):
    mock.patch.object(requests, nome, sem_rede).start()
mock.patch.object(urllib.request, 'urlopen', sem_rede).start()
mock.patch.object(socket.socket, 'connect', sem_rede).start()


class Duble:
    """O lugar do WhatsApp. ``falhar``: número -> detalhe; ``explodir``: números que levantam."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.enviados, self.falhar, self.explodir, self.segurar = [], {}, set(), None
        self.trava = threading.Lock()

    def __call__(self, numero, texto, *, timeout=10):
        if self.segurar is not None:
            self.segurar.wait(15)
        if numero in self.explodir:
            raise RuntimeError('estourou')
        if numero in self.falhar:
            return False, self.falhar[numero]
        with self.trava:
            self.enviados.append((numero, texto))
        return True, '{"ok": true}'


zap = Duble()
mock.patch.object(evolution, 'enviar_texto', zap).start()


def usuario(apelido, hierarquia='PADRAO', telefone='', **extra):
    return User.objects.create_user(
        username=f'zzr.{apelido}', email=f'zzr.{apelido}@exemplo-teste.local', password='S3nha!teste',
        first_name=apelido.capitalize(), last_name='Teste', hierarchy=hierarquia, phone=telefone, **extra)


chefe = usuario('chefe', 'SUPERADMIN', '27999990001')
chefe_django = usuario('raiz', 'PADRAO', '27999990002', is_superuser=True)
ana = usuario('ana', 'ADMIN', '27999990003')
bia = usuario('bia', 'SUPERVISOR', '27999990004')
caio = usuario('caio', 'ADMINISTRATIVO', '27999990005')
sem_fone = usuario('semfone', 'ADMIN', '')
fone_ruim = usuario('foneruim', 'ADMIN', '123')
gerente = usuario('gerente', 'SUPERVISOR', '27999990006')
outra = usuario('outra', 'PADRAO', '27999990007')
gestor_global = usuario('global', 'ADMIN', '27999990008')
SystemConfig.get_config().contestacao_global_managers.add(gestor_global)

NUM_ANA, NUM_BIA, NUM_CAIO = '5527999990003', '5527999990004', '5527999990005'


def cliente(user):
    c = Client()
    c.force_login(user)
    return c


cc, cg, cm, co, cb = cliente(chefe), cliente(gerente), cliente(gestor_global), cliente(outra), cliente(bia)


def limpar():
    zap.reset()
    EnvioResumoCiclo.objects.all().delete()
    DestinatarioResumoCiclo.objects.all().delete()
    Contestation.objects.all().delete()
    ExclusionRecord.objects.all().delete()
    ExclusionSyncBatch.objects.all().delete()
    ContestationHistory.objects.all().delete()
    LiberacaoContestacao.objects.all().delete()


def novo_ciclo(lojas=('NORTE SUL', 'MONTSERRAT', 'SEDE ADMINISTRATIVA'), vendas=2, janela_vencida=True, quando=None):
    lote = ExclusionSyncBatch.objects.create(record_type=TipoBase.EXCLUSAO, record_count=vendas * len(lojas))
    for loja in lojas:
        for i in range(vendas):
            ExclusionRecord.objects.create(
                sync_batch=lote, filial=loja, vendedor=f'Vendedor {i}', receita='100.50', pilar='Móvel',
                numero_venda=f'V{lote.pk}-{loja[:2]}-{i}', nome_cliente=f'Cliente {loja} {i}',
                cpf_cnpj='123.456.789-09', data_venda='01/10/2026', record_type=TipoBase.EXCLUSAO)
    h = ContestationHistory.objects.create(action='synced', user=chefe, extra_data={'sync_batch_id': lote.pk})
    data = quando or (timezone.now() - dt.timedelta(days=5 if janela_vencida else 1))
    ContestationHistory.objects.filter(pk=h.pk).update(created_at=data)
    ExclusionSyncBatch.objects.filter(pk=lote.pk).update(created_at=data)
    return ExclusionSyncBatch.objects.get(pk=lote.pk)


def contestar(lote, loja, indice=0, status='confirmed', com_de_acordo=False, requester=None, **extra):
    venda = ExclusionRecord.objects.filter(sync_batch=lote, filial=loja).order_by('pk')[indice]
    campos = {'reason': 'Venda foi feita certa.'}
    if status == 'confirmed':
        campos['payment_status'] = 'pending_payment'
    if com_de_acordo:
        campos['confirmed_at'] = timezone.now()
    campos.update(extra)
    c = Contestation(exclusion=venda, requester=requester or gerente, status=status, **campos)
    c.attachment.save('evidencia.png', ContentFile(b'zz'), save=False)
    c.save()
    return c


def escolher(*usuarios):
    for u in usuarios:
        DestinatarioResumoCiclo.objects.get_or_create(usuario=u)


def fechar_ciclo():
    """Um ciclo que já fechou: duas lojas, tudo respondido, e a sede sem contestação."""
    lote = novo_ciclo()
    contestar(lote, 'NORTE SUL', 0)
    contestar(lote, 'MONTSERRAT', 0, 'rejected', com_de_acordo=True)
    return lote


def esperar(condicao, segundos=15):
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if condicao():
            return True
        time.sleep(0.05)
    return False


def texto_do_pdf(conteudo):
    with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
        return '\n'.join(p.extract_text() or '' for p in pdf.pages), len(pdf.pages)


def ler_resposta(r):
    return b''.join(r.streaming_content) if r.streaming else r.content


print('armazenamento do teste é local')
t('PDF do resumo não vai para o MinIO no teste',
  isinstance(EnvioResumoCiclo._meta.get_field('pdf').storage, FileSystemStorage)
  and str(EnvioResumoCiclo._meta.get_field('pdf').storage.location).startswith(str(PASTA)),
  EnvioResumoCiclo._meta.get_field('pdf').storage)

print('a condição: quando o ciclo fecha')
limpar()
t('sem planilha: não fecha', resumo.estado_do_ciclo()['fechado'] is False)

lote = novo_ciclo(janela_vencida=False)
contestar(lote, 'NORTE SUL')
estado = resumo.estado_do_ciclo()
por_loja = {l['filial']: l for l in estado['lojas']}
t('janela aberta: não fecha', not estado['fechado'] and any('janela' in m for m in estado['motivos']), estado['motivos'])
t('janela aberta: loja sem contestação ainda pode enviar, não conta como pronta',
  not por_loja['SEDE ADMINISTRATIVA']['pronta'] and por_loja['SEDE ADMINISTRATIVA']['rotulo'] == 'Ainda pode enviar')
t('janela aberta: quem enviou tudo ainda pode mandar mais', not por_loja['NORTE SUL']['pronta'])

limpar()
novo_ciclo()
estado = resumo.estado_do_ciclo()
t('janela vencida sem nenhuma contestação: não fecha (nada a resumir)',
  not estado['fechado'] and any('Nenhuma contestação' in m for m in estado['motivos']))

limpar()
lote = novo_ciclo()
c1 = contestar(lote, 'NORTE SUL', 0, 'pending')
estado = resumo.estado_do_ciclo()
t('contestação sem análise do gestor: não fecha', not estado['fechado'] and estado['falta_analise'] == 1)

c1.status, c1.payment_status = 'confirmed', 'pending_payment'
c1.save()
t('todas aprovadas e janela vencida: fecha (aprovar vai direto para pagamento, sem de acordo)',
  resumo.estado_do_ciclo()['fechado'])

c2 = contestar(lote, 'MONTSERRAT', 0, 'rejected')
estado = resumo.estado_do_ciclo()
por_loja = {l['filial']: l for l in estado['lojas']}
t('rejeitada sem de acordo: não fecha', not estado['fechado'] and estado['falta_de_acordo'] == 1)
t('a tela sabe quem falta', por_loja['MONTSERRAT']['quem'] == ['Gerente Teste'] and not por_loja['MONTSERRAT']['pronta'],
  por_loja['MONTSERRAT'])
t('a loja sem pendência continua pronta mesmo com outra em aberto', por_loja['NORTE SUL']['pronta'])

c2.confirmed_at = timezone.now()
c2.save()
estado = resumo.estado_do_ciclo()
por_loja = {l['filial']: l for l in estado['lojas']}
t('de acordo dado: fecha', estado['fechado'], estado['motivos'])
t('loja sem contestação conta como pronta depois do prazo',
  por_loja['SEDE ADMINISTRATIVA']['pronta'] and por_loja['SEDE ADMINISTRATIVA']['rotulo'] == 'Pronta: sem contestação')

c2.status, c2.confirmed_at = 'accepted', None
c2.save()
t('aprovada no fluxo antigo esperando de acordo: não fecha', not resumo.estado_do_ciclo()['fechado'])
c2.status, c2.confirmed_at = 'denied', timezone.now()
c2.save()
t('negar também é resposta: fecha', resumo.estado_do_ciclo()['fechado'])

c2.status, c2.confirmed_at = 'rejected', None
c2.save()
t('volta a faltar o de acordo', not resumo.estado_do_ciclo()['fechado'])
novo = Contestation.objects.create(exclusion=c2.exclusion, requester=gerente, reason='De novo', status='confirmed',
                                   payment_status='pending_payment')
t('rejeitada que foi contestada de novo não trava o ciclo', resumo.estado_do_ciclo()['fechado'])
novo.delete()

lib = LiberacaoContestacao.objects.create(setor=Sector.objects.create(name='LOJA NORTE SUL'),
                                          prazo=timezone.now() + dt.timedelta(days=1))
c2.confirmed_at = timezone.now()
c2.save()
estado = resumo.estado_do_ciclo()
t('liberação avulsa em vigor: não fecha (a loja ainda pode enviar)',
  not estado['fechado'] and any('Liberação' in m for m in estado['motivos']), estado['motivos'])
lib.ativa = False
lib.save()
t('liberação revogada: fecha', resumo.estado_do_ciclo()['fechado'])

venda_valor = ExclusionRecord.objects.create(sync_batch=None, filial='NORTE SUL', vendedor='X', receita='1', pilar='P',
                                             record_type=TipoBase.PAGAMENTO)
Contestation.objects.create(exclusion=venda_valor, requester=gerente, reason='Valor', status='pending')
t('contestação de valores (outra base) não entra na conta', resumo.estado_do_ciclo()['fechado'])

limpar()
antigo = novo_ciclo(quando=timezone.now() - dt.timedelta(days=40))
contestar(antigo, 'NORTE SUL', 0, 'rejected')
atual = fechar_ciclo()
t('pendência de ciclo anterior não trava o atual', resumo.estado_do_ciclo()['fechado'])
t('o ciclo é o lote mais recente', resumo.estado_do_ciclo()['lote'].pk == atual.pk)

print('nasce sem ninguém escolhido')
limpar()
fechar_ciclo()
t('ciclo fechado e ninguém escolhido: não cria envio', resumo.verificar_e_enviar(em_thread=False) is None)
t('...e não manda nada', EnvioResumoCiclo.objects.count() == 0 and not zap.enviados)
html = cc.get('/contestacao/resumo-whatsapp/').content.decode()
t('a tela avisa que nada sai', 'nada é enviado até você marcar' in html)
agendador_resultado = resumo.tarefa_diaria()
t('a tarefa diária também não gasta o ciclo', EnvioResumoCiclo.objects.count() == 0, agendador_resultado)
escolher(ana)
inativa = usuario('inativa', 'ADMIN', '27999990009', is_active=False)
limpar()
fechar_ciclo()
escolher(inativa)
t('só gente inativa escolhida: não gasta o ciclo',
  resumo.verificar_e_enviar(em_thread=False) is None and EnvioResumoCiclo.objects.count() == 0)

print('o envio automático')
limpar()
lote = fechar_ciclo()
escolher(ana, bia)
envio = resumo.verificar_e_enviar(em_thread=False)
t('ciclo fechado: cria o envio', envio is not None and envio.origem == EnvioResumoCiclo.AUTOMATICO and envio.ciclo_fechado)
t('manda para os dois escolhidos', sorted(n for n, _ in zap.enviados) == [NUM_ANA, NUM_BIA], zap.enviados)
linhas = {l.usuario_id: l for l in envio.linhas.all()}
t('os dois ficam como enviado, com data e número',
  all(l.situacao == Linha.ENVIADO and l.enviado_em and l.numero for l in linhas.values()))
envio.refresh_from_db()
t('envio concluído e PDF guardado', envio.concluido_em and envio.pdf and envio.pdf_gerado_em)
mensagem = dict(zap.enviados)[NUM_ANA]
t('mensagem cumprimenta e traz totais', 'Olá, Ana!' in mensagem and '*2 contestação(ões)*' in mensagem
  and 'R$ 201,00' in mensagem, mensagem)
t('mensagem lista lojas e diz quem não contestou', 'NORTE SUL: 1' in mensagem and 'Sem contestação: SEDE ADMINISTRATIVA' in mensagem)
t('mensagem traz o link do PDF', f'https://portal.exemplo-teste.local/contestacao/resumo-whatsapp/pdf/{envio.pk}/' in mensagem)
t('mensagem sem cliente nem CPF (isso fica atrás do login)',
  'Cliente' not in mensagem and '123.456' not in mensagem and '456.789' not in mensagem)
t('ciclo normal diz que todas enviaram e sem aviso', 'Todas as lojas enviaram e os de acordo foram dados.' in mensagem)

print('uma vez só')
t('verificar de novo não manda de novo', resumo.verificar_e_enviar(em_thread=False) is None and len(zap.enviados) == 2)
resumo.tarefa_diaria()
t('a tarefa diária não manda de novo', len(zap.enviados) == 2 and EnvioResumoCiclo.objects.count() == 1)
try:
    with transaction.atomic():
        EnvioResumoCiclo.objects.create(lote=lote)
    t('o banco recusa um segundo envio para o mesmo ciclo', False)
except IntegrityError:
    t('o banco recusa um segundo envio para o mesmo ciclo', True)
t('reivindicar de novo não cria', resumo.reivindicar(lote)[1] is False)

limpar()
lote = fechar_ciclo()
escolher(ana, bia, caio)
barreira = threading.Barrier(8)
resultados, erros = [], []


def concorrente():
    try:
        barreira.wait(10)
        resultados.append(resumo.verificar_e_enviar(em_thread=False))
    except Exception as exc:  # noqa: BLE001
        erros.append(repr(exc))
    finally:
        connection.close()


linhas_thread = [threading.Thread(target=concorrente) for _ in range(8)]
[x.start() for x in linhas_thread]
[x.join(60) for x in linhas_thread]
t('8 verificações ao mesmo tempo: nenhuma quebra', not erros, erros)
t('8 verificações ao mesmo tempo: só uma ganha', sum(1 for r in resultados if r) == 1, resultados)
t('8 verificações ao mesmo tempo: um envio só', EnvioResumoCiclo.objects.count() == 1)
t('8 verificações ao mesmo tempo: cada pessoa recebe uma vez',
  sorted(n for n, _ in zap.enviados) == [NUM_ANA, NUM_BIA, NUM_CAIO], [n for n, _ in zap.enviados])

limpar()
lote = fechar_ciclo()
escolher(ana, bia, caio)
envio, _ = resumo.reivindicar(lote)
barreira2 = threading.Barrier(6)
erros = []


def processa_junto():
    try:
        barreira2.wait(10)
        resumo.processar(envio.pk)
    except Exception as exc:  # noqa: BLE001
        erros.append(repr(exc))
    finally:
        connection.close()


linhas_thread = [threading.Thread(target=processa_junto) for _ in range(6)]
[x.start() for x in linhas_thread]
[x.join(60) for x in linhas_thread]
t('thread, agendador e botão processando juntos: ninguém recebe duas vezes',
  not erros and sorted(n for n, _ in zap.enviados) == [NUM_ANA, NUM_BIA, NUM_CAIO], (erros, zap.enviados))

print('falha parcial')
limpar()
lote = fechar_ciclo()
escolher(ana, bia, caio, sem_fone, fone_ruim)
zap.falhar[NUM_BIA] = 'HTTP 500: instância desconectada'
zap.explodir.add(NUM_CAIO)
envio = resumo.verificar_e_enviar(em_thread=False)
linhas = {l.usuario_id: l for l in envio.linhas.all()}
t('quem falhou não impede os outros', linhas[ana.pk].situacao == Linha.ENVIADO)
t('só está como enviado quem foi de verdade',
  [n for n, _ in zap.enviados] == [NUM_ANA] and Linha.objects.filter(situacao=Linha.ENVIADO).count() == 1)
t('falha fica registrada com o motivo', linhas[bia.pk].situacao == Linha.FALHOU and 'desconectada' in linhas[bia.pk].detalhe)
t('exceção na integração vira falha', linhas[caio.pk].situacao == Linha.FALHOU and 'estourou' in linhas[caio.pk].detalhe)
t('sem telefone fica separado', linhas[sem_fone.pk].situacao == Linha.SEM_TELEFONE and not linhas[sem_fone.pk].enviado_em)
t('telefone inválido diz o porquê', linhas[fone_ruim.pk].situacao == Linha.SEM_TELEFONE and linhas[fone_ruim.pk].detalhe)
resumo.tarefa_diaria()
t('a tarefa diária não reenvia falha sozinha', len(zap.enviados) == 1)

zap.falhar.clear()
zap.explodir.clear()
User.objects.filter(pk=sem_fone.pk).update(phone='27999990010')
r = cc.post('/contestacao/resumo-whatsapp/enviar/', {'envio': envio.pk, 'alvo': 'faltantes'})
esperar(lambda: not Linha.objects.filter(situacao__in=[Linha.PENDENTE, Linha.ENVIANDO]).exists())
linhas = {l.usuario_id: l for l in envio.linhas.all()}
numeros = [n for n, _ in zap.enviados]
t('reenviar aos que faltam: quem falhou recebe', NUM_BIA in numeros and NUM_CAIO in numeros and r.status_code == 302)
t('reenviar aos que faltam: quem já recebeu não recebe de novo', numeros.count(NUM_ANA) == 1, numeros)
t('reenviar aos que faltam: telefone corrigido sai', linhas[sem_fone.pk].situacao == Linha.ENVIADO)
t('reenviar aos que faltam: telefone ainda ruim continua marcado', linhas[fone_ruim.pk].situacao == Linha.SEM_TELEFONE)
t('as tentativas ficam guardadas', linhas[bia.pk].tentativas == 2
  and [h['situacao'] for h in linhas[bia.pk].historico] == [Linha.FALHOU, Linha.ENVIADO], linhas[bia.pk].historico)
User.objects.filter(pk=sem_fone.pk).update(phone='')

print('recuperar envio interrompido')
limpar()
lote = fechar_ciclo()
escolher(ana, bia)
envio, criou = resumo.reivindicar(lote)   # a thread morreu antes de mandar qualquer coisa
t('ficou na fila', criou and envio.linhas.filter(situacao=Linha.PENDENTE).count() == 2 and not envio.pdf)
resposta = resumo.tarefa_diaria()
t('a tarefa diária retoma a fila', sorted(n for n, _ in zap.enviados) == [NUM_ANA, NUM_BIA], resposta)

limpar()
lote = fechar_ciclo()
escolher(ana)
envio, _ = resumo.reivindicar(lote)
r = cc.post('/contestacao/resumo-whatsapp/enviar/', {'envio': envio.pk, 'alvo': 'faltantes'})
esperar(lambda: len(zap.enviados) == 1 and not Linha.objects.exclude(situacao=Linha.ENVIADO).exists())
t('o botão retoma a fila', [n for n, _ in zap.enviados] == [NUM_ANA])

limpar()
lote = fechar_ciclo()
escolher(ana)
envio, _ = resumo.reivindicar(lote)
Linha.objects.filter(envio=envio).update(situacao=Linha.ENVIANDO, enviando_desde=timezone.now() - dt.timedelta(minutes=30))
resumo.tarefa_diaria()
linha = envio.linhas.get()
t('morreu no meio do envio: não reenvia sozinho (pode ter chegado)',
  not zap.enviados and linha.situacao == Linha.FALHOU and 'pode ter chegado' in linha.detalhe, linha.detalhe)
Linha.objects.filter(envio=envio).update(situacao=Linha.ENVIANDO, enviando_desde=timezone.now())
resumo.recuperar_interrompidos()
t('quem está enviando agora não é tocado', envio.linhas.get().situacao == Linha.ENVIANDO)

limpar()
lote = fechar_ciclo()
escolher(ana)
with mock.patch.object(resumo, 'gerar_pdf', side_effect=RuntimeError('sem reportlab')):
    envio = resumo.verificar_e_enviar(em_thread=False)
envio.refresh_from_db()
t('PDF quebrou: ninguém é avisado e o erro aparece', not zap.enviados and 'sem reportlab' in envio.erro
  and envio.linhas.get().situacao == Linha.PENDENTE)
html = cc.get('/contestacao/resumo-whatsapp/').content.decode()
t('a tela mostra o erro de preparo', 'Não conseguiu preparar o PDF' in html)
resumo.tarefa_diaria()
envio.refresh_from_db()
t('no dia seguinte sai e o erro some', [n for n, _ in zap.enviados] == [NUM_ANA] and not envio.erro and envio.pdf)

print('o gatilho: de acordo da loja não fica esperando o envio')
limpar()
lote = novo_ciclo()
contestar(lote, 'NORTE SUL', 0)
ultima = contestar(lote, 'MONTSERRAT', 0, 'rejected', requester=gerente)
escolher(ana)
ultima.save()
time.sleep(0.2)
t('processo de teste nunca reivindica um ciclo (outros teste_*.py rodam contra o banco real)',
  EnvioResumoCiclo.objects.count() == 0)

with mock.patch.object(resumo, 'processo_de_teste', return_value=False):
    zap.segurar = threading.Event()
    inicio = time.monotonic()
    r = cg.post(f'/contestacao/{ultima.pk}/confirmar/', {'confirmation_notes': 'ok'})
    gasto = time.monotonic() - inicio
    ultima.refresh_from_db()
    t('o de acordo responde sem esperar o WhatsApp', r.status_code == 302 and ultima.confirmed_at and not zap.enviados,
      (r.status_code, gasto))
    t('...e o WhatsApp sai depois, em segundo plano',
      esperar(lambda: Linha.objects.filter(situacao=Linha.ENVIANDO).exists()), list(Linha.objects.values('situacao')))
    t('...enquanto isso, ainda não está como enviado', not Linha.objects.filter(situacao=Linha.ENVIADO).exists())
    zap.segurar.set()
    t('o de acordo que fechou o ciclo dispara o resumo',
      esperar(lambda: Linha.objects.filter(situacao=Linha.ENVIADO).count() == 1) and [n for n, _ in zap.enviados] == [NUM_ANA])
    zap.segurar = None

    limpar()
    lote = novo_ciclo()
    ultima = contestar(lote, 'NORTE SUL', 0, 'pending')
    escolher(ana)
    cc.post(f'/contestacao/{ultima.pk}/aprovar/', {'review_notes': 'ok'})
    t('a análise do gestor também fecha o ciclo quando é o último passo',
      esperar(lambda: Linha.objects.filter(situacao=Linha.ENVIADO).count() == 1))

    limpar()
    lote = novo_ciclo()
    a = contestar(lote, 'NORTE SUL', 0, 'rejected')
    contestar(lote, 'MONTSERRAT', 0, 'rejected')
    escolher(ana)
    cg.post(f'/contestacao/{a.pk}/confirmar/', {})
    time.sleep(0.5)
    t('falta outra loja: de acordo não dispara', EnvioResumoCiclo.objects.count() == 0 and not zap.enviados)

    limpar()
    lote = novo_ciclo()
    ultima = contestar(lote, 'NORTE SUL', 0, 'rejected')
    cg.post(f'/contestacao/{ultima.pk}/negar/', {})
    time.sleep(0.3)
    t('sem ninguém escolhido o gatilho não faz nada', EnvioResumoCiclo.objects.count() == 0)

    limpar()
    lote = novo_ciclo()
    ultima = contestar(lote, 'NORTE SUL', 0, 'rejected')
    with mock.patch.object(resumo.DestinatarioResumoCiclo.objects, 'exists', side_effect=RuntimeError('falta migrate')):
        r = cg.post(f'/contestacao/{ultima.pk}/confirmar/', {})
    ultima.refresh_from_db()
    t('banco sem o migrate: o de acordo da loja segue funcionando', r.status_code == 302 and ultima.confirmed_at)

limpar()
fechar_ciclo()
escolher(ana)
t('a tarefa está no agendador diário', any(x[0] == 'contestacao_resumo_whatsapp' for x in agendador_diario.TAREFAS))
sucesso, detalhe = agendador_diario.executar('contestacao_resumo_whatsapp')
t('janela venceu sem ninguém salvar nada: o agendador percebe e manda',
  sucesso and [n for n, _ in zap.enviados] == [NUM_ANA], detalhe)

print('permissão')
limpar()
lote = fechar_ciclo()
escolher(bia)
envio = resumo.verificar_e_enviar(em_thread=False)
url = '/contestacao/resumo-whatsapp/'
t('anônimo vai para o login', Client().get(url).status_code == 302 and 'login' in Client().get(url)['Location'])
t('SUPERADMIN abre', cc.get(url).status_code == 200)
t('superuser do Django abre', cliente(chefe_django).get(url).status_code == 200)
for nome, c in (('ADMIN gestor global', cm), ('gerente', cg), ('PADRÃO', co)):
    r = c.get(url)
    t(f'{nome} não abre a tela', r.status_code == 302 and r['Location'] == '/contestacao/', (r.status_code, r.get('Location')))
    antes = set(DestinatarioResumoCiclo.objects.values_list('usuario_id', flat=True))
    c.post(url, {'usuarios': [outra.pk]})
    t(f'{nome} não muda a seleção', set(DestinatarioResumoCiclo.objects.values_list('usuario_id', flat=True)) == antes)
    qtd = len(zap.enviados)
    r = c.post('/contestacao/resumo-whatsapp/enviar/', {'envio': envio.pk, 'alvo': 'todos'})
    t(f'{nome} não reenvia', r.status_code == 302 and len(zap.enviados) == qtd and r['Location'] == '/contestacao/')
t('reenviar por GET não existe', cc.get('/contestacao/resumo-whatsapp/enviar/').status_code == 405)

pdf_url = f'/contestacao/resumo-whatsapp/pdf/{envio.pk}/'
t('PDF: anônimo vai para o login', Client().get(pdf_url).status_code == 302)
t('PDF: quem não recebeu não abre', co.get(pdf_url).status_code == 403 and cm.get(pdf_url).status_code == 403)
r = cb.get(pdf_url)
conteudo = ler_resposta(r)
t('PDF: quem recebeu abre', r.status_code == 200 and r['Content-Type'] == 'application/pdf' and conteudo.startswith(b'%PDF'))
r = cc.get(pdf_url)
ler_resposta(r)
t('PDF: SUPERADMIN abre', r.status_code == 200)
t('PDF de envio que não existe: 404', cc.get('/contestacao/resumo-whatsapp/pdf/999999/').status_code == 404)
html_lista = cc.get('/contestacao/').content.decode()
t('o botão aparece para o SUPERADMIN', '/contestacao/resumo-whatsapp/' in html_lista)
t('o botão não aparece para quem não é', '/contestacao/resumo-whatsapp/' not in cm.get('/contestacao/').content.decode())

print('o PDF e os dados de cliente')
t('CPF com máscara', resumo_pdf.mascarar_documento('123.456.789-09') == '***.456.789-**')
t('CPF só com dígitos', resumo_pdf.mascarar_documento('12345678909') == '***.456.789-**')
t('CPF que veio da planilha como número', resumo_pdf.mascarar_documento('12345678909.0') == '***.456.789-**')
t('CPF sem o zero da frente', resumo_pdf.mascarar_documento('1234567890') == '***.345.678-**')
t('CNPJ', resumo_pdf.mascarar_documento('12.345.678/0001-95') == '**.345.678/****-**')
t('vazio', resumo_pdf.mascarar_documento('') == '' and resumo_pdf.mascarar_documento(None) == '')
t('irreconhecível não vaza', resumo_pdf.mascarar_documento('1234567890123456789') == '***')
t('CPF digitado no motivo', resumo_pdf.mascarar_texto_livre('cliente 123.456.789-09 e 98765432100')
  == 'cliente ***.456.789-** e ***.654.321-**', resumo_pdf.mascarar_texto_livre('cliente 123.456.789-09 e 98765432100'))
t('CNPJ digitado no motivo', resumo_pdf.mascarar_texto_livre('12.345.678/0001-95') == '**.345.678/****-**')

limpar()
lote = novo_ciclo(lojas=('NORTE SUL', 'MONTSERRAT', 'SEDE ADMINISTRATIVA'), vendas=40)
vendas = ExclusionRecord.objects.filter(sync_batch=lote, filial='NORTE SUL').order_by('pk')
ExclusionRecord.objects.filter(pk=vendas[0].pk).update(nome_cliente='MARIA <b>& FILHOS</b>', cpf_cnpj='98765432100',
                                                       numero_venda='VENDA-ZZ1', vendedor='João Ação')
c = contestar(lote, 'NORTE SUL', 0, reason='Cliente 98765432100 <script> & emoji 🎉 ' + 'motivo longo ' * 40)
contestar(lote, 'NORTE SUL', 1, 'rejected', com_de_acordo=True)
sem_anexo = contestar(lote, 'NORTE SUL', 2, 'denied', confirmed_at=timezone.now())
Contestation.objects.filter(pk=sem_anexo.pk).update(attachment='')
contestar(lote, 'NORTE SUL', 3, payment_status='paid')
for i in range(40):
    contestar(lote, 'MONTSERRAT', i)
dados = resumo.dados_do_resumo(lote)
bruto = resumo.gerar_pdf(dados)
texto, paginas = texto_do_pdf(bruto)
achatado = ' '.join(texto.split())
t('PDF gerado, várias páginas', bruto.startswith(b'%PDF') and paginas >= 2, paginas)
t('PDF sem CPF inteiro', '987.654.321-00' not in texto and '98765432100' not in achatado
  and '123.456.789-09' not in texto and '12345678909' not in achatado)
t('PDF com CPF mascarado', '***.654.321-**' in achatado and '***.456.789-**' in achatado)
t('motivo com CPF digitado sai mascarado e curto', 'Cliente ***.654.321-**' in achatado and achatado.count('motivo longo') < 14,
  achatado.count('motivo longo'))
t('texto com caractere especial não quebra o PDF', 'MARIA <b>& FILHOS</b>' in achatado and '<script>' in achatado)
t('acento sai certo', 'João Ação' in achatado)
t('traz vendedor, venda, pilar, valor', 'VENDA-ZZ1' in achatado and 'Móvel' in achatado and 'R$ 100,50' in achatado)
t('traz os totais por loja', 'Totais por loja' in achatado and 'R$ 4.422,00' in achatado, achatado[:600])
t('loja sem contestação aparece como tal', 'Sem contestação no ciclo' in achatado and 'SEDE ADMINISTRATIVA' in achatado)
situacoes = {v['situacao'] for l in dados['lojas'] for v in l['vendas']}
t('situações', {'Aprovada, aguardando pagamento', 'Aprovada, paga', 'Negada pelo gerente'} <= situacoes, situacoes)
t('as situações saem no PDF', all(s in achatado for s in ('Aprovada, paga', 'Negada pelo', 'aguardando')))
t('situação de rejeitada com de acordo', 'de acordo dado' in achatado)
t('evidência sim e não', dados['totais']['sem_evidencia'] == 1 and dados['totais']['com_evidencia'] == 43, dados['totais'])
linha_sem = next(v for l in dados['lojas'] for v in l['vendas'] if v['situacao'] == 'Negada pelo gerente')
t('evidência da venda sem anexo é Não', linha_sem['evidencia'] == 'Não')
t('a loja se repete na virada de página', achatado.count('MONTSERRAT · 40 contestação') >= 2 or paginas < 3,
  achatado.count('MONTSERRAT · 40 contestação'))

limpar()
lote = novo_ciclo()
contestar(lote, 'NORTE SUL', 0, 'pending')
escolher(ana)
html = cc.get(url).content.decode()
t('ciclo aberto: botão avisa que não fechou', 'AINDA NÃO FECHOU' in html and 'return confirm(this.dataset.confirmar)' in html)
r = cc.post('/contestacao/resumo-whatsapp/enviar/', {})
esperar(lambda: Linha.objects.filter(situacao=Linha.ENVIADO).exists())
envio = EnvioResumoCiclo.objects.get()
t('envio manual antes de fechar fica marcado', envio.origem == EnvioResumoCiclo.MANUAL and not envio.ciclo_fechado
  and envio.disparado_por == chefe)
t('a mensagem avisa do que está em aberto', 'Atenção' in zap.enviados[0][1] and 'Todas as lojas' not in zap.enviados[0][1], zap.enviados)
texto, _ = texto_do_pdf(envio.pdf.open('rb').read())
t('o PDF avisa do que está em aberto', 'Atenção' in texto and 'Aguardando análise' in texto)

print('reenviar a todos')
Contestation.objects.update(status='confirmed', payment_status='pending_payment')
pdf_antigo = envio.pdf.name
resumo.verificar_e_enviar(em_thread=False)
t('depois do envio manual, fechar não manda de novo', len(zap.enviados) == 1 and EnvioResumoCiclo.objects.count() == 1)
r = cc.post('/contestacao/resumo-whatsapp/enviar/', {'envio': envio.pk, 'alvo': 'todos'})
time.sleep(0.5)
t('quem recebeu há pouco não recebe de novo (clique duplo)', len(zap.enviados) == 1, len(zap.enviados))
Linha.objects.filter(envio=envio).update(enviado_em=timezone.now() - dt.timedelta(hours=1))
escolher(bia)
r = cc.post('/contestacao/resumo-whatsapp/enviar/', {'envio': envio.pk, 'alvo': 'todos'})
esperar(lambda: len(zap.enviados) == 3 and not Linha.objects.filter(situacao__in=[Linha.PENDENTE, Linha.ENVIANDO]).exists())
numeros = [n for n, _ in zap.enviados]
t('manda de novo a todos, inclusive quem entrou depois', numeros.count(NUM_ANA) == 2 and numeros.count(NUM_BIA) == 1, numeros)
envio.refresh_from_db()
t('refez o PDF com os dados de agora', envio.pdf.name != pdf_antigo and 'Todas as lojas' in zap.enviados[-1][1],
  zap.enviados[-1][1])
t('o PDF antigo é apagado', not envio.pdf.storage.exists(pdf_antigo))
Linha.objects.filter(envio=envio).update(enviado_em=timezone.now() - dt.timedelta(hours=1))
linha_ana = envio.linhas.get(usuario=ana)
cc.post('/contestacao/resumo-whatsapp/enviar/', {'envio': envio.pk, 'linha': linha_ana.pk})
esperar(lambda: len(zap.enviados) == 4)
numeros = [n for n, _ in zap.enviados]
t('reenviar uma pessoa só', numeros.count(NUM_ANA) == 3 and numeros.count(NUM_BIA) == 1, numeros)
DestinatarioResumoCiclo.objects.filter(usuario=bia).delete()
Linha.objects.filter(envio=envio, usuario=bia).update(situacao=Linha.FALHOU)
cc.post('/contestacao/resumo-whatsapp/enviar/', {'envio': envio.pk, 'alvo': 'faltantes'})
time.sleep(0.5)
t('quem saiu da seleção não recebe no reenvio', [n for n, _ in zap.enviados].count(NUM_BIA) == 1)
User.objects.filter(pk=ana.pk).update(is_active=False)
Linha.objects.filter(envio=envio, usuario=ana).update(situacao=Linha.PENDENTE)
resumo.processar(envio.pk)
t('desligado no meio do caminho não recebe', envio.linhas.get(usuario=ana).situacao == Linha.FALHOU)
User.objects.filter(pk=ana.pk).update(is_active=True)

print('a tela')
limpar()
lote = fechar_ciclo()
Contestation.objects.filter(exclusion__filial='MONTSERRAT').update(confirmed_at=None)
r = cc.post(url, {'usuarios': [ana.pk, sem_fone.pk, fone_ruim.pk, inativa.pk, 999999, 'lixo']})
t('salvar a seleção', r.status_code == 302
  and set(DestinatarioResumoCiclo.objects.values_list('usuario_id', flat=True)) == {ana.pk, sem_fone.pk, fone_ruim.pk})
t('quem salvou fica registrado', DestinatarioResumoCiclo.objects.filter(incluido_por=chefe).count() == 3)
html = cc.get(url).content.decode()
t('mostra quem está sem telefone', 'não vão receber até corrigir o cadastro' in html and 'Semfone Teste' in html
  and 'Foneruim Teste' in html and 'poucos dígitos' in html)
t('mostra a loja e quem deve o de acordo', 'MONTSERRAT' in html and '1 sem de acordo (Gerente Teste)' in html, html[:0])
t('não vaza comentário de template', '{#' not in html and '{%' not in html)
t('nada foi enviado ao salvar a seleção', not zap.enviados and EnvioResumoCiclo.objects.count() == 0)
Contestation.objects.filter(exclusion__filial='MONTSERRAT').update(confirmed_at=timezone.now())
html = cc.get(url).content.decode()
t('ciclo fechado aguardando: a tela explica quando sai', '8h30' in html)
envio = resumo.verificar_e_enviar(em_thread=False)
html = cc.get(url).content.decode()
t('histórico: quem, quando, situação', 'Ana Teste' in html and 'Recebeu em' in html and 'Sem telefone' in html
  and 'Ciclo da planilha de' in html and 'Automático' in html)
t('histórico: botão de reenviar com confirmação', 'Reenviar para quem falta' in html and 'Refazer e mandar a todos' in html
  and 'Reenviar o resumo para' in html)
cc.post(url, {})
t('desmarcar todos esvazia a seleção', DestinatarioResumoCiclo.objects.count() == 0)
html = cc.get(url).content.decode()
t('quem saiu da seleção continua no histórico', 'Ana Teste' in html and envio.linhas.count() == 3)

print()
print(f'{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
