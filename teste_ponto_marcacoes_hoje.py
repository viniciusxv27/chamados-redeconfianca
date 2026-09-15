"""/ponto: as marcações de hoje voltam a aparecer, e a sincronização volta a gravar.

O endpoint novo de marcações (``payssego``, desde o bf794d9) trata a ``endDate``
como EXCLUSIVA: pedindo "até hoje", as batidas de hoje não vinham — nem no
/ponto, nem no widget da home, nem no painel do gestor. Ele também ignora
``pageSize``/``pageNumber`` e, sem ``size``, devolve só as 20 batidas mais
recentes (dizendo que é a última página). O payload não traz id de par, e a
sincronização diária pulava todos os pares. No meio das batidas ainda vêm blocos
de afastamento (meia-noite a meia-noite, repetidos por dia), que não são marcação.

Nada sai daqui: a API do Tangerino é trocada por um dublê que imita o endpoint
(``endDate`` exclusiva, 404 quando não há batida, só obedece ``size``),
``_request`` explode se algo tentar a rede, o cache é de memória e o agendador
do middleware não dispara. Nenhum ponto é batido. Roda dentro de uma transação
desfeita no fim.
"""
import os
import re
import sys
from datetime import datetime, time, timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.cache.backends.locmem import LocMemCache
from django.db import transaction
from django.test import Client
from django.test.utils import override_settings
from django.utils import timezone

from tangerino import client, jornada, ponto, sync
from tangerino.models import MarcacaoPonto

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


EID, EID_SEM_BATIDA = 990000101, 990000102        # não existem no Tangerino
NOME = 'ZZ PESSOA PONTO TESTE'
hoje = timezone.localdate()
ontem = hoje - timedelta(days=1)
anteontem = hoje - timedelta(days=2)
bloco = hoje - timedelta(days=4)                  # afastamento de dois dias inteiros
esquecido = hoje - timedelta(days=5)              # entrou e não bateu a saída
madrugada = hoje - timedelta(days=6)              # par curto que começa à meia-noite
HISTORICO = [hoje - timedelta(days=n) for n in range(8, 20)]   # 12 dias normais, dois pares cada


def ms(dia, hora, minuto=0):
    return int(timezone.make_aware(datetime.combine(dia, time(hora, minuto))).timestamp() * 1000)


def registro(dia_trabalhado, entrada, saida=None, eid=EID):
    """Um registro como o payssego devolve; sem saída, a chave nem vem."""
    r = {'employeeId': eid, 'pis': '00000000000', 'status': 'APPROVED',
         'dateWorked': ms(dia_trabalhado, 0), 'startDateTimestamp': entrada}
    if saida:
        r.update(endDateTimestamp=saida, workedTimeInSeconds=(saida - entrada) // 1000)
    return r


REGISTROS = [
    registro(hoje, ms(hoje, 8, 2), ms(hoje, 12, 0)),
    registro(hoje, ms(hoje, 13, 1)),                                   # voltou e ainda não saiu
    registro(ontem, ms(ontem, 8, 0), ms(ontem, 12, 0)),
    registro(ontem, ms(ontem, 13, 0), ms(ontem, 17, 30)),
    registro(anteontem, ms(anteontem, 22, 22), ms(ontem, 0, 54)),      # vira a meia-noite
    registro(bloco, ms(bloco, 0), ms(bloco + timedelta(days=2), 0)),   # afastamento, um por dia
    registro(bloco + timedelta(days=1), ms(bloco, 0), ms(bloco + timedelta(days=2), 0)),
    registro(esquecido, ms(esquecido, 9, 15)),
    registro(madrugada, ms(madrugada, 0), ms(madrugada, 6, 0)),
] + [registro(dia, ms(dia, ini), ms(dia, fim)) for dia in HISTORICO for ini, fim in ((8, 12), (13, 17))]
REPETIDO = registro(hoje, ms(hoje, 8, 2), ms(hoje, 12, 0))
API = {'repete': True}          # a API já devolveu o mesmo registro duas vezes
PADRAO_DO_SERVIDOR = 20         # sem ``size``, o payssego devolve só as 20 mais recentes
chamadas = []


def api_falsa(base, caminho, params=None):
    """Imita o payssego: endDate exclusiva, 404 sem batida e só obedece ``size``.

    ``pageSize``/``pageNumber``/``page`` são ignorados, e a resposta diz sempre
    ``last=True`` com ``totalElements`` contando só o que veio — como o real.
    """
    params = dict(params or {})
    chamadas.append((caminho, params))
    if caminho == '/employee/find-all':
        return {'content': [{'id': EID, 'name': NOME}, {'id': EID_SEM_BATIDA, 'name': 'ZZ SEM BATIDA'}],
                'totalPages': 1, 'last': True}
    achou = re.fullmatch(r'/external/api/v1/payssego/punches/(\d+)', caminho)
    if not achou:
        raise client.TangerinoError(f'Tangerino respondeu 404 em {caminho}: dublê do teste')
    de = datetime.strptime(params['startDate'], '%d/%m/%Y').date()
    ate = datetime.strptime(params['endDate'], '%d/%m/%Y').date()        # exclusiva, como a real
    fonte = REGISTROS + ([REPETIDO] if API['repete'] else [])
    achados = sorted((r for r in fonte if r['employeeId'] == int(achou.group(1))
                      and de <= client.de_millis(r['dateWorked']).date() < ate),
                     key=lambda r: r['startDateTimestamp'], reverse=True)
    if not achados:
        raise client.TangerinoError(f'Tangerino respondeu 404 em {caminho}: Cant find punches for this employee')
    tamanho = min(int(params.get('size', PADRAO_DO_SERVIDOR)), 2000)
    pagina = achados[:tamanho]
    return {'content': pagina, 'size': tamanho, 'number': 0, 'totalElements': len(pagina),
            'totalPages': 1, 'first': True, 'last': True}


def hhmm(valor):
    if not valor:
        return None
    quando = client.de_millis(valor) if isinstance(valor, int) else timezone.localtime(valor)
    return quando.strftime('%H:%M')


def horarios(pares):
    return sorted(((hhmm(p.get('dateIn')), hhmm(p.get('dateOut'))) for p in pares),
                  key=lambda par: (par[0] or '', par[1] or ''))


marcador = transaction.atomic()
marcador.__enter__()
try:
    assert not User.objects.filter(tangerino_employee_id__in=[EID, EID_SEM_BATIDA]).exists(), \
        'um usuário do banco já usa o employee_id falso do teste'
    assert not MarcacaoPonto.objects.filter(employee_id__in=[EID, EID_SEM_BATIDA]).exists(), \
        'a tabela já tem marcação com o employee_id falso do teste'
    cache_do_teste = LocMemCache('zz-teste-ponto-hoje', {})

    with override_settings(TANGERINO_ENABLED=True, TANGERINO_TOKEN='token-falso-do-teste'), \
            mock.patch.object(client, 'cache', cache_do_teste), \
            mock.patch.object(client, '_request', side_effect=AssertionError('tentou a rede')) as rede, \
            mock.patch.object(client, '_get', side_effect=api_falsa), \
            mock.patch('tangerino.middleware.disparar_se_esta_na_hora', return_value=False):

        print('== O PEDIDO À API ==')
        pares = client.listar_marcacoes(hoje, hoje, employee_id=EID, usar_cache=False)
        pedido = [p for c, p in chamadas if c.endswith(f'/{EID}')][0]
        t('pede a partir de hoje', pedido.get('startDate') == f'{hoje:%d/%m/%Y}', pedido)
        t('e até AMANHÃ, porque a endDate do payssego é exclusiva',
          pedido.get('endDate') == f'{hoje + timedelta(days=1):%d/%m/%Y}', pedido)
        t('com size no máximo (pageSize/pageNumber o payssego ignora)',
          pedido.get('size') == client.MARCACOES_POR_PEDIDO == 2000
          and 'pageSize' not in pedido and 'pageNumber' not in pedido, pedido)
        t('as batidas de hoje vêm (a repetida inclusive, antes da deduplicação)',
          horarios(pares) == [('08:02', '12:00'), ('08:02', '12:00'), ('13:01', None)], horarios(pares))
        t('cada par ganha um id estável: funcionário + entrada',
          all(p['id'] == f"{EID}-{p['dateIn']}" for p in pares), [p['id'] for p in pares])
        t('e a repetição cai no _sem_duplicatas',
          horarios(ponto._sem_duplicatas(pares)) == [('08:02', '12:00'), ('13:01', None)])

        por_dia = {d: client.listar_marcacoes(d, d, employee_id=EID, usar_cache=False)
                   for d in (ontem, anteontem, bloco, madrugada)}
        t('ontem: só os pares de ontem (o dia a mais pedido é recortado)',
          horarios(por_dia[ontem]) == [('08:00', '12:00'), ('13:00', '17:30')], horarios(por_dia[ontem]))
        t('turno que vira a meia-noite fica no dia da entrada',
          horarios(por_dia[anteontem]) == [('22:22', '00:54')], horarios(por_dia[anteontem]))
        t('afastamento de meia-noite a meia-noite não vira marcação',
          por_dia[bloco] == [], horarios(por_dia[bloco]))
        t('mas par curto que começa à meia-noite é batida',
          horarios(por_dia[madrugada]) == [('00:00', '06:00')], horarios(por_dia[madrugada]))
        t('quem não tem batida (404) recebe lista vazia',
          client.listar_marcacoes(hoje, hoje, employee_id=EID_SEM_BATIDA, usar_cache=False) == [])

        print('\n== JANELA LONGA: NADA DE CORTAR NAS 20 MAIS RECENTES ==')
        sem_size = api_falsa('', f'/external/api/v1/payssego/punches/{EID}',
                             {'startDate': f'{hoje - timedelta(days=20):%d/%m/%Y}',
                              'endDate': f'{hoje + timedelta(days=1):%d/%m/%Y}', 'pageSize': 500})
        t('(o dublê imita o servidor: com pageSize, só as 20 mais recentes e "last")',
          len(sem_size['content']) == 20 and sem_size['last'] is True)
        antes = len(chamadas)
        periodo = ponto._sem_duplicatas(
            client.listar_marcacoes(hoje - timedelta(days=20), hoje, employee_id=EID, usar_cache=False))
        dias_vistos = {client.de_millis(p['dateIn']).date() for p in periodo}
        t('20 dias vêm inteiros num pedido só, até o dia mais antigo',
          len(chamadas) - antes == 1 and len(periodo) == 7 + 2 * len(HISTORICO) and set(HISTORICO) <= dias_vistos,
          (len(chamadas) - antes, len(periodo), sorted(set(HISTORICO) - dias_vistos)))
        t('e sem o afastamento', bloco not in dias_vistos)
        with mock.patch.object(client, 'MARCACOES_POR_PEDIDO', 5), \
                mock.patch.object(client.logger, 'warning') as aviso:
            client.listar_marcacoes(hoje - timedelta(days=20), hoje, employee_id=EID, usar_cache=False)
        t('página cheia avisa no log que pode ter faltado batida antiga', aviso.call_count == 1,
          aviso.call_args_list)

        print('\n== O DIA DE HOJE, AS PENDÊNCIAS E O PAINEL ==')
        st = ponto.status_do_dia(EID, dia=hoje)
        t('o status do dia enxerga as batidas de hoje',
          [(e['tipo'], e['quando'].strftime('%H:%M')) for e in st['eventos']]
          == [('ENTRADA', '08:02'), ('SAIDA', '12:00'), ('ENTRADA', '13:01')], st['eventos'])
        t('e mostra a pessoa trabalhando, de volta do intervalo',
          st['situacao'] == 'TRABALHANDO' and st['dentro'] and st['voltou_almoco'], st['rotulo'])
        pend = ponto.pendencias(EID)
        t('a saída esquecida vira pendência; a entrada aberta de hoje, não',
          [p['dia'] for p in pend] == [esquecido], pend)
        painel = ponto.painel_da_empresa(hoje)
        t('painel do gestor: quem bateu hoje aparece trabalhando',
          EID in painel and painel[EID]['dentro'] is True, sorted(painel))
        t('e quem não tem batida hoje fica de fora', EID_SEM_BATIDA not in painel)

        print('\n== A TELA /ponto/ ==')
        API['repete'] = False
        cache_do_teste.clear()
        pessoa = User.objects.create_user(
            username='zzponto.pessoa', email='zzponto.pessoa@exemplo-teste.local', password='S3nha!teste',
            first_name='ZZ', last_name='Ponto', is_superuser=True, is_staff=True)
        pessoa.tangerino_employee_id = EID
        pessoa.save(update_fields=['tangerino_employee_id'])
        widget = ponto.resumo_para_usuario(pessoa)
        t('o widget da home também vê a entrada de hoje',
          widget.get('disponivel') is True and widget.get('bateu_entrada') is True, widget.get('motivo'))
        navegador = Client()
        navegador.force_login(pessoa)
        r = navegador.get('/ponto/')
        html = r.content.decode()
        t('abre (200)', r.status_code == 200, r.status_code)
        t('sem erro do Tangerino na tela', 'Tangerino respondeu' not in html and 'tentou a rede' not in html)
        trecho = (html.split('Marcações de hoje', 1)[1].split('<!-- Pendências -->', 1)[0]
                  if 'Marcações de hoje' in html else '')
        batidas = re.findall(r'tabular">(\d\d:\d\d)</p>', trecho)
        t('"Marcações de hoje" mostra as três batidas', batidas == ['08:02', '12:00', '13:01'], batidas)
        t('com a saída ainda por registrar', 'Saída ainda não registrada' in trecho)
        t('e não diz mais "Nenhuma marcação hoje ainda."', 'Nenhuma marcação hoje ainda.' not in html)
        t('a pendência da saída esquecida aparece', 'Entrada às 09:15, sem saída' in html)
        ultimos = html.split('Últimos 28 dias', 1)[1] if 'Últimos 28 dias' in html else ''
        chips = re.findall(r'px-2 py-0\.5 rounded text-xs tabular[^"]*">\s*(\d\d:\d\d)\s*</span>', ultimos)
        t('os últimos 28 dias trazem hoje e ontem', '13:01' in chips and '17:30' in chips, chips)
        t('e os dias mais antigos também (antes cortava nas 20 batidas mais recentes)',
          chips.count('17:00') == len(HISTORICO), chips.count('17:00'))
        t('e nenhum afastamento aparece como 00:00 (só a batida da madrugada)', chips.count('00:00') == 1, chips)

        print('\n== A SINCRONIZAÇÃO VOLTA A GRAVAR ==')
        API['repete'] = True
        cache_do_teste.clear()
        antiga = MarcacaoPonto.objects.create(
            employee_id=EID, data=ontem, nome='', total_segundos=60, editado=True, plataforma='MOBILE',
            sincronizado_em=timezone.now() - timedelta(days=1))
        with mock.patch.object(sync, '_grades_por_funcionario', return_value={}), \
                mock.patch.object(jornada, 'carregar_abonos', return_value={}), \
                mock.patch.object(sync, 'recalcular_previsto', return_value=0):
            res = sync.sincronizar_marcacoes(dias=7, employee_id=EID)
            linhas = {m.data: m for m in MarcacaoPonto.objects.filter(employee_id=EID)}
            t('grava os dias com batida (antes pulava todos por falta de id)',
              set(linhas) == {hoje, ontem, anteontem, esquecido, madrugada}, sorted(linhas))
            t('4 dias novos e 1 atualizado', res.get('criados') == 4 and res.get('atualizados') == 1, res)
            d = linhas.get(hoje)
            t('hoje: 08:02–12:00 e 13:01 em aberto',
              d is not None and (hhmm(d.entrada1), hhmm(d.saida1), hhmm(d.entrada2), d.saida2)
              == ('08:02', '12:00', '13:01', None) and d.em_aberto,
              d and (hhmm(d.entrada1), hhmm(d.saida1), hhmm(d.entrada2), d.saida2, d.em_aberto))
            t('a batida repetida pela API não vira um terceiro par',
              d is not None and d.entrada3 is None and len(d.tangerino_ids) == 2, d and d.tangerino_ids)
            t('o nome vem do cadastro (o payload novo não traz)', d is not None and d.nome == NOME, d and d.nome)
            t('ligado ao usuário do portal', d is not None and d.usuario_id == pessoa.id)
            d = linhas.get(ontem)
            t('ontem: a linha que já existia é atualizada (8h30 trabalhadas)',
              d is not None and d.pk == antiga.pk and d.total_segundos == 8 * 3600 + 30 * 60,
              d and d.total_segundos)
            t('sem apagar o "editado", que o endpoint novo não informa', d is not None and d.editado is True)
            t('nem a plataforma', d is not None and d.plataforma == 'MOBILE', d and d.plataforma)
            t('e ganha o nome que estava vazio', d is not None and d.nome == NOME)
            d = linhas.get(anteontem)
            t('turno que vira a meia-noite: 2h32 no dia da entrada',
              d is not None and d.total_segundos == 2 * 3600 + 32 * 60, d and d.total_segundos)
            d = linhas.get(esquecido)
            t('saída esquecida: dia em aberto', d is not None and d.em_aberto and d.saida1 is None)
            t('afastamento não vira dia trabalhado',
              bloco not in linhas and bloco + timedelta(days=1) not in linhas)

            res = sync.sincronizar_marcacoes(dias=7)
            t('a rodada diária (empresa inteira) repete sem duplicar',
              res.get('criados') == 0 and res.get('atualizados') == 5
              and MarcacaoPonto.objects.filter(employee_id=EID).count() == 5, res)
            t('quem não tem batida não ganha linha',
              not MarcacaoPonto.objects.filter(employee_id=EID_SEM_BATIDA).exists())

        print('\n== NADA SAIU DAQUI ==')
        t('nenhuma chamada de verdade ao Tangerino', rede.call_count == 0, rede.call_count)
        consultados = {c.rsplit('/', 1)[-1] for c, _ in chamadas if 'payssego' in c}
        t('só os funcionários do dublê foram consultados',
          consultados <= {str(EID), str(EID_SEM_BATIDA)}, consultados)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
