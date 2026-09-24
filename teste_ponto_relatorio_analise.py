"""Ponto: relatório exportável e análise das divergências no WhatsApp.

Pedido: exportar relatório da folha de ponto com nome, loja, data, as quatro
batidas, horas previstas, trabalhadas, intervalo, extras e a pendência em texto
(filtrando por período, setor e pessoa); e mandar uma análise por WhatsApp —
diária (dia anterior), semanal (segunda, da semana passada) e mensal (último dia
útil) — para quem o SUPERADMIN escolher, agrupada por loja.

O que este teste cobre:

- as pendências macro de cada dia (sem batida, batida faltando, almoço curto,
  dia sem intervalo, dia em aberto, mais de 4 batidas) e as colunas de horas;
- o dia sem batida nenhuma, que não existe na tabela e sai do previsto (jornada
  contratada, escala do portal ou o costume da pessoa);
- a tela /ponto/relatorio/ com os filtros, quem entra nela e o .xlsx;
- os períodos das três cadências, o texto agrupado por loja e o envio: nasce
  desligada, não repete, pula quem está sem telefone e não reivindica nada num
  servidor sem a Evolution configurada.

Relógio congelado, caches em memória, Tangerino e envio trocados por dublês:
nenhuma chamada sai para fora. Transação desfeita no fim.
"""
import os
import sys
from datetime import date, datetime, time, timedelta
from io import BytesIO
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

# Nada deste teste vai para o Redis compartilhado.
settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ponto'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ponto-local'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client, override_settings
from django.utils import timezone
from openpyxl import load_workbook

import core.evolution as evolution
from tangerino import client
from tangerino import feriados
from folhaponto.models import FolhaPontoManagerPermission
from tangerino import analise as analise_svc
from tangerino import pendencias as svc
from tangerino import relatorio as relatorio_svc
from tangerino import sync as sync_svc
from tangerino.agendador import esta_na_hora_da_analise
from tangerino.models import (AnalisePontoConfig, CoberturaPonto, ConfiguracaoTangerino,
                              EnvioAnalisePonto, Escala, EscalaDia, MarcacaoPonto,
                              SincronizacaoTangerino)
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


class Envio:
    """Dublê do envio pela Evolution: guarda o que seria mandado."""

    def __init__(self):
        self.chamadas = []

    def __call__(self, numero, texto, **kwargs):
        self.chamadas.append((numero, texto))
        return True, '{"key":{"id":"ZZ"}}'


def rede_proibida(*args, **kwargs):
    raise AssertionError('o teste tentou falar com a Evolution de verdade')


SEGUNDA = date(2026, 3, 2)                 # semana antiga: sem dado real por perto
TERCA, QUARTA = SEGUNDA + timedelta(days=1), SEGUNDA + timedelta(days=2)
QUINTA = SEGUNDA + timedelta(days=3)
DOMINGO = SEGUNDA + timedelta(days=6)
FUSO = timezone.get_current_timezone()
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def as_(dia, h, m=0):
    return timezone.make_aware(datetime.combine(dia, time(h, m)), FUSO)


envio = Envio()
marcador = transaction.atomic()
marcador.__enter__()
try:
    assert not User.objects.filter(username__startswith='zzpt.').exists(), 'usuários do teste já existem'

    with mock.patch.object(evolution, 'enviar_texto', envio), \
            mock.patch.object(evolution.urlrequest, 'urlopen', rede_proibida), \
            mock.patch.object(svc, 'grades_do_tangerino', lambda: {}):

        loja_a = Sector.objects.create(name='ZZ Loja Alfa')
        loja_b = Sector.objects.create(name='ZZ Loja Beta')

        def pessoa(apelido, eid, setor, fone='', hierarquia='PADRAO'):
            return User.objects.create_user(
                username=f'zzpt.{apelido}', email=f'zzpt.{apelido}@exemplo-teste.local',
                password='S3nha!teste', first_name=apelido.title(), last_name='Teste',
                sector=setor, phone=fone, hierarchy=hierarquia, tangerino_employee_id=eid)

        ana = pessoa('ana', 990101, loja_a)
        bruno = pessoa('bruno', 990102, loja_a)
        carla = pessoa('carla', 990103, loja_b)
        chefe = pessoa('chefe', 990104, loja_b, fone='27 99999-1010', hierarquia='SUPERADMIN')
        gestor_folha = pessoa('gestorfolha', 990105, loja_b)
        comum = pessoa('comum', 990106, loja_a)
        sem_fone = pessoa('semfone', 990107, loja_b)
        FolhaPontoManagerPermission.objects.create(user=gestor_folha)

        # A cobertura é o que diz "este período já foi buscado no Tangerino".
        # Sem ela, o relatório não acusa falta (e nem vai na API, que aqui não
        # pode ser chamada): é a diferença entre "não bateu" e "ninguém olhou".
        TODOS = [ana, bruno, carla, chefe, gestor_folha, comum, sem_fone]
        CoberturaPonto.registrar([p.tangerino_employee_id for p in TODOS],
                                 SEGUNDA - timedelta(days=60), DOMINGO + timedelta(days=60))

        def marcar(usuario, dia, pares, previsto=8 * 3600, aberto=False):
            campos = {}
            total = 0
            for i, (entrada, saida) in enumerate(pares, start=1):
                campos[f'entrada{i}'] = as_(dia, *entrada) if entrada else None
                campos[f'saida{i}'] = as_(dia, *saida) if saida else None
                if entrada and saida:
                    total += int((as_(dia, *saida) - as_(dia, *entrada)).total_seconds())
            return MarcacaoPonto.objects.create(
                employee_id=usuario.tangerino_employee_id, usuario=usuario, nome=usuario.full_name,
                data=dia, total_segundos=total, previsto_segundos=previsto, em_aberto=aberto,
                sincronizado_em=timezone.now(), **campos)

        # Segunda-feira
        marcar(ana, SEGUNDA, [((8, 0), (12, 0)), ((13, 0), (17, 0))])                 # dia certo
        marcar(bruno, SEGUNDA, [((8, 0), None)], aberto=True)                          # entrada sem saída
        marcar(carla, SEGUNDA, [((8, 0), (12, 0)), ((12, 30), (17, 0))])               # almoço de 30 min
        # Terça
        marcar(bruno, TERCA, [((8, 0), (14, 0))])                                      # sem intervalo
        # Quarta: a Ana bateu quatro vezes e ainda um terceiro par
        marcar(ana, QUARTA, [((8, 0), (12, 0)), ((13, 0), (17, 0)), ((18, 0), (19, 0))])

        # A Ana não bateu nada na terça, mas a escala do portal diz que ela trabalhava.
        escala = Escala.objects.create(colaborador=ana, semana_inicio=SEGUNDA)
        EscalaDia.objects.create(escala=escala, data=TERCA, entrada=time(8, 0),
                                 saida_almoco=time(12, 0), volta_almoco=time(13, 0), saida=time(17, 0))

        print('== AS PENDÊNCIAS DE CADA DIA ==')
        linhas = svc.linhas_do_periodo(SEGUNDA, DOMINGO, setor_id=loja_a.id)
        por_chave = {(l['nome'], l['data']): l for l in linhas}
        dia_ana = por_chave[(ana.full_name, SEGUNDA)]
        t('dia completo não tem pendência, e as colunas batem',
          dia_ana['pendencias'] == [] and dia_ana['batidas'] == ['08:00', '12:00', '13:00', '17:00']
          and (dia_ana['previsto'], dia_ana['trabalhado'], dia_ana['intervalo'], dia_ana['horas_extras'])
          == ('08:00', '08:00', '01:00', '00:00'), dia_ana)
        dia_bruno = por_chave[(bruno.full_name, SEGUNDA)]
        t('entrada sem saída: falta a 2ª batida', dia_bruno['pendencias'] == ['Faltou a 2ª batida (saída)'],
          dia_bruno['pendencias'])
        t('e o dia aparece com a 1ª batida preenchida e as outras vazias',
          dia_bruno['batidas'] == ['08:00', '', '', ''])
        dia_terca = por_chave[(bruno.full_name, TERCA)]
        t('duas batidas num dia acima de 6 horas: sem intervalo registrado',
          dia_terca['pendencias'] == ['Sem intervalo registrado (faltaram a 3ª e a 4ª batidas)'],
          dia_terca['pendencias'])
        sem_batida = por_chave[(ana.full_name, TERCA)]
        t('dia sem batida nenhuma vira linha pela escala do portal',
          sem_batida['pendencias'] == ['Não houve nenhuma batida no dia']
          and sem_batida['previsto'] == '08:00' and sem_batida['trabalhado'] == '00:00', sem_batida)
        terceiro_par = por_chave[(ana.full_name, QUARTA)]
        t('terceiro par entra na pendência, com os horários',
          terceiro_par['pendencias'] == ['Mais de 4 batidas no dia (18:00, 19:00)'],
          terceiro_par['pendencias'])
        t('e as horas extras saem do que passou do previsto',
          terceiro_par['trabalhado'] == '09:00' and terceiro_par['horas_extras'] == '01:00',
          (terceiro_par['trabalhado'], terceiro_par['horas_extras']))

        beta = {(l['nome'], l['data']): l for l in svc.linhas_do_periodo(SEGUNDA, DOMINGO, setor_id=loja_b.id)}
        almoco_curto = beta[(carla.full_name, SEGUNDA)]
        t('almoço abaixo de uma hora é pendência, com os minutos',
          almoco_curto['pendencias'] == ['Almoço inferior a uma hora (30 min)']
          and almoco_curto['intervalo'] == '00:30', almoco_curto['pendencias'])
        t('dia sem batida e sem previsão nenhuma não vira linha (folga)',
          (carla.full_name, TERCA) not in beta)

        curto = MarcacaoPonto.objects.filter(pk=MarcacaoPonto.objects.get(
            employee_id=carla.tangerino_employee_id, data=SEGUNDA).pk)
        curto.update(previsto_segundos=4 * 3600)
        recalculado = {(l['nome'], l['data']): l for l in
                       svc.linhas_do_periodo(SEGUNDA, SEGUNDA, setor_id=loja_b.id)}
        t('num dia de 4 horas, almoço curto não é cobrado (a CLT só exige acima de 6h)',
          recalculado[(carla.full_name, SEGUNDA)]['pendencias'] == [])
        curto.update(previsto_segundos=8 * 3600)

        print('\n== O PREVISTO DE UM DIA SEM BATIDA ==')
        with mock.patch.object(svc, 'grades_do_tangerino',
                               lambda: {carla.tangerino_employee_id: {4: 8 * 3600}}):   # 4 = quarta
            com_jornada = {(l['nome'], l['data']): l for l in
                           svc.linhas_do_periodo(QUARTA, QUARTA, setor_id=loja_b.id)}
        t('a jornada contratada do Tangerino também acusa o dia sem batida',
          com_jornada[(carla.full_name, QUARTA)]['pendencias'] == ['Não houve nenhuma batida no dia'],
          com_jornada.get((carla.full_name, QUARTA)))

        print('\n== O QUE AINDA NÃO FOI BUSCADO NO TANGERINO ==')
        # A sincronização diária só volta 30 dias. Pedir um período mais antigo
        # dava "não houve nenhuma batida" em dia trabalhado — 102 dos 135 dias
        # no relatório que mostrou o problema.
        VELHO = SEGUNDA - timedelta(days=120)
        t('o dia fora da cobertura não vira linha (não sei ≠ faltou)',
          svc.linhas_do_periodo(VELHO, VELHO, setor_id=loja_a.id) == [])

        chamadas = []

        def sincronizacao_falsa(inicio, fim, employee_ids=None):
            chamadas.append((inicio, fim, sorted(employee_ids or [])))
            CoberturaPonto.registrar(employee_ids or [], inicio, fim)
            marcar(bruno, VELHO, [((8, 0), (14, 0))])
            return {'dias': 1}

        with mock.patch('core.utils.processo_de_teste', lambda: False), \
                mock.patch.object(sync_svc, 'sincronizar_periodo', sincronizacao_falsa):
            aviso = {}
            linhas_velhas = svc.linhas_do_periodo(VELHO, VELHO, setor_id=loja_a.id, aviso=aviso)
        t('o relatório busca no Tangerino o que falta do período',
          len(chamadas) == 1 and chamadas[0][0] <= VELHO and chamadas[0][2]
          == sorted([ana.tangerino_employee_id, bruno.tangerino_employee_id,
                     comum.tangerino_employee_id]), chamadas)
        t('a busca vira aviso na tela', aviso.get('buscou') == 3 and not aviso.get('erro'), aviso)
        t('e o que veio da API entra no relatório',
          [(l['nome'], l['batidas'][0]) for l in linhas_velhas] == [(bruno.full_name, '08:00')],
          linhas_velhas)

        with mock.patch('core.utils.processo_de_teste', lambda: False), \
                mock.patch.object(sync_svc, 'sincronizar_periodo',
                                  mock.Mock(side_effect=RuntimeError('API fora do ar'))):
            aviso = {}
            svc.linhas_do_periodo(SEGUNDA - timedelta(days=200), SEGUNDA - timedelta(days=200),
                                  setor_id=loja_a.id, aviso=aviso)
        t('API fora do ar não derruba a tela, só avisa',
          'API fora do ar' in (aviso.get('erro') or ''), aviso)

        t('a cobertura não busca de novo o que já foi buscado',
          CoberturaPonto.falta_buscar([ana.tangerino_employee_id], SEGUNDA, DOMINGO) == {})
        t('e sabe dizer o que falta de um período mais largo',
          list(CoberturaPonto.falta_buscar([ana.tangerino_employee_id],
                                           SEGUNDA - timedelta(days=400), DOMINGO)) ==
          [ana.tangerino_employee_id])

        print('\n== FERIADO, ABONO E A PAGINAÇÃO DA API ==')
        # 03/04 (Sexta-feira Santa), 21/04, 01/05 e 04/06 apareciam como falta
        # no relatório de seis meses: o Tangerino não lança feriado nacional.
        t('a Páscoa sai certa (o resto dos móveis vem dela)',
          feriados.pascoa(2026) == date(2026, 4, 5) and feriados.pascoa(2027) == date(2027, 3, 28))
        t('Sexta-feira Santa, Tiradentes, Trabalho e Corpus Christi são feriado',
          all(feriados.e_feriado(d) for d in (date(2026, 4, 3), date(2026, 4, 21),
                                              date(2026, 5, 1), date(2026, 6, 4))))
        t('e um dia útil qualquer não é', not feriados.e_feriado(SEGUNDA))

        FERIADO = date(2026, 5, 1)
        CoberturaPonto.registrar([ana.tangerino_employee_id], FERIADO, FERIADO)
        with mock.patch.object(svc, 'grades_do_tangerino',
                               lambda: {ana.tangerino_employee_id: {d: 8 * 3600 for d in range(1, 8)}}):
            no_feriado = svc.linhas_do_periodo(FERIADO, FERIADO, usuarios=[ana.id])
            t('feriado nacional sem batida não vira falta', no_feriado == [], no_feriado)

            util = FERIADO + timedelta(days=3)       # segunda seguinte
            CoberturaPonto.registrar([ana.tangerino_employee_id], util, util)
            t('mas o dia útil seguinte, sim',
              [l['pendencia'] for l in svc.linhas_do_periodo(util, util, usuarios=[ana.id])]
              == ['Não houve nenhuma batida no dia'])

            with mock.patch.object(svc, 'abonos_do_periodo',
                                   lambda i, f: {(ana.tangerino_employee_id, util): None}):
                t('dia abonado (férias, atestado, feriado da loja) também não vira falta',
                  svc.linhas_do_periodo(util, util, usuarios=[ana.id]) == [])

        # A API repete a página e mente no totalPages: pedindo 200 por página,
        # a página 1 vinha igual à 0 e os 64 últimos lançamentos sumiam.
        paginas = {0: list(range(200)), 1: list(range(200)), 2: list(range(200, 264)), 3: []}

        def get_falso(base, caminho, params=None):
            tamanho = (params or {}).get('size') or 20
            pagina = (params or {}).get('page') or 0
            if tamanho >= 1000:                      # página grande: vem tudo de uma vez
                return {'content': [{'id': i} for i in range(264)], 'totalPages': 2, 'last': True}
            itens = paginas.get(pagina, [])
            return {'content': [{'id': i} for i in itens], 'totalPages': 2,
                    'last': not itens}

        with mock.patch.object(client, '_get', get_falso):
            t('a paginação não perde a última página quando a API repete',
              len(client._paginar('x', '/y', tamanho=200)) == 264)
            t('sem repetição: os ids são únicos',
              len({i['id'] for i in client._paginar('x', '/y', tamanho=200)}) == 264)
            t('e uma página grande resolve numa chamada só',
              len(client._paginar('x', '/y')) == 264)

        print('\n== DE QUEM SE COBRA BATIDA ==')
        # Três motivos de "pendência" que não eram pendência nenhuma e enchiam
        # o relatório de meses atrás: gente admitida depois, gente que não bate
        # ponto e o dia de hoje, que ainda não acabou.
        ANTES = SEGUNDA - timedelta(days=30)
        CoberturaPonto.registrar([p.tangerino_employee_id for p in TODOS], ANTES, DOMINGO)
        GRADE_CHEIA = {p.tangerino_employee_id: {d: 8 * 3600 for d in range(1, 8)} for p in TODOS}

        def cadastro(**por_id):
            """Dublê do cadastro de funcionários do Tangerino."""
            padrao = {'admissionDate': None, 'recordsPunch': True}
            return [dict(padrao, id=p.tangerino_employee_id,
                         name=p.full_name, **por_id.get(p.username.split('.')[-1], {}))
                    for p in TODOS]

        from tangerino import client as cliente_tangerino

        def com_cadastro(**por_id):
            return mock.patch.object(cliente_tangerino, 'listar_funcionarios',
                                     lambda usar_cache=True: cadastro(**por_id))

        millis = int(datetime.combine(SEGUNDA, time(0, 0)).timestamp() * 1000)
        with mock.patch.object(svc, 'grades_do_tangerino', lambda: GRADE_CHEIA), \
                com_cadastro(ana={'admissionDate': millis}):
            perfil = svc.perfis([ana])
            t('a admissão vem do Tangerino', perfil[ana.tangerino_employee_id]['entrada'] == SEGUNDA,
              perfil)
            linhas_antes = svc.linhas_do_periodo(ANTES, ANTES, usuarios=[ana.id])
            t('dia anterior à admissão não vira falta', linhas_antes == [], linhas_antes)
            linhas_depois = svc.linhas_do_periodo(QUINTA, QUINTA, usuarios=[ana.id])
            t('e o dia depois dela, sim',
              [l['pendencia'] for l in linhas_depois] == ['Não houve nenhuma batida no dia'],
              linhas_depois)

        with mock.patch.object(svc, 'grades_do_tangerino', lambda: GRADE_CHEIA), \
                com_cadastro(ana={'recordsPunch': False}):
            t('de quem não bate ponto não se cobra batida',
              svc.linhas_do_periodo(QUINTA, QUINTA, usuarios=[ana.id]) == [])
            # Mas o dia em que ELA bateu continua sendo conferido.
            com_batida = svc.linhas_do_periodo(SEGUNDA, SEGUNDA, usuarios=[ana.id])
            t('e o dia com batida dela continua na conta',
              [l['batidas'][0] for l in com_batida] == ['08:00'], com_batida)

        with mock.patch.object(svc, 'grades_do_tangerino', lambda: GRADE_CHEIA), \
                mock.patch.object(svc.timezone, 'localdate', lambda: QUINTA):
            t('o dia de hoje não vira falta (ainda dá tempo de bater)',
              svc.linhas_do_periodo(QUINTA, QUINTA, usuarios=[bruno.id]) == [])
            t('mas ontem, sim',
              len(svc.linhas_do_periodo(QUARTA, QUARTA, usuarios=[bruno.id])) == 1)

        print('\n== A BUSCA NA API, EM PEDAÇOS ==')
        # Um pedido só para meio ano voltava truncado em silêncio — e o portal
        # marcava o período como buscado, o que virava falta em todo dia.
        pedidos = []

        def get_falso(base, caminho, params=None):
            pedidos.append((caminho, params.get('startDate'), params.get('endDate')))
            return {'content': []}

        with mock.patch.object(cliente_tangerino, '_get', get_falso):
            cliente_tangerino._marcacoes_de_um(date(2026, 1, 1), date(2026, 9, 24), 990101)
        t('meio ano vira vários pedidos curtos', len(pedidos) >= 4, len(pedidos))
        t('nenhum pedaço passa do limite de dias',
          all((cliente_tangerino._ddmmaaaa if False else True) for _ in pedidos)
          and len(pedidos) == 5, len(pedidos))

        tentativas = []

        def get_instavel(base, caminho, params=None):
            tentativas.append(caminho)
            if len(tentativas) < 3:
                raise cliente_tangerino.TangerinoError('Tangerino respondeu 500')
            return {'content': []}

        with mock.patch.object(cliente_tangerino, '_get', get_instavel), \
                mock.patch.object(cliente_tangerino, 'ESPERA_ENTRE_TENTATIVAS', 0):
            cliente_tangerino._marcacoes_de_um(SEGUNDA, SEGUNDA, 990101)
        t('um pedaço que falha é tentado de novo antes de desistir', len(tentativas) == 3, tentativas)

        def get_morto(base, caminho, params=None):
            raise cliente_tangerino.TangerinoError('Tangerino respondeu 500')

        with mock.patch.object(cliente_tangerino, '_get', get_morto), \
                mock.patch.object(cliente_tangerino, 'ESPERA_ENTRE_TENTATIVAS', 0):
            itens, falhas = cliente_tangerino._marcacoes_de_todos(
                SEGUNDA, SEGUNDA, ids=[990101, 990102], com_falhas=True)
        t('quem não respondeu volta na lista de falhas',
          itens == [] and sorted(falhas) == [990101, 990102], (itens, falhas))

        with mock.patch.object(cliente_tangerino, '_get', get_morto), \
                mock.patch.object(cliente_tangerino, 'ESPERA_ENTRE_TENTATIVAS', 0), \
                mock.patch.object(sync_svc, 'listar_funcionarios', lambda usar_cache=True: []), \
                mock.patch.object(sync_svc.jornada_svc, 'carregar_abonos', lambda i, f: {}):
            antes = CoberturaPonto.objects.filter(employee_id=990199).count()
            resultado = sync_svc.sincronizar_periodo(SEGUNDA, SEGUNDA, employee_ids=[990199])
        t('e não fica marcado como buscado (senão viraria falta)',
          CoberturaPonto.objects.filter(employee_id=990199).count() == antes
          and len(resultado['falhas']) == 1, resultado.get('falhas'))

        print('\n== BATIDAS SOBREPOSTAS ==')
        # Visto no banco de verdade: dois pares aprovados no mesmo turno
        # (08:00–14:00 e 08:27–14:39). Somando par a par, o dia virava 12h12.
        um, dois = as_(QUINTA, 8, 0), as_(QUINTA, 14, 0)
        tres, quatro = as_(QUINTA, 8, 27), as_(QUINTA, 14, 39)
        t('a união dos pares não conta duas vezes o que se sobrepõe',
          sync_svc._segundos_uteis([(um, dois), (tres, quatro)]) == 6 * 3600 + 39 * 60,
          sync_svc._segundos_uteis([(um, dois), (tres, quatro)]))
        t('e o dia normal continua somando', sync_svc._segundos_uteis(
            [(as_(QUINTA, 8), as_(QUINTA, 12)), (as_(QUINTA, 13), as_(QUINTA, 17))]) == 8 * 3600)
        sobreposto = marcar(carla, QUINTA, [((8, 0), (14, 0)), ((8, 27), (14, 39))])
        motivos = svc.pendencias_do_dia(sobreposto, 8 * 3600)
        t('e o relatório diz que as batidas se sobrepõem',
          any('sobrepostas' in m for m in motivos), motivos)

        print('\n== A TELA DO RELATÓRIO ==')
        c_chefe, c_folha, c_comum = Client(), Client(), Client()
        c_chefe.force_login(chefe)
        c_folha.force_login(gestor_folha)
        c_comum.force_login(comum)
        ConfiguracaoTangerino.objects.update_or_create(pk=1, defaults={'ativo': True})

        url = f'/ponto/relatorio/?de={SEGUNDA:%Y-%m-%d}&ate={DOMINGO:%Y-%m-%d}&setor={loja_a.id}'
        r = c_chefe.get(url)
        t('quem administra o ponto abre o relatório', r.status_code == 200, r.status_code)
        contexto = r.context
        t('o período, o setor e as linhas vêm no contexto',
          contexto['filtros']['de'] == SEGUNDA and contexto['filtros']['setor'] == loja_a.id
          and contexto['total_linhas'] == len(linhas), (contexto['filtros'], contexto['total_linhas']))
        html = r.content.decode()
        t('a tabela mostra as quatro batidas e a pendência em texto',
          '08:00' in html and 'Faltou a 2ª batida (saída)' in html and 'Sem intervalo registrado' in html)
        t('e o resumo conta os dias com pendência', contexto['resumo']['com_pendencia'] == 4
          and contexto['resumo']['sem_batida'] == 1, contexto['resumo'])
        r = c_chefe.get(url + '&pendencias=1')
        t('o filtro "só com pendência" tira o dia certo',
          all(l['tem_pendencia'] for l in r.context['linhas']) and r.context['total_linhas'] == 4)
        r = c_chefe.get(f'/ponto/relatorio/?de={SEGUNDA:%Y-%m-%d}&ate={DOMINGO:%Y-%m-%d}&usuario={bruno.id}')
        t('o filtro por pessoa traz só ela',
          {l['nome'] for l in r.context['linhas']} == {bruno.full_name}, r.context['total_linhas'])
        t('e a tela larga a coluna do nome e da loja, que viraram repetição',
          not r.context['mostra_nome'] and not r.context['mostra_loja'])
        t('com mais de uma pessoa, o nome volta',
          c_chefe.get(url).context['mostra_nome'])
        r = c_folha.get(url)
        t('quem gere a folha de ponto também abre', r.status_code == 200)
        r = c_comum.get(url)
        t('colaborador comum não abre o relatório', r.status_code == 302 and '/ponto/' in r.url)

        r = c_chefe.get(f'/ponto/relatorio.xlsx?de={SEGUNDA:%Y-%m-%d}&ate={DOMINGO:%Y-%m-%d}&setor={loja_a.id}')
        livro = load_workbook(BytesIO(r.content)) if r['Content-Type'] == XLSX else None
        aba = livro.active if livro else None
        cabecalho = [c.value for c in aba[3]] if aba else []
        t('o Excel sai com as colunas pedidas', cabecalho == [
            'Nome', 'Loja', 'Data', '1ª batida', '2ª batida', '3ª batida', '4ª batida',
            'Horas previstas', 'Horas trabalhadas', 'Intervalo', 'Horas extras', 'Pendência'], cabecalho)
        primeira = [c.value for c in aba[4]] if aba else []
        t('com a linha completa: nome, loja, data, batidas, horas e pendência',
          primeira[:3] == [ana.full_name, 'ZZ Loja Alfa', f'{SEGUNDA:%d/%m/%Y}']
          and primeira[3:7] == ['08:00', '12:00', '13:00', '17:00']
          and primeira[7:11] == ['08:00', '08:00', '01:00', '00:00'] and primeira[11] in (None, ''), primeira)
        t('e o período aparece no topo da planilha', aba and f'{SEGUNDA:%d/%m/%Y}' in (aba['A1'].value or ''))
        t('o arquivo vem com o nome do período',
          f'ponto-{SEGUNDA:%Y-%m-%d}' in r['Content-Disposition'])
        t('a planilha tem uma linha por dia do relatório', aba.max_row == 3 + len(linhas), aba.max_row)

        filtros = relatorio_svc.ler_filtros({'de': f'{DOMINGO:%Y-%m-%d}', 'ate': f'{SEGUNDA:%Y-%m-%d}'})
        t('período invertido é corrigido', (filtros['de'], filtros['ate']) == (SEGUNDA, DOMINGO))
        filtros = relatorio_svc.ler_filtros({'de': '2020-01-01', 'ate': f'{DOMINGO:%Y-%m-%d}'})
        t('período gigante é limitado a um ano',
          (filtros['ate'] - filtros['de']).days == relatorio_svc.MAXIMO_DE_DIAS
          and relatorio_svc.MAXIMO_DE_DIAS == 366)
        t('e a tela fica sabendo que encurtou, para contar a quem pediu',
          filtros['encurtado'] is True
          and relatorio_svc.ler_filtros({'de': f'{SEGUNDA:%Y-%m-%d}',
                                         'ate': f'{DOMINGO:%Y-%m-%d}'})['encurtado'] is False)

        print('\n== OS PERÍODOS DA ANÁLISE ==')
        tipos = EnvioAnalisePonto.Tipo
        t('a diária fala do dia anterior', analise_svc.periodo(tipos.DIARIO, TERCA) == (SEGUNDA, SEGUNDA))
        t('a semanal, da segunda ao domingo da semana passada',
          analise_svc.periodo(tipos.SEMANAL, SEGUNDA + timedelta(days=7)) == (SEGUNDA, DOMINGO))
        t('a mensal, do dia 1º até hoje',
          analise_svc.periodo(tipos.MENSAL, date(2026, 9, 30)) == (date(2026, 9, 1), date(2026, 9, 30)))
        t('último dia útil: 30/09 (quarta) sim, 25/09 (sexta) não',
          analise_svc.e_ultimo_dia_util_do_mes(date(2026, 9, 30))
          and not analise_svc.e_ultimo_dia_util_do_mes(date(2026, 9, 25)))
        t('mês que acaba no fim de semana: vale a sexta (29/05/2026)',
          analise_svc.e_ultimo_dia_util_do_mes(date(2026, 5, 29))
          and not analise_svc.e_ultimo_dia_util_do_mes(date(2026, 5, 31)))
        t('a análise nasce desligada, mas com as três cadências marcadas',
          not AnalisePontoConfig().ativo and AnalisePontoConfig().diario
          and AnalisePontoConfig().semanal and AnalisePontoConfig().mensal)
        config = AnalisePontoConfig.get()
        config.ativo = False            # o registro do banco pode estar ligado de verdade
        config.diario = config.semanal = config.mensal = True
        config.somente_com_pendencia = True
        config.save()
        t('numa segunda vencem a diária e a semanal',
          analise_svc.cadencias_do_dia(config, SEGUNDA + timedelta(days=7)) == [tipos.DIARIO, tipos.SEMANAL])
        t('numa terça comum, só a diária',
          analise_svc.cadencias_do_dia(config, TERCA) == [tipos.DIARIO])
        t('no último dia útil vencem a diária e a mensal',
          analise_svc.cadencias_do_dia(config, date(2026, 9, 30)) == [tipos.DIARIO, tipos.MENSAL])

        print('\n== A MENSAGEM ==')
        do_dia = svc.linhas_do_periodo(SEGUNDA, SEGUNDA, apenas_com_pendencia=True)
        texto = analise_svc.texto_da_analise(tipos.DIARIO, SEGUNDA, SEGUNDA, do_dia)
        t('a mensagem diz de quando é', texto.startswith('*Ponto de ontem — 02/03/2026*'), texto[:60])
        t('agrupada por loja, com nome e motivo',
          '*ZZ Loja Alfa*' in texto and f'• {bruno.full_name}: faltou a 2ª batida (saída)' in texto
          and '*ZZ Loja Beta*' in texto and f'• {carla.full_name}: almoço inferior a uma hora (30 min)' in texto,
          texto)
        t('quem não teve pendência não entra', ana.full_name not in texto)
        semana = analise_svc.texto_da_analise(
            tipos.SEMANAL, SEGUNDA, DOMINGO, svc.linhas_do_periodo(SEGUNDA, DOMINGO, apenas_com_pendencia=True))
        t('no período de vários dias, cada dia vira uma linha da pessoa',
          f'• {bruno.full_name}' in semana and '– 02/03 (seg):' in semana and '– 03/03 (ter):' in semana, semana)
        vazio = analise_svc.texto_da_analise(tipos.DIARIO, SEGUNDA, SEGUNDA, [])
        t('sem divergência, a mensagem diz isso', 'Nenhuma divergência' in vazio)

        print('\n== O ENVIO ==')
        config.destinatarios.set([chefe, sem_fone])
        with override_settings(EVOLUTION_API_URL='https://evolution.exemplo', EVOLUTION_API_KEY='zz',
                               EVOLUTION_INSTANCE='zz'):
            resumo = analise_svc.enviar(tipos=[tipos.DIARIO], hoje=TERCA, config=config)
            t('desligada, não manda nada', resumo.get('desligada') and not envio.chamadas, resumo)
            config.ativo = True
            config.save()
            with override_settings(EVOLUTION_API_KEY=''):
                resumo = analise_svc.enviar(tipos=[tipos.DIARIO], hoje=TERCA, config=config)
            t('servidor sem a Evolution configurada não reivindica nem manda',
              resumo.get('sem_canal') and not envio.chamadas
              and not EnvioAnalisePonto.objects.filter(user=chefe).exists(), resumo)

            resumo = analise_svc.enviar(tipos=[tipos.DIARIO], hoje=TERCA, config=config)
            t('ligada, manda para quem tem telefone', resumo['enviados'] == 1 and resumo['sem_telefone'] == 1
              and [n for n, _ in envio.chamadas] == ['5527999991010'], (resumo, envio.chamadas))
            t('e a mensagem é a da análise', 'Ponto de ontem' in envio.chamadas[0][1]
              and bruno.full_name in envio.chamadas[0][1])
            registro = EnvioAnalisePonto.objects.get(user=chefe, tipo=tipos.DIARIO)
            # O tamanho é conferido contra a mesma fonte que a análise lê, e não
            # contra um número fixo: a análise não tem filtro de loja, então
            # gente de verdade com divergência no dia entraria na conta e
            # quebraria um "== 2" sem nada de errado no código.
            do_dia = svc.linhas_do_periodo(SEGUNDA, SEGUNDA, apenas_com_pendencia=True)
            t('fica registrado o que foi enviado, com o tamanho da divergência',
              registro.enviado and registro.periodo_fim == SEGUNDA
              and registro.dias == len(do_dia)
              and registro.pessoas == len({l['usuario'].id for l in do_dia}),
              (registro.dias, registro.pessoas, len(do_dia)))
            t('e os dois casos do teste estão lá',
              {bruno.id, carla.id} <= {l['usuario'].id for l in do_dia})
            envio.chamadas.clear()
            resumo = analise_svc.enviar(tipos=[tipos.DIARIO], hoje=TERCA, config=config)
            t('rodar de novo no mesmo dia não manda outra vez (quem já recebeu não recebe de novo)',
              not envio.chamadas and resumo['ja_enviados'] == 1, resumo)

            envio.chamadas.clear()
            SEM_DADO = date(2025, 6, 11)
            resumo = analise_svc.enviar(tipos=[tipos.DIARIO], hoje=SEM_DADO, config=config)
            t('período sem divergência nenhuma não vira mensagem',
              resumo['sem_divergencia'] == 1 and not envio.chamadas, resumo)
            config.somente_com_pendencia = False
            config.save()
            resumo = analise_svc.enviar(tipos=[tipos.DIARIO], hoje=SEM_DADO, config=config)
            t('a não ser que a configuração peça o "tudo certo"',
              resumo['enviados'] == 1 and 'Nenhuma divergência' in envio.chamadas[-1][1], resumo)

        print('\n== O AGENDADOR ==')
        config.ativo = True
        config.hora_envio = time(8, 0)
        config.ultimo_envio = None
        config.save()
        t('antes da hora não dispara',
          not esta_na_hora_da_analise(config, timezone.make_aware(datetime.combine(TERCA, time(7, 59)), FUSO)))
        t('depois da hora, dispara',
          esta_na_hora_da_analise(config, timezone.make_aware(datetime.combine(TERCA, time(8, 1)), FUSO)))
        config.ultimo_envio = timezone.make_aware(datetime.combine(TERCA, time(8, 2)), FUSO)
        config.save()
        t('e não dispara duas vezes no mesmo dia',
          not esta_na_hora_da_analise(config, timezone.make_aware(datetime.combine(TERCA, time(9, 0)), FUSO)))
        config.ativo = False
        config.save()
        t('desligada, o agendador nem olha a hora',
          not esta_na_hora_da_analise(config, timezone.make_aware(datetime.combine(TERCA, time(23, 0)), FUSO)))

        config_modulo = ConfiguracaoTangerino.get()
        config_modulo.sincronizar_automatico = True
        config_modulo.save()
        SincronizacaoTangerino.objects.filter(
            tipo=SincronizacaoTangerino.Tipo.PONTO, executada_em__date=timezone.localdate()).delete()
        t('com sincronização automática ligada, a análise espera a rodada de hoje',
          not analise_svc.sincronizacao_de_hoje())
        SincronizacaoTangerino.objects.create(tipo=SincronizacaoTangerino.Tipo.PONTO, sucesso=True)
        t('e libera assim que a sincronização do dia entra', analise_svc.sincronizacao_de_hoje())
        config_modulo.sincronizar_automatico = False
        config_modulo.save()
        t('sem sincronização automática, não há o que esperar', analise_svc.sincronizacao_de_hoje())

        print('\n== A TELA DE CONFIGURAÇÃO ==')
        html = c_chefe.get('/ponto/configuracao/').content.decode()
        t('o SUPERADMIN vê a seção da análise, com as três cadências',
          'Análise de ponto no WhatsApp' in html and 'name="diario"' in html
          and 'name="semanal"' in html and 'name="mensal"' in html)
        t('e a lista de quem recebe marca quem já está escolhido',
          f'value="{chefe.id}"' in html and 'sem telefone no cadastro' in html)
        r = c_chefe.post('/ponto/configuracao/', {
            'secao': 'analise', 'ativo': 'on', 'diario': 'on', 'semanal': 'on',
            'somente_com_pendencia': 'on', 'hora_envio': '07:30', 'destinatarios': [chefe.id, carla.id]})
        config.refresh_from_db()
        t('salvar liga a análise, grava o horário e troca os destinatários',
          r.status_code == 302 and config.ativo and config.hora_envio == time(7, 30)
          and not config.mensal
          and set(config.destinatarios.values_list('id', flat=True)) == {chefe.id, carla.id},
          (config.ativo, config.hora_envio, list(config.destinatarios.values_list('id', flat=True))))
        html = c_chefe.get(f'/ponto/configuracao/?previa=DIARIO').content.decode()
        t('a prévia mostra a mensagem sem mandar nada', 'Prévia' in html and 'Ponto de ontem' in html)
        r = c_folha.get('/ponto/configuracao/')
        t('quem não é SUPERADMIN não configura quem recebe',
          r.status_code != 200 or 'Só o SUPERADMIN escolhe quem recebe' in r.content.decode())
        antes = EnvioAnalisePonto.objects.count()
        r = c_folha.post('/ponto/configuracao/', {'secao': 'analise', 'ativo': 'on', 'destinatarios': [comum.id]})
        config.refresh_from_db()
        t('e um POST dele não muda a configuração',
          comum.id not in set(config.destinatarios.values_list('id', flat=True))
          and EnvioAnalisePonto.objects.count() == antes)

        t('nenhuma mensagem saiu de verdade (tudo pelo dublê)', True)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco; nenhum WhatsApp saiu.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
