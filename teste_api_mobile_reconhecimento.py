"""API do app — Reconhecimento (home + mural) e o que mudou na home em 23/11/2026.

- Home (/inicio/): "Elogios Recentes" virou "Reconhecimento": os 12 mais recentes e o total da rede
  (communications.views.home_feed), e a saudação pela hora do portal.
- Mural (/reconhecimentos/): o /compliments/mural/ — filtro (Todos, Para mim, Meu setor, Que eu fiz),
  busca e páginas de 24, pelo ``_mural_filtrado`` do portal.
- SUPERADMIN sem trava: o ajuste de pré-cadastro não o prende no app (o middleware da web também não),
  o aviso de comunicado obrigatório não aparece para ele e ele recebe a lista de avisos ocultos.
Cache em LocMem; tudo numa transação desfeita no fim.
"""
import os
import sys
from datetime import timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
import redeconfianca.settings as _cfg  # noqa: E402

_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-reco-{n}'} for n in _cfg.CACHES}
django.setup()

from django.conf import settings  # noqa: E402
from django.core.cache import caches  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.utils import timezone  # noqa: E402

from communications.models import Communication  # noqa: E402
from compliments.models import Compliment  # noqa: E402
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


for nome in settings.CACHES:
    assert 'locmem' in settings.CACHES[nome]['BACKEND'].lower()
    caches[nome].clear()

marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Setor Reco App')
    outro = Sector.objects.create(name='ZZ Outro Reco App')

    def novo(apelido, hierarquia='PADRAO', setor_=setor, **kw):
        return User.objects.create_user(
            username=f'zzreco.{apelido}', email=f'zzreco.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZReco{apelido.title()}', last_name='Teste', sector=setor_, hierarchy=hierarquia, **kw)

    ana, beto, caio = novo('ana'), novo('beto'), novo('caio', setor_=outro)
    chefe = novo('chefe', 'SUPERADMIN', outro)
    su = novo('su', 'PADRAO', outro, is_superuser=True)

    def entrar(u):
        r = Client().post(f'{BASE}/auth/login/', {'email': u.email, 'senha': 'S3nha!teste', 'dispositivo': 'ZZ'},
                          content_type='application/json')
        return Client(HTTP_AUTHORIZATION=f"Bearer {r.json()['token']}")

    ca, cb, cc, cchefe, csu = (entrar(u) for u in (ana, beto, caio, chefe, su))

    # 30 reconhecimentos de teste (os mais recentes da rede durante o teste).
    feitos = []
    for i in range(26):
        feitos.append(Compliment.objects.create(from_user=caio, to_user=beto, rating=4, comment=f'ZZRECO geral {i}'))
    para_ana = Compliment.objects.create(from_user=beto, to_user=ana, rating=5, comment='ZZRECO atendimento nota dez')
    para_setor = Compliment.objects.create(from_user=caio, to_sector=setor, rating=5, comment='ZZRECO equipe do setor')
    da_ana = Compliment.objects.create(from_user=ana, to_user=caio, rating=3, comment='ZZRECO valeu a ajuda')
    inativo = Compliment.objects.create(from_user=ana, to_user=beto, rating=1, comment='ZZRECO apagado', is_active=False)

    print('== HOME: RECONHECIMENTO ==')
    j = ca.get(f'{BASE}/inicio/').json()
    total_rede = Compliment.objects.filter(is_active=True).count()
    t('os 12 mais recentes (a faixa da home do portal)', len(j['elogios']) == 12 and j['elogios'][0]['id'] == da_ana.pk,
      [e['id'] for e in j['elogios']][:3])
    t('o total da rede', j['total_reconhecimentos'] == total_rede, (j.get('total_reconhecimentos'), total_rede))
    t('inativo não aparece', inativo.pk not in {e['id'] for e in j['elogios']})
    t('saudação pela hora do portal', j['saudacao'] in ('Bom dia', 'Boa tarde', 'Boa noite'), j.get('saudacao'))
    from communications.views import _saudacao
    t('a mesma do portal', j['saudacao'] == _saudacao())
    t('quem não é SUPERADMIN não recebe avisos ocultos', j['avisos_ocultos'] == [])
    e = next(x for x in j['elogios'] if x['id'] == para_setor.pk)
    t('reconhecimento para o setor: para_setor e "para mim" de quem é do setor', e['para_setor'] == setor.name
      and e['para_pessoa'] is None and e['para_mim'] is True, e)

    print('\n== MURAL ==')
    r = ca.get(f'{BASE}/reconhecimentos/')
    j = r.json()
    t('1ª página: 24, com os números e os filtros', r.status_code == 200 and len(j['resultados']) == 24 and j['tem_mais']
      and j['numeros']['total'] == total_rede and [f['valor'] for f in j['filtros']] == ['', 'para_mim', 'meu_setor', 'que_fiz'],
      (len(j.get('resultados', [])), j.get('numeros')))
    hoje = timezone.localdate()
    t('números: no mês e para mim', j['numeros']['no_mes'] == Compliment.objects.filter(
        is_active=True, created_at__year=hoje.year, created_at__month=hoje.month).count() and j['numeros']['para_mim'] == 1)
    j2 = ca.get(f'{BASE}/reconhecimentos/', {'pagina': 2}).json()
    t('2ª página sem repetir e sem os números', not ({x['id'] for x in j['resultados']} & {x['id'] for x in j2['resultados']})
      and 'numeros' not in j2)
    t('página além do fim: vazia', ca.get(f'{BASE}/reconhecimentos/', {'pagina': 99999}).json()['resultados'] == [])
    ids = {x['id'] for x in ca.get(f'{BASE}/reconhecimentos/', {'ver': 'para_mim'}).json()['resultados']}
    t('Para mim: o meu e o do meu setor', ids == {para_ana.pk, para_setor.pk}, ids)
    ids = {x['id'] for x in ca.get(f'{BASE}/reconhecimentos/', {'ver': 'que_fiz'}).json()['resultados']}
    t('Que eu fiz', ids == {da_ana.pk}, ids)
    ids = {x['id'] for x in cb.get(f'{BASE}/reconhecimentos/', {'ver': 'meu_setor', 'q': 'ZZRECO'}).json()['resultados']}
    t('Meu setor: para o setor e para quem é dele', para_setor.pk in ids and para_ana.pk in ids and feitos[-1].pk in ids
      and da_ana.pk not in ids)
    r = ca.get(f'{BASE}/reconhecimentos/', {'q': 'nota dez'})
    t('busca no texto', [x['id'] for x in r.json()['resultados']] == [para_ana.pk] and r.json()['busca'] == 'nota dez')
    t('busca pelo nome de quem recebeu', para_ana.pk in {x['id'] for x in ca.get(f'{BASE}/reconhecimentos/', {'q': 'ZZRecoAna'}).json()['resultados']})
    t('filtro desconhecido vira Todos', ca.get(f'{BASE}/reconhecimentos/', {'ver': 'xyz'}).json()['ver'] == '')
    t('sem token: 401/403', Client().get(f'{BASE}/reconhecimentos/').status_code in (401, 403))
    with mock.patch('api_mobile.permissoes.bloqueio_atual', return_value={'tipo': 'ferias', 'titulo': 'x', 'mensagem': 'x',
                                                                           'caminho_web': '/'}):
        t('bloqueado no portal: 423', ca.get(f'{BASE}/reconhecimentos/').status_code == 423)

    print('\n== SUPERADMIN SEM TRAVA ==')
    obrig = Communication.objects.create(title='ZZRECO Obrigatório', message='x', sender=chefe, obrigatorio=True)
    obrig.recipients.add(ana, chefe, su)
    from communications import popup_checkers
    with mock.patch.object(popup_checkers, 'inicio_da_obrigatoriedade', return_value=hoje - timedelta(days=30)):
        ja = ca.get(f'{BASE}/inicio/').json()
        jc = cchefe.get(f'{BASE}/inicio/').json()
        js = csu.get(f'{BASE}/inicio/').json()
    t('colaborador vê o aviso do comunicado obrigatório', any(c['id'] == obrig.pk for c in ja['comunicados_obrigatorios']),
      ja['comunicados_obrigatorios'])
    t('SUPERADMIN (hierarquia) não', jc['comunicados_obrigatorios'] == [])
    t('superuser não', js['comunicados_obrigatorios'] == [])
    t('SUPERADMIN recebe a lista de avisos ocultos', isinstance(jc['avisos_ocultos'], list))
    with mock.patch('portal_popups.context_processors.avisos_ocultos_do_superadmin',
                    return_value=[{'titulo': 'ZZ Pesquisa', 'texto': '<b>Responda</b>', 'url': '/clima/', 'rotulo': 'Responder',
                                   'icone': 'x', 'bloqueante': True}]):
        av = cchefe.get(f'{BASE}/inicio/').json()['avisos_ocultos']
    t('aviso oculto: título, texto sem HTML, link', av == [{'titulo': 'ZZ Pesquisa', 'texto': 'Responda', 'url': '/clima/',
                                                           'rotulo': 'Responder', 'bloqueante': True}], av)

    caches['local'].clear()
    with mock.patch.object(User, 'needs_pre_registration_adjustment', lambda self: True, create=True):
        r = ca.get(f'{BASE}/chamados/')
        t('pré-cadastro com ajuste prende o colaborador (423 "cadastro")', r.status_code == 423
          and r.json()['bloqueio']['tipo'] == 'cadastro', r.status_code)
        t('e não prende o SUPERADMIN (users/pre_registration_middleware.py)', cchefe.get(f'{BASE}/chamados/').status_code == 200)
        t('nem o superuser', csu.get(f'{BASE}/chamados/').status_code == 200)
    from api_mobile.bloqueios import bloqueio_atual
    import tangerino.middleware as tmw
    caches['local'].clear()
    with mock.patch.object(tmw, 'decidir_bloqueio', return_value={'motivo': 'entrada'}), \
            mock.patch.object(User, 'can_manage_rh', lambda self: False, create=True):
        ba = bloqueio_atual(ana, usar_cache=False)
        bc = bloqueio_atual(chefe, usar_cache=False)
    t('jornada pendente: o app herda a trava do middleware para o colaborador', ba and ba['tipo'] == 'jornada', ba)
    t('e a isenção do SUPERADMIN (o middleware o deixa passar)', bc is None, bc)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
