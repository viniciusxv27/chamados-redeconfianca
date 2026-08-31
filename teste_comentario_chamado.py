"""Chamados: quem responde pelo chamado apaga comentário, sem sumir do histórico.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys

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

from tickets.models import Ticket, TicketComment
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


marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Setor Chamado')
    outro_setor = Sector.objects.create(name='ZZ Setor Alheio')

    def novo(username, setor_do_user=None, **kw):
        return User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local',
            password='S3nha!teste', first_name=username.split('.')[1].title(),
            last_name='Teste', sector=setor_do_user, **kw)

    autor = novo('tk.autor', setor)
    responsavel = novo('tk.responsavel', setor)
    colega = novo('tk.colega', setor)
    estranho = novo('tk.estranho', outro_setor)
    chefia = novo('tk.chefia', setor, hierarchy='SUPERVISOR')

    chamado = Ticket.objects.create(
        title='ZZ Chamado de teste', description='Teste de exclusão de comentário',
        created_by=autor, sector=setor, assigned_to=responsavel)

    def comentario(quem, texto, tipo='COMMENT'):
        return TicketComment.objects.create(
            ticket=chamado, user=quem, comment=texto, comment_type=tipo)

    print('== QUEM PODE APAGAR ==')
    c_autor = comentario(autor, 'ZZ Recado do autor')
    t('o autor apaga o que escreveu', c_autor.pode_excluir(autor))
    t('o responsável pelo chamado apaga qualquer recado',
      c_autor.pode_excluir(responsavel))
    t('colega do setor sem papel no chamado não apaga',
      not c_autor.pode_excluir(colega), 'colega apagou')
    t('gente de fora não apaga', not c_autor.pode_excluir(estranho))
    t('a chefia do setor apaga', c_autor.pode_excluir(chefia))
    t('supervisor de OUTRO setor não apaga',
      not c_autor.pode_excluir(novo('tk.chefia2', outro_setor, hierarchy='SUPERVISOR')))

    c_sistema = comentario(responsavel, 'Status alterado', tipo='STATUS_CHANGE')
    t('registro automático do chamado não se apaga',
      not c_sistema.pode_excluir(responsavel) and not c_sistema.pode_excluir(autor))

    print('\n== APAGAR PELA TELA ==')
    ca = Client()
    ca.force_login(autor)
    r = ca.post(f'/tickets/comentario/{c_autor.id}/excluir/', follow=True)
    c_autor.refresh_from_db()
    t('marcou como excluído', c_autor.excluido, r.status_code)
    t('guarda quem apagou', c_autor.excluido_por_id == autor.id)
    t('guarda quando apagou', c_autor.excluido_em is not None)

    print('\n== NÃO SOME DO HISTÓRICO ==')
    t('o registro continua no banco',
      TicketComment.objects.filter(id=c_autor.id).exists())
    t('o texto original continua guardado',
      TicketComment.objects.get(id=c_autor.id).comment == 'ZZ Recado do autor')
    t('continua contando na conversa do chamado',
      chamado.comments.filter(id=c_autor.id).exists())

    html = ca.get(f'/tickets/{chamado.id}/').content.decode()
    t('a tela mostra que foi excluído', 'Comentário excluído por' in html)
    t('a tela diz quem apagou', autor.full_name in html)
    t('o autor consegue reler o texto original', 'ZZ Recado do autor' in html)

    cc = Client()
    cc.force_login(colega)
    html_colega = cc.get(f'/tickets/{chamado.id}/').content.decode()
    t('para quem não responde pelo chamado, o texto some',
      'ZZ Recado do autor' not in html_colega)
    t('mas o registro da exclusão aparece para todos',
      'Comentário excluído por' in html_colega)

    cr = Client()
    cr.force_login(responsavel)
    t('o responsável relê o original',
      'ZZ Recado do autor' in cr.get(f'/tickets/{chamado.id}/').content.decode())

    print('\n== SEGURANÇA ==')
    c_outro = comentario(responsavel, 'ZZ Recado do responsavel')
    r = cc.post(f'/tickets/comentario/{c_outro.id}/excluir/', follow=True)
    c_outro.refresh_from_db()
    t('colega não apaga comentário alheio pela tela', not c_outro.excluido)
    t('a tela explica a recusa', 'não pode excluir' in r.content.decode().lower())

    ce = Client()
    ce.force_login(estranho)
    r = ce.post(f'/tickets/comentario/{c_outro.id}/excluir/', follow=True)
    c_outro.refresh_from_db()
    t('gente de outro setor não apaga', not c_outro.excluido)

    r = Client().post(f'/tickets/comentario/{c_outro.id}/excluir/')
    c_outro.refresh_from_db()
    t('anônimo não apaga', not c_outro.excluido)

    r = ca.get(f'/tickets/comentario/{c_outro.id}/excluir/')
    t('GET não apaga (405)', r.status_code == 405, r.status_code)

    r = ca.post('/tickets/comentario/99999999/excluir/')
    t('id inexistente devolve 404', r.status_code == 404, r.status_code)

    r = ca.post(f'/tickets/comentario/{c_autor.id}/excluir/', follow=True)
    t('apagar duas vezes não muda o registro',
      TicketComment.objects.get(id=c_autor.id).excluido_por_id == autor.id)

    print('\n== BOTÃO APARECE SÓ PARA QUEM PODE ==')
    c_novo = comentario(autor, 'ZZ Outro recado do autor')
    alvo = f'/tickets/comentario/{c_novo.id}/excluir/'
    t('autor vê a lixeira', alvo in ca.get(f'/tickets/{chamado.id}/').content.decode())
    t('responsável vê a lixeira', alvo in cr.get(f'/tickets/{chamado.id}/').content.decode())
    t('colega não vê a lixeira',
      alvo not in cc.get(f'/tickets/{chamado.id}/').content.decode())
    t('comentário já apagado não oferece lixeira',
      f'/tickets/comentario/{c_autor.id}/excluir/'
      not in ca.get(f'/tickets/{chamado.id}/').content.decode())

    print('\n== MOTIVO ==')
    c_motivo = comentario(autor, 'ZZ Recado com motivo')
    ca.post(f'/tickets/comentario/{c_motivo.id}/excluir/',
            {'motivo': 'Mandei no chamado errado'}, follow=True)
    c_motivo.refresh_from_db()
    t('guarda o motivo', c_motivo.motivo_exclusao == 'Mandei no chamado errado')
    t('o motivo aparece no histórico',
      'Mandei no chamado errado' in ca.get(f'/tickets/{chamado.id}/').content.decode())

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
