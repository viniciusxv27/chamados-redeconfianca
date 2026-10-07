"""/impulso/metas/: quem foi chamado para fazer junto vê o convite no Kanban.

Pedido (07/10/2026): "deve aparecer para o usuário convidado as tarefas que
outros usuários chamaram ele para participar". O convite (ParceriaMeta) só
chegava pelo sino — dos 9 convites reais, 7 seguiam pendentes, vários de
tarefas já entregues. Agora o Kanban tem o painel "Convites para fazer junto",
com aceitar/recusar ali mesmo, e o card da tarefa de um colega diz de quem é.

Tudo numa transação desfeita; os avisos do Impulso só gravam no sino.
"""
import os
import sys
from datetime import timedelta

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ck'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ck2'},
}
django.setup()

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from communications.models import CommunicationGroup
from core.models import Notification
from impulso.models import Meta, ParceriaMeta
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


KANBAN = '/impulso/metas/'

marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Setor Convites Kanban')
    chefe = User.objects.create_user(
        username='zzck.chefe', email='zzck.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True, sector=setor)
    adm, _ = CommunicationGroup.objects.get_or_create(name='ESCRITÓRIO (ADM)', defaults={'created_by': chefe})

    def pessoa(nome):
        u = User.objects.create_user(
            username=f'zzck.{nome}', email=f'zzck.{nome}@exemplo-teste.local', password='S3nha!teste',
            first_name=nome.capitalize(), last_name='Convite', hierarchy='PADRAO', sector=setor)
        u.communication_groups.add(adm)
        return u

    joao, vini = pessoa('joao'), pessoa('vini')
    hoje = timezone.localdate()
    # Prazo fora do mês corrente: o painel não pode depender do filtro de mês.
    longe = hoje + timedelta(days=70)

    def meta(titulo, prazo=longe, aprovacao=Meta.Aprovacao.APROVADA):
        return Meta.objects.create(gestor=chefe, colaborador=joao, titulo=f'ZZCK {titulo}', descricao='ZZ',
                                   prazo=prazo, aprovacao=aprovacao, created_by=joao)

    def convite(m, mensagem=''):
        return ParceriaMeta.objects.create(meta=m, convidado=vini, convidado_por=joao, mensagem=mensagem)

    m1 = meta('Categorização do cliente')
    c1 = convite(m1, mensagem='ZZCK preciso da sua ajuda no cadastro')
    m2 = meta('Pedido recusado pelo gestor', aprovacao=Meta.Aprovacao.RECUSADA)
    convite(m2)
    m3 = meta('Email interno da rede', prazo=hoje)
    c3 = convite(m3)
    m4 = meta('Já respondida')
    ParceriaMeta.objects.create(meta=m4, convidado=vini, convidado_por=joao, status=ParceriaMeta.Status.RECUSADA)

    cv = Client()
    cv.force_login(vini)

    print('== O CONVIDADO VÊ OS CONVITES NO KANBAN ==')
    html = cv.get(KANBAN).content.decode()
    t('aparece o painel de convites', 'Convites para fazer junto' in html)
    t('com a tarefa do colega, mesmo com prazo fora do mês aberto', 'ZZCK Categorização do cliente' in html)
    t('e a do mês também', 'ZZCK Email interno da rede' in html)
    t('diz quem chamou e o recado', 'Joao Convite chamou você' in html and 'ZZCK preciso da sua ajuda' in html)
    t('aceitar e recusar ali mesmo',
      f'/impulso/metas/parceria/{c1.id}/responder/' in html and 'value="aceitar"' in html and 'value="recusar"' in html)
    t('pedido recusado pelo gestor não vira convite', 'ZZCK Pedido recusado pelo gestor' not in html)
    t('convite já respondido sai do painel', 'ZZCK Já respondida' not in html)

    print('\n== ACEITAR PELO KANBAN ==')
    volta = f'{KANBAN}?mes={longe:%Y-%m}'
    r = cv.post(f'/impulso/metas/parceria/{c1.id}/responder/', {'resposta': 'aceitar', 'next': volta})
    c1.refresh_from_db()
    t('aceitar volta para o Kanban', r.status_code == 302 and r['Location'] == volta, r.get('Location'))
    t('o convite fica aceito', c1.status == ParceriaMeta.Status.ACEITA)
    t('e a pessoa entra na tarefa', m1.participantes.filter(id=vini.id).exists())
    t('quem chamou é avisado', Notification.objects.filter(user=joao, title__icontains='parceria').exists())
    html = cv.get(volta).content.decode()
    t('a tarefa aparece no Kanban dela, dizendo de quem é',
      'ZZCK Categorização do cliente' in html and 'Em parceria com Joao' in html)
    t('e sai do painel de convites', f'/impulso/metas/parceria/{c1.id}/responder/' not in html)

    print('\n== RECUSAR, E O NEXT SÓ VALE PARA DENTRO DO PORTAL ==')
    r = cv.post(f'/impulso/metas/parceria/{c3.id}/responder/',
                {'resposta': 'recusar', 'next': 'https://exemplo-malicioso.test/impulso/metas/'})
    c3.refresh_from_db()
    t('recusa', c3.status == ParceriaMeta.Status.RECUSADA)
    t('next de fora do portal é ignorado (vai para a meta)',
      r.status_code == 302 and r['Location'] == f'/impulso/metas/{m3.id}/', r.get('Location'))
    html = cv.get(KANBAN).content.decode()
    t('sem convite pendente, o painel some', 'Convites para fazer junto' not in html)

    print('\n== O DONO NÃO VÊ "EM PARCERIA" NA PRÓPRIA TAREFA ==')
    cj = Client()
    cj.force_login(joao)
    html = cj.get(volta).content.decode()
    t('o card do dono não tem o selo', 'ZZCK Categorização do cliente' in html and 'Em parceria com' not in html)
    t('e o dono não vê o painel de convites (não foi convidado)', 'Convites para fazer junto' not in html)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)

print(f'\n{ok} OK / {fail} falhas — rollback: nada deste teste ficou no banco.')
sys.exit(1 if fail else 0)
