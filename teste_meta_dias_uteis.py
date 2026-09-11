"""Impulso: recorrência "somente dias úteis" — fim de semana anda para a segunda.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
from datetime import date, timedelta

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

from communications.models import CommunicationGroup
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, Meta, proximo_dia_util
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


def proximo(dia_da_semana, a_partir=None):
    """Próxima data (a partir de amanhã) que cai naquele dia da semana (0=seg)."""
    d = (a_partir or timezone.localdate()) + timedelta(days=1)
    while d.weekday() != dia_da_semana:
        d += timedelta(days=1)
    return d


marcador = transaction.atomic()
marcador.__enter__()
try:
    print('== A RÉGUA ==')
    sab = date(2026, 9, 12)          # sábado
    t('sábado vira segunda', proximo_dia_util(sab) == date(2026, 9, 14))
    t('domingo vira segunda', proximo_dia_util(date(2026, 9, 13)) == date(2026, 9, 14))
    t('segunda fica', proximo_dia_util(date(2026, 9, 14)) == date(2026, 9, 14))
    t('sexta fica', proximo_dia_util(date(2026, 9, 11)) == date(2026, 9, 11))

    adm = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
    ges = CommunicationGroup.objects.filter(name__iexact=GRUPO_GESTOR).first()
    area = Sector.objects.create(name='ZZ Area Dias Uteis')

    def novo(u, grupos):
        x = User.objects.create_user(
            username=u, email=f'{u}@exemplo-teste.local', password='S3nha!teste',
            sector=area, first_name=u.split('.')[1].title(), last_name='T')
        for g in grupos:
            x.communication_groups.add(g)
        return x

    gestor = novo('du.gestor', [adm, ges])
    colab = novo('du.colab', [adm])

    print('\n== A PRÓXIMA OCORRÊNCIA RESPEITA O FIM DE SEMANA ==')
    sexta = date(2026, 9, 11)
    m = Meta.objects.create(titulo='ZZ Diária útil', colaborador=colab, gestor=gestor,
                            prazo=sexta, recorrencia=Meta.Recorrencia.DIARIA,
                            apenas_dias_uteis=True, aprovacao=Meta.Aprovacao.APROVADA)
    t('diária na sexta → próxima é SEGUNDA, não sábado',
      m.proximo_prazo() == date(2026, 9, 14), m.proximo_prazo())

    m2 = Meta.objects.create(titulo='ZZ Diária comum', colaborador=colab, gestor=gestor,
                             prazo=sexta, recorrencia=Meta.Recorrencia.DIARIA,
                             apenas_dias_uteis=False, aprovacao=Meta.Aprovacao.APROVADA)
    t('sem a opção, continua caindo no sábado',
      m2.proximo_prazo() == date(2026, 9, 12), m2.proximo_prazo())

    m3 = Meta.objects.create(titulo='ZZ Semanal útil', colaborador=colab, gestor=gestor,
                             prazo=date(2026, 9, 5),        # sábado
                             recorrencia=Meta.Recorrencia.SEMANAL,
                             apenas_dias_uteis=True, aprovacao=Meta.Aprovacao.APROVADA)
    t('semanal que cairia no sábado vai para a segunda',
      m3.proximo_prazo() == date(2026, 9, 14), m3.proximo_prazo())

    m4 = Meta.objects.create(titulo='ZZ Mensal útil', colaborador=colab, gestor=gestor,
                             prazo=date(2026, 10, 3),       # próximo mês: 03/11 é terça
                             recorrencia=Meta.Recorrencia.MENSAL,
                             apenas_dias_uteis=True, aprovacao=Meta.Aprovacao.APROVADA)
    t('mensal em dia útil não mexe', m4.proximo_prazo() == date(2026, 11, 3), m4.proximo_prazo())
    m5 = Meta.objects.create(titulo='ZZ Mensal fds', colaborador=colab, gestor=gestor,
                             prazo=date(2026, 9, 6),        # 06/10 é terça; 06/12 domingo
                             recorrencia=Meta.Recorrencia.MENSAL,
                             apenas_dias_uteis=True, aprovacao=Meta.Aprovacao.APROVADA)
    m5.prazo = date(2026, 11, 6)                            # 06/12/2026 = domingo
    t('mensal que cai no domingo vai para 07/12 (segunda)',
      m5.proximo_prazo() == date(2026, 12, 7), m5.proximo_prazo())

    print('\n== A OCORRÊNCIA GERADA HERDA A OPÇÃO ==')
    m.status = Meta.Status.CONCLUIDA
    m.nota_qualidade = 5
    m.nota_prazo = 5
    m.save()
    prox = m.criar_proxima_ocorrencia()
    t('gera a próxima', prox is not None)
    t('na segunda', prox and prox.prazo == date(2026, 9, 14), prox and prox.prazo)
    t('e continua "somente dias úteis"', prox and prox.apenas_dias_uteis)

    print('\n== A TELA ==')
    c = Client(); c.force_login(gestor)
    html = c.get('/impulso/metas/nova/').content.decode()
    t('o formulário tem a opção', 'name="apenas_dias_uteis"' in html)
    t('escondida até escolher uma recorrência', 'id="impDiasUteis" class="hidden' in html)
    t('e explica o que faz', 'passa para a segunda-feira' in html)

    sabado = proximo(5)
    r = c.post('/impulso/metas/nova/', {
        'titulo': 'ZZ Criada no sábado', 'descricao': 'x', 'colaborador': colab.id,
        'gestor': gestor.id, 'recorrencia': 'DIARIA', 'apenas_dias_uteis': 'on',
        'prazo': sabado.isoformat(),
    }, follow=True)
    criada = Meta.objects.filter(titulo='ZZ Criada no sábado').first()
    t('criou', criada is not None, r.status_code)
    t('guardou a opção', criada and criada.apenas_dias_uteis)
    t('o prazo escolhido no sábado já nasce na segunda',
      criada and criada.prazo == sabado + timedelta(days=2), criada and criada.prazo)
    t('e a tela avisa que o prazo andou', 'caía no fim de semana' in r.content.decode())

    r = c.post('/impulso/metas/nova/', {
        'titulo': 'ZZ Única no sábado', 'descricao': 'x', 'colaborador': colab.id,
        'gestor': gestor.id, 'recorrencia': 'UNICA', 'apenas_dias_uteis': 'on',
        'prazo': sabado.isoformat(),
    }, follow=True)
    unica = Meta.objects.filter(titulo='ZZ Única no sábado').first()
    t('para "única vez" a opção é ignorada', unica and not unica.apenas_dias_uteis)
    t('e o prazo fica no sábado mesmo', unica and unica.prazo == sabado, unica and unica.prazo)

    print('\n== EDITAR E DUPLICAR ==')
    html = c.get(f'/impulso/metas/{criada.id}/editar/').content.decode()
    t('a edição mostra a opção marcada', 'name="apenas_dias_uteis" checked' in html)
    r = c.post(f'/impulso/metas/{criada.id}/editar/', {
        'titulo': criada.titulo, 'descricao': 'x', 'prazo': criada.prazo.isoformat(),
        'recorrencia': 'DIARIA',
    }, follow=True)
    criada.refresh_from_db()
    t('desmarcar na edição desliga', not criada.apenas_dias_uteis)
    r = c.post(f'/impulso/metas/{criada.id}/editar/', {
        'titulo': criada.titulo, 'descricao': 'x', 'prazo': sabado.isoformat(),
        'recorrencia': 'SEMANAL', 'apenas_dias_uteis': 'on',
    }, follow=True)
    criada.refresh_from_db()
    t('marcar na edição liga', criada.apenas_dias_uteis)
    t('e o prazo editado para o sábado também anda',
      criada.prazo == sabado + timedelta(days=2), criada.prazo)

    r = c.post(f'/impulso/metas/{criada.id}/duplicar/', follow=True)
    copia = Meta.objects.filter(titulo__startswith='Cópia de ZZ Criada').first()
    t('a cópia herda a opção', copia is not None and copia.apenas_dias_uteis)

    html = c.get(f'/impulso/metas/{criada.id}/').content.decode()
    t('o detalhe mostra "somente dias úteis"', 'somente dias úteis' in html)
    html = c.get('/impulso/metas/?mes=').content.decode()
    t('o card sinaliza', 'Somente dias úteis' in html)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
