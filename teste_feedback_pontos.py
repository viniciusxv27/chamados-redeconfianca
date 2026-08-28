"""Impulso: os 10 pontos do feedback do gestor pelos três caminhos.

1. primeiro feedback recebido
2. nota maior que a do feedback anterior
3. nota >= 90 de 100 (9 de 10)

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
from django.utils import timezone

from feedback.models import Feedback
from impulso.scoring import (FEEDBACK_NOTA_MINIMA, PT_FEEDBACK, _nota_feedback,
                             avaliar_feedback, periodo_do_mes)

User = get_user_model()
ok = fail = 0
criados = {'users': [], 'feedbacks': []}


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def novo_usuario(username):
    u = User.objects.create_user(username=username, email=f'{username}@exemplo-teste.local',
                                 password='S3nha!teste', first_name=username)
    criados['users'].append(u)
    return u


def cria_feedback(gestor, pessoa, nota_0a10, data, criado_em=None):
    """Cria um feedback com todas as notas iguais, para a média ser exata."""
    campos = {campo: nota_0a10 for campo, _ in Feedback.SCALE_FIELDS}
    fb = Feedback.objects.create(evaluator=gestor, evaluatee=pessoa, data=data, **campos)
    if criado_em:
        Feedback.objects.filter(id=fb.id).update(created_at=criado_em)
        fb.refresh_from_db()
    criados['feedbacks'].append(fb)
    return fb


try:
    gestor = novo_usuario('fbp.gestor.t')
    hoje = timezone.localdate()
    inicio_mes, fim_mes = periodo_do_mes()
    agora = timezone.now()

    print('== CAMINHO 1 — PRIMEIRO FEEDBACK ==')
    p1 = novo_usuario('fbp.novato.t')
    fb = cria_feedback(gestor, p1, 6, hoje, agora)          # nota 60/100
    d = avaliar_feedback(fb)
    t('reconhece o primeiro feedback', d['primeiro'] is True)
    t('primeiro feedback garante o ponto mesmo com nota baixa',
      d['atingiu'] is True and d['nota'] == 60.0, d)
    pontos, aplicavel, det = _nota_feedback(p1, inicio_mes, fim_mes)
    t('pontuação dá os 10 pontos', pontos == PT_FEEDBACK, pontos)
    t('explica o motivo na tela', det['motivo'] == 'primeiro feedback recebido', det['motivo'])

    print('\n== CAMINHO 2 — A NOTA SUBIU ==')
    p2 = novo_usuario('fbp.evoluiu.t')
    cria_feedback(gestor, p2, 5, hoje - timedelta(days=60), agora - timedelta(days=60))
    fb2 = cria_feedback(gestor, p2, 6, hoje, agora)          # 50 -> 60
    d = avaliar_feedback(fb2)
    t('não é mais o primeiro', d['primeiro'] is False)
    t('enxerga a nota anterior', d['anterior'] == 50.0, d['anterior'])
    t('reconhece a evolução', d['evoluiu'] is True)
    t('nota que subiu garante o ponto', d['atingiu'] is True)
    pontos, _ap, det = _nota_feedback(p2, inicio_mes, fim_mes)
    t('pontuação dá os 10 pontos', pontos == PT_FEEDBACK, pontos)
    t('explica a evolução', 'evoluiu de 50 para 60' in det['motivo'], det['motivo'])

    print('\n== CAMINHO 3 — NOTA ALTA ==')
    p3 = novo_usuario('fbp.nota9.t')
    cria_feedback(gestor, p3, 10, hoje - timedelta(days=60), agora - timedelta(days=60))
    fb3 = cria_feedback(gestor, p3, 9, hoje, agora)          # 100 -> 90 (caiu, mas é 90)
    d = avaliar_feedback(fb3)
    t('nota caiu em relação à anterior', d['evoluiu'] is False and d['anterior'] == 100.0)
    t('9 de 10 (90 de 100) garante o ponto', d['nota_alta'] is True and d['atingiu'] is True,
      d['nota'])
    pontos, _ap, det = _nota_feedback(p3, inicio_mes, fim_mes)
    t('pontuação dá os 10 pontos', pontos == PT_FEEDBACK, pontos)
    t('quem está no topo não perde ponto por não ter como subir',
      'acima de 90' in det['motivo'], det['motivo'])

    print('\n== QUEM NÃO FECHA POR NENHUM CAMINHO ==')
    p4 = novo_usuario('fbp.caiu.t')
    cria_feedback(gestor, p4, 8, hoje - timedelta(days=60), agora - timedelta(days=60))
    fb4 = cria_feedback(gestor, p4, 7, hoje, agora)          # 80 -> 70
    d = avaliar_feedback(fb4)
    t('nota que caiu e está abaixo de 90 não garante',
      d['atingiu'] is False, d)
    pontos, aplicavel, det = _nota_feedback(p4, inicio_mes, fim_mes)
    t('pontuação não dá os pontos', pontos == 0, pontos)
    t('o item continua contando no total possível', aplicavel == PT_FEEDBACK)
    t('explica por que não fechou',
      'não subiu' in det['motivo'] and '90' in det['motivo'], det['motivo'])

    print('\n== EMPATE NÃO É EVOLUÇÃO ==')
    p5 = novo_usuario('fbp.empate.t')
    cria_feedback(gestor, p5, 7, hoje - timedelta(days=60), agora - timedelta(days=60))
    fb5 = cria_feedback(gestor, p5, 7, hoje, agora)          # 70 -> 70
    d = avaliar_feedback(fb5)
    t('nota igual não conta como evolução', d['evoluiu'] is False and d['atingiu'] is False, d)

    print('\n== FEEDBACK ANTERIOR SEM NOTA ==')
    p6 = novo_usuario('fbp.embranco.t')
    vazio = Feedback.objects.create(evaluator=gestor, evaluatee=p6,
                                    data=hoje - timedelta(days=60))
    criados['feedbacks'].append(vazio)
    Feedback.objects.filter(id=vazio.id).update(created_at=agora - timedelta(days=60))
    fb6 = cria_feedback(gestor, p6, 6, hoje, agora)
    d = avaliar_feedback(fb6)
    t('formulário anterior em branco não vira "piorou"', d['atingiu'] is True, d)
    t('conta como primeiro feedback com nota', d['primeiro'] is True)

    print('\n== SEM FEEDBACK NO MÊS ==')
    p7 = novo_usuario('fbp.semfb.t')
    pontos, aplicavel, det = _nota_feedback(p7, inicio_mes, fim_mes)
    t('sem feedback não pontua', pontos == 0)
    t('sem feedback sai do total possível (não zera a nota)', aplicavel == 0, aplicavel)
    t('marca sem_feedback', det.get('sem_feedback') is True)

    print('\n== DOIS FEEDBACKS NO MÊS ==')
    p8 = novo_usuario('fbp.dois.t')
    cria_feedback(gestor, p8, 9, hoje - timedelta(days=200), agora - timedelta(days=200))
    cria_feedback(gestor, p8, 6, hoje, agora - timedelta(hours=3))     # 60: caiu de 90
    cria_feedback(gestor, p8, 7, hoje, agora - timedelta(hours=1))     # 70: subiu de 60
    pontos, _ap, det = _nota_feedback(p8, inicio_mes, fim_mes)
    t('basta um feedback do mês fechar por algum caminho', pontos == PT_FEEDBACK, det)
    t('mostra o que garantiu o ponto', det['atingiu'] is True and det['nota'] == 70.0, det)

    print('\n== A TELA USA A MESMA CONTA ==')
    from django.test import Client

    from impulso.models import GRUPO_ADM, GRUPO_GESTOR
    from communications.models import CommunicationGroup

    adm = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
    ges = CommunicationGroup.objects.filter(name__iexact=GRUPO_GESTOR).first()
    if adm:
        gestor.communication_groups.add(adm)
    if ges:
        gestor.communication_groups.add(ges)
    for p in (p1, p2, p3, p4):
        if adm:
            p.communication_groups.add(adm)

    c = Client()
    c.force_login(gestor)
    r = c.get('/impulso/feedbacks/')
    t('tela de feedbacks abre', r.status_code == 200, r.status_code)
    html = r.content.decode()
    t('tela explica os três caminhos',
      'primeiro feedback recebido' in html and 'nota maior que a do' in html)

    from impulso.views import feedback_list  # noqa: F401  (garante import válido)

    for pessoa, esperado in ((p1, True), (p2, True), (p3, True), (p4, False)):
        pontos, _ap, det = _nota_feedback(pessoa, inicio_mes, fim_mes)
        t(f'{pessoa.username}: motor e regra combinam',
          (pontos == PT_FEEDBACK) is esperado, f'{pontos} / {det}')

finally:
    Feedback.objects.filter(id__in=[f.id for f in criados['feedbacks']]).delete()
    for u in criados['users']:
        Feedback.objects.filter(evaluatee=u).delete()
        Feedback.objects.filter(evaluator=u).delete()
        User.objects.filter(id=u.id).delete()
    print('\nlimpeza: só o que este teste criou foi removido.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
