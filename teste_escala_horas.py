"""Escala: 4 horários por dia, soma das horas e aviso de menos de 44h/semana.

Só apaga o que este arquivo cria.
"""
import os, sys, django
from datetime import date, time, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.test import Client
from communications.models import CommunicationGroup
from tangerino.models import (HORAS_SEMANAIS, Escala, EscalaConfig, EscalaDia,
                              minutos_do_dia)
from users.models import Sector

User = get_user_model()
ok = fail = 0
criados = {'users': [], 'escalas': [], 'setor': None}


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1; print(f'  OK   {nome}')
    else:
        fail += 1; print(f'  FALHA {nome} {extra}')


try:
    print('== CONTA DE HORAS ==')
    h = lambda x, y: time(x, y)
    t('08:00–12:00 + 13:00–17:00 = 8h',
      minutos_do_dia(h(8, 0), h(12, 0), h(13, 0), h(17, 0)) == 480,
      minutos_do_dia(h(8, 0), h(12, 0), h(13, 0), h(17, 0)))
    t('sem almoço 08:00–17:00 = 9h',
      minutos_do_dia(h(8, 0), None, None, h(17, 0)) == 540)
    t('folga não conta', minutos_do_dia(h(8, 0), None, None, h(17, 0), folga=True) == 0)
    t('só entrada não conta', minutos_do_dia(h(8, 0), None, None, None) == 0)
    t('turno que vira a meia-noite conta certo',
      minutos_do_dia(h(22, 0), None, None, h(6, 0)) == 480,
      minutos_do_dia(h(22, 0), None, None, h(6, 0)))
    t('almoço pela metade cai para entrada→saída',
      minutos_do_dia(h(8, 0), h(12, 0), None, h(17, 0)) == 540)
    t('minutos quebrados: 08:30–12:00 + 13:00–17:20 = 7h50',
      minutos_do_dia(h(8, 30), h(12, 0), h(13, 0), h(17, 20)) == 470,
      minutos_do_dia(h(8, 30), h(12, 0), h(13, 0), h(17, 20)))

    print('\n== GRAVAÇÃO PELA TELA ==')
    setor = Sector.objects.create(name='ZZ Setor Teste Escala')
    criados['setor'] = setor
    gerentes, _ = CommunicationGroup.objects.get_or_create(name='GERENTES')

    gerente = User.objects.create_user(username='esc.gerente.t', email='esc.gerente.t@exemplo-teste.local',
                                       password='S3nha!teste', first_name='Gerente', last_name='Escala',
                                       hierarchy='PADRAO')
    colab = User.objects.create_user(username='esc.colab.t', email='esc.colab.t@exemplo-teste.local',
                                     password='S3nha!teste', first_name='Colab', last_name='Escala',
                                     hierarchy='PADRAO')
    criados['users'] += [gerente, colab]
    gerente.sector = setor; gerente.save(update_fields=['sector'])
    colab.sector = setor; colab.save(update_fields=['sector'])
    gerente.communication_groups.add(gerentes)

    hoje = date.today()
    segunda = hoje - timedelta(days=hoje.weekday())

    c = Client(); c.force_login(gerente)
    r = c.get(f'/ponto/escala/?inicio={segunda.isoformat()}')
    t('tela abre para o gerente', r.status_code == 200, r.status_code)
    html = r.content.decode()
    t('tem os 4 campos do dia',
      all(f'name="{campo}_{colab.id}_{segunda.isoformat()}"' in html
          for campo in ('entrada', 'saida_almoco', 'volta_almoco', 'saida')))
    t('mostra o total ao lado do nome', 'esc-total' in html)

    # 5 dias de 8h48 = 44h exatas
    dados = {'inicio': segunda.isoformat(), 'colaborador': str(colab.id)}
    for i in range(5):
        d = (segunda + timedelta(days=i)).isoformat()
        dados[f'entrada_{colab.id}_{d}'] = '08:00'
        dados[f'saida_almoco_{colab.id}_{d}'] = '12:00'
        dados[f'volta_almoco_{colab.id}_{d}'] = '13:00'
        dados[f'saida_{colab.id}_{d}'] = '17:48'
    r = c.post('/ponto/escala/salvar/', dados, follow=True)
    t('salvou', r.status_code == 200, r.status_code)

    esc = Escala.objects.filter(colaborador=colab, semana_inicio=segunda).first()
    criados['escalas'].append(esc.id if esc else None)
    t('escala criada', esc is not None)
    dia = EscalaDia.objects.filter(escala=esc, data=segunda).first()
    t('gravou a saída para o almoço', dia and dia.saida_almoco == time(12, 0),
      dia.saida_almoco if dia else None)
    t('gravou a volta do almoço', dia and dia.volta_almoco == time(13, 0))
    t('dia dá 8h48', dia and dia.minutos == 528, dia.minutos if dia else None)

    total = sum(d.minutos for d in EscalaDia.objects.filter(escala=esc))
    t('semana fecha 44h', total == HORAS_SEMANAIS * 60, f'{total/60:.2f}h')

    html = c.get(f'/ponto/escala/?inicio={segunda.isoformat()}').content.decode()
    t('44h aparece como cumprida (verde)', '44,0h' in html or '44.0h' in html, )
    t('sem aviso de abaixo da meta', 'abaixo de 44h' not in html)

    # tira 2 horas: tem que avisar
    for i in range(5):
        d = (segunda + timedelta(days=i)).isoformat()
        dados[f'saida_{colab.id}_{d}'] = '17:24'
    c.post('/ponto/escala/salvar/', dados, follow=True)
    total = sum(d.minutos for d in EscalaDia.objects.filter(escala=esc))
    t('semana menor que 44h', total < HORAS_SEMANAIS * 60, f'{total/60:.2f}h')
    html = c.get(f'/ponto/escala/?inicio={segunda.isoformat()}').content.decode()
    t('avisa que ficou abaixo de 44h', 'abaixo das 44h semanais' in html)
    t('conta o quanto ficou', '42,0h' in html or '42.0h' in html)

    # folga zera o dia
    d0 = segunda.isoformat()
    dados[f'folga_{colab.id}_{d0}'] = 'on'
    c.post('/ponto/escala/salvar/', dados, follow=True)
    dia = EscalaDia.objects.filter(escala=esc, data=segunda).first()
    t('folga apaga os horários', dia and dia.folga and dia.entrada is None
      and dia.saida_almoco is None and dia.volta_almoco is None)
    t('folga não soma horas', dia and dia.minutos == 0)

    print('\n== O QUE O COLABORADOR VÊ ==')
    cc = Client(); cc.force_login(colab)
    html = cc.get(f'/ponto/escala/?inicio={segunda.isoformat()}').content.decode()
    t('colaborador vê o total da semana', 'Total da semana' in html)
    t('colaborador vê o aviso', 'abaixo das 44h semanais' in html)
    t('colaborador vê o horário do almoço', 'almoço' in html)

    print('\n== SEGURANÇA ==')
    outro = User.objects.create_user(username='esc.outro.t', email='esc.outro.t@exemplo-teste.local',
                                     password='S3nha!teste', first_name='Outro', hierarchy='PADRAO')
    criados['users'].append(outro)
    r = cc.post('/ponto/escala/salvar/', dados, follow=True)
    t('colaborador comum não salva escala',
      'não tem acesso' in r.content.decode().lower() or r.status_code == 403)
    antes = EscalaDia.objects.filter(escala=esc).count()
    fora = dict(dados); fora['colaborador'] = str(outro.id)
    c.post('/ponto/escala/salvar/', fora, follow=True)
    t('gerente não escala quem não é do setor dele',
      not Escala.objects.filter(colaborador=outro).exists())
    t('escala do colaborador dele ficou intacta',
      EscalaDia.objects.filter(escala=esc).count() == antes)

finally:
    for eid in criados['escalas']:
        if eid:
            EscalaDia.objects.filter(escala_id=eid).delete()
            Escala.objects.filter(id=eid).delete()
    for u in criados['users']:
        Escala.objects.filter(colaborador=u).delete()
        User.objects.filter(id=u.id).delete()
    if criados['setor']:
        Sector.objects.filter(id=criados['setor'].id).delete()
    print('\nlimpeza: só o que este teste criou foi removido.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
