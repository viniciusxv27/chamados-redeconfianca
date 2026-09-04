"""Conectar: saber o que está DE FATO concluído e poder aprovar ou recusar.

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

from communications.models import CommunicationGroup
from core.models import Notification
from impulso.models import (GRUPO_ADM, GRUPO_GESTOR, ConclusaoConteudo,
                            ConteudoConectar)
from impulso.scoring import _nota_conteudos, periodo_do_mes
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
    adm = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
    ges = CommunicationGroup.objects.filter(name__iexact=GRUPO_GESTOR).first()
    assert adm and ges, 'grupos do Impulso não encontrados'
    area = Sector.objects.create(name='ZZ Area Conectar')

    def novo(u, grupos=(), **kw):
        x = User.objects.create_user(
            username=u, email=f'{u}@exemplo-teste.local', password='S3nha!teste',
            sector=area, first_name=u.split('.')[1].title(), last_name='T', **kw)
        for g in grupos:
            x.communication_groups.add(g)
        return x

    gestor = novo('cn.gestor', [adm, ges])
    colab = novo('cn.colab', [adm])
    outro = novo('cn.outro', [adm])

    conteudo = ConteudoConectar.objects.create(
        titulo='ZZ Curso de teste', tipo=ConteudoConectar.Tipo.CURSO,
        criado_por=gestor)
    c = ConclusaoConteudo.objects.create(
        conteudo=conteudo, user=colab, concluido=True,
        concluido_em=timezone.now())

    print('== TRÊS ESTADOS, NÃO DOIS ==')
    t('marcada como feita nasce aguardando',
      c.aprovacao == ConclusaoConteudo.Aprovacao.PENDENTE)
    t('e ainda não vale ponto', not c.vale_ponto)
    t('a tela sabe que está aguardando', c.aguardando)

    c.aprovacao = ConclusaoConteudo.Aprovacao.APROVADA
    c.save()
    t('aprovada vale ponto', c.vale_ponto)
    t('e não está mais aguardando', not c.aguardando)

    c.aprovacao = ConclusaoConteudo.Aprovacao.RECUSADA
    c.save()
    t('recusada não vale ponto', not c.vale_ponto)

    c.aprovacao = ConclusaoConteudo.Aprovacao.PENDENTE
    c.save()

    print('\n== QUEM CONFERE ==')
    t('o gestor confere', c.pode_decidir(gestor))
    t('quem fez NÃO confere o próprio', not c.pode_decidir(colab))
    t('outro colaborador não confere', not c.pode_decidir(outro))
    chefe = novo('cn.chefe', [adm, ges], is_superuser=True, is_staff=True)
    t('superadmin confere', c.pode_decidir(chefe))
    naofeita = ConclusaoConteudo.objects.create(
        conteudo=conteudo, user=outro, concluido=False)
    t('o que não foi marcado não entra na fila', not naofeita.pode_decidir(gestor))

    print('\n== APROVAR E RECUSAR ==')
    cg = Client(); cg.force_login(gestor)
    r = cg.post(f'/impulso/conectar/conclusao/{c.id}/decidir/',
                {'decisao': 'aprovar'}, follow=True)
    c.refresh_from_db()
    t('aprovar grava', c.aprovacao == ConclusaoConteudo.Aprovacao.APROVADA)
    t('registra quem conferiu', c.decidida_por_id == gestor.id)
    t('e quando', c.decidida_em is not None)
    t('a pessoa é avisada',
      Notification.objects.filter(user=colab, title='Conteúdo aprovado').exists())

    c.aprovacao = ConclusaoConteudo.Aprovacao.PENDENTE
    c.save()
    r = cg.post(f'/impulso/conectar/conclusao/{c.id}/decidir/',
                {'decisao': 'recusar'}, follow=True)
    c.refresh_from_db()
    t('recusa sem motivo é bloqueada',
      c.aprovacao == ConclusaoConteudo.Aprovacao.PENDENTE)
    t('e a tela explica', 'motivo da recusa' in r.content.decode())

    r = cg.post(f'/impulso/conectar/conclusao/{c.id}/decidir/',
                {'decisao': 'recusar', 'observacao': 'certificado ilegível'}, follow=True)
    c.refresh_from_db()
    t('com motivo, recusa', c.aprovacao == ConclusaoConteudo.Aprovacao.RECUSADA)
    t('guarda o motivo', c.observacao == 'certificado ilegível')
    t('a pessoa é avisada do motivo',
      Notification.objects.filter(user=colab, title='Conteúdo recusado').exists())

    cc = Client(); cc.force_login(colab)
    r = cc.post(f'/impulso/conectar/conclusao/{c.id}/decidir/',
                {'decisao': 'aprovar'}, follow=True)
    c.refresh_from_db()
    t('quem fez não aprova o próprio pela tela',
      c.aprovacao == ConclusaoConteudo.Aprovacao.RECUSADA)
    t('e a tela avisa', 'não confere' in r.content.decode())

    r = cg.get(f'/impulso/conectar/conclusao/{c.id}/decidir/')
    t('GET não decide (405)', r.status_code == 405, r.status_code)

    print('\n== REENVIO DEPOIS DA RECUSA ==')
    r = cc.post(f'/impulso/conectar/{conteudo.id}/concluir/', follow=True)
    c.refresh_from_db()
    t('volta para a fila', c.aprovacao == ConclusaoConteudo.Aprovacao.PENDENTE,
      c.aprovacao)
    t('o motivo antigo é apagado', c.observacao == '')
    t('e quem tinha decidido também', c.decidida_por_id is None)
    t('a tela avisa que depende da conferência',
      'quando o gestor conferir' in r.content.decode())

    print('\n== A PONTUAÇÃO SÓ CONTA O CONFERIDO ==')
    from decimal import Decimal
    inicio, fim = periodo_do_mes()
    nota, maximo, det = _nota_conteudos(
        colab, [ConteudoConectar.Tipo.CURSO], Decimal('10'), inicio, fim)
    t('aguardando não pontua', det.get('concluidos') == 0, det)
    t('mas a tela sabe que está na fila', det.get('aguardando') == 1, det)

    c.aprovacao = ConclusaoConteudo.Aprovacao.APROVADA
    c.save()
    nota2, _m, det2 = _nota_conteudos(
        colab, [ConteudoConectar.Tipo.CURSO], Decimal('10'), inicio, fim)
    t('aprovado pontua', det2.get('concluidos') == 1, det2)
    t('e a nota sobe', nota2 > nota, (nota, nota2))

    print('\n== A TELA DO CONECTAR ==')
    html = cg.get('/impulso/conectar/').content.decode()
    t('o gestor não vê fila quando não há nada pendente',
      'Aguardando sua conferência' not in html)

    c.aprovacao = ConclusaoConteudo.Aprovacao.PENDENTE
    c.save()
    html = cg.get('/impulso/conectar/').content.decode()
    t('a fila aparece para o gestor', 'Aguardando sua conferência' in html)
    t('com o nome de quem marcou', colab.get_full_name() in html)
    t('e os dois botões', 'Aprovar' in html and 'Recusar' in html)

    html = cc.get('/impulso/conectar/').content.decode()
    t('o colaborador não vê a fila de conferência',
      'Aguardando sua conferência' not in html)
    t('mas sabe que o dele está esperando', 'aguardando conferência do gestor' in html)

    c.aprovacao = ConclusaoConteudo.Aprovacao.RECUSADA
    c.observacao = 'ZZ refaça o anexo'
    c.save()
    html = cc.get('/impulso/conectar/').content.decode()
    t('recusado aparece como "precisa refazer"', 'Precisa refazer' in html)
    t('com o motivo', 'ZZ refaça o anexo' in html)

    print('\n== O CARD MOSTRA O ESTADO CERTO ==')
    with open('templates/impulso/_conteudo_secao.html', encoding='utf-8') as f:
        secao = f.read()
    t('tem "Em conferência"', 'Em conferência' in secao)
    t('tem "Recusado"', 'Recusado' in secao)
    t('e "Concluído" só quando aprovado',
      "c.minha_conclusao.aprovacao == 'APROVADA'" in secao)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
