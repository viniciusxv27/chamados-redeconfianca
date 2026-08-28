"""Módulo de Reuniões: convite por cargo/coordenação/setor/grupo, sala e ata.

Só apaga o que este arquivo cria.
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
from django.test import Client
from django.utils import timezone

from communications.models import CommunicationGroup
from reunioes import publico
from reunioes.models import ConfiguracaoReunioes, ParticipanteReuniao, Reuniao
from simulator.models import CoordinatorStoreAccess
from users.models import Sector

User = get_user_model()
ok = fail = 0
criados = {'users': [], 'reunioes': [], 'grupo': None, 'setores': [], 'coord': None}


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def novo(username, **kw):
    u = User.objects.create_user(username=username, email=f'{username}@exemplo-teste.local',
                                 password='S3nha!teste', **kw)
    criados['users'].append(u)
    return u


try:
    print('== CENÁRIO ==')
    chefe = novo('rn.chefe.t', first_name='Chefe', last_name='Reuniao',
                 job_title='DIRETOR', hierarchy='SUPERADMIN')
    ger1 = novo('rn.ger1.t', first_name='Gerente', last_name='Um', job_title='GERENTE DE VENDAS')
    ger2 = novo('rn.ger2.t', first_name='Gerente', last_name='Dois', job_title='GERENTE DE VENDAS')
    vend = novo('rn.vend.t', first_name='Vendedor', last_name='Tres', job_title='CONSULTOR ZZ')
    coord = novo('rn.coord.t', first_name='Coord', last_name='Quatro', job_title='COORDENADOR ZZ')

    loja_a = Sector.objects.create(name='ZZ Loja Reuniao A')
    loja_b = Sector.objects.create(name='ZZ Loja Reuniao B')
    criados['setores'] += [loja_a, loja_b]
    ger1.sector = loja_a
    ger1.save(update_fields=['sector'])
    vend.sector = loja_b
    vend.save(update_fields=['sector'])

    grupo = CommunicationGroup.objects.create(name='ZZ Grupo Reuniao', created_by=chefe)
    criados['grupo'] = grupo
    grupo.members.add(ger2, vend)

    acesso = CoordinatorStoreAccess.objects.create(coordinator=coord)
    acesso.sectors.set([loja_a, loja_b])
    criados['coord'] = acesso

    print('\n== DE ONDE SAEM OS CONVIDADOS ==')
    catalogo = publico.tudo(chefe)
    cargos = {c['nome']: c for c in catalogo['cargos']}
    t('lista por cargo existe', 'GERENTE DE VENDAS' in cargos)
    t('cargo junta as duas pessoas',
      {ger1.id, ger2.id} <= set(cargos.get('GERENTE DE VENDAS', {}).get('membros', [])))
    t('quem cria não entra na própria lista',
      all(chefe.id not in c['membros'] for c in catalogo['cargos']))

    setores = {s['nome']: s for s in catalogo['setores']}
    t('lista por setor existe', 'ZZ Loja Reuniao A' in setores)
    t('setor traz quem está nele', ger1.id in setores['ZZ Loja Reuniao A']['membros'])

    grupos = {g['nome']: g for g in catalogo['grupos']}
    t('lista por grupo existe', 'ZZ Grupo Reuniao' in grupos)
    t('grupo traz os membros', {ger2.id, vend.id} <= set(grupos['ZZ Grupo Reuniao']['membros']))

    coords = [c for c in catalogo['coordenacoes'] if 'Coord Quatro' in c['nome']]
    t('lista por coordenação existe', bool(coords))
    if coords:
        membros = set(coords[0]['membros'])
        t('coordenação traz as lojas dela', {ger1.id, vend.id} <= membros)
        t('coordenação traz o próprio coordenador', coord.id in membros)

    print('\n== CRIAR REUNIÃO ==')
    c = Client()
    c.force_login(chefe)
    inicio = timezone.localtime(timezone.now()) + timedelta(hours=2)

    r = c.post('/reunioes/nova/', {
        'titulo': 'ZZ Alinhamento de teste',
        'pauta': 'Falar sobre a meta do mês.',
        'inicio': inicio.strftime('%Y-%m-%dT%H:%M'),
        'fim': (inicio + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
        'cargos': ['GERENTE DE VENDAS'],
        'grupos': [str(grupo.id)],
        'usuarios': [str(coord.id)],
        'gravar_ata': 'on',
    }, follow=True)
    reuniao = Reuniao.objects.filter(titulo='ZZ Alinhamento de teste').first()
    if reuniao:
        criados['reunioes'].append(reuniao)
    t('reunião criada', reuniao is not None, r.status_code)
    t('guarda a pauta', reuniao and reuniao.pauta == 'Falar sobre a meta do mês.')
    t('sala tem nome sorteado (não adivinhável)',
      reuniao and reuniao.sala.startswith('rc-') and len(reuniao.sala) > 12, reuniao.sala if reuniao else '')

    convidados = set(reuniao.participantes.values_list('user_id', flat=True))
    t('cargo convidou os dois gerentes', {ger1.id, ger2.id} <= convidados)
    t('grupo convidou o vendedor', vend.id in convidados)
    t('pessoa escolhida na mão entrou', coord.id in convidados)
    t('organizador não vira participante', chefe.id not in convidados)

    p1 = reuniao.participantes.get(user=ger1)
    t('registra por que a pessoa foi chamada',
      p1.origem == ParticipanteReuniao.CARGO and 'GERENTE' in p1.rotulo_origem,
      f'{p1.origem}/{p1.rotulo_origem}')

    t('espelhou na agenda do portal', reuniao.evento_id is not None)
    if reuniao.evento_id:
        t('evento da agenda leva para a sala',
          '/reunioes/' in (reuniao.evento.link or ''), reuniao.evento.link)

    print('\n== AVISO PARA OS CONVIDADOS ==')
    try:
        from core.models import Notification
        avisos = Notification.objects.filter(user=ger1, title='Convite para reunião')
        t('convidado recebeu notificação', avisos.exists())
    except Exception as exc:                                    # noqa: BLE001
        t('convidado recebeu notificação', False, exc)

    print('\n== LEMBRETE NA HOME ==')
    cg = Client()
    cg.force_login(ger1)
    html = cg.get('/').content.decode()
    t('home mostra o lembrete', 'ZZ Alinhamento de teste' in html)
    t('lembrete traz o horário', inicio.strftime('%H:%M') in html)

    cv = Client()
    cv.force_login(novo('rn.fora.t', first_name='Fora', last_name='Reuniao'))
    t('quem não foi convidado não vê lembrete',
      'ZZ Alinhamento de teste' not in cv.get('/').content.decode())

    print('\n== SALA ==')
    r = cg.get(f'/reunioes/{reuniao.id}/sala/')
    t('convidado entra na sala', r.status_code == 200, r.status_code)
    html = r.content.decode()
    t('sala usa o servidor configurado', ConfiguracaoReunioes.get().servidor_jitsi in html)
    t('sala não tem limite de tempo declarado', 'sem limite de tempo' in html)
    t('sala oferece gravar a ata', 'Gravar ata' in html)

    reuniao.refresh_from_db()
    t('entrar marca presença',
      reuniao.participantes.get(user=ger1).entrou_em is not None)
    t('primeira entrada abre a reunião', reuniao.status == Reuniao.EM_ANDAMENTO, reuniao.status)

    r = cv.get(f'/reunioes/{reuniao.id}/sala/', follow=True)
    t('estranho não entra na sala', 'não está nesta reunião' in r.content.decode())
    r = cv.get(f'/reunioes/{reuniao.id}/', follow=True)
    t('estranho não vê o detalhe', 'não está nesta reunião' in r.content.decode())

    print('\n== EDIÇÃO E PERMISSÃO ==')
    r = cg.post(f'/reunioes/{reuniao.id}/editar/', {
        'titulo': 'ZZ Invasor', 'inicio': inicio.strftime('%Y-%m-%dT%H:%M')}, follow=True)
    reuniao.refresh_from_db()
    t('convidado não edita a reunião', reuniao.titulo == 'ZZ Alinhamento de teste')

    r = c.post(f'/reunioes/{reuniao.id}/editar/', {
        'titulo': 'ZZ Alinhamento de teste (v2)',
        'pauta': 'Nova pauta.',
        'inicio': inicio.strftime('%Y-%m-%dT%H:%M'),
        'cargos': ['GERENTE DE VENDAS'],
    }, follow=True)
    reuniao.refresh_from_db()
    t('organizador edita', reuniao.titulo == 'ZZ Alinhamento de teste (v2)')
    restantes = set(reuniao.participantes.values_list('user_id', flat=True))
    t('quem saiu da seleção sai da reunião', vend.id not in restantes, restantes)
    t('quem já entrou na sala continua na lista', ger1.id in restantes)

    print('\n== HORÁRIO INVÁLIDO ==')
    r = c.post('/reunioes/nova/', {
        'titulo': 'ZZ Invertida', 'inicio': inicio.strftime('%Y-%m-%dT%H:%M'),
        'fim': (inicio - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')}, follow=True)
    t('recusa fim antes do início', 'fim previsto tem que ser depois' in r.content.decode())
    t('não criou a reunião inválida', not Reuniao.objects.filter(titulo='ZZ Invertida').exists())

    r = c.post('/reunioes/nova/', {'titulo': '', 'inicio': ''}, follow=True)
    t('cobra tema e horário', 'obrigatórios' in r.content.decode())

    print('\n== ATA ==')
    from agenda.models import MeetingTranscription

    ata = MeetingTranscription.objects.create(owner=chefe, title='ZZ Ata de teste',
                                              status='processing')
    r = c.post(f'/reunioes/{reuniao.id}/ata/', {'transcricao': ata.id})
    dados = r.json()
    t('ata vinculada à reunião', dados.get('ok') is True, dados)
    ata.refresh_from_db()
    t('ata aponta para o evento da reunião', ata.event_id == reuniao.evento_id)
    compartilhada = set(ata.shared_with.values_list('id', flat=True))
    t('ata compartilhada com quem estava na agenda', ger1.id in compartilhada, compartilhada)
    reuniao.refresh_from_db()
    t('reunião encerra ao gerar a ata', reuniao.status == Reuniao.ENCERRADA)

    ata_alheia = MeetingTranscription.objects.create(owner=ger2, title='ZZ Ata alheia',
                                                    status='processing')
    r = cg.post(f'/reunioes/{reuniao.id}/ata/', {'transcricao': ata_alheia.id})
    t('não dá para pendurar ata de outra pessoa', r.status_code == 403, r.status_code)

    r = cv.post(f'/reunioes/{reuniao.id}/ata/', {'transcricao': ata.id})
    t('estranho não registra ata', r.status_code == 403, r.status_code)

    html = c.get(f'/reunioes/{reuniao.id}/').content.decode()
    t('ata aparece no detalhe da reunião', 'ZZ Ata de teste' in html)

    print('\n== CANCELAR ==')
    r = cg.post(f'/reunioes/{reuniao.id}/cancelar/', follow=True)
    reuniao.refresh_from_db()
    t('convidado não cancela', reuniao.status != Reuniao.CANCELADA)

    r = c.post(f'/reunioes/{reuniao.id}/cancelar/', follow=True)
    reuniao.refresh_from_db()
    t('organizador cancela', reuniao.status == Reuniao.CANCELADA)
    t('sala fecha depois de cancelada',
      'foi cancelada' in c.get(f'/reunioes/{reuniao.id}/sala/', follow=True).content.decode())
    try:
        from core.models import Notification
        t('cancelamento avisa os convidados',
          Notification.objects.filter(user=ger1, title='Reunião cancelada').exists())
    except Exception as exc:                                    # noqa: BLE001
        t('cancelamento avisa os convidados', False, exc)

    print('\n== CONFIGURAÇÃO ==')
    r = cg.get('/reunioes/configuracao/', follow=True)
    t('só SUPERADMIN configura', 'Só o SUPERADMIN' in r.content.decode())
    cfg_antes = ConfiguracaoReunioes.get().servidor_jitsi
    r = c.post('/reunioes/configuracao/', {'servidor_jitsi': 'https://meet.exemplo.local/',
                                           'gerar_ata': 'on'}, follow=True)
    cfg = ConfiguracaoReunioes.get()
    t('limpa o https:// do servidor', cfg.servidor_jitsi == 'meet.exemplo.local', cfg.servidor_jitsi)
    cfg.servidor_jitsi = cfg_antes
    cfg.save()

    print('\n== HIGIENE DE TEMPLATE ==')
    import glob
    import re
    ruins = []
    for f in glob.glob('templates/reunioes/*.html'):
        txt = open(f, encoding='utf-8', errors='ignore').read()
        for m in re.finditer(r'\{#', txt):
            resto = txt[m.start():]
            fim = resto.find('#}')
            if fim == -1 or '\n' in resto[:fim]:
                ruins.append(f)
                break
    t('nenhum comentário {# #} de várias linhas', not ruins, ruins)

    MeetingTranscription.objects.filter(title__startswith='ZZ ').delete()

finally:
    from agenda.models import CalendarEvent, MeetingTranscription

    MeetingTranscription.objects.filter(title__startswith='ZZ ').delete()
    for r_ in criados['reunioes']:
        obj = Reuniao.objects.filter(id=r_.id).first()
        if obj and obj.evento_id:
            CalendarEvent.objects.filter(id=obj.evento_id).delete()
        Reuniao.objects.filter(id=r_.id).delete()
    Reuniao.objects.filter(titulo__startswith='ZZ ').delete()
    if criados['coord']:
        CoordinatorStoreAccess.objects.filter(id=criados['coord'].id).delete()
    for u in criados['users']:
        Reuniao.objects.filter(organizador=u).delete()
        User.objects.filter(id=u.id).delete()
    if criados['grupo']:
        CommunicationGroup.objects.filter(id=criados['grupo'].id).delete()
    for s in criados['setores']:
        Sector.objects.filter(id=s.id).delete()
    print('\nlimpeza: só o que este teste criou foi removido.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
