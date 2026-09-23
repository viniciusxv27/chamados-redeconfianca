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
from folhaponto.models import FolhaPontoManagerPermission
from tangerino import analise as analise_svc
from tangerino import pendencias as svc
from tangerino import relatorio as relatorio_svc
from tangerino.agendador import esta_na_hora_da_analise
from tangerino.models import (AnalisePontoConfig, ConfiguracaoTangerino, EnvioAnalisePonto, Escala,
                              EscalaDia, MarcacaoPonto, SincronizacaoTangerino)
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
        t('período gigante é limitado a seis meses',
          (filtros['ate'] - filtros['de']).days == relatorio_svc.MAXIMO_DE_DIAS)

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
        config = AnalisePontoConfig.get()
        t('a análise nasce desligada, mas com as três cadências marcadas',
          not config.ativo and config.diario and config.semanal and config.mensal)
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
            t('fica registrado o que foi enviado, com o tamanho da divergência',
              registro.enviado and registro.periodo_fim == SEGUNDA and registro.dias == 2
              and registro.pessoas == 2, (registro.dias, registro.pessoas))
            envio.chamadas.clear()
            resumo = analise_svc.enviar(tipos=[tipos.DIARIO], hoje=TERCA, config=config)
            t('rodar de novo no mesmo dia não manda outra vez (quem já recebeu não recebe de novo)',
              not envio.chamadas and resumo['ja_enviados'] == 1, resumo)

            envio.chamadas.clear()
            resumo = analise_svc.enviar(tipos=[tipos.DIARIO], hoje=date(2026, 2, 20), config=config)
            t('período sem divergência nenhuma não vira mensagem',
              resumo['sem_divergencia'] == 1 and not envio.chamadas, resumo)
            config.somente_com_pendencia = False
            config.save()
            resumo = analise_svc.enviar(tipos=[tipos.DIARIO], hoje=date(2026, 2, 20), config=config)
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
