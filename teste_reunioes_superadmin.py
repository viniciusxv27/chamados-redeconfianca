"""Reuniões: o SUPERADMIN vê todas, inclusive as que não organizou nem foi convidado.

Pedido: "SUPERADMIN deve conseguir ver todas as reuniões". O módulo só olhava
`is_superuser`, e o SUPERADMIN do portal é cadastrado pela hierarquia — ele não
via nem abria as reuniões dos outros.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
from datetime import timedelta

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from agenda.models import MeetingTranscription
from reunioes.models import ParticipanteReuniao, Reuniao
from reunioes.permissoes import e_superadmin

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


def novo(username, **kw):
    return User.objects.create_user(username=username, email=f'{username}@exemplo-teste.local',
                                    password='S3nha!teste', **kw)


marcador = transaction.atomic()
marcador.__enter__()
try:
    print('== QUEM É SUPERADMIN ==')
    chefe = novo('zzrs.chefe', first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN')
    root = novo('zzrs.root', first_name='ZZ', last_name='Root', is_superuser=True)
    ana = novo('zzrs.ana', first_name='ZZAna', last_name='Organiza')
    bia = novo('zzrs.bia', first_name='ZZBia', last_name='Convidada')
    caio = novo('zzrs.caio', first_name='ZZCaio', last_name='DeFora')
    t('hierarquia SUPERADMIN conta', e_superadmin(chefe))
    t('superusuário do Django também', e_superadmin(root))
    t('usuário comum não', not e_superadmin(ana))

    agora = timezone.now()
    futura = Reuniao.objects.create(titulo='ZZRS Planejamento da Ana', organizador=ana,
                                    inicio=agora + timedelta(days=2))
    ParticipanteReuniao.objects.create(reuniao=futura, user=bia)
    passada = Reuniao.objects.create(titulo='ZZRS Retro antiga da Ana', organizador=ana,
                                     inicio=agora - timedelta(days=5), status=Reuniao.ENCERRADA)
    cancelada = Reuniao.objects.create(titulo='ZZRS Cancelada do Caio', organizador=caio,
                                       inicio=agora + timedelta(days=1), status=Reuniao.CANCELADA)
    entrevista = Reuniao.objects.create(titulo='ZZRS Entrevista do Caio', organizador=caio,
                                        inicio=agora + timedelta(days=3), tipo=Reuniao.ENTREVISTA)
    do_chefe = Reuniao.objects.create(titulo='ZZRS Reunião em que o chefe foi convidado', organizador=caio,
                                      inicio=agora + timedelta(days=4))
    ParticipanteReuniao.objects.create(reuniao=do_chefe, user=chefe)

    c = Client()
    c.force_login(chefe)
    cc = Client()
    cc.force_login(caio)

    print('\n== A LISTA DO SUPERADMIN ==')
    r = c.get('/reunioes/')
    html = r.content.decode()
    t('abre (200)', r.status_code == 200, r.status_code)
    t('tem a seção "Todas as reuniões"', 'Todas as reuniões' in html)
    t('mostra reunião futura de outra pessoa', futura.titulo in html)
    t('mostra reunião passada de outra pessoa', passada.titulo in html)
    t('mostra a cancelada, na parte de anteriores e canceladas', cancelada.titulo in html)
    t('marca a entrevista', 'Entrevista' in html and entrevista.titulo in html)
    t('a reunião em que ele foi convidado aparece uma vez só (nas dele)',
      html.count(do_chefe.titulo) == 1, html.count(do_chefe.titulo))
    t('mostra o organizador', 'ZZAna Organiza' in html)
    t('botão de configuração para o SUPERADMIN pela hierarquia', '/reunioes/configuracao/' in html)

    r = c.get('/reunioes/?q=Retro')
    html = r.content.decode()
    t('a busca filtra pelo tema', passada.titulo in html and futura.titulo not in html)
    r = c.get('/reunioes/?q=ZZAna')
    html = r.content.decode()
    t('e pelo nome do organizador', futura.titulo in html and entrevista.titulo not in html)

    print('\n== QUEM NÃO É SUPERADMIN ==')
    r = cc.get('/reunioes/')
    html = r.content.decode()
    t('lista abre', r.status_code == 200)
    t('não tem a seção de todas', 'Todas as reuniões' not in html)
    t('não vê reunião de outra pessoa', futura.titulo not in html and passada.titulo not in html)
    t('vê as próprias', entrevista.titulo in html)
    t('sem botão de configuração', '/reunioes/configuracao/' not in html)
    r = cc.get(f'/reunioes/{futura.id}/', follow=True)
    t('não abre o detalhe da reunião alheia', futura.titulo not in r.content.decode()
      or 'Você não está nesta reunião' in r.content.decode())

    print('\n== O SUPERADMIN ABRE E CUIDA DA REUNIÃO ALHEIA ==')
    r = c.get(f'/reunioes/{futura.id}/')
    t('detalhe (200)', r.status_code == 200 and futura.titulo in r.content.decode(), r.status_code)
    r = c.get(f'/reunioes/{futura.id}/sala/')
    t('sala (200)', r.status_code == 200, r.status_code)
    t('entrar na sala não o marca como participante',
      not ParticipanteReuniao.objects.filter(reuniao=futura, user=chefe).exists())
    t('pode editar a reunião alheia', futura.pode_editar(chefe))
    t('quem é de fora não pode', not futura.pode_editar(caio))
    t('a convidada vê, mas não edita', futura.pode_ver(bia) and not futura.pode_editar(bia))
    r = c.get('/reunioes/configuracao/')
    t('configuração abre para o SUPERADMIN pela hierarquia', r.status_code == 200, r.status_code)

    ata = MeetingTranscription.objects.create(owner=ana, title='ZZRS Ata da Ana', status='processing')
    r = c.post(f'/reunioes/{futura.id}/ata/', {'transcricao': ata.id})
    dados = r.json() if r.headers.get('Content-Type', '').startswith('application/json') else {}
    t('registra a ata de outra pessoa na reunião', r.status_code == 200 and dados.get('ok'), (r.status_code, dados))
    ata.refresh_from_db()
    futura.refresh_from_db()
    t('a ata fica ligada ao evento da reunião', ata.event_id == futura.evento_id)
    t('a reunião vira encerrada', futura.status == Reuniao.ENCERRADA)
    r = cc.post(f'/reunioes/{entrevista.id}/ata/', {'transcricao': ata.id})
    t('quem não é dono nem SUPERADMIN não registra ata alheia', r.status_code == 403, r.status_code)

    print('\n== O DETALHE MOSTRA O ESTADO DA ATA ==')
    ata.status = 'recording'
    ata.save(update_fields=['status'])
    if futura.evento_id:
        html = c.get(f'/reunioes/{futura.id}/').content.decode()
        t('ata gravando aparece como "gravando"', 'gravando' in html)
        ata.status = 'error'
        ata.save(update_fields=['status'])
        html = c.get(f'/reunioes/{futura.id}/').content.decode()
        t('ata com erro diz o que fazer', 'abra para tentar de novo' in html)
    else:
        t('reunião sem evento na agenda (nada a mostrar no detalhe)', True)

    print('\n== NADA DE COMENTÁRIO VAZANDO ==')
    for url in ('/reunioes/', f'/reunioes/{futura.id}/'):
        corpo = c.get(url).content.decode()
        t(f'{url}: sem {{# #}} nem comment cru', '{#' not in corpo and '{% comment' not in corpo)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
