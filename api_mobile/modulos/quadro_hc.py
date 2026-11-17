"""Módulo Quadro de HC no app — o quadro de /quadro-hc/ em árvore: rede → carteira → loja → pessoas.

Regras (todas do portal, nenhuma nova):

- Entrada: ``quadro_hc.servicos.pode_acessar`` (quadro_hc/servicos.py:44) — a mesma
  da view web ``quadro`` (quadro_hc/views.py:47) e do menu (quadro_hc/context_processors.py:5,
  templates/base.html:790): SUPERADMIN (``is_superuser`` ou hierarquia SUPERADMIN) ou
  quem tem ``AcessoQuadroHC`` (papel Ver ou Gerenciar). Não há liberação individual
  em users/module_access.py para o quadro.
- Recorte: a web mostra o quadro inteiro para quem entra (quadro_hc/views.py:51) — aqui também.
- Ações: ajuste manual (``servicos.salvar_ajuste``) e HC previsto da loja
  (``servicos.salvar_previsto``) só para ``servicos.pode_gerenciar`` (quadro_hc/views.py:33,
  o mesmo ``_api`` das rotas /quadro-hc/ajuste/ e /quadro-hc/previsto/). Papel Ver → 403.
- Números: tudo sai de ``servicos.montar_quadro`` (o mesmo dict que a tela renderiza);
  aqui só se escolhe o que vai para o JSON e se somam os totais da rede.
- Filtros: os mesmos da barra da tela (templates/quadro_hc/quadro.html:261-292 e o
  ``filtrar()`` em :800-826): busca em nome/login/cargo/cidade, carteira, loja, cargo,
  rápidos (com alerta, vagas, novos, experiência em 7 dias, demissão no cadastro,
  ajustados) e "sem gerente" (KPI). A mais: ``hc=falta|sobra`` (lojas abaixo/acima do previsto).

De fora (o app abre o portal): movimentações (simular/aplicar/agendar/cancelar), vagas,
observação, carteiras, meta proporcional do consultor (model novo ainda sem migrate),
histórico geral e a tela de acessos.
"""
import unicodedata

from django.http import Http404
from django.urls import path
from django.utils import timezone
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from quadro_hc import servicos
from quadro_hc.models import FUNCOES, AjusteHC, MovimentacaoHC
from users.models import Sector, User

from .. import dados
from ..autenticacao import TokenMobileAuthentication
from . import permissao


def pode_entrar(user):
    # quadro_hc/servicos.py:44 — a mesma regra de quadro_hc/views.py:47 e do menu (context_processors.py:5).
    return bool(servicos.pode_acessar(user))


MODULO = {'rotulo': 'Quadro de HC', 'descricao': 'HC loja a loja: previsto, real e quem está em cada loja',
          'icone': 'people-circle-outline', 'cor': '#6366F1', 'pode_entrar': pode_entrar}

RAPIDOS = ('alerta', 'vaga', 'novo', 'experiencia', 'demissao', 'ajustado')


def _protecao(*metodos):
    def aplicar(funcao):
        funcao = permission_classes([permissao('quadro_hc')])(funcao)
        funcao = authentication_classes([TokenMobileAuthentication])(funcao)
        return api_view(list(metodos or ['GET']))(funcao)
    return aplicar


def _erro(msg, codigo=400):
    return Response({'erro': msg}, status=codigo)


def _norm(texto):
    # O norm() do JS da tela (quadro.html:746): sem acento e em minúsculas.
    texto = unicodedata.normalize('NFD', str(texto or ''))
    return ''.join(c for c in texto if not unicodedata.combining(c)).lower()


def _papel(user):
    if servicos.e_superadmin(user):
        return 'SUPERADMIN'
    return servicos._papel_liberado(user) or ''


# ---------------------------------------------------------------------------
# Filtros (espelho do filtrar() da tela: quadro.html:800-826)
# ---------------------------------------------------------------------------
class Filtro:
    def __init__(self, params):
        self.termo = _norm((params.get('busca') or '').strip())
        self.carteira = str(params.get('carteira') or '').strip()
        self.loja = str(params.get('loja') or '').strip()
        self.cargo = (params.get('cargo') or '').strip()
        self.rapido = params.get('rapido') if params.get('rapido') in RAPIDOS else ''
        self.sem_gerente = str(params.get('sem_gerente') or '').lower() in ('1', 'true', 'sim')
        self.hc = params.get('hc') if params.get('hc') in ('falta', 'sobra') else ''

    @property
    def filtrando_itens(self):
        # quadro.html:817 — só busca, cargo e rápido escondem loja sem resultado.
        return bool(self.termo or self.cargo or self.rapido)

    @property
    def ativo(self):
        return bool(self.filtrando_itens or self.carteira or self.loja or self.sem_gerente or self.hc)

    def loja_ok(self, carteira_id, b):
        return ((not self.carteira or str(carteira_id) == self.carteira)
                and (not self.loja or str(b['setor'].id) == self.loja)
                and (not self.sem_gerente or b['sem_gerente'])
                and (not self.hc or _situacao_hc(b) == self.hc))

    def item_ok(self, item):
        """item = (tipo, dict) com tipo 'pessoa', 'vaga' ou 'prevista' (as linhas data-item da tela)."""
        tipo, d = item
        if tipo == 'pessoa':
            busca, cargo = f"{d['nome']} {d['login']} {d['cargo']} {d['cidade']}", d['cargo']
        elif tipo == 'vaga':
            busca, cargo = f'vaga em aberto {d.cargo}', d.cargo
        else:
            busca, cargo = f"vaga em aberto {d['grupo']} hc previsto", ''
        if self.termo and self.termo not in _norm(busca):
            return False
        if self.cargo and cargo != self.cargo:
            return False
        r = self.rapido
        if not r:
            return True
        if tipo != 'pessoa':
            return r in ('alerta', 'vaga')  # vaga tem data-alerta e data-vaga (quadro.html:384, :393)
        return ((r == 'alerta' and _tem_alerta(d)) or (r == 'novo' and d['novo'])
                or (r == 'experiencia' and _experiencia(d)) or (r == 'demissao' and bool(d['demissao_antiga']))
                or (r == 'ajustado' and _ajustado(d)))


def _experiencia(p):
    return 'proxima' in (p['exp1_alerta'], p['exp2_alerta'])


def _tem_alerta(p):
    # quadro.html:348 — experiência em 7 dias, movimentação agendada ou saída marcada.
    return _experiencia(p) or bool(p['agendadas']) or bool(p['sai_em'])


def _ajustado(p):
    # quadro.html:344 — ajuste manual ou gerente pelo grupo GERENTES.
    return bool(p['ajuste']) or p['origem_funcao'] != 'cargo'


def _itens(b):
    return ([('pessoa', p) for p in b['pessoas']] + [('vaga', v) for v in b['vagas']]
            + [('prevista', v) for v in b['vagas_previstas']])


# ---------------------------------------------------------------------------
# Previsto × real
# ---------------------------------------------------------------------------
def _falta_sobra(b):
    """falta = o que a tela chama de falta (quadro_hc/servicos.py:331); sobra = acima do previsto nas mesmas funções."""
    sobra = sum(max(-c['falta'], 0) for c in b['comparacao'])
    return b['falta'], sobra


def _situacao_hc(b):
    if not b['total_previsto']:
        return 'sem_previsto'
    falta, sobra = _falta_sobra(b)
    return 'falta' if falta else 'sobra' if sobra else 'ok'


def _loja_resumo(b, carteira, filtro=None):
    falta, sobra = _falta_sobra(b)
    setor = b['setor']
    encontrados = sum(1 for it in _itens(b) if filtro.item_ok(it)) if filtro else None
    return {
        'id': setor.id, 'nome': setor.name, 'adabas': setor.adabas or '', 'e_loja': servicos.e_loja(setor),
        'carteira_id': carteira['id'], 'carteira': carteira['nome'],
        'total': b['total'], 'total_previsto': b['total_previsto'],
        'diferenca': (b['total'] - b['total_previsto']) if b['total_previsto'] else None,
        'falta': falta, 'sobra': sobra, 'situacao_hc': _situacao_hc(b),
        'sem_gerente': b['sem_gerente'], 'vagas': b['total_vagas'],
        'resumo': [{'grupo': g, 'n': n} for g, n in b['resumo']],
        'comparacao': [{'grupo': c['grupo'], 'real': c['real'], 'previsto': c['previsto'],
                        'diferenca': c['real'] - c['previsto']} for c in b['comparacao']],
        'marcas': {
            'demissao': sum(1 for p in b['pessoas'] if p['demissao_antiga']),
            'ajustados': sum(1 for p in b['pessoas'] if _ajustado(p)),
            'experiencia': sum(1 for p in b['pessoas'] if _experiencia(p)),
            'novos': sum(1 for p in b['pessoas'] if p['novo']),
            'alerta': sum(1 for p in b['pessoas'] if _tem_alerta(p)),
        },
        'encontrados': encontrados,
    }


def _pessoa(p, extra=None, completo=False):
    item = {
        'id': p['id'], 'nome': p['nome'], 'iniciais': p['iniciais'], 'cargo': p['cargo'], 'grupo': p['grupo'],
        'origem_funcao': p['origem_funcao'], 'cargo_grupo': p['cargo_grupo'],
        'status': p['status'],  # '' = ativo; senão o rótulo (Férias, Afastado…)
        'login': p['login'], 'cidade': p['cidade'], 'telefone': p['telefone'] or '',
        'contratacao': dados.iso(p['contratacao']), 'nascimento': dados.iso(p['nascimento']),
        'exp1': dados.iso(p['exp1']), 'exp2': dados.iso(p['exp2']),
        'exp1_alerta': p['exp1_alerta'], 'exp2_alerta': p['exp2_alerta'],
        'novo': p['novo'], 'sai_em': dados.iso(p['sai_em']), 'demissao_antiga': dados.iso(p['demissao_antiga']),
        'observacao': p['observacao'],
        'agendadas': [{'id': a['id'], 'data': dados.iso(a['data']), 'resumo': a['resumo']} for a in p['agendadas']],
        'ajuste': p['ajuste'], 'ajustado': _ajustado(p), 'alerta': _tem_alerta(p), 'experiencia': _experiencia(p),
        'e_consultor': bool(p.get('e_consultor')), 'meta_pct': p.get('meta_pct'),
        'setor_id': p['setor_id'], 'setor_cadastro': p['setor_cadastro'],
    }
    item.update(extra.get(p['id'], {}) if extra else {})
    if completo:
        item['historico'] = p['historico']
    return item


def _situacao_cadastro(ids):
    """Férias/afastamento do cadastro (a tela só mostra o rótulo do status; o app mostra as datas)."""
    saida = {}
    for u in User.objects.filter(id__in=list(ids)).values('id', 'status', 'vacation_start_date', 'vacation_end_date',
                                                           'leave_return_date'):
        ferias = u['status'] == User.STATUS_FERIAS
        saida[u['id']] = {
            'situacao': u['status'],
            'ferias_inicio': dados.iso(u['vacation_start_date']) if ferias else None,
            'ferias_fim': dados.iso(u['vacation_end_date']) if ferias else None,
            'retorno': dados.iso(u['leave_return_date']) if u['status'] == User.STATUS_AFASTADO else None,
        }
    return saida


def _blocos(q):
    for c in q['carteiras']:
        for b in c['lojas']:
            yield c, b


def _numeros(blocos):
    com_previsto = [b for b in blocos if b['total_previsto']]
    return {
        'real': sum(b['total'] for b in com_previsto), 'previsto': sum(b['total_previsto'] for b in com_previsto),
        'falta': sum(_falta_sobra(b)[0] for b in com_previsto), 'sobra': sum(_falta_sobra(b)[1] for b in com_previsto),
        'lojas_com_previsto': len(com_previsto),
        'lojas_com_falta': sum(1 for b in com_previsto if _situacao_hc(b) == 'falta'),
        'lojas_com_sobra': sum(1 for b in com_previsto if _situacao_hc(b) == 'sobra'),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@_protecao()
def quadro(request):
    """GET ?busca=&carteira=&loja=&cargo=&rapido=&sem_gerente=1&hc=falta|sobra — a árvore da rede."""
    hoje = timezone.localdate()
    q = servicos.montar_quadro(hoje)
    filtro = Filtro(request.query_params)
    agendadas = list(MovimentacaoHC.objects.filter(situacao=MovimentacaoHC.AGENDADA).values_list('data', flat=True))

    carteiras, resultados = [], 0
    for c in q['carteiras']:
        lojas = []
        for b in c['lojas']:
            if not filtro.loja_ok(c['id'], b):
                continue
            resumo = _loja_resumo(b, c, filtro if filtro.ativo else None)
            if filtro.filtrando_itens and not resumo['encontrados']:
                continue
            resultados += resumo['encontrados'] or 0
            lojas.append(resumo)
        if not lojas:
            continue
        carteiras.append({'id': c['id'], 'nome': c['nome'], 'coordenacao': c['coordenacao'],
                          'total': c['total'], 'vagas': c['vagas'],
                          'hc': _numeros([b for b in c['lojas']]), 'lojas': lojas})

    todas = [b for _c, b in _blocos(q)]
    return Response({
        'hoje': dados.iso(hoje), 'papel': _papel(request.user), 'pode_gerenciar': servicos.pode_gerenciar(request.user),
        'kpis': {
            'total': q['total'], 'lojas': q['lojas'], 'vagas': q['vagas'], 'vagas_previstas': q['vagas_previstas'],
            'sem_gerente': q['sem_gerente'], 'exp_proximas': q['exp_proximas'], 'demissao_antiga': q['demissao_antiga'],
            'ajustes': q['ajustes'], 'agendadas': len(agendadas), 'para_hoje': sum(1 for d in agendadas if d <= hoje),
        },
        'hc': _numeros(todas),
        'filtrando': filtro.ativo, 'resultados': resultados if filtro.ativo else None,
        # As pílulas de carteira da tela (quadro.html:280-283): todas, mesmo com filtro.
        'todas_carteiras': [{'id': c['id'], 'nome': c['nome'], 'total': c['total']} for c in q['carteiras']],
        'carteiras': carteiras,
        'sem_loja': [{'id': p['id'], 'nome': p['nome'], 'cargo': p['cargo']} for p in q['sem_loja']],
        'fora': [{'id': p['id'], 'nome': p['nome'], 'cargo': p['cargo'], 'loja': p['loja'], 'motivo': p['motivo']}
                 for p in q['fora']],
    })


@_protecao()
def opcoes(request):
    """Opções dos filtros e do ajuste (os mesmos selects da tela: quadro.html:266-273, :689-692)."""
    return Response({
        'cargos': servicos.cargos_disponiveis(),
        'funcoes': [f for f, _r in FUNCOES],
        'situacoes_ajuste': [{'valor': v, 'rotulo': r} for v, r in AjusteHC.SITUACOES],
        'setores': [{'id': s.id, 'nome': s.name} for s in Sector.objects.order_by('name')],
    })


def _achar_loja(q, pk):
    for c, b in _blocos(q):
        if b['setor'].id == pk:
            return c, b
    raise Http404


def _loja_detalhe(request, pk):
    hoje = timezone.localdate()
    q = servicos.montar_quadro(hoje)
    c, b = _achar_loja(q, pk)
    filtro = Filtro(request.query_params)
    filtro.carteira = filtro.loja = ''          # já estamos na loja
    itens = [it for it in _itens(b) if filtro.item_ok(it)]
    extra = _situacao_cadastro(p['id'] for p in b['pessoas'])
    prev = b['previsto']
    return {
        'loja': _loja_resumo(b, c),
        'coordenacao': c['coordenacao'],
        'pode_gerenciar': servicos.pode_gerenciar(request.user),
        'filtrando': filtro.filtrando_itens,
        'pessoas': [_pessoa(d, extra) for t, d in itens if t == 'pessoa'],
        'vagas': [{'id': v.id, 'cargo': v.cargo, 'observacao': v.observacao, 'aberta_em': dados.iso(v.criado_em),
                   'aberta_por': servicos.nome_completo(v.criado_por) if v.criado_por else ''}
                  for t, v in itens if t == 'vaga'],
        'vagas_previstas': [{'grupo': v['grupo'], 'real': v['real'], 'previsto': v['previsto']}
                            for t, v in itens if t == 'prevista'],
        # Todas as funções, para o formulário do HC previsto (quadro.html:719-724).
        'previsto': [{'funcao': f, 'quantidade': prev.get(f, 0)} for f, _r in FUNCOES],
    }


@_protecao()
def loja(request, pk):
    """GET — a loja aberta: comparação previsto × real, pessoas, vagas (aceita os mesmos filtros de item)."""
    return Response(_loja_detalhe(request, pk))


@_protecao()
def pessoa(request, pk):
    """GET — uma pessoa do quadro (ou fora dele por ajuste manual). Quem não está no quadro → 404."""
    q = servicos.montar_quadro(timezone.localdate())
    for c, b in _blocos(q):
        for p in b['pessoas']:
            if p['id'] == pk:
                return Response({**_pessoa(p, _situacao_cadastro([pk]), completo=True), 'fora': False,
                                 'loja': {'id': b['setor'].id, 'nome': b['setor'].name}, 'carteira': c['nome'],
                                 'pode_gerenciar': servicos.pode_gerenciar(request.user)})
    for p in q['sem_loja']:
        if p['id'] == pk:
            return Response({**_pessoa(p, _situacao_cadastro([pk]), completo=True), 'fora': False, 'loja': None,
                             'carteira': '', 'pode_gerenciar': servicos.pode_gerenciar(request.user)})
    for p in q['fora']:
        if p['id'] == pk:
            a = AjusteHC.objects.select_related('setor').filter(colaborador_id=pk).first()
            return Response({
                'id': pk, 'nome': p['nome'], 'cargo': p['cargo'], 'fora': True, 'loja_cadastro': p['loja'],
                'ajuste': ({'funcao': a.funcao, 'setor': a.setor_id, 'setor_nome': a.setor.name if a.setor else '',
                            'situacao': a.situacao, 'motivo': a.motivo} if a else None),
                'pode_gerenciar': servicos.pode_gerenciar(request.user)})
    raise Http404


def _so_quem_gerencia(request):
    if not servicos.pode_gerenciar(request.user):
        # quadro_hc/views.py:33-34 — a mesma recusa (e a mesma frase) da web.
        return _erro('Seu acesso ao Quadro de HC é só para ver.', 403)
    return None


@_protecao('POST')
def ajuste(request):
    """POST {colaborador, funcao, setor, situacao, motivo} — servicos.salvar_ajuste (quadro_hc/views.py:174)."""
    negado = _so_quem_gerencia(request)
    if negado:
        return negado
    corpo = request.data if isinstance(request.data, dict) else {}
    try:
        feito = servicos.salvar_ajuste(dict(corpo), request.user)
    except servicos.ErroQuadro as e:
        return _erro(str(e))
    msg = 'Ajuste manual salvo.' if feito else 'Ajuste manual retirado: a pessoa volta a seguir o cadastro.'
    return Response({'ok': True, 'msg': msg})


@_protecao('POST')
def previsto(request, pk):
    """POST {quantidades: {função: n}} — servicos.salvar_previsto (quadro_hc/views.py:182). Devolve a loja."""
    negado = _so_quem_gerencia(request)
    if negado:
        return negado
    corpo = request.data if isinstance(request.data, dict) else {}
    quantidades = corpo.get('quantidades') or {}
    if not isinstance(quantidades, dict):
        return _erro('Quantidades inválidas.')
    try:
        total = servicos.salvar_previsto(pk, quantidades, request.user)
    except servicos.ErroQuadro as e:
        return _erro(str(e))
    msg = f'HC previsto salvo: {total} pessoa(s).' if total else 'HC previsto zerado.'
    try:
        detalhe = _loja_detalhe(request, pk)
    except Http404:      # setor que não aparece no quadro (sem gente nem previsto): gravou, mas não há loja para mostrar
        detalhe = None
    return Response({'ok': True, 'msg': msg, 'loja': detalhe})


urlpatterns = [
    path('', quadro, name='quadro_hc_quadro'),
    path('opcoes/', opcoes, name='quadro_hc_opcoes'),
    path('loja/<int:pk>/', loja, name='quadro_hc_loja'),
    path('loja/<int:pk>/previsto/', previsto, name='quadro_hc_previsto'),
    path('pessoa/<int:pk>/', pessoa, name='quadro_hc_pessoa'),
    path('ajuste/', ajuste, name='quadro_hc_ajuste'),
]
