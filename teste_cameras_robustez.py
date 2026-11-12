"""Câmeras (12/11/2026): limpeza e lote de alertas, indicadores com cobertura e ponto, saúde, avisos, atualização do agente e instalador. Banco descartável, senha fictícia; nada sai para a rede."""
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-camrob-'))
(PASTA / 'zz_camrob_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-camrob'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-camrob-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_camrob_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import datetime as dt
import hashlib
import json
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from cameras import avisos, ia, indicadores, servicos
from cameras.models import AlertaCamera, Canal, ConfigCameras, EventoLoja, Gravador
from core.models import Notification
from tangerino.models import MarcacaoPonto
from users.models import Sector

ia.EM_THREAD = False
avisos.EM_THREAD = False
sys.path.insert(0, str(pathlib.Path.cwd() / 'agente_cameras'))

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
# Nenhum WhatsApp de verdade: o dublê guarda o que sairia.
enviados = []
mock.patch('core.evolution.enviar_texto', lambda numero, texto, **k: (enviados.append((numero, texto)) or (True, 'ok'))).start()

chefe = User.objects.create_user(username='zzr.chefe', email='zzr.chefe@exemplo-teste.local', password='S3nha!teste', first_name='Che', hierarchy='SUPERADMIN', is_superuser=True)
comum = User.objects.create_user(username='zzr.comum', email='zzr.comum@exemplo-teste.local', password='S3nha!teste', first_name='Com', hierarchy='PADRAO')
cs = Client(); cs.force_login(chefe)
cc = Client(); cc.force_login(comum)
anon = Client(enforce_csrf_checks=True)
config = ConfigCameras.atual(); config.ativo = True; config.save()
setor = Sector.objects.create(name='Loja ZZ Robusta')
g = Gravador.objects.create(loja='ZZ ROBUSTA', setor=setor, ip_local='192.168.0.9')
c1 = Canal.objects.create(gravador=g, numero=1)
c2 = Canal.objects.create(gravador=g, numero=2)
token = g.gerar_token()
ag = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
jpg = b'\xff\xd8\xff\xe0' + b'0' * 200


def alertar(tipo, canal=1, **k):
    return anon.post('/cameras/agente/alerta/', {'canal': canal, 'tipo': tipo, 'confianca': k.pop('confianca', 80), **k}, **ag)


def horario(abre, fecha, tolerancia=60):
    Gravador.objects.filter(pk=g.pk).update(abre_as=abre, fecha_as=fecha, tolerancia_horario=tolerancia)
    g.refresh_from_db()


agora_local = timezone.localtime()


def hora_mais(minutos):
    return (agora_local + dt.timedelta(minutes=minutos)).time().replace(second=0, microsecond=0)


print('alertas: barulho que não vira alerta')
r = alertar('PERMANENCIA')
t('permanência nasce desligada: 200 e ignorado (o agente não reenvia)', r.status_code == 200 and r.json().get('ignorado') and not AlertaCamera.objects.exists(), r.content)
horario(hora_mais(30), hora_mais(600))      # abre daqui a 30 min: estamos na tolerância antes de abrir
r = alertar('FORA_HORARIO')
t('equipe chegando antes de abrir: ignorado', r.status_code == 200 and 'tolerância' in r.json().get('ignorado', '') and not AlertaCamera.objects.exists(), r.content)
horario(hora_mais(-600), hora_mais(-120))   # fechou há 2 h: fora da tolerância de 60 min
r1, r2 = alertar('FORA_HORARIO', 1), alertar('FORA_HORARIO', 2)
t('loja fechada de verdade: alerta', r1.status_code == 200 and r1.json().get('alerta'), r1.content)
t('a mesma pessoa vista por 2 câmeras é um caso só', r2.json().get('alerta') == r1.json().get('alerta') and AlertaCamera.objects.get().repeticoes == 2, r2.content)
Gravador.objects.filter(pk=g.pk).update(alerta_fora_horario=False)
t('tipo desligado na loja: ignorado', alertar('FORA_HORARIO').json().get('ignorado'))
Gravador.objects.filter(pk=g.pk).update(alerta_fora_horario=True, alerta_permanencia=True)
t('permanência ligada na loja: vira alerta', alertar('PERMANENCIA').json().get('alerta'))
t('aparelho que saiu do mostruário sempre alerta', alertar('ITEM_SUMIU').json().get('alerta'))
cs.post(f'/cameras/configuracao/{g.id}/', {'loja': g.loja, 'caminho_rtsp': g.caminho_rtsp, 'porta_rtsp': 554, 'canais': 2, 'tem_equipamento': '1', 'setor': setor.id})
g.refresh_from_db()
t('formulário sem os campos novos não desliga nem zera nada', g.alerta_fora_horario and g.alerta_permanencia and g.tolerancia_horario == 60, (g.alerta_fora_horario, g.tolerancia_horario))
cs.post(f'/cameras/configuracao/{g.id}/', {'loja': g.loja, 'caminho_rtsp': g.caminho_rtsp, 'porta_rtsp': 554, 'canais': 2, 'tem_equipamento': '1', 'setor': setor.id,
                                           'tem_tipos_alerta': '1', 'alerta_fora_horario': '1', 'tolerancia_horario': '30'})
g.refresh_from_db()
t('tela da loja liga/desliga tipos e muda a tolerância', g.alerta_fora_horario and not g.alerta_permanencia and g.tolerancia_horario == 30)
pagina = cs.get(f'/cameras/configuracao/{g.id}/').content.decode()
t('tela da loja mostra tolerância e tipos', 'tolerancia_horario' in pagina and 'alerta_permanencia' in pagina)

print('alertas: tela, lote e limpeza')
for i in range(3):
    a = AlertaCamera.objects.create(gravador=g, canal=c1, tipo='ITEM_SUMIU', prioridade=10 + i)
    a.foto.save('a.jpg', ContentFile(jpg))
fotos = [a.foto.name for a in AlertaCamera.objects.exclude(foto='')]
pagina = cs.get('/cameras/alertas/').content.decode()
t('lista com seleção em lote, resumo por tipo e limpeza do filtro', 'name="ids"' in pagina and 'ca-resumo-tipos' in pagina and 'Limpar: apagar os' in pagina)
t('filtro por tipo', 'Aparelho saiu' in cs.get('/cameras/alertas/?tipo=ITEM_SUMIU').content.decode()
  and cs.get('/cameras/alertas/?tipo=ITEM_SUMIU').context['total'] == AlertaCamera.objects.filter(tipo='ITEM_SUMIU').count())
t('filtro por período inválido não quebra', cs.get('/cameras/alertas/?de=lixo&ate=2026-13-40').status_code == 200)
t('colaborador comum não usa o lote', cc.post('/cameras/alertas/lote/', {'acao': 'apagar', 'ids': [1]}).status_code == 302 and AlertaCamera.objects.filter(pk=1).exists() is not None)
ids = list(AlertaCamera.objects.filter(tipo='ITEM_SUMIU').values_list('id', flat=True))
r = cs.post('/cameras/alertas/lote/', {'acao': 'FALSO', 'ids': ids[:2]})
t('falso alarme em lote exige o motivo', r.status_code == 302 and not AlertaCamera.objects.filter(status='FALSO').exists())
r = cs.post('/cameras/alertas/lote/', {'acao': 'FALSO', 'motivo_falso': 'FUNCIONARIO', 'ids': ids[:2], 'consulta': 'status=abertos'})
t('falso alarme em lote com motivo, quem e quando', AlertaCamera.objects.filter(status='FALSO', motivo_falso='FUNCIONARIO', tratado_por=chefe).count() == 2
  and r['Location'] == '/cameras/alertas/?status=abertos', r.get('Location'))
r = cs.post('/cameras/alertas/lote/', {'acao': 'apagar', 'ids': ids})
t('apagar em lote tira os alertas e as fotos do armazenamento', not AlertaCamera.objects.filter(id__in=ids).exists() and not any(default_storage.exists(f) for f in fotos), fotos)
restantes = AlertaCamera.objects.count()
cs.post('/cameras/alertas/lote/', {'acao': 'apagar', 'todos_do_filtro': '1', 'status': 'abertos', 'tipo': 'PERMANENCIA'})
t('limpar o filtro apaga só o que o filtro mostra', not AlertaCamera.objects.filter(tipo='PERMANENCIA').exists() and AlertaCamera.objects.filter(tipo='FORA_HORARIO').exists())
from django.core.management import call_command as cc_
import io as _io
buf = _io.StringIO(); cc_('limpar_alertas_cameras', '--abertos', stdout=buf)
t('comando sem --confirmar só conta', 'Seriam apagados' in buf.getvalue() and AlertaCamera.objects.exists(), buf.getvalue())
buf = _io.StringIO(); cc_('limpar_alertas_cameras', '--abertos', '--confirmar', stdout=buf)
t('comando com --confirmar apaga', 'Apagados' in buf.getvalue() and not AlertaCamera.objects.filter(status__in=['NOVO', 'ANALISE']).exists(), buf.getvalue())

print('alerta: ponto da equipe')
func = User.objects.create_user(username='zzr.func', email='zzr.func@exemplo-teste.local', password='S3nha!teste', first_name='Fun', sector=setor)
momento = timezone.now() - dt.timedelta(minutes=5)
MarcacaoPonto.objects.create(employee_id=1, usuario=func, nome='Fulana Teste', data=timezone.localdate(momento),
                             entrada1=momento - dt.timedelta(minutes=10), sincronizado_em=timezone.now())
a = AlertaCamera.objects.create(gravador=g, canal=c1, tipo='FORA_HORARIO')
AlertaCamera.objects.filter(pk=a.pk).update(criado_em=momento)
pagina = cs.get(f'/cameras/alertas/{a.id}/').content.decode()
t('detalhe mostra quem da equipe tinha ponto batido', 'Fulana Teste' in pagina and 'muito provável que seja ela' in pagina)

print('avisos fora da tela (nascem desligados)')
a.prioridade = 90; a.save()
t('desligado: não avisa', not avisos.avisar_alerta(a.id) and not enviados)
config.aviso_whatsapp, config.aviso_destino, config.aviso_prioridade = True, '27999990000', 60
config.save()
t('alerta acima da prioridade: avisa uma vez', avisos.avisar_alerta(a.id) and len(enviados) == 1 and 'não uma acusação' in enviados[0][1])
t('não repete o mesmo alerta', not avisos.avisar_alerta(a.id) and len(enviados) == 1)
t('notificação no portal para o SUPERADMIN', Notification.objects.filter(user=chefe, title__contains='ZZ ROBUSTA').exists())
baixo = AlertaCamera.objects.create(gravador=g, canal=c1, tipo='ITEM_SUMIU', prioridade=20)
t('abaixo da prioridade: não avisa', not avisos.avisar_alerta(baixo.id))
enviados.clear()
r = alertar('ITEM_SUMIU', confianca=95)
t('alerta novo que chega com prioridade alta avisa na hora', AlertaCamera.objects.get(pk=r.json()['alerta']).avisado_em is not None or AlertaCamera.objects.get(pk=r.json()['alerta']).prioridade < 60)
enviados.clear()
horario(hora_mais(-120), hora_mais(240))
Gravador.objects.filter(pk=g.pk).update(ultimo_contato=timezone.now() - dt.timedelta(minutes=50))
config.aviso_offline, config.aviso_offline_minutos = True, 30
config.save()
t('loja sem contato no horário: avisa', avisos.vigiar_lojas() == (1, 0) and 'sem contato' in enviados[-1][1])
t('não repete a mesma queda', avisos.vigiar_lojas() == (0, 0) and len(enviados) == 1)
Gravador.objects.filter(pk=g.pk).update(ultimo_contato=timezone.now())
t('loja voltou: avisa a volta', avisos.vigiar_lojas() == (0, 1) and 'voltou' in enviados[-1][1])
horario(hora_mais(60), hora_mais(240))
Gravador.objects.filter(pk=g.pk).update(ultimo_contato=timezone.now() - dt.timedelta(minutes=50), aviso_offline_em=None)
t('fora do horário da loja (computador desligado à noite): não avisa', avisos.vigiar_lojas() == (0, 0))
pagina = cs.get('/cameras/configuracao/').content.decode()
t('configuração mostra os avisos', 'Avisos fora da tela' in pagina and '27999990000' in pagina)
r = cs.post('/cameras/configuracao/', {'bloco': 'avisos', 'aviso_destino': '', 'aviso_prioridade': '70', 'aviso_offline_minutos': '15'})
config.refresh_from_db()
t('salvar avisos pela tela', not config.aviso_whatsapp and not config.aviso_offline and config.aviso_prioridade == 70 and config.aviso_offline_minutos == 15)

print('saúde do agente')
saude = {'cameras': {'1': {'ok': True, 'fps': 12.5}, '2': {'ok': False, 'erro': 'TimeoutError'}}, 'detector': 'YOLOX-s 640px', 'analise': True,
         'disco_livre_gb': 1.5, 'relogio_diferenca_s': 400, 'fila_clipes': 0, 'cpu': 20, 'segredo': 'x' * 500}
r = anon.post('/cameras/agente/sinal/', {'versao': '3.1', 'saude': json.dumps(saude)}, **ag)
g.refresh_from_db()
t('sinal guarda a saúde, sem texto gigante', g.saude['detector'] == 'YOLOX-s 640px' and len(g.saude['segredo']) <= 160 and g.saude_em)
problemas = servicos.problemas_de_saude(g)
t('aponta câmera sem imagem, disco e relógio', any('Sem imagem: Câmera 2' in p for _n, p in problemas) and any('disco' in p for _n, p in problemas)
  and any('adiantado' in p for _n, p in problemas), problemas)
t('sinal devolve a hora do portal, a tolerância e os tipos ligados', r.json().get('agora') and r.json().get('tolerancia') == g.tolerancia_horario and 'FORA_HORARIO' in r.json().get('alertas', {}))
t('saúde gigante é recusada', not servicos.registrar_saude(g, 'x' * (servicos.SAUDE_TAMANHO_MAX + 1)))
t('painel e loja mostram a saúde', 'precisam de atenção' in cs.get('/cameras/').content.decode() and 'Saúde do agente' in cs.get(f'/cameras/loja/{g.id}/').content.decode())

print('atualização automática do agente')
exe = PASTA / 'AgenteCameras.exe'; exe.write_bytes(b'MZ' + b'1' * 500)
buf = _io.StringIO(); cc_('publicar_instalador_cameras', str(exe), '--versao', '3.2', stdout=buf)
config.refresh_from_db()
t('publicar guarda versão, tamanho e SHA-256', config.instalador_versao == '3.2' and config.instalador_tamanho == 502
  and config.instalador_sha256 == hashlib.sha256(exe.read_bytes()).hexdigest(), buf.getvalue())
r = anon.post('/cameras/agente/sinal/', {'versao': '3.1'}, **ag)
t('desligada: o agente não recebe atualização', r.json().get('atualizacao') is None)
cs.post('/cameras/configuracao/', {'bloco': 'atualizacao', 'atualizar_agentes': '1'})
r = anon.post('/cameras/agente/sinal/', {'versao': '3.1'}, **ag)
info = r.json().get('atualizacao') or {}
t('ligada: agente 3.1 recebe versão, link e impressão digital', info.get('versao') == '3.2' and info.get('sha256') == config.instalador_sha256 and info.get('url'), info)
t('agente antigo (2.2) não recebe: ele não sabe se atualizar', anon.post('/cameras/agente/sinal/', {'versao': '2.2'}, **ag).json().get('atualizacao') is None)
t('agente já na versão não recebe', anon.post('/cameras/agente/sinal/', {'versao': '3.2'}, **ag).json().get('atualizacao') is None)
t('configuração mostra versão publicada e quem está atrás', 'versão 3.2' in cs.get('/cameras/configuracao/').content.decode())

print('indicadores: cobertura por área, mesas por câmera, ponto')
EventoLoja.objects.all().delete()
gi = Gravador.objects.create(loja='ZZ MEDIDA', setor=setor, abre_as=dt.time(9), fecha_as=dt.time(18), matinal_inicio=dt.time(7), matinal_fim=dt.time(9))
porta = Canal.objects.create(gravador=gi, numero=1, zonas=[{'nome': 'Porta', 'tipo': 'PORTA', 'x': 0, 'y': 0, 'w': .5, 'h': .5, 'sentido': 'baixo'}])
mesa1 = Canal.objects.create(gravador=gi, numero=2, zonas=[{'nome': 'Mesa 1', 'tipo': 'MESA', 'x': 0, 'y': 0, 'w': .5, 'h': .5}, {'nome': 'Tela', 'tipo': 'TELA', 'x': .5, 'y': .5, 'w': .4, 'h': .4}])
mesa2 = Canal.objects.create(gravador=gi, numero=3, zonas=[{'nome': 'Mesa 1', 'tipo': 'MESA', 'x': 0, 'y': 0, 'w': .5, 'h': .5}])
Gravador.objects.filter(pk=gi.pk).update(ultimo_contato=timezone.now())
dia = timezone.localdate() - dt.timedelta(days=1)


def em(h, m=0):
    return timezone.make_aware(dt.datetime.combine(dia, dt.time(h, m)))


def ev(tipo, canal, h, m=0, **k):
    inicio = em(h, m)
    return EventoLoja(gravador=gi, canal=canal, chave=f'{tipo}{canal.numero}{h}{m}{k.get("zona", "")}{k.get("duracao", 0)}', tipo=tipo,
                      zona=k.get('zona', ''), inicio=inicio, fim=inicio + dt.timedelta(seconds=k.get('duracao', 0)), data=dia, hora=h,
                      duracao=k.get('duracao', 0), pessoas=k.get('pessoas', 1))


eventos = []
for h in range(9, 18):                       # mesas cobertas o dia todo
    eventos.append(ev('COBERTURA', mesa1, h, zona='MESA,TELA'))
    eventos.append(ev('COBERTURA', mesa2, h, zona='MESA'))
eventos.append(ev('COBERTURA', mesa1, 8, zona='MESA,TELA'))   # tela coberta na janela da matinal
eventos += [ev('ATENDIMENTO', mesa1, 10, zona='Mesa 1', duracao=300), ev('ATENDIMENTO', mesa1, 11, zona='Mesa 1', duracao=600),
            ev('ATENDIMENTO', mesa2, 12, zona='Mesa 1', duracao=120), ev('ATENDIMENTO', mesa2, 13, zona='Mesa 1', duracao=4 * 3600)]
EventoLoja.objects.bulk_create(eventos)
d = indicadores.indicadores_do_periodo(dia, dia, gi.id)
l = d['linhas'][0]
t('porta sem cobertura da câmera da porta: "Sem medição", não zero', l['entradas'] is None and l['entradas_aviso'] == 'Sem medição', l['entradas_aviso'])
t('mesas da mesma loja com o mesmo nome contam separadas por câmera', sorted(m['nome'] for m in l['por_mesa']) == ['Câmera 2 · Mesa 1', 'Câmera 3 · Mesa 1'], l['por_mesa'])
t('ocupação de 4 h fica fora da média, contada à parte', l['atendimentos'] == 3 and l['longos'] == 1 and l['maior'] == '10 min 00 s', (l['atendimentos'], l['longos'], l['maior']))
t('mediana do atendimento típico', l['mediana'] == '5 min 00 s', l['mediana'])
t('matinal coberta, sem matinal e sem ninguém de ponto: "sem equipe"', l['matinal_status'][dia] == 'sem_equipe', l['matinal_status'])
MarcacaoPonto.objects.create(employee_id=2, usuario=func, nome='Fulana Teste', data=dia, entrada1=em(8, 30), saida1=em(12),
                             entrada2=em(13), saida2=em(17), sincronizado_em=timezone.now())
EventoLoja.objects.bulk_create([ev('COBERTURA', porta, h, zona='PORTA') for h in range(9, 18)]
                               + [ev('ENTRADA', porta, 10, m, zona='Porta') for m in range(0, 50, 10)])
l = indicadores.indicadores_do_periodo(dia, dia, gi.id)['linhas'][0]
t('com ponto: matinal vira "não" (havia equipe)', l['matinal_status'][dia] == 'nao', l['matinal_status'])
t('entradas da equipe no ponto descontadas: clientes estimados', l['entradas'] == 5 and l['entradas_equipe'] == 1 and l['clientes'] == 4,
  (l['entradas'], l['entradas_equipe'], l['clientes']))
t('cobertura do horário em %', l['cobertura'] == 100 and l['cobertura_porta'] == 100, (l['cobertura'], l['cobertura_porta']))
EventoLoja.objects.filter(gravador=gi, tipo='COBERTURA', canal=porta, hora__gte=12).delete()
l = indicadores.indicadores_do_periodo(dia, dia, gi.id)['linhas'][0]
t('porta coberta só metade do dia: número marcado como parcial', l['parcial_entradas'] and l['cobertura_porta'] < 80, l['cobertura_porta'])
EventoLoja.objects.create(gravador=gi, canal=porta, chave='velha', tipo='COBERTURA', zona='', inicio=em(9), fim=em(9), data=dia - dt.timedelta(days=1), hora=9)
EventoLoja.objects.create(gravador=gi, canal=porta, chave='nada', tipo='COBERTURA', zona='-', inicio=em(10), fim=em(10), data=dia - dt.timedelta(days=1), hora=10)
lo = indicadores._Loja(gi, dia - dt.timedelta(days=1), dia - dt.timedelta(days=1),
                       [(porta.id, dia - dt.timedelta(days=1), 9, ''), (porta.id, dia - dt.timedelta(days=1), 10, '-')], [], {})
t('cobertura de agente antigo vale pela área marcada; "-" (nada medido) não vale',
  lo.cobertas['PORTA'] == {(dia - dt.timedelta(days=1), 9)}, lo.cobertas)
r = cs.get(f'/cameras/indicadores/?de={dia:%Y-%m-%d}&ate={dia:%Y-%m-%d}&loja={gi.id}')
html = r.content.decode()
t('tela: cobertura, clientes, mediana e ocupação longa', r.status_code == 200 and 'Cobertura' in html and '≈ 4 clientes' in html and 'ocupação longa' in html)
r = cs.get(f'/cameras/indicadores/?de={dia - dt.timedelta(days=2):%Y-%m-%d}&ate={dia:%Y-%m-%d}&loja={gi.id}')
t('uma loja em vários dias: tabela dia a dia', 'dia a dia' in r.content.decode())
r = cs.get(f'/cameras/indicadores/?de={dia:%Y-%m-%d}&ate={dia:%Y-%m-%d}&loja={gi.id}&formato=csv')
csv_ = r.content.decode('utf-8-sig')
t('CSV por loja e dia', r['Content-Type'].startswith('text/csv') and 'ZZ MEDIDA' in csv_ and 'Clientes estimados' in csv_, csv_[:200])
t('colaborador comum não exporta', cc.get(f'/cameras/indicadores/?formato=csv').status_code == 302)

print('agente 3.1 (sem câmera nem rede)')
import agente as ag_mod  # noqa: E402
from regras import Metricas  # noqa: E402
rel = ag_mod.Relogio()
rel.acertar((timezone.now() + dt.timedelta(minutes=10)).isoformat())
t('relógio: corrige o computador atrasado', 590 < rel.diferenca < 610, rel.diferenca)
rel.acertar(timezone.now().isoformat())
t('relógio: diferença pequena é a demora da resposta, não mexe', rel.diferenca == 0.0)
rel.acertar('lixo')
t('relógio: resposta torta não quebra', rel.diferenca == 0.0)
m7 = dt.datetime(2026, 11, 12, 7, 30)
t('tolerância antes de abrir', ag_mod.na_tolerancia('08:00', '20:00', 60, m7) and not ag_mod.na_tolerancia('08:00', '20:00', 15, m7))
t('tolerância depois de fechar', ag_mod.na_tolerancia('08:00', '20:00', 60, dt.datetime(2026, 11, 12, 20, 30)) and not ag_mod.na_tolerancia('08:00', '20:00', 60, dt.datetime(2026, 11, 12, 23)))
mt = Metricas(zonas=[{'tipo': 'MESA', 'nome': 'Mesa 1', 'x': 0, 'y': 0, 'w': .5, 'h': .5}, {'tipo': 'MOSTRUARIO', 'x': 0, 'y': 0, 'w': 1, 'h': 1}])
cob = [e for e in mt.analisar([], 0, '10:00') if e['tipo'] == 'COBERTURA']
t('cobertura diz quais áreas mediu', cob and cob[0]['zona'] == 'MESA', cob)
cob = [e for e in Metricas(zonas=[]).analisar([], 0, '10:00') if e['tipo'] == 'COBERTURA']
t('câmera sem área de medida: "-"', cob and cob[0]['zona'] == '-', cob)


class RespostaHttp:
    def __init__(self, corpo):
        self.corpo = corpo
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def raise_for_status(self): pass
    def iter_content(self, n): yield self.corpo


class HttpAtual:
    def __init__(self, corpo): self.corpo, self.pedidos = corpo, []
    def get(self, url, **k):
        self.pedidos.append(url)
        return RespostaHttp(self.corpo)


pasta_ag = pathlib.Path(tempfile.mkdtemp())
corpo = b'MZ' + b'novo' * 100
comandos = []
at = ag_mod.Atualizador(str(pasta_ag), http=HttpAtual(corpo), rodar=lambda *c: comandos.append(c) or mock.Mock(returncode=0, stdout='', stderr=''), em_thread=False)
info_ok = {'versao': '3.9', 'url': 'https://portal/x.exe', 'sha256': hashlib.sha256(corpo).hexdigest(), 'tamanho': len(corpo)}
t('não atualiza para versão igual ou mais velha', not at.talvez({**info_ok, 'versao': ag_mod.VERSAO}) and not at.talvez({**info_ok, 'versao': '1.0'}))
with patch.object(ag_mod.os, 'name', 'nt'):
    t('baixa, confere e instala numa tarefa à parte do Windows', at.talvez(info_ok) and (pasta_ag / 'atualizacao' / 'AgenteCameras-3.9.exe').read_bytes() == corpo
      and any('/Create' in c and ag_mod.TAREFA_ATUALIZAR in c and any('/S' in x for x in c) for c in comandos), comandos)
t('não tenta a mesma versão de novo logo em seguida', not at.talvez(info_ok))
at2 = ag_mod.Atualizador(str(pasta_ag), http=HttpAtual(corpo + b'adulterado'), rodar=lambda *c: comandos.append(c), em_thread=False)
comandos.clear()
with patch.object(ag_mod.os, 'name', 'nt'):
    at2.talvez({**info_ok, 'tamanho': 0})
t('impressão digital diferente: não instala', not comandos and 'SHA-256' in at2.situacao, at2.situacao)
at3 = ag_mod.Atualizador(str(pasta_ag), http=HttpAtual(corpo), rodar=lambda *c: comandos.append(c), em_thread=False)
at3.talvez({**info_ok, 'url': 'http://portal/x.exe'})
t('sem https: não baixa', not at3.http.pedidos and 'https' in at3.situacao, at3.situacao)


class CamFalsa:
    def __init__(self, ok_): self.ok_ = ok_
    def saude(self, agora): return {'ok': self.ok_}


class FilaFalsa:
    eventos = [1, 2]


s = ag_mod.montar_saude({1: CamFalsa(True), 2: CamFalsa(False)}, None, FilaFalsa(), None, str(pasta_ag), 0)
t('saúde do agente: câmeras, filas, disco e versão', s['cameras']['2'] == {'ok': False} and s['fila_medicoes'] == 2 and s['analise'] is False
  and 'disco_livre_gb' in s and s['versao'] == ag_mod.VERSAO, s)
t('saúde cabe no limite do portal', len(json.dumps(s)) < servicos.SAUDE_TAMANHO_MAX)

print('instalador do Windows')
import xml.dom.minidom as _md  # noqa: E402
import instalar as ins  # noqa: E402
xml = ins.xml_da_tarefa('C:\\ProgramData\\AgenteCameras')
_md.parseString(xml.replace('encoding="UTF-16"', 'encoding="UTF-8"'))
t('tarefa: liga no boot e a vigia religa a cada 5 min sem abrir duas', '<Interval>PT5M</Interval>' in xml and 'IgnoreNew' in xml and 'BootTrigger' in xml)
pasta_ins = pathlib.Path(tempfile.mkdtemp())
(pasta_ins / 'config.json').write_text(json.dumps({'portal': 'https://portal', 'token': 'tk'}))
rodados = []
inst = ins.Instalacao(str(pasta_ins), rodar=lambda *c: rodados.append(c) or mock.Mock(returncode=0, stdout='', stderr=''),
                      esperar=lambda s: None, http=mock.Mock())
inst.remover()
t('remover: tira as duas tarefas e a regra do firewall, e avisa o portal',
  ('schtasks', '/Delete', '/TN', ins.TAREFA, '/F') in rodados and ('schtasks', '/Delete', '/TN', ins.TAREFA_ATUALIZAR, '/F') in rodados
  and any(c[:4] == ('netsh', 'advfirewall', 'firewall', 'delete') for c in rodados) and inst.http.post.called, rodados)
inst = ins.Instalacao(str(pasta_ins), rodar=lambda *c: mock.Mock(returncode=0, stdout='', stderr=''), esperar=lambda s: None, http=mock.Mock())
(pasta_ins / 'agente.log').write_text('x')
inst.confirmar_agente()
t('agente rodando sem falar com o portal: avisa, não falha', inst.avisos and 'ainda não conversou' in inst.avisos[0])
(pasta_ins / 'contato.txt').write_text('agora')
inst = ins.Instalacao(str(pasta_ins), rodar=lambda *c: mock.Mock(returncode=0, stdout='', stderr=''), esperar=lambda s: None, http=mock.Mock())
inst.inicio -= 5
inst.confirmar_agente()
t('agente falou com o portal: sem aviso', not inst.avisos, inst.avisos)
inst.conferir_relogio((dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)).strftime('%a, %d %b %Y %H:%M:%S GMT'))
t('relógio do Windows muito errado: avisa na hora de instalar', inst.avisos and 'adiantado' in inst.avisos[0], inst.avisos)
t('desinstalar pelo instalador.py --remover não trava', ins.principal(['instalar.py', '--remover'], ins.Instalacao(str(pasta_ins), rodar=lambda *c: mock.Mock(returncode=0), http=mock.Mock())) == 0)

print(f'\n{ok} ok, {fail} falha(s)')
sys.exit(1 if fail else 0)
