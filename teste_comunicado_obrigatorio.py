"""Comunicados: só os marcados como "Obrigatoriedade" travam o popup de de acordo.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
O WhatsApp da criação é substituído por um dublê — nenhuma mensagem sai.
"""
import os
import re
import sys
from datetime import date
from unittest import mock

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

from communications import whatsapp
from communications.models import Communication, CommunicationRead
from communications.popup_checkers import (REGRA_ATIVA_DESDE, comunicados_pendentes,
                                           comunicados_todos_cientes)
from communications.templatetags.comunicados_popup import comunicados_para_ler
from portal_popups.models import PortalPopup
from users.models import Sector

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


def fresco(u):
    return User.objects.get(pk=u.pk)


marcador = transaction.atomic()
marcador.__enter__()
try:
    print('== A MIGRAÇÃO NÃO MUDOU NADA SOZINHA ==')
    t('comunicado anterior a 27/07/2026 nunca virou obrigatório',
      not Communication.objects.filter(created_at__date__lt=REGRA_ATIVA_DESDE,
                                       obrigatorio=True).exists())

    area = Sector.objects.create(name='ZZ Setor Comunicados')
    remetente = User.objects.create_user(
        username='co.remetente', email='co.remetente@exemplo-teste.local', password='S3nha!teste',
        sector=area, first_name='Remetente', last_name='ZZ', hierarchy='ADMIN')
    dest = User.objects.create_user(
        username='co.dest', email='co.dest@exemplo-teste.local', password='S3nha!teste',
        sector=area, first_name='Destino', last_name='ZZ', hierarchy='PADRAO')

    # O banco de dev tem comunicados obrigatórios "para todos" de verdade, que
    # também alcançam este usuário novo e o manteriam travado de qualquer jeito.
    # Desliga-os aqui (a transação desfaz) para medir só os deste teste.
    Communication.objects.update(obrigatorio=False)

    obrig = Communication.objects.create(title='ZZ Obrigatório', message='x', sender=remetente,
                                         obrigatorio=True)
    obrig.recipients.add(dest)
    opcional = Communication.objects.create(title='ZZ Opcional', message='x', sender=remetente,
                                            obrigatorio=False)
    opcional.recipients.add(dest)

    print('\n== A TRAVA ==')
    pend = comunicados_pendentes(dest)
    t('o obrigatório entra na lista de pendentes', pend.filter(id=obrig.id).exists())
    t('o opcional NÃO entra', not pend.filter(id=opcional.id).exists())
    t('com um obrigatório sem de acordo, a pessoa fica travada', not comunicados_todos_cientes(dest))

    popup = PortalPopup.objects.filter(external_check_key='comunicados_pendentes').first()
    if popup:
        t('o popup de comunicados continua pendente para ela', not popup.is_completed_by(dest))

    lista = comunicados_para_ler(dest)
    ids = {c.id for c in lista['itens']}
    t('a lista DENTRO do popup mostra o obrigatório', obrig.id in ids)
    t('e não mostra o opcional', opcional.id not in ids)

    CommunicationRead.objects.create(communication=obrig, user=dest, status='ESTOU_CIENTE')
    t('deu o Estou Ciente no obrigatório: destrava', comunicados_todos_cientes(dest))
    if popup:
        t('e o popup some', popup.is_completed_by(dest))
    t('mesmo com o opcional ainda sem de acordo',
      not CommunicationRead.objects.filter(communication=opcional, user=dest).exists())

    print('\n== O FORMULÁRIO DE CRIAÇÃO ==')
    cr = Client(); cr.force_login(remetente)
    html = cr.get('/communications/create/').content.decode()
    t('tem o checkbox "Obrigatoriedade"', 'name="obrigatorio"' in html and 'Obrigatoriedade' in html)
    t('nasce desmarcado', not re.search(r'name="obrigatorio"\s*checked', html))
    t('explica o que faz', 'trava o portal' in html)

    with mock.patch.object(whatsapp, 'enviar_whatsapp_comunicado') as zap, \
         mock.patch('core.zapi.send_whatsapp_message') as zapi:
        cr.post('/communications/create/', {
            'title': 'ZZ Criado obrigatório', 'message': 'Leia com atenção.',
            'recipients': [dest.id], 'obrigatorio': 'on'})
        criado_obr = Communication.objects.filter(title='ZZ Criado obrigatório').first()
        t('marcado: nasce obrigatório', criado_obr is not None and criado_obr.obrigatorio)
        t('o WhatsApp é avisado de que é obrigatório',
          zap.called and zap.call_args.kwargs.get('obrigatorio') is True, zap.call_args)

        zap.reset_mock()
        cr.post('/communications/create/', {
            'title': 'ZZ Criado opcional', 'message': 'Só um aviso.',
            'recipients': [dest.id]})
        criado_opc = Communication.objects.filter(title='ZZ Criado opcional').first()
        t('desmarcado: nasce opcional', criado_opc is not None and not criado_opc.obrigatorio)
        t('o WhatsApp é avisado de que é opcional',
          zap.called and zap.call_args.kwargs.get('obrigatorio') is False, zap.call_args)
        t('nenhuma mensagem real saiu', not zapi.called)

    dest = fresco(dest)
    pend = comunicados_pendentes(dest)
    t('o criado obrigatório trava o destinatário', pend.filter(id=criado_obr.id).exists())
    t('o criado opcional não trava', not pend.filter(id=criado_opc.id).exists())

    print('\n== A EDIÇÃO ==')
    html = cr.get(f'/communications/{criado_obr.id}/edit/').content.decode()
    t('o checkbox vem marcado no obrigatório', bool(re.search(r'name="obrigatorio"\s*checked', html)))
    html = cr.get(f'/communications/{criado_opc.id}/edit/').content.decode()
    t('e desmarcado no opcional', 'name="obrigatorio"' in html
      and not re.search(r'name="obrigatorio"\s*checked', html))

    cr.post(f'/communications/{criado_obr.id}/edit/', {
        'title': criado_obr.title, 'message': criado_obr.message, 'recipients': [dest.id]})
    criado_obr.refresh_from_db()
    t('desmarcar na edição tira a obrigatoriedade', not criado_obr.obrigatorio)
    t('e libera na hora quem estava travado por ele', comunicados_todos_cientes(fresco(dest)))

    cr.post(f'/communications/{criado_opc.id}/edit/', {
        'title': criado_opc.title, 'message': criado_opc.message, 'recipients': [dest.id],
        'obrigatorio': 'on'})
    criado_opc.refresh_from_db()
    t('marcar na edição torna obrigatório', criado_opc.obrigatorio)
    t('e passa a travar', not comunicados_todos_cientes(fresco(dest)))

    print('\n== QUEM LÊ SABE SE É OBRIGATÓRIO ==')
    cd = Client(); cd.force_login(dest)
    html = cd.get(f'/communications/{criado_opc.id}/').content.decode()
    t('o detalhe avisa quando é obrigatório', 'é <strong>obrigatório</strong>' in html)
    html = cd.get(f'/communications/{criado_obr.id}/').content.decode()
    t('e quando o de acordo é opcional', 'é opcional neste comunicado' in html)

    print('\n== O WHATSAPP ==')
    msg_obr = whatsapp._mensagem('Ana', 'Aviso', 'https://x', obrigatorio=True)
    msg_opc = whatsapp._mensagem('Ana', 'Aviso', 'https://x', obrigatorio=False)
    t('o obrigatório pede o de acordo', 'de acordo' in msg_obr)
    t('o opcional não pede', 'de acordo' not in msg_opc)
    t('os dois levam o link', 'https://x' in msg_obr and 'https://x' in msg_opc)
    t('sem dizer nada, continua pedindo (compatível)', 'de acordo' in whatsapp._mensagem('Ana', 'A', 'l'))

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
