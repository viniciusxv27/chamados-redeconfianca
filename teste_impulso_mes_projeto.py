"""Impulso: filtro de mês (Confiar/Conectar/Inovar) e Projeto FOCO em 2 metades.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
from datetime import timedelta
from decimal import Decimal

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client, RequestFactory
from django.utils import timezone

from communications.models import CommunicationGroup
from core.models import Notification
from impulso import filtros
from impulso.models import (GRUPO_ADM, GRUPO_GESTOR, Ideia, Meta, ProjetoFoco,
                            TarefaProjeto)
from impulso.scoring import _nota_projeto_foco, periodo_do_mes
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
    area = Sector.objects.create(name='ZZ Area Mes')

    def novo(u, nome, grupos=(), **kw):
        x = User.objects.create_user(
            username=u, email=f'{u}@exemplo-teste.local', password='S3nha!teste',
            sector=area, first_name=nome, last_name='Teste', **kw)
        for g in grupos:
            x.communication_groups.add(g)
        return x

    chefe = novo('zzm.chefe', 'ZZMChefe', [adm, ges], is_superuser=True, is_staff=True)
    dev = novo('zzm.dev', 'ZZMDev', [adm])

    hoje = timezone.localdate()
    inicio, fim = periodo_do_mes()
    mes_atual = f'{hoje.year:04d}-{hoje.month:02d}'
    anterior = inicio - timedelta(days=1)
    mes_anterior = f'{anterior.year:04d}-{anterior.month:02d}'

    print('== O SELETOR DE MÊS ==')
    def f(qs=''):
        return filtros.ler(RequestFactory().get(f'/x/?{qs}'))

    t('lê YYYY-MM', f(f'mes={mes_atual}')['mes'] == mes_atual)
    t('calcula o período', f(f'mes={mes_atual}')['inicio'] == inicio)
    t('e o último dia', f(f'mes={mes_atual}')['fim'] == fim)
    t('mês inválido é ignorado, não quebra', f('mes=banana')['mes'] == '')
    t('mês 13 é ignorado', f('mes=2026-13')['mes'] == '')
    t('ano fora da faixa é ignorado', f('mes=1500-01')['mes'] == '')
    t('sem mês não filtra', f()['inicio'] is None)
    t('o seletor oferece 12 meses', len(filtros.meses_disponiveis()) == 12)
    t('começando pelo mês atual',
      filtros.meses_disponiveis()[0][0] == mes_atual)

    print('\n== CONFIAR: METAS POR MÊS ==')
    m_agora = Meta.objects.create(titulo='ZZM Meta deste mes', colaborador=dev,
                                  gestor=chefe, prazo=hoje,
                                  aprovacao=Meta.Aprovacao.APROVADA)
    m_antes = Meta.objects.create(titulo='ZZM Meta do mes passado', colaborador=dev,
                                  gestor=chefe, prazo=anterior,
                                  aprovacao=Meta.Aprovacao.APROVADA)

    c = Client(); c.force_login(chefe)
    html = c.get('/impulso/metas/').content.decode()
    t('o seletor de mês aparece no Confiar',
      'name="mes"' in html and 'Todos os meses' in html)
    t('sem filtro, as duas metas', 'ZZM Meta deste mes' in html
      and 'ZZM Meta do mes passado' in html)

    html = c.get(f'/impulso/metas/?mes={mes_atual}').content.decode()
    t('mês atual: só a deste mês',
      'ZZM Meta deste mes' in html and 'ZZM Meta do mes passado' not in html)
    html = c.get(f'/impulso/metas/?mes={mes_anterior}').content.decode()
    t('mês passado: só a do mês passado',
      'ZZM Meta do mes passado' in html and 'ZZM Meta deste mes' not in html)

    html = c.get(f'/impulso/atividades/?mes={mes_atual}').content.decode()
    t('próximas atividades filtram por mês',
      'ZZM Meta deste mes' in html and 'ZZM Meta do mes passado' not in html)

    print('\n== CONECTAR: MÊS NAS TRÊS TELAS ==')
    for url, nome in [('/impulso/conectar/', 'Cursos, Vídeos e POPs'),
                      ('/impulso/conectar/projetos/', 'Projetos Foco'),
                      ('/impulso/minhas-tarefas/', 'Minhas Tarefas')]:
        html = c.get(url).content.decode()
        t(f'{nome}: tem seletor de mês',
          'name="mes"' in html and 'Todos os meses' in html)

    print('\n== INOVAR: MÊS SIM, NOME NÃO ==')
    import datetime as _dt

    ideia_agora = Ideia.objects.create(
        autor=dev, descricao='ZZM Ideia nova', setor_impacto='ZZ', motivo='m')
    ideia_velha = Ideia.objects.create(
        autor=dev, descricao='ZZM Ideia velha', setor_impacto='ZZ', motivo='m')
    quando = _dt.datetime.combine(anterior, _dt.time(12, 0))
    if timezone.is_naive(quando):
        quando = timezone.make_aware(quando)
    Ideia.objects.filter(pk=ideia_velha.pk).update(criado_em=quando)

    html = c.get('/impulso/inovar/').content.decode()
    t('o seletor de mês aparece no Inovar', 'name="mes"' in html)
    t('mas NÃO tem busca por nome', 'name="q"' not in html)
    t('nem seletor de setor', 'name="setor"' not in html)

    html = c.get(f'/impulso/inovar/?mes={mes_atual}').content.decode()
    t('filtra as ideias do mês', 'ZZM Ideia nova' in html)
    t('e deixa a do mês passado de fora', 'ZZM Ideia velha' not in html)

    print('\n== O MÊS CONVIVE COM O NOME E O SETOR ==')
    html = c.get(f'/impulso/metas/?mes={mes_atual}&q=ZZMDev').content.decode()
    t('mês + nome juntos', 'ZZM Meta deste mes' in html
      and 'ZZM Meta do mes passado' not in html)
    t('o mês volta selecionado', f'value="{mes_atual}" selected' in html)
    t('e o nome também', f'value="ZZMDev"' in html)

    print('\n== PROJETO FOCO: DUAS METADES ==')
    proj = ProjetoFoco.objects.create(nome='ZZM Projeto', criado_por=chefe)
    proj.membros.add(dev)
    t1 = TarefaProjeto.objects.create(projeto=proj, titulo='ZZM T1',
                                      responsavel=dev, prazo=hoje)
    t2 = TarefaProjeto.objects.create(projeto=proj, titulo='ZZM T2',
                                      responsavel=dev, prazo=hoje)

    nota, maximo, det = _nota_projeto_foco(dev, inicio, fim)
    t('nada feito, nada pontuado', nota == Decimal('0.00'), nota)
    t('o máximo continua 20', maximo == Decimal('20'), maximo)

    t1.status = TarefaProjeto.Status.CONCLUIDA
    t1.save()
    nota, maximo, det = _nota_projeto_foco(dev, inicio, fim)
    t('1 de 2 tarefas = 1/4 do total (metade da metade)',
      nota == Decimal('5.00'), nota)
    t('e a conclusão ainda não pagou',
      det['pontos_conclusao'] == Decimal('0.00'), det)

    t2.status = TarefaProjeto.Status.CONCLUIDA
    t2.save()
    nota, maximo, det = _nota_projeto_foco(dev, inicio, fim)
    t('tudo entregue vale só a metade dos 20', nota == Decimal('10.00'), nota)
    t('a entrega pagou 10', det['pontos_entrega'] == Decimal('10.00'), det)
    t('a conclusão ainda não', det['pontos_conclusao'] == Decimal('0.00'), det)
    t('o projeto sabe que está tudo entregue', proj.tudo_entregue)

    proj.concluido = True
    proj.save()
    nota, maximo, det = _nota_projeto_foco(dev, inicio, fim)
    t('projeto concluído fecha os 20', nota == Decimal('20.00'), nota)
    t('a segunda metade veio da conclusão',
      det['pontos_conclusao'] == Decimal('10.00'), det)

    # Entregou a parte dele, mas o projeto não saiu: só metade.
    proj.concluido = False
    proj.save()
    proj2 = ProjetoFoco.objects.create(nome='ZZM Projeto 2', criado_por=chefe)
    t3 = TarefaProjeto.objects.create(projeto=proj2, titulo='ZZM T3',
                                      responsavel=dev, prazo=hoje,
                                      status=TarefaProjeto.Status.CONCLUIDA)
    proj2.concluido = True
    proj2.save()
    nota, maximo, det = _nota_projeto_foco(dev, inicio, fim)
    t('em 2 projetos com 1 concluído, a conclusão paga metade da metade',
      det['pontos_conclusao'] == Decimal('5.00'), det)
    t('tudo entregue + 1 de 2 projetos = 15', nota == Decimal('15.00'), nota)

    print('\n== QUEM CONCLUI O PROJETO ==')
    proj.concluido = False
    proj.save()
    cd = Client(); cd.force_login(dev)
    r = cd.post(f'/impulso/conectar/projetos/{proj.id}/concluir/', follow=True)
    proj.refresh_from_db()
    t('colaborador não conclui projeto', not proj.concluido)

    r = c.post(f'/impulso/conectar/projetos/{proj.id}/concluir/', follow=True)
    proj.refresh_from_db()
    t('o gestor conclui', proj.concluido)
    t('registra quem', proj.concluido_por_id == chefe.id)
    t('e quando', proj.concluido_em is not None)
    t('avisa quem tem tarefa no projeto',
      Notification.objects.filter(user=dev, title='Projeto foco concluído').exists())

    r = c.get(f'/impulso/conectar/projetos/{proj.id}/concluir/')
    t('GET não conclui (405)', r.status_code == 405, r.status_code)

    r = c.post(f'/impulso/conectar/projetos/{proj.id}/concluir/',
               {'reabrir': '1'}, follow=True)
    proj.refresh_from_db()
    t('dá para reabrir', not proj.concluido)
    t('e limpa quem tinha concluído', proj.concluido_por_id is None)
    t('a tela avisa o efeito na pontuação',
      'sai da pontuação' in r.content.decode())

    print('\n== A TELA DO PROJETO ==')
    html = c.get(f'/impulso/conectar/projetos/{proj.id}/').content.decode()
    t('o gestor vê o botão de concluir', 'Concluir projeto' in html)
    t('e o progresso das tarefas', 'de 2 tarefa(s) concluída(s)' in html)
    t('com o aviso da metade', 'Metade dos pontos' in html
      or 'dá para encerrar o projeto' in html)

    proj.concluido = True
    proj.concluido_em = timezone.now()
    proj.save()
    html = c.get(f'/impulso/conectar/projetos/{proj.id}/').content.decode()
    t('concluído mostra o selo', 'concluído' in html)
    t('e vira Reabrir', 'Reabrir projeto' in html)

    html = cd.get(f'/impulso/conectar/projetos/{proj.id}/').content.decode()
    t('o colaborador não vê o botão', 'Concluir projeto' not in html
      and 'Reabrir projeto' not in html)

    html = c.get('/impulso/conectar/projetos/').content.decode()
    t('a lista marca o projeto concluído', 'Concluído' in html)

    print('\n== O DETALHAMENTO EXPLICA AS DUAS METADES ==')
    from impulso.scoring import calcular_pontuacao, linhas_detalhadas
    linhas = linhas_detalhadas(calcular_pontuacao(dev))
    linha = next((l for l in linhas if 'Projeto FOCO' in l['item']
                  or 'projeto' in l['item'].lower()), None)
    t('a linha do Projeto FOCO existe', linha is not None,
      [l['item'] for l in linhas])
    t('e diz tarefas E projetos',
      linha and 'tarefa(s) concluída(s)' in linha['info']
      and 'projeto(s) concluído(s)' in linha['info'],
      linha and linha['info'])

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
