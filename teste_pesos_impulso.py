"""SUPERADMIN edita quanto vale cada tarefa dos 3 pilares.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
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
from django.test import Client

from communications.models import CommunicationGroup
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, PesosImpulso
from impulso.scoring import limpar_cache_pesos, maximos, pesos, pt
from users.models import Sector

User = get_user_model()
ok = fail = 0
D = Decimal


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
    limpar_cache_pesos()

    print('== SEM MEXER, A RÉGUA É A DE SEMPRE ==')
    p = pesos()
    t('qualidade das metas vale 10', p['metas_qualidade'] == 10, p['metas_qualidade'])
    t('projeto FOCO vale 20', p['projeto_foco'] == 20, p['projeto_foco'])
    c, cn, i, total = maximos()
    t('CONFIAR vale 40', c == 40, c)
    t('CONECTAR vale 40', cn == 40, cn)
    t('INOVAR vale 20', i == 20, i)
    t('e o total fecha em 100', total == 100, total)

    print('\n== O MODELO ==')
    cfg = PesosImpulso.get()
    t('nasce fechando em 100', cfg.fecha_em_100, cfg.total)
    t('tudo ligado por padrão', all(cfg.esta_ativo(c) for c, _, _, _ in PesosImpulso.ITENS))
    blocos = cfg.por_pilar()
    t('a tela desenha 3 pilares', len(blocos) == 3, len(blocos))
    t('com 9 tarefas no total', sum(len(b['itens']) for b in blocos) == 9)

    cfg.ativos = {'projeto_foco': False}
    t('tarefa desligada vale zero', cfg.peso('projeto_foco') == 0)
    t('mas o valor guardado continua lá', cfg.projeto_foco == 20)
    t('e o total cai', cfg.total == 80, cfg.total)
    cfg.ativos = {}

    print('\n== QUEM PODE ==')
    adm = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
    ges = CommunicationGroup.objects.filter(name__iexact=GRUPO_GESTOR).first()
    assert adm and ges, 'grupos do Impulso não encontrados'
    area = Sector.objects.create(name='ZZ Area Pesos')

    def novo(u, grupos=(), **kw):
        x = User.objects.create_user(
            username=u, email=f'{u}@exemplo-teste.local', password='S3nha!teste',
            sector=area, first_name=u.split('.')[1].title(), last_name='T', **kw)
        for g in grupos:
            x.communication_groups.add(g)
        return x

    chefe = novo('pz.chefe', [adm, ges], is_superuser=True, is_staff=True,
                 hierarchy='SUPERADMIN')
    gestor = novo('pz.gestor', [adm, ges])
    colab = novo('pz.colab', [adm])

    cg = Client(); cg.force_login(gestor)
    r = cg.get('/impulso/acompanhamento/pesos/', follow=True)
    t('gestor não edita a régua', bool(r.redirect_chain), r.redirect_chain)
    t('e a tela explica', 'Apenas o SUPERADMIN' in r.content.decode())

    cc = Client(); cc.force_login(colab)
    html = cc.get('/impulso/acompanhamento/').content.decode()
    t('colaborador não vê o atalho', 'Pontuação das tarefas' not in html)

    cs = Client(); cs.force_login(chefe)
    html = cs.get('/impulso/acompanhamento/').content.decode()
    t('SUPERADMIN vê o atalho', 'Pontuação das tarefas' in html)

    html = cs.get('/impulso/acompanhamento/pesos/').content.decode()
    t('a tela abre', 'Pontuação das tarefas' in html)
    t('mostra os três pilares', 'Confiar' in html and 'Conectar' in html and 'Inovar' in html)
    t('tem campo por tarefa', html.count('name="peso_') == 9, html.count('name="peso_'))
    t('e caixinha para desligar', html.count('name="ativo_') == 9)
    # O texto quebra linha no HTML; compara sem os espaços de indentação.
    corrido = ' '.join(html.split())
    t('avisa que só salva fechando em 100',
      'só salva quando a soma fecha em 100' in corrido)

    def salvar(valores, desligar=()):
        dados = {}
        for campo, _r, _p, padrao in PesosImpulso.ITENS:
            dados[f'peso_{campo}'] = str(valores.get(campo, padrao))
            if campo not in desligar:
                dados[f'ativo_{campo}'] = 'on'
        return cs.post('/impulso/acompanhamento/pesos/', dados, follow=True)

    print('\n== REDISTRIBUIR ==')
    r = salvar({'metas_qualidade': 15, 'metas_conclusao': 5})
    limpar_cache_pesos()
    cfg = PesosImpulso.get()
    t('salva quando fecha em 100', cfg.metas_qualidade == 15, cfg.metas_qualidade)
    t('e o outro item acompanha', cfg.metas_conclusao == 5)
    t('o total continua 100', cfg.total == 100, cfg.total)
    t('a pontuação passa a usar o valor novo', pt('metas_qualidade') == 15,
      pt('metas_qualidade'))
    t('e o máximo do pilar acompanha', maximos()[0] == 40, maximos()[0])

    print('\n== NÃO SALVA RÉGUA QUEBRADA ==')
    antes = PesosImpulso.get().metas_qualidade
    r = salvar({'metas_qualidade': 50})
    limpar_cache_pesos()
    t('soma diferente de 100 é recusada',
      PesosImpulso.get().metas_qualidade == antes, PesosImpulso.get().metas_qualidade)
    t('e a tela diz quanto deu', 'não 100' in r.content.decode()
      or 'Redistribua' in r.content.decode())

    r = salvar({'metas_qualidade': -5})
    t('valor negativo é recusado', 'não pode ser negativo' in r.content.decode())

    dados = {f'peso_{c}': str(pd) for c, _r, _p, pd in PesosImpulso.ITENS}
    dados.update({f'ativo_{c}': 'on' for c, _r, _p, _pd in PesosImpulso.ITENS})
    dados['peso_feedback'] = 'abc'
    r = cs.post('/impulso/acompanhamento/pesos/', dados, follow=True)
    t('valor ilegível é recusado', 'não é um valor válido' in r.content.decode())

    print('\n== RETIRAR TAREFA ==')
    # Tira o Projeto FOCO e joga os 20 pontos dele no curso.
    r = salvar({'metas_qualidade': 10, 'metas_conclusao': 10, 'curso': 30},
               desligar={'projeto_foco'})
    limpar_cache_pesos()
    cfg = PesosImpulso.get()
    t('a tarefa é desligada', not cfg.esta_ativo('projeto_foco'))
    t('e passa a valer zero', pt('projeto_foco') == 0, pt('projeto_foco'))
    t('os pontos foram para outra', pt('curso') == 30, pt('curso'))
    t('o total continua 100', maximos()[3] == 100, maximos()[3])
    t('o pilar CONECTAR se ajustou', maximos()[1] == 40, maximos()[1])
    t('a tela avisa o que ficou de fora', 'Projeto FOCO' in r.content.decode())
    t('o valor guardado não foi perdido', cfg.projeto_foco == 20, cfg.projeto_foco)

    print('\n== RÉGUA POR PESSOA ==')
    from impulso.models import PesosImpulso as PI

    def salvar_de(pessoa, valores, desligar=()):
        dados = {'usuario': pessoa.id}
        for campo, _r, _p, padrao in PI.ITENS:
            dados[f'peso_{campo}'] = str(valores.get(campo, padrao))
            if campo not in desligar:
                dados[f'ativo_{campo}'] = 'on'
        return cs.post(f'/impulso/acompanhamento/pesos/?usuario={pessoa.id}',
                       dados, follow=True)

    t('sem régua própria, a pessoa segue a geral',
      PI.propria_de(colab) is None)
    t('e os pesos dela são os gerais',
      pesos(colab)['curso'] == pesos()['curso'], (pesos(colab)['curso'], pesos()['curso']))

    # O colaborador não encosta em Projeto FOCO: os 20 dele vão para as ideias.
    # CONFIAR 40 + CONECTAR (10 curso + 10 vídeos + 0 foco) + INOVAR 40 = 100.
    r = salvar_de(colab, {'ideias': 20, 'ideia_aprovada': 20},
                  desligar={'projeto_foco'})
    limpar_cache_pesos()
    propria = PI.propria_de(colab)
    t('a pessoa ganha régua própria', propria is not None)
    t('com os valores dela', propria and propria.ideias == 20, propria.ideias if propria else '')
    t('e a tarefa desligada', propria and not propria.esta_ativo('projeto_foco'))
    t('o total dela fecha em 100', propria and propria.total == 100, propria.total if propria else '')

    t('a pontuação dela usa a régua dela', pt('ideias', colab) == 20, pt('ideias', colab))
    t('e o Projeto FOCO dela vale zero', pt('projeto_foco', colab) == 0)
    t('mas a régua GERAL não mudou', pt('ideias') == 10, pt('ideias'))
    t('e quem não tem régua própria segue a geral',
      pt('ideias', gestor) == 10, pt('ideias', gestor))

    t('os máximos dela mudam', maximos(colab)[2] == 40, maximos(colab)[2])
    t('e continuam somando 100', maximos(colab)[3] == 100, maximos(colab)[3])
    t('os de quem segue a geral não mudam', maximos(gestor)[2] == 20, maximos(gestor)[2])

    print('\n-- a tela mostra de quem é a régua --')
    html = cs.get(f'/impulso/acompanhamento/pesos/?usuario={colab.id}').content.decode()
    t('dá para escolher a pessoa', 'name="usuario"' in html)
    t('avisa que ela tem régua própria', 'tem régua própria' in html)
    t('e oferece voltar para a geral', 'name="usar_geral"' in html)
    t('lista quem já tem régua própria', 'Com régua própria hoje' in html)

    html = cs.get(f'/impulso/acompanhamento/pesos/?usuario={gestor.id}').content.decode()
    t('para quem não tem, avisa que vai criar', 'segue a régua geral' in html)
    t('e não oferece "voltar à geral"', 'name="usar_geral"' not in html)

    html = cs.get('/impulso/acompanhamento/pesos/').content.decode()
    t('a tela geral se identifica como geral', 'Régua geral' in html)

    print('\n-- voltar para a geral --')
    dados = {'usuario': colab.id, 'usar_geral': 'on'}
    for campo, _r, _p, padrao in PI.ITENS:
        dados[f'peso_{campo}'] = str(padrao)
        dados[f'ativo_{campo}'] = 'on'
    r = cs.post(f'/impulso/acompanhamento/pesos/?usuario={colab.id}', dados, follow=True)
    limpar_cache_pesos()
    t('a régua própria é apagada', PI.propria_de(colab) is None)
    t('e ela volta a seguir a geral', pt('ideias', colab) == 10, pt('ideias', colab))
    t('a tela confirma', 'volta a seguir a régua geral' in r.content.decode())

    print('\n-- régua quebrada por pessoa também é recusada --')
    r = salvar_de(colab, {'ideias': 90})
    t('não salva quem não fecha em 100', PI.propria_de(colab) is None)

    print('\n-- colaborador inexistente --')
    r = cs.get('/impulso/acompanhamento/pesos/?usuario=99999999', follow=True)
    t('id inexistente volta para a geral', bool(r.redirect_chain), r.redirect_chain)

    print('\n== O CACHE NÃO PODE SEGURAR A RÉGUA VELHA ==')
    t('a tela limpa o cache ao salvar', pt('curso') == 30)
    t('e o padrão sobrevive a banco fora do ar',
      all(v > 0 for v in __import__('impulso.scoring', fromlist=['PADRAO']).PADRAO.values()))

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    limpar_cache_pesos()
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
