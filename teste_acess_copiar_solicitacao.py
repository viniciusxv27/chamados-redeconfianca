"""Acess (22/10/2026): feedback da operação.

- "não está salvando": a ficha salva cada bloco sem recarregar (JSON com a versão nova), o status do
  sistema salva ao trocar, e o segundo salvamento na mesma tela não cai no "alterada por outra pessoa";
- botão de copiar (nº do chamado sem o #, nome, CPF só números, nascimento, e-mail, login...) na ficha,
  nos chamados e na aba Lojas; "Copiar tudo";
- o "#" que o Acess anterior gravava no nº do chamado sai;
- aba "Acessos" na janela de Suporte do portal: o pedido cai em Solicitações e vira chamado.

Transação desfeita no fim; nada sai para a rede.
"""
import json
import os
import sys
from datetime import date
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-acp'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-acp2'}}
django.setup()
from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import requests
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from acess import servicos as sv
from acess.models import AcessoAcess, Chamado, Colaborador, Solicitacao
from acess.permissoes import CHAVES
from core.models import Notification
from users.models import Sector

User = get_user_model()
ok = fail = 0
AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def sem_rede(*a, **k):
    raise AssertionError('o teste tentou sair para a rede')


marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede):
        loja = Sector.objects.create(name='Loja ZZ Copia', adabas='ESZZC01-001')
        op = User.objects.create_user(username='zz.acp.op', email='zz.acp.op@exemplo-teste.local', password='x',
                                      first_name='ZZ', last_name='Operadora', hierarchy='ADMIN')
        AcessoAcess.objects.create(user=op, permissoes=sorted(CHAVES), trata_chamados=True)
        pessoa = User.objects.create_user(username='zz.acp.pessoa', email='zz.acp.pessoa@exemplo-teste.local',
                                          password='x', first_name='ZZ', last_name='Vendedora', cpf='529.982.247-25',
                                          phone='27 99999-1111', sector=loja)
        sem_ficha = User.objects.create_user(username='zz.acp.sem', email='zz.acp.sem@exemplo-teste.local',
                                             password='x', first_name='ZZ', last_name='SemFicha')
        c = sv.criar_colaborador({'nome': 'ZZ Vendedora Copia', 'cpf': '52998224725', 'nascimento': '1990-05-07',
                                  'email': 'zz.vend@exemplo-teste.local', 'telefone': '27 99999-1111',
                                  'cargo': 'consultor'}, loja, usuario=op)
        Colaborador.objects.filter(pk=c.pk).update(login='81234567')
        c.refresh_from_db()
        co = Client()
        co.force_login(op)

        print('— Salvar sem recarregar (o "não está salvando")')
        url = f'/acess/colaborador/{c.id}/acao/'
        dados = {'acao': 'dados', 'versao': c.versao, 'nome': c.nome, 'cpf': '529.982.247-25', 'nascimento': '1990-05-07',
                 'email': c.email, 'telefone': '27 98888-2222', 'cargo': c.cargo}
        r = co.post(url, dados, **AJAX)
        j = r.json()
        c.refresh_from_db()
        t('dados salvos pela tela respondem JSON com a versão nova', r.status_code == 200 and j['ok'] and j['versao'] == c.versao
          and c.telefone == '27 98888-2222', j)
        t('JSON traz o que copiar já atualizado', j['copia']['telefone'] == '27 98888-2222' and j['copia']['cpf'] == '52998224725')
        sis = {'acao': 'sistemas', 'versao': j['versao']}
        for i, s in enumerate(sv.SISTEMAS):
            sis[f'sis_{i}'] = c.status_de(s)
        sis['sis_0'] = 'OK'
        r = co.post(url, sis, **AJAX)
        c.refresh_from_db()
        t('segundo bloco com a versão devolvida salva (sem "alterada por outra pessoa")', r.json()['ok']
          and c.status_de(sv.SISTEMAS[0]) == 'OK', r.content[:200])
        sis['versao'] = j['versao']  # versão velha
        sis['sis_0'] = 'PENDENTE'
        r = co.post(url, sis, **AJAX)
        t('versão velha: erro de conflito em JSON (não perde em silêncio)', r.status_code == 400 and r.json()['conflito'])
        r = co.post(url, dict(dados, versao=c.versao, telefone='27 97777-3333'), follow=True)
        c.refresh_from_db()
        t('sem JavaScript continua salvando com a mensagem', c.telefone == '27 97777-3333'
          and 'Dados salvos.' in [str(m) for m in r.context['messages']])
        h = co.get(f'/acess/colaborador/{c.id}/').content.decode()
        t('ficha marca os blocos que salvam na tela', h.count('" data-ficha-form') == 3 and 'data-autosalvar' in h
          and 'beforeunload' in h)

        print('— Copiar')
        t('ficha: bloco "Para abrir chamado" com Copiar tudo', 'Para abrir chamado' in h and 'data-copia-tudo' in h)
        t('CPF só com números, nascimento dd/mm/aaaa, login e e-mail', 'data-copiar="52998224725"' in h
          and 'data-copiar="07/05/1990"' in h and 'data-copiar="81234567"' in h and 'data-copiar="zz.vend@exemplo-teste.local"' in h)
        t('texto do Copiar tudo', 'CPF: 52998224725' in c.texto_para_copiar and 'Loja: Loja ZZ Copia' in c.texto_para_copiar
          and 'ADABAS: ESZZC01-001' in c.texto_para_copiar)
        ch = sv.criar_chamado(c, '#ZZ998877', 'TROCA_SENHA', ['SAP'], 'zz', minutos=60, usuario=op)
        t('nº do chamado grava sem o #', ch.codigo == 'ZZ998877')
        h = co.get(f'/acess/colaborador/{c.id}/').content.decode()
        t('ficha: copiar ao lado do nº do chamado, sem o #', '#ZZ998877</span><button type="button" class="ac-copiar" data-copiar="ZZ998877"' in h)
        h = co.get('/acess/chamados/?q=%23ZZ998877').content.decode()
        t('chamados: busca com # acha e copia o nº e os dados', 'data-copiar="ZZ998877"' in h and 'data-copiar="52998224725"' in h
          and 'Chamado: ZZ998877' in h)
        h = co.get('/acess/lojas/?q=ZZ+Vendedora').content.decode()
        t('lojas: copiar nome, CPF, nascimento e login', 'data-copiar="52998224725"' in h and 'data-copiar="07/05/1990"' in h
          and 'data-copiar="81234567"' in h and 'data-copiar="ZZ VENDEDORA COPIA"' in h, h.count('data-copiar'))

        print('— Pedido pela janela de Suporte')
        cp = Client()
        cp.force_login(pessoa)
        h = cp.get('/').content.decode()
        t('janela de Suporte tem a aba Acessos', 'id="accessReqTab"' in h and 'id="accessReqView"' in h)
        d = cp.get('/acess/solicitar/').json()
        t('formulário: motivos, sistemas e a ficha achada pelo usuário', d['ficha'] and d['ficha']['login'] == '81234567'
          and any(m['valor'] == 'DESBLOQUEIO' for m in d['motivos']) and 'SAP' in d['sistemas'] and d['contato'] == '27 99999-1111', d)
        r = cp.post('/acess/solicitar/', data=json.dumps({'motivo': 'DESBLOQUEIO', 'sistemas': ['SAP'], 'descricao': 'x'}),
                    content_type='application/json')
        t('descrição curta é recusada', r.status_code == 400 and 'poucas palavras' in r.json()['erro'])
        antes = Notification.objects.filter(user=op).count()
        r = cp.post('/acess/solicitar/', data=json.dumps({'motivo': 'DESBLOQUEIO', 'sistemas': ['SAP', 'CITRIX'],
                                                          'descricao': 'SAP diz usuário bloqueado', 'contato': '27 9 1234'}),
                    content_type='application/json')
        j = r.json()
        sol = Solicitacao.objects.get(solicitante=pessoa)
        t('pedido criado e ligado à ficha', j['ok'] and sol.colaborador_id == c.id and sol.sistemas == ['SAP', 'CITRIX']
          and j['numero'] == sol.numero, j)
        t('quem trata chamados é avisado', Notification.objects.filter(user=op).count() == antes + 1)
        cs = Client()
        cs.force_login(sem_ficha)
        cs.post('/acess/solicitar/', data=json.dumps({'motivo': 'FALHA', 'descricao': 'nada entra hoje'}),
                content_type='application/json')
        t('pedido de quem não tem ficha também entra', Solicitacao.objects.filter(solicitante=sem_ficha, colaborador=None).exists())
        r = co.get('/acess/solicitacoes/')
        h = r.content.decode()
        t('aba Solicitações lista os pedidos com contador', r.status_code == 200 and sol.numero in h
          and r.context['contadores']['solicitacoes'] == 2)
        t('com o botão de abrir chamado já preenchido', f'data-sol="{sol.id}"' in h and 'data-motivo="DESBLOQUEIO"' in h
          and 'data-sistemas="SAP|CITRIX"' in h and 'Quem pediu não tem ficha' in h)
        r = co.post('/acess/chamados/novo/', {'colaborador': c.id, 'solicitacao': sol.id, 'codigo': 'ZZ-5551',
                                              'motivo': 'DESBLOQUEIO', 'sistemas': ['SAP'], 'observacao': 'zz', 'minutos': '60',
                                              'voltar': '/acess/solicitacoes/'})
        sol.refresh_from_db()
        t('abrir o chamado liga o pedido', sol.status == Solicitacao.EM_CHAMADO and sol.chamado.codigo == 'ZZ-5551')
        t('quem pediu é avisado do nº', Notification.objects.filter(user=pessoa, title__contains='ZZ-5551').exists())
        d = cp.get('/acess/solicitar/').json()
        t('"Meus pedidos" mostra o chamado aberto', d['minhas'][0]['chamado'] == 'ZZ-5551')
        ch2 = sol.chamado
        co.post(f'/acess/chamados/{ch2.id}/acao/', {'acao': 'concluir', 'versao': ch2.versao})
        sol.refresh_from_db()
        t('concluir o chamado resolve o pedido e avisa', sol.status == Solicitacao.RESOLVIDA
          and Notification.objects.filter(user=pessoa, title__contains='concluído').exists())
        outro = Solicitacao.objects.get(solicitante=sem_ficha)
        r = co.post(f'/acess/solicitacoes/{outro.id}/responder/', {'status': 'RECUSADA', 'resposta': ''}, follow=True)
        outro.refresh_from_db()
        t('recusar exige resposta', outro.status == Solicitacao.ABERTA)
        co.post(f'/acess/solicitacoes/{outro.id}/responder/', {'status': 'RECUSADA', 'resposta': 'Procure a TI da loja.'})
        outro.refresh_from_db()
        t('recusado com resposta e aviso', outro.status == Solicitacao.RECUSADA
          and Notification.objects.filter(user=sem_ficha, message='Procure a TI da loja.').exists())
        r = cp.post(f'/acess/solicitacoes/{outro.id}/responder/', {'status': 'RESOLVIDA', 'resposta': 'x'})
        t('quem não trata chamados não responde', r.status_code == 302 and Solicitacao.objects.get(pk=outro.id).status == 'RECUSADA')
        t('quem não é do Acess não vê a fila', cp.get('/acess/solicitacoes/').status_code == 302)
        for i in range(5):
            cp.post('/acess/solicitar/', data=json.dumps({'motivo': 'FALHA', 'descricao': f'pedido zz {i}'}),
                    content_type='application/json')
        r = cp.post('/acess/solicitar/', data=json.dumps({'motivo': 'FALHA', 'descricao': 'pedido zz demais'}),
                    content_type='application/json')
        t('limite de pedidos abertos por pessoa', r.status_code == 400 and 'aguardando' in r.json()['erro'])
finally:
    marcador.__exit__(Exception, Exception('desfaz'), None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
