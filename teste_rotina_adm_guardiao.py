"""Missão Guardião em /rotina-adm/guardiao/ (26/11/2026).

Cobre:
- regras das 9 missões (guardiao.py): Renova proporcional e "sem venda = 5"; CheckLojas registrado pela loja;
  Melhoria Contínua com 3 ideias aprovadas; Evolução com curso no prazo, fora do prazo e sem curso;
  Contagem LVUT em 4 janelas com bloqueio por divergência e recuperação; Protocolo de Recuperação
  proporcional; Proteção Fiscal pelas "Sem NF" do mês; Agenda (check-list até o horário + Rotina
  Gerencial no dia); Estoque Total com corte de 80%; níveis e formatação "12,5";
- cálculo em lote (ranking) igual ao cálculo de uma pessoa;
- telas: as 6 abas abrem; acesso igual ao da Rotina ADM (sem acesso volta para a home, Visualizador vê só a
  própria missão, Gestor escolhe o Guardião); registrar CheckLojas, contagem, regularização e missão manual;
  só quem gere lança o Estoque Total; missão manual não soma pontos.

Roda no banco do dev dentro de uma transação desfeita no fim; cache em memória; rede bloqueada.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import redeconfianca.settings as _cfg  # noqa: E402

_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'zz-guardiao-{n}'}
               for n in _cfg.CACHES}
_cfg.ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

import django  # noqa: E402

django.setup()

from datetime import date, datetime, time, timedelta  # noqa: E402
from decimal import Decimal  # noqa: E402
from unittest import mock  # noqa: E402

import requests  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402
from django.utils import timezone  # noqa: E402

setup_test_environment()

from auditoria_sap.models import LinhaAuditoria, VendaRenova  # noqa: E402
from cursos.models import AtribuicaoCurso, Comprovante, Curso  # noqa: E402
from impulso.models import Ideia  # noqa: E402
from rotina.models import AtividadeRotina, ConclusaoAtividade, RotinaGerencial  # noqa: E402
from rotina_adm import guardiao  # noqa: E402
from rotina_adm.models import (AcessoRotinaAdm, ContagemEstoque, ItemChecklist, LancamentoGuardiao,  # noqa: E402
                               MarcacaoChecklist, MissaoAgenda)
from users.models import Sector, User  # noqa: E402

FALHAS = []


def t(nome, cond, extra=''):
    print(('OK   ' if cond else 'FALHA ') + nome + (f'  [{extra}]' if extra and not cond else ''))
    if not cond:
        FALHAS.append(nome)


def sem_rede(*a, **k):
    raise RuntimeError('rede bloqueada no teste')


class Desfaz(Exception):
    pass


def aware(d, h=10, m=0):
    return timezone.make_aware(datetime.combine(d, time(h, m)))


def pts(r, chave):
    return r['por_chave'][chave]['pontos']


def rodar():
    sufixo = timezone.now().strftime('%H%M%S%f')
    loja = Sector.objects.create(name=f'Loja Zz Guardiao {sufixo}')
    outra = Sector.objects.create(name=f'Loja Zz Outra {sufixo}')
    escritorio = Sector.objects.create(name=f'Zz Escritorio {sufixo}')

    def usuario(nome, setor, hierarquia='PADRAO'):
        return User.objects.create(username=f'zz-{nome}-{sufixo}', email=f'zz-{nome}-{sufixo}@teste.local',
                                   first_name=nome.capitalize(), last_name='Zz', sector=setor,
                                   hierarchy=hierarquia, is_active=True)

    ana = usuario('ana', loja)
    bia = usuario('bia', outra)
    gil = usuario('gil', escritorio)          # Gestor
    zeca = usuario('zeca', loja)              # sem acesso
    AcessoRotinaAdm.objects.create(user=ana, papel='VISUALIZADOR')
    AcessoRotinaAdm.objects.create(user=bia, papel='VISUALIZADOR')
    AcessoRotinaAdm.objects.create(user=gil, papel='GESTOR')

    # ── Utilidades ──
    t('fmt 12.5 → 12,5', guardiao.fmt(12.5) == '12,5')
    t('fmt 6.6666 → 6,67', guardiao.fmt(6.6666) == '6,67')
    t('fmt 20.0 → 20', guardiao.fmt(20.0) == '20')
    t('nível 59,99 = Desenvolvimento', guardiao.nivel(59.99)['nome'] == 'Missão em Desenvolvimento')
    t('nível 60 = Evolução', guardiao.nivel(60)['nome'] == 'Missão em Evolução')
    t('nível 73,17 = Avançada', guardiao.nivel(73.17)['nome'] == 'Missão Avançada')
    t('nível 90 = Cumprida', guardiao.nivel(90)['nome'] == 'Missão Cumprida')
    t('nível 100 = Excelência', guardiao.nivel(100)['nome'] == 'Missão de Excelência')
    # Faixas do card do Destaque (27/11/2026): a mesma régua de NIVEIS.
    fx = guardiao.faixas(73.17)
    t('faixas: 5 faixas, a atual é Avançada', len(fx['lista']) == len(guardiao.NIVEIS)
      and fx['atual']['nome'] == 'Missão Avançada' and [f['estado'] for f in fx['lista']]
      == ['alcancada', 'alcancada', 'atual', 'futura', 'futura'], fx['atual'])
    t('faixas: faltam 16,83 para Cumprida', fx['proxima']['nome'] == 'Missão Cumprida' and fx['falta_txt'] == '16,83',
      fx['falta_txt'])
    t('faixas: avanço dentro da faixa (70→90)', fx['pct_na_faixa'] in (15.8, 15.9), fx['pct_na_faixa'])
    t('faixas: intervalos 0–59, 60–69, 70–89, 90–99, 100', [(f['minimo'], f['ate']) for f in fx['lista']]
      == [(0, 59), (60, 69), (70, 89), (90, 99), (100, 100)])
    fx = guardiao.faixas(100)
    t('faixas: 100 pontos = última faixa, sem próxima', fx['proxima'] is None and fx['atual']['ultima'] and fx['falta'] == 0)
    t('faixas: 0 pontos = primeira, faltam 60', guardiao.faixas(0)['falta'] == 60)
    js = guardiao.janelas(date(2026, 10, 1), date(2026, 10, 31))
    t('4 janelas LVUT em outubro (a última vai até 31)', [(a.day, b.day) for _n, a, b in js] == [(1, 7), (8, 14), (15, 21), (22, 31)])
    js = guardiao.janelas(date(2027, 2, 1), date(2027, 2, 28))
    t('última janela de fevereiro termina no 28', js[-1][2] == date(2027, 2, 28))

    # ── Mês fechado (outubro/2026, visto em 26/11) ──
    out_ini, out_fim, hoje_nov = date(2026, 10, 1), date(2026, 10, 31), date(2026, 11, 26)

    def calc(pessoa, mes='2026-10', hoje=hoje_nov):
        return guardiao.calcular(pessoa, mes, hoje=hoje)

    r = calc(ana)
    t('sem dado: Renova sem venda vale 5', pts(r, 'renova') == 5)
    t('sem dado: fiscal sem pendência vale 10', pts(r, 'fiscal') == 10, r['por_chave']['fiscal']['resumo'])
    t('sem dado: Evolução sem curso vale 5', pts(r, 'evolucao') == 5)
    t('sem dado: CheckLojas 0 e não cumprido', pts(r, 'checklojas') == 0 and r['por_chave']['checklojas']['status'] == 'perdida')
    t('sem dado: recuperação começa com os 10 pontos', pts(r, 'recuperacao') == 10)
    t('sem dado: agenda sem nada previsto 0 com aviso', pts(r, 'agenda') == 0 and r['por_chave']['agenda']['aviso'])
    t('sem dado: total 30', r['total'] == 30, r['total'])

    # Renova: 3 de 4 conformes (2 com dispensa, 1 com... dispensa também; 1 sem)
    for n, disp in enumerate([True, True, True, False]):
        VendaRenova.objects.create(ordem=f'ZZG{sufixo[-8:]}{n}', setor=loja, data_fatura=date(2026, 10, 5 + n),
                                   dispensada=disp, ativa=True)
    VendaRenova.objects.create(ordem=f'ZZG{sufixo[-8:]}9', setor=loja, data_fatura=date(2026, 9, 30), dispensada=False)
    r = calc(ana)
    t('Renova 3 de 4 = 3,75', pts(r, 'renova') == 3.75, pts(r, 'renova'))

    # CheckLojas registrado
    LancamentoGuardiao.objects.create(loja=loja, competencia=out_ini, missao='CHECKLOJAS', data=date(2026, 10, 20))
    # Estoque total 79% → 0
    LancamentoGuardiao.objects.create(loja=loja, competencia=out_ini, missao='ESTOQUE_TOTAL', data=date(2026, 10, 30),
                                      valor=Decimal('79.99'))
    r = calc(ana)
    t('CheckLojas registrado = 5', pts(r, 'checklojas') == 5)
    t('Estoque Total 79,99% = 0', pts(r, 'estoque_total') == 0)
    LancamentoGuardiao.objects.filter(missao='ESTOQUE_TOTAL', loja=loja).update(valor=Decimal('80'))
    r = calc(ana)
    t('Estoque Total 80% = 20', pts(r, 'estoque_total') == 20)
    r_bia = calc(bia)
    t('lançamento de uma loja não vale para a outra', pts(r_bia, 'checklojas') == 0 and pts(r_bia, 'estoque_total') == 0)

    # Melhoria: 2 aprovadas (uma como participante) + 1 em análise = 0; terceira aprovada = 5
    def ideia(autor, status, quando, participantes=()):
        i = Ideia.objects.create(autor=autor, descricao='zz', setor_impacto='Loja', motivo='zz', status=status)
        Ideia.objects.filter(pk=i.pk).update(criado_em=aware(quando))
        i.participantes.set(participantes)
        return i
    ideia(ana, 'APROVADA', date(2026, 10, 3))
    ideia(gil, 'APROVADA', date(2026, 10, 4), participantes=[ana])
    ideia(ana, 'EM_ANALISE', date(2026, 10, 5))
    ideia(ana, 'APROVADA', date(2026, 9, 28))     # fora do mês
    r = calc(ana)
    t('Melhoria com 2 aprovadas = 0', pts(r, 'melhoria') == 0)
    t('Melhoria mostra a que aguarda avaliação', 'aguardando' in r['por_chave']['melhoria']['resumo'])
    ideia(ana, 'APROVADA', date(2026, 10, 25))
    r = calc(ana)
    t('Melhoria com 3 aprovadas = 5', pts(r, 'melhoria') == 5)

    # Evolução: capacitação atribuída com prazo no mês
    curso = Curso.objects.create(tipo=Curso.CAPACITACAO, titulo='Zz Prevenção de Perdas', prazo=date(2026, 10, 15),
                                 publicado=True)
    AtribuicaoCurso.objects.create(curso=curso, colaborador=ana)
    r = calc(ana)
    t('Evolução: curso não realizado = 0', pts(r, 'evolucao') == 0 and r['por_chave']['evolucao']['status'] == 'perdida')
    comp = Comprovante.objects.create(curso=curso, colaborador=ana, arquivo='zz/comprovante.pdf', status='APROVADO')
    Comprovante.objects.filter(pk=comp.pk).update(enviado_em=aware(date(2026, 10, 16)))
    r = calc(ana)
    t('Evolução: concluído fora do prazo = 0', pts(r, 'evolucao') == 0)
    Comprovante.objects.filter(pk=comp.pk).update(enviado_em=aware(date(2026, 10, 15), 18))
    r = calc(ana)
    t('Evolução: concluído no prazo = 5', pts(r, 'evolucao') == 5)
    Comprovante.objects.filter(pk=comp.pk).update(status='RECUSADO')
    r = calc(ana)
    t('Evolução: comprovante recusado não vale', pts(r, 'evolucao') == 0)
    Comprovante.objects.filter(pk=comp.pk).update(status='PENDENTE')

    # LVUT: janela 1 todas sem divergência (4 × 1,25 = 5); janela 2 smartphone com divergência regularizada no mês;
    # janela 2 SIM card com divergência nunca regularizada (perdida); janela 3 películas regularizada em novembro (perdida)
    for cat in ('SMARTPHONES', 'ELETRONICOS', 'SIMCARD', 'PELICULAS'):
        ContagemEstoque.objects.create(loja=loja, categoria=cat, data=date(2026, 10, 3), divergencias=0,
                                       itens_contados=25)
    ContagemEstoque.objects.create(loja=loja, categoria='SMARTPHONES', data=date(2026, 10, 9), divergencias=2,
                                   itens_contados=20, regularizada_em=aware(date(2026, 10, 12)), regularizacao='ajuste')
    ContagemEstoque.objects.create(loja=loja, categoria='SIMCARD', data=date(2026, 10, 10), divergencias=3,
                                   itens_contados=20)
    ContagemEstoque.objects.create(loja=loja, categoria='PELICULAS', data=date(2026, 10, 16), divergencias=5,
                                   itens_contados=10, regularizada_em=aware(date(2026, 11, 2)), regularizacao='tarde')
    ContagemEstoque.objects.create(loja=outra, categoria='SMARTPHONES', data=date(2026, 10, 16), divergencias=0)
    r = calc(ana)
    lv = r['por_chave']['lvut']
    t('LVUT: 5 conquistadas (4 limpas + 1 recuperada) = 6,25', lv['conquistadas'] == 5 and pts(r, 'lvut') == 6.25,
      (lv['conquistadas'], pts(r, 'lvut')))
    t('LVUT: mês fechado não deixa bloqueada', lv['bloqueadas'] == 0)
    t('LVUT: 11 perdidas (2 divergências + 9 não realizadas)', lv['perdidas'] == 11, lv['perdidas'])
    cel = {c['janela']: c['situacao'] for c in lv['grade'][0]['celulas']}
    t('LVUT: smartphones janela 2 recuperada', cel[2] == 'recuperada', cel)
    # 150 itens contados; no fechamento ficaram 8 divergências em aberto (3 nunca regularizadas + 5 regularizadas
    # só em novembro): 10 × 142/150 = 9,47
    rec = r['por_chave']['recuperacao']
    t('Recuperação: desconta o que ficou em aberto no fechamento (10 × 142/150 = 9,47)', pts(r, 'recuperacao') == 9.47,
      (pts(r, 'recuperacao'), rec['resumo']))
    t('Recuperação: resumo diz quanto está em aberto e quanto foi regularizado',
      '8 divergência(s) em aberto em 150' in rec['resumo'] and '2 regularizada(s)' in rec['resumo'], rec['resumo'])
    t('Recuperação: ciclo fechado com desconto = não cumprida', rec['status'] == 'perdida')

    # Mês aberto: mesma situação vista em 12/10 (sem as de depois)
    r_aberto = calc(ana, hoje=date(2026, 10, 12))
    lv = r_aberto['por_chave']['lvut']
    t('mês aberto: SIM card com divergência fica bloqueada', lv['bloqueadas'] == 1, lv['bloqueadas'])
    t('mês aberto: janelas que ainda não acabaram ficam pendentes', lv['pendentes'] >= 8, lv['pendentes'])
    # Em 12/10: 140 itens contados até o dia; só o SIM card (3) em aberto, o smartphone já foi regularizado
    t('mês aberto: recuperação desconta só o que está em aberto (10 × 137/140 = 9,79)',
      pts(r_aberto, 'recuperacao') == 9.79, pts(r_aberto, 'recuperacao'))
    t('mês aberto: com desconto fica em andamento', r_aberto['por_chave']['recuperacao']['status'] == 'parcial')
    # Contagem anterior ao campo (sem itens contados) vale como itens = divergências
    ContagemEstoque.objects.create(loja=outra, categoria='PELICULAS', data=date(2026, 10, 20), divergencias=4)
    t('contagem sem itens contados pesa inteira (4 de 4 em aberto = 0)', pts(calc(bia), 'recuperacao') == 0,
      calc(bia)['por_chave']['recuperacao']['resumo'])

    # Fiscal: PDV que casa com a loja ("ZZ GUARDIAO <sufixo>"); 4 do mês: 1 saiu do SAP, 1 resolvida, 2 abertas
    pdv = f'ZZ GUARDIAO {sufixo}'

    def semnf(n, data_venda, **campos):
        return LinhaAuditoria.objects.create(chave=f'zzg{sufixo}{n}', tipo_erro='SEM NF', pdv=pdv,
                                             data_venda=data_venda, **campos)
    semnf(1, date(2026, 10, 2), ativa=False, sumiu_em=aware(date(2026, 10, 20)))
    semnf(2, date(2026, 10, 3), resolvida=True, resolvida_em=aware(date(2026, 10, 21)))
    semnf(3, date(2026, 10, 4))
    semnf(4, date(2026, 10, 5), ativa=False, sumiu_em=aware(date(2026, 11, 3)))   # saiu depois do fechamento
    semnf(5, date(2026, 9, 30))                                                   # venda de outro mês
    with mock.patch('auditoria_sap.models.inicio_da_ultima_leitura', return_value=None):
        r = calc(ana)
    t('Fiscal: 2 de 4 regularizadas no prazo = 5', pts(r, 'fiscal') == 5.0, r['por_chave']['fiscal']['resumo'])
    with mock.patch('auditoria_sap.models.inicio_da_ultima_leitura', return_value=aware(date(2026, 10, 25))):
        r = calc(ana)
    t('Fiscal: resolvida que o SAP releu e continua lá não vale (1 de 4 = 2,5)', pts(r, 'fiscal') == 2.5,
      r['por_chave']['fiscal']['resumo'])

    # Agenda: 2 itens no check-list (um com horário 09:00); outubro tem 31 dias, 4 domingos (pulados sem marcação)
    i1 = ItemChecklist.objects.create(loja=loja, titulo='Zz abrir caixa', horario=time(9, 0), ordem=1)
    i2 = ItemChecklist.objects.create(loja=loja, titulo='Zz limpar vitrine', ordem=2)
    ItemChecklist.objects.filter(pk__in=[i1.pk, i2.pk]).update(criado_em=aware(date(2026, 9, 1)))
    m1 = MarcacaoChecklist.objects.create(item=i1, data=date(2026, 10, 1), feito_por=ana)
    m2 = MarcacaoChecklist.objects.create(item=i1, data=date(2026, 10, 2), feito_por=ana)
    m3 = MarcacaoChecklist.objects.create(item=i2, data=date(2026, 10, 2), feito_por=ana)
    MarcacaoChecklist.objects.filter(pk=m1.pk).update(feito_em=aware(date(2026, 10, 1), 8, 50))   # no prazo
    MarcacaoChecklist.objects.filter(pk=m2.pk).update(feito_em=aware(date(2026, 10, 2), 9, 30))   # atrasado
    MarcacaoChecklist.objects.filter(pk=m3.pk).update(feito_em=aware(date(2026, 10, 2), 15, 0))   # sem horário
    rotina = RotinaGerencial.objects.create(user=ana, ativa=True)
    seg = AtividadeRotina.objects.create(rotina=rotina, dia_semana=0, inicio=time(10), fim=time(11), titulo='Zz reunião')
    AtividadeRotina.objects.filter(pk=seg.pk).update(criada_em=aware(date(2026, 9, 1)))
    c1 = ConclusaoAtividade.objects.create(atividade=seg, user=ana, data=date(2026, 10, 5))
    c2 = ConclusaoAtividade.objects.create(atividade=seg, user=ana, data=date(2026, 10, 12))
    ConclusaoAtividade.objects.filter(pk=c1.pk).update(criado_em=aware(date(2026, 10, 5), 11))     # no dia
    ConclusaoAtividade.objects.filter(pk=c2.pk).update(criado_em=aware(date(2026, 10, 13), 9))     # no dia seguinte
    r = calc(ana)
    det = r['por_chave']['agenda']['detalhe']
    # 31 dias − 4 domingos = 27 dias × 2 itens = 54 previstos; 2 no prazo (8:50 e o sem horário)
    t('Agenda: check-list 2 de 54 no prazo', (det['check_ok'], det['check_prev']) == (2, 54), det)
    # 4 segundas em outubro (5, 12, 19, 26); 1 concluída no dia
    t('Agenda: rotina 1 de 4 no dia', (det['rot_ok'], det['rot_prev']) == (1, 4), det)
    t('Agenda: 3 de 58 = 1,03 ponto', pts(r, 'agenda') == round(20 * 3 / 58, 2), pts(r, 'agenda'))

    # Hoje: o que ainda dá tempo não perde ponto (visto em 02/10 às cegas: o dia 2 só conta o que foi feito)
    r2 = calc(ana, hoje=date(2026, 10, 2))
    det = r2['por_chave']['agenda']['detalhe']
    t('Agenda no dia: hoje entra só o feito (2 de ontem + 2 de hoje)', det['check_prev'] == 4 and det['check_ok'] == 2, det)

    # Missão manual não soma pontos
    antes = pts(calc(ana), 'agenda')
    MissaoAgenda.objects.create(pessoa=ana, titulo='Zz organizar acessórios', prazo=date(2026, 10, 10),
                                status='CUMPRIDA', cumprida_em=aware(date(2026, 10, 9)))
    r = calc(ana)
    t('Missão manual não muda a nota da agenda', pts(r, 'agenda') == antes)
    t('Missão manual aparece no quadro como cumprida', any(c['manual'] and c['status'] == 'CUMPRIDA' for c in r['cartoes']))

    # Total, pilares e lote
    t('pilares somam o total', round(sum(p['pontos'] for p in r['pilares']), 2) == r['total'])
    t('PREVENIR máximo 20 / PROTEGER 40 / GARANTIR 40', [p['maximo'] for p in r['pilares']] == [20, 40, 40])
    b = guardiao.Base(out_ini, out_fim, hoje_nov).preparar([ana, bia, gil])
    for p in (ana, bia, gil):
        um = [m['pontos'] for m in guardiao.calcular(p, '2026-10', hoje=hoje_nov, com_cartoes=False)['missoes']]
        lote = [m['pontos'] for m in guardiao.calcular(p, base=b, com_cartoes=False)['missoes']]
        t(f'lote igual ao individual ({p.first_name})', um == lote, (um, lote))
    rk = guardiao.ranking_do_mes('2026-10', hoje=hoje_nov)
    nomes = [l['pessoa'].id for l in rk['linhas']]
    t('ranking tem os Guardiões de loja (ana e bia), não o gestor de escritório', ana.id in nomes and bia.id in nomes
      and gil.id not in nomes)
    t('ranking em ordem de total', [l['total'] for l in rk['linhas']] == sorted([l['total'] for l in rk['linhas']], reverse=True))
    t('ana na frente de bia', nomes.index(ana.id) < nomes.index(bia.id))

    # ── Telas ──
    with mock.patch.object(requests.Session, 'request', sem_rede):
        c_ana, c_bia, c_gil, c_zeca = Client(), Client(), Client(), Client()
        c_ana.force_login(ana)
        c_bia.force_login(bia)
        c_gil.force_login(gil)
        c_zeca.force_login(zeca)
        for aba in ('', 'missoes/', 'lvut/', 'agenda/', 'destaque/'):
            resp = c_ana.get(f'/rotina-adm/guardiao/{aba}')
            t(f'aba {aba or "visão geral"} abre (200)', resp.status_code == 200, resp.status_code)
        resp = c_ana.get('/rotina-adm/guardiao/codigo/?mes=2026-10')
        t('Código de Conquista saiu: o link antigo vai para a visão geral', resp.status_code == 302
          and resp['Location'] == '/rotina-adm/guardiao/?mes=2026-10', resp.get('Location'))
        resp = c_ana.get('/rotina-adm/guardiao/?mes=2026-10')
        html = resp.content.decode()
        t('visão geral mostra o título e o ciclo', 'Missão Guardião' in html and 'Ciclo 10/2026' in html)
        t('visão geral mostra "Como a nota foi formada"', 'Como a nota foi formada' in html and 'Renova — Conformidade SAP' in html)
        t('visão geral mostra o total formatado', f'{r["total_txt"]}%' in html, r['total_txt'])
        t('menu da Rotina ADM tem a aba Missão Guardião', 'Missão Guardião</a>' in html)
        t('a aba Código de Conquista não aparece mais', 'Código de Conquista' not in html and '/guardiao/codigo/' not in html)
        resp = c_zeca.get('/rotina-adm/guardiao/')
        t('sem acesso volta para a home', resp.status_code == 302 and '/rotina-adm/' not in resp['Location'])
        resp = c_ana.get(f'/rotina-adm/guardiao/?pessoa={bia.id}&mes=2026-10')
        t('Visualizador não vê a missão de outra pessoa', resp.context['pessoa'].id == ana.id)
        resp = c_gil.get(f'/rotina-adm/guardiao/?pessoa={bia.id}&mes=2026-10')
        t('Gestor escolhe o Guardião', 'Bia Zz' in resp.content.decode() and resp.context['pessoa'].id == bia.id)

        # CheckLojas deste mês pela própria loja
        hoje = timezone.localdate()
        resp = c_bia.post('/rotina-adm/guardiao/lancar/', {'acao': 'checklojas', 'observacao': 'tudo certo'})
        lanc = LancamentoGuardiao.objects.filter(loja=outra, competencia=hoje.replace(day=1), missao='CHECKLOJAS').first()
        t('Visualizador registra o CheckLojas da loja dele', resp.status_code == 302 and lanc and lanc.data == hoje)
        resp = c_bia.post('/rotina-adm/guardiao/lancar/', {'acao': 'estoque_total', 'valor': '90'})
        t('Visualizador não lança o Estoque Total', not LancamentoGuardiao.objects.filter(loja=outra, missao='ESTOQUE_TOTAL').exists())
        resp = c_gil.post('/rotina-adm/guardiao/lancar/', {'acao': 'estoque_total', 'valor': '87,5', 'pessoa': bia.id,
                                                            'mes': '2026-10', 'data': '2026-10-31'})
        lanc = LancamentoGuardiao.objects.filter(loja=outra, missao='ESTOQUE_TOTAL', competencia=out_ini).first()
        t('Gestor lança o Estoque Total de um mês passado', lanc and lanc.valor == Decimal('87.50'))
        resp = c_gil.post('/rotina-adm/guardiao/lancar/', {'acao': 'estoque_total', 'valor': '120', 'pessoa': bia.id})
        t('Estoque Total acima de 100 é recusado', not LancamentoGuardiao.objects.filter(
            loja=outra, missao='ESTOQUE_TOTAL', competencia=hoje.replace(day=1)).exists())

        # Contagem e regularização pela tela
        c_bia.post('/rotina-adm/guardiao/contagem/', {'categoria': 'SIMCARD', 'divergencias': '2'})
        t('contagem sem itens contados é recusada', not ContagemEstoque.objects.filter(loja=outra, data=hoje).exists())
        c_bia.post('/rotina-adm/guardiao/contagem/', {'categoria': 'SIMCARD', 'itens_contados': '3', 'divergencias': '5'})
        t('divergências acima do contado são recusadas', not ContagemEstoque.objects.filter(loja=outra, data=hoje).exists())
        t('antes da contagem a recuperação do mês está nos 10', guardiao.calcular(bia)['por_chave']['recuperacao']['pontos'] == 10)
        resp = c_bia.post('/rotina-adm/guardiao/contagem/', {'categoria': 'SIMCARD', 'itens_contados': '40',
                                                             'divergencias': '2'})
        cont = ContagemEstoque.objects.filter(loja=outra, data=hoje, categoria='SIMCARD').first()
        t('contagem com divergência registrada com os itens contados', cont and cont.divergencias == 2
          and cont.itens_contados == 40)
        r_b = guardiao.calcular(bia)
        t('contagem com divergência fica bloqueada no mês aberto', r_b['por_chave']['lvut']['bloqueadas'] == 1)
        t('divergência em aberto tira pontos da recuperação (10 × 38/40 = 9,5)',
          r_b['por_chave']['recuperacao']['pontos'] == 9.5, r_b['por_chave']['recuperacao']['pontos'])
        resp = c_ana.post(f'/rotina-adm/guardiao/contagem/{cont.id}/regularizar/', {'regularizacao': 'x'})
        cont.refresh_from_db()
        t('outra loja não regulariza', cont.regularizada_em is None)
        resp = c_bia.post(f'/rotina-adm/guardiao/contagem/{cont.id}/regularizar/', {'regularizacao': ''})
        cont.refresh_from_db()
        t('regularizar exige a evidência', cont.regularizada_em is None)
        resp = c_bia.post(f'/rotina-adm/guardiao/contagem/{cont.id}/regularizar/', {'regularizacao': 'ajuste no SAP'})
        cont.refresh_from_db()
        t('regularização grava quem e quando e mantém o número original', cont.regularizada_em and
          cont.regularizada_por_id == bia.id and cont.divergencias == 2)
        r_b = guardiao.calcular(bia)
        t('regularizada vira conquista recuperada', r_b['por_chave']['lvut']['bloqueadas'] == 0
          and r_b['por_chave']['lvut']['conquistadas'] >= 1)
        t('regularizar devolve os pontos da recuperação (volta a 10)', r_b['por_chave']['recuperacao']['pontos'] == 10)

        # Missão manual: incluir, mover, outra pessoa não mexe, excluir
        resp = c_bia.post('/rotina-adm/guardiao/missao/', {'acao': 'nova', 'titulo': 'Zz conferir equipamentos',
                                                           'origem': 'GERENTE', 'prazo': hoje.isoformat()})
        m = MissaoAgenda.objects.filter(pessoa=bia, titulo='Zz conferir equipamentos').first()
        t('Guardião inclui missão na própria agenda', m is not None and m.status == 'ATRIBUIDA')
        c_ana.post('/rotina-adm/guardiao/missao/', {'acao': 'mover', 'missao': m.id, 'status': 'CUMPRIDA'})
        m.refresh_from_db()
        t('outra pessoa não move a missão', m.status == 'ATRIBUIDA')
        c_bia.post('/rotina-adm/guardiao/missao/', {'acao': 'mover', 'missao': m.id, 'status': 'CUMPRIDA'})
        m.refresh_from_db()
        t('dono move para Cumprida e grava quando', m.status == 'CUMPRIDA' and m.cumprida_em is not None)
        c_ana.post('/rotina-adm/guardiao/missao/', {'acao': 'nova', 'titulo': 'Zz intrusa', 'prazo': hoje.isoformat(),
                                                    'pessoa': bia.id})
        t('Visualizador não inclui missão para outra pessoa', not MissaoAgenda.objects.filter(pessoa=bia, titulo='Zz intrusa').exists())
        c_gil.post('/rotina-adm/guardiao/missao/', {'acao': 'nova', 'titulo': 'Zz do gestor', 'prazo': hoje.isoformat(),
                                                    'pessoa': bia.id, 'origem': 'LIDERANCA'})
        t('Gestor atribui missão ao Guardião', MissaoAgenda.objects.filter(pessoa=bia, titulo='Zz do gestor',
                                                                            origem='LIDERANCA').exists())
        c_bia.post('/rotina-adm/guardiao/missao/', {'acao': 'excluir', 'missao': m.id})
        t('dono exclui a missão', not MissaoAgenda.objects.filter(pk=m.pk).exists())
        resp = c_bia.get('/rotina-adm/guardiao/agenda/')
        t('agenda mostra a missão atribuída pelo gestor', 'Zz do gestor' in resp.content.decode())
        resp = c_gil.get('/rotina-adm/guardiao/destaque/?mes=2026-10')
        t('Guardiões em Destaque lista a ana', 'Ana Zz' in resp.content.decode())
        # Destaque visual (27/11/2026): Faixas ao lado do pódio e a classificação em barras com a pessoa em foco.
        html = resp.content.decode()
        t('destaque tem o card Faixas com as 5 faixas', 'data-faixas' in html and html.count('data-faixa="') == 5
          and 'Missão de Excelência' in html)
        t('faixas marcam uma faixa atual', html.count('data-faixa="atual"') == 1)
        resp = c_ana.get('/rotina-adm/guardiao/destaque/?mes=2026-10')
        html = resp.content.decode()
        ctx = resp.context
        linhas = ctx['ranking']
        t('pódio + demais = todos do ranking', len(ctx['podio']) + len(ctx['demais']) == len(linhas))
        eu_no_podio = any(l['pessoa'].id == ana.id for l in ctx['podio'])
        t('a pessoa aparece destacada (pódio ou barra)',
          ('mg-degrau p' in html and ' eu"' in html) if eu_no_podio else 'data-eu' in html, eu_no_podio)
        t('faixa do card = nível da pessoa', ctx['faixas']['atual']['nome'] == ctx['nivel']['nome'])
        if ctx['demais']:
            t('barras divididas por pilar', 'mg-barra-pilares' in html and html.count('class="marco"') == 3 * len(ctx['demais']))
        resp = c_ana.get('/rotina-adm/ranking/?mes=2026-10')
        t('URL antiga do ranking leva ao Destaque', resp.status_code == 302
          and resp['Location'] == '/rotina-adm/guardiao/destaque/?mes=2026-10', resp.get('Location'))


if __name__ == '__main__':
    try:
        with transaction.atomic():
            rodar()
            raise Desfaz()
    except Desfaz:
        pass
    print()
    if FALHAS:
        print(f'{len(FALHAS)} FALHA(S):')
        for f in FALHAS:
            print(' -', f)
        sys.exit(1)
    print('Tudo certo.')
