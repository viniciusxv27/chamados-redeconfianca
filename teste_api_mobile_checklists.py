"""Módulo Checklists no app (/api/mobile/v1/m/checklists/): meus checklists, preencher, evidências e acompanhamento.

Roda dentro de uma transação desfeita no fim: não grava nada no banco. As evidências vão
para um storage em memória (nada sobe para o MinIO). As views do portal usadas pela API
não mandam aviso externo; mesmo assim os envios conhecidos ficam trocados por dublês que
anotam e falham, e o teste confere que ninguém chamou.
"""
import os
import sys
import time
from datetime import timedelta
from unittest import mock
from urllib.parse import urlparse

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
# Caches em memória ANTES do setup: o 'default' é o Redis remoto compartilhado (clear() nele = FLUSHDB).
import redeconfianca.settings as _cfg  # noqa: E402
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'}
               for n in _cfg.CACHES}
django.setup()

from django.conf import settings
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client
from django.utils import timezone

from checklists.models import (ChecklistAssignment, ChecklistExecution, ChecklistTask, ChecklistTaskEvidence,
                               ChecklistTaskExecution, ChecklistTemplate)
from checklists.views import create_executions_for_assignment
from communications.models import CommunicationGroup
from users.models import Sector
from users.module_access import set_user_modules

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
M = f'{BASE}/m/checklists'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


# Nada sobe para o MinIO.
memoria = InMemoryStorage()
ChecklistTaskEvidence._meta.get_field('file').storage = memoria

# Envio externo: anota e falha (nada sai daqui).
envios = []


def _envio(nome):
    def falso(*a, **k):
        envios.append(nome)
        raise RuntimeError(f'{nome} desligado no teste')
    return falso


dubles = [mock.patch(alvo, side_effect=_envio(alvo)) for alvo in (
    'django.core.mail.EmailMessage.send',
    'requests.post',
)]
for d in dubles:
    d.start()

hoje = timezone.localdate()
ontem = hoje - timedelta(days=1)

marcador = transaction.atomic()
marcador.__enter__()
try:
    loja = Sector.objects.create(name='ZZ Loja Checklist App')
    loja2 = Sector.objects.create(name='ZZ Loja Checklist App 2')
    sessoes_antes = Session.objects.count()

    def novo(apelido, hierarquia='PADRAO', setor=loja):
        return User.objects.create_user(
            username=f'zzchk.{apelido}', email=f'zzchk.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZ{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia)

    gerentes = CommunicationGroup.objects.filter(name__iexact='GERENTES').first()
    executor = novo('executor')
    outro = novo('outro', setor=loja2)
    fora = novo('fora')                       # PADRÃO fora do GERENTES: módulo fechado
    liberado = novo('liberado')               # PADRÃO fora do GERENTES, com liberação individual
    supervisor = novo('supervisor', 'SUPERVISOR')   # vê por ser admin do checklist (não atribuiu nada)
    atribuidor = novo('atribuidor', 'ADMINISTRATIVO')
    if gerentes is None:
        gerentes = CommunicationGroup.objects.create(name='GERENTES', created_by=supervisor)
    gerentes.members.add(executor, outro)
    set_user_modules(liberado, ['checklists'])

    modelo = ChecklistTemplate.objects.create(name='ZZ Abertura da loja', sector=loja, created_by=atribuidor,
                                              description='Antes de abrir as portas.')
    t1 = ChecklistTask.objects.create(template=modelo, title='ZZ Ligar as luzes', order=1, is_required=True)
    t2 = ChecklistTask.objects.create(template=modelo, title='ZZ Conferir vitrine', order=2, is_required=False)
    t3 = ChecklistTask.objects.create(template=modelo, title='ZZ Alarme desligado?', order=3, is_required=True,
                                      task_type='yes_no')
    t4 = ChecklistTask.objects.create(template=modelo, title='ZZ Ar-condicionado', order=4, is_required=False,
                                      task_type='dropdown')

    def atribuir(pessoa, datas):
        a = ChecklistAssignment.objects.create(template=modelo, assigned_to=pessoa, assigned_by=atribuidor,
                                               schedule_type='custom', period='morning',
                                               custom_dates=[d.isoformat() for d in datas],
                                               start_date=min(datas), end_date=max(datas))
        create_executions_for_assignment(a)
        return a

    atribuir(executor, [ontem, hoje])
    atribuir(outro, [hoje])
    ex_hoje = ChecklistExecution.objects.get(assignment__assigned_to=executor, execution_date=hoje)
    ex_ontem = ChecklistExecution.objects.get(assignment__assigned_to=executor, execution_date=ontem)
    ex_outro = ChecklistExecution.objects.get(assignment__assigned_to=outro)
    te = {x.task_id: x for x in ChecklistTaskExecution.objects.filter(execution=ex_hoje)}

    def entrar(user):
        r = Client().post(f'{BASE}/auth/login/', {'email': user.email, 'senha': 'S3nha!teste', 'plataforma': 'android'},
                          content_type='application/json')
        return Client(HTTP_AUTHORIZATION=f'Bearer {r.json()["token"]}')

    ce, co, cf, cl, cs = (entrar(u) for u in (executor, outro, fora, liberado, supervisor))

    def chaves(c):
        return {m['chave'] for m in c.get(f'{BASE}/modulos/').json()}

    print('== ENTRADA NO MÓDULO ==')
    t('aparece para o PADRÃO do grupo GERENTES', 'checklists' in chaves(ce))
    t('aparece para o SUPERVISOR', 'checklists' in chaves(cs))
    t('some para o PADRÃO fora do GERENTES', 'checklists' not in chaves(cf))
    t('PADRÃO fora do GERENTES: 403 no módulo', cf.get(f'{M}/inicio/').status_code == 403)
    t('liberação individual devolve o módulo', 'checklists' in chaves(cl) and cl.get(f'{M}/inicio/').status_code == 200)
    t('sem token: 401/403', Client().get(f'{M}/inicio/').status_code in (401, 403))

    print('\n== MEUS CHECKLISTS ==')
    r = ce.get(f'{M}/inicio/').json()
    t('início: o de hoje aparece', [e['id'] for e in r['execucoes']] == [ex_hoje.pk], r['execucoes'])
    t('início: contadores', r['contadores']['hoje_abertos'] == 1 and r['contadores']['atrasados'] == 1, r['contadores'])
    t('início: executor não é gestor', r['gestor'] is False)
    t('início: supervisor é gestor', cs.get(f'{M}/inicio/').json()['gestor'] is True)
    pend = ce.get(f'{M}/execucoes/', {'filtro': 'pendentes'}).json()
    t('pendentes: hoje e ontem (atrasado)', [e['id'] for e in pend['resultados']] == [ex_hoje.pk, ex_ontem.pk], pend)
    t('pendentes: ontem marcado como atrasado', pend['resultados'][1]['atrasado'] is True)
    t('enviados: vazio', ce.get(f'{M}/execucoes/', {'filtro': 'enviados'}).json()['total'] == 0)
    t('lista é só minha (o do outro não vem)', ex_outro.pk not in [e['id'] for e in
                                                                    ce.get(f'{M}/execucoes/', {'filtro': 'todos'}).json()['resultados']])

    print('\n== DETALHE E RECORTE ==')
    r = ce.get(f'{M}/execucoes/{ex_hoje.pk}/')
    d = r.json()
    t('detalhe: 200 com as 4 tarefas na ordem', r.status_code == 200 and [x['tarefa_id'] for x in d['tarefas']] == [t1.pk, t2.pk, t3.pk, t4.pk], r.content[:300])
    t('detalhe: dono pode preencher', d['pode_preencher'] is True and d['sou_executor'] is True)
    t('detalhe: tipos e obrigatoriedade', [(x['tipo'], x['obrigatoria']) for x in d['tarefas']]
      == [('normal', True), ('normal', False), ('yes_no', True), ('dropdown', False)])
    t('execução de outra pessoa: 404', co.get(f'{M}/execucoes/{ex_hoje.pk}/').status_code == 404)
    t('supervisor vê (admin do checklist) mas não preenche',
      cs.get(f'{M}/execucoes/{ex_hoje.pk}/').status_code == 200
      and cs.get(f'{M}/execucoes/{ex_hoje.pk}/').json()['pode_preencher'] is False)
    t('supervisor não envia o checklist dos outros',
      cs.post(f'{M}/execucoes/{ex_hoje.pk}/enviar/', {'respostas': []}, content_type='application/json').status_code == 400)
    t('id inexistente: 404', ce.get(f'{M}/execucoes/999999999/').status_code == 404)

    print('\n== ENVIAR (VALIDAÇÃO DO PORTAL) ==')
    inicio_ms = time.monotonic()
    r = ce.post(f'{M}/execucoes/{ex_hoje.pk}/enviar/',
                {'respostas': [{'id': te[t2.pk].pk, 'feita': True, 'observacao': 'ZZ vitrine ok'}]},
                content_type='application/json')
    gasto = time.monotonic() - inicio_ms
    t('obrigatórias faltando: 400 com o nome das tarefas', r.status_code == 400
      and 'ZZ Ligar as luzes' in r.json()['erro'] and 'ZZ Alarme desligado?' in r.json()['erro'], r.content[:400])
    print(f'       (recusa levou {gasto:.1f} s — a view do portal desenha a tela de erro)')
    te2 = ChecklistTaskExecution.objects.get(pk=te[t2.pk].pk)
    t('recusado: nada gravado (nem a observação da opcional)', te2.notes == '' and not te2.is_completed)
    t('recusado: segue pendente', ChecklistExecution.objects.get(pk=ex_hoje.pk).status == 'pending')

    r = ce.post(f'{M}/execucoes/{ex_hoje.pk}/enviar/',
                {'respostas': [{'id': te[t1.pk].pk, 'feita': True}, {'id': te[t3.pk].pk, 'resposta': 'yes'}]},
                content_type='application/json')
    t('feita sem descrição nem evidência: 400 (regra do portal)', r.status_code == 400 and 'descrição' in r.json()['erro'], r.content[:300])
    t('e nada gravado', not ChecklistTaskExecution.objects.get(pk=te[t1.pk].pk).is_completed)

    print('\n== EVIDÊNCIAS ==')
    foto = SimpleUploadedFile('luzes.jpg', b'\xff\xd8\xff zz-foto', content_type='image/jpeg')
    r = ce.post(f'{M}/tarefas/{te[t1.pk].pk}/evidencias/', {'files': [foto]})
    t('foto sobe pela view do portal', r.status_code == 200 and len(r.json()['tarefa']['evidencias']) == 1, r.content[:300])
    ev = r.json()['tarefa']['evidencias'][0]
    t('evidência é imagem, com nome e link', ev['imagem'] and ev['nome'] == 'luzes.jpg' and '/arquivo/' in ev['url'])
    caminho = urlparse(ev['url']).path
    r = Client().get(caminho)
    t('link assinado abre o arquivo', r.status_code == 200 and b''.join(r.streaming_content) == b'\xff\xd8\xff zz-foto')
    alheio = urlparse(cs.get(f'{M}/execucoes/{ex_hoje.pk}/').json()['tarefas'][0]['evidencias'][0]['url']).path
    t('link gerado para o supervisor também abre (ele vê a execução)', Client().get(alheio).status_code == 200)
    with mock.patch('checklists.views.has_checklist_admin_permission', return_value=False):
        t('link de quem perdeu a visão: 404', Client().get(alheio).status_code == 404)
    t('outra pessoa não sobe evidência aqui: 404',
      co.post(f'{M}/tarefas/{te[t1.pk].pk}/evidencias/', {'files': [SimpleUploadedFile('x.jpg', b'x', content_type='image/jpeg')]}).status_code == 404)
    t('sem arquivo: 400', ce.post(f'{M}/tarefas/{te[t1.pk].pk}/evidencias/', {}).status_code == 400)
    doc = SimpleUploadedFile('nota.pdf', b'%PDF-1.4 zz', content_type='application/pdf')
    r = ce.post(f'{M}/tarefas/{te[t2.pk].pk}/evidencias/', {'files': [doc]})
    ev_doc = r.json()['tarefa']['evidencias'][0]
    t('PDF entra como documento', r.status_code == 200 and ev_doc['tipo'] == 'document' and not ev_doc['imagem'])
    t('supervisor (não é dono nem quem atribuiu) não exclui: 403 do portal',
      cs.post(f'{M}/evidencias/{ev_doc["id"]}/excluir/').status_code == 403)
    t('outra pessoa não exclui: 404', co.post(f'{M}/evidencias/{ev_doc["id"]}/excluir/').status_code == 404)
    r = ce.post(f'{M}/evidencias/{ev_doc["id"]}/excluir/')
    t('dono exclui a evidência', r.status_code == 200 and r.json()['tarefa']['evidencias'] == []
      and not ChecklistTaskEvidence.objects.filter(pk=ev_doc['id']).exists(), r.content[:200])

    print('\n== ENVIAR (OK) ==')
    r = ce.post(f'{M}/execucoes/{ex_hoje.pk}/enviar/', {'respostas': [
        {'id': te[t1.pk].pk, 'feita': True, 'situacao': 'yes'},
        {'id': te[t2.pk].pk, 'feita': False, 'observacao': 'ZZ faltou etiqueta'},
        {'id': te[t3.pk].pk, 'resposta': 'no', 'observacao': 'ZZ estava ligado'},
        {'id': te[t4.pk].pk, 'situacao': 'not_applicable'},
    ]}, content_type='application/json')
    t('enviado para aprovação', r.status_code == 200 and r.json()['execucao']['status'] == 'awaiting_approval', r.content[:400])
    t('mensagem do portal vem limpa', r.status_code == 200 and r.json()['mensagem'].startswith('Checklist'), r.json().get('mensagem'))
    gravadas = {x.task_id: x for x in ChecklistTaskExecution.objects.filter(execution=ex_hoje)}
    t('tarefa normal feita, com data', gravadas[t1.pk].is_completed and gravadas[t1.pk].completed_at is not None)
    t('opcional não feita, com observação', not gravadas[t2.pk].is_completed and gravadas[t2.pk].notes == 'ZZ faltou etiqueta')
    t('sim/não gravado como no portal', gravadas[t3.pk].yes_no_answer is False and gravadas[t3.pk].is_completed)
    t('situação gravada', gravadas[t4.pk].dropdown_answer == 'not_applicable' and gravadas[t1.pk].dropdown_answer == 'yes')
    ex = ChecklistExecution.objects.get(pk=ex_hoje.pk)
    t('execução com data de envio', ex.submitted_at is not None)
    d = r.json()['execucao']
    t('depois de enviado não preenche mais', d['pode_preencher'] is False)
    t('enviar de novo: 400', ce.post(f'{M}/execucoes/{ex_hoje.pk}/enviar/', {'respostas': []},
                                     content_type='application/json').status_code == 400)
    r = ce.post(f'{M}/tarefas/{te[t1.pk].pk}/evidencias/', {'files': [SimpleUploadedFile('y.jpg', b'y', content_type='image/jpeg')]})
    t('depois de enviado o dono não sobe mais evidência (regra do portal)', r.status_code == 400 and 'aprovação' in r.json()['erro'], r.content[:200])
    t('aparece em enviados', [e['id'] for e in ce.get(f'{M}/execucoes/', {'filtro': 'enviados'}).json()['resultados']] == [ex_hoje.pk])

    print('\n== ACOMPANHAMENTO (GESTOR) ==')
    t('executor: 403', ce.get(f'{M}/acompanhamento/').status_code == 403)
    t('liberado individualmente (PADRÃO) também não: 403', cl.get(f'{M}/acompanhamento/').status_code == 403)
    r = cs.get(f'{M}/acompanhamento/', {'data': hoje.isoformat()})
    a = r.json()
    lojas = {g['loja']: g for g in a.get('lojas', [])}
    t('supervisor: 200 por loja', r.status_code == 200 and loja.name in lojas and loja2.name in lojas, list(lojas)[:5])
    minha = lojas.get(loja.name, {'pessoas': []})
    linha = next((p for p in minha['pessoas'] if p['pessoa']['id'] == executor.pk), None)
    t('executor feito na manhã, 0 atrasado (ontem ainda não tem 2 dias)', linha and linha['manha']['feitos'] == 1
      and linha['feitos'] == 1 and linha['atrasados'] == 0, linha)
    l2 = next((p for p in lojas.get(loja2.name, {'pessoas': []})['pessoas'] if p['pessoa']['id'] == outro.pk), None)
    t('o outro aparece sem fazer', l2 and l2['feitos'] == 0 and l2['total'] == 1, l2)
    r = cs.get(f'{M}/acompanhamento/', {'data': (hoje + timedelta(days=1)).isoformat()})
    lojas_amanha = {g['loja']: g for g in r.json()['lojas']}
    linha = next((p for p in lojas_amanha.get(loja.name, {'pessoas': []})['pessoas'] if p['pessoa']['id'] == executor.pk), None)
    t('dia seguinte: ontem pendente vira atrasado', linha and linha['atrasados'] == 1 and linha['total'] == 0, linha)
    r = cs.get(f'{M}/acompanhamento/pessoas/{executor.pk}/', {'data': hoje.isoformat()})
    t('pessoa: checklists do dia', r.status_code == 200 and [e['id'] for e in r.json()['do_dia']] == [ex_hoje.pk], r.content[:300])
    t('pessoa fora do recorte (sem checklist): 404', cs.get(f'{M}/acompanhamento/pessoas/{fora.pk}/').status_code == 404)
    t('executor não abre a visão de pessoa: 403', ce.get(f'{M}/acompanhamento/pessoas/{outro.pk}/').status_code == 403)

    print('\n== SEM EFEITO COLATERAL ==')
    t('nenhum envio externo', envios == [], envios)
    t('a API não criou sessão web', Session.objects.count() == sessoes_antes)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    for d in dubles:
        d.stop()
    print('\nrollback: nada deste teste foi gravado no banco.')
    print(f'arquivos em memória (descartados): {len(memoria.listdir("checklists/evidences")[1]) if memoria.exists("checklists/evidences") else 0}')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
