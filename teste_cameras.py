"""Câmeras das lojas (04/11/2026): portal e regras do agente. Banco descartável, senha fictícia; nada sai para a rede."""
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-cameras-'))
(PASTA / 'zz_cameras_settings.py').write_text(f"""
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
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_cameras_settings'
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
import re
from unittest.mock import patch

import openpyxl
import requests
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from cameras.importacao import importar_planilha
from cameras.models import AcessoCamera, AlertaCamera, Canal, ConfigCameras, Gravador
from cameras import ia, servicos

ia.EM_THREAD = False   # a análise de IA roda na hora (sem chave no teste: fica registrado o erro)

sys.path.insert(0, str(pathlib.Path.cwd() / 'agente_cameras'))
from agente import Portal, endereco_rtsp, loja_aberta  # noqa: E402
from regras import Analisador  # noqa: E402

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

SENHA = 'S3nha-ficticia!@#'
chefe = User.objects.create_user(username='zzc.chefe', email='zzc.chefe@exemplo-teste.local', password='S3nha!teste', first_name='Che', hierarchy='SUPERADMIN', is_superuser=True)
comum = User.objects.create_user(username='zzc.comum', email='zzc.comum@exemplo-teste.local', password='S3nha!teste', first_name='Com', hierarchy='PADRAO')
cs = Client(); cs.force_login(chefe)
cc = Client(); cc.force_login(comum)
anon = Client(enforce_csrf_checks=True)

print('importação')
wb = openpyxl.Workbook(); ws = wb.active
ws.append(['Loja', 'Coordenados', 'app', 'Login', 'Serial', 'Senha'])
ws.append(['LOJA ZZ', 'Fulana', 'hellosecurity\nMotorola', 'admin', 'ABC123', SENHA])
ws.append(['SEM GRAVADOR', 'Fulana', 0, 0, 0, 0])
b = io.BytesIO(); wb.save(b)
xlsx = SimpleUploadedFile('c.xlsx', b.getvalue())
r = cs.post('/cameras/configuracao/importar/', {'planilha': xlsx}, follow=True)
g = Gravador.objects.get(loja='LOJA ZZ')
t('importa e não mostra a senha', r.status_code == 200 and Gravador.objects.count() == 2 and SENHA not in r.content.decode(), r.status_code)
t('senha fica cifrada e volta certa', SENHA not in g.senha_cifrada and g.senha == SENHA)
t('aplicativo com quebra de linha fica legível', g.aplicativo == 'hellosecurity Motorola', g.aplicativo)
t('cria 4 câmeras', g.canais.count() == 4)
sem = Gravador.objects.get(loja='SEM GRAVADOR')
t('linha com 0 vira loja sem gravador, sem senha', not sem.tem_equipamento and not sem.senha_cifrada and not sem.serial and sem.canais.count() == 0)
importar_planilha(io.BytesIO(b.getvalue()))
t('importar de novo atualiza, não duplica', Gravador.objects.count() == 2)
try:
    w2 = openpyxl.Workbook(); w2.active.append(['Loja', 'Outra']); b2 = io.BytesIO(); w2.save(b2); b2.seek(0)
    importar_planilha(b2); falhou = False
except ValueError:
    falhou = True
t('planilha sem as colunas é recusada', falhou)

print('permissões')
for u in ('/cameras/', '/cameras/alertas/', '/cameras/configuracao/', f'/cameras/loja/{g.id}/', '/cameras/fotos/', '/cameras/estado/'):
    t(f'colaborador comum não entra em {u}', cc.get(u).status_code == 302)
    t(f'sem login não entra em {u}', anon.get(u).status_code == 302)
hc = cc.get('/').content.decode()
t('menu e aviso não aparecem para quem não é SUPERADMIN', '/cameras/' not in hc)
for u in ('/cameras/', '/cameras/alertas/', '/cameras/configuracao/', f'/cameras/loja/{g.id}/', f'/cameras/configuracao/{g.id}/'):
    r = cs.get(u)
    t(f'SUPERADMIN abre {u}', r.status_code == 200 and SENHA not in r.content.decode(), r.status_code)
t('módulo nasce desligado', not ConfigCameras.atual().ativo and 'Módulo desligado' in cs.get('/cameras/').content.decode())
t('acesso fica registrado', AcessoCamera.objects.filter(user=chefe, acao='ver_mosaico').exists())

print('agente')
token = None
r = cs.post(f'/cameras/configuracao/{g.id}/token/', follow=True)
h = r.content.decode()
achado = re.search(r'data-token>([^<]+)<', h)
token = achado.group(1) if achado else ''
t('token aparece uma vez', token and 'data-token' not in cs.get('/cameras/configuracao/').content.decode())
g.refresh_from_db()
t('no banco só o resumo', token not in g.token_hash and g.token_hash == Gravador.resumo(token))
ag = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
t('sem token: 401', anon.post('/cameras/agente/sinal/').status_code == 401)
t('token errado: 401', anon.post('/cameras/agente/sinal/', HTTP_AUTHORIZATION='Bearer x').status_code == 401)
t('GET recusado', anon.get('/cameras/agente/sinal/', **ag).status_code == 405)
r = anon.post('/cameras/agente/sinal/', **ag)
t('desligado: não entrega senha nem configuração', r.json() == {'ativo': False, 'proximo_sinal': 30} and SENHA not in r.content.decode(), r.content)
r = anon.post('/cameras/agente/alerta/', {'canal': 1, 'tipo': 'FORA_HORARIO', 'confianca': 80}, **ag)
t('desligado: alerta não entra', not AlertaCamera.objects.exists())
cs.post('/cameras/configuracao/', {'ativo': '1'})
t('liga pelo portal', ConfigCameras.atual().ativo)
Gravador.objects.filter(pk=g.pk).update(ip_local='192.168.0.9')
r = anon.post('/cameras/agente/sinal/', {'versao': '1.0'}, **ag).json()
g.refresh_from_db()
t('ligado: entrega a configuração da loja', r['ativo'] and r['gravador']['senha'] == SENHA and r['gravador']['ip'] == '192.168.0.9' and len(r['canais']) == 4, r)
t('registra o contato e fica online', g.online and g.situacao == 'online' and g.versao_agente == '1.0')
outro = Gravador.objects.create(loja='OUTRA'); token2 = outro.gerar_token(); outro.definir_senha('outra'); outro.save()
r2 = anon.post('/cameras/agente/sinal/', HTTP_AUTHORIZATION=f'Bearer {token2}').json()
t('cada token só vê a própria loja', r2['gravador']['senha'] == 'outra')
ag_sem = sem.gerar_token()
t('loja sem gravador não usa token', anon.post('/cameras/agente/sinal/', HTTP_AUTHORIZATION=f'Bearer {ag_sem}').status_code == 401)

print('fotos')
from django.core.cache import cache
cache.clear()
r = anon.post('/cameras/agente/sinal/', **ag).json()
t('ninguém olhando: não pede foto', r['fotos'] == [] and r['foco'] is None and r['proximo_sinal'] == 30, r)
c1 = g.canais.get(numero=1); c2 = g.canais.get(numero=2)
cs.get('/cameras/')
r = anon.post('/cameras/agente/sinal/', **ag).json()
t('mosaico aberto: pede só a principal', r['fotos'] == [1], r['fotos'])
cs.get(f'/cameras/loja/{g.id}/')
r = anon.post('/cameras/agente/sinal/', **ag).json()
t('loja aberta: pede todas', r['fotos'] == [1, 2, 3, 4], r['fotos'])
cs.get(f'/cameras/fotos/?loja={g.id}&canais={c2.id}&foco=2')
r = anon.post('/cameras/agente/sinal/', **ag).json()
t('câmera ampliada: ritmo rápido', r['foco'] == 2 and r['proximo_sinal'] == 3, r)
jpg = b'\xff\xd8\xff' + b'x' * 100
r = anon.post('/cameras/agente/fotos/', {'canal_1': SimpleUploadedFile('a.jpg', jpg, 'image/jpeg'), 'canal_99': SimpleUploadedFile('b.jpg', jpg, 'image/jpeg')}, **ag).json()
t('guarda só canal da própria loja', r['guardadas'] == 1, r)
outro_canal = Canal.objects.create(gravador=outro, numero=1)
d = cs.get(f'/cameras/fotos/?canais={c1.id},{c2.id},{outro_canal.id}').json()['fotos']
t('foto volta em uma resposta só', list(d) == [str(c1.id)] and d[str(c1.id)]['b64'], d.keys())
t('não reenvia foto que a tela já tem', cs.get(f'/cameras/fotos/?canais={c1.id}&desde={d[str(c1.id)]["em"]}').json()['fotos'] == {})
t('parâmetro inválido: 400', cs.get('/cameras/fotos/?canais=abc').status_code == 400)

print('alertas')
foto = SimpleUploadedFile('a.jpg', jpg, 'image/jpeg')
r = anon.post('/cameras/agente/alerta/', {'canal': 1, 'tipo': 'ITEM_SUMIU', 'confianca': 70, 'motivo': 'Aparelho saiu', 'foto': foto}, **ag)
a = AlertaCamera.objects.get()
t('alerta entra com foto', r.status_code == 200 and a.canal == c1 and a.status == 'NOVO' and a.foto, r.content)
t('foto com nome impossível de adivinhar', re.search(r'[0-9a-f]{32}\.jpg$', a.foto.name), a.foto.name)
anon.post('/cameras/agente/alerta/', {'canal': 1, 'tipo': 'ITEM_SUMIU', 'confianca': 90}, **ag)
a.refresh_from_db()
t('repetição soma no mesmo alerta', AlertaCamera.objects.count() == 1 and a.repeticoes == 2 and a.confianca == 90)
t('tipo desconhecido: 400', anon.post('/cameras/agente/alerta/', {'canal': 1, 'tipo': 'X'}, **ag).status_code == 400)
hh = cs.get('/').content.decode()
t('aviso vermelho na home para SUPERADMIN', 'camAvisoDashboard' in hh and 'data-novos="1"' in hh)
t('contagem no estado', cs.get('/cameras/estado/').json()['novos'] == 1)
t('foto do alerta só com login de SUPERADMIN', cs.get(f'/cameras/alertas/{a.id}/foto/').status_code == 200 and cc.get(f'/cameras/alertas/{a.id}/foto/').status_code == 302)
r = cs.post(f'/cameras/alertas/{a.id}/', {'status': 'FALSO', 'observacao': 'Funcionário guardou'})
a.refresh_from_db()
t('falso alarme sem o motivo não grava (é com ele que a análise aprende)', a.status == 'NOVO' and r['Location'] == f'/cameras/alertas/{a.id}/')
r = cs.post(f'/cameras/alertas/{a.id}/', {'status': 'FALSO', 'motivo_falso': 'FUNCIONARIO', 'observacao': 'Funcionário guardou'})
a.refresh_from_db()
t('tratar registra quem, quando e o motivo', a.status == 'FALSO' and a.tratado_por == chefe and a.tratado_em and a.observacao
  and a.motivo_falso == 'FUNCIONARIO')
t('tratado sai dos abertos', str(a.id) not in re.findall(r'/cameras/alertas/(\d+)/"', cs.get('/cameras/alertas/').content.decode()))
t('filtro "todos" traz', f'/cameras/alertas/{a.id}/' in cs.get('/cameras/alertas/?status=todos').content.decode())
for i in range(servicos.ALERTAS_POR_HORA):
    AlertaCamera.objects.create(gravador=g, tipo='PERMANENCIA')
t('limite por hora: 429', anon.post('/cameras/agente/alerta/', {'canal': 2, 'tipo': 'FORA_HORARIO'}, **ag).status_code == 429)
AlertaCamera.objects.filter(tipo='PERMANENCIA').delete()

print('áreas e edição')
import json as _j
r = cs.post(f'/cameras/canal/{c1.id}/salvar/', _j.dumps({'nome': 'Entrada', 'ia_ativa': False, 'zonas': [{'nome': 'Mostruário', 'x': .1, 'y': .1, 'w': .3, 'h': .3}, {'x': 'a'}, {'x': 2, 'y': 0, 'w': 0, 'h': 0}]}), content_type='application/json')
c1.refresh_from_db()
t('salva área válida e descarta as ruins', len(c1.zonas) == 1 and c1.nome == 'Entrada' and not c1.ia_ativa, c1.zonas)
t('colaborador comum não altera', cc.post(f'/cameras/canal/{c1.id}/salvar/', '{}', content_type='application/json').status_code == 302)
base = {'loja': 'LOJA ZZ', 'caminho_rtsp': '/x?c={canal}', 'porta_rtsp': 554, 'canais': 2, 'tem_equipamento': '1', 'abre_as': '09:00', 'fecha_as': '18:00', 'usuario': 'admin', 'senha': ''}
cs.post(f'/cameras/configuracao/{g.id}/', base)
g.refresh_from_db()
t('senha em branco mantém a atual', g.senha == SENHA)
t('reduzir câmeras desativa o resto', g.canais.filter(ativo=True).count() == 2)
cs.post(f'/cameras/configuracao/{g.id}/', {**base, 'senha': 'nova'})
g.refresh_from_db()
t('senha nova substitui', g.senha == 'nova')

print('agente: funções')
t('senha com símbolo não quebra o endereço', endereco_rtsp({'usuario': 'admin', 'senha': 'a@b!/c', 'ip': '10.0.0.1', 'porta': 554, 'caminho': '/cam?channel={canal}'}, 3) == 'rtsp://admin:a%40b%21%2Fc@10.0.0.1:554/cam?channel=3')
try:
    Portal('http://portal.exemplo', 't'); recusou = False
except SystemExit:
    recusou = True
t('agente recusa portal sem HTTPS', recusou)
t('horário da loja', loja_aberta('08:00', '20:00', dt.datetime(2026, 1, 1, 19, 59)) and not loja_aberta('08:00', '20:00', dt.datetime(2026, 1, 1, 20, 0)))

print('regras da análise')
Z = [{'nome': 'Mostruário', 'x': .2, 'y': .2, 'w': .4, 'h': .4}]
PES = {'classe': 'person', 'conf': .9, 'caixa': (.25, .25, .2, .3)}
FORA = {'classe': 'person', 'conf': .9, 'caixa': (.8, .7, .1, .2)}
APA = {'classe': 'cell phone', 'conf': .6, 'caixa': (.3, .3, .05, .05)}


def rodar(an, quadros):
    saida = []
    for tempo, det, aberta in quadros:
        saida += [(tempo, e.tipo) for e in an.analisar(det, tempo, aberta)]
    return saida


t('loja aberta com gente: nada', rodar(Analisador(Z), [(i, [FORA], True) for i in range(20)]) == [])
s = rodar(Analisador(Z), [(i, [FORA], False) for i in range(5)])
t('pessoa com a loja fechada: alerta no 3º quadro, sem repetir', s == [(2, 'FORA_HORARIO')], s)
t('um quadro só com a loja fechada não alerta', rodar(Analisador(Z), [(0, [FORA], False), (1, [], False), (2, [FORA], False), (3, [], False)]) == [])
t('detecção fraca não conta', rodar(Analisador(Z), [(i, [{**FORA, 'conf': .3}], False) for i in range(6)]) == [])
s = rodar(Analisador(Z), [(i * 10, [PES], True) for i in range(40)])
t('permanência longa na área', s[0][1] == 'PERMANENCIA' and s[0][0] == 300 and len(s) == 1, s)
t('passar rápido pela área: nada', rodar(Analisador(Z), [(i * 10, [PES] if i % 3 else [], True) for i in range(60)]) == [])
firme = [(i, [APA], True) for i in range(40)]
s = rodar(Analisador(Z), firme + [(40 + i, [PES], True) for i in range(5)] + [(45 + i, [], True) for i in range(30)])
t('aparelho firme some depois de pessoa passar: alerta', [x[1] for x in s] == ['ITEM_SUMIU'], s)
t('alerta só depois da pessoa sair e do tempo de espera', s[0][0] == 64, s)
s = rodar(Analisador(Z), firme + [(40 + i, [PES], True) for i in range(5)] + [(45 + i, [APA], True) for i in range(30)])
t('pessoa cobriu e o aparelho voltou: nada', s == [], s)
s = rodar(Analisador(Z), firme + [(40 + i, [], True) for i in range(120)])
t('aparelho some sem ninguém por perto: falha da detecção, nada', s == [], s)
s = rodar(Analisador(Z), [(i, [APA], True) for i in range(10)] + [(10, [PES], True)] + [(11 + i, [], True) for i in range(60)])
t('aparelho que acabou de aparecer não é do mostruário: nada', s == [], s)
s = rodar(Analisador([]), [(i, [APA], True) for i in range(40)] + [(40, [PES], True)] + [(41 + i, [], True) for i in range(60)])
t('sem área marcada: aparelho não alerta', s == [], s)
s = rodar(Analisador(Z), [(i, [{**APA, 'caixa': (.8, .8, .05, .05)}], True) for i in range(40)] + [(40, [FORA], True)] + [(41 + i, [], True) for i in range(40)])
t('aparelho fora da área (na mão de alguém): nada', s == [], s)

print('instalador e descoberta no portal')
cache.clear()
from django.core.management import call_command
g3 = Gravador.objects.create(loja='LOJA NOVA Ação'); g3.definir_senha(SENHA); g3.save()
r = cs.post(f'/cameras/configuracao/{g3.id}/instalador/', follow=True)
t('sem instalador publicado: avisa', 'ainda não foi publicado' in r.content.decode())
exe = pathlib.Path(tempfile.mkdtemp()) / 'AgenteCameras.exe'
exe.write_bytes(b'MZ' + b'0' * 100)
call_command('publicar_instalador_cameras', str(exe), verbosity=0)
primeiro = ConfigCameras.atual().instalador_arquivo
t('publica o exe', primeiro.startswith('cameras/instalador/') and default_storage.exists(primeiro))
falso = pathlib.Path(tempfile.mkdtemp()) / 'x.exe'; falso.write_bytes(b'nada')
try:
    call_command('publicar_instalador_cameras', str(falso), verbosity=0); recusou = False
except Exception:
    recusou = True
t('recusa o que não é exe do Windows', recusou)
t('colaborador comum não baixa', cc.post(f'/cameras/configuracao/{g3.id}/instalador/').status_code == 302 and not Gravador.objects.get(pk=g3.id).codigo_hash)
t('GET não gera código', cs.get(f'/cameras/configuracao/{g3.id}/instalador/').status_code == 405)
t('sem download para loja sem gravador', cs.post(f'/cameras/configuracao/{sem.id}/instalador/').status_code == 404)
r = cs.post(f'/cameras/configuracao/{g3.id}/instalador/')
nome_baixado = re.search(r'filename="([^"]+)"', r['Content-Disposition']).group(1)
g3.refresh_from_db()
t('baixa com o nome da loja e o código', re.fullmatch(r'Instalar-Cameras-LOJA-NOVA-ACAO-[A-Z0-9]{12}\.exe', nome_baixado) and b''.join(r.streaming_content).startswith(b'MZ'), nome_baixado)
t('no banco só o resumo do código', nome_baixado.split('-')[-1][:12] not in g3.codigo_hash and not g3.token_hash)
t('download fica registrado', AcessoCamera.objects.filter(acao='baixar_instalador', gravador=g3).exists())
from cameras.servicos import nome_do_instalador
t('nome só com letras e números, sem acento', nome_do_instalador(Gravador(loja='SÃO JOSÉ / CENTRO'), 'ABC') == 'Instalar-Cameras-SAO-JOSE-CENTRO-ABC.exe')
codigo_real = nome_baixado.split('-')[-1][:12]
t('código errado não ativa', anon.post('/cameras/agente/ativar/', {'codigo': 'ZZZZZZZZZZZZ'}).status_code == 403)
t('sem código não ativa', anon.post('/cameras/agente/ativar/', {}).status_code == 403 and anon.get('/cameras/agente/ativar/').status_code == 405)
Gravador.objects.filter(pk=g3.pk).update(codigo_validade=timezone.now() - dt.timedelta(minutes=1))
t('código vencido não ativa', anon.post('/cameras/agente/ativar/', {'codigo': codigo_real}).status_code == 403)
Gravador.objects.filter(pk=g3.pk).update(codigo_validade=timezone.now() + dt.timedelta(days=1))
r = anon.post('/cameras/agente/ativar/', {'codigo': codigo_real.lower()})
cfg = {'token': r.json()['token']}
g3.refresh_from_db()
t('código certo vira token da loja', r.status_code == 200 and r.json()['loja'] == 'LOJA NOVA Ação' and g3.token_hash == Gravador.resumo(cfg['token']))
t('resposta sem a senha do gravador', SENHA not in r.content.decode())
ag3 = {'HTTP_AUTHORIZATION': 'Bearer ' + cfg['token']}
hp = cs.get('/cameras/').content.decode()
t('tela diz por que está sem imagem', 'data-faixa-sem-agente' in hp and 'Agente não instalado' in hp)
r = anon.post('/cameras/agente/sinal/', **ag3).json()
t('agente recebe o serial para procurar', r['gravador']['serial'] == '' and r['gravador']['ip'] == '')
anon.post('/cameras/agente/descoberta/', {'erro': 'O gravador em 192.168.0.9 recusou o usuário e a senha do cadastro.'}, **ag3)
g3.refresh_from_db()
t('erro do agente chega no cadastro', 'recusou' in g3.erro_agente and 'recusou' in cs.get('/cameras/configuracao/').content.decode())
for ruim, motivo in (({'ip': '8.8.8.8', 'caminho': '/a?c={canal}', 'canais': '1'}, 'IP público'), ({'ip': 'x', 'caminho': '/a?c={canal}', 'canais': '1'}, 'IP inválido'),
                     ({'ip': '192.168.0.9', 'caminho': 'http://mal/{canal}', 'canais': '1'}, 'caminho estranho'), ({'ip': '192.168.0.9', 'caminho': '/a', 'canais': '1'}, 'sem canal'),
                     ({'ip': '192.168.0.9', 'caminho': '/a?c={canal}', 'canais': ''}, 'sem câmeras'), ({'ip': '192.168.0.9', 'caminho': '/a?c={canal}', 'canais': '1,99'}, 'canal absurdo')):
    t(f'descoberta recusa {motivo}', anon.post('/cameras/agente/descoberta/', ruim, **ag3).status_code == 400 and not Gravador.objects.get(pk=g3.id).ip_local)
t('sem token não registra', anon.post('/cameras/agente/descoberta/', {'ip': '192.168.0.9'}).status_code == 401)
r = anon.post('/cameras/agente/descoberta/', {'ip': '192.168.0.50', 'porta': '554', 'caminho': '/cam?c={canal}', 'canais': '1,2,3,4,5,6', 'serial': 'DIFERENTE', 'modelo': 'NVD'}, **ag3)
g3.refresh_from_db()
t('descoberta preenche tudo sozinha', r.status_code == 200 and g3.ip_local == '192.168.0.50' and g3.caminho_rtsp == '/cam?c={canal}' and g3.canais.filter(ativo=True).count() == 6 and not g3.erro_agente)
t('apaga o erro antigo e avisa serial diferente', g3.serial == '' and not g3.serial_diferente)
Gravador.objects.filter(pk=g3.pk).update(serial='CADASTRO'); g3.refresh_from_db()
t('serial do cadastro diferente do achado aparece', g3.serial_diferente and 'data-serial-diferente' in cs.get('/cameras/configuracao/').content.decode())
anon.post('/cameras/agente/descoberta/', {'ip': '192.168.0.50', 'caminho': '/cam?c={canal}', 'canais': '1,2'}, **ag3)
t('câmera que sumiu sai da tela', g3.canais.filter(ativo=True).count() == 2)
r = anon.post('/cameras/agente/sinal/', **ag3).json()
t('próximo sinal já traz o gravador achado', r['gravador']['ip'] == '192.168.0.50' and [c['numero'] for c in r['canais']] == [1, 2])
ConfigCameras.objects.update(ativo=False)
anon.post('/cameras/agente/descoberta/', {'ip': '192.168.0.77', 'caminho': '/cam?c={canal}', 'canais': '1'}, **ag3)
t('módulo desligado não grava descoberta', Gravador.objects.get(pk=g3.id).ip_local == '192.168.0.50')
ConfigCameras.objects.update(ativo=True)

print('instalador: aviso ao portal')
gi = Gravador.objects.create(loja='LOJA INSTALANDO'); ti = gi.gerar_token(); agi = {'HTTP_AUTHORIZATION': f'Bearer {ti}'}
r = anon.post('/cameras/agente/descoberta/', 'erro=' + 'Instalador%3A%20falhou%3A%20sem%20permiss%C3%A3o', content_type='application/x-www-form-urlencoded', **agi)
gi.refresh_from_db()
t('falha do instalador chega ao portal, com acento', r.status_code == 200 and gi.erro_agente == 'Instalador: falhou: sem permissão', gi.erro_agente)
t('e aparece na linha da loja', 'Instalador: falhou' in cs.get('/cameras/configuracao/').content.decode())
anon.post('/cameras/agente/sinal/', {'versao': '1.2'}, **agi)
gi.refresh_from_db()
t('agente respondeu: aviso do instalador some', gi.erro_agente == '' and gi.ultimo_contato)
Gravador.objects.filter(pk=gi.pk).update(erro_agente='O gravador em 1.1.1.1 recusou o usuário')
anon.post('/cameras/agente/sinal/', **agi)
t('erro do próprio agente continua', 'recusou' in Gravador.objects.get(pk=gi.pk).erro_agente)

print('agente: procura do gravador (servidor RTSP falso, em localhost)')
import hashlib as _h
import socket as _s
import threading as _t
import descoberta as dsc
from agente import Camera, precisa_descobrir


def md5(x):
    return _h.md5(x.encode()).hexdigest()


class FalsoGravador:
    """Fala o suficiente de RTSP com Digest. Conta o que recebeu, para provar que a senha não passa aberta."""

    def __init__(self, usuario='admin', senha='s3nha', caminho='/cam/realmonitor?channel={canal}&subtype=1', canais=4, basic=False):
        self.usuario, self.senha, self.caminho, self.canais, self.basic = usuario, senha, caminho, canais, basic
        self.recebido = ''
        self.sock = _s.socket(); self.sock.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1); self.sock.bind(('127.0.0.1', 0)); self.sock.listen(32)
        self.porta = self.sock.getsockname()[1]
        _t.Thread(target=self.rodar, daemon=True).start()

    def rodar(self):
        while True:
            try:
                con, _ = self.sock.accept()
            except OSError:
                return
            _t.Thread(target=self.atender, args=(con,), daemon=True).start()

    def atender(self, con):
        while True:
            dados = con.recv(4096).decode('latin-1')
            if not dados:
                return con.close()
            self.recebido += dados
            uri = dados.split(' ')[1]
            caminho = uri.split(str(self.porta), 1)[1]
            cseq = re.search(r'CSeq: (\d+)', dados).group(1)
            aut = re.search(r'response="(\w+)"', dados)
            if self.basic:
                con.sendall(f'RTSP/1.0 401 Unauthorized\r\nCSeq: {cseq}\r\nWWW-Authenticate: Basic realm="x"\r\n\r\n'.encode())
            elif not aut:
                con.sendall(f'RTSP/1.0 401 Unauthorized\r\nCSeq: {cseq}\r\nWWW-Authenticate: Digest realm="R", nonce="N1"\r\n\r\n'.encode())
            else:
                certo = md5(f"{md5(f'{self.usuario}:R:{self.senha}')}:N1:{md5(f'DESCRIBE:{uri}')}")
                validos = {self.caminho.replace('{canal}', str(n)) for n in range(1, self.canais + 1)}
                codigo = '401 Unauthorized' if aut.group(1) != certo else ('200 OK' if caminho in validos else '404 Not Found')
                con.sendall(f'RTSP/1.0 {codigo}\r\nCSeq: {cseq}\r\n\r\n'.encode())


fg = FalsoGravador()
CAM = fg.caminho.replace('{canal}', '1')
t('senha certa abre', dsc.testar_rtsp('127.0.0.1', fg.porta, CAM, 'admin', 's3nha') == 200)
t('senha errada: 401', dsc.testar_rtsp('127.0.0.1', fg.porta, CAM, 'admin', 'errada') == 401)
t('caminho errado: 404', dsc.testar_rtsp('127.0.0.1', fg.porta, '/nada', 'admin', 's3nha') == 404)
t('a senha nunca viaja na rede', 's3nha' not in fg.recebido and 'errada' not in fg.recebido)
t('conta 4 câmeras', dsc.contar_canais('127.0.0.1', fg.porta, fg.caminho, 'admin', 's3nha') == [1, 2, 3, 4])
fb = FalsoGravador(basic=True)
t('quem só aceita senha aberta não recebe a senha', dsc.testar_rtsp('127.0.0.1', fb.porta, CAM, 'admin', 's3nha') == -1 and 's3nha' not in fb.recebido and 'Authorization' not in fb.recebido)
t('porta fechada: 0', dsc.testar_rtsp('127.0.0.1', 1, CAM, 'admin', 's3nha') == 0)
sem_busca = dict(buscar_dhip=lambda: {}, buscar_onvif=lambda: set(), faixa_local=lambda: [])
with patch.multiple(dsc, **sem_busca):
    r = dsc.descobrir('admin', 's3nha', ips_extra=['127.0.0.1'], porta=fg.porta)
    t('acha sozinho IP, caminho e câmeras', r.get('ip') == '127.0.0.1' and r['caminho'] == fg.caminho and r['canais'] == [1, 2, 3, 4], r)
    f2 = FalsoGravador(caminho='/stream{canal}', canais=2)
    r = dsc.descobrir('admin', 's3nha', ips_extra=['127.0.0.1'], porta=f2.porta)
    t('gravador de outra família: acha o caminho dele', r.get('caminho') == '/stream{canal}' and r['canais'] == [1, 2], r)
    t('senha errada vira motivo para a tela', 'recusou' in dsc.descobrir('admin', 'x', ips_extra=['127.0.0.1'], porta=fg.porta).get('erro', ''))
    t('rede sem gravador vira motivo para a tela', 'mesma rede' in dsc.descobrir('admin', 'x').get('erro', ''))
    t('aparelho sem vídeo vira motivo para a tela', 'nenhum abriu' in dsc.descobrir('admin', 's3nha', ips_extra=['127.0.0.1'], porta=FalsoGravador(caminho='/x{canal}').porta).get('erro', ''))
    with patch.object(dsc, 'buscar_dhip', return_value={'10.9.9.1': {'serial': 'OUTRO', 'modelo': ''}, '127.0.0.1': {'serial': 'CERTO', 'modelo': 'NVD'}}):
        ordem, _ = dsc._candidatos('certo')
        t('serial do cadastro é tentado primeiro', ordem[0] == '127.0.0.1', ordem)
        r = dsc.descobrir('admin', 's3nha', 'CERTO', porta=fg.porta)
        t('devolve serial e modelo achados', r.get('serial') == 'CERTO' and r['modelo'] == 'NVD', r)
resp = b' ' * 32 + _j.dumps({'params': {'deviceInfo': {'IPv4Address': {'IPAddress': '192.168.1.108'}, 'SerialNo': 'ABC', 'DeviceType': 'NVD'}}}).encode() + b'\x00'
t('lê a resposta da busca do gravador', dsc.ler_resposta_dhip(resp) == ('192.168.1.108', 'ABC', 'NVD'))
t('resposta estranha não derruba', dsc.ler_resposta_dhip(b'lixo') is None and dsc.ler_resposta_dhip(b'{"params": 1}') is None)
t('lê o endereço do ONVIF', dsc.ler_resposta_onvif(b'<a:XAddrs>http://192.168.1.20/onvif/device_service</a:XAddrs>') == ['192.168.1.20'])
pac = dsc._dhip_pacote()
t('pacote da busca tem o cabeçalho certo', pac[4:8] == b'DHIP' and pac[32:33] == b'{')


class Cam:
    def __init__(self, idade):
        self.ultimo_quadro_em = 1000 - idade


t('sem IP: procura', precisa_descobrir({'ip': ''}, {}, None, 1000))
t('sem IP, mas procurou agora: espera', not precisa_descobrir({'ip': ''}, {}, 900, 1000))
t('sem IP: tenta de novo depois do intervalo', precisa_descobrir({'ip': ''}, {}, 300, 1000))
t('câmeras com imagem: não procura', not precisa_descobrir({'ip': '1.1.1.1'}, {1: Cam(10), 2: Cam(999)}, None, 1000))
t('todas sem imagem (IP mudou): procura', precisa_descobrir({'ip': '1.1.1.1'}, {1: Cam(400), 2: Cam(999)}, None, 1000))

print('agente: o laço principal')
import agente as ag_mod


class Fim(BaseException):  # o laço do agente não morre com Exception: o teste encerra por fora
    pass


class PortalFalso:
    def __init__(self, respostas):
        self.respostas, self.achados, self.fotos_recebidas = list(respostas), [], []

    def sinal(self, aviso='', extras=None):
        if not self.respostas:
            raise Fim
        return self.respostas.pop(0)

    def descoberta(self, resultado):
        self.achados.append(resultado)

    def fotos(self, fotos):
        self.fotos_recebidas.append(fotos)


base_sinal = {'ativo': True, 'proximo_sinal': 1, 'fotos': [], 'foco': None, 'abre_as': '08:00', 'fecha_as': '20:00', 'canais': []}
sem_ip = {**base_sinal, 'gravador': {'ip': '', 'porta': 554, 'caminho': '/c?x={canal}', 'usuario': 'admin', 'senha': 'a@b', 'serial': 'S1'}}
com_ip = {**base_sinal, 'fotos': [1], 'canais': [{'numero': 1, 'ia': True, 'zonas': []}, {'numero': 2, 'ia': True, 'zonas': []}],
          'gravador': {**sem_ip['gravador'], 'ip': '192.168.0.9'}}
outro_ip = {**com_ip, 'gravador': {**com_ip['gravador'], 'ip': '192.168.0.10'}}
pf = PortalFalso([sem_ip, com_ip, outro_ip, {'ativo': False, 'proximo_sinal': 1}])
iniciadas, paradas = [], []
real_start = Camera.start


def start_falso(self):
    self.ultimo = object()
    iniciadas.append(self.url)


with patch.object(ag_mod, 'Portal', return_value=pf), patch.object(ag_mod, 'carregar_detector', return_value=(None, '')), \
        patch.object(ag_mod, 'descobrir', return_value={'ip': '192.168.0.9', 'porta': 554, 'caminho': '/c?x={canal}', 'canais': [1, 2]}) as procura, \
        patch.object(Camera, 'start', start_falso), patch.object(ag_mod, '_jpg', return_value=b'jpg'), patch.object(ag_mod.time, 'sleep'), patch.object(PortalFalso, 'metricas', create=True):
    try:
        ag_mod.rodar({'portal': 'https://x', 'token': 't', '_pasta': tempfile.mkdtemp()})
    except Fim:
        pass
t('sem IP: procura e manda o que achou', procura.call_count == 1 and procura.call_args[0][:3] == ('admin', 'a@b', 'S1') and pf.achados[0]['ip'] == '192.168.0.9', procura.call_args)
t('com IP: abre as câmeras com a senha protegida no endereço', any('admin:a%40b@192.168.0.9:554/c?x=1' in u for u in iniciadas), iniciadas)
t('manda só a foto pedida', pf.fotos_recebidas and list(pf.fotos_recebidas[0]) == [1], pf.fotos_recebidas)
t('IP mudou: reabre no novo', any('192.168.0.10' in u for u in iniciadas), iniciadas)
with patch.dict(sys.modules, {'detector': None}):
    det_, aviso_ = ag_mod.carregar_detector('x.onnx')
t('sem a análise, o agente segue só com imagem e avisa', det_ is None and 'Análise desligada' in aviso_, aviso_)
r = anon.post('/cameras/agente/sinal/', {'aviso': aviso_}, **ag3)
t('aviso do agente aparece na linha da loja', 'Análise desligada' in Gravador.objects.get(pk=g3.id).erro_agente)
Gravador.objects.filter(pk=g3.pk).update(erro_agente='')

print('instalador do Windows: etapa final')
import xml.dom.minidom as _md
import instalar as ins


class Resposta:
    def __init__(self, codigo=200, dados=None):
        self.status_code, self._dados = codigo, dados or {}

    def json(self):
        return self._dados


class HttpFalso:
    def __init__(self, ativar=None, cair=False):
        self.ativar, self.cair, self.pedidos = ativar or Resposta(200, {'token': 'TOK', 'loja': 'LOJA X'}), cair, []

    def post(self, url, data=None, headers=None, timeout=None):
        self.pedidos.append((url, data, headers))
        if self.cair:
            raise OSError('sem rede')
        return self.ativar if url.endswith('/ativar/') else Resposta()


class Roda:
    def __init__(self, falha=None, pasta=None):
        self.falha, self.pasta, self.comandos = falha, pasta, []

    def __call__(self, *comando):
        self.comandos.append(comando)
        if comando[0] == 'schtasks' and comando[1] == '/Run':
            open(os.path.join(self.pasta, 'agente.log'), 'w').close()
        erro = self.falha and self.falha in ' '.join(comando)
        return mock.Mock(returncode=1 if erro else 0, stderr='motivo do Windows' if erro else '', stdout='')


def montar_instalacao(**k):
    pasta = tempfile.mkdtemp()
    with open(os.path.join(pasta, 'portal.txt'), 'w') as arq:
        arq.write('https://portal.exemplo/\n')
    roda = Roda(k.pop('falha', None), pasta)
    http = k.pop('http', None) or HttpFalso()
    return ins.Instalacao(pasta, rodar=roda, esperar=lambda s: None, http=http), roda, http, pasta


def rodar_instalador(nome='Instalar-Cameras-LOJA-ABCDEFGH2345.exe', **k):
    inst, roda, http, pasta = montar_instalacao(**k)
    codigo = ins.principal(['instalar.py', nome], inst)
    with open(os.path.join(pasta, 'resultado.txt'), encoding='utf-16') as arq:
        return codigo, arq.read(), roda, http, pasta


t('lê o código do nome', ins.codigo_do_arquivo('Instalar-Cameras-SERRA-SEDE-ABCDEFGH2345.exe') == 'ABCDEFGH2345')
t('baixou duas vezes: " (1)" não atrapalha', ins.codigo_do_arquivo('Instalar-Cameras-X-ABCDEFGH2345 (1).exe') == 'ABCDEFGH2345')
t('nome sem código', ins.codigo_do_arquivo('AgenteCameras.exe') == '' and ins.codigo_do_arquivo('') == '')
c_, msg, roda, http, pasta = rodar_instalador()
t('instalação inteira dá certo e fala a loja', c_ == 0 and 'LOJA X' in msg, msg)
t('troca o código pelo token', http.pedidos[0][0] == 'https://portal.exemplo/cameras/agente/ativar/' and http.pedidos[0][1] == {'codigo': 'ABCDEFGH2345'})
with open(os.path.join(pasta, 'config.json')) as arq:
    cfg_ins = _j.load(arq)
t('grava a configuração', cfg_ins['token'] == 'TOK' and cfg_ins['portal'] == 'https://portal.exemplo' and cfg_ins['modelo'] == 'yolox_tiny.onnx')
t('confere as peças antes de ligar', [c[2] for c in roda.comandos if c[1] == '-c'] == ['import cv2', 'import onnxruntime', 'import numpy', 'import av', 'import aiortc'])
t('libera o vídeo no Firewall do Windows, só UDP e só para o agente', any(c[0] == 'netsh' and 'add' in c and 'protocol=udp' in c and any('runtime' in p for p in c) for c in roda.comandos))
ordem = [c[1] for c in roda.comandos if c[0] == 'schtasks']
t('cria, reinicia e confere a tarefa', ordem == ['/Create', '/End', '/Run', '/Query'], ordem)
t('protege o token por SID', any(c[0] == 'icacls' and '*S-1-5-32-544:F' in c for c in roda.comandos))
t('avisa cada passo no portal', sum(1 for p in http.pedidos if p[0].endswith('/descoberta/')) == 2)
with open(os.path.join(pasta, 'tarefa.xml'), encoding='utf-16') as arq:
    texto_xml = arq.read()
doc = _md.parseString(texto_xml.encode('utf-16'))
t('XML da tarefa válido, sem limite de 3 dias, como SYSTEM, ao ligar', doc.getElementsByTagName('ExecutionTimeLimit')[0].firstChild.data == 'PT0S' and doc.getElementsByTagName('UserId')[0].firstChild.data == 'S-1-5-18' and doc.getElementsByTagName('BootTrigger'))
xml_amp = ins.xml_da_tarefa(r'C:\Program Data\A&B')
t('caminho com & não quebra o XML', _md.parseString(xml_amp.encode('utf-16')) and 'A&amp;B' in xml_amp)
c_, msg, *_x = rodar_instalador('renomeado.exe')
t('sem código e sem instalação anterior: explica', c_ == 1 and 'sem renomear' in msg, msg)
inst, roda, http, pasta = montar_instalacao()
with open(os.path.join(pasta, 'config.json'), 'w') as arq:
    _j.dump({'portal': 'x', 'token': 'ANTIGO'}, arq)
t('renomeado em reinstalação: segue com a loja de antes', ins.principal(['i', 'renomeado.exe'], inst) == 0 and inst.token == 'ANTIGO')
c_, msg, *_x = rodar_instalador(http=HttpFalso(cair=True))
t('sem internet: explica', c_ == 1 and 'Sem conexão' in msg, msg)
c_, msg, *_x = rodar_instalador(http=HttpFalso(ativar=Resposta(403)))
t('código vencido: manda baixar de novo', c_ == 1 and 'Baixe de novo' in msg, msg)
c_, msg, *_x = rodar_instalador(http=HttpFalso(ativar=Resposta(404)))
t('portal sem a versão nova: explica', c_ == 1 and 'ainda não tem' in msg, msg)
c_, msg, roda, *_x = rodar_instalador(falha='import cv2')
t('peça que não abre: nomeia qual e não liga', c_ == 1 and 'leitura de vídeo' in msg and 'motivo do Windows' in msg and not any(c[0] == 'schtasks' for c in roda.comandos), msg)
c_, msg, roda, http, _p = rodar_instalador(falha='/Create')
t('falha ao criar a tarefa: mostra e avisa o portal', c_ == 1 and 'não criou' in msg and any('falhou' in (p[1] or {}).get('erro', '') for p in http.pedidos), msg)
inst, roda, http, pasta = montar_instalacao()
inst.conferir_pecas = mock.Mock(side_effect=ValueError('estranho'))
t('erro inesperado também vira mensagem', ins.principal(['i', 'I-ABCDEFGH2345.exe'], inst) == 1)
inst, roda, http, pasta = montar_instalacao()
roda_sem_log = lambda *c: mock.Mock(returncode=0, stderr='', stdout='')
inst.rodar = roda_sem_log
t('agente que não sobe não vira falso "pronto"', ins.principal(['i', 'I-ABCDEFGH2345.exe'], inst) == 1)
nsi = pathlib.Path('agente_cameras/instalador.nsi').read_text(encoding='utf-8')
t('instalador pede administrador e confere 64 bits e Windows 10', 'RequestExecutionLevel admin' in nsi and 'RunningX64' in nsi and 'AtLeastWin10' in nsi)
import construir_instalador as ci
t('o arquivo de caminhos põe a pasta do agente no alcance do Python', '..' in ci.PTH.split('\n') and 'import site' in ci.PTH)
t('empacota tudo que o agente importa', set(ci.ARQUIVOS) >= {'agente.py', 'ao_vivo.py', 'descoberta.py', 'detector.py', 'regras.py', 'instalar.py'})

print('medições: regras')
from regras import Metricas, Rastreador, cruzou_para_dentro, zonas_do_tipo
from agente import FilaDeEventos, carimbar
PORTA = {'nome': 'Porta', 'tipo': 'PORTA', 'x': .2, 'y': .4, 'w': .4, 'h': .2, 'sentido': 'baixo'}
MESA = {'nome': 'Mesa 1', 'tipo': 'MESA', 'x': .6, 'y': .5, 'w': .2, 'h': .3}
TELA = {'nome': 'Tela', 'tipo': 'TELA', 'x': .0, 'y': .0, 'w': .5, 'h': .5}
def pessoa_pes(x, y): return (x - .05, y - .2, .1, .2)
def tipos(ev): return [e['tipo'] for e in ev if e['tipo'] != 'COBERTURA']
t('mostruário antigo sem tipo continua mostruário', len(zonas_do_tipo([{'nome': 'a'}, MESA], 'MOSTRUARIO')) == 1)
t('cruzar para dentro', cruzou_para_dentro(PORTA, (.4, .45), (.4, .55)))
t('cruzar para fora não conta', not cruzou_para_dentro(PORTA, (.4, .55), (.4, .45)))
t('passar ao lado da porta não conta', not cruzou_para_dentro(PORTA, (.9, .45), (.9, .55)))
t('sentido lateral', cruzou_para_dentro({**PORTA, 'sentido': 'esquerda'}, (.45, .5), (.35, .5)))
def caminho(m, pontos, inicio=0, passo=.33, aberta=True, hora='10:00'):
    saida = []
    for i, ponto in enumerate(pontos):
        saida += m.analisar([pessoa_pes(*p) for p in ponto], inicio + i * passo, hora, aberta)
    return saida
m = Metricas([PORTA])
s1 = caminho(m, [[(.4, y)] for y in (.42, .46, .49, .52, .57)])
t('uma pessoa entrando conta 1', tipos(s1) == ['ENTRADA'], s1)
t('ficar parado na linha não conta de novo', tipos(caminho(Metricas([PORTA]), [[(.4, y)] for y in (.49, .51, .49, .51, .49, .51)])) == ['ENTRADA'])
t('duas pessoas juntas contam 2', tipos(caminho(Metricas([PORTA]), [[(.3, y), (.5, y)] for y in (.42, .46, .52, .57)])) == ['ENTRADA', 'ENTRADA'])
t('quem sai não conta', tipos(caminho(Metricas([PORTA]), [[(.4, y)] for y in (.57, .52, .46, .42)])) == [])
t('dois em sentidos opostos: conta só quem entra', tipos(caminho(Metricas([PORTA]), [[(.3, a), (.5, b)] for a, b in ((.42, .58), (.46, .54), (.52, .46), (.57, .42))])) == ['ENTRADA'])
t('loja fechada não conta', tipos(caminho(Metricas([PORTA]), [[(.4, y)] for y in (.42, .52, .57)], aberta=False)) == [])
t('sem porta marcada não conta', tipos(caminho(Metricas([]), [[(.4, y)] for y in (.42, .52, .57)])) == [])
m = Metricas([PORTA])
primeira = caminho(m, [[(.4, y)] for y in (.42, .52)])
segunda = caminho(m, [[], [], []], inicio=10, passo=5) + caminho(m, [[(.4, y)] for y in (.42, .52)], inicio=30)
t('pessoa diferente, depois, conta de novo', tipos(primeira + segunda) == ['ENTRADA', 'ENTRADA'], primeira + segunda)
r_ = Rastreador()
a1 = r_.atualizar([(.1, .1), (.9, .9)], 0); a2 = r_.atualizar([(.12, .1), (.88, .9)], 1)
t('rastreador mantém cada um', {k: v for k, v in a2.items()}.keys() == a1.keys() and a2[list(a1)[0]][0] == .12)
t('longe demais é outra pessoa', set(r_.atualizar([(.5, .5)], 2)).isdisjoint(a1))
t('primeira análise da hora registra a cobertura', [e['tipo'] for e in Metricas([]).analisar([], 0, '10:00')] == ['COBERTURA'])
mc = Metricas([]); mc.analisar([], 0, '10:00')
t('uma cobertura por hora', mc.analisar([], 1, '10:30') == [] and [e['tipo'] for e in mc.analisar([], 2, '11:00')] == ['COBERTURA'])
def mesa(cheia, inicio=0, passo=1):
    m = Metricas([MESA]); saida = []
    for i, c in enumerate(cheia):
        saida += m.analisar([(.65, .55, .1, .1)] if c else [], inicio + i * passo, '10:00')
    return [e for e in saida if e['tipo'] == 'ATENDIMENTO']
ate = mesa([True] * 301 + [False] * 60)
t('tempo na mesa: 5 minutos', len(ate) == 1 and ate[0]['duracao'] == 300 and ate[0]['zona'] == 'Mesa 1', ate)
t('passou pela cadeira: não é atendimento', mesa([True] * 30 + [False] * 60) == [])
t('detector piscou: não parte o atendimento', len(x := mesa([True] * 100 + [False] * 20 + [True] * 100 + [False] * 60)) == 1 and x[0]['duracao'] == 219, x)
t('dois clientes separados', len(mesa([True] * 100 + [False] * 60 + [True] * 100 + [False] * 60)) == 2)
t('ainda sentado: não fecha antes da hora', mesa([True] * 500) == [])
def grupo(n): return [(.1 + i * .05, .1, .05, .1) for i in range(n)]
def matinal(seq, minimo=300):
    m = Metricas([TELA], matinal_minimo=minimo); saida = []
    for i, (n, hora) in enumerate(seq):
        saida += m.analisar(grupo(n), i * 10, hora)
    return [e for e in saida if e['tipo'] == 'MATINAL']
x = matinal([(4, '08:00')] * 40 + [(0, '08:07')] * 10)
t('matinal: 4 pessoas por mais de 5 min', len(x) == 1 and x[0]['pessoas'] == 4 and x[0]['duracao'] == 390, x)
t('2 pessoas não é matinal', matinal([(2, '08:00')] * 40 + [(0, '08:07')] * 10) == [])
t('3 pessoas já é matinal', len(matinal([(3, '08:00')] * 40 + [(0, '08:07')] * 10)) == 1)
t('menos de 5 min não é matinal', matinal([(4, '08:00')] * 20 + [(0, '08:07')] * 10) == [])
t('depois das 9h não é matinal', matinal([(4, '09:00')] * 40 + [(0, '09:07')] * 10) == [])
t('antes das 5h não é matinal', matinal([(4, '04:30')] * 40 + [(0, '04:38')] * 10) == [])
x = matinal([(4, '08:50')] * 40 + [(4, '09:00')] * 5)
t('passou das 9h: fecha sem contar o resto', len(x) == 1 and x[0]['duracao'] == 390, x)
t('saiu um por instantes: é a mesma matinal', len(matinal([(4, '08:00')] * 20 + [(1, '08:03')] * 3 + [(4, '08:04')] * 20 + [(0, '08:08')] * 10)) == 1)
momento = timezone.localtime()
c_ = carimbar([{'tipo': 'ATENDIMENTO', 'zona': 'M', 'inicio': 100, 'fim': 400, 'duracao': 300, 'pessoas': 1}], 460, momento)[0]
t('carimbo volta à hora real', c_['fim'] == (momento - dt.timedelta(seconds=60)).isoformat() and c_['inicio'] == (momento - dt.timedelta(seconds=360)).isoformat() and len(c_['chave']) == 32)
caminho_fila = os.path.join(tempfile.mkdtemp(), 'fila.json')
fila = FilaDeEventos(caminho_fila); fila.adicionar([c_])
t('fila sobrevive a reiniciar', FilaDeEventos(caminho_fila).eventos == [c_])
class PortalQueda:
    def __init__(self): self.falhar, self.recebidos = True, []
    def metricas(self, lote):
        if self.falhar:
            raise requests.ConnectionError('fora')
        self.recebidos += lote
pq = PortalQueda()
try:
    fila.enviar(pq); caiu = False
except requests.ConnectionError:
    caiu = True
t('portal fora: guarda para depois', caiu and fila.eventos == [c_])
pq.falhar = False; fila.enviar(pq)
t('voltou: envia e esvazia', pq.recebidos == [c_] and fila.eventos == [] and FilaDeEventos(caminho_fila).eventos == [])

print('medições: portal')
from cameras.indicadores import formatar_duracao, indicadores_do_periodo
from cameras.models import EventoLoja
EventoLoja.objects.all().delete()
gm = Gravador.objects.create(loja='LOJA MEDIDA'); tok = gm.gerar_token()
cm = Canal.objects.create(gravador=gm, numero=1, zonas=[PORTA, MESA, TELA])
agm = {'HTTP_AUTHORIZATION': f'Bearer {tok}'}
Gravador.objects.filter(pk=gm.pk).update(ultimo_contato=timezone.now())
agora_ = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0)
if agora_ > timezone.localtime():
    agora_ = agora_ - dt.timedelta(days=1)
dia_ = agora_.date()
oito = agora_.replace(hour=8)
def ev(chave, tipo, inicio, fim=None, **k):
    return {'chave': chave, 'tipo': tipo, 'canal': 1, 'zona': k.pop('zona', ''), 'inicio': inicio.isoformat(), 'fim': (fim or inicio).isoformat(), 'duracao': k.pop('duracao', 0), 'pessoas': k.pop('pessoas', 0)}
post = lambda lista, **kw: anon.post('/cameras/agente/metricas/', _j.dumps({'eventos': lista}), content_type='application/json', **{**agm, **kw})
lote = [ev('c1', 'COBERTURA', oito), ev('c2', 'COBERTURA', agora_), ev('e1', 'ENTRADA', agora_, zona='Porta', pessoas=1), ev('e2', 'ENTRADA', agora_, zona='Porta', pessoas=1),
        ev('e3', 'ENTRADA', agora_ - dt.timedelta(hours=1), zona='Porta', pessoas=1), ev('a1', 'ATENDIMENTO', agora_ - dt.timedelta(seconds=300), agora_, zona='Mesa 1', duracao=300, pessoas=1),
        ev('a2', 'ATENDIMENTO', agora_ - dt.timedelta(seconds=100), agora_, zona='Mesa 1', duracao=100, pessoas=1), ev('m1', 'MATINAL', oito, oito + dt.timedelta(minutes=10), zona='Tela', duracao=600, pessoas=5)]
r = post(lote)
t('recebe as medições', r.status_code == 200 and EventoLoja.objects.filter(gravador=gm).count() == 8, r.content)
t('reenvio não conta duas vezes', post(lote).status_code == 200 and EventoLoja.objects.filter(gravador=gm).count() == 8)
t('sem token: recusa', anon.post('/cameras/agente/metricas/', '{}', content_type='application/json').status_code == 401)
t('JSON quebrado: recusa', anon.post('/cameras/agente/metricas/', '{x', content_type='application/json', **agm).status_code == 400)
ruins = [{'chave': 'r1'}, ev('r2', 'INVENTADO', agora_), ev('r3', 'ENTRADA', timezone.now() + dt.timedelta(days=2)), ev('r4', 'ENTRADA', timezone.now() - dt.timedelta(days=90)),
         {**ev('r5', 'ATENDIMENTO', agora_), 'duracao': -5}, ev('r6', 'ATENDIMENTO', agora_, agora_ - dt.timedelta(hours=1))]
post(ruins + [ev('bom', 'ENTRADA', agora_, zona='Porta')])
t('item estragado cai, o bom entra', EventoLoja.objects.filter(gravador=gm, chave='bom').exists() and not EventoLoja.objects.filter(chave__in=['r1', 'r2', 'r3', 'r4', 'r5', 'r6']).exists())
EventoLoja.objects.filter(chave='bom').delete()
ConfigCameras.objects.update(ativo=False)
post([ev('off', 'ENTRADA', agora_)])
t('módulo desligado não grava medição', not EventoLoja.objects.filter(chave='off').exists())
ConfigCameras.objects.update(ativo=True)
d_ = indicadores_do_periodo(dia_, dia_, gm.id)
l_ = d_['linhas'][0]
t('conta os clientes', l_['entradas'] == 3 and d_['total_entradas'] == 3)
t('hora de pico', l_['pico'] == f'{agora_.hour:02d}h (2)', l_['pico'])
t('matinal do dia', l_['matinal_dias'] == 1 and l_['matinais'][0]['pessoas'] == 5 and not l_['matinal_aviso'])
t('tempo médio e maior na mesa', l_['atendimentos'] == 2 and l_['media_segundos'] == 200 and l_['maior'] == '5 min 00 s', l_)
t('formato do tempo', formatar_duracao(45) == '45 s' and formatar_duracao(200) == '3 min 20 s' and formatar_duracao(3900) == '1h05' and formatar_duracao(None) == '—')
Canal.objects.create(gravador=gm, numero=2, zonas=[{**MESA, 'nome': 'Mesa 2'}])
post([ev('a3', 'ATENDIMENTO', agora_ - dt.timedelta(seconds=60), agora_, zona='Mesa 2', duracao=60, pessoas=1)])
t('separa por mesa', [m['nome'] for m in indicadores_do_periodo(dia_, dia_, gm.id)['linhas'][0]['por_mesa']] == ['Mesa 1', 'Mesa 2'])
h = cs.get(f'/cameras/indicadores/?de={dia_}&ate={dia_}&loja={gm.id}').content.decode()
t('tela mostra os números', 'LOJA MEDIDA' in h and 'data-total-entradas>3<' in h and 'Sim' in h and '5 pessoas' in h and 'Mesa 2' in h)
t('tela avisa que inclui funcionários e não mede o olhar', 'Inclui funcionários' in h and 'não para onde elas olham' in h)
t('colaborador comum não vê', cc.get('/cameras/indicadores/').status_code == 302)
t('data inválida não quebra', cs.get('/cameras/indicadores/?de=abc&ate=x').status_code == 200)
ontem = dia_ - dt.timedelta(days=1)
sem = indicadores_do_periodo(ontem, ontem, gm.id)['linhas'][0]
t('sem cobertura é sem medição, não zero', sem['entradas'] is None and sem['entradas_aviso'] == 'Sem medição' and sem['matinal_aviso'] == 'Sem medição' and sem['atendimentos'] is None, sem['entradas_aviso'])
post([ev('c9', 'COBERTURA', agora_ - dt.timedelta(days=1))])
tarde = indicadores_do_periodo(ontem, ontem, gm.id)['linhas'][0]
t('cobriu só depois da janela: entrada zero, matinal sem medição', tarde['entradas'] == 0 and tarde['matinal_aviso'] == 'Sem medição', tarde['matinal_aviso'])
post([ev('c10', 'COBERTURA', oito - dt.timedelta(days=1))])
nao = indicadores_do_periodo(ontem, ontem, gm.id)['linhas'][0]
t('cobriu a janela e nada aconteceu: não houve matinal', nao['matinal_aviso'] == '' and nao['matinal_dias'] == 0)
gsem = Gravador.objects.create(loja='LOJA SEM AGENTE')
t('sem agente diz isso', indicadores_do_periodo(dia_, dia_, gsem.id)['linhas'][0]['entradas_aviso'] == 'Agente não instalado')
gz = Gravador.objects.create(loja='LOJA SEM AREA', ultimo_contato=timezone.now()); Canal.objects.create(gravador=gz, numero=1)
lz = indicadores_do_periodo(dia_, dia_, gz.id)['linhas'][0]
t('sem área marcada pede para marcar', lz['entradas_aviso'] == 'Marque a porta' and lz['mesas_aviso'] == 'Marque a mesa' and lz['matinal_aviso'] == 'Marque a tela')
r = cs.post(f'/cameras/canal/{cm.id}/salvar/', _j.dumps({'zonas': [{'nome': 'Mesa', 'tipo': 'MESA', 'x': .1, 'y': .1, 'w': .2, 'h': .2}, {'nome': 'Mesa', 'tipo': 'MESA', 'x': .4, 'y': .1, 'w': .2, 'h': .2}, {'tipo': 'PORTA', 'sentido': 'cima', 'x': .1, 'y': .5, 'w': .2, 'h': .2}, {'tipo': 'INVENTADO', 'sentido': 'x', 'x': .5, 'y': .5, 'w': .2, 'h': .2}]}), content_type='application/json')
z_ = r.json()['zonas']
t('áreas guardam o tipo e o sentido', z_[2]['tipo'] == 'PORTA' and z_[2]['sentido'] == 'cima' and z_[3]['tipo'] == 'MOSTRUARIO' and 'sentido' not in z_[3], z_)
t('duas mesas com o mesmo nome viram duas', z_[0]['nome'] == 'Mesa' and z_[1]['nome'] == 'Mesa 2')
r = anon.post('/cameras/agente/sinal/', **agm).json()
t('agente recebe o horário da matinal', r['matinal'] == {'inicio': '05:00', 'fim': '09:00', 'minimo': 300}, r.get('matinal'))
cs.post(f'/cameras/configuracao/{gm.id}/', {'loja': 'LOJA MEDIDA', 'caminho_rtsp': '/x?c={canal}', 'porta_rtsp': 554, 'canais': 2, 'tem_equipamento': '1', 'abre_as': '09:00', 'fecha_as': '18:00', 'matinal_inicio': '06:30', 'matinal_fim': '08:30', 'matinal_minutos': '10'})
gm.refresh_from_db()
t('horário da matinal por loja', gm.matinal_inicio.strftime('%H:%M') == '06:30' and gm.matinal_minutos == 10)

print('vídeo ao vivo: portal')
from django.db import connection
from django.test.utils import CaptureQueriesContext
from cameras.models import SessaoAoVivo
SessaoAoVivo.objects.all().delete()
cache.clear()
ConfigCameras.objects.update(ativo=True)
gv = Gravador.objects.create(loja='LOJA VIVO', versao_agente='2.1', ultimo_contato=timezone.now()); tv = gv.gerar_token()
cv1 = Canal.objects.create(gravador=gv, numero=7); cv2 = Canal.objects.create(gravador=gv, numero=8)
agv = {'HTTP_AUTHORIZATION': f'Bearer {tv}'}
gold = Gravador.objects.create(loja='LOJA ANTIGA', versao_agente='2.0', ultimo_contato=timezone.now()); cold = Canal.objects.create(gravador=gold, numero=1)
goff = Gravador.objects.create(loja='LOJA FORA', versao_agente='2.1', ultimo_contato=timezone.now() - dt.timedelta(hours=1)); coff = Canal.objects.create(gravador=goff, numero=1)
SDP = 'v=0 oferta do navegador'
def abrir(cliente, *itens):
    return cliente.post('/cameras/aovivo/abrir/', _j.dumps({'sessoes': list(itens)}), content_type='application/json')
def pedir(*ativas, **k):
    return anon.post('/cameras/agente/aovivo/', _j.dumps({'ativas': list(ativas)}), content_type='application/json', **{**agv, **k})
def responder(dados, **k):
    return anon.post('/cameras/agente/aovivo/resposta/', _j.dumps(dados), content_type='application/json', **{**agv, **k})
t('versão 2.1 tem ao vivo, 2.0 não, sem versão não', gv.tem_ao_vivo and not gold.tem_ao_vivo and not Gravador(versao_agente='').tem_ao_vivo and not Gravador(versao_agente='x').tem_ao_vivo)
t('colaborador comum não pede vídeo', abrir(cc, {'canal': cv1.id, 'sdp': SDP}).status_code == 302 and not SessaoAoVivo.objects.exists())
t('sem login não pede vídeo', anon.post('/cameras/aovivo/abrir/', '{}', content_type='application/json').status_code in (302, 403))
r = pedir()
t('agente parado: nada a fazer', r.status_code == 200 and r.json() == {'ofertas': [], 'fechar': [], 'proximo': 4}, r.content)
with CaptureQueriesContext(connection) as parado:
    pedir()
vistas = [q['sql'] for q in parado.captured_queries if 'cameras_' in q['sql']]
t('a pergunta parada não vai às tabelas das câmeras', not vistas, vistas)
t('token errado: 401', pedir(HTTP_AUTHORIZATION='Bearer x').status_code == 401 and anon.post('/cameras/agente/aovivo/').status_code == 401)
t('JSON quebrado: 400', anon.post('/cameras/agente/aovivo/', '{x', content_type='application/json', **agv).status_code == 400)
r = abrir(cs, {'canal': cv1.id, 'qualidade': 'ALTA', 'sdp': SDP}, {'canal': cv2.id, 'qualidade': 'x', 'sdp': SDP}, {'canal': cold.id, 'sdp': SDP}, {'canal': coff.id, 'sdp': SDP}, {'canal': 999999, 'sdp': SDP}, {'canal': cv1.id, 'sdp': ''}, {'canal': cv1.id, 'sdp': 'x' * 30000}, 'lixo').json()['sessoes']
t('pedido certo vira sessão', 'id' in r[0] and 'id' in r[1], r[:2])
t('agente antigo: pede para reinstalar', r[2].get('atualizar') and 'Reinstale' in r[2]['erro'], r[2])
t('agente fora do ar: avisa', 'sem contato' in r[3].get('erro', ''), r[3])
t('canal inexistente, sem oferta, oferta enorme e lixo: recusa', all('erro' in x for x in r[4:]) and SessaoAoVivo.objects.count() == 2)
s1, s2 = r[0]['id'], r[1]['id']
t('qualidade desconhecida vira baixa', SessaoAoVivo.objects.get(id=s2).qualidade == 'BAIXA' and SessaoAoVivo.objects.get(id=s1).qualidade == 'ALTA')
t('quem viu fica registrado', AcessoCamera.objects.filter(acao='ver_ao_vivo', gravador=gv).count() == 1)
r = pedir().json()
t('agente recebe as ofertas com o número da câmera', {o['id']: o['canal'] for o in r['ofertas']} == {s1: 7, s2: 8} and r['ofertas'][0]['sdp'] == SDP and r['proximo'] == 1, r)
t('e não recebe de novo', pedir().json()['ofertas'] == [])
agb = {'HTTP_AUTHORIZATION': 'Bearer ' + gold.gerar_token()}
t('outra loja não recebe nem responde', pedir(**agb).json()['ofertas'] == [] and responder({'id': s1, 'sdp': 'invasor'}, **agb).status_code == 400 and not SessaoAoVivo.objects.get(id=s1).resposta)
t('resposta inválida: 400', responder({'id': s1}).status_code == 400 and responder({'id': 'x', 'sdp': 'a'}).status_code == 400 and responder({'id': s1, 'sdp': 'x' * 30000}).status_code == 400)
t('resposta certa', responder({'id': s1, 'sdp': 'ANSWER', 'codec': 'h264'}).status_code == 200)
responder({'id': s2, 'erro': 'A câmera não abriu.'})
ouvidos = cs.get(f'/cameras/aovivo/respostas/?ids={s1},{s2},lixo').json()['respostas']
t('navegador recebe resposta e erro', ouvidos[s1] == {'sdp': 'ANSWER', 'erro': '', 'codec': 'h264'} and ouvidos[s2]['erro'] == 'A câmera não abriu.', ouvidos)
outro_chefe = User.objects.create_user(username='zzc.chefe2', email='zzc.chefe2@exemplo-teste.local', password='S3nha!teste', first_name='C2', hierarchy='SUPERADMIN')
cs2 = Client(); cs2.force_login(outro_chefe)
t('outra pessoa não lê a sessão de quem pediu', cs2.get(f'/cameras/aovivo/respostas/?ids={s1}').json()['respostas'] == {})
t('colaborador comum não lê', cc.get(f'/cameras/aovivo/respostas/?ids={s1}').status_code == 302)
t('outra pessoa não fecha a sessão', cs2.post('/cameras/aovivo/estado/', {'ids': s1, 'estado': 'FECHADA'}).json()['feitas'] == 0)
t('estado inventado é ignorado', cs.post('/cameras/aovivo/estado/', {'ids': s1, 'estado': 'X'}).json()['feitas'] == 0)
cs.post('/cameras/aovivo/estado/', {'ids': s1, 'estado': 'CONECTADA'})
t('conexão fica registrada, para diagnosticar', SessaoAoVivo.objects.get(id=s1).estado == 'CONECTADA')
t('sessão aberta continua', pedir(s1).json()['fechar'] == [])
cs.post('/cameras/aovivo/estado/', {'ids': s1, 'estado': 'FECHADA'})
t('fechou no navegador: agente é avisado', pedir(s1).json()['fechar'] == [s1])
t('id estranho do agente não derruba', pedir('nao-e-id', 5).status_code == 200)
SessaoAoVivo.objects.filter(id=s2).update(estado='PEDIDA', criada_em=timezone.now() - dt.timedelta(minutes=5))
t('pedido velho não é entregue', pedir().json()['ofertas'] == [])
SessaoAoVivo.objects.filter(id=s2).update(criada_em=timezone.now() - dt.timedelta(days=2))
abrir(cs, {'canal': cv1.id, 'sdp': SDP})
t('sessões antigas são limpas', not SessaoAoVivo.objects.filter(id=s2).exists())
ConfigCameras.objects.update(ativo=False)
t('módulo desligado não abre vídeo', abrir(cs, {'canal': cv1.id, 'sdp': SDP}).status_code == 403)
ConfigCameras.objects.update(ativo=True)
h = cs.get(f'/cameras/loja/{gv.id}/').content.decode()
s9 = abrir(cs, {'canal': cv1.id, 'sdp': SDP}).json()['sessoes'][0]['id']
cs.post('/cameras/aovivo/estado/', {'ids': s9, 'estado': 'FECHADA'})
responder({'id': s9, 'sdp': 'ATRASADA', 'codec': 'h264'})
sess9 = SessaoAoVivo.objects.get(id=s9)
t('resposta atrasada não reabre sessão fechada', sess9.estado == 'FECHADA' and sess9.respondida_em)
s10 = abrir(cs, {'canal': cv1.id, 'sdp': SDP}).json()['sessoes'][0]['id']
pedir(); responder({'id': s10, 'sdp': 'R', 'codec': 'h264'}); cs.post('/cameras/aovivo/estado/', {'ids': s10, 'estado': 'CONECTADA'}); cs.post('/cameras/aovivo/estado/', {'ids': s10, 'estado': 'FECHADA'})
sess10 = SessaoAoVivo.objects.get(id=s10)
t('registro guarda até onde a sessão chegou', sess10.entregue_em and sess10.respondida_em and sess10.conectada_em and sess10.estado == 'FECHADA')
r = anon.post('/cameras/agente/sinal/', {'versao': '2.2', 'ao_vivo': 'ok sessoes=1', 'log': 'linha ' * 3000}, **agv)
gv.refresh_from_db()
t('diagnóstico do agente chega ao portal', gv.ao_vivo_situacao == 'ok sessoes=1' and len(gv.log_agente) <= 8000 and gv.log_agente_em)
t('tela da loja traz o vídeo e o reprodutor', 'data-ao-vivo' in h and 'cameras/ao-vivo.js' in h and 'id="caVideo"' in h and 'id="caTelaCheia"' in h)
hp = cs.get('/cameras/').content.decode()
t('mosaico traz o vídeo de quem está online', f'data-ao-vivo="{cv1.id}"' in hp and f'data-ao-vivo="{coff.id}"' not in hp)
t('tela da loja traz o contador', 'caContadorVivo' in h)
t('configuração pede reinstalar quem está com agente antigo', 'data-sem-ao-vivo' in cs.get('/cameras/configuracao/').content.decode())

print('vídeo de 3 minutos, IA de visão e aprendizado (06/11/2026)')
import json as _json
import shutil as _shutil
import types as _types

from cameras import aprendizado
from cameras.models import EventoLoja  # noqa: F401
from regras import Evento  # noqa: E402

ConfigCameras.objects.update(ativo=True)
cache.clear()
perguntas = []
RESPOSTA_IA = {'risco': 82, 'classificacao': 'suspeito', 'resumo': 'Pessoa 1 pega o aparelho do mostruário e guarda na mochila.',
               'linha_do_tempo': [{'segundo': -25, 'o_que': 'pessoa 1 pega o aparelho'}, {'segundo': -5, 'o_que': 'sai pela porta'}],
               'sinais_de_risco': ['guardou na mochila'], 'sinais_normais': [], 'recomendacao': 'Conferir o vídeo em -25 s.',
               'conferir_no_video': [{'segundo': -25, 'o_que': 'a mão no mostruário'}], 'extra': 'ignorar'}


class _Completions:
    def create(self, **kw):
        perguntas.append(kw)
        return _types.SimpleNamespace(choices=[_types.SimpleNamespace(message=_types.SimpleNamespace(content=_json.dumps(RESPOSTA_IA)))])


falso_cliente = _types.SimpleNamespace(chat=_types.SimpleNamespace(completions=_Completions()))
mock.patch.object(ia, '_cliente', lambda: falso_cliente).start()
settings.OPENAI_API_KEY = 'chave-de-teste'
detalhes = {'zona': 'Mostruário', 'bolsa_ou_mochila_perto': True, 'segundos_com_pessoa_perto': 12, 'acao_segundo': -25, '_x': 1}
r = anon.post('/cameras/agente/alerta/', {'canal': 1, 'tipo': 'ITEM_SUMIU', 'confianca': 72, 'motivo': 'Aparelho saiu', 'video': '1',
                                          'detalhes': _json.dumps(detalhes), 'foto': SimpleUploadedFile('a.jpg', jpg, 'image/jpeg')}, **ag)
j = r.json()
av1 = AlertaCamera.objects.get(pk=j['alerta'])
t('agente 3.0: o portal pede os 3 minutos (1min30 antes e depois)', j['quer_video'] and j['antes'] == 90 and j['depois'] == 90
  and av1.video_status == 'AGUARDANDO', j)
t('guarda o que o detector mediu (sem chave interna)', av1.detalhes == {k: v for k, v in detalhes.items() if k != '_x'}, av1.detalhes)
t('já nasce com chance pelo histórico e prioridade', av1.chance_real is not None and av1.prioridade > 0 and av1.ia_status == '')
t('a IA espera o vídeo chegar', not perguntas)
j2 = anon.post('/cameras/agente/alerta/', {'canal': 1, 'tipo': 'ITEM_SUMIU', 'confianca': 80, 'video': '1'}, **ag).json()
t('repetição não pede outro vídeo', j2['alerta'] == av1.id and not j2['quer_video'])
mp4 = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 4 + bytes(range(256)) * 40
quadro_jpg = jpg
r = anon.post('/cameras/agente/clipe/', {'alerta': av1.id, 'segundos': '184', 'evento_segundo': '96', 'inicio': '2026-11-06T10:00:00-03:00',
                                         'video': SimpleUploadedFile('clipe.mp4', mp4, 'video/mp4'),
                                         'quadro_m25': SimpleUploadedFile('q.jpg', quadro_jpg, 'image/jpeg'),
                                         'quadro_0': SimpleUploadedFile('q.jpg', quadro_jpg, 'image/jpeg'),
                                         'quadro_60': SimpleUploadedFile('q.jpg', quadro_jpg, 'image/jpeg'),
                                         'quadro_9': SimpleUploadedFile('q.jpg', b'nao e jpg', 'image/jpeg')}, **ag)
av1.refresh_from_db()
t('o vídeo chega e fica guardado', r.status_code == 200 and av1.video_status == 'RECEBIDO' and av1.video_segundos == 184
  and av1.video_evento_segundo == 96 and av1.video_tamanho == len(mp4) and av1.video.name.endswith('.mp4'), r.content)
t('quadros-chave em ordem, sem o que não é JPEG', [q['segundo'] for q in av1.quadros] == [-25, 0, 60], av1.quadros)
t('nome do vídeo impossível de adivinhar', re.search(r'cameras/clipes/\d{4}/\d{2}/[0-9a-f]{32}\.mp4$', av1.video.name), av1.video.name)
t('a IA de visão analisou quando o vídeo chegou', len(perguntas) == 1 and av1.ia_status == 'FEITA' and av1.ia_risco == 82
  and av1.ia_classificacao == 'suspeito' and 'extra' not in av1.ia_analise and av1.ia_modelo)
pergunta = perguntas[0]
partes = pergunta['messages'][1]['content']
texto = partes[0]['text']
imagens = [p for p in partes if p['type'] == 'image_url']
t('a IA recebe os quadros em ordem, com o momento de perto em alta', len(imagens) == 3 and imagens[0]['image_url']['detail'] == 'low'
  and imagens[1]['image_url']['detail'] == 'high' and 'Quadro em -25 s:' in [p.get('text') for p in partes])
t('a IA recebe o que o detector mediu', 'bolsa_ou_mochila_perto = True' in texto and 'acao_segundo = -25' in texto, texto)
t('a IA aprende com os casos decididos (decisão, motivo e observação)', 'FALSO ALARME (Funcionário trabalhando)' in texto
  and 'quem conferiu anotou: "Funcionário guardou"' in texto and 'Motivos dos falsos alarmes desta câmera' in texto, texto)
t('a IA é instruída a não identificar pessoas', 'Nunca identifique pessoas' in pergunta['messages'][0]['content'] and pergunta['response_format'] == {'type': 'json_object'})
t('a prioridade passa a contar a IA', av1.prioridade == aprendizado.prioridade(av1) and av1.prioridade > 72 * 0 and av1.prioridade >= 60, av1.prioridade)
r = anon.post('/cameras/agente/clipe/', {'alerta': av1.id, 'video': SimpleUploadedFile('clipe.mp4', mp4, 'video/mp4')}, **ag)
t('reenvio do mesmo vídeo não duplica nem reanalisa', r.json().get('ja_tinha') and len(perguntas) == 1)
r = anon.post('/cameras/agente/clipe/', {'alerta': 999999, 'video': SimpleUploadedFile('c.mp4', mp4, 'video/mp4')}, **ag)
t('alerta inexistente: o agente pode descartar', r.status_code == 404 and r.json()['descartar'])
outro = AlertaCamera.objects.create(gravador=Gravador.objects.exclude(pk=g.pk).first(), tipo='ITEM_SUMIU', video_status='AGUARDANDO')
r = anon.post('/cameras/agente/clipe/', {'alerta': outro.id, 'video': SimpleUploadedFile('c.mp4', mp4, 'video/mp4')}, **ag)
t('não manda vídeo em alerta de outra loja', r.status_code == 404 and not AlertaCamera.objects.get(pk=outro.pk).video)
av_txt = AlertaCamera.objects.create(gravador=g, canal=c1, tipo='PERMANENCIA', video_status='AGUARDANDO')
r = anon.post('/cameras/agente/clipe/', {'alerta': av_txt.id, 'video': SimpleUploadedFile('c.mp4', b'nao e video nenhum', 'video/mp4')}, **ag)
t('arquivo que não é MP4 é recusado', r.status_code == 400 and r.json()['descartar'] and AlertaCamera.objects.get(pk=av_txt.pk).video_status == 'AGUARDANDO')
t('sem token não manda vídeo', anon.post('/cameras/agente/clipe/', {'alerta': av1.id}).status_code == 401)

print('vídeo e quadros na tela')
url_video = f'/cameras/alertas/{av1.id}/video/'
r = cs.get(url_video)
corpo = b''.join(r.streaming_content) if r.streaming else r.content
t('o vídeo toca pelo portal', r.status_code == 200 and r['Content-Type'] == 'video/mp4' and r['Accept-Ranges'] == 'bytes' and corpo == mp4)
r = cs.get(url_video, HTTP_RANGE='bytes=0-9')
t('pedaço do vídeo (para avançar e voltar)', r.status_code == 206 and r['Content-Range'] == f'bytes 0-9/{len(mp4)}' and r.content == mp4[:10])
r = cs.get(url_video, HTTP_RANGE='bytes=100-')
t('pedaço até o fim', r.status_code == 206 and r.content == mp4[100:] and r['Content-Range'] == f'bytes 100-{len(mp4) - 1}/{len(mp4)}')
r = cs.get(url_video, HTTP_RANGE='bytes=-50')
t('últimos bytes', r.status_code == 206 and r.content == mp4[-50:])
t('pedaço fora do arquivo: 416', cs.get(url_video, HTTP_RANGE=f'bytes={len(mp4) + 10}-').status_code == 416)
t('quem não é SUPERADMIN não vê o vídeo', cc.get(url_video).status_code == 302 and anon.get(url_video).status_code == 302)
t('ver o vídeo fica registrado', AcessoCamera.objects.filter(user=chefe, acao='ver_video', detalhe=f'#{av1.id}').exists())
t('quadro-chave abre; índice inventado não', cs.get(f'/cameras/alertas/{av1.id}/quadro/0/').status_code == 200
  and cs.get(f'/cameras/alertas/{av1.id}/quadro/7/').status_code == 404 and cc.get(f'/cameras/alertas/{av1.id}/quadro/0/').status_code == 302)
h = cs.get(f'/cameras/alertas/{av1.id}/').content.decode()
t('tela do alerta: vídeo, ir ao momento, linha do tempo e quadros', 'id="caVideo"' in h and 'data-evento="96"' in h and 'Ir para o momento do alerta' in h
  and f'/cameras/alertas/{av1.id}/quadro/2/' in h and 'data-ir="71"' in h)
t('tela do alerta: análise da IA', 'Pessoa 1 pega o aparelho do mostruário e guarda na mochila.' in h and 'Pesa contra' in h
  and 'data-ir-relativo="-25"' in h and 'risco 82%' in h and 'Suspeito' in h)
t('tela do alerta: histórico, medidas e motivos do falso alarme', 'O que o histórico diz' in h and 'Bolsa ou mochila perto</span><b>sim</b>' in h and 'Ação (s antes do alerta)</span><b>25</b>' in h
  and 'name="motivo_falso" value="FUNCIONARIO"' in h and 'ensina a análise' in h)
t('lista: prioridade, vídeo e veredito da IA', 'ca-selo-video' in (hl := cs.get('/cameras/alertas/').content.decode()) and 'IA: <b class="ca-classe suspeito">suspeito' in hl)
t('estado da IA para a tela', cs.get(f'/cameras/alertas/{av1.id}/ia/').json() == {'status': 'FEITA', 'video': 'RECEBIDO'})
cs.post(f'/cameras/alertas/{av1.id}/ia/')
t('analisar de novo', len(perguntas) == 2 and AcessoCamera.objects.filter(acao='reanalisar_alerta').exists())
RESPOSTA_IA_BACKUP = RESPOSTA_IA
_Completions.create = lambda self, **kw: (_ for _ in ()).throw(RuntimeError('cota estourada'))
cs.post(f'/cameras/alertas/{av1.id}/ia/')
av1.refresh_from_db()
t('IA com erro: alerta segue, com o motivo', av1.ia_status == 'ERRO' and 'cota estourada' in av1.ia_erro and 'cota estourada' in cs.get(f'/cameras/alertas/{av1.id}/').content.decode())


def _create(self, **kw):
    perguntas.append(kw)
    return _types.SimpleNamespace(choices=[_types.SimpleNamespace(message=_types.SimpleNamespace(content='lixo {"risco": "900", "classificacao": "inventada"} fim'))])


_Completions.create = _create
cs.post(f'/cameras/alertas/{av1.id}/ia/')
av1.refresh_from_db()
t('resposta torta da IA vira algo seguro', av1.ia_status == 'FEITA' and av1.ia_risco == 100 and av1.ia_classificacao == 'inconclusivo')

print('agente antigo e vídeo que não chegou')
perguntas.clear()
r = anon.post('/cameras/agente/alerta/', {'canal': 2, 'tipo': 'FORA_HORARIO', 'confianca': 85, 'foto': SimpleUploadedFile('a.jpg', jpg, 'image/jpeg')}, **ag)
antigo = AlertaCamera.objects.get(pk=r.json()['alerta'])
t('agente sem vídeo: não pede vídeo e a IA olha a foto na hora', not r.json()['quer_video'] and antigo.video_status == 'SEM_VIDEO'
  and antigo.ia_status == 'FEITA' and len(perguntas) == 1 and len([p for p in perguntas[0]['messages'][1]['content'] if p['type'] == 'image_url']) == 1)
atrasado = AlertaCamera.objects.create(gravador=g, canal=c1, tipo='PERMANENCIA', video_status='AGUARDANDO')
AlertaCamera.objects.filter(pk=atrasado.pk).update(criado_em=timezone.now() - dt.timedelta(minutes=servicos.VIDEO_ESPERA_MINUTOS + 5))
cs.get('/cameras/alertas/')
atrasado.refresh_from_db()
t('vídeo que não chegou a tempo: desiste e a IA tenta com o que tem', atrasado.video_status == 'SEM_VIDEO' and atrasado.ia_status == 'ERRO'
  and 'imagem' in atrasado.ia_erro, (atrasado.video_status, atrasado.ia_status, atrasado.ia_erro))

print('aprendizado')
cache.clear()
c2 = Canal.objects.get(gravador=g, numero=2)
base = aprendizado.avaliar('PERMANENCIA', g.id, c2.id)
t('sem decisões vale a taxa inicial do tipo', base['chance'] == 15 and base['decisoes_canal'] == 0, base)
for i in range(6):
    AlertaCamera.objects.create(gravador=g, canal=c2, tipo='PERMANENCIA', status='FALSO', motivo_falso='FUNCIONARIO', tratado_em=timezone.now())
aprendizado.esquecer()
ruim = aprendizado.avaliar('PERMANENCIA', g.id, c2.id)
t('só falso alarme: a chance cai', ruim['chance'] < base['chance'] and ruim['decisoes_canal'] == 6, ruim)
t('e o agente fica muito exigente nessa câmera', aprendizado.rigor('PERMANENCIA', g.id, c2.id) == aprendizado.RIGOR_MUITO_EXIGENTE)
for i in range(8):
    AlertaCamera.objects.create(gravador=g, canal=c2, tipo='FORA_HORARIO', status='CONFIRMADO', tratado_em=timezone.now())
aprendizado.esquecer()
t('só confirmado: fica mais sensível', aprendizado.rigor('FORA_HORARIO', g.id, c2.id) == aprendizado.RIGOR_MAIS_SENSIVEL)
sinal = anon.post('/cameras/agente/sinal/', {'versao': '3.0'}, **ag).json()
t('o ajuste vai para a loja no sinal', sinal['ajustes'].get('2') == {'PERMANENCIA': 2, 'FORA_HORARIO': -1}
  and sinal['video'] == {'antes': 90, 'depois': 90}, sinal.get('ajustes'))
outra_loja = Gravador.objects.exclude(pk=g.pk).first()
t('outra loja continua no normal', aprendizado.rigor('PERMANENCIA', outra_loja.id, None) == aprendizado.RIGOR_NORMAL)
novo = AlertaCamera.objects.create(gravador=g, canal=c2, tipo='PERMANENCIA', confianca=45)
aprendizado.aplicar(novo)
t('alerta novo dessa câmera nasce com prioridade baixa', novo.chance_real == ruim['chance'] and novo.prioridade < 45, (novo.chance_real, novo.prioridade))
alvo = AlertaCamera.objects.create(gravador=g, canal=c2, tipo='ITEM_SUMIU', confianca=70)
antes_decisao = aprendizado.avaliar('ITEM_SUMIU', g.id, c2.id)['decisoes_canal']
cs.post(f'/cameras/alertas/{alvo.id}/', {'status': 'CONFIRMADO', 'observacao': 'Guardou na mochila'})
t('confirmar já conta na hora (sem esperar o cache)', aprendizado.avaliar('ITEM_SUMIU', g.id, c2.id)['decisoes_canal'] == antes_decisao + 1)
h = cs.get('/cameras/aprendizado/').content.decode()
t('tela do aprendizado', 'Muito exigente' in h and 'Mais sensível' in h and 'Funcionário trabalhando (6)' in h and 'Acerto da IA de visão' in h, h[:200])
t('aprendizado só para o SUPERADMIN', cc.get('/cameras/aprendizado/').status_code == 302)
an = Analisador(Z, ajustes={'ITEM_SUMIU': 2})
s = rodar(an, firme + [(40 + i, [PES], True) for i in range(5)] + [(45 + i, [], True) for i in range(60)])
t('no agente: muito exigente espera o dobro para alertar', s == [(84, 'ITEM_SUMIU')], s)
s = rodar(Analisador(Z, ajustes={'ITEM_SUMIU': -1}), firme + [(40 + i, [PES], True) for i in range(5)] + [(45 + i, [], True) for i in range(60)])
t('no agente: mais sensível alerta antes', s == [(59, 'ITEM_SUMIU')], s)
s = rodar(Analisador(Z, ajustes={'FORA_HORARIO': 2}), [(i, [FORA], False) for i in range(8)])
t('no agente: loja fechada muito exigente pede mais quadros', s == [(5, 'FORA_HORARIO')], s)
s = rodar(Analisador(Z, ajustes={'ITEM_SUMIU': 'lixo'}), firme + [(40 + i, [PES], True) for i in range(5)] + [(45 + i, [], True) for i in range(30)])
t('no agente: ajuste estragado vale o normal', s == [(64, 'ITEM_SUMIU')], s)
BOLSA = {'classe': 'backpack', 'conf': .8, 'caixa': (.3, .45, .08, .1)}
an = Analisador(Z)
eventos = []
for tempo, det in [(i, [APA]) for i in range(40)] + [(40 + i, [PES, BOLSA]) for i in range(10)] + [(50 + i, []) for i in range(25)]:
    eventos += an.analisar(det, tempo, True)
ev = eventos[0] if eventos else Evento('X', 0, '')
t('no agente: o alerta leva as medidas e a nota sobe com a mochila', ev.tipo == 'ITEM_SUMIU' and ev.detalhes['bolsa_ou_mochila_perto']
  and ev.detalhes['segundos_com_pessoa_perto'] == 9 and ev.detalhes['acao_segundo'] == -20 and ev.confianca >= 80 and 'mochila' in ev.motivo,
  (ev.confianca, ev.detalhes, ev.motivo))

print('prazo de guarda dos vídeos')
velho_falso = AlertaCamera.objects.create(gravador=g, canal=c1, tipo='ITEM_SUMIU', status='FALSO', motivo_falso='DETECCAO')
velho_conf = AlertaCamera.objects.create(gravador=g, canal=c1, tipo='ITEM_SUMIU', status='CONFIRMADO')
for a in (velho_falso, velho_conf):
    servicos.registrar_clipe(a, SimpleUploadedFile('c.mp4', mp4, 'video/mp4'), [(0, jpg)])
    AlertaCamera.objects.filter(pk=a.pk).update(criado_em=timezone.now() - dt.timedelta(days=40), ia_resumo='resumo guardado')
velho_falso.refresh_from_db()
arquivos = [velho_falso.video.name] + [q['arquivo'] for q in velho_falso.quadros]
apagados = servicos.limpar_videos_antigos()
velho_falso.refresh_from_db(); velho_conf.refresh_from_db()
t('falso alarme com mais de 30 dias: vídeo e quadros apagados', apagados == 1 and velho_falso.video_status == 'APAGADO' and not velho_falso.video
  and velho_falso.quadros == [] and not any(default_storage.exists(n) for n in arquivos))
t('fica a decisão e a análise; furto confirmado fica guardado', velho_falso.ia_resumo == 'resumo guardado' and velho_falso.status == 'FALSO'
  and velho_conf.video_status == 'RECEBIDO' and default_storage.exists(velho_conf.video.name))
t('tela avisa que o vídeo foi apagado', 'apagado no fim do prazo' in cs.get(f'/cameras/alertas/{velho_falso.id}/').content.decode())
from core.agendador_diario import TAREFAS
t('a limpeza roda todo dia', any(c == 'cameras_limpar_videos' and f == 'cameras.servicos.limpar_videos_antigos' for c, _h, f, _d in TAREFAS))

print('agente: fila dos vídeos')
import gravacao  # noqa: E402
t('quadros-chave concentrados na ação', gravacao.momentos_dos_quadros(90, 90, -25) == [-80, -45, -35, -30, -27, -25, -22, 0, 15, 60])
t('ação fora da janela é limitada', min(gravacao.momentos_dos_quadros(30, 20, -500)) >= -30 and max(gravacao.momentos_dos_quadros(30, 20, 0)) <= 20)
ped = gravacao.Pedido(alerta=1, canal=1, evento=1000.0, momento=timezone.now(), antes=90, depois=90, acao=-25)
qs = [(1000.0 + s, f'q{s}'.encode()) for s in range(-90, 91, 2)]
esc = gravacao.escolher_quadros(ped, qs)
t('escolhe o quadro mais perto de cada momento, sem repetir', [s for s, _ in esc] == [-80, -46, -36, -30, -28, -26, -22, 0, 14, 60]
  and len({j for _, j in esc}) == len(esc), [s for s, _ in esc])
pasta_fila = pathlib.Path(tempfile.mkdtemp(prefix='zz-fila-'))
fila_v = gravacao.FilaDeClipes(str(pasta_fila))


def por_na_fila(alerta):
    (pasta_fila / f'{alerta}.mp4').write_bytes(mp4)
    (pasta_fila / f'{alerta}_q0.jpg').write_bytes(jpg)
    fila_v.registrar(alerta, {'alerta': alerta, 'segundos': 180, 'evento_segundo': 90, 'quadros': [{'segundo': 0, 'arquivo': f'{alerta}_q0.jpg'}]})


class PortalFalso:
    def __init__(self, resposta):
        self.resposta, self.enviados = resposta, []

    def clipe(self, dados, video, quadros):
        if isinstance(self.resposta, Exception):
            raise self.resposta
        self.enviados.append((dados['alerta'], os.path.getsize(video), quadros))
        return self.resposta


por_na_fila(7)
p_rede = PortalFalso(requests.ConnectionError('sem internet'))
fila_v.enviar(p_rede)
t('sem internet o vídeo espera na fila', (pasta_fila / '7.mp4').exists() and (pasta_fila / '7.json').exists())
p_ok = PortalFalso(_types.SimpleNamespace(status_code=200, json=lambda: {'ok': True}))
fila_v.enviar(p_ok)
t('subiu: sai da fila (vídeo, dados e quadros)', p_ok.enviados[0][:2] == (7, len(mp4)) and p_ok.enviados[0][2][0][0] == 0
  and not list(pasta_fila.iterdir()))
por_na_fila(8)
fila_v.enviar(PortalFalso(_types.SimpleNamespace(status_code=404, json=lambda: {'descartar': True})))
t('portal recusou de vez: sai da fila', not list(pasta_fila.iterdir()))
por_na_fila(9)
fila_v.enviar(PortalFalso(_types.SimpleNamespace(status_code=500, json=lambda: {})))
t('erro do portal: tenta de novo depois', (pasta_fila / '9.mp4').exists())
os.utime(pasta_fila / '9.json', (0, 0)); os.utime(pasta_fila / '9.mp4', (0, 0)); os.utime(pasta_fila / '9_q0.jpg', (0, 0))
fila_v.podar()
t('vídeo esquecido há dias é descartado', not list(pasta_fila.iterdir()))
_shutil.rmtree(pasta_fila, ignore_errors=True)
from agente import Portal as _Portal  # noqa: E402
enviado = {}


class _Sessao:
    headers = {}

    def post(self, url, data=None, files=None, timeout=None):
        enviado.update(url=url, data=data, nomes=[f[0] for f in files], timeout=timeout)
        return _types.SimpleNamespace(status_code=200)


pasta_p = pathlib.Path(tempfile.mkdtemp(prefix='zz-portal-'))
(pasta_p / 'v.mp4').write_bytes(mp4); (pasta_p / 'q.jpg').write_bytes(jpg)
pt = _Portal('https://portal.exemplo', 'tok'); pt.sessao = _Sessao()
pt.clipe({'alerta': 5, 'segundos': 180, 'evento_segundo': 90, 'inicio': 'x', 'quadros': []}, str(pasta_p / 'v.mp4'),
         [(-25, str(pasta_p / 'q.jpg')), (10, str(pasta_p / 'q.jpg')), (3, str(pasta_p / 'nao-existe.jpg'))])
t('agente manda o vídeo e os quadros no formato do portal', enviado['url'].endswith('/cameras/agente/clipe/') and enviado['nomes'] == ['video', 'quadro_m25', 'quadro_10']
  and enviado['data'] == {'alerta': 5, 'inicio': 'x', 'segundos': 180, 'evento_segundo': 90} and enviado['timeout'][1] >= 300, enviado)
_shutil.rmtree(pasta_p, ignore_errors=True)

print(f'\n{ok} OK, {fail} falha(s)')
