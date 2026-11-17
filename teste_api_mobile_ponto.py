"""Módulo Ponto no app (/api/mobile/v1/m/ponto/): marcações, solicitações ao DP, férias e escala.

Roda dentro de uma transação desfeita no fim: não grava nada no banco. A API do
Tangerino é trocada por dublês (nenhuma chamada sai daqui) e os anexos vão para um
storage em memória (nada sobe para o MinIO).
"""
import os
import sys
from datetime import datetime, time, timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
# Caches em memória ANTES do setup: o 'default' é o Redis remoto compartilhado (clear() nele = FLUSHDB).
import redeconfianca.settings as _cfg  # noqa: E402
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'}
               for n in _cfg.CACHES}
django.setup()

from django.conf import settings
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client
from django.utils import timezone

from communications.models import CommunicationGroup
from solicitacoes_dp.models import Anexo, Evento, ResponsavelDP, Solicitacao
from tangerino import client as tg_client
from tangerino.models import ConfiguracaoTangerino, Escala, EscalaDia, SolicitacaoAjustePonto
from users.models import Sector, UserDocument

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
M = f'{BASE}/m/ponto'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


# Nada sobe para o MinIO: anexo e documento arquivado ficam em memória.
memoria = InMemoryStorage()
Anexo._meta.get_field('arquivo').storage = memoria
UserDocument._meta.get_field('file').storage = memoria

# Toda ida à API do Tangerino passa por aqui: anota e falha (nenhuma chamada real sai).
chamadas_api = []


def _request_falso(metodo, base, caminho, **kw):
    chamadas_api.append((metodo, caminho))
    raise tg_client.TangerinoError('API desligada no teste')


EID = 987650001
hoje = timezone.localdate()
ontem = hoje - timedelta(days=1)


def ms(dia, h, m=0):
    return int(timezone.make_aware(datetime.combine(dia, time(h, m))).timestamp() * 1000)


PARES = [
    {'id': 1, 'dateIn': ms(ontem, 8), 'dateOut': None},                 # ontem: entrou e não saiu
    {'id': 2, 'dateIn': ms(hoje, 8), 'dateOut': ms(hoje, 12)},
    {'id': 3, 'dateIn': ms(hoje, 13), 'dateOut': None},
]

escritas = {n: mock.MagicMock(name=n) for n in ('registrar_ponto', 'registrar_ponto_atrasado',
                                                 'alterar_marcacao', 'excluir_marcacao')}
dubles = [
    mock.patch('tangerino.client._request', side_effect=_request_falso),
    mock.patch('tangerino.client.integracao_ativa', return_value=True),
    mock.patch('tangerino.client.listar_marcacoes', return_value=PARES),
    mock.patch('tangerino.views.listar_funcionarios', return_value=[{'id': EID, 'currentWorkSchedule': None}]),
    mock.patch('tangerino.ferias.listar_funcionarios',
               return_value=[{'id': EID, 'admissionDate': ms(hoje - timedelta(days=800), 0)}]),
    mock.patch('tangerino.ferias.listar_ferias', return_value=[]),
    *[mock.patch(f'tangerino.client.{n}', escritas[n]) for n in escritas],
    *[mock.patch(f'tangerino.ajustes.{n}', escritas[n]) for n in ('registrar_ponto_atrasado', 'alterar_marcacao',
                                                                   'excluir_marcacao')],
]
for d in dubles:
    d.start()

marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Loja Ponto App')

    def novo(apelido, hierarquia='PADRAO', **kw):
        return User.objects.create_user(
            username=f'zzponto.{apelido}', email=f'zzponto.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZ{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia, **kw)

    colab = novo('colab', tangerino_employee_id=EID)
    outro = novo('outro')
    analista = novo('analista')
    ResponsavelDP.objects.create(user=analista)

    # Ponto e Férias restritos a um grupo: colab e analista dentro, outro fora.
    grupo = CommunicationGroup.objects.create(name='ZZ Grupo Ponto App', created_by=analista)
    grupo.members.add(colab, analista)
    config = ConfiguracaoTangerino.get()
    config.ativo, config.ferias_ativo, config.restrito_ao_grupo, config.grupo = True, True, True, grupo
    config.ajustes_ativo, config.ajustes_setor = False, None
    config.save()

    def entrar(user):
        r = Client().post(f'{BASE}/auth/login/', {'email': user.email, 'senha': 'S3nha!teste', 'plataforma': 'android'},
                          content_type='application/json')
        return Client(HTTP_AUTHORIZATION=f'Bearer {r.json()["token"]}')

    cc, co, ca = entrar(colab), entrar(outro), entrar(analista)

    print('== ENTRADA NO MÓDULO ==')
    chaves = {m['chave'] for m in cc.get(f'{BASE}/modulos/').json()}
    t('aparece em /modulos/ para quem está logado (Solicitações ao DP é de todo logado)', 'ponto' in chaves, chaves)
    t('e também para quem está fora do grupo do ponto', 'ponto' in {m['chave'] for m in co.get(f'{BASE}/modulos/').json()})
    with mock.patch('api_mobile.modulos.ponto.pode_entrar', return_value=False):
        from api_mobile.modulos import carregar
        with mock.patch.dict(carregar('ponto'), {'pode_entrar': lambda u: False}):
            t('sem a regra: some de /modulos/', 'ponto' not in {m['chave'] for m in cc.get(f'{BASE}/modulos/').json()})
            t('sem a regra: 403 no módulo', cc.get(f'{M}/resumo/').status_code == 403)
    t('sem token: 401/403', Client().get(f'{M}/resumo/').status_code in (401, 403))

    r = cc.get(f'{M}/resumo/')
    s = r.json().get('secoes', {})
    t('resumo do colaborador: ponto e férias abertos, caixa do DP fechada',
      r.status_code == 200 and s.get('marcacoes') and s.get('ferias') and s.get('solicitacoes')
      and not s.get('caixa_dp'), r.content[:300])
    s = co.get(f'{M}/resumo/').json()['secoes']
    t('resumo de quem está fora do grupo: só solicitações e escala',
      not s['marcacoes'] and not s['ferias'] and s['solicitacoes'] and s['escala'] and not s['caixa_dp'], s)
    t('resumo de quem analisa: caixa do DP aberta', ca.get(f'{M}/resumo/').json()['secoes']['caixa_dp'] is True)

    print('\n== MEU PONTO (Tangerino, leitura) ==')
    r = cc.get(f'{M}/marcacoes/')
    j = r.json()
    t('marcações respondem', r.status_code == 200 and j['vinculado'] and j['erro'] is None, r.content[:300])
    st = j.get('status') or {}
    t('hoje: 3 batidas e "trabalhando"', len(st.get('eventos', [])) == 3 and st.get('situacao') == 'TRABALHANDO'
      and [e['hora'] for e in st['eventos']] == ['08:00', '12:00', '13:00'], st)
    t('pendência: ontem com entrada sem saída', [p['dia'] for p in j['pendencias']] == [ontem.isoformat()],
      j['pendencias'])
    t('período: hoje + 28 dias + a semana', len(j['dias']) == 29 + hoje.weekday() and j['dias'][0]['dia'] == hoje.isoformat())
    t('totais da semana e do período', j['semana'] and j['periodo'] and j['periodo']['feito'])
    t('fora do grupo: 403 nas marcações', co.get(f'{M}/marcacoes/').status_code == 403)
    with mock.patch.object(ConfiguracaoTangerino, 'libera_ponto', return_value=False):
        t('ponto desligado: 403 também para quem estava no grupo', cc.get(f'{M}/marcacoes/').status_code == 403)

    print('\n== AJUSTE NA SÓLIDES (só listar e cancelar o próprio pedido) ==')
    pedido = SolicitacaoAjustePonto.objects.create(
        usuario=colab, employee_id=EID, solicitante=colab, tipo=SolicitacaoAjustePonto.INCLUIR, dia=ontem,
        horario_novo=timezone.make_aware(datetime.combine(ontem, time(17))), motivo='ZZ esqueci a saída')
    t('desligado: a lista não aparece', cc.get(f'{M}/marcacoes/').json()['ajustes_solides']['ligado'] is False)
    config.ajustes_ativo, config.ajustes_setor = True, setor
    config.save()
    aj = cc.get(f'{M}/marcacoes/').json()['ajustes_solides']
    t('ligado: o pedido aparece', aj['ligado'] and [p['id'] for p in aj['pedidos']] == [pedido.pk], aj)
    t('pedido de outra pessoa: 404', ca.post(f'{M}/ajustes-solides/{pedido.pk}/cancelar/').status_code == 404)
    r = cc.post(f'{M}/ajustes-solides/{pedido.pk}/cancelar/')
    pedido.refresh_from_db()
    t('cancelar o próprio pedido', r.status_code == 200 and pedido.status == SolicitacaoAjustePonto.CANCELADA, r.content[:200])
    config.ajustes_ativo, config.ajustes_setor = False, None
    config.save()

    print('\n== FÉRIAS E ESCALA ==')
    r = cc.get(f'{M}/ferias/')
    t('férias: situação calculada', r.status_code == 200 and r.json()['disponivel'] and len(r.json()['periodos']) == 3,
      r.content[:300])
    t('férias fora do grupo: 403', co.get(f'{M}/ferias/').status_code == 403)
    semana = hoje - timedelta(days=hoje.weekday())
    esc = Escala.objects.create(colaborador=colab, semana_inicio=semana)
    EscalaDia.objects.create(escala=esc, data=semana, entrada=time(8), saida_almoco=time(12), volta_almoco=time(13),
                             saida=time(17))
    r = cc.get(f'{M}/escala/', {'inicio': semana.isoformat()})
    j = r.json()
    t('escala: a semana da própria pessoa', r.status_code == 200 and j['tem_escala'] and j['dias'][0]['entrada'] == '08:00'
      and j['dias'][0]['horas'] == 8, r.content[:300])
    t('escala de outra pessoa não vaza', co.get(f'{M}/escala/', {'inicio': semana.isoformat()}).json()['tem_escala'] is False)

    print('\n== SOLICITAÇÕES AO DP ==')
    r = cc.get(f'{M}/formulario/')
    t('formulário: tipos, espécies e horários', r.status_code == 200 and len(r.json()['tipos']) == 3
      and len(r.json()['rotulos_marcacao']) == 6)
    t('marcações do dia (espelho): responde', cc.get(f'{M}/marcacoes-do-dia/', {'data': ontem.isoformat()}).status_code == 200)

    ajuste = {'tipo': 'AJUSTE', 'data': ontem.isoformat(), 'motivo_ajuste': 'ESQUECEU', 'hora_0': '08:00',
              'hora_3': '17:00', 'justificativa': 'ZZ Esqueci de bater a saída ontem à tarde.'}
    r = cc.post(f'{M}/solicitacoes/', ajuste, content_type='application/json')
    j = r.json()
    t('nova: ajuste de ponto', r.status_code == 201 and j['status'] == 'ENVIADA' and j['dono'] and not j['dp']
      and [m['hora'] for m in j['marcacoes']] == ['08:00', '17:00'], r.content[:300])
    sol_ajuste = Solicitacao.objects.get(pk=j['id'])
    r = cc.post(f'{M}/solicitacoes/', ajuste, content_type='application/json')
    t('mesma regra do portal: ajuste repetido no dia é recusado', r.status_code == 400
      and 'em aberto para esse dia' in r.json()['erro'], r.content[:200])
    r = cc.post(f'{M}/solicitacoes/', {**ajuste, 'data': (hoje - timedelta(days=2)).isoformat(), 'hora_0': '18:00'},
                content_type='application/json')
    t('horários fora de ordem: 400 com a mensagem', r.status_code == 400 and any('ordem' in e for e in r.json()['erros']))
    atestado = {'tipo': 'ATESTADO', 'data': ontem.isoformat(), 'especie': 'MEDICO',
                'justificativa': 'ZZ Consulta médica com afastamento.'}
    r = cc.post(f'{M}/solicitacoes/', atestado, content_type='application/json')
    t('atestado sem documento: 400', r.status_code == 400 and any('Anexe' in e for e in r.json()['erros']))
    pdf = SimpleUploadedFile('atestado.pdf', b'%PDF-1.4 zz teste', content_type='application/pdf')
    r = cc.post(f'{M}/solicitacoes/', {**atestado, 'anexos': [pdf]})
    j = r.json()
    t('atestado com PDF (multipart)', r.status_code == 201 and len(j['anexos_lista']) == 1, r.content[:300])
    sol_atestado = Solicitacao.objects.get(pk=j['id'])
    exe = SimpleUploadedFile('virus.exe', b'MZ', content_type='application/octet-stream')
    r = cc.post(f'{M}/solicitacoes/', {**atestado, 'data': hoje.isoformat(), 'anexos': [exe]})
    t('arquivo que o portal recusa: 400', r.status_code == 400 and any('PDF ou foto' in e for e in r.json()['erros']))

    print('\n== RECORTE ==')
    r = cc.get(f'{M}/solicitacoes/')
    ids = {s['id'] for s in r.json()['resultados']}
    t('lista: as minhas', {sol_ajuste.pk, sol_atestado.pk} <= ids and r.json()['contagem']['abertas'] == 2, r.content[:300])
    t('lista de outra pessoa não traz as minhas', not ({sol_ajuste.pk, sol_atestado.pk} & {
        s['id'] for s in co.get(f'{M}/solicitacoes/').json()['resultados']}))
    t('filtro "abertas"', {s['id'] for s in cc.get(f'{M}/solicitacoes/', {'status': 'abertas'}).json()['resultados']} == ids)
    t('detalhe de outra pessoa: 404', co.get(f'{M}/solicitacoes/{sol_ajuste.pk}/').status_code == 404)
    t('ação em solicitação de outra pessoa: 404',
      co.post(f'{M}/solicitacoes/{sol_ajuste.pk}/acao/', {'acao': 'comentar', 'texto': 'oi'},
              content_type='application/json').status_code == 404)
    t('corrigir a de outra pessoa: 404',
      co.post(f'{M}/solicitacoes/{sol_ajuste.pk}/corrigir/', ajuste, content_type='application/json').status_code == 404)
    r = ca.get(f'{M}/solicitacoes/{sol_ajuste.pk}/')
    t('quem analisa abre o detalhe com as ações do DP', r.status_code == 200 and r.json()['dp']
      and r.json()['pode_analisar'] and r.json()['pode_assumir'] and not r.json()['pode_cancelar'])

    print('\n== ANEXO (link assinado) ==')
    url = cc.get(f'{M}/solicitacoes/{sol_atestado.pk}/').json()['anexos_lista'][0]['url']
    caminho = url.split('testserver', 1)[1]
    r = Client().get(caminho)
    t('o dono abre o anexo pelo link', r.status_code == 200 and b''.join(r.streaming_content).startswith(b'%PDF'))
    from django.core import signing
    alheio = signing.dumps({'u': outro.pk, 't': 'ponto_anexo', 'i': str(sol_atestado.anexos.first().pk)},
                           salt='api_mobile.arquivo', compress=True)
    t('link no nome de outra pessoa: 404', Client().get(f'{BASE}/arquivo/{alheio}/').status_code == 404)
    url_dp = ca.get(f'{M}/solicitacoes/{sol_atestado.pk}/').json()['anexos_lista'][0]['url']
    t('quem analisa também abre', Client().get(url_dp.split('testserver', 1)[1]).status_code == 200)

    print('\n== FLUXO DO DP ==')
    t('caixa do DP: 403 para o colaborador', cc.get(f'{M}/caixa/').status_code == 403)
    r = ca.get(f'{M}/caixa/')
    t('caixa do DP: pendentes trazem as duas', r.status_code == 200
      and {sol_ajuste.pk, sol_atestado.pk} <= {s['id'] for s in r.json()['resultados']} and r.json()['kpis']['aguardando'] >= 2,
      r.content[:300])
    r = cc.post(f'{M}/solicitacoes/{sol_ajuste.pk}/acao/', {'acao': 'assumir'}, content_type='application/json')
    t('o colaborador não assume a análise', r.status_code == 400 and 'Só o DP' in r.json()['erro'])
    r = ca.post(f'{M}/solicitacoes/{sol_ajuste.pk}/acao/', {'acao': 'assumir'}, content_type='application/json')
    t('DP assume', r.status_code == 200 and r.json()['status'] == 'EM_ANALISE')
    r = ca.post(f'{M}/solicitacoes/{sol_ajuste.pk}/acao/', {'acao': 'devolver', 'texto': 'x'}, content_type='application/json')
    t('devolver sem dizer o que corrigir: 400', r.status_code == 400)
    r = ca.post(f'{M}/solicitacoes/{sol_ajuste.pk}/acao/', {'acao': 'devolver', 'texto': 'ZZ A saída foi às 18h?'},
                content_type='application/json')
    t('DP devolve', r.status_code == 200 and r.json()['status'] == 'DEVOLVIDA')
    r = cc.get(f'{M}/solicitacoes/{sol_ajuste.pk}/')
    t('o colaborador vê a devolução e pode corrigir', r.json()['resposta'] == 'ZZ A saída foi às 18h?' and r.json()['pode_corrigir'])
    r = cc.post(f'{M}/solicitacoes/{sol_ajuste.pk}/corrigir/', {**ajuste, 'hora_3': '18:00', 'comentario': 'ZZ foi às 18h'},
                content_type='application/json')
    ev = Evento.objects.filter(solicitacao=sol_ajuste, acao=Evento.REENVIADA).first()
    t('corrigir e reenviar (histórico com o que mudou)', r.status_code == 200 and r.json()['status'] == 'ENVIADA'
      and ev is not None and any(a['campo'] == 'Horários' for a in ev.alteracoes), r.content[:300])
    r = cc.post(f'{M}/solicitacoes/{sol_ajuste.pk}/acao/', {'acao': 'comentar', 'texto': 'ZZ obrigado'},
                content_type='application/json')
    t('colaborador comenta', r.status_code == 200 and r.json()['eventos'][-1]['acao'] == 'COMENTARIO')
    r = ca.post(f'{M}/solicitacoes/{sol_ajuste.pk}/acao/', {'acao': 'aprovar', 'texto': 'ZZ ok', 'ajustado': True},
                content_type='application/json')
    sol_ajuste.refresh_from_db()
    t('DP aprova já lançado no ponto', r.status_code == 200 and sol_ajuste.status == 'APROVADA' and sol_ajuste.ajustado_no_ponto)
    docs_antes = UserDocument.objects.filter(user=colab).count()
    r = ca.post(f'{M}/solicitacoes/{sol_atestado.pk}/acao/', {'acao': 'aprovar', 'arquivar': True},
                content_type='application/json')
    t('DP aprova o atestado e guarda nos documentos', r.status_code == 200 and r.json()['status'] == 'APROVADA'
      and UserDocument.objects.filter(user=colab).count() == docs_antes + 1, r.content[:200])
    r = cc.post(f'{M}/solicitacoes/{sol_atestado.pk}/acao/', {'acao': 'cancelar'}, content_type='application/json')
    t('não cancela o que já foi aprovado', r.status_code == 400)
    decl = {'tipo': 'DECLARACAO', 'data': hoje.isoformat(), 'especie': 'ESCOLAR',
            'justificativa': 'ZZ Prova da faculdade no período da manhã.',
            'anexos': [SimpleUploadedFile('decl.jpg', b'\xff\xd8\xff zz', content_type='image/jpeg')]}
    r = cc.post(f'{M}/solicitacoes/', decl)
    did = r.json().get('id')
    r = cc.post(f'{M}/solicitacoes/{did}/acao/', {'acao': 'cancelar'}, content_type='application/json')
    t('declaração enviada e cancelada pelo dono', r.status_code == 200 and r.json()['status'] == 'CANCELADA', r.content[:200])
    t('ação desconhecida: 400', cc.post(f'{M}/solicitacoes/{did}/acao/', {'acao': 'xyz'},
                                         content_type='application/json').status_code == 400)

    print('\n== NADA FOI PARA A SÓLIDES ==')
    t('nenhuma escrita no Tangerino (bater ponto, incluir, alterar, excluir)',
      not any(m.called for m in escritas.values()), {n: m.call_count for n, m in escritas.items()})
    t('nenhuma chamada além de leitura (GET)', all(c[0] == 'GET' for c in chamadas_api), chamadas_api)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    for d in dubles:
        d.stop()
    print('\nrollback: nada deste teste foi gravado no banco.')
    print(f'arquivos em memória (descartados): {len(memoria.listdir("")[0]) + len(memoria.listdir("")[1])} entradas')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
