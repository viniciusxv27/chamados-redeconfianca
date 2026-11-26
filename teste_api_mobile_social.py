"""API do app — Feed da rede (/social/) e o cartão do Impulso da home (25/11/2026).

- Feed: páginas de 8 com os 3 últimos comentários, curtir/descurtir, comentar (avisa o dono), post com
  todos os comentários, publicar foto e vídeo (mesma conferência do portal), apagar post e comentário
  com as regras do portal (autor, dono do post, SUPERADMIN).
- Impulso: /m/impulso/home/ devolve o cartão do portal (impulso.resumo_home) com os ícones do app;
  quem não é do Impulso não recebe; erro na conta vira 204 (a home segue sem o cartão).
Storage do post em memória, cache em LocMem, push trocado por mock; tudo numa transação desfeita.
"""
import io
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
import redeconfianca.settings as _cfg  # noqa: E402

_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-social-{n}'} for n in _cfg.CACHES}
django.setup()

from django.conf import settings  # noqa: E402
from django.core.cache import caches  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.files.storage import InMemoryStorage  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from PIL import Image  # noqa: E402

from core.models import Notification  # noqa: E402
from social.models import Comentario, Post  # noqa: E402
from users.models import Sector  # noqa: E402

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def foto(nome='foto.png', tamanho=(120, 90)):
    buf = io.BytesIO()
    Image.new('RGB', tamanho, (255, 107, 53)).save(buf, 'PNG')
    return SimpleUploadedFile(nome, buf.getvalue(), content_type='image/png')


def video(nome='clipe.mp4'):
    return SimpleUploadedFile(nome, b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 2000, content_type='video/mp4')


for nome in settings.CACHES:
    assert 'locmem' in settings.CACHES[nome]['BACKEND'].lower()
    caches[nome].clear()

memoria = InMemoryStorage(base_url='https://midia.exemplo-teste.local/')
marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(Post._meta.get_field('midia'), 'storage', memoria), \
            mock.patch('api_mobile.push._enfileirar'):
        setor = Sector.objects.create(name='ZZ Setor Social App')

        def novo(apelido, hierarquia='PADRAO', **kw):
            return User.objects.create_user(
                username=f'zzsoc.{apelido}', email=f'zzsoc.{apelido}@exemplo-teste.local', password='S3nha!teste',
                first_name=f'ZZSoc{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia, **kw)

        ana, beto, caio = novo('ana'), novo('beto'), novo('caio')
        chefe = novo('chefe', 'SUPERADMIN')

        def entrar(u):
            r = Client().post(f'{BASE}/auth/login/', {'email': u.email, 'senha': 'S3nha!teste', 'dispositivo': 'ZZ'},
                              content_type='application/json')
            return Client(HTTP_AUTHORIZATION=f"Bearer {r.json()['token']}")

        ca, cb, cc, cchefe = (entrar(u) for u in (ana, beto, caio, chefe))

        print('== PUBLICAR ==')
        r = ca.post(f'{BASE}/social/publicar/', {'midia': foto(), 'legenda': '  ZZSOC primeira foto  '})
        j = r.json()
        t('foto publicada: 201 com o post pronto', r.status_code == 201 and j['tipo'] == 'IMAGEM'
          and j['legenda'] == 'ZZSOC primeira foto' and j['autor']['id'] == ana.pk, (r.status_code, j))
        t('medida lida pelo servidor (120x90) e proporção limitada', j['largura'] == 120 and j['altura'] == 90
          and j['proporcao'] == 75.0, (j.get('largura'), j.get('altura'), j.get('proporcao')))
        t('URL da mídia absoluta', str(j['midia']).startswith('https://midia.exemplo-teste.local/social/'), j.get('midia'))
        t('quem publicou pode apagar', j['pode_apagar'] is True and j['curti'] is False and j['comentarios'] == [])
        p1 = Post.objects.get(pk=j['id'])
        r = cb.post(f'{BASE}/social/publicar/', {'midia': video(), 'largura': '1080', 'altura': '1920'})
        j = r.json()
        t('vídeo publicado com a medida que o app mandou', r.status_code == 201 and j['tipo'] == 'VIDEO'
          and j['largura'] == 1080 and j['altura'] == 1920 and j['proporcao'] == 125.0, (r.status_code, j))
        p2 = Post.objects.get(pk=j['id'])
        r = ca.post(f'{BASE}/social/publicar/', {'midia': SimpleUploadedFile('virus.jpg', b'MZ\x90\x00' * 50, content_type='image/jpeg')})
        t('arquivo que não é foto: 400 com a mensagem do portal', r.status_code == 400
          and 'foto' in r.json()['erro'].lower(), (r.status_code, r.content[:200]))
        r = ca.post(f'{BASE}/social/publicar/', {'legenda': 'sem arquivo'})
        t('sem arquivo: 400', r.status_code == 400 and r.json()['erro'] == 'Escolha uma foto ou um vídeo.')
        r = ca.post(f'{BASE}/social/publicar/', {'midia': SimpleUploadedFile('a.txt', b'oi', content_type='text/plain')})
        t('extensão não aceita: 400', r.status_code == 400)

        print('\n== FEED ==')
        mais = [Post.objects.create(autor=caio, midia=foto(f'z{i}.png'), tipo=Post.IMAGEM, legenda=f'ZZSOC {i}')
                for i in range(9)]
        r = ca.get(f'{BASE}/social/')
        j = r.json()
        t('1ª página: 8, do mais novo ao mais antigo, com mais', r.status_code == 200 and len(j['resultados']) == 8
          and j['resultados'][0]['id'] == mais[-1].pk and j['tem_mais'] and j['ultimo'] == j['resultados'][-1]['id'],
          (len(j.get('resultados', [])), j.get('tem_mais')))
        j2 = ca.get(f'{BASE}/social/', {'antes': j['ultimo']}).json()
        ids2 = [x['id'] for x in j2['resultados']]
        t('página seguinte pelo "antes": sem repetir, chega nos meus', not (set(ids2) & {x['id'] for x in j['resultados']})
          and p2.pk in ids2 and p1.pk in ids2, ids2[:10])
        t('o de outra pessoa não pode ser apagado por mim', next(x for x in j['resultados'] if x['id'] == mais[-1].pk)['pode_apagar'] is False)
        t('limite pedido pelo app (3) e teto de 20', len(ca.get(f'{BASE}/social/', {'limite': 3}).json()['resultados']) == 3
          and len(ca.get(f'{BASE}/social/', {'limite': 999}).json()['resultados']) <= 20)
        t('sem token: 401/403', Client().get(f'{BASE}/social/').status_code in (401, 403))
        with mock.patch('api_mobile.permissoes.bloqueio_atual', return_value={'tipo': 'ferias', 'titulo': 'x', 'mensagem': 'x',
                                                                               'caminho_web': '/'}):
            t('bloqueado no portal: 423', ca.get(f'{BASE}/social/').status_code == 423)

        print('\n== CURTIR ==')
        r = cb.post(f'{BASE}/social/{p1.pk}/curtir/')
        t('curtiu', r.status_code == 200 and r.json() == {'curti': True, 'curtidas': 1}, r.content)
        cc.post(f'{BASE}/social/{p1.pk}/curtir/')
        r = cb.post(f'{BASE}/social/{p1.pk}/curtir/')
        t('tocar de novo descurte (o total fica certo)', r.json() == {'curti': False, 'curtidas': 1}, r.content)
        t('o feed mostra quem curtiu', next(x for x in cc.get(f'{BASE}/social/', {'antes': p1.pk + 1, 'limite': 1}).json()['resultados'])['curti'] is True)
        t('curtir post que não existe: 404', cb.post(f'{BASE}/social/999999999/curtir/').status_code == 404)

        print('\n== COMENTAR ==')
        antes = Notification.objects.filter(user=ana).count()
        ultimos = []
        for i, (cli, txt) in enumerate([(cb, 'ZZSOC c1'), (cc, 'ZZSOC c2'), (cb, 'ZZSOC c3'), (cc, 'ZZSOC c4')]):
            r = cli.post(f'{BASE}/social/{p1.pk}/comentar/', {'texto': txt}, content_type='application/json')
            ultimos.append(r.json()['comentario']['id'])
        j = r.json()
        t('comentou: 201, total e o comentário pronto', r.status_code == 201 and j['total_comentarios'] == 4
          and j['comentario']['texto'] == 'ZZSOC c4' and j['comentario']['autor']['id'] == caio.pk, j)
        t('dono do post avisado no sino (como no portal)', Notification.objects.filter(user=ana).count() == antes + 4)
        n = Notification.objects.filter(user=ana).order_by('-id').first()
        t('o aviso leva para /social/<id>/ (o app abre o post)', n.related_url == f'/social/{p1.pk}/', n.related_url)
        a0 = Notification.objects.filter(user=ana).count()
        ca.post(f'{BASE}/social/{p1.pk}/comentar/', {'texto': 'ZZSOC meu mesmo'}, content_type='application/json')
        t('comentar no próprio post não avisa', Notification.objects.filter(user=ana).count() == a0)
        r = cb.post(f'{BASE}/social/{p1.pk}/comentar/', {'texto': '   '}, content_type='application/json')
        t('comentário vazio: 400', r.status_code == 400 and r.json()['erro'] == 'Escreva o comentário.')
        j = cb.get(f'{BASE}/social/', {'antes': p1.pk + 1, 'limite': 1}).json()['resultados'][0]
        t('no feed: os 3 últimos, do mais antigo ao mais novo', [c['texto'] for c in j['comentarios']]
          == ['ZZSOC c3', 'ZZSOC c4', 'ZZSOC meu mesmo'] and j['total_comentarios'] == 5, [c['texto'] for c in j['comentarios']])
        j = cb.get(f'{BASE}/social/{p1.pk}/').json()
        t('post sozinho: todos os comentários', len(j['comentarios']) == 5 and j['comentarios'][0]['texto'] == 'ZZSOC c1')
        meu = next(c for c in j['comentarios'] if c['texto'] == 'ZZSOC c1')
        alheio = next(c for c in j['comentarios'] if c['texto'] == 'ZZSOC c2')
        t('posso apagar o meu comentário, não o dos outros', meu['pode_apagar'] is True and alheio['pode_apagar'] is False)
        t('o dono do post apaga qualquer comentário dele', all(c['pode_apagar'] for c in ca.get(f'{BASE}/social/{p1.pk}/').json()['comentarios']))

        print('\n== APAGAR ==')
        r = cb.post(f'{BASE}/social/comentario/{alheio["id"]}/apagar/')
        t('apagar comentário dos outros: 403', r.status_code == 403)
        r = cb.post(f'{BASE}/social/comentario/{meu["id"]}/apagar/')
        t('apagar o meu: ok e o total cai', r.status_code == 200 and r.json()['total_comentarios'] == 4, r.content)
        t('apagar é desativar', Comentario.objects.filter(pk=meu['id'], ativo=False).exists())
        r = ca.post(f'{BASE}/social/comentario/{alheio["id"]}/apagar/')
        t('o dono do post apaga o comentário de outro', r.status_code == 200 and r.json()['total_comentarios'] == 3)
        r = cb.post(f'{BASE}/social/{p1.pk}/apagar/')
        t('apagar o post dos outros: 403', r.status_code == 403 and r.json()['erro'] == 'Só quem publicou apaga.')
        r = cchefe.post(f'{BASE}/social/{p2.pk}/apagar/')
        t('SUPERADMIN apaga qualquer post', r.status_code == 200)
        p2.refresh_from_db()
        t('post apagado: desativado, com quem apagou', p2.ativo is False and p2.apagado_por_id == chefe.pk)
        t('apagado some do feed e abre 404', p2.pk not in {x['id'] for x in ca.get(f'{BASE}/social/', {'limite': 20}).json()['resultados']}
          and ca.get(f'{BASE}/social/{p2.pk}/').status_code == 404)
        t('pode_apagar no feed do SUPERADMIN', all(x['pode_apagar'] for x in cchefe.get(f'{BASE}/social/').json()['resultados']))
        r = ca.post(f'{BASE}/social/{p1.pk}/apagar/')
        t('quem publicou apaga o próprio', r.status_code == 200)

        print('\n== IMPULSO NA HOME ==')
        modelo = {
            'mes': 'novembro', 'em_conferencia': False, 'percentual': 72.5, 'total': 72.5, 'maximo': 100.0,
            'faixa': {'nome': 'Prata', 'cor': '#94A3B8', 'icone': 'fas fa-medal', 'grad': 'x'},
            'proxima': {'nome': 'Ouro', 'falta': 17.5},
            'blocos': [{'nome': 'Confiar', 'cor': '#2563EB', 'icone': 'fa-handshake', 'pct': 80, 'valor': 40.0, 'max': 50.0},
                       {'nome': 'Inovar', 'cor': '#F59E0B', 'icone': 'fa-desconhecido', 'pct': 0, 'valor': 0.0, 'max': 20.0}],
            'metas': {'abertas': 3, 'atrasadas': 1, 'vencem_hoje': 0},
            'dicas': [{'icone': 'fa-triangle-exclamation', 'tom': 'vermelho', 'url': '/impulso/metas/', 'texto': '1 meta atrasada'},
                      {'icone': 'fa-lightbulb', 'tom': 'laranja', 'url': '', 'texto': 'Mande ideias', 'pontos': '5'}],
            'posicao': 4, 'participantes': 38,
        }
        with mock.patch('impulso.resumo_home.contexto_do_cartao', return_value=modelo):
            r = cchefe.get(f'{BASE}/m/impulso/home/')
            j = r.json()
            t('cartão: 200 com pontuação, faixa e posição', r.status_code == 200 and j['percentual'] == 72.5
              and j['faixa'] == {'nome': 'Prata', 'cor': '#94A3B8', 'icone': 'medal'} and j['posicao'] == 4
              and j['participantes'] == 38 and j['proxima'] == {'nome': 'Ouro', 'falta': 17.5}, j)
            t('ícones do Font Awesome viram Ionicons (desconhecido → foguete)', [b['icone'] for b in j['blocos']] == ['people', 'rocket-outline']
              and [d['icone'] for d in j['dicas']] == ['warning', 'bulb'], j.get('blocos'))
            t('dica sem link vai como null; pontos só onde há', j['dicas'][1]['url'] is None and j['dicas'][1]['pontos'] == '5'
              and j['dicas'][0]['pontos'] is None and j['dicas'][0]['url'] == '/impulso/metas/', j['dicas'])
            t('quem não é do Impulso não recebe o cartão', ca.get(f'{BASE}/m/impulso/home/').status_code == 403)
        with mock.patch('impulso.resumo_home.contexto_do_cartao', side_effect=RuntimeError('conta quebrou')):
            t('erro na conta: 204 (a home segue sem o cartão)', cchefe.get(f'{BASE}/m/impulso/home/').status_code == 204)
        with mock.patch('impulso.resumo_home._calcular_ranking_em_segundo_plano') as calcular:
            r = cchefe.get(f'{BASE}/m/impulso/home/')
            j = r.json() if r.status_code == 200 else {}
            t('conta de verdade (impulso.resumo_home) responde sem erro', r.status_code == 200
              and isinstance(j.get('blocos'), list) and 'percentual' in j and isinstance(j.get('dicas'), list), (r.status_code, r.content[:300]))
            t('sem ranking pronto: posição vazia e o cálculo vai para o segundo plano', j.get('posicao') is None and calcular.called)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
