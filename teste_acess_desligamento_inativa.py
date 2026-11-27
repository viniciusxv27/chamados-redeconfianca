"""Acess (27/11/2026): três pedidos do time, no Postgres e numa transação desfeita no fim.

1. /acess/pedir-desligamento/: enviar o pedido já inativa o perfil do portal e grava a demissão (a saída da
   empresa), com o registro no log do Acess e no histórico do cadastro; a tela avisa. Ficha sem vínculo acha
   o usuário pelo CPF (e passa a ter o vínculo); sem usuário nenhum, nada é inativado e a tela diz isso.
   O sinal de inativação não abre um AvisoInativacao repetido (o pedido já está na fila).
2. /acess/desligamentos/: aviso de inativação cuja ficha não tem usuário (legado achado pelo CPF) dava
   VariableDoesNotExist no template. Agora abre e mostra a data; o aviso novo já liga a ficha ao usuário.
3. /acess/pendentes/: o card mostra os 19 campos na ordem pedida ("—" no vazio) e copia no mesmo formato.

Nada sai para a rede (requests travado); envios do portal (WhatsApp/e-mail) não são chamados por este fluxo.
"""
import os
import random
import sys
from datetime import date, timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
import redeconfianca.settings as _cfg

_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'}
               for n in _cfg.CACHES}
django.setup()
from django.conf import settings
from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import html as html_mod

import requests
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Q
from django.test import Client
from django.utils import timezone

from acess import servicos as sv
from acess.models import AcessoAcess, AvisoInativacao, Colaborador, LogAcess, PedidoDesligamento
from users.models import Sector, UserChangeLog

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


def cpf_novo():
    """CPF válido que não existe no banco (nem no cadastro do portal, nem no Acess)."""
    while True:
        n = [random.randint(0, 9) for _ in range(9)]
        for _ in range(2):
            s = sum(d * p for d, p in zip(n, range(len(n) + 1, 1, -1)))
            n.append(0 if s % 11 < 2 else 11 - s % 11)
        d = ''.join(map(str, n))
        f = f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}'
        if len(set(d)) > 1 and not User.objects.filter(Q(cpf=d) | Q(cpf=f)).exists() \
                and not Colaborador.objects.filter(cpf=d).exists():
            return d


print('banco:', connection.vendor)
marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede), \
            mock.patch.object(requests, 'request', sem_rede):
        hoje = timezone.localdate()
        loja = Sector.objects.create(name='Loja ZZ Inativa Pedido', adabas='ZZI0001-001')
        outra = Sector.objects.create(name='Loja ZZ Inativa Outra', adabas='ZZI0002-001')
        rh_setor = Sector.objects.filter(name__iexact='RH').first() or Sector.objects.create(name='RH')
        op = User.objects.create_user(username='zz.ip.op', email='zz.ip.op@exemplo-teste.local', password='x',
                                      first_name='ZZ', last_name='Operadora', hierarchy='ADMIN')
        AcessoAcess.objects.create(user=op, permissoes=['desligamentos.gerenciar', 'colaboradores.ver'])
        rh = User.objects.create_user(username='zz.ip.rh', email='zz.ip.rh@exemplo-teste.local', password='x',
                                      first_name='ZZ', last_name='do RH', sector=rh_setor)
        crh, cop = Client(), Client()
        crh.force_login(rh)
        cop.force_login(op)
        saida, execucao = hoje - timedelta(days=2), hoje + timedelta(days=5)

        def pessoa(n, cpf, **extra):
            return User.objects.create_user(username=f'zz.ip.{n}', email=f'zz.ip.{n}@exemplo-teste.local',
                                            password='x', first_name='ZZ Inativa', last_name=n.capitalize(),
                                            cpf=cpf, sector=loja, **extra)

        def pedir(c, saida_=saida):
            return crh.post('/acess/pedir-desligamento/', {
                'colaborador': c.id, 'tipo': PedidoDesligamento.DESLIGAMENTO, 'saida': saida_.isoformat(),
                'execucao': execucao.isoformat(), 'observacao': 'teste'}, follow=True)

        print('— 1. Pedido de desligamento inativa o perfil')
        h = crh.get('/acess/pedir-desligamento/').content.decode()
        t('a tela avisa antes de enviar que o perfil é inativado', 'data-aviso-inativa' in h)
        cpf1 = cpf_novo()
        u1 = pessoa('um', cpf1, job_title='Consultor ZZ')
        c1 = sv.criar_colaborador({'nome': 'ZZ Inativa Um', 'cpf': cpf1}, loja, usuario=op, user=u1)
        avisos_antes = AvisoInativacao.objects.count()
        r = pedir(c1)
        u1.refresh_from_db()
        p1 = PedidoDesligamento.objects.filter(colaborador=c1).first()
        t('pedido criado e aberto', p1 is not None and p1.status == PedidoDesligamento.ABERTO)
        t('perfil inativado na hora', u1.is_active is False and u1.status == User.STATUS_INATIVO, (u1.is_active, u1.status))
        t('demissão = saída da empresa informada', u1.demission_date == saida, u1.demission_date)
        t('motivo da inativação diz quem pediu', 'ZZ do RH' in (u1.inactivation_reason or ''), u1.inactivation_reason)
        log = LogAcess.objects.filter(colaborador=c1, acao='USUARIO_INATIVADO_PEDIDO').first()
        t('log do Acess com quem fez', log is not None and log.usuario_id == rh.id and saida.strftime('%d/%m/%Y') in log.descricao,
          getattr(log, 'descricao', None))
        campos = set(UserChangeLog.objects.filter(target=u1, changed_by=rh).values_list('field', flat=True))
        t('histórico do cadastro com is_active e demissão', {'is_active', 'demission_date'} <= campos, campos)
        t('sem AvisoInativacao repetido (o pedido já está na fila)', AvisoInativacao.objects.count() == avisos_antes)
        corpo = html_mod.unescape(r.content.decode())
        t('a tela de confirmação avisa que o perfil foi inativado',
          f'perfil de {c1.nome} no portal foi inativado' in corpo and saida.strftime('%d/%m/%Y') in corpo)
        t('segundo pedido para a mesma pessoa é recusado (sem mudar nada)',
          'Já existe um pedido em aberto' in html_mod.unescape(pedir(c1).content.decode()))

        print('— Ficha do legado sem vínculo: acha pelo CPF (formatado no cadastro)')
        cpf2 = cpf_novo()
        u2 = pessoa('dois', f'{cpf2[:3]}.{cpf2[3:6]}.{cpf2[6:9]}-{cpf2[9:]}')
        c2 = sv.criar_colaborador({'nome': 'ZZ Inativa Dois', 'cpf': cpf2}, loja, usuario=op)
        pedir(c2, saida_=hoje)
        u2.refresh_from_db()
        c2.refresh_from_db()
        t('usuário achado pelo CPF é inativado', u2.is_active is False and u2.demission_date == hoje)
        t('a ficha passa a ter o vínculo com o usuário', c2.user_id == u2.id)

        print('— Sem usuário no portal')
        c3 = sv.criar_colaborador({'nome': 'ZZ Inativa Tres', 'cpf': cpf_novo()}, loja, usuario=op)
        r = pedir(c3)
        corpo = html_mod.unescape(r.content.decode())
        t('pedido criado mesmo sem usuário', PedidoDesligamento.objects.filter(colaborador=c3).exists())
        t('a tela diz que nenhum perfil foi inativado', 'nenhum perfil foi inativado' in corpo)

        print('— Usuário já inativo sem demissão')
        cpf4 = cpf_novo()
        u4 = pessoa('quatro', cpf4)
        c4 = sv.criar_colaborador({'nome': 'ZZ Inativa Quatro', 'cpf': cpf4}, loja, usuario=op, user=u4)
        User.objects.filter(pk=u4.pk).update(is_active=False)   # por fora do save: sem aviso
        corpo = html_mod.unescape(pedir(c4).content.decode())
        u4.refresh_from_db()
        t('só a demissão é preenchida', u4.is_active is False and u4.demission_date == saida)
        t('a tela diz que já estava inativo', 'já estava inativo' in corpo)

        print('— 2. /acess/desligamentos/ com aviso de ficha sem usuário')
        cpf5 = cpf_novo()
        u5 = pessoa('cinco', cpf5, demission_date=date(2026, 2, 9))
        c5 = sv.criar_colaborador({'nome': 'ZZ Inativa Cinco', 'cpf': cpf5}, loja, usuario=op)  # sem vínculo
        # como estava no banco real (aviso 88): aviso com usuário, ficha sem
        a5 = AvisoInativacao.objects.create(colaborador=c5, user=u5)
        c6 = sv.criar_colaborador({'nome': 'ZZ Inativa Seis', 'cpf': cpf_novo()}, outra, usuario=op)
        a6 = AvisoInativacao.objects.create(colaborador=c6, user=None)                           # nem um nem outro
        r = cop.get('/acess/desligamentos/')
        h = r.content.decode()
        t('a tela abre (antes: VariableDoesNotExist)', r.status_code == 200, r.status_code)
        bloco5 = h[h.find(c5.nome):][:1500]
        bloco6 = h[h.find(c6.nome):][:1500]
        t('data de demissão do usuário do aviso aparece', 'data de demissão: 09/02/2026' in bloco5)
        t('sem usuário nenhum: "não informada"', 'não informada' in bloco6)
        a5.delete()
        a6.delete()
        print('— O aviso novo liga a ficha achada pelo CPF')
        cpf7 = cpf_novo()
        u7 = pessoa('sete', cpf7)
        c7 = sv.criar_colaborador({'nome': 'ZZ Inativa Sete', 'cpf': cpf7}, loja, usuario=op)
        u7.is_active = False
        u7.save()                                   # o sinal do Acess cria o aviso
        c7.refresh_from_db()
        aviso7 = AvisoInativacao.objects.filter(colaborador=c7, status=AvisoInativacao.PENDENTE).first()
        t('aviso criado pela inativação', aviso7 is not None and aviso7.user_id == u7.id)
        t('a ficha ganhou o vínculo com o usuário', c7.user_id == u7.id)
        t('desligamentos abre com esse aviso', cop.get('/acess/desligamentos/').status_code == 200)

        print('— 3. Campos do usuário no card de Acessos pendentes')
        cpf8 = cpf_novo()
        u8 = pessoa('oito', cpf8, phone='(11) 98888-7777', birth_date=date(1995, 3, 4),
                    admission_date=date(2026, 11, 3), job_title='Consultor de Vendas', rg='12.345.678-9',
                    rg_issuer='SSP/SP', sex='FEMININO', pis='123.45678.90-1', marital_status='CASADO',
                    address='Rua das Flores', address_number='45', address_complement='ap 2', neighborhood='Centro',
                    city='Campinas', state='SP', cep='13010-000', father_name='ZZ Pai', mother_name='ZZ Mãe',
                    uniform_size_shirt='M', uniform_size_pants='40')
        u8.sector = outra
        u8.save()
        c8 = sv.criar_colaborador({'nome': 'ZZ Inativa Oito', 'cpf': cpf8, 'cargo': 'Outro cargo'}, loja, usuario=op,
                                  user=u8)
        c9 = sv.criar_colaborador({'nome': 'ZZ Inativa Nove', 'cpf': cpf_novo(), 'email': 'zz9@exemplo-teste.local'},
                                  loja, usuario=op)
        r = cop.get('/acess/pendentes/', {'etapa': 1, 'q': 'ZZ Inativa'})
        h = html_mod.unescape(r.content.decode())
        t('pendentes abre', r.status_code == 200, r.status_code)
        b8 = h[h.find(f'>{c8.nome}<'):]
        b8 = b8[:b8.find('</details>')]
        ordem = [b8.find(f'<dt>{rot}</dt>') for rot in sv.CAMPOS_DO_CARD]
        t('os 19 campos no card, na ordem pedida', all(x >= 0 for x in ordem) and ordem == sorted(ordem), ordem)
        esperados = ['ZZ Inativa Oito', 'zz.ip.oito@exemplo-teste.local', cpf8, '04/03/1995', '(11) 98888-7777',
                     '03/11/2026', 'Loja ZZ Inativa Outra', 'Consultor de Vendas', '12.345.678-9', 'SSP/SP', 'Feminino',
                     '123.45678.90-1', 'Casado(a)', 'Rua das Flores, 45, ap 2 – Centro – Campinas/SP – CEP 13010-000',
                     'ZZ Pai', 'ZZ Mãe', '>M<', '>40<']
        faltam = [e for e in esperados if e not in b8]
        t('valores do cadastro (loja = setor principal, não a ficha)', not faltam, faltam)
        dd = b8[b8.find('<dt>VALE TRANSPORTE</dt>'):][:80]
        t('vale transporte sem campo no portal aparece "—"', 'class="vazio">—' in dd, dd)
        texto = sv.texto_do_card(sv.dados_do_card(c8, u8))
        linhas = texto.split('\n')
        t('copiar dados: uma linha por campo, mesma ordem e "—" no vazio',
          len(linhas) == 19 and linhas[0] == 'NOME: ZZ Inativa Oito' and linhas[10] == 'VALE TRANSPORTE: —'
          and linhas[6] == 'LOJA: Loja ZZ Inativa Outra', linhas[:11])
        t('o botão do card copia esse texto', 'data-copiar-card' in b8 and 'NOME: ZZ Inativa Oito' in b8)
        b9 = h[h.find(f'>{c9.nome}<'):]
        b9 = b9[:b9.find('</details>')]
        t('ficha sem usuário: usa nome/e-mail/loja da ficha e "—" no resto',
          'zz9@exemplo-teste.local' in b9 and 'Loja ZZ Inativa Pedido' in b9 and b9.count('class="vazio">—') >= 12,
          b9.count('class="vazio">—'))
finally:
    marcador.__exit__(Exception, Exception('desfaz'), None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
