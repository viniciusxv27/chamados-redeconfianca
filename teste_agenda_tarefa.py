"""Tarefa de verdade na agenda, tarefas do dia e edição por hierarquia.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import json
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

from agenda.models import CalendarEvent
from agenda.tarefas import rotulo_repeticao
from core.models import TaskActivity
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
    area = Sector.objects.create(name='ZZ Area Agenda')

    def novo(username, **kw):
        return User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local',
            password='S3nha!teste', sector=area,
            first_name=username.split('.')[1].title(), last_name='Teste', **kw)

    dono = novo('ag.dono', hierarchy='PADRAO')
    convidado = novo('ag.convidado', hierarchy='PADRAO')

    hoje = timezone.localdate()
    # uma segunda-feira futura, para o rótulo ser previsível
    segunda = hoje + timedelta(days=(7 - hoje.weekday()) % 7 or 7)
    inicio = timezone.make_aware(timezone.datetime.combine(segunda, timezone.datetime.min.time())
                                 .replace(hour=9))
    fim = inicio + timedelta(hours=1)

    c = Client()
    c.force_login(dono)

    def criar(tipo, titulo, participantes=(), repetir=False, ini=None, f=None):
        corpo = {
            'title': titulo, 'description': 'combinado na agenda',
            'event_type': tipo,
            'start': (ini or inicio).isoformat(), 'end': (f or fim).isoformat(),
            'participants': [p.id for p in participantes],
        }
        if repetir:
            corpo['recurrence'] = 'weekly'
            corpo['recurrence_until'] = (segunda + timedelta(days=21)).isoformat()
        r = c.post('/agenda/api/events/create/', data=json.dumps(corpo),
                   content_type='application/json')
        return r

    print('== TAREFA NA AGENDA VIRA TAREFA DE VERDADE ==')
    r = criar('task', 'ZZ tarefa da agenda')
    t('o evento é criado', r.status_code == 201, (r.status_code, r.content[:120]))
    dados = r.json()
    ev = CalendarEvent.objects.get(pk=dados['id'])
    t('e ganha uma tarefa vinculada', ev.tarefa_id is not None)
    t('a API devolve o id da tarefa', dados.get('tarefa_id') == ev.tarefa_id)

    tarefa = ev.tarefa
    t('a tarefa fica com quem marcou', tarefa.assigned_to_id == dono.id)
    t('leva o título', tarefa.title == 'ZZ tarefa da agenda')
    t('leva a descrição', tarefa.description == 'combinado na agenda')
    t('o prazo é o início do evento', tarefa.due_date == ev.start, (tarefa.due_date, ev.start))
    t('nasce pendente', tarefa.status == 'PENDING')
    t('dá para voltar do evento para a tarefa e vice-versa',
      tarefa.evento_agenda.pk == ev.pk)

    print('\n== SÓ TAREFA VIRA TAREFA ==')
    r = criar('event', 'ZZ compromisso comum')
    ev2 = CalendarEvent.objects.get(pk=r.json()['id'])
    t('evento comum não cria tarefa', ev2.tarefa_id is None)
    r = criar('reminder', 'ZZ lembrete')
    ev3 = CalendarEvent.objects.get(pk=r.json()['id'])
    t('lembrete também não', ev3.tarefa_id is None)

    print('\n== CONVIDADO GANHA A PRÓPRIA TAREFA ==')
    r = criar('task', 'ZZ tarefa com convidado', participantes=[convidado])
    ev4 = CalendarEvent.objects.get(pk=r.json()['id'])
    minhas = TaskActivity.objects.filter(title='ZZ tarefa com convidado')
    t('duas tarefas: uma para cada', minhas.count() == 2, minhas.count())
    t('o convidado tem a dele',
      minhas.filter(assigned_to=convidado).exists())
    t('cada uma com status próprio',
      minhas.filter(assigned_to=dono).exists() and minhas.filter(assigned_to=convidado).exists())

    print('\n== REMARCAR NÃO DUPLICA ==')
    antes = TaskActivity.objects.filter(title='ZZ tarefa da agenda').count()
    novo_inicio = inicio + timedelta(days=1)
    c.post(f'/agenda/api/events/{ev.pk}/update/',
           data=json.dumps({'start': novo_inicio.isoformat(),
                            'end': (novo_inicio + timedelta(hours=1)).isoformat()}),
           content_type='application/json')
    ev.refresh_from_db(); tarefa.refresh_from_db()
    t('continua uma tarefa só',
      TaskActivity.objects.filter(title='ZZ tarefa da agenda').count() == antes)
    t('e o prazo acompanhou a remarcação', tarefa.due_date == ev.start,
      (tarefa.due_date, ev.start))

    print('\n== REPETIR TODA SEGUNDA ==')
    r = criar('task', 'ZZ tarefa semanal', repetir=True)
    pai = CalendarEvent.objects.get(pk=r.json()['id'])
    filhos = CalendarEvent.objects.filter(recurrence_parent=pai)
    t('a série gera ocorrências', filhos.count() >= 2, filhos.count())
    t('cada ocorrência tem a própria tarefa',
      all(f.tarefa_id is not None for f in filhos),
      [f.tarefa_id for f in filhos])
    t('uma tarefa por semana, em datas diferentes',
      len({f.tarefa.due_date.date() for f in filhos}) == filhos.count())
    t('todas caem na mesma segunda-feira',
      all(f.tarefa.due_date.weekday() == pai.start.weekday() for f in filhos))

    t('o rótulo nomeia o dia', rotulo_repeticao(segunda) == 'Repetir toda segunda',
      rotulo_repeticao(segunda))
    import datetime as _dt
    t('sábado é "todo"', rotulo_repeticao(_dt.date(2026, 9, 5)) == 'Repetir todo sábado',
      rotulo_repeticao(_dt.date(2026, 9, 5)))
    t('sem data, volta ao genérico', rotulo_repeticao(None) == 'Repetir toda semana')

    with open('templates/agenda/calendar.html', encoding='utf-8') as f:
        cal = f.read()
    t('a tela atualiza o rótulo ao mudar a data',
      'atualizarRotuloRepetir' in cal and 'onchange="atualizarRotuloRepetir()"' in cal)

    print('\n== /users/tasks/ MOSTRA O DIA ==')
    ct = Client(); ct.force_login(dono)
    html = ct.get('/users/tasks/').content.decode()
    t('a tela abre em hoje', 'O que você tem para hoje' in html)
    t('tem navegação de dia', 'dia=' in html and 'Dia anterior' in html)

    alvo = ev.start.date()
    html = ct.get(f'/users/tasks/?dia={alvo.isoformat()}').content.decode()
    t('o dia do evento mostra a tarefa dele', 'ZZ tarefa da agenda' in html)
    t('e diz a data', alvo.strftime('%d/%m/%Y') in html)

    outro_dia = (alvo + timedelta(days=30)).isoformat()
    html = ct.get(f'/users/tasks/?dia={outro_dia}').content.decode()
    t('outro dia não mostra a tarefa', 'ZZ tarefa da agenda' not in html)
    t('e oferece voltar para hoje', '>Hoje<' in html)

    sem = TaskActivity.objects.create(
        title='ZZ sem prazo', description='x', assigned_to=dono, created_by=dono)
    html = ct.get('/users/tasks/').content.decode()
    t('tarefa sem prazo aparece em hoje', 'ZZ sem prazo' in html)
    html = ct.get(f'/users/tasks/?dia={outro_dia}').content.decode()
    t('mas não se repete nos outros dias', 'ZZ sem prazo' not in html)

    print('\n== ADMINISTRAÇÃO EDITA, MENOS QUEM ESTÁ ACIMA ==')
    adm = novo('ag.adm', hierarchy='ADMIN')
    padrao = novo('ag.padrao2', hierarchy='PADRAO')
    supervisor = novo('ag.sup', hierarchy='SUPERVISOR')
    outro_adm = novo('ag.adm2', hierarchy='ADMIN')
    chefe = novo('ag.chefe', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)

    t('ADMINISTRAÇÃO abre a edição', adm.can_edit_users())
    t('edita um PADRÃO', adm.pode_editar_usuario(padrao))
    t('edita um SUPERVISOR (abaixo dela)', adm.pode_editar_usuario(supervisor))
    t('edita outro da ADMINISTRAÇÃO (mesmo nível)', adm.pode_editar_usuario(outro_adm))
    t('NÃO edita um SUPERADMIN', not adm.pode_editar_usuario(chefe))
    t('edita o próprio cadastro', adm.pode_editar_usuario(adm))
    t('SUPERADMIN edita qualquer um', chefe.pode_editar_usuario(adm)
      and chefe.pode_editar_usuario(chefe))
    t('PADRÃO continua sem editar ninguém',
      not padrao.can_edit_users() and not padrao.pode_editar_usuario(padrao))
    t('SUPERVISOR também não', not supervisor.can_edit_users())

    ca = Client(); ca.force_login(adm)
    r = ca.get(f'/users/manage/users/{padrao.id}/edit/', follow=True)
    t('a tela de edição do PADRÃO abre', not r.redirect_chain, r.redirect_chain)
    r = ca.get(f'/users/manage/users/{chefe.id}/edit/', follow=True)
    t('a do SUPERADMIN é recusada', bool(r.redirect_chain), r.redirect_chain)
    t('e explica por quê', 'hierarquia acima da sua' in r.content.decode())

    r = ca.post(f'/users/manage/users/{chefe.id}/change-password/',
                {'new_password': 'NovaSenha123', 'confirm_password': 'NovaSenha123'},
                follow=True)
    chefe.refresh_from_db()
    t('nem troca a senha do SUPERADMIN', not chefe.check_password('NovaSenha123'))

    html = ca.get('/users/manage/users/').content.decode()
    t('a lista marca quem não pode ser editado', 'Hierarquia acima da sua' in html)

    print('\n== A BUSCA DA LISTA NÃO PODE MORRER ==')
    # O listener do formulário de senha rodava solto no topo do script, e o
    # formulário só existe para quem pode editar. Sem elemento, TypeError — e
    # tudo o que vinha depois morria junto, inclusive a busca e os filtros.
    for pessoa, rotulo in ((adm, 'ADMINISTRAÇÃO'), (supervisor, 'SUPERVISOR'),
                           (chefe, 'SUPERADMIN')):
        cli = Client(); cli.force_login(pessoa)
        pagina = cli.get('/users/manage/users/').content.decode()
        t(f'{rotulo}: a busca está na tela', 'id="searchUsers"' in pagina)
        t(f'{rotulo}: nenhum listener solto derruba o script',
          "document.getElementById('changePasswordForm').addEventListener" not in pagina)
        t(f'{rotulo}: as linhas têm o texto para buscar', 'data-search=' in pagina)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
