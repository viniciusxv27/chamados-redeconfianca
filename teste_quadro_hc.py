"""Quadro de HC, projeção diária do simulador e o agendador diário.

Roda numa transação desfeita no fim. Os lembretes não saem: o envio é trocado
por um dublê que só anota.
"""
import json
import os
import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.utils import timezone

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
    executor = MigrationExecutor(connection)
    alvos = [n for app in ('core', 'simulator', 'quadro_hc') for n in executor.loader.graph.leaf_nodes(app)]
    if executor.migration_plan(alvos):
        executor.migrate(alvos)

    from core import agendador_diario
    from core.models import TarefaDiaria
    from quadro_hc import servicos
    from quadro_hc.models import Carteira, MovimentacaoHC, ObservacaoHC, VagaHC
    from simulator import projecao
    from simulator.models import ProjecaoSimulador
    from users.models import Sector, UserChangeLog

    hoje = timezone.localdate()
    A = Sector.objects.create(name='Loja ZZ Alfa', adabas='ZZX001')
    B = Sector.objects.create(name='Loja ZZ Beta', adabas='ZZX002')

    def pessoa(username, setor, cargo, **kw):
        u = User.objects.create_user(username=username, email=f'{username}@exemplo-teste.local', password='S3nha!teste',
                                     first_name=kw.pop('first_name', 'ZZ'), last_name=kw.pop('last_name', username),
                                     sector=setor, job_title=cargo, **kw)
        if setor:
            u.sectors.add(setor)
        return u

    chefe = pessoa('zzhc.super', None, 'DIRETOR', hierarchy='SUPERADMIN')
    comum = pessoa('zzhc.comum', A, 'CONSULTOR DE VENDAS')
    gerente = pessoa('zzhc.ger', A, 'GERENTE DE VENDAS')
    c1 = pessoa('zzhc.c1', A, 'CONSULTOR DE VENDAS', admission_date=hoje - timedelta(days=40), city='SERRA',
                neighborhood='JACARAIPE', login_code='81000001', phone='27 99999-0001')
    c2 = pessoa('zzhc.c2', B, 'CONSULTOR DE VENDAS')
    ca, cc = Client(), Client()
    ca.force_login(chefe)
    cc.force_login(comum)

    def post(url, corpo, cliente=ca):
        return cliente.post(url, json.dumps(corpo), content_type='application/json')

    print('== ACESSO ==')
    r = cc.get('/quadro-hc/')
    t('quem não é SUPERADMIN não entra', r.status_code == 302)
    t('nem usa a API', post('/quadro-hc/simular/', {}, cc).status_code == 403)
    r = ca.get('/quadro-hc/')
    t('SUPERADMIN abre o quadro', r.status_code == 200, r.status_code)
    t('o menu mostra o Quadro de HC só para SUPERADMIN', 'href="/quadro-hc/"' in r.content.decode()
      and 'href="/quadro-hc/"' not in cc.get('/facilitador/').content.decode())

    print('\n== CARTEIRAS INICIAIS (migração) ==')
    nomes = list(Carteira.objects.values_list('nome', flat=True))
    t('as 4 carteiras da planilha existem', {'SERRÃO', 'VITÓRIA VELHA', 'INTERIOR RJ/ES', 'SPC'} <= set(nomes), nomes)
    serrao = Carteira.objects.filter(nome='SERRÃO').first()
    t('lojas achadas pelo nome (Montserrat no Serrão)', serrao and serrao.lojas.filter(name__icontains='Montserrat').exists())

    print('\n== QUADRO LIGADO AO CADASTRO ==')
    html = r.content.decode()
    t('a loja aparece com as pessoas', 'Loja ZZ Alfa' in html and 'ZZ zzhc.c1' in html)
    t('mostra cidade, login, telefone e janelas de experiência',
      'JACARAIPE - SERRA' in html and '81000001' in html and (c1.admission_date + timedelta(days=44)).strftime('%d/%m/%y') in html)
    novato = pessoa('zzhc.novo', B, 'RECEPCIONISTA')
    html = ca.get('/quadro-hc/').content.decode()
    t('colaborador novo já entra no quadro da loja dele', 'ZZ zzhc.novo' in html)
    q = servicos.montar_quadro()
    blocos = {b['setor'].id: b for c in q['carteiras'] for b in c['lojas']}
    t('loja segmentada por cargo (gerente antes de consultor)',
      [p['grupo'] for p in blocos[A.id]['pessoas']][:1] == ['Gerente'], [p['grupo'] for p in blocos[A.id]['pessoas']])
    t('loja sem gerente é sinalizada', blocos[B.id]['sem_gerente'] and not blocos[A.id]['sem_gerente'])
    t('lojas sem carteira ficam num grupo próprio', any(c['nome'] == 'Lojas sem carteira' and A.id in [b['setor'].id for b in c['lojas']]
                                                     for c in q['carteiras']))
    t('cargos do cadastro nas opções', 'CONSULTOR DE VENDAS' in servicos.cargos_disponiveis())

    print('\n== SIMULAR ANTES DE SALVAR ==')
    movs_antes = MovimentacaoHC.objects.count()
    r = post('/quadro-hc/simular/', {'colaborador': c1.pk, 'novo_setor': B.pk, 'data': hoje.isoformat()})
    s = r.json()
    imp = {i['loja']: {l['grupo']: (l['antes'], l['depois']) for l in i['linhas']} for i in s['impacto']}
    t('simulação mostra o antes → depois do cadastro', s['mudancas'] == [{'campo': 'Loja', 'antes': 'Loja ZZ Alfa', 'depois': 'Loja ZZ Beta'}], s)
    t('e o efeito nas duas lojas', imp.get('Loja ZZ Alfa', {}).get('Consultor') == (2, 1)
      and imp.get('Loja ZZ Beta', {}).get('Consultor') == (1, 2), imp)
    t('simular não grava nada', User.objects.get(pk=c1.pk).sector_id == A.id and MovimentacaoHC.objects.count() == movs_antes)
    s = post('/quadro-hc/simular/', {'colaborador': gerente.pk, 'novo_setor': B.pk, 'data': hoje.isoformat()}).json()
    t('avisa quando a loja fica sem gerente', any('fica sem gerente' in a for a in s['avisos']), s['avisos'])
    r = post('/quadro-hc/simular/', {'colaborador': c1.pk, 'data': hoje.isoformat()})
    t('movimentação sem mudança é recusada', r.status_code == 400 and 'Nada muda' in r.json()['erro'])
    r = post('/quadro-hc/simular/', {'colaborador': c1.pk, 'desligar': True, 'novo_setor': B.pk})
    t('desligar + mudar de loja é recusado', r.status_code == 400)

    print('\n== APLICAR AGORA (grava no cadastro) ==')
    r = post('/quadro-hc/movimentar/', {'colaborador': c1.pk, 'novo_setor': B.pk, 'novo_cargo': 'CONSULTOR DE VENDAS PL',
                                       'data': hoje.isoformat(), 'observacao': 'reforço'})
    c1.refresh_from_db()
    t('muda loja e cargo no cadastro', r.status_code == 200 and c1.sector_id == B.id and c1.job_title == 'CONSULTOR DE VENDAS PL', r.content[:200])
    t('troca a loja também na lista de setores', set(c1.sectors.values_list('id', flat=True)) == {B.id})
    logs = UserChangeLog.objects.filter(target=c1, changed_by=chefe)
    t('o histórico do cadastro registra quem fez', {'sector', 'job_title', 'sectors'} <= set(logs.values_list('field', flat=True)))
    m = MovimentacaoHC.objects.get(colaborador=c1)
    t('movimentação aplicada com a frase do histórico', m.situacao == 'APLICADA' and 'foi para a Loja ZZ Beta' in m.resumo
      and m.aplicado_por == chefe, m.resumo)
    html = ca.get('/quadro-hc/').content.decode()
    t('a frase aparece ao lado do nome, com quem fez', 'foi para a Loja ZZ Beta' in html and 'por ZZ zzhc.super' in html)

    print('\n== CADASTRO → QUADRO ==')
    from users.auditoria import fotografar, registrar
    antes = fotografar(c2)
    c2.sector = A
    c2.save()
    registrar(c2, antes, comum)
    q = servicos.montar_quadro()
    blocos = {b['setor'].id: b for c in q['carteiras'] for b in c['lojas']}
    pessoa_c2 = next(p for p in blocos[A.id]['pessoas'] if p['id'] == c2.pk)
    t('mudar a loja pelo cadastro já muda no quadro', pessoa_c2 is not None)
    t('e o histórico mostra a alteração do cadastro', any('Loja ZZ Beta → Loja ZZ Alfa' in h['frase'] for h in pessoa_c2['historico']),
      pessoa_c2['historico'])

    print('\n== NOME ==')
    post('/quadro-hc/movimentar/', {'colaborador': c2.pk, 'novo_nome': 'Maria Clara Souza', 'data': hoje.isoformat()})
    c2.refresh_from_db()
    t('corrigir nome grava nome e sobrenome', c2.first_name == 'Maria' and c2.last_name == 'Clara Souza')

    print('\n== AGENDAR (lembrete 2 dias antes) ==')
    dia = hoje + timedelta(days=5)
    r = post('/quadro-hc/movimentar/', {'colaborador': novato.pk, 'desligar': True, 'data': dia.isoformat(),
                                       'observacao': 'fim do contrato'})
    ag = MovimentacaoHC.objects.get(colaborador=novato)
    novato.refresh_from_db()
    t('data futura agenda e não mexe no cadastro', r.json()['situacao'] == 'AGENDADA' and novato.is_active
      and novato.demission_date is None)
    html = ca.get('/quadro-hc/?aba=futuras').content.decode()
    t('aparece na aba de movimentações futuras', 'desligado(a) da Loja ZZ Beta' in html and 'Aplicar agora' in html)
    s = post('/quadro-hc/simular/', {'colaborador': novato.pk, 'novo_cargo': 'CONSULTOR DE VENDAS', 'data': hoje.isoformat()}).json()
    t('simulação avisa da movimentação já agendada', any('Já existe movimentação agendada' in a for a in s['avisos']))
    avisos = []
    with mock.patch.object(servicos, '_avisar', side_effect=lambda m, tit, msg: avisos.append((m.pk, tit)) or True):
        servicos.enviar_lembretes(hoje=dia - timedelta(days=3))
        t('3 dias antes ainda não avisa', avisos == [])
        servicos.enviar_lembretes(hoje=dia - timedelta(days=2))
        t('2 dias antes avisa', avisos == [(ag.pk, 'Quadro de HC: movimentação em 2 dia(s)')], avisos)
        servicos.enviar_lembretes(hoje=dia - timedelta(days=1))
        t('e não repete no dia seguinte', len(avisos) == 1)
        servicos.enviar_lembretes(hoje=dia)
        t('no dia avisa para aplicar', len(avisos) == 2 and 'aplicar hoje' in avisos[-1][1])
    destinos = servicos._destinos(ag)
    t('lembrete vai para os SUPERADMIN', chefe in destinos)
    r = post(f'/quadro-hc/movimentacao/{ag.pk}/aplicar/', {})
    novato.refresh_from_db()
    t('aplicar a agendada desliga no cadastro', r.status_code == 200 and not novato.is_active
      and novato.demission_date == dia and novato.status == 'INATIVO')
    q = servicos.montar_quadro()
    t('desligado sai do quadro', novato.pk not in [p['id'] for c in q['carteiras'] for b in c['lojas'] for p in b['pessoas']])
    r = post(f'/quadro-hc/movimentacao/{ag.pk}/cancelar/', {})
    t('não cancela o que já foi aplicado', r.status_code == 400)
    ag2 = servicos.registrar_movimentacao({'colaborador': c2.pk, 'lembrete': 'conversar sobre promoção',
                                           'data': (hoje + timedelta(days=3)).isoformat()}, chefe)[0]
    post(f'/quadro-hc/movimentacao/{ag2.pk}/cancelar/', {})
    t('cancelar agendada', MovimentacaoHC.objects.get(pk=ag2.pk).situacao == 'CANCELADA')

    print('\n== OBSERVAÇÃO, VAGA, CARTEIRA ==')
    post('/quadro-hc/observacao/', {'colaborador': c1.pk, 'texto': 'Apoio SPC'})
    t('observação salva', ObservacaoHC.objects.get(colaborador=c1).texto == 'Apoio SPC')
    post('/quadro-hc/vaga/', {'setor': B.pk, 'cargo': 'CONSULTOR DE VENDAS', 'observacao': 'substituição'})
    vaga = VagaHC.objects.get(setor=B)
    t('vaga em aberto aparece na loja', 'VAGA EM ABERTO' in ca.get('/quadro-hc/').content.decode())
    post(f'/quadro-hc/vaga/{vaga.pk}/fechar/', {})
    t('fechar vaga', VagaHC.objects.get(pk=vaga.pk).fechada_em is not None)
    post('/quadro-hc/carteira/', {'nome': 'ZZ Carteira', 'coordenador_nome': 'Fulano', 'lojas': [A.pk, B.pk]})
    zz = Carteira.objects.get(nome='ZZ Carteira')
    post('/quadro-hc/carteira/', {'nome': 'ZZ Outra', 'lojas': [B.pk]})
    t('loja fica em uma carteira só', list(zz.lojas.values_list('id', flat=True)) == [A.pk])
    q = servicos.montar_quadro()
    t('o quadro agrupa pela carteira', any(c['nome'] == 'ZZ Carteira' and c['coordenacao'] == 'Fulano' for c in q['carteiras']))

    print('\n== PROJEÇÃO DO SIMULADOR (tabela no banco) ==')
    linhas = [{'id': c1.pk, 'name': 'ZZ c1', 'role_key': 'consultor', 'role_label': 'Consultor', 'sector': 'LOJA ZZ',
               'sector_id': B.pk, 'coordinator': 'X', 'realizado': 1000.4, 'projecao': 1500.555, 'diferenca': 500.15,
               'motivo': 'Móvel +R$ 500', 'fatores': [{'label': 'Móvel'}], 'has_data': True, 'erro': ''}]
    ref = date(2031, 1, 5)
    t('grava uma linha por pessoa no dia', projecao.gravar(linhas, ref) == 1
      and ProjecaoSimulador.objects.get(referencia=ref, user=c1).projecao == Decimal('1500.56'))
    projecao.gravar(linhas + [dict(linhas[0])], ref)
    t('rodar de novo no mesmo dia troca (não duplica)', ProjecaoSimulador.objects.filter(referencia=ref).count() == 1)
    with mock.patch('simulator.projecao.construir_linhas', return_value=linhas):
        r = projecao.gerar_projecao_diaria()
    t('a tarefa diária grava o retrato de hoje', ProjecaoSimulador.objects.filter(referencia=hoje).count() == 1, r)

    print('\n== GERENTE PELO GRUPO, DEMISSÃO ANTIGA E AJUSTE MANUAL (20/10/2026) ==')
    from communications.models import CommunicationGroup
    from quadro_hc.models import AjusteHC, HCPrevisto
    G = Sector.objects.create(name='Loja ZZ Gama', adabas='ZZX003')
    ger_grupo = pessoa('zzhc.gg', G, 'CONSULTOR DE VENDAS')
    grupo_ger = CommunicationGroup.objects.filter(name__iexact='GERENTES').first() or CommunicationGroup.objects.create(name='GERENTES')
    ger_grupo.communication_groups.add(grupo_ger)
    q = servicos.montar_quadro()
    bl = {b['setor'].id: b for c in q['carteiras'] for b in c['lojas']}
    pg = next(p for p in bl[G.id]['pessoas'] if p['id'] == ger_grupo.id)
    t('no grupo GERENTES e lotado na loja: conta como gerente', pg['grupo'] == 'Gerente' and pg['origem_funcao'] == 'grupo', pg['grupo'])
    t('e a loja deixa de estar "sem gerente"', not bl[G.id]['sem_gerente'])
    t('a tela marca "gerente pelo grupo"', 'gerente pelo grupo' in ca.get('/quadro-hc/').content.decode())
    antiga = pessoa('zzhc.antiga', G, 'GERENTE OPERACIONAL', demission_date=date(2024, 11, 1), status='ATIVO',
                    login_code='81079439.0')
    saiu = pessoa('zzhc.saiu', G, 'CONSULTOR DE VENDAS', demission_date=hoje - timedelta(days=3), status='INATIVO')
    q = servicos.montar_quadro()
    gente = {p['id']: p for p in next(b for c in q['carteiras'] for b in c['lojas'] if b['setor'].id == G.id)['pessoas']}
    t('ativa com demissão antiga no cadastro entra no quadro', antiga.id in gente and gente[antiga.id]['demissao_antiga'] == date(2024, 11, 1))
    t('e não aparece como "sai em"', gente.get(antiga.id, {}).get('sai_em') is None)
    t('desligada de fato (status inativo) fica de fora', saiu.id not in gente)
    t('login de planilha sem ".0"', gente.get(antiga.id, {}).get('login') == '81079439')
    t('o quadro conta as demissões antigas', q['demissao_antiga'] >= 1)
    html = ca.get('/quadro-hc/').content.decode()
    t('e avisa no topo', 'data de demissão no passado' in html and 'demissão 01/11/24 no cadastro' in html)

    r = post('/quadro-hc/ajuste/', {'colaborador': comum.pk, 'funcao': 'Gerente'})
    t('ajuste manual pede motivo', r.status_code == 400 and 'motivo' in r.json()['erro'])
    r = post('/quadro-hc/ajuste/', {'colaborador': comum.pk, 'funcao': 'Gerente', 'setor': G.pk, 'motivo': 'é a gerente de fato'})
    t('ajuste salvo', r.status_code == 200 and AjusteHC.objects.filter(colaborador=comum, funcao='Gerente', setor=G).exists(), r.content)
    q = servicos.montar_quadro()
    bl = {b['setor'].id: b for c in q['carteiras'] for b in c['lojas']}
    pc = next((p for p in bl[G.id]['pessoas'] if p['id'] == comum.id), None)
    t('a pessoa passa a contar na loja e função do ajuste', pc and pc['grupo'] == 'Gerente' and pc['origem_funcao'] == 'manual')
    t('e sai da loja do cadastro', all(p['id'] != comum.id for p in bl[A.id]['pessoas']))
    t('o cadastro não muda', User.objects.get(pk=comum.pk).sector_id == A.id and User.objects.get(pk=comum.pk).job_title == 'CONSULTOR DE VENDAS')
    post('/quadro-hc/ajuste/', {'colaborador': c2.pk, 'situacao': 'EXCLUIR', 'motivo': 'emprestado ao escritório'})
    q = servicos.montar_quadro()
    t('"não contar" tira do quadro e lista à parte', all(p['id'] != c2.pk for c in q['carteiras'] for b in c['lojas'] for p in b['pessoas'])
      and any(f['id'] == c2.pk for f in q['fora']))
    post('/quadro-hc/ajuste/', {'colaborador': saiu.pk, 'situacao': 'INCLUIR', 'motivo': 'volta semana que vem'})
    q = servicos.montar_quadro()
    t('"contar sempre" põe no quadro quem o cadastro tira', any(p['id'] == saiu.pk for c in q['carteiras'] for b in c['lojas'] for p in b['pessoas']))
    post('/quadro-hc/ajuste/', {'colaborador': comum.pk, 'situacao': 'AUTO'})
    t('tirar o ajuste volta ao cadastro', not AjusteHC.objects.filter(colaborador=comum).exists())

    r = post('/quadro-hc/previsto/', {'setor': G.pk, 'quantidades': {'Gerente': 1, 'Consultor': 4, 'Recepção': 0}})
    t('HC previsto salvo', r.status_code == 200 and HCPrevisto.objects.filter(setor=G).count() == 2, r.content)
    q = servicos.montar_quadro()
    bg = next(b for c in q['carteiras'] for b in c['lojas'] if b['setor'].id == G.id)
    comp = {c['grupo']: c for c in bg['comparacao']}
    t('compara real × previsto por função', bg['total_previsto'] == 5 and comp['Consultor']['previsto'] == 4
      and comp['Consultor']['falta'] == 4 - comp['Consultor']['real'], bg['comparacao'])
    t('a loja mostra HC real/previsto', f"HC {bg['total']}/5" in ca.get('/quadro-hc/').content.decode())
    r = post('/quadro-hc/previsto/', {'setor': G.pk, 'quantidades': {'Consultor': 'x'}})
    t('quantidade inválida avisa', r.status_code == 400)

    print('\n== VAGA EM ABERTO PELO HC PREVISTO ==')
    falta_cons = comp['Consultor']['falta']
    previstas = [v['grupo'] for v in bg['vagas_previstas']]
    t('um card "Vaga em aberto" para cada pessoa que falta', previstas.count('Consultor') == falta_cons > 0, previstas)
    t('função completa não gera vaga', 'Gerente' not in previstas or comp['Gerente']['falta'] > 0)
    html = ca.get('/quadro-hc/').content.decode()
    t('a tela mostra o card com o motivo', 'pelo HC previsto' in html and 'data-vaga-prevista' in html and 'data-preencher=' in html)
    VagaHC.objects.create(setor=G, cargo='CONSULTOR DE VENDAS', observacao='já aberta', criado_por=chefe)
    q = servicos.montar_quadro()
    bg = next(b for c in q['carteiras'] for b in c['lojas'] if b['setor'].id == G.id)
    t('vaga aberta à mão para a função conta (não duplica)',
      [v['grupo'] for v in bg['vagas_previstas']].count('Consultor') == falta_cons - 1 and bg['total_vagas'] == falta_cons)
    t('o topo soma as vagas previstas', q['vagas'] >= bg['total_vagas'] and q['vagas_previstas'] >= falta_cons - 1)

    print('\n== AGENDADOR DIÁRIO ==')
    chamadas = []
    tarefas = [('zz_teste', time(8, 0), 'builtins.print', 'teste')]
    with mock.patch.object(agendador_diario, 'TAREFAS', tarefas), \
         mock.patch.object(agendador_diario, '_funcao', return_value=lambda: chamadas.append(1) or 'feito'):
        cedo = timezone.make_aware(datetime.combine(hoje, time(7, 59)))
        agendador_diario._ultima_checagem = None
        t('antes das 08:00 não roda', agendador_diario.disparar_tarefas_vencidas(cedo, em_thread=False) == [])
        depois = timezone.make_aware(datetime.combine(hoje, time(8, 5)))
        agendador_diario._ultima_checagem = None
        t('depois das 08:00 roda', agendador_diario.disparar_tarefas_vencidas(depois, em_thread=False) == ['zz_teste']
          and chamadas == [1])
        agendador_diario._ultima_checagem = None
        mais_tarde = timezone.make_aware(datetime.combine(hoje, time(15, 0)))
        t('uma vez por dia', agendador_diario.disparar_tarefas_vencidas(mais_tarde, em_thread=False) == [] and chamadas == [1])
        t('grava o resultado', TarefaDiaria.objects.get(chave='zz_teste').sucesso is True)
    t('as duas tarefas reais estão registradas', {t_[0] for t_ in agendador_diario.TAREFAS} == {'simulador_projecao', 'quadro_hc_lembretes'})
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado; nenhum lembrete foi enviado.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
