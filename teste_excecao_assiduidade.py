"""Dia de exceção na assiduidade e sincronização automática do ponto.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
from datetime import date, datetime, time, timedelta

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
from impulso.assiduidade_ponto import LIMITE_AJUSTES_MES, avaliar
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, ExcecaoAssiduidade
from tangerino.models import ConfiguracaoTangerino
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


class M:
    """Marcação fake, só com o que o avaliar() lê."""
    def __init__(self, dia, batidas=4, editado=False, previsto=28800):
        self.data = dia
        self.editado = editado
        self.previsto_segundos = previsto
        pares = [None] * 6
        for i in range(min(batidas, 6)):
            pares[i] = time(8 + i, 0)
        (self.entrada1, self.saida1, self.entrada2,
         self.saida2, self.entrada3, self.saida3) = pares


marcador = transaction.atomic()
marcador.__enter__()
try:
    agora = timezone.localtime()
    base = (agora.date() - timedelta(days=20)).replace(day=1) + timedelta(days=1)
    dias = [base + timedelta(days=i) for i in range(6)]

    print('== A EXCEÇÃO TIRA O AJUSTE DA CONTA ==')
    marcacoes = [M(d, batidas=4, editado=True) for d in dias[:4]]
    sem = avaliar(marcacoes, agora=agora)
    t('sem exceção, os 4 ajustes contam', sem['total_ajustes'] == 4, sem['total_ajustes'])
    t('e isso estoura o limite de 3', sem['total_ajustes'] > LIMITE_AJUSTES_MES)

    com = avaliar(marcacoes, agora=agora, excecoes={dias[0]})
    t('com 1 dia de exceção, sobram 3 ajustes', com['total_ajustes'] == 3, com['total_ajustes'])
    t('o perdoado fica registrado à parte', com['total_perdoados'] == 1)
    t('e é o dia certo', com['ajustes_perdoados'][0]['data'] == dias[0])
    t('já não estoura o limite', com['total_ajustes'] <= LIMITE_AJUSTES_MES)

    print('\n== E SÓ ISSO: O RESTO DO DIA CONTINUA VALENDO ==')
    # dia de exceção com batida faltando e fora do prazo continua incompleto
    velho = agora.date() - timedelta(days=10)
    incompleto = avaliar([M(velho, batidas=2, editado=True)], agora=agora, excecoes={velho})
    t('o dia continua sendo dia útil', incompleto['dias_uteis'] == 1)
    t('faltar batida continua pesando', len(incompleto['incompletos']) == 1,
      incompleto['incompletos'])
    t('mas o ajuste dele saiu da conta', incompleto['total_ajustes'] == 0)

    falta = avaliar([M(velho, batidas=4, editado=True)], faltas=[velho],
                    agora=agora, excecoes={velho})
    t('falta injustificada no dia de exceção continua contando',
      falta['total_faltas'] == 1)

    folga = avaliar([M(velho, batidas=0, editado=True, previsto=0)],
                    agora=agora, excecoes={velho})
    t('folga segue fora da conta', folga['dias_uteis'] == 0)

    print('\n== QUEM PODE CRIAR ==')
    adm = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
    ges = CommunicationGroup.objects.filter(name__iexact=GRUPO_GESTOR).first()
    assert adm and ges, 'grupos do Impulso não encontrados'
    area = Sector.objects.create(name='ZZ Area Excecao')

    def novo(username, grupos=(), **kw):
        u = User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local',
            password='S3nha!teste', sector=area,
            first_name=username.split('.')[1].title(), last_name='Teste', **kw)
        for g in grupos:
            u.communication_groups.add(g)
        return u

    chefe = novo('ex.chefe', [adm], is_superuser=True, is_staff=True)
    gestor = novo('ex.gestor', [adm, ges])
    colab = novo('ex.colab', [adm])

    dia_alvo = dias[0]
    antes = ExcecaoAssiduidade.objects.count()

    cc = Client(); cc.force_login(colab)
    r = cc.post('/impulso/assiduidade/excecao/',
                {'data': dia_alvo.isoformat(), 'motivo': 'teste'}, follow=True)
    t('colaborador não cria', ExcecaoAssiduidade.objects.count() == antes)
    t('e a tela avisa', 'Apenas o superadmin' in r.content.decode())

    cg = Client(); cg.force_login(gestor)
    cg.post('/impulso/assiduidade/excecao/',
            {'data': dia_alvo.isoformat(), 'motivo': 'teste'}, follow=True)
    t('gestor do módulo também não cria', ExcecaoAssiduidade.objects.count() == antes)

    cs = Client(); cs.force_login(chefe)
    r = cs.post('/impulso/assiduidade/excecao/',
                {'data': dia_alvo.isoformat(), 'motivo': 'relógio fora do ar',
                 'mes': dia_alvo.month, 'ano': dia_alvo.year}, follow=True)
    criada = ExcecaoAssiduidade.objects.filter(data=dia_alvo).first()
    t('superadmin cria', criada is not None)
    t('guarda o motivo', criada and criada.motivo == 'relógio fora do ar')
    t('guarda quem criou', criada and criada.criado_por_id == chefe.id)
    t('volta para o mês que estava sendo visto',
      f'mes={dia_alvo.month}' in r.redirect_chain[-1][0], r.redirect_chain)

    print('\n== VALIDAÇÕES ==')
    n = ExcecaoAssiduidade.objects.count()
    r = cs.post('/impulso/assiduidade/excecao/', {'data': '', 'motivo': 'x'}, follow=True)
    t('sem data, recusa', ExcecaoAssiduidade.objects.count() == n)
    t('e explica', 'Escolha o dia' in r.content.decode())

    r = cs.post('/impulso/assiduidade/excecao/',
                {'data': dias[3].isoformat(), 'motivo': '   '}, follow=True)
    t('sem motivo, recusa', ExcecaoAssiduidade.objects.count() == n)
    t('e explica por quê', 'Escreva o motivo' in r.content.decode())

    r = cs.post('/impulso/assiduidade/excecao/',
                {'data': dia_alvo.isoformat(), 'motivo': 'outro'}, follow=True)
    t('dia repetido não duplica', ExcecaoAssiduidade.objects.filter(data=dia_alvo).count() == 1)
    t('e avisa', 'já era uma exceção' in r.content.decode())

    r = cs.get('/impulso/assiduidade/excecao/')
    t('GET não cria (405)', r.status_code == 405, r.status_code)

    print('\n== REMOVER ==')
    r = cc.post(f'/impulso/assiduidade/excecao/{criada.id}/excluir/', follow=True)
    t('colaborador não remove', ExcecaoAssiduidade.objects.filter(id=criada.id).exists())
    r = cs.post(f'/impulso/assiduidade/excecao/{criada.id}/excluir/', follow=True)
    t('superadmin remove', not ExcecaoAssiduidade.objects.filter(id=criada.id).exists())
    t('e a tela confirma', 'deixou de ser exceção' in r.content.decode())
    r = cs.post('/impulso/assiduidade/excecao/999999999/excluir/')
    t('id inexistente devolve 404', r.status_code == 404, r.status_code)

    print('\n== TELA ==')
    ExcecaoAssiduidade.objects.create(data=dia_alvo, motivo='ZZ motivo visível',
                                      criado_por=chefe)
    html = cs.get(f'/impulso/assiduidade/?mes={dia_alvo.month}&ano={dia_alvo.year}').content.decode()
    t('superadmin vê o cartão de exceções', 'Dias de exceção' in html)
    t('e o dia listado', 'ZZ motivo visível' in html)
    t('o formulário pede motivo', 'name="motivo"' in html)

    html = cg.get(f'/impulso/assiduidade/?mes={dia_alvo.month}&ano={dia_alvo.year}').content.decode()
    t('gestor não vê o cartão', 'Dias de exceção' not in html)
    t('mas vê o aviso de que há exceção no mês',
      'há dia de exceção' in html)

    html = cc.get('/impulso/assiduidade/').content.decode()
    t('colaborador não vê o cartão', 'Dias de exceção' not in html)

    print('\n== PONTO: SINCRONIZAÇÃO DIÁRIA DAS 7h ==')
    from tangerino.agendador import esta_na_hora

    cfg = ConfiguracaoTangerino.get()
    t('a configuração tem o agendamento', hasattr(cfg, 'sincronizar_automatico'))
    t('está ligada', cfg.sincronizar_automatico is True)
    t('marcada para as 7h', cfg.hora_sincronizacao == time(7, 0), cfg.hora_sincronizacao)

    tz = timezone.get_current_timezone()
    hoje = timezone.localdate()

    def em(h, m=0):
        return timezone.make_aware(datetime.combine(hoje, time(h, m)), tz)

    cfg.sincronizar_automatico = True
    cfg.hora_sincronizacao = time(7, 0)
    cfg.ultima_sincronizacao_automatica = None
    t('antes das 7 não dispara', not esta_na_hora(cfg, em(6, 59)))
    t('às 7 em ponto dispara', esta_na_hora(cfg, em(7, 0)))
    t('depois das 7 também', esta_na_hora(cfg, em(11, 0)))

    cfg.ultima_sincronizacao_automatica = em(7, 1)
    t('tendo rodado hoje, não repete', not esta_na_hora(cfg, em(18, 0)))
    cfg.ultima_sincronizacao_automatica = em(7, 1) - timedelta(days=1)
    t('rodou ontem: dispara de novo hoje', esta_na_hora(cfg, em(7, 30)))

    cfg.sincronizar_automatico = False
    t('desligada, nunca dispara', not esta_na_hora(cfg, em(23, 0)))

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
