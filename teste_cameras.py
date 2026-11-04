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

print(f'\n{ok} OK, {fail} falha(s)')
