"""API do app — módulo Agenda (/api/mobile/v1/m/agenda/).

Roda dentro de uma transação desfeita no fim: não grava nada no banco. Caches em LocMem (nunca o Redis do
dev). O push da agenda (pywebpush) fica fora da trava central de teste: aqui ele é trocado por um mock e o
teste confere que o envio de verdade (webpush) nunca foi chamado. Nada de IA: reprocessar/processar
transcrição também vira mock e o teste confere que não foi chamado.
"""
import os
import sys
from datetime import datetime, time, timedelta
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
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.test import Client
from django.utils import timezone

from agenda.models import CalendarEvent, EventParticipant, MeetingRequest, MeetingTranscription
from api_mobile.modulos import agenda as modulo_agenda
from communications.models import CommunicationGroup
from core.models import Notification, TaskActivity
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, Meta
from users.models import Sector

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
M = f'{BASE}/m/agenda'


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

# Envio externo e IA: tudo trocado por mock (e conferido no fim).
push_agenda = mock.patch('agenda.views.send_push_notification_to_user', mock.Mock(return_value={}))
webpush_real = mock.patch('notifications.push_utils.webpush')
processar = mock.patch('agenda.views._start_transcription_background_job')
varredura = mock.patch('agenda.processamento.garantir_varredura')
m_push, m_webpush, m_processar, m_varredura = push_agenda.start(), webpush_real.start(), processar.start(), varredura.start()

marcador = transaction.atomic()
marcador.__enter__()
try:
    s1 = Sector.objects.create(name='ZZ Agenda App S1')
    s2 = Sector.objects.create(name='ZZ Agenda App S2')

    def novo(apelido, setor, hierarquia='PADRAO', ativo=True):
        return User.objects.create_user(
            username=f'zzage.{apelido}', email=f'zzage.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZ{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia, is_active=ativo)

    ana, bia, caio = novo('ana', s1), novo('bia', s1), novo('caio', s2)
    gil = novo('gil', s1)                                  # gestor do Impulso do setor S1
    chefe = novo('chefe', s2, hierarquia='SUPERADMIN')
    parado = novo('parado', s1, ativo=False)

    def grupo(nome):
        g = CommunicationGroup.objects.filter(name__iexact=nome).first()
        return g or CommunicationGroup.objects.create(name=nome, created_by=gil)

    grupo(GRUPO_ADM).members.add(ana)
    grupo(GRUPO_GESTOR).members.add(gil)

    def entrar(user):
        r = Client().post(f'{BASE}/auth/login/', {'email': user.email, 'senha': 'S3nha!teste', 'dispositivo': 'ZZ'},
                          content_type='application/json')
        return Client(HTTP_AUTHORIZATION=f'Bearer {r.json()["token"]}')

    ca, cb, cc, cchefe = (entrar(u) for u in (ana, bia, caio, chefe))

    def post(c, url, corpo=None):
        return c.post(url, corpo or {}, content_type='application/json')

    amanha = timezone.localdate() + timedelta(days=1)

    def hora(dia, h, m=0):
        return timezone.make_aware(datetime.combine(dia, time(h, m))).isoformat()

    print('== ACESSO ==')
    chaves = {m['chave'] for m in ca.get(f'{BASE}/modulos/').json()}
    t('logado vê a Agenda em /modulos/', 'agenda' in chaves, chaves)
    t('regra de entrada: inativo fica fora', modulo_agenda.pode_entrar(parado) is False)
    t('regra de entrada: anônimo fica fora', modulo_agenda.pode_entrar(AnonymousUser()) is False)
    t('sem token: 401/403', Client().get(f'{M}/eventos/').status_code in (401, 403))
    with mock.patch.dict(modulo_agenda.MODULO, {'pode_entrar': lambda u: False}):
        t('sem acesso ao módulo: 403 nos endpoints', ca.get(f'{M}/eventos/').status_code == 403
          and ca.get(f'{M}/transcricoes/').status_code == 403)
        t('sem acesso: o módulo some de /modulos/', 'agenda' not in {m['chave'] for m in ca.get(f'{BASE}/modulos/').json()})

    print('\n== CRIAR EVENTO ==')
    t('sem título: 400', post(ca, f'{M}/eventos/novo/', {'titulo': ' ', 'inicio': hora(amanha, 9), 'fim': hora(amanha, 10)}).status_code == 400)
    r = post(ca, f'{M}/eventos/novo/', {'titulo': 'ZZ x', 'inicio': hora(amanha, 10), 'fim': hora(amanha, 9)})
    t('fim antes do início: 400 com a mensagem da web', r.status_code == 400 and 'fim' in r.json().get('erro', ''), r.content[:200])
    t('data inválida: 400', post(ca, f'{M}/eventos/novo/', {'titulo': 'ZZ x', 'inicio': 'amanhã', 'fim': hora(amanha, 9)}).status_code == 400)

    m_push.reset_mock()
    r = post(ca, f'{M}/eventos/novo/', {
        'titulo': 'ZZ Reunião semanal', 'descricao': 'Pauta', 'inicio': hora(amanha, 9), 'fim': hora(amanha, 10),
        'tipo': 'meeting', 'local': 'Sala 2', 'participantes': [bia.pk, ana.pk],
        'repetir_semanal': True, 'repetir_ate': (amanha + timedelta(days=14)).isoformat()})
    j = r.json()
    ev_id = j.get('evento', {}).get('id')
    serie = list(CalendarEvent.objects.filter(title='ZZ Reunião semanal', owner=ana).order_by('start'))
    t('cria pela view do portal: 201 com o detalhe', r.status_code == 201 and j['evento']['titulo'] == 'ZZ Reunião semanal'
      and j['evento']['sou_dono'] and j['evento']['pode_editar'], r.content[:300])
    t('repetição semanal gera a série (3 semanas)', len(serie) == 3 and serie[0].pk == ev_id
      and all(e.recurrence_parent_id == ev_id for e in serie[1:]), [e.start for e in serie])
    t('convite pendente para a Bia em cada ocorrência (sem convidar a própria Ana)',
      EventParticipant.objects.filter(event__in=serie, user=bia, status='pending').count() == 3
      and not EventParticipant.objects.filter(event__in=serie, user=ana).exists())
    t('aviso no sino da Bia', Notification.objects.filter(user=bia, title='Convite para evento').exists())
    t('push da agenda passou pelo mock (Ana + Bia)', m_push.call_count >= 2, m_push.call_count)

    r = post(ca, f'{M}/eventos/novo/', {'titulo': 'ZZ Chamada', 'inicio': hora(amanha, 15), 'fim': hora(amanha, 16), 'tipo': 'call'})
    j = r.json()
    t('Chamada ganha a sala do portal (link absoluto do portal)', r.status_code == 201 and j.get('sala', '').startswith('/reunioes/')
      and j['evento']['link_do_portal'] and j['evento']['link'].endswith(j['sala']), r.content[:300])
    chamada_id = j.get('evento', {}).get('id')

    print('\n== MINHA AGENDA ==')
    r = ca.get(f'{M}/eventos/', {'inicio': amanha.isoformat(), 'fim': (amanha + timedelta(days=6)).isoformat()})
    j = r.json()
    ids = [e['id'] for e in j.get('eventos', [])]
    t('semana traz meus eventos em ordem', r.status_code == 200 and ids[:2] == [ev_id, chamada_id], ids)
    t('sem datas: a semana de hoje', ca.get(f'{M}/eventos/').status_code == 200)
    t('período longo demais: 400', ca.get(f'{M}/eventos/', {'inicio': '2026-01-01', 'fim': '2026-06-01'}).status_code == 400)
    jb = cb.get(f'{M}/eventos/', {'inicio': amanha.isoformat(), 'fim': amanha.isoformat()}).json()
    t('convite pendente não entra na agenda da Bia (como na web)', ev_id not in [e['id'] for e in jb['eventos']]
      and jb['convites_pendentes'] == 3, jb)
    t('Caio não vê os eventos da Ana', not cc.get(f'{M}/eventos/', {'inicio': amanha.isoformat(), 'fim': amanha.isoformat()}).json()['eventos'])

    print('\n== DETALHE ==')
    r = ca.get(f'{M}/eventos/{ev_id}/')
    j = r.json()
    t('dono abre o detalhe com participantes', r.status_code == 200 and j['descricao'] == 'Pauta'
      and [p['id'] for p in j['participantes']] == [bia.pk] and j['participantes'][0]['status'] == 'pending', r.content[:300])
    j = cb.get(f'{M}/eventos/{ev_id}/').json()
    t('convidada abre e pode responder', j.get('pode_responder') is True and j.get('pode_editar') is False, j)
    t('de fora: 404', cc.get(f'{M}/eventos/{ev_id}/').status_code == 404)
    t('SUPERADMIN abre (agenda completa, como na web)', cchefe.get(f'{M}/eventos/{ev_id}/').status_code == 200)
    t('id que não existe: 404', ca.get(f'{M}/eventos/999999999/').status_code == 404)

    print('\n== CONFLITOS ==')
    j = ca.get(f'{M}/eventos/conflitos/', {'inicio': hora(amanha, 9, 30), 'fim': hora(amanha, 11)}).json()
    t('avisa o que já está no horário', [e['id'] for e in j['conflitos']] == [ev_id], j)
    j = ca.get(f'{M}/eventos/conflitos/', {'inicio': hora(amanha, 9, 30), 'fim': hora(amanha, 11), 'ignorar': ev_id}).json()
    t('na edição ignora o próprio evento', j['conflitos'] == [], j)
    t('conflitos só da minha agenda', cc.get(f'{M}/eventos/conflitos/', {'inicio': hora(amanha, 9), 'fim': hora(amanha, 10)}).json()['conflitos'] == [])

    print('\n== EDITAR ==')
    corpo = {'titulo': 'ZZ Reunião remarcada', 'inicio': hora(amanha, 11), 'fim': hora(amanha, 12), 'tipo': 'meeting',
             'participantes': [bia.pk]}
    t('convidada não edita: 404', post(cb, f'{M}/eventos/{ev_id}/editar/', corpo).status_code == 404)
    t('edição com fim antes do início: 400', post(ca, f'{M}/eventos/{ev_id}/editar/', {**corpo, 'fim': hora(amanha, 10)}).status_code == 400)
    r = post(ca, f'{M}/eventos/{ev_id}/editar/', corpo)
    ev = CalendarEvent.objects.get(pk=ev_id)
    t('dono edita pela view do portal', r.status_code == 200 and ev.title == 'ZZ Reunião remarcada'
      and timezone.localtime(ev.start).hour == 11 and r.json()['evento']['titulo'] == 'ZZ Reunião remarcada', r.content[:300])
    t('repetição não muda na edição', ev.recurrence_rule == 'weekly')
    sala_antes = CalendarEvent.objects.get(pk=chamada_id).link
    r = post(ca, f'{M}/eventos/{chamada_id}/editar/', {'titulo': 'ZZ Chamada 2', 'inicio': hora(amanha, 15), 'fim': hora(amanha, 17), 'tipo': 'call'})
    t('editar Chamada sem link mantém a sala do portal', r.status_code == 200
      and CalendarEvent.objects.get(pk=chamada_id).link == sala_antes and sala_antes.startswith('/reunioes/'), r.content[:200])

    print('\n== CONVITES ==')
    j = cb.get(f'{M}/convites/').json()
    convite = next((c for c in j['convites'] if c['evento']['id'] == ev_id), None)
    t('Bia vê o convite pendente', convite is not None and len(j['convites']) == 3, j)
    t('convite de outra pessoa: 404', post(cc, f'{M}/convites/{convite["id"]}/responder/', {'resposta': 'aceitar'}).status_code == 404)
    t('resposta inválida: 400', post(cb, f'{M}/convites/{convite["id"]}/responder/', {'resposta': 'talvez'}).status_code == 400)
    r = post(cb, f'{M}/convites/{convite["id"]}/responder/', {'resposta': 'aceitar', 'observacao': 'Vou'})
    t('aceitar pela view do portal', r.status_code == 200 and r.json()['status'] == 'accepted'
      and ev.participants.filter(pk=bia.pk).exists(), r.content[:200])
    t('dono é avisado', Notification.objects.filter(user=ana, title='Convite aceito').exists())
    t('responder de novo: 404 (já respondido)', post(cb, f'{M}/convites/{convite["id"]}/responder/', {'resposta': 'recusar'}).status_code == 404)
    jb = cb.get(f'{M}/eventos/', {'inicio': amanha.isoformat(), 'fim': amanha.isoformat()}).json()
    t('aceito, entra na agenda da Bia', ev_id in [e['id'] for e in jb['eventos']])

    sol = MeetingRequest.objects.create(requester=caio, target=ana, title='ZZ Conversa', proposed_start=ev.start + timedelta(days=2),
                                        proposed_end=ev.end + timedelta(days=2))
    j = ca.get(f'{M}/convites/').json()
    t('solicitação de reunião recebida aparece', [s['id'] for s in j['solicitacoes']] == [sol.pk], j)
    t('solicitação de outra pessoa: 404', post(cb, f'{M}/solicitacoes/{sol.pk}/responder/', {'resposta': 'aceitar'}).status_code == 404)
    r = post(ca, f'{M}/solicitacoes/{sol.pk}/responder/', {'resposta': 'aceitar'})
    sol.refresh_from_db()
    t('aceitar solicitação cria o evento dos dois', r.status_code == 200 and sol.status == 'accepted' and sol.created_event_id
      and CalendarEvent.objects.filter(owner=caio, title='ZZ Conversa').exists(), r.content[:200])
    t('o pedido virou evento visível para o Caio (participante)', cc.get(f'{M}/eventos/{sol.created_event_id}/').status_code == 200)

    print('\n== EXCLUIR ==')
    t('convidada não exclui: 404', post(cb, f'{M}/eventos/{ev_id}/excluir/').status_code == 404)
    filho = serie[1]
    r = post(ca, f'{M}/eventos/{filho.pk}/excluir/', {'serie': False})
    t('excluir só uma ocorrência', r.status_code == 200 and not CalendarEvent.objects.filter(pk=filho.pk).exists()
      and CalendarEvent.objects.filter(pk=ev_id).exists())
    r = post(ca, f'{M}/eventos/{serie[2].pk}/excluir/', {'serie': True})
    t('excluir a série inteira', r.status_code == 200 and not CalendarEvent.objects.filter(pk__in=[e.pk for e in serie]).exists(),
      r.content[:200])

    print('\n== TRANSCRIÇÕES ==')
    t1 = MeetingTranscription.objects.create(
        owner=ana, title='ZZ Reunião de vendas', status='completed', summary='Resumo da reunião.', duration_seconds=1800,
        key_decisions=[{'decision': 'Abrir loja nova', 'context': 'Meta do trimestre', 'impact': 'high'}, 'Decisão em texto'],
        action_items=[{'task': 'Mandar proposta', 'responsible': 'A definir', 'deadline': '20/11', 'priority': 'high'}],
        risks=[{'risk': 'Falta de estoque', 'severity': 'medium', 'mitigation': 'Pedir antes'}],
        sections=[{'title': 'Abertura', 'content': 'Começo', 'duration_estimate': 5, 'topics_discussed': ['metas']}],
        tags=['vendas'], participants_identified=['Ana'])
    t1.shared_with.add(bia)
    t_caio = MeetingTranscription.objects.create(owner=caio, title='ZZ Só do Caio', status='processing', etapa='transcricao',
                                                 progresso={'total_segmentos': 4, 'segmentos': {'0': 'a', '1': 'b'}})
    tarefa = TaskActivity.objects.create(title='Mandar proposta', description='Para o cliente', assigned_to=ana, created_by=ana,
                                         priority='HIGH', due_date=timezone.now() + timedelta(days=3))
    t1.tasks_created.add(tarefa)
    outra = TaskActivity.objects.create(title='Outra', description='x', assigned_to=caio, created_by=caio)
    t_caio.tasks_created.add(outra)

    r = ca.get(f'{M}/transcricoes/')
    ids = [x['id'] for x in r.json().get('resultados', [])]
    t('dono vê a sua e não a do Caio', r.status_code == 200 and t1.pk in ids and t_caio.pk not in ids, ids)
    t('compartilhada aparece para a Bia', t1.pk in [x['id'] for x in cb.get(f'{M}/transcricoes/').json()['resultados']])
    jc = cc.get(f'{M}/transcricoes/').json()['resultados']
    proc = next((x for x in jc if x['id'] == t_caio.pk), {})
    t('processando mostra etapa e progresso', proc.get('status') == 'processing' and proc.get('progresso_pct') == 45
      and proc.get('etapa_rotulo') == 'Transcrevendo', proc)
    t('SUPERADMIN vê as duas', {t1.pk, t_caio.pk} <= {x['id'] for x in cchefe.get(f'{M}/transcricoes/').json()['resultados']})

    r = ca.get(f'{M}/transcricoes/{t1.pk}/')
    j = r.json()
    t('detalhe com resumo, decisões, ações, riscos e seções', r.status_code == 200 and j['resumo'] == 'Resumo da reunião.'
      and [d['texto'] for d in j['decisoes']] == ['Abrir loja nova', 'Decisão em texto']
      and j['itens_acao'][0]['responsavel'] is None and j['riscos'][0]['severidade'] == 'medium'
      and j['secoes'][0]['minutos'] == 5, r.content[:400])
    t('Tarefas Criadas com responsável', [x['id'] for x in j['lista_tarefas']] == [tarefa.pk]
      and j['lista_tarefas'][0]['responsavel']['id'] == ana.pk)
    t('Ana está no Impulso: vem o gestor do setor', j['impulso'] and [g['id'] for g in j['impulso']['gestores']] == [gil.pk], j.get('impulso'))
    t('Bia fora do Impulso: sem o bloco', cb.get(f'{M}/transcricoes/{t1.pk}/').json()['impulso'] is None)
    t('de fora: 404', cc.get(f'{M}/transcricoes/{t1.pk}/').status_code == 404)

    print('\n== ATRIBUIR RESPONSÁVEL ==')
    url = f'{M}/transcricoes/{t1.pk}/tarefas/{tarefa.pk}/responsavel/'
    t('quem não vê a transcrição: 404', post(cc, url, {'user_id': caio.pk}).status_code == 404)
    t('tarefa de outra transcrição: 404', post(ca, f'{M}/transcricoes/{t1.pk}/tarefas/{outra.pk}/responsavel/', {'user_id': bia.pk}).status_code == 404)
    t('sem usuário: 400', post(cb, url, {}).status_code == 400)
    t('usuário inativo: 404 (a mensagem da web)', post(cb, url, {'user_id': parado.pk}).status_code == 404)
    r = post(cb, url, {'user_id': caio.pk})
    tarefa.refresh_from_db()
    t('compartilhada também atribui (regra de 11/11)', r.status_code == 200 and tarefa.assigned_to_id == caio.pk
      and r.json()['transcricao']['lista_tarefas'][0]['responsavel']['id'] == caio.pk, r.content[:300])

    print('\n== IMPORTAR PARA O IMPULSO ==')
    url = f'{M}/transcricoes/{t1.pk}/tarefas/{tarefa.pk}/impulso/'
    corpo = {'titulo': 'Mandar proposta', 'descricao': 'Para o cliente', 'prazo': (timezone.localdate() + timedelta(days=3)).isoformat(),
             'gestor': gil.pk, 'precisa_aprovacao': True}
    t('fora do Impulso: 403', post(cb, url, corpo).status_code == 403)
    t('prazo no passado: 400', post(ca, url, {**corpo, 'prazo': '2020-01-01'}).status_code == 400)
    t('gestor de fora: 400', post(ca, url, {**corpo, 'gestor': caio.pk}).status_code == 400)
    r = post(ca, url, corpo)
    meta = Meta.objects.filter(tarefa_origem=tarefa, colaborador=ana).first()
    t('importa pela view do portal (pendente de aprovação)', r.status_code == 200 and meta is not None
      and meta.pendente_aprovacao and r.json()['meta_id'] == meta.id
      and r.json()['transcricao']['lista_tarefas'][0]['impulso']['minha_meta']['id'] == meta.id, r.content[:300])
    r = post(ca, url, corpo)
    t('importar de novo: 409 com a meta', r.status_code == 409 and r.json().get('meta_id') == meta.id, r.content[:200])

    print('\n== AGENDA DE OUTRA PESSOA ==')
    ev_caio = CalendarEvent.objects.create(owner=caio, title='ZZ Compromisso do Caio', event_type='meeting',
                                           start=timezone.make_aware(datetime.combine(amanha, time(14, 0))),
                                           end=timezone.make_aware(datetime.combine(amanha, time(15, 0))))
    periodo = {'inicio': amanha.isoformat(), 'fim': amanha.isoformat()}
    r = ca.get(f'{M}/eventos/', {**periodo, 'usuario': caio.pk})
    j = r.json()
    t('colega comum: só os horários ocupados, sem detalhe (api_events com user_id)', r.status_code == 200
      and j['completa'] is False and j['eventos'] == [] and len(j['ocupados']) == 1
      and j['ocupados'][0]['inicio'].startswith(f'{amanha.isoformat()}T14:00') and j['pessoa']['id'] == caio.pk, r.content[:300])
    j = cchefe.get(f'{M}/eventos/', {**periodo, 'usuario': caio.pk}).json()
    t('SUPERADMIN vê a agenda completa (_can_view_full_calendar)', j['completa'] is True
      and [e['id'] for e in j['eventos']] == [ev_caio.pk] and j['ocupados'] == [], j)
    t('pessoa inativa ou inexistente: 404', ca.get(f'{M}/eventos/', {**periodo, 'usuario': parado.pk}).status_code == 404
      and ca.get(f'{M}/eventos/', {**periodo, 'usuario': 'x'}).status_code == 404)
    j = ca.get(f'{M}/eventos/', {**periodo, 'usuario': ana.pk}).json()
    t('com o próprio id é a minha agenda', j['pessoa'] is None and j['completa'] is True)

    print('\n== DISPONIBILIDADE ==')
    r = ca.get(f'{M}/pessoas/{caio.pk}/disponibilidade/', {'data': amanha.isoformat()})
    j = r.json()
    inicios = [x['inicio'][11:16] for x in j.get('livres', [])]
    t('livres em blocos de 30 min das 8h às 18h, fora do compromisso (user_availability)', r.status_code == 200
      and inicios[0] == '08:00' and '14:00' not in inicios and '14:30' not in inicios and '15:00' in inicios
      and inicios[-1] == '17:30' and len(j['semana']) == 7, inicios)
    t('colega comum não vê a agenda completa; SUPERADMIN vê', j['pode_ver_completa'] is False
      and cchefe.get(f'{M}/pessoas/{caio.pk}/disponibilidade/').json()['pode_ver_completa'] is True)
    t('tipos do pedido (Reunião, Chamada, Horário)', [x['valor'] for x in j['tipos']] == ['meeting', 'call', 'appointment'])
    t('pessoa inativa: 404', ca.get(f'{M}/pessoas/{parado.pk}/disponibilidade/').status_code == 404)

    print('\n== PEDIR REUNIÃO ==')
    url = f'{M}/pessoas/{caio.pk}/solicitar/'
    pedido = {'titulo': 'ZZ Alinhar metas', 'tipo': 'call', 'inicio': hora(amanha, 15), 'fim': hora(amanha, 15, 30),
              'local': 'Sala 2', 'descricao': 'Conversa rápida'}
    r = post(ca, url, {**pedido, 'titulo': ' '})
    t('sem título: 400 com o campo', r.status_code == 400 and 'titulo' in r.json().get('campos', {}), r.content[:200])
    r = post(ca, url, {**pedido, 'fim': hora(amanha, 14)})
    t('fim antes do início: 400', r.status_code == 400 and 'fim' in r.json().get('campos', {}))
    t('consigo mesmo: 400', post(ca, f'{M}/pessoas/{ana.pk}/solicitar/', pedido).status_code == 400)
    push_antes = m_push.call_count
    r = post(ca, url, pedido)
    sol = MeetingRequest.objects.filter(requester=ana, target=caio).order_by('-pk').first()
    t('pede pela view do portal (request_meeting)', r.status_code == 201 and sol is not None and sol.status == 'pending'
      and sol.meeting_type == 'call' and sol.location == 'Sala 2' and r.json()['solicitacao']['id'] == sol.pk
      and timezone.localtime(sol.proposed_start).time() == time(15, 0), r.content[:300])
    t('o Caio é avisado (sino; push trocado por mock)', Notification.objects.filter(user=caio, title='Nova solicitação de reunião').exists()
      and m_push.call_count == push_antes + 1)

    print('\n== SOLICITAÇÕES ==')
    j = ca.get(f'{M}/solicitacoes/', {'aba': 'enviadas'}).json()
    s_ana = next((x for x in j['resultados'] if x['id'] == sol.pk), None)
    t('enviadas: a minha, com "cancelar"', s_ana and s_ana['para']['id'] == caio.pk and s_ana['pode_cancelar']
      and not s_ana['pode_responder'], s_ana)
    j = cc.get(f'{M}/solicitacoes/').json()
    s_caio = next((x for x in j['resultados'] if x['id'] == sol.pk), None)
    t('recebidas: a do Caio, com "responder"', j['aba'] == 'recebidas' and s_caio and s_caio['pode_responder']
      and not s_caio['pode_cancelar'] and j['pendentes_recebidas'] >= 1, j)
    t('quem recebeu não cancela: 404', post(cc, f'{M}/solicitacoes/{sol.pk}/cancelar/').status_code == 404)
    r = post(ca, f'{M}/solicitacoes/{sol.pk}/cancelar/')
    sol.refresh_from_db()
    t('cancelar pela view do portal (meeting_request_cancel)', r.status_code == 200 and sol.status == 'cancelled'
      and r.json()['solicitacao']['status'] == 'cancelled', r.content[:200])
    t('cancelar de novo: 404', post(ca, f'{M}/solicitacoes/{sol.pk}/cancelar/').status_code == 404)

    print('\n== COMPARTILHAR TRANSCRIÇÃO ==')
    url = f'{M}/transcricoes/{t1.pk}/compartilhar/'
    t('quem só recebeu a transcrição não compartilha: 404', post(cb, url, {'user_ids': [caio.pk]}).status_code == 404)
    t('sem lista: 400', post(ca, url, {'user_ids': 'x'}).status_code == 400)
    r = post(ca, url, {'user_ids': [caio.pk, ana.pk]})
    t('compartilha pela view do portal (o dono sai da lista)', r.status_code == 200
      and set(t1.shared_with.values_list('pk', flat=True)) == {caio.pk}
      and [p['id'] for p in r.json()['transcricao']['compartilhada_com']] == [caio.pk], r.content[:300])
    t('o Caio agora vê a transcrição', cc.get(f'{M}/transcricoes/{t1.pk}/').status_code == 200)
    t('só o dono tem "compartilhar"', ca.get(f'{M}/transcricoes/{t1.pk}/').json()['pode_compartilhar'] is True
      and cc.get(f'{M}/transcricoes/{t1.pk}/').json()['pode_compartilhar'] is False)

    print('\n== NADA SAIU ==')
    t('webpush de verdade nunca foi chamado', m_webpush.call_count == 0)
    t('nenhum processamento de transcrição (IA) foi disparado', m_processar.call_count == 0)
    t('a varredura de transcrições não foi ligada', m_varredura.call_count == 0)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    for p in (push_agenda, webpush_real, processar, varredura):
        p.stop()

print(f'\n{ok} OK / {fail} falha(s)')
sys.exit(1 if fail else 0)
