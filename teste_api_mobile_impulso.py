"""API do app — módulo Impulso (/api/mobile/v1/m/impulso/).

Roda dentro de uma transação desfeita no fim: não grava nada no banco. O ranking
usa só as pessoas do teste (get_colaboradores trocado); os caches são LocMem (nunca o Redis do dev).
"""
import os
import sys
from datetime import timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
# Caches em memória ANTES do setup: o `default` é o Redis remoto e compartilhado do dev (nada de clear() nele).
import redeconfianca.settings as _cfg  # noqa: E402

_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'} for n in _cfg.CACHES}
django.setup()

from django.conf import settings
from django.core.cache import caches

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from communications.models import CommunicationGroup
from core.models import Notification
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, Meta, MetaComentario, MetaItem, MetaVisualizacao, ParceriaMeta
from users.models import Sector

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
M = f'{BASE}/m/impulso'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


for nome in settings.CACHES:                 # só LocMem aqui (trocados acima)
    assert 'locmem' in settings.CACHES[nome]['BACKEND'].lower()
    caches[nome].clear()

marcador = transaction.atomic()
marcador.__enter__()
try:
    s1 = Sector.objects.create(name='ZZ Impulso App S1')
    s2 = Sector.objects.create(name='ZZ Impulso App S2')

    def novo(apelido, setor, hierarquia='PADRAO'):
        return User.objects.create_user(
            username=f'zzimp.{apelido}', email=f'zzimp.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZ{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia)

    ana, bia, caio = novo('ana', s1), novo('bia', s1), novo('caio', s2)
    gil, gal = novo('gil', s1), novo('gal', s1)          # gestores do Impulso do setor S1
    fora = novo('fora', s1)                               # sem grupo nenhum: fora do Impulso

    def grupo(nome):
        g = CommunicationGroup.objects.filter(name__iexact=nome).first()
        return g or CommunicationGroup.objects.create(name=nome, created_by=gil)

    grupo(GRUPO_ADM).members.add(ana, bia, caio)
    grupo(GRUPO_GESTOR).members.add(gil, gal)

    hoje = timezone.localdate()
    m1 = Meta.objects.create(gestor=gil, colaborador=ana, titulo='ZZ meta da Ana', descricao='Fazer X', prazo=hoje,
                             created_by=gil)
    i1 = MetaItem.objects.create(meta=m1, texto='ZZ passo 1', ordem=0)
    i2 = MetaItem.objects.create(meta=m1, texto='ZZ passo 2', ordem=1)
    m_caio = Meta.objects.create(gestor=fora, colaborador=caio, titulo='ZZ meta do Caio', descricao='y', prazo=hoje,
                                 created_by=caio)
    m_pend = Meta.objects.create(gestor=gil, colaborador=ana, titulo='ZZ pedido da Ana', descricao='z', prazo=hoje,
                                 aprovacao=Meta.Aprovacao.PENDENTE, solicitada_por=ana, created_by=ana)
    m_conc = Meta.objects.create(gestor=gil, colaborador=ana, titulo='ZZ concluída da Ana', descricao='w',
                                 prazo=hoje, status=Meta.Status.CONCLUIDA, nota_qualidade=5, nota_prazo=5,
                                 created_by=gil)
    convite = ParceriaMeta.objects.create(meta=m_caio, convidado=bia, convidado_por=caio)

    def entrar(user):
        r = Client().post(f'{BASE}/auth/login/', {'email': user.email, 'senha': 'S3nha!teste', 'dispositivo': 'ZZ'},
                          content_type='application/json')
        return Client(HTTP_AUTHORIZATION=f'Bearer {r.json()["token"]}')

    ca, cb, cc, cg, cgal, cf = (entrar(u) for u in (ana, bia, caio, gil, gal, fora))

    def post(c, url, corpo=None):
        return c.post(url, corpo or {}, content_type='application/json')

    print('== ACESSO ==')
    chaves_ana = {m['chave'] for m in ca.get(f'{BASE}/modulos/').json()}
    chaves_fora = {m['chave'] for m in cf.get(f'{BASE}/modulos/').json()}
    t('membro vê o Impulso em /modulos/', 'impulso' in chaves_ana, chaves_ana)
    t('quem não é do Impulso não vê o módulo', 'impulso' not in chaves_fora, chaves_fora)
    for url in ('resumo/', 'ranking/', 'metas/', f'metas/{m1.pk}/'):
        t(f'fora do Impulso: 403 em {url}', cf.get(f'{M}/{url}').status_code == 403)
    t('fora do Impulso: 403 ao mover', post(cf, f'{M}/metas/{m1.pk}/status/', {'status': 'EM_ANDAMENTO'}).status_code == 403)
    t('sem token: 401/403', Client().get(f'{M}/resumo/').status_code in (401, 403))

    print('\n== RESUMO E RANKING ==')
    pessoas_do_teste = User.objects.filter(pk__in=[ana.pk, bia.pk, caio.pk])
    with mock.patch('impulso.utils.get_colaboradores', return_value=pessoas_do_teste):
        r = ca.get(f'{M}/resumo/')
        j = r.json()
        t('resumo abre', r.status_code == 200 and 'percentual' in j['minha'] and len(j['minha']['blocos']) == 3
          and len(j['minha']['linhas']) == 9, r.content[:300])
        t('resumo conta as metas abertas da Ana', j.get('metas', {}).get('abertas') == 1
          and j['metas']['vencem_hoje'] == 1, j.get('metas'))
        t('resumo: Ana está no ranking e não é gestora', j.get('no_ranking') is True and j.get('is_gestor') is False)
        t('resumo do gestor traz o que avaliar', cg.get(f'{M}/resumo/').json().get('is_gestor') is True)

        r = ca.get(f'{M}/ranking/')
        j = r.json()
        ids = [l['id'] for l in j.get('linhas', [])]
        t('ranking abre com as pessoas do acompanhamento', r.status_code == 200 and set(ids) == {ana.pk, bia.pk, caio.pk},
          r.content[:300])
        t('ranking marca a minha posição', j.get('minha_posicao') == ids.index(ana.pk) + 1
          and any(l['sou_eu'] and l['id'] == ana.pk for l in j['linhas']))
        t('ranking do gestor fora da lista: sem posição', cg.get(f'{M}/ranking/').json().get('minha_posicao') is None)

    print('\n== KANBAN ==')
    r = ca.get(f'{M}/metas/', {'mes': ''})
    j = r.json()
    por_status = {c['status']: [m['id'] for m in c['metas']] for c in j.get('colunas', [])}
    t('Kanban abre com as 4 colunas', r.status_code == 200 and list(por_status) == ['A_FAZER', 'EM_ANDAMENTO', 'ENTREGUE', 'CONCLUIDA'],
      r.content[:300])
    t('a meta da Ana está em A fazer', m1.pk in por_status.get('A_FAZER', []))
    t('a concluída está em Concluída', m_conc.pk in por_status.get('CONCLUIDA', []))
    todos = {i for ids_ in por_status.values() for i in ids_}
    t('meta de outra pessoa não aparece', m_caio.pk not in todos)
    t('pedido pendente não é card', m_pend.pk not in todos)
    card = next(m for c in j['colunas'] for m in c['metas'] if m['id'] == m1.pk)
    t('card traz progresso do to-do e pode mover', card['total_itens'] == 2 and card['feitos'] == 0 and card['pode_mover'])
    t('A fazer vem repartido por prazo', any(g['chave'] == 'hoje' and m1.pk in g['ids']
                                              for g in j['colunas'][0]['grupos'] or []))
    t('colaborador não recebe a lista de pessoas', j['filtros']['colaboradores'] is None)
    r = ca.get(f'{M}/metas/')
    t('sem mês: abre no mês atual', r.json()['filtros']['mes'] == f'{hoje:%Y-%m}')
    t('busca por nome filtra', m1.pk not in {m['id'] for c in ca.get(f'{M}/metas/', {'mes': '', 'q': 'ZZNinguem'}).json()['colunas']
                                              for m in c['metas']})

    r = cg.get(f'{M}/metas/', {'mes': '', 'colaborador': ana.pk})
    ids_gil = {m['id'] for c in r.json()['colunas'] for m in c['metas']}
    t('gestor vê a meta da equipe filtrando a pessoa', m1.pk in ids_gil and m_caio.pk not in ids_gil, ids_gil)
    t('gestor recebe a lista de colaboradores', isinstance(r.json()['filtros']['colaboradores'], list))
    r = ca.get(f'{M}/metas/concluidas/', {'mes': '', 'inicio': 0})
    t('Mostrar mais das concluídas', r.status_code == 200 and m_conc.pk in {m['id'] for m in r.json()['metas']})

    print('\n== DETALHE ==')
    r = ca.get(f'{M}/metas/{m1.pk}/')
    j = r.json()
    t('detalhe abre', r.status_code == 200 and j['titulo'] == 'ZZ meta da Ana' and len(j['itens']) == 2
      and j['pode_marcar_itens'] and j['pode_mover'] and not j['pode_avaliar'], r.content[:300])
    t('abrir a meta marca como vista', MetaVisualizacao.objects.filter(meta=m1, user=ana).exists())
    t('meta de outra pessoa: 404', ca.get(f'{M}/metas/{m_caio.pk}/').status_code == 404)
    t('gestor da equipe abre a meta', cgal.get(f'{M}/metas/{m1.pk}/').status_code == 200)
    t('gestor da meta pode avaliar', cg.get(f'{M}/metas/{m1.pk}/').json()['pode_avaliar'] is True)
    r = cb.get(f'{M}/metas/{m_caio.pk}/')
    t('convidado abre a meta com o convite', r.status_code == 200 and r.json()['meu_convite']
      and r.json()['meu_convite']['id'] == convite.pk, r.content[:200])

    print('\n== MOVER ==')
    r = post(ca, f'{M}/metas/{m1.pk}/status/', {'status': 'EM_ANDAMENTO'})
    m1.refresh_from_db()
    t('mover para Em andamento', r.status_code == 200 and m1.status == 'EM_ANDAMENTO'
      and r.json()['meta']['status'] == 'EM_ANDAMENTO', r.content[:200])
    avisos_antes = Notification.objects.filter(user=gil).count()
    r = post(ca, f'{M}/metas/{m1.pk}/status/', {'status': 'ENTREGUE'})
    m1.refresh_from_db()
    t('entregar vai para avaliação e avisa o gestor', r.status_code == 200 and m1.status == 'ENTREGUE'
      and m1.entregue_em and Notification.objects.filter(user=gil).count() == avisos_antes + 1, r.content[:200])
    r = post(ca, f'{M}/metas/{m1.pk}/status/', {'status': 'CONCLUIDA'})
    t('concluir pelo arrastar: 400 (é a avaliação do gestor)', r.status_code == 400 and 'avaliação' in r.json()['erro'],
      r.content[:200])
    r = post(ca, f'{M}/metas/{m_pend.pk}/status/', {'status': 'EM_ANDAMENTO'})
    t('pedido não aprovado não anda', r.status_code == 400 and 'aprovada' in r.json()['erro'], r.content[:200])
    r = post(ca, f'{M}/metas/{m_conc.pk}/status/', {'status': 'A_FAZER'})
    m_conc.refresh_from_db()
    t('concluída não volta de coluna', r.status_code == 400 and m_conc.status == 'CONCLUIDA')
    t('mover meta alheia: 404', post(ca, f'{M}/metas/{m_caio.pk}/status/', {'status': 'EM_ANDAMENTO'}).status_code == 404)

    print('\n== TO-DO ==')
    r = post(ca, f'{M}/metas/itens/{i1.pk}/marcar/')
    i1.refresh_from_db()
    t('responsável marca o passo', r.status_code == 200 and r.json()['concluido'] and i1.concluido
      and i1.concluido_por_id == ana.pk and r.json()['feitos'] == 1, r.content[:200])
    r = post(ca, f'{M}/metas/itens/{i1.pk}/marcar/')
    i1.refresh_from_db()
    t('marcar de novo desmarca', r.status_code == 200 and not r.json()['concluido'] and not i1.concluido)
    t('gestor da meta marca', post(cg, f'{M}/metas/itens/{i2.pk}/marcar/').status_code == 200)
    r = post(cgal, f'{M}/metas/itens/{i2.pk}/marcar/')
    t('gestor da equipe que não é o da meta: 403 (régua do portal)', r.status_code == 403, r.content[:200])
    t('quem não vê a meta: 404', post(cc, f'{M}/metas/itens/{i2.pk}/marcar/').status_code == 404)

    print('\n== COMENTAR ==')
    r = post(ca, f'{M}/metas/{m1.pk}/comentarios/', {'mensagem': 'ZZ comentei pelo app'})
    t('comentar', r.status_code == 201 and r.json()['sou_eu']
      and MetaComentario.objects.filter(meta=m1, autor=ana, mensagem='ZZ comentei pelo app').exists(), r.content[:200])
    t('comentário vazio: 400', post(ca, f'{M}/metas/{m1.pk}/comentarios/', {'mensagem': ' '}).status_code == 400)
    t('comentar em meta alheia: 404', post(cc, f'{M}/metas/{m1.pk}/comentarios/', {'mensagem': 'x'}).status_code == 404)
    t('o comentário aparece no detalhe', any(c['texto'] == 'ZZ comentei pelo app'
                                             for c in ca.get(f'{M}/metas/{m1.pk}/').json()['comentarios']))

    print('\n== AVALIAR ==')
    r = post(ca, f'{M}/metas/{m1.pk}/avaliar/', {'nota_qualidade': 5, 'nota_prazo': 5})
    m1.refresh_from_db()
    t('colaborador não avalia', r.status_code == 400 and not m1.is_avaliada, r.content[:200])
    r = post(cg, f'{M}/metas/{m1.pk}/avaliar/', {'nota_qualidade': 9, 'nota_prazo': 5})
    t('nota fora de 0–5: 400', r.status_code == 400)
    r = post(cg, f'{M}/metas/{m1.pk}/avaliar/', {'nota_qualidade': 4, 'nota_prazo': 5, 'comentario': 'ZZ bom'})
    m1.refresh_from_db()
    t('gestor avalia e conclui', r.status_code == 200 and m1.status == 'CONCLUIDA' and m1.nota_qualidade == 4
      and r.json()['meta']['avaliacao']['media'] == 4.5, r.content[:200])
    t('avaliar de novo: 400', post(cg, f'{M}/metas/{m1.pk}/avaliar/', {'nota_qualidade': 1, 'nota_prazo': 1}).status_code == 400)

    print('\n== CONVITE ==')
    t('convite de outra pessoa: 404', post(cc, f'{M}/convites/{convite.pk}/responder/', {'resposta': 'aceitar'}).status_code == 404)
    t('resposta inválida: 400', post(cb, f'{M}/convites/{convite.pk}/responder/', {'resposta': 'talvez'}).status_code == 400)
    r = post(cb, f'{M}/convites/{convite.pk}/responder/', {'resposta': 'aceitar'})
    convite.refresh_from_db()
    t('aceitar o convite põe a pessoa na meta', r.status_code == 200 and convite.status == 'ACEITA'
      and m_caio.participantes.filter(pk=bia.pk).exists(), r.content[:200])
    t('responder de novo: 400', post(cb, f'{M}/convites/{convite.pk}/responder/', {'resposta': 'recusar'}).status_code == 400)
    r = cb.get(f'{M}/metas/', {'mes': ''})
    card = next((m for c in r.json()['colunas'] for m in c['metas'] if m['id'] == m_caio.pk), None)
    t('a meta em parceria entra no Kanban de quem aceitou', card is not None and card['em_parceria'] is True)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)

print(f'\n{ok} OK / {fail} falha(s)')
sys.exit(1 if fail else 0)
