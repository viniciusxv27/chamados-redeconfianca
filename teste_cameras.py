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
from django.core.files.uploadedfile import SimpleUploadedFile

from cameras.importacao import importar_planilha
from cameras.models import AcessoCamera, AlertaCamera, Canal, ConfigCameras, Gravador
from cameras import servicos

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
t('tratar registra quem e quando', a.status == 'FALSO' and a.tratado_por == chefe and a.tratado_em and a.observacao)
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
g3 = Gravador.objects.create(loja='LOJA NOVA'); g3.definir_senha(SENHA); g3.save()
t('colaborador comum não baixa o instalador', cc.post(f'/cameras/configuracao/{g3.id}/instalador/').status_code == 302 and not Gravador.objects.get(pk=g3.id).token_hash)
t('GET não gera token', cs.get(f'/cameras/configuracao/{g3.id}/instalador/').status_code == 405 and not Gravador.objects.get(pk=g3.id).token_hash)
r = cs.post(f'/cameras/configuracao/{g3.id}/instalador/')
import zipfile as _z
pacote = _z.ZipFile(io.BytesIO(r.content))
nomes = pacote.namelist()
cfg = _j.loads(pacote.read('agente-cameras/config.json'))
g3.refresh_from_db()
t('pacote vem completo', r['Content-Type'] == 'application/zip' and {'agente-cameras/' + n for n in ('agente.py', 'descoberta.py', 'regras.py', 'instalar.bat', 'instalar.ps1', 'instalar.sh', 'config.json')} <= set(nomes), nomes)
t('token no pacote funciona e fica só resumido', g3.token_hash == Gravador.resumo(cfg['token']))
todo = b''.join(pacote.read(n) for n in nomes)
t('senha do gravador fora do pacote', SENHA.encode() not in todo)
t('sem download para loja sem gravador', cs.post(f'/cameras/configuracao/{sem.id}/instalador/').status_code == 404)
t('download fica registrado', AcessoCamera.objects.filter(acao='baixar_instalador', gravador=g3).exists())
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


class Fim(Exception):
    pass


class PortalFalso:
    def __init__(self, respostas):
        self.respostas, self.achados, self.fotos_recebidas = list(respostas), [], []

    def sinal(self):
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


with patch.object(ag_mod, 'Portal', return_value=pf), patch.object(ag_mod.Detector, 'se_instalado', return_value=None), \
        patch.object(ag_mod, 'descobrir', return_value={'ip': '192.168.0.9', 'porta': 554, 'caminho': '/c?x={canal}', 'canais': [1, 2]}) as procura, \
        patch.object(Camera, 'start', start_falso), patch.object(ag_mod, '_jpg', return_value=b'jpg'), patch.object(ag_mod.time, 'sleep'):
    try:
        ag_mod.rodar({'portal': 'https://x', 'token': 't'})
    except Fim:
        pass
t('sem IP: procura e manda o que achou', procura.call_count == 1 and procura.call_args[0][:3] == ('admin', 'a@b', 'S1') and pf.achados[0]['ip'] == '192.168.0.9', procura.call_args)
t('com IP: abre as câmeras com a senha protegida no endereço', any('admin:a%40b@192.168.0.9:554/c?x=1' in u for u in iniciadas), iniciadas)
t('manda só a foto pedida', pf.fotos_recebidas and list(pf.fotos_recebidas[0]) == [1], pf.fotos_recebidas)
t('IP mudou: reabre no novo', any('192.168.0.10' in u for u in iniciadas), iniciadas)
try:
    with patch.dict(sys.modules, {'ultralytics': None}):
        t('sem o pacote de IA, o agente segue só com imagem', ag_mod.Detector.se_instalado('x') is None)
except Exception as exc:
    t('sem o pacote de IA, o agente segue só com imagem', False, exc)

print(f'\n{ok} OK, {fail} falha(s)')
