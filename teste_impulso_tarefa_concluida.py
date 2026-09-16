"""Impulso — Projeto FOCO: a tarefa pontua no mês em que foi entregue.

Antes a tarefa só contava no mês do prazo: criada com prazo já vencido (ou
entregue atrasada), caía num mês fora do ciclo e não pontuava nunca.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import importlib
import os
import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from communications.models import CommunicationGroup
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, Ciclo, ProjetoFoco, TarefaProjeto
from impulso.scoring import _nota_projeto_foco, calcular_pontuacao, periodo_do_mes
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


def meio_dia(dia):
    return timezone.make_aware(datetime.combine(dia, time(12)))


marcador = transaction.atomic()
marcador.__enter__()
try:
    adm = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
    ges = CommunicationGroup.objects.filter(name__iexact=GRUPO_GESTOR).first()
    area = Sector.objects.create(name='ZZ Area Tarefa Concluida')

    def novo(u, nome, grupos=(), **kw):
        x = User.objects.create_user(
            username=u, email=f'{u}@exemplo-teste.local', password='S3nha!teste',
            sector=area, first_name=nome, last_name='Teste', **kw)
        for g in grupos:
            x.communication_groups.add(g)
        return x

    chefe = novo('zztc.chefe', 'ZZTCChefe', [adm, ges], is_superuser=True, is_staff=True)
    dev = novo('zztc.dev', 'ZZTCDev', [adm])

    hoje = timezone.localdate()
    inicio, fim = periodo_do_mes()
    mes_passado_fim = inicio - timedelta(days=1)
    mes_passado_ini, _ = periodo_do_mes(mes_passado_fim)
    prox_ini = fim + timedelta(days=1)
    prox_fim = periodo_do_mes(prox_ini)[1]

    proj = ProjetoFoco.objects.create(nome='ZZTC Projeto', criado_por=chefe)
    proj.membros.add(dev)

    print('== A DATA ACOMPANHA O STATUS ==')
    tarefa = TarefaProjeto.objects.create(projeto=proj, titulo='ZZTC atrasada',
                                          responsavel=dev, prazo=mes_passado_ini + timedelta(days=5))
    t('tarefa nova não tem data de conclusão', tarefa.concluida_em is None)
    tarefa.status = TarefaProjeto.Status.EM_ANDAMENTO
    tarefa.save()
    t('em andamento continua sem data', tarefa.concluida_em is None)

    c = Client()
    c.force_login(dev)
    antes = timezone.now()
    r = c.post(f'/impulso/conectar/tarefa/{tarefa.id}/status/', {'status': 'CONCLUIDA'})
    tarefa.refresh_from_db()
    t('concluir pela tela responde', r.status_code == 302, r.status_code)
    t('concluir pela tela grava a data (save com update_fields)',
      tarefa.concluida_em is not None and tarefa.concluida_em >= antes, tarefa.concluida_em)

    primeira = tarefa.concluida_em
    tarefa.save()
    tarefa.refresh_from_db()
    t('salvar de novo não troca a data', tarefa.concluida_em == primeira)

    c.post(f'/impulso/conectar/tarefa/{tarefa.id}/status/', {'status': 'A_FAZER'})
    tarefa.refresh_from_db()
    t('reabrir apaga a data', tarefa.concluida_em is None and tarefa.status == 'A_FAZER')
    c.post(f'/impulso/conectar/tarefa/{tarefa.id}/status/', {'status': 'CONCLUIDA'})
    tarefa.refresh_from_db()
    t('concluir de novo grava a data de novo', tarefa.concluida_em is not None)

    html = c.get(f'/impulso/conectar/projetos/{proj.id}/').content.decode()
    t('o projeto mostra quando foi concluída',
      f'concluída em {timezone.localtime(tarefa.concluida_em):%d/%m/%Y}' in html)

    print('\n== O CASO RELATADO: PRAZO VENCIDO, ENTREGUE NESTE MÊS ==')
    nota, maximo, det = _nota_projeto_foco(dev, inicio, fim)
    t('a entrega pontua neste mês (antes: "sem tarefas")',
      not det.get('sem_tarefas') and det['concluidas'] == 1 and det['total'] == 1, det)
    t('metade dos 20 pela entrega', nota == Decimal('10.00'), nota)
    nota_ant, _, det_ant = _nota_projeto_foco(dev, mes_passado_ini, mes_passado_fim)
    t('no mês do prazo ela conta como pendente (não foi entregue lá)',
      det_ant.get('total') == 1 and det_ant.get('concluidas') == 0 and nota_ant == Decimal('0.00'), det_ant)

    dados = calcular_pontuacao(dev)
    t('a pontuação do mês soma o Projeto FOCO', dados['p_projeto_foco'] == Decimal('10.00'),
      dados['p_projeto_foco'])

    print('\n== ENTREGA ADIANTADA E ENTREGA DEPOIS DO MÊS ==')
    dev2 = novo('zztc.dev2', 'ZZTCDev2', [adm])
    proj.membros.add(dev2)
    adiantada = TarefaProjeto.objects.create(projeto=proj, titulo='ZZTC adiantada', responsavel=dev2,
                                             prazo=prox_ini + timedelta(days=3),
                                             status=TarefaProjeto.Status.CONCLUIDA)
    t('criada já concluída ganha a data', adiantada.concluida_em is not None)
    _, _, det = _nota_projeto_foco(dev2, inicio, fim)
    t('entregue antes do prazo pontua no mês da entrega', det.get('concluidas') == 1, det)
    _, _, det = _nota_projeto_foco(dev2, prox_ini, prox_fim)
    t('e não conta de novo no mês do prazo', det.get('sem_tarefas') is True, det)

    dev3 = novo('zztc.dev3', 'ZZTCDev3', [adm])
    tardia = TarefaProjeto.objects.create(projeto=proj, titulo='ZZTC entregue depois',
                                          responsavel=dev3, prazo=mes_passado_ini + timedelta(days=2),
                                          status=TarefaProjeto.Status.CONCLUIDA)
    TarefaProjeto.objects.filter(pk=tardia.pk).update(concluida_em=meio_dia(inicio + timedelta(days=1)))
    _, _, det = _nota_projeto_foco(dev3, mes_passado_ini, mes_passado_fim)
    t('recalcular o mês do prazo: entregue depois conta como pendente lá',
      det.get('total') == 1 and det.get('concluidas') == 0, det)
    _, _, det = _nota_projeto_foco(dev3, inicio, fim)
    t('e como entrega no mês em que foi entregue', det.get('total') == 1 and det.get('concluidas') == 1, det)

    print('\n== PENDENTES E LEGADO ==')
    dev4 = novo('zztc.dev4', 'ZZTCDev4', [adm])
    TarefaProjeto.objects.create(projeto=proj, titulo='ZZTC aberta com prazo no mês', responsavel=dev4, prazo=hoje)
    TarefaProjeto.objects.create(projeto=proj, titulo='ZZTC aberta sem prazo', responsavel=dev4)
    TarefaProjeto.objects.create(projeto=proj, titulo='ZZTC aberta prazo mês que vem', responsavel=dev4,
                                 prazo=prox_ini + timedelta(days=1))
    _, _, det = _nota_projeto_foco(dev4, inicio, fim)
    t('aberta com prazo no mês e aberta sem prazo contam como pendentes',
      det.get('total') == 2 and det.get('concluidas') == 0, det)
    antiga = TarefaProjeto.objects.create(projeto=proj, titulo='ZZTC concluída sem data', responsavel=dev4,
                                          prazo=hoje, status=TarefaProjeto.Status.CONCLUIDA)
    TarefaProjeto.objects.filter(pk=antiga.pk).update(concluida_em=None)
    _, _, det = _nota_projeto_foco(dev4, inicio, fim)
    t('concluída sem data (gravada por fora do save) fica com a régua do prazo',
      det.get('total') == 3 and det.get('concluidas') == 1, det)

    dev5 = novo('zztc.dev5', 'ZZTCDev5', [adm])
    fora = TarefaProjeto.objects.create(projeto=proj, titulo='ZZTC projeto inativo', responsavel=dev5,
                                        status=TarefaProjeto.Status.CONCLUIDA)
    inativo = ProjetoFoco.objects.create(nome='ZZTC inativo', criado_por=chefe, ativo=False)
    TarefaProjeto.objects.filter(pk=fora.pk).update(projeto=inativo)
    _, _, det = _nota_projeto_foco(dev5, inicio, fim)
    t('projeto inativo continua fora', det.get('sem_tarefas') is True, det)

    print('\n== AS TELAS FILTRADAS POR MÊS MOSTRAM O QUE PONTUA NO MÊS ==')
    mes = f'{hoje.year:04d}-{hoje.month:02d}'
    html = c.get(f'/impulso/minhas-tarefas/?mes={mes}').content.decode()
    t('minhas tarefas do mês traz a entregue neste mês com prazo vencido', 'ZZTC atrasada' in html)
    ant = f'{mes_passado_ini.year:04d}-{mes_passado_ini.month:02d}'
    html = c.get(f'/impulso/minhas-tarefas/?mes={ant}').content.decode()
    t('o mês do prazo também a mostra (lá ela conta como pendente, igual à pontuação)',
      'ZZTC atrasada' in html)
    adiantada_mes = f'{prox_ini.year:04d}-{prox_ini.month:02d}'
    c2 = Client()
    c2.force_login(dev2)
    html = c2.get(f'/impulso/minhas-tarefas/?mes={adiantada_mes}').content.decode()
    t('a entregue adiantada não aparece no mês do prazo (lá ela não conta)', 'ZZTC adiantada' not in html)
    html = c2.get(f'/impulso/minhas-tarefas/?mes={mes}').content.decode()
    t('e aparece no mês em que foi entregue', 'ZZTC adiantada' in html)

    projeto_novo = ProjetoFoco.objects.create(nome='ZZTC só entrega no mês', criado_por=chefe)
    projeto_novo.membros.add(dev)
    ProjetoFoco.objects.filter(pk=projeto_novo.pk).update(criado_em=meio_dia(mes_passado_ini))
    t_nova = TarefaProjeto.objects.create(projeto=projeto_novo, titulo='ZZTC tarefa velha', responsavel=dev,
                                          prazo=mes_passado_ini, status=TarefaProjeto.Status.CONCLUIDA)
    cg = Client()
    cg.force_login(chefe)
    html = cg.get(f'/impulso/conectar/projetos/?mes={mes}').content.decode()
    t('a lista de projetos do mês traz o projeto com entrega no mês', 'ZZTC só entrega no mês' in html)

    print('\n== A MIGRAÇÃO DAS TAREFAS JÁ CONCLUÍDAS ==')
    migracao = importlib.import_module('impulso.migrations.0019_tarefas_concluidas_antigas')
    primeiro_ciclo = Ciclo.objects.order_by('inicio').values_list('inicio', flat=True).first()
    if primeiro_ciclo is None:
        Ciclo.objects.create(nome='ZZTC ciclo', inicio=inicio, fim=fim, criado_por=chefe)
        primeiro_ciclo = inicio
    pre_ciclo = primeiro_ciclo - timedelta(days=20)
    v1 = TarefaProjeto.objects.create(projeto=proj, titulo='ZZTC legado antes do ciclo', responsavel=dev5,
                                      prazo=pre_ciclo, status=TarefaProjeto.Status.CONCLUIDA)
    v2 = TarefaProjeto.objects.create(projeto=proj, titulo='ZZTC legado dentro do ciclo', responsavel=dev5,
                                      prazo=primeiro_ciclo + timedelta(days=4), status=TarefaProjeto.Status.CONCLUIDA)
    v3 = TarefaProjeto.objects.create(projeto=proj, titulo='ZZTC legado prazo futuro', responsavel=dev5,
                                      prazo=hoje + timedelta(days=400), status=TarefaProjeto.Status.CONCLUIDA)
    v4 = TarefaProjeto.objects.create(projeto=proj, titulo='ZZTC legado aberta', responsavel=dev5, prazo=pre_ciclo)
    ids = [v1.pk, v2.pk, v3.pk, v4.pk]
    TarefaProjeto.objects.filter(pk__in=ids).update(concluida_em=None,
                                                   criado_em=meio_dia(pre_ciclo - timedelta(days=1)))
    migracao.preencher(django_apps, None)
    v1, v2, v3, v4 = (TarefaProjeto.objects.get(pk=pk) for pk in ids)
    t('entrega de antes do ciclo vai para o primeiro dia do ciclo',
      v1.concluida_em and timezone.localtime(v1.concluida_em).date() == min(primeiro_ciclo, hoje), v1.concluida_em)
    t('com prazo dentro do ciclo fica no dia do prazo (mesmo mês de antes)',
      v2.concluida_em and timezone.localtime(v2.concluida_em).date() == min(primeiro_ciclo + timedelta(days=4), hoje),
      v2.concluida_em)
    t('nunca grava data no futuro', v3.concluida_em and v3.concluida_em <= timezone.now(), v3.concluida_em)
    t('tarefa aberta fica sem data', v4.concluida_em is None)
    anterior = {pk: TarefaProjeto.objects.get(pk=pk).concluida_em for pk in ids}
    migracao.preencher(django_apps, None)
    t('rodar de novo não mexe em quem já tem data',
      all(TarefaProjeto.objects.get(pk=pk).concluida_em == anterior[pk] for pk in ids))

    print('\n== O USUÁRIO DO RELATO (só leitura) ==')
    vini = User.objects.filter(pk=1, first_name__iexact='VINICIUS').first()
    if vini is None:
        print('  (usuário 1 não é o do relato neste banco — pulado)')
    else:
        _, maximo, det = _nota_projeto_foco(vini, inicio, fim)
        t('VINICIUS MARCELOS: a tarefa concluída entra no mês',
          not det.get('sem_tarefas') and det.get('concluidas', 0) >= 1, det)
        t('e pontua a metade da entrega', det.get('pontos_entrega', Decimal('0')) > 0, det)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
