"""Acess (23/11/2026): "Excluir" ao lado de cada pessoa em Acessos pendentes (/acess/pendentes/).

- só nesta tela (a ficha continua com o "Arquivar ficha" dela, com senha);
- permissão colaboradores.excluir (a mesma "Excluir (arquivar)" da ficha); SUPERADMIN tem;
- só POST, com CSRF; confere no servidor que a pessoa ainda está pendente (Etapas 1–4);
- não apaga: arquiva a ficha (status DESLIGADO), guarda quem/quando no histórico, mantém XML e chamados;
- aviso de inativação pendente vira descartado e pedido de desligamento em aberto vira recusado (com aviso a quem pediu);
- chamado ativo trava, como no arquivar da ficha; versão velha da tela dá conflito.

Transação desfeita no fim; nada sai para a rede.
"""
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
import redeconfianca.settings as _cfg  # noqa: E402

_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-pe-{n}'}
               for n in _cfg.CACHES}
django.setup()
from django.conf import settings  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import requests  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.utils import timezone  # noqa: E402

from acess import servicos as sv  # noqa: E402
from acess.models import (AcessoAcess, AvisoInativacao, Chamado, Colaborador, LogAcess,  # noqa: E402
                          PedidoDesligamento, XmlGefac)
from core.models import Notification  # noqa: E402
from users.models import Sector  # noqa: E402

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
    raise AssertionError('o teste tentou sair para a rede')


def url(c):
    return f'/acess/colaborador/{c.id}/excluir-pendente/'


marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede):
        hoje = timezone.localdate()
        loja = Sector.objects.create(name='Loja ZZ Pendente Excluir', adabas='ZZPE001-001')

        def operador(n, permissoes):
            u = User.objects.create_user(username=f'zz.pe.{n}', email=f'zz.pe.{n}@exemplo-teste.local', password='x',
                                         first_name='ZZ', last_name=f'Op {n}', hierarchy='ADMIN')
            if permissoes is not None:
                AcessoAcess.objects.create(user=u, permissoes=permissoes)
            cl = Client()
            cl.force_login(u)
            return u, cl

        op, co = operador('exclui', ['colaboradores.excluir', 'sistemas.concluir'])
        so_ve, cv = operador('ve', ['colaboradores.ver', 'sistemas.concluir', 'gefac.concluir'])
        rh = User.objects.create_user(username='zz.pe.rh', email='zz.pe.rh@exemplo-teste.local', password='x',
                                      first_name='ZZ', last_name='RH')

        def pessoa(n, cpf):
            u = User.objects.create_user(username=f'zz.pe.p{n}', email=f'zz.pe.p{n}@exemplo-teste.local', password='x',
                                         first_name='ZZ', last_name=f'Pend {n}', sector=loja)
            return sv.criar_colaborador({'nome': f'ZZ Pend {n}', 'cpf': cpf}, loja, usuario=op, user=u)

        c1 = pessoa('um', '52998224725')          # Etapa 1 (GEFAC)
        c2 = pessoa('dois', '11144477735')        # Etapa 4 com chamado ativo
        c3 = pessoa('tres', '39053344705')        # liberado (fora da esteira)
        c4 = pessoa('quatro', '86288366757')      # Etapa 1, para o teste de versão velha
        t('as fichas novas nascem na Etapa 1', c1.etapa_calculada == 1 and c4.etapa_calculada == 1)

        Colaborador.objects.filter(pk=c2.pk).update(login='ZZ12345', entrega_login='PENDENTE', etapa=4,
                                                    sistemas={**c2.sistemas, 'GEFAC': 'OK', 'TREINAMENTO': 'OK'})
        c2.refresh_from_db()
        ch = sv.criar_chamado(c2, 'ZZPE-777', 'LIBERACAO_INICIAL', [], '', minutos=480, usuario=op)
        Colaborador.objects.filter(pk=c3.pk).update(login='ZZ54321', entrega_login='ENVIADO', etapa=0)
        c3.refresh_from_db()
        t('preparação: etapa 4 e liberado', c2.etapa_calculada == 4 and c3.etapa_calculada == 0)

        # o que está ligado à ficha c1: XML do GEFAC, aviso de inativação pendente, pedido de desligamento aberto
        xml = XmlGefac.objects.create(colaborador=c1, arquivo='acess/gefac/zz/zz-pe.xml', nome_original='zz.xml', tamanho=5)
        aviso = AvisoInativacao.objects.create(colaborador=c1, user=c1.user)
        pedido = PedidoDesligamento.objects.create(colaborador=c1, saida_empresa=hoje, execucao_sugerida=hoje,
                                                   solicitante=rh)

        print('— Tela')
        h = co.get('/acess/pendentes/?etapa=1').content.decode()
        t('botão Excluir ao lado de quem está pendente', url(c1) in h and 'data-excluir-pendente' in h)
        t('popup de confirmação na própria tela', 'id="acExcluirPendente"' in h and 'name="motivo"' in h)
        h4 = co.get('/acess/pendentes/?etapa=4').content.decode()
        i = h4.index('ZZ PEND DOIS')
        t('com chamado ativo o botão vem travado e explica', 'conclua o chamado' in h4[i:i + 6000].lower()
          and url(c2) not in h4)
        hv = cv.get('/acess/pendentes/?etapa=1').content.decode()
        t('quem não tem colaboradores.excluir não vê o botão', url(c1) not in hv and 'ZZ PEND UM' in hv)
        hf = co.get(f'/acess/colaborador/{c1.id}/').content.decode()
        t('a ficha não ganhou este botão (só a tela de pendentes)', 'excluir-pendente' not in hf)
        hc = co.get('/acess/colaboradores/').content.decode()
        t('a lista de colaboradores também não', 'excluir-pendente' not in hc)

        print('— Regras do servidor')
        t('GET não exclui (405)', co.get(url(c1)).status_code == 405)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(op)
        r = csrf.post(url(c1), {'versao': c1.versao})
        c1.refresh_from_db()
        t('sem CSRF é recusado', r.status_code == 403 and c1.status == Colaborador.ATIVO)
        r = cv.post(url(c1), {'versao': c1.versao})
        c1.refresh_from_db()
        t('sem a permissão é recusado', r.status_code == 302 and c1.status == Colaborador.ATIVO)
        r = cv.post(url(c1), {'versao': c1.versao}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        t('sem a permissão, por fetch, vem 403', r.status_code == 403)
        r = co.post(url(c3), {'versao': c3.versao, 'voltar': '/acess/pendentes/?etapa=1'})
        c3.refresh_from_db()
        t('quem já saiu de pendente não é excluído por aqui', c3.status == Colaborador.ATIVO and r.status_code == 302)
        msgs = [str(m) for m in r.wsgi_request._messages]
        t('e a tela diz por quê', any('não está mais em Acessos pendentes' in m for m in msgs), msgs)
        r = co.post(url(c2), {'versao': c2.versao})
        c2.refresh_from_db()
        t('chamado ativo trava a exclusão', c2.status == Colaborador.ATIVO
          and Chamado.objects.get(pk=ch.pk).status == Chamado.ANDAMENTO)
        versao_velha = c4.versao
        sv.editar_dados(c4, {'nome': 'ZZ Pend Quatro Editado', 'cpf': c4.cpf}, c4.versao, op)
        r = co.post(url(c4), {'versao': versao_velha})
        c4.refresh_from_db()
        msgs = [str(m) for m in r.wsgi_request._messages]
        t('versão velha da tela dá conflito', c4.status == Colaborador.ATIVO
          and any('alterada por outra pessoa' in m for m in msgs), msgs)
        t('voltar só aceita /acess/', co.post(url(c4), {'versao': 'x', 'voltar': 'https://fora.exemplo/'})['Location']
          .startswith('/acess/pendentes/'))

        print('— Excluir')
        notif_antes = Notification.objects.filter(user=rh).count()
        r = co.post(url(c1), {'versao': c1.versao, 'motivo': 'desistiu da vaga', 'voltar': '/acess/pendentes/?etapa=1'})
        t('redireciona de volta para a etapa', r.status_code == 302 and r['Location'] == '/acess/pendentes/?etapa=1')
        msgs = [str(m) for m in r.wsgi_request._messages]
        t('com mensagem de sucesso', any('excluído de Acessos pendentes' in m for m in msgs), msgs)
        c1.refresh_from_db()
        t('não apaga: a ficha fica arquivada (DESLIGADO)', Colaborador.objects.filter(pk=c1.pk).exists()
          and c1.status == Colaborador.DESLIGADO and c1.desligado_em is not None and c1.etapa == 0)
        log = LogAcess.objects.filter(colaborador=c1, acao='EXCLUIDO_PENDENTES').first()
        t('histórico guarda quem excluiu, quando, a etapa e o motivo', log is not None and log.usuario == op
          and log.quando is not None and 'desistiu da vaga' in log.descricao and 'Etapa 1' in log.descricao
          and (log.antes or {}).get('etapa') == 1 and (log.depois or {}).get('status') == 'DESLIGADO')
        t('o XML do GEFAC continua guardado', XmlGefac.objects.filter(pk=xml.pk).exists())
        aviso.refresh_from_db()
        t('aviso de inativação pendente vira descartado', aviso.status == AvisoInativacao.DESCARTADO
          and aviso.decidido_por == op and 'Acessos pendentes' in aviso.observacao)
        pedido.refresh_from_db()
        t('pedido de desligamento aberto vira recusado', pedido.status == PedidoDesligamento.RECUSADO
          and pedido.tratado_por == op and 'Acessos pendentes' in pedido.resposta)
        t('e quem pediu é avisado no portal', Notification.objects.filter(user=rh).count() == notif_antes + 1)
        h = co.get('/acess/pendentes/?etapa=1').content.decode()
        t('some da lista de pendentes (os outros continuam)', url(c1) not in h and f'/acess/colaborador/{c1.id}/"' not in h
          and url(c4) in h, h[h.find('ac-msg'):][:300])
        r = co.post(url(c1), {'versao': c1.versao})
        t('excluir de novo é recusado', LogAcess.objects.filter(colaborador=c1, acao='EXCLUIDO_PENDENTES').count() == 1)

        print('— Por fetch (JSON)')
        c5 = pessoa('cinco', '74682489070')
        r = co.post(url(c5), {'versao': c5.versao}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        c5.refresh_from_db()
        t('fetch recebe ok e a ficha é arquivada', r.status_code == 200 and r.json().get('ok') is True
          and c5.status == Colaborador.DESLIGADO)
        r = co.post(url(c5), {'versao': c5.versao}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        t('fetch de quem não está mais pendente recebe erro', r.status_code == 400 and r.json().get('ok') is False)

        print('— SUPERADMIN')
        sa = User.objects.create_user(username='zz.pe.sa', email='zz.pe.sa@exemplo-teste.local', password='x',
                                      first_name='ZZ', last_name='SA', hierarchy='SUPERADMIN')
        cs = Client()
        cs.force_login(sa)
        c6 = pessoa('seis', '15350946056')
        t('SUPERADMIN vê o botão', url(c6) in cs.get('/acess/pendentes/?etapa=1').content.decode())
        cs.post(url(c6), {'versao': c6.versao})
        c6.refresh_from_db()
        t('e exclui', c6.status == Colaborador.DESLIGADO)
finally:
    marcador.__exit__(Exception, Exception('desfaz'), None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
