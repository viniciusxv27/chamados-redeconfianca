"""Impulso: "Vídeos e POPs" só pontua o que tem o período inteiro dentro do mês.

Pedido (05/10/2026), em /impulso/acompanhamento/: "a pontuação de 'Vídeos e
POPs' deve conter somente quando for vídeo e POP com o período inicial e o
período final estando dentro do mês da pontuação".

Antes valia a regra do curso — criado até o fim do mês e ainda não encerrado —,
então POP sem período era cobrado todo mês, para sempre, e o que atravessava
meses contava nos dois. O curso continua com a regra dele.

Tudo numa transação desfeita. Usa um mês distante (03/2031) para nenhum
conteúdo real cair no período testado.
"""
import os
import sys
from datetime import date

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.urls import reverse

from impulso.models import ConclusaoConteudo, ConteudoConectar as C
from impulso.scoring import _conteudos_do_usuario, calcular_pontuacao, linhas_detalhadas

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


INI, FIM = date(2031, 3, 1), date(2031, 3, 31)

marcador = transaction.atomic()
marcador.__enter__()
try:
    pessoa = User.objects.create_user(username='zz.vp.pessoa', email='zz.vp@exemplo-teste.local',
                                      password='x', first_name='Pessoa', last_name='Teste')
    outra = User.objects.create_user(username='zz.vp.outra', email='zz.vp2@exemplo-teste.local', password='x')

    def novo(titulo, tipo=C.Tipo.POP, inicio=None, fim=None, para=None, **kw):
        c = C.objects.create(tipo=tipo, titulo=f'ZZ {titulo}', inicio=inicio, fim=fim, **kw)
        c.obrigatorio_para.set([para or pessoa])
        return c

    dentro_pop = novo('POP dentro do mês', inicio=date(2031, 3, 1), fim=date(2031, 3, 31))
    dentro_video = novo('Vídeo dentro do mês', tipo=C.Tipo.VIDEO, inicio=date(2031, 3, 10), fim=date(2031, 3, 15))
    um_dia = novo('POP de um dia só', inicio=date(2031, 3, 20), fim=date(2031, 3, 20))
    sem_periodo = novo('POP sem período')
    so_inicio = novo('POP só com início', inicio=date(2031, 3, 5))
    so_fim = novo('POP só com fim', fim=date(2031, 3, 25))
    atravessa_antes = novo('Vídeo que começa no mês anterior', tipo=C.Tipo.VIDEO,
                           inicio=date(2031, 2, 20), fim=date(2031, 3, 10))
    atravessa_depois = novo('POP que termina no mês seguinte', inicio=date(2031, 3, 25), fim=date(2031, 4, 5))
    mes_anterior = novo('POP do mês anterior', inicio=date(2031, 2, 1), fim=date(2031, 2, 28))
    mes_seguinte = novo('POP do mês seguinte', inicio=date(2031, 4, 1), fim=date(2031, 4, 30))
    inativo = novo('POP inativo', inicio=date(2031, 3, 1), fim=date(2031, 3, 31), ativo=False)
    opcional = novo('POP não obrigatório', inicio=date(2031, 3, 1), fim=date(2031, 3, 31), obrigatorio=False)
    de_outra = novo('POP de outra pessoa', inicio=date(2031, 3, 1), fim=date(2031, 3, 31), para=outra)
    curso_sem_periodo = novo('Curso sem período', tipo=C.Tipo.CURSO)

    print('== O QUE CONTA EM 03/2031 ==')
    contam = set(_conteudos_do_usuario(pessoa, [C.Tipo.VIDEO, C.Tipo.POP], INI, FIM, True))
    t('POP com início e fim dentro do mês conta', dentro_pop in contam)
    t('vídeo com início e fim dentro do mês conta', dentro_video in contam)
    t('período de um dia só dentro do mês conta', um_dia in contam)
    t('sem período não conta (antes contava todo mês)', sem_periodo not in contam)
    t('só com início não conta', so_inicio not in contam)
    t('só com fim não conta', so_fim not in contam)
    t('começando no mês anterior não conta', atravessa_antes not in contam)
    t('terminando no mês seguinte não conta', atravessa_depois not in contam)
    t('período do mês anterior não conta', mes_anterior not in contam)
    t('período do mês seguinte não conta', mes_seguinte not in contam)
    t('inativo e não obrigatório continuam fora', inativo not in contam and opcional not in contam)
    t('o de outra pessoa continua fora', de_outra not in contam)
    t('só esses três', contam == {dentro_pop, dentro_video, um_dia}, sorted(c.titulo for c in contam))

    print('\n== QUE ATRAVESSA MESES NÃO CONTA EM NENHUM DELES ==')
    fev = set(_conteudos_do_usuario(pessoa, [C.Tipo.VIDEO, C.Tipo.POP], date(2031, 2, 1), date(2031, 2, 28), True))
    abr = set(_conteudos_do_usuario(pessoa, [C.Tipo.VIDEO, C.Tipo.POP], date(2031, 4, 1), date(2031, 4, 30), True))
    t('fevereiro só tem o POP de fevereiro', fev == {mes_anterior}, sorted(c.titulo for c in fev))
    t('abril só tem o POP de abril', abr == {mes_seguinte}, sorted(c.titulo for c in abr))

    print('\n== A NOTA ==')
    ConclusaoConteudo.objects.create(conteudo=dentro_pop, user=pessoa, concluido=True,
                                     aprovacao=ConclusaoConteudo.Aprovacao.APROVADA)
    # Concluir o que não conta não pode empurrar a nota.
    ConclusaoConteudo.objects.create(conteudo=sem_periodo, user=pessoa, concluido=True,
                                     aprovacao=ConclusaoConteudo.Aprovacao.APROVADA)
    ConclusaoConteudo.objects.create(conteudo=atravessa_depois, user=pessoa, concluido=True,
                                     aprovacao=ConclusaoConteudo.Aprovacao.APROVADA)
    dados = calcular_pontuacao(pessoa, inicio=INI, fim=FIM)
    vp = dados['detalhes']['videos_pops']
    t('1 de 3 concluído', (vp.get('concluidos'), vp.get('total')) == (1, 3), vp)
    t('nota proporcional: 10 × 1/3', dados['p_videos_pops'] == Decimal('3.33'), dados['p_videos_pops'])
    linha = next(l for l in linhas_detalhadas(dados) if l['item'] == 'Vídeos e POPs')
    t('o detalhamento mostra a conta', linha['info'] == '1 de 3 concluído(s)', linha['info'])

    vazio = calcular_pontuacao(pessoa, inicio=date(2031, 5, 1), fim=date(2031, 5, 31))
    linha = next(l for l in linhas_detalhadas(vazio) if l['item'] == 'Vídeos e POPs')
    t('mês sem nenhum: zero, e o detalhamento explica o porquê',
      vazio['p_videos_pops'] == 0 and 'período dentro do mês' in linha['info'], linha['info'])

    print('\n== O CURSO NÃO MUDOU ==')
    cursos = set(_conteudos_do_usuario(pessoa, [C.Tipo.CURSO], INI, FIM))
    t('curso sem período continua valendo (regra dele)', curso_sem_periodo in cursos)
    t('e não entra em Vídeos e POPs', curso_sem_periodo not in contam)

    print('\n== O FORMULÁRIO AVISA ==')
    gestor = User.objects.create_user(username='zz.vp.gestor', email='zz.vpg@exemplo-teste.local',
                                      password='x', is_superuser=True)
    c = Client()
    c.force_login(gestor)
    resp = c.get(reverse('impulso:conteudo_create'))
    html = resp.content.decode()
    t('o cadastro abre', resp.status_code == 200, resp.status_code)
    t('tem o aviso de período para Vídeo e POP', 'data-periodo-aviso' in html
      and 'não entra na pontuação de nenhum mês' in html)
    resp = c.get(reverse('impulso:conteudo_editar', args=[dentro_pop.id]))
    t('a edição também', resp.status_code == 200 and 'data-periodo-aviso' in resp.content.decode(),
      resp.status_code)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)

print(f'\n{ok} OK / {fail} falhas — rollback: nada deste teste ficou no banco.')
sys.exit(1 if fail else 0)
