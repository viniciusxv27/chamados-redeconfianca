"""Ponto: alerta semanal de faltas (DP + gestor de cada loja) e a visão de assiduidade da rede.

Pedidos de 25/11/2026:
- o DP recebe o geral das faltas da semana anterior e o gestor de cada loja recebe as da equipe
  dele, para procurar o DP — toda segunda, sexta ou domingo;
- projeto de incentivo à marcação correta: as métricas de assiduidade que o portal já usa, para a
  rede inteira (por loja, por pessoa, mês a mês, com a evolução).

Dados de mentira numa transação desfeita; só as pessoas do teste entram nas contas (pessoas_do_ponto
trocada), caches em memória e WhatsApp por dublê.
"""
import os
import sys
from datetime import date, datetime, time, timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-faltas'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-faltas-local'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

import core.evolution as evolution
from cursos.models import GrupoWhatsappLoja
from tangerino import alerta_faltas
from tangerino import assiduidade
from tangerino import pendencias as svc
from tangerino.models import (AlertaFaltasConfig, CoberturaPonto, EnvioAlertaFaltas, Escala, EscalaDia,
                              MarcacaoPonto, SolicitacaoAjustePonto)
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
    def __init__(self):
        self.chamadas = []

    def __call__(self, numero, texto, **kwargs):
        self.chamadas.append((numero, texto))
        return True, 'ok'


def rede_proibida(*a, **k):
    raise AssertionError('o teste tentou falar com a Evolution de verdade')


SEGUNDA = date(2026, 3, 2)
DOMINGO = SEGUNDA + timedelta(days=6)
FUSO = timezone.get_current_timezone()


def as_(dia, h, m=0):
    return timezone.make_aware(datetime.combine(dia, time(h, m)), FUSO)


envio = Envio()
marcador = transaction.atomic()
marcador.__enter__()
try:
    assert not User.objects.filter(username__startswith='zzft.').exists()
    loja_a = Sector.objects.create(name='Loja ZZ Alfa')
    loja_b = Sector.objects.create(name='Loja ZZ Beta')
    escritorio = Sector.objects.create(name='ZZ Escritório')

    def pessoa(apelido, eid, setor, fone='', hierarquia='PADRAO'):
        return User.objects.create_user(
            username=f'zzft.{apelido}', email=f'zzft.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=apelido.title(), last_name='Teste', sector=setor, phone=fone, hierarchy=hierarquia,
            tangerino_employee_id=eid)

    ana = pessoa('ana', 880101, loja_a)
    bruno = pessoa('bruno', 880102, loja_a)
    carla = pessoa('carla', 880103, loja_b)
    duda = pessoa('duda', 880104, escritorio)
    gerente_a = pessoa('gerentea', None, loja_a, fone='27 99999-2001')
    gerente_b = pessoa('gerenteb', None, loja_b)                       # sem telefone
    dp = pessoa('dp', None, escritorio, fone='27 99999-2002')
    chefe = pessoa('chefe', None, escritorio, fone='27 99999-2003', hierarquia='SUPERADMIN')
    comum = pessoa('comum', None, loja_a)
    PONTO = [ana, bruno, carla, duda]
    CoberturaPonto.registrar([p.tangerino_employee_id for p in PONTO], SEGUNDA - timedelta(days=70),
                             DOMINGO + timedelta(days=70))

    def escala(usuario, dias):
        e = Escala.objects.create(colaborador=usuario, semana_inicio=dias[0] - timedelta(days=dias[0].weekday()))
        for d in dias:
            EscalaDia.objects.create(escala=e, data=d, entrada=time(8, 0), saida_almoco=time(12, 0),
                                     volta_almoco=time(13, 0), saida=time(17, 0))

    def marcar(usuario, dia, pares, aberto=False):
        campos, total = {}, 0
        for i, (entrada, saida) in enumerate(pares, start=1):
            campos[f'entrada{i}'] = as_(dia, *entrada) if entrada else None
            campos[f'saida{i}'] = as_(dia, *saida) if saida else None
            if entrada and saida:
                total += int((as_(dia, *saida) - as_(dia, *entrada)).total_seconds())
        return MarcacaoPonto.objects.create(
            employee_id=usuario.tangerino_employee_id, usuario=usuario, nome=usuario.full_name, data=dia,
            total_segundos=total, previsto_segundos=8 * 3600, em_aberto=aberto, sincronizado_em=timezone.now(),
            **campos)

    certo = [((8, 0), (12, 0)), ((13, 0), (17, 0))]
    semana = [SEGUNDA + timedelta(days=i) for i in range(5)]
    for p in PONTO:
        escala(p, semana)
    # Ana: faltou terça e quinta. Bruno: veio todo dia, mas esqueceu a saída na quarta (não é falta).
    # Carla: faltou segunda. Duda (escritório): faltou sexta.
    for d in semana:
        if d not in (semana[1], semana[3]):
            marcar(ana, d, certo)
        marcar(bruno, d, [((8, 0), (12, 0)), ((13, 0), None)] if d == semana[2] else certo,
               aberto=d == semana[2])
        if d != semana[0]:
            marcar(carla, d, certo)
        if d != semana[4]:
            marcar(duda, d, certo)
    GrupoWhatsappLoja.objects.create(setor=loja_a, grupo='120363000000000001')

    original = svc.pessoas_do_ponto
    ids_do_teste = {x.pk for x in PONTO}

    def so_os_do_teste(setor_id=None, usuarios=None):
        return [p for p in original(setor_id, usuarios) if p.pk in ids_do_teste]

    gerentes = lambda hoje=None: {loja_a.id: [gerente_a], loja_b.id: [gerente_b], escritorio.id: [chefe]}  # noqa: E731

    with mock.patch.object(svc, 'pessoas_do_ponto', so_os_do_teste), \
            mock.patch.object(svc, 'grades_do_tangerino', lambda: {}), \
            mock.patch('quadro_hc.servicos.gerentes_por_loja', gerentes), \
            mock.patch.object(evolution, 'enviar_texto', envio), \
            mock.patch.object(evolution.urlrequest, 'urlopen', rede_proibida), \
            mock.patch('tangerino.analise.faltando_no_canal', lambda: []):

        print('== A SEMANA ==')
        t('segunda olha a semana anterior inteira', alerta_faltas.semana_fechada(date(2026, 3, 9)) == (SEGUNDA, DOMINGO))
        t('sexta também', alerta_faltas.semana_fechada(date(2026, 3, 13)) == (SEGUNDA, DOMINGO))
        t('domingo também (a semana dele ainda não fechou)', alerta_faltas.semana_fechada(date(2026, 3, 15)) == (SEGUNDA, DOMINGO))
        t('semana de um dia qualquer', alerta_faltas.semana_do_dia(semana[3]) == (SEGUNDA, DOMINGO))

        print('\n== AS FALTAS POR LOJA ==')
        lojas = {l['nome']: l for l in alerta_faltas.lojas_com_faltas(SEGUNDA, DOMINGO, buscar=False)}
        alfa = lojas.get('Loja ZZ Alfa')
        t('Alfa: as duas faltas da Ana', alfa and alfa['faltas'] == 2 and [p['nome'] for p in alfa['pessoas']] == ['Ana Teste']
          and alfa['pessoas'][0]['dias'] == [semana[1], semana[3]], alfa)
        t('esquecer a saída não é falta (o Bruno não entra)', alfa and all(p['nome'] != 'Bruno Teste' for p in alfa['pessoas']))
        t('o gestor vem do Quadro de HC, com o telefone', alfa and alfa['gerentes'][0]['user'] == gerente_a
          and alfa['gerentes'][0]['numero'] == '5527999992001')
        t('o grupo da loja vem do cadastro dos cursos', alfa and alfa['grupo'] == '120363000000000001@g.us')
        beta = lojas.get('Loja ZZ Beta')
        t('Beta: a falta da Carla, gestor sem telefone', beta and beta['faltas'] == 1 and beta['gerentes'][0]['numero'] == '')
        esc = lojas.get('ZZ Escritório')
        t('setor que não é loja: entra sem gestor (só no geral)', esc and not esc['e_loja'] and esc['gerentes'] == [])

        config = AlertaFaltasConfig.get()
        t('nasce desligado', config.ativo is False)
        texto = alerta_faltas.texto_do_gestor(alfa, SEGUNDA, DOMINGO, config.orientacao)
        t('mensagem do gestor: loja, semana, quem e quando, e o pedido para procurar o DP',
          'Loja ZZ Alfa' in texto and '02/03 a 08/03/2026' in texto and 'Ana Teste: 03/03 (ter), 05/03 (qui)' in texto
          and 'entre em contato com o DP' in texto, texto)
        t('e não fala de outra loja', 'Carla' not in texto)
        geral = alerta_faltas.texto_do_dp(list(lojas.values()), SEGUNDA, DOMINGO, config)
        t('geral do DP: todas as lojas, total e quem foi avisado',
          '4 falta(s) de 3 colaborador(es)' in geral and 'Loja ZZ Beta' in geral and 'ZZ Escritório' in geral
          and 'gestor: Gerentea' in geral and 'sem telefone' in geral, geral)

        print('\n== O ENVIO ==')
        config.dp.set([dp])
        config.grupo_da_loja = True
        config.save()
        resumo = alerta_faltas.enviar(SEGUNDA, DOMINGO, config=config)
        destinos = [n for n, _ in envio.chamadas]
        t('DP, gestor da Alfa e grupo da Alfa recebem', sorted(destinos) == sorted(
            ['5527999992002', '5527999992001', '120363000000000001@g.us']), destinos)
        t('o gestor recebe só a loja dele', any('Ana Teste' in tx and 'Carla' not in tx for n, tx in envio.chamadas if n == '5527999992001'))
        t('gestor sem telefone é contado', resumo['sem_telefone'] == 1, resumo)
        t('cada envio fica registrado', EnvioAlertaFaltas.objects.filter(periodo_fim=DOMINGO, enviado=True).count() == 3)
        envio.chamadas.clear()
        resumo = alerta_faltas.enviar(SEGUNDA, DOMINGO, config=config)
        t('a mesma semana não sai duas vezes', not envio.chamadas and resumo['ja_enviados'] == 3, resumo)
        alerta_faltas.enviar(SEGUNDA, DOMINGO, config=config, reenviar=True, manual=True)
        t('reenviar manda de novo', len(envio.chamadas) == 3)
        envio.chamadas.clear()
        config.grupo_da_loja = False
        config.gerentes = False
        config.save()
        alerta_faltas.enviar(SEGUNDA + timedelta(weeks=1), DOMINGO + timedelta(weeks=1), config=config)
        t('sem gestores e sem grupo: só o DP', [n for n, _ in envio.chamadas] == ['5527999992002'], envio.chamadas)
        envio.chamadas.clear()
        with mock.patch('tangerino.analise.faltando_no_canal', lambda: ['EVOLUTION_API_KEY']):
            resumo = alerta_faltas.enviar(SEGUNDA, DOMINGO, config=config, reenviar=True)
        t('sem a Evolution no servidor não reivindica nada', resumo.get('sem_canal') and not envio.chamadas)

        print('\n== O HORÁRIO ==')
        config.ativo, config.dia_semana, config.hora_envio, config.ultimo_envio = True, 0, time(9, 0), None
        config.save()
        seg_10h = timezone.make_aware(datetime(2026, 3, 9, 10, 0), FUSO)
        t('segunda depois das 9h: na hora', alerta_faltas.esta_na_hora(config, seg_10h))
        t('segunda antes das 9h: ainda não', not alerta_faltas.esta_na_hora(config, seg_10h.replace(hour=8)))
        t('terça: não é o dia', not alerta_faltas.esta_na_hora(config, seg_10h + timedelta(days=1)))
        config.ultimo_envio = seg_10h
        t('já rodou hoje: não repete', not alerta_faltas.esta_na_hora(config, seg_10h + timedelta(hours=1)))
        config.ativo = False
        t('desligado: nunca', not alerta_faltas.esta_na_hora(config, seg_10h + timedelta(days=7)))
        config.save()

        print('\n== A TELA ==')
        c_chefe, c_comum = Client(), Client()
        c_chefe.force_login(chefe)
        c_comum.force_login(comum)
        r = c_comum.get('/ponto/faltas/')
        t('quem não administra o ponto não abre', r.status_code == 302)
        with mock.patch('tangerino.views.pode_ver_relatorio', lambda u: True):
            r = c_comum.get(f'/ponto/faltas/?semana={SEGUNDA:%Y-%m-%d}')
            html = r.content.decode()
            t('quem vê o relatório abre (sem configurar)', r.status_code == 200 and 'Loja ZZ Alfa' in html
              and 'Só o SUPERADMIN muda a configuração' in html and 'name="secao" value="config"' not in html)
            antes = EnvioAlertaFaltas.objects.count()
            c_comum.post('/ponto/faltas/', {'secao': 'enviar', 'semana': f'{SEGUNDA:%Y-%m-%d}', 'para': 'todos'})
            t('e não dispara', EnvioAlertaFaltas.objects.count() == antes)
        r = c_chefe.get(f'/ponto/faltas/?semana={SEGUNDA:%Y-%m-%d}')
        html = r.content.decode()
        t('SUPERADMIN: prévia com a mensagem de cada gestor e a configuração',
          r.status_code == 200 and 'Ana Teste: 03/03 (ter), 05/03 (qui)' in html and 'name="secao" value="config"' in html, r.status_code)
        r = c_chefe.post('/ponto/faltas/', {'secao': 'config', 'ativo': 'on', 'dia_semana': '4', 'hora_envio': '08:30',
                                            'gerentes': 'on', 'orientacao': 'Fale comigo.', 'dp': [dp.pk, chefe.pk]}, follow=True)
        config.refresh_from_db()
        t('salva: sexta às 8h30, ligado, DP escolhido', config.ativo and config.dia_semana == 4
          and config.hora_envio == time(8, 30) and set(config.dp.all()) == {dp, chefe} and config.orientacao == 'Fale comigo.')
        envio.chamadas.clear()
        c_chefe.post('/ponto/faltas/', {'secao': 'enviar', 'semana': f'{SEGUNDA:%Y-%m-%d}', 'para': 'eu'})
        t('"para mim": só o geral, só para quem clicou', [n for n, _ in envio.chamadas] == ['5527999992003']
          and envio.chamadas[0][1].startswith('🧪 Teste'), envio.chamadas)

        print('\n== ASSIDUIDADE DA REDE ==')
        # Ajuste pedido pelo portal conta no mês.
        SolicitacaoAjustePonto.objects.create(usuario=bruno, employee_id=bruno.tangerino_employee_id,
                                              tipo=SolicitacaoAjustePonto.INCLUIR, dia=semana[2], motivo='esqueci')
        # Fevereiro: o Bruno esqueceu 3 saídas; em março só 1 (evolução).
        fev = [date(2026, 2, d) for d in (2, 3, 4, 5, 6, 9, 10)]
        escala(bruno, fev[:5])
        escala(bruno, fev[5:])
        for i, d in enumerate(fev):
            marcar(bruno, d, [((8, 0), (12, 0)), ((13, 0), None)] if i < 3 else certo, aberto=i < 3)
        v = assiduidade.visao(meses=2, hoje=date(2026, 4, 10), setor_id=loja_a.id)
        t('meses do mais antigo ao atual', v['rotulos'] == ['mar/26', 'abr/26'], v['rotulos'])
        v = assiduidade.visao(meses=3, hoje=date(2026, 4, 10), setor_id=loja_a.id)
        t('o último mês fechado é a referência', v['ref_rotulo'] == 'mar/26', v['ref_rotulo'])
        pessoas = {p['nome']: p for p in v['pessoas']}
        ana_m = pessoas['Ana Teste']['meses'][1]
        t('Ana em março: 2 faltas em 5 dias = 40% de absenteísmo, faixa Atenção',
          ana_m['faltas'] == 2 and ana_m['absenteismo'] == 40 and ana_m['faixa'] == 'Atenção', ana_m)
        bruno_m = pessoas['Bruno Teste']['meses'][1]
        t('Bruno em março: 1 esquecimento em 5 dias trabalhados = 80% de marcação correta, 1 ajuste',
          bruno_m['esquecimentos'] == 1 and bruno_m['trabalhados'] == 5 and bruno_m['marcacao_correta'] == 80
          and bruno_m['ajustes'] == 1, bruno_m)
        bruno_f = pessoas['Bruno Teste']['meses'][0]
        t('Bruno em fevereiro: 3 esquecimentos em 7 dias', bruno_f['esquecimentos'] == 3 and bruno_f['trabalhados'] == 7, bruno_f)
        t('e a evolução da marcação correta é positiva', pessoas['Bruno Teste']['evolucao_correta'] > 0,
          pessoas['Bruno Teste']['evolucao_correta'])
        alfa_l = [l for l in v['lojas'] if l['id'] == loja_a.id][0]
        t('a loja soma as duas pessoas no mês', alfa_l['meses'][1]['trabalhados'] == 8
          and alfa_l['meses'][1]['esquecimentos'] == 1 and alfa_l['meses'][1]['ajustes'] == 1 and alfa_l['pessoas'] == 2,
          alfa_l['meses'][1])
        t('filtrar a loja mostra só ela', [l['id'] for l in v['lojas']] == [loja_a.id])
        t('faixas da Rotina ADM', [assiduidade.faixa(x)[0] for x in (0, 1.5, 4, 9)] == ['Excelente', 'Boa', 'Regular', 'Atenção'])
        v_rede = assiduidade.visao(meses=3, hoje=date(2026, 4, 10))
        destaques = [p['nome'] for p in v_rede['destaques']]
        t('regularidade exemplar: só quem não faltou, não esqueceu e não pediu ajuste (com 10+ dias)', destaques == [], destaques)
        visao_real = assiduidade.visao
        with mock.patch('tangerino.views.pode_ver_relatorio', lambda u: True), \
                mock.patch.object(assiduidade, 'visao', lambda **kw: visao_real(hoje=date(2026, 4, 10), **kw)):
            r = c_comum.get('/ponto/assiduidade/?meses=3&setor=' + str(loja_a.id))
        html = r.content.decode()
        t('a tela abre com loja e colaboradores', r.status_code == 200 and 'Loja ZZ Alfa' in html and 'Bruno Teste' in html
          and 'Regularidade exemplar' in html, [x in html for x in ('Loja ZZ Alfa', 'Bruno Teste', 'Regularidade exemplar')])
        r = Client()
        r.force_login(comum)
        t('quem não vê o relatório não abre', r.get('/ponto/assiduidade/').status_code == 302)

        t('nenhuma mensagem saiu de verdade (tudo pelo dublê)', True)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco; nenhum WhatsApp saiu.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
