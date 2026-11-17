"""App mobile — módulo Câmeras (/api/mobile/v1/m/cameras/).

Roda dentro de uma transação desfeita no fim. Fotos, quadros e vídeo vão para um storage em
memória (os campos são trocados só durante o teste): nada sobe para o MinIO. Nenhuma câmera
real é tocada: as lojas, câmeras, alertas e medições são criados aqui. A IA do alerta é
trocada por um dublê (nada sai para fora).
"""
import os
import sys
from datetime import timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
# Cache em memória: o `default` é o Redis remoto do dev e limpar ele seria um FLUSHDB do ambiente todo.
import redeconfianca.settings as _cfg  # noqa: E402
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'} for n in _cfg.CACHES}
django.setup()

from django.conf import settings
from django.core.cache import cache, caches

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.db import transaction
from django.test import Client
from django.utils import timezone

from cameras import servicos
from cameras.indicadores import indicadores_do_periodo
from cameras.models import AcessoCamera, AlertaCamera, Canal, ConfigCameras, EventoLoja, Gravador, SessaoAoVivo
from users.models import Sector

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
M = f'{BASE}/m/cameras'

JPG = b'\xff\xd8\xff\xe0' + b'0' * 200 + b'\xff\xd9'
MP4 = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 4000


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def corpo(r):
    return b''.join(r.streaming_content) if getattr(r, 'streaming', False) else r.content


for nome in settings.CACHES:
    caches[nome].clear()

memoria = InMemoryStorage()
marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(AlertaCamera._meta.get_field('foto'), 'storage', memoria), \
            mock.patch.object(AlertaCamera._meta.get_field('video'), 'storage', memoria), \
            mock.patch('cameras.ia.disparar') as ia_disparar:
        setor = Sector.objects.create(name='ZZ Setor Cameras App')

        def novo(apelido, hierarquia='PADRAO', **kw):
            return User.objects.create_user(
                username=f'zzcam.{apelido}', email=f'zzcam.{apelido}@exemplo-teste.local', password='S3nha!teste',
                first_name=f'ZZ{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia, **kw)

        chefe = novo('chefe', 'SUPERADMIN')
        comum = novo('comum')
        admin = novo('admin', 'ADMIN')
        root = novo('root', is_superuser=True)

        agora = timezone.now()
        g = Gravador.objects.create(loja='ZZ Loja Câmera App', coordenador='ZZ Coord', token_hash='x' * 64,
                                    ultimo_contato=agora, versao_agente='3.1.0')
        g_off = Gravador.objects.create(loja='ZZ Loja Sem Contato', token_hash='y' * 64,
                                        ultimo_contato=agora - timedelta(hours=2), versao_agente='3.1.0')
        c1 = Canal.objects.create(gravador=g, numero=1, nome='Porta',
                                  zonas=[{'nome': 'Porta', 'tipo': 'PORTA', 'x': .1, 'y': .1, 'w': .5, 'h': .5, 'sentido': 'baixo'}])
        c2 = Canal.objects.create(gravador=g, numero=2)
        Canal.objects.create(gravador=g, numero=3, ativo=False)

        a1 = AlertaCamera(gravador=g, canal=c2, tipo='ITEM_SUMIU', confianca=80, motivo='Aparelho saiu', prioridade=70,
                          detalhes={'zona': 'Mostruário', 'pessoas': 2, '_interno': 1})
        a1.foto.save('a.jpg', ContentFile(JPG), save=False)
        a1.save()
        servicos.registrar_clipe(a1, ContentFile(MP4, name='c.mp4'), [(-10, JPG), (5, JPG)], segundos=180)
        a2 = AlertaCamera.objects.create(gravador=g_off, tipo='FORA_HORARIO', confianca=60, status='CONFIRMADO')

        def entrar(user):
            r = Client().post(f'{BASE}/auth/login/', {'email': user.email, 'senha': 'S3nha!teste',
                                                      'plataforma': 'android', 'dispositivo': 'ZZ'},
                              content_type='application/json')
            return Client(HTTP_AUTHORIZATION=f'Bearer {r.json()["token"]}')

        cc, cm, ca, cr = entrar(chefe), entrar(comum), entrar(admin), entrar(root)
        anon = Client()

        print('== ENTRADA (cameras/permissoes.py e_superadmin) ==')
        from api_mobile.modulos import cameras as mod
        t('SUPERADMIN vê o módulo em /modulos/', 'cameras' in [m['chave'] for m in cc.get(f'{BASE}/modulos/').json()])
        t('is_superuser também (mesma regra da web)', 'cameras' in [m['chave'] for m in cr.get(f'{BASE}/modulos/').json()])
        t('colaborador não vê o módulo', 'cameras' not in [m['chave'] for m in cm.get(f'{BASE}/modulos/').json()])
        t('ADMINISTRAÇÃO não vê o módulo', 'cameras' not in [m['chave'] for m in ca.get(f'{BASE}/modulos/').json()])
        for rota in ('', f'lojas/{g.pk}/', 'alertas/', f'alertas/{a1.pk}/', 'indicadores/'):
            r = cm.get(f'{M}/{rota}')
            t(f'colaborador: 403 em /{rota}', r.status_code == 403, r.status_code)
        r = cm.post(f'{M}/alertas/{a1.pk}/tratar/', {'status': 'ANALISE'}, content_type='application/json')
        t('colaborador não trata alerta: 403', r.status_code == 403, r.status_code)
        t('sem token: 401/403', anon.get(f'{M}/').status_code in (401, 403))
        inativo = novo('inativo', 'SUPERADMIN', is_active=False)
        t('pode_entrar: SUPERADMIN inativo não entra', mod.pode_entrar(inativo) is False and mod.pode_entrar(chefe) is True)

        print('\n== INÍCIO (mosaico) ==')
        servicos.guardar_foto(c1.pk, JPG)
        antes = AcessoCamera.objects.filter(user=chefe).count()
        r = cc.get(f'{M}/')
        js = r.json()
        t('início responde 200', r.status_code == 200, r.content[:300])
        lojas = {l['id']: l for l in js['lojas']}
        t('lista as lojas com situação', lojas[g.pk]['situacao'] == 'online' and lojas[g_off.pk]['situacao'] == 'offline',
          (lojas.get(g.pk), lojas.get(g_off.pk)))
        t('câmeras ativas por loja (desativada fora)', lojas[g.pk]['cameras'] == 2, lojas[g.pk]['cameras'])
        t('alertas novos por loja', lojas[g.pk]['alertas_novos'] == 1 and lojas[g_off.pk]['alertas_novos'] == 0)
        t('última vez visto', lojas[g_off.pk]['ultimo_contato'] is not None)
        t('foto da câmera principal por link assinado', '/api/mobile/v1/arquivo/' in (lojas[g.pk]['foto'] or ''))
        t('sem foto no cache: sem link', lojas[g_off.pk]['foto'] is None)
        t('recentes: alertas abertos', a1.pk in [a['id'] for a in js['recentes']] and a2.pk not in [a['id'] for a in js['recentes']])
        t('marca "olhando o mosaico" como a web', cache.get(servicos.CHAVE_VENDO_MOSAICO) == 1)
        t('registra o acesso (LGPD) como a web', AcessoCamera.objects.filter(user=chefe, acao='ver_mosaico',
                                                                            detalhe__contains='pelo app').count() == 1
          and AcessoCamera.objects.filter(user=chefe).count() == antes + 1)
        r = anon.get(lojas[g.pk]['foto'])
        t('link da foto abre a imagem do cache', r.status_code == 200 and corpo(r) == JPG
          and r['Content-Type'] == 'image/jpeg', r.status_code)
        forjado = signing.dumps({'u': comum.pk, 't': 'cameras_foto', 'i': str(c1.pk)}, salt='api_mobile.arquivo', compress=True)
        t('link assinado de quem não tem acesso: 404', anon.get(f'{BASE}/arquivo/{forjado}/').status_code == 404)

        print('\n== LOJA ==')
        r = cc.get(f'{M}/lojas/{g.pk}/')
        d = r.json()
        t('loja responde 200', r.status_code == 200, r.content[:300])
        t('câmeras da loja com foto quando há', [c['id'] for c in d['cameras_lista']] == [c1.pk, c2.pk]
          and d['cameras_lista'][0]['foto'] and d['cameras_lista'][1]['foto'] is None, d.get('cameras_lista'))
        t('ao vivo disponível (agente 3.1 online) — só pelo portal', d['ao_vivo']['disponivel'] is ConfigCameras.atual().ativo)
        t('últimos alertas da loja', [a['id'] for a in d['alertas']] == [a1.pk])
        t('caminho da web para ver ao vivo', d['caminho_web'] == f'/cameras/loja/{g.pk}/')
        t('marca "olhando a loja" como a web', cache.get(f'cameras:vendo:{g.pk}') == 1)
        r = cc.get(f'{M}/lojas/{g_off.pk}/').json()
        t('loja sem contato: ao vivo indisponível com motivo', r['ao_vivo']['disponivel'] is False and r['ao_vivo']['motivo'])
        t('loja que não existe: 404', cc.get(f'{M}/lojas/999999999/').status_code == 404)
        t('nenhum pedido de vídeo ao vivo criado', not SessaoAoVivo.objects.filter(gravador__in=[g, g_off]).exists())

        print('\n== ALERTAS ==')
        r = cc.get(f'{M}/alertas/', {'loja': g.pk})
        js = r.json()
        t('lista (abertos) da loja', r.status_code == 200 and [a['id'] for a in js['alertas']] == [a1.pk], r.content[:300])
        t('filtro de status igual à web', [a['id'] for a in cc.get(f'{M}/alertas/', {'status': 'CONFIRMADO', 'loja': g_off.pk}).json()['alertas']] == [a2.pk])
        t('filtro de tipo', cc.get(f'{M}/alertas/', {'loja': g.pk, 'tipo': 'FORA_HORARIO'}).json()['alertas'] == [])
        t('miniatura por link', '/api/mobile/v1/arquivo/' in (js['alertas'][0]['foto'] or ''))
        t('opções de filtro', any(o['valor'] == str(g.pk) for o in js['opcoes']['lojas']) and js['opcoes']['status'][0]['valor'] == 'abertos')

        r = cc.get(f'{M}/alertas/{a1.pk}/')
        d = r.json()
        t('detalhe do alerta: 200', r.status_code == 200, r.content[:300])
        t('medidas como a web (sem as internas)', [m['rotulo'] for m in d['medidas']] == ['Área', 'Pessoas'], d.get('medidas'))
        t('quadros-chave com segundo no vídeo', [(q['segundo'], q['no_video']) for q in d['quadros']] == [(-10, 80), (5, 95)], d.get('quadros'))
        t('vídeo por link assinado próprio', '/api/mobile/v1/m/cameras/video/' in (d['video'] or ''))
        t('alerta que não existe: 404', cc.get(f'{M}/alertas/999999999/').status_code == 404)
        t('registra ver_alerta', AcessoCamera.objects.filter(user=chefe, acao='ver_alerta', detalhe__startswith=f'#{a1.pk}').exists())

        r = anon.get(d['foto'])
        t('foto do alerta abre', r.status_code == 200 and corpo(r) == JPG, r.status_code)
        r = anon.get(d['quadros'][1]['imagem'])
        t('quadro do alerta abre', r.status_code == 200 and corpo(r) == JPG, r.status_code)
        forjado = signing.dumps({'u': chefe.pk, 't': 'cameras_quadro', 'i': f'{a1.pk}:9'}, salt='api_mobile.arquivo', compress=True)
        t('quadro que não existe: 404', anon.get(f'{BASE}/arquivo/{forjado}/').status_code == 404)

        r = anon.get(d['video'], HTTP_RANGE='bytes=0-99')
        t('vídeo responde por pedaço (Range) para o iPhone', r.status_code == 206 and len(r.content) == 100
          and r['Content-Range'] == f'bytes 0-99/{len(MP4)}', r.status_code)
        r = anon.get(d['video'])
        t('vídeo inteiro sem Range', r.status_code == 200 and corpo(r) == MP4, r.status_code)
        t('registra ver_video com o dono do link', AcessoCamera.objects.filter(user=chefe, acao='ver_video').exists())
        forjado = signing.dumps({'u': comum.pk, 'a': a1.pk}, salt='api_mobile.cameras.video', compress=True)
        t('vídeo com link de quem não tem acesso: 404', anon.get(f'{M}/video/{forjado}/').status_code == 404)
        t('vídeo com link adulterado: 404', anon.get(d['video'].rstrip('/')[:-2] + 'xx/').status_code == 404)
        forjado = signing.dumps({'u': chefe.pk, 'a': a2.pk}, salt='api_mobile.cameras.video', compress=True)
        t('alerta sem vídeo: 404', anon.get(f'{M}/video/{forjado}/').status_code == 404)
        with mock.patch('django.core.signing.time.time', return_value=__import__('time').time() + 16 * 60):
            t('vídeo com link vencido: 404', anon.get(d['video']).status_code == 404)

        print('\n== TRATAR ALERTA ==')
        r = cc.post(f'{M}/alertas/{a1.pk}/tratar/', {'status': 'FALSO'}, content_type='application/json')
        t('falso alarme sem motivo: 400 (regra da web)', r.status_code == 400 and 'falso alarme' in r.json()['erro'], r.content[:200])
        r = cc.post(f'{M}/alertas/{a1.pk}/tratar/', {'status': 'XYZ'}, content_type='application/json')
        t('situação inválida: 400', r.status_code == 400)
        r = cc.post(f'{M}/alertas/{a1.pk}/tratar/', {'status': 'FALSO', 'motivo_falso': 'FUNCIONARIO', 'observacao': ' era o vendedor '},
                    content_type='application/json')
        a1.refresh_from_db()
        t('marca falso alarme: 200', r.status_code == 200 and r.json()['status'] == 'FALSO', r.content[:300])
        t('gravou como a web', a1.status == 'FALSO' and a1.motivo_falso == 'FUNCIONARIO' and a1.observacao == 'era o vendedor'
          and a1.tratado_por_id == chefe.pk and a1.tratado_em is not None)
        t('registra tratar_alerta', AcessoCamera.objects.filter(user=chefe, acao='tratar_alerta', detalhe__contains='FALSO').exists())
        r = cc.post(f'{M}/alertas/{a1.pk}/tratar/', {'status': 'ANALISE', 'motivo_falso': 'FUNCIONARIO'}, content_type='application/json')
        a1.refresh_from_db()
        t('em análise limpa o motivo do falso', r.status_code == 200 and a1.status == 'ANALISE' and a1.motivo_falso == '')
        t('alerta inexistente: 404', cc.post(f'{M}/alertas/999999999/tratar/', {'status': 'ANALISE'},
                                             content_type='application/json').status_code == 404)
        t('IA não foi disparada pelo app', not ia_disparar.called)

        print('\n== INDICADORES ==')
        hoje = timezone.localdate()
        base_hora = timezone.make_aware(timezone.datetime.combine(hoje, timezone.datetime.min.time())) + timedelta(hours=10)
        EventoLoja.objects.create(gravador=g, canal=c1, chave='zzcob', tipo='COBERTURA', inicio=base_hora,
                                  fim=base_hora + timedelta(minutes=59), data=hoje, hora=10)
        for i in range(3):
            EventoLoja.objects.create(gravador=g, canal=c1, chave=f'zzent{i}', tipo='ENTRADA', zona='Porta',
                                      inicio=base_hora + timedelta(minutes=i), fim=base_hora + timedelta(minutes=i),
                                      data=hoje, hora=10, pessoas=1)
        r = cc.get(f'{M}/indicadores/', {'loja': g.pk})
        d = r.json()
        web = indicadores_do_periodo(hoje, hoje, g.pk)
        t('indicadores do dia: 200', r.status_code == 200, r.content[:300])
        t('entradas pela porta = conta da web', d['total_entradas'] == 3 == web['total_entradas'], (d.get('total_entradas'), web['total_entradas']))
        t('pico por hora', d['linhas'][0]['pico'] == '10h (3)', d['linhas'][0].get('pico'))
        t('mesa e matinal sem área: aviso, não zero', d['linhas'][0]['mesas_aviso'] == 'Marque a mesa'
          and d['linhas'][0]['matinal_aviso'] == 'Marque a tela', d['linhas'][0])
        t('dia a dia quando é uma loja', len(d['linhas'][0]['por_dia']) == 1 and d['linhas'][0]['por_dia'][0]['entradas'] == 3)
        r = cc.get(f'{M}/indicadores/', {'loja': g.pk, 'de': (hoje - timedelta(days=6)).isoformat(), 'ate': hoje.isoformat()})
        d = r.json()
        t('período de 7 dias com comparação', r.status_code == 200 and d['um_dia'] is False and d['anterior'] is not None
          and len(d['linhas'][0]['por_dia']) == 7)
        r = cc.get(f'{M}/indicadores/', {'de': '2026-01-01', 'ate': '2026-12-31', 'loja': g.pk}).json()
        t('período limitado a 92 dias como a web', r['ate'] == '2026-04-03', r['ate'])
        t('registra ver_indicadores', AcessoCamera.objects.filter(user=chefe, acao='ver_indicadores').exists())

        print('\n== BLOQUEIO ==')
        with mock.patch('api_mobile.permissoes.bloqueio_atual',
                        return_value={'tipo': 'ferias', 'titulo': 'x', 'mensagem': 'x', 'caminho_web': '/'}):
            t('bloqueado no portal: 423', cc.get(f'{M}/').status_code == 423)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco (imagens e vídeo só em memória, cache local).')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
