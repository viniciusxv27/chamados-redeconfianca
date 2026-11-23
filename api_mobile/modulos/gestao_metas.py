"""Módulo Gerenciamento de metas no app — o /gestao-metas/ no celular.

As regras são as do portal (app ``gestao_metas``), chamadas daqui:

- Entrada: ``gestao_metas.permissoes.pode_ver`` — o mesmo gate do item do menu
  (``gestao_metas/context_processors.py``) e do ``@exige()`` das views: SUPERADMIN ou quem está em
  ``AcessoGestaoMetas`` (pessoa ou grupo). Editor × Visualizador pelo ``papel``.
- Tabela da competência: ``servicos.montar`` (um pilar por vez), ``divisoes_da_competencia`` e o
  realizado "até o momento" de ``servicos.realizado_ate_agora`` (banco de vendas, 10 min de cache).
- Mudar: a própria view do portal é chamada — ``salvar`` (meta, CNs, decisão; a Fixa recalcula a nova
  meta), ``dividir`` (botão "Salvar" da loja: grava e divide entre os consultores do Quadro de HC),
  ``preencher`` (preenchimento rápido) e ``fechar`` (fechar / reabrir, este só SUPERADMIN). O
  ``@exige(editar=True)`` delas barra quem só visualiza; competência fechada não muda.
- Histórico e a planilha (.xlsx de ``exportar.gerar``, por link assinado).

Fica no portal: importações (base, banco, meta Vivo, planilha — subir Excel e conferir a prévia),
abrir competência nova, a lista de lojas e os acessos (SUPERADMIN).
"""
import io
import json

from django.contrib import messages
from django.http import Http404, HttpRequest, QueryDict
from django.urls import path
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, parser_classes, permission_classes
from rest_framework.parsers import FormParser, JSONParser
from rest_framework.response import Response

from ..arquivos import link, registrar
from ..autenticacao import TokenMobileAuthentication
from . import permissao


def pode_entrar(user):
    from gestao_metas.permissoes import pode_ver
    return pode_ver(user)


MODULO = {'rotulo': 'Gestão de metas', 'descricao': 'Meta do mês de cada loja, pilar a pilar',
          'icone': 'flag-outline', 'cor': '#F97316', 'pode_entrar': pode_entrar}

PAPEIS = {'SUPERADMIN': 'Superadmin', 'EDITOR': 'Editor', 'VISUALIZADOR': 'Visualizador'}


def _api(metodos=('GET',)):
    def decorar(funcao):
        funcao = parser_classes([JSONParser, FormParser])(funcao)
        funcao = permission_classes([permissao('gestao_metas')])(funcao)
        funcao = authentication_classes([TokenMobileAuthentication])(funcao)
        return api_view(list(metodos))(funcao)
    return decorar


def _erro(mensagem, codigo=status.HTTP_400_BAD_REQUEST, **extra):
    return Response({'erro': mensagem, **extra}, status=codigo)


def _web():
    from gestao_metas import views
    return views


def _competencia(codigo):
    from gestao_metas.models import Competencia
    from gestao_metas.servicos import mes_do_codigo
    try:
        mes = mes_do_codigo(codigo)
    except (ValueError, TypeError):
        raise Http404
    comp = Competencia.objects.filter(mes=mes).first()
    if comp is None:
        raise Http404
    return comp


# ── Ponte com as views do portal ─────────────────────────────────────────────

def _chamar_view_json(request, view, *args, corpo=None):
    """Roda uma view JSON do portal (as que a tela da competência chama por fetch) com a pessoa do
    token e devolve (status, conteúdo). Requisição nova: o DRF já pode ter lido o corpo."""
    dj = request._request
    nova = HttpRequest()
    nova.method = 'POST'
    nova.path = dj.path
    nova.path_info = dj.path_info
    nova.META = {**dj.META, 'REQUEST_METHOD': 'POST', 'CONTENT_TYPE': 'application/json',
                 'HTTP_ACCEPT': 'application/json'}
    nova.content_type = 'application/json'
    nova._body = json.dumps(corpo or {}).encode('utf-8')
    nova.user = request.user
    if hasattr(dj, 'session'):
        nova.session = dj.session
    resposta = view(nova, *args)
    try:
        conteudo = json.loads(resposta.content or b'{}')
    except ValueError:
        conteudo = {}
    return resposta.status_code, conteudo if isinstance(conteudo, dict) else {}


def _chamar_view_form(request, view, *args, post=None):
    """Roda uma view de formulário do portal (POST → mensagem → redirect) e devolve
    (resposta, [(nível, texto)]). As mensagens são lidas aqui e não ficam na sessão. (O ``@exige``
    responde 403 em JSON, sem mensagem, quando o pedido veio em JSON — por isso a resposta volta.)"""
    dj = request._request
    dj.user = request.user
    dj.method = 'POST'
    corpo = QueryDict(mutable=True)
    for chave, valor in (post or {}).items():
        corpo[chave] = '' if valor is None else str(valor)
    dj.POST = corpo
    resposta = view(dj, *args)
    return resposta, [(m.level_tag, str(m)) for m in messages.get_messages(dj)]


# ── Formato ──────────────────────────────────────────────────────────────────

def _n(valor, casas=2):
    if valor is None:
        return None
    try:
        return round(float(valor), casas)
    except (TypeError, ValueError):
        return None


def _lista(valores, casas=2):
    return [_n(v, casas) for v in (valores or [])]


COMPARACOES = ('mxm', 'yxy', 'mxquad', 'mxmeta')


def _linha(linha, fixa, divisao=None):
    """Uma loja (ou subtotal) de um pilar, com os nomes das colunas da tela do portal."""
    saida = {
        'meses': _lista(linha.get('meses')),
        'media_quad': _n(linha.get('media_quad')),
        'real_ano_anterior': _n(linha.get('real_ano_anterior')),
        'meta_vivo': _n(linha.get('meta_vivo'), 4 if fixa else 2),
        'meta_anterior': _n(linha.get('meta_anterior')),
        'meta': _n(linha.get('meta')),
        'alvo': _n(linha.get('alvo')),
        **{c: _n(linha.get(c), 4) for c in COMPARACOES},
    }
    if fixa:
        saida.update({
            'meses_valor': _lista(linha.get('meses_valor')),
            'media_quad_valor': _n(linha.get('media_quad_valor')),
            'ticket': _n(linha.get('ticket')),
            'ticket_da_rede': bool(linha.get('ticket_da_rede')),
            'qtd_cns': linha.get('qtd_cns'),
            'qtd': linha.get('qtd'),
            'cima': linha.get('cima'),
            'baixo': linha.get('baixo'),
            'decisao': linha.get('decisao'),
            'nova_meta': linha.get('nova_meta'),
        })
    if 'loja' in linha:
        loja = linha['loja']
        saida.update({'loja_id': loja.id, 'loja': loja.nome, 'regiao': linha.get('regiao') or None,
                      'divisao': [{'nome': d.colaborador_nome, 'percentual': d.percentual, 'meta': _n(d.meta)}
                                  for d in (divisao or [])]})
    else:
        saida.update({'titulo': linha.get('titulo'), 'qtd_lojas': linha.get('qtd_lojas')})
    return saida


def _competencia_resumo(comp, n_metas=None):
    from gestao_metas.servicos import rotulo_mes
    return {'codigo': comp.codigo, 'rotulo': rotulo_mes(comp.mes, True), 'rotulo_curto': rotulo_mes(comp.mes),
            'fechada': comp.fechada, 'status_nome': comp.get_status_display(),
            'metas': n_metas, 'observacao': comp.observacao or None}


# ── Endpoints ────────────────────────────────────────────────────────────────

@_api()
def inicio(request):
    """As competências (gestao_metas/views.py ``inicio``), da mais nova para a mais antiga."""
    from django.db.models import Count
    from gestao_metas.models import Competencia, LojaMeta
    from gestao_metas.permissoes import papel, pode_editar

    p = papel(request.user)
    comps = list(Competencia.objects.annotate(n_metas=Count('metas')).order_by('-mes'))
    return Response({
        'papel': p, 'papel_rotulo': PAPEIS.get(p, ''), 'pode_editar': pode_editar(request.user),
        'lojas_ativas': LojaMeta.objects.filter(ativa=True).count(),
        'competencias': [_competencia_resumo(c, c.n_metas) for c in comps],
        'url_web': '/gestao-metas/',
    })


@_api()
def competencia(request, codigo):
    """A tela da competência (gestao_metas/views.py ``competencia``), um pilar por vez."""
    from gestao_metas import servicos
    from gestao_metas.models import PILARES, Competencia, Realizado
    from gestao_metas.permissoes import e_superadmin, pode_editar

    comp = _competencia(codigo)
    pilares = dict(PILARES)
    pilar = request.query_params.get('pilar') or PILARES[0][0]
    if pilar not in pilares:
        return _erro('Pilar desconhecido.')
    dados, ctx = servicos.montar(comp, [pilar])
    p = dados[pilar]
    fixa = p['fixa']
    divisoes = servicos.divisoes_da_competencia(comp)
    anterior = Competencia.objects.filter(mes__lt=comp.mes).order_by('-mes').first()
    seguinte = Competencia.objects.filter(mes__gt=comp.mes).order_by('mes').first()
    com_dado = set(Realizado.objects.filter(mes__in=ctx['meses_quad'] + [ctx['mes_ano']])
                   .values_list('mes', flat=True).distinct())
    faltando = [servicos.rotulo_mes(m) for m in ctx['meses_quad'] + [ctx['mes_ano']] if m not in com_dado]
    comp_ant = ctx['competencia_anterior']
    editar = pode_editar(request.user) and not comp.fechada
    return Response({
        **_competencia_resumo(comp),
        'anterior': anterior.codigo if anterior else None,
        'seguinte': seguinte.codigo if seguinte else None,
        'pilar': pilar, 'pilar_nome': pilares[pilar], 'fixa': fixa,
        'pilares': [{'valor': k, 'rotulo': n} for k, n in PILARES],
        'rotulos': {
            'meses': [servicos.rotulo_mes(m) for m in ctx['meses_quad']],
            'ano_anterior': servicos.rotulo_mes(ctx['mes_ano']),
            'mes_anterior': servicos.rotulo_mes(ctx['mes_anterior']),
            'competencia': servicos.rotulo_mes(comp.mes),
        },
        'meses_faltando': faltando,
        # Avisos do topo da tela do portal sobre a "meta mês anterior".
        'anterior_ausente': comp_ant is None,
        'anterior_aberta': bool(comp_ant and not comp_ant.fechada),
        'pode_editar': editar,
        'pode_fechar': pode_editar(request.user) and not comp.fechada,
        'pode_reabrir': comp.fechada and e_superadmin(request.user),
        'origens': [{'valor': k, 'rotulo': r} for k, r in servicos.ORIGENS.items()],
        'linhas': [_linha(lin, fixa, divisoes.get((lin['loja_id'], pilar))) for lin in p['linhas']],
        'grupos': [_linha(g, fixa) for g in p['grupos']],
        'rede': _linha(p['rede'], fixa),
        'excel': link(request, 'gestao_metas_excel', comp.codigo),
        'url_web': f'/gestao-metas/{comp.codigo}/#{pilar}',
    })


@_api()
def realizado_agora(request, codigo):
    """A coluna "Realizado até o momento" (gestao_metas/views.py ``realizado_agora``)."""
    from gestao_metas import servicos
    comp = _competencia(codigo)
    return Response(servicos.realizado_ate_agora(comp, forcar=request.query_params.get('atualizar') == '1'))


def _loja_e_pilar(d):
    """(loja_id, pilar) do pedido, ou erro — antes de chamar a view (id torto daria 500 lá)."""
    from gestao_metas.models import PILARES
    try:
        loja_id = int(d.get('loja'))
    except (TypeError, ValueError):
        return None, None, _erro('Loja não encontrada.')
    pilar = d.get('pilar')
    if pilar not in dict(PILARES):
        return None, None, _erro('Pilar desconhecido.')
    return loja_id, pilar, None


def _resposta_da_linha(request, comp, loja_id, pilar, conteudo):
    """O JSON da view do portal + a linha no formato do app (recalculada com divisão)."""
    from gestao_metas import servicos
    from gestao_metas.models import LojaMeta
    loja = LojaMeta.objects.filter(pk=loja_id).first()
    linha = servicos.linha_da_loja(comp, loja, pilar) if loja else None
    divisao = servicos.divisoes_da_competencia(comp).get((loja.id, pilar)) if loja else None
    return {'linha': _linha(linha, pilar == 'fixa', divisao) if linha else None,
            'salvo_em': conteudo.get('salvo_em')}


@_api(('POST',))
def salvar(request, codigo):
    """Um campo da linha (meta; na Fixa também CNs e decisão) pela view ``salvar`` do portal."""
    comp = _competencia(codigo)
    d = request.data if isinstance(request.data, dict) else {}
    loja_id, pilar, erro = _loja_e_pilar(d)
    if erro:
        return erro
    corpo = {'loja': loja_id, 'pilar': pilar, 'campo': d.get('campo'), 'valor': d.get('valor')}
    codigo_http, conteudo = _chamar_view_json(request, _web().salvar, comp.codigo, corpo=corpo)
    if codigo_http != 200 or not conteudo.get('ok'):
        return _erro(conteudo.get('erro') or 'Não foi possível salvar.', codigo_http if codigo_http >= 400 else 400)
    return Response({'ok': True, **_resposta_da_linha(request, comp, loja_id, pilar, conteudo)})


@_api(('POST',))
def dividir(request, codigo):
    """O botão "Salvar" da loja (view ``dividir``): grava e divide entre os consultores do Quadro de HC."""
    comp = _competencia(codigo)
    d = request.data if isinstance(request.data, dict) else {}
    loja_id, pilar, erro = _loja_e_pilar(d)
    if erro:
        return erro
    campos = d.get('campos') if isinstance(d.get('campos'), dict) else {}
    corpo = {'loja': loja_id, 'pilar': pilar, 'campos': campos}
    codigo_http, conteudo = _chamar_view_json(request, _web().dividir, comp.codigo, corpo=corpo)
    extra = _resposta_da_linha(request, comp, loja_id, pilar, conteudo)
    if codigo_http != 200 or not conteudo.get('ok'):
        return _erro(conteudo.get('erro') or 'Não foi possível salvar.', codigo_http if codigo_http >= 400 else 400,
                     **extra)
    div = conteudo.get('divisao') or {}
    return Response({'ok': True, **extra, 'divisao': {
        'total': div.get('total'), 'consultores': div.get('n'), 'base': div.get('base'),
        'cheios': div.get('cheios'),
        'itens': [{'nome': i.get('nome'), 'percentual': i.get('percentual'), 'meta': i.get('meta')}
                  for i in div.get('itens') or []],
    }, 'mensagem': 'Meta da {} salva e dividida entre {} consultor(es).'.format(
        conteudo.get('loja_nome') or 'loja', div.get('n') or 0)})


def _por_mensagens(resposta, avisos, padrao_ok):
    if getattr(resposta, 'status_code', 200) >= 400:
        try:
            conteudo = json.loads(resposta.content or b'{}')
        except ValueError:
            conteudo = {}
        return _erro(conteudo.get('erro') or 'Não foi possível concluir.', resposta.status_code)
    erro = next((t for n, t in avisos if n == 'error'), None)
    if erro:
        return _erro(erro)
    return Response({'ok': True, 'mensagem': next((t for n, t in avisos if n == 'success'), padrao_ok)})


@_api(('POST',))
def preencher(request, codigo):
    """Preenchimento rápido (view ``preencher``): referência × (1 + x%), arredondada."""
    comp = _competencia(codigo)
    d = request.data if isinstance(request.data, dict) else {}
    resposta, avisos = _chamar_view_form(request, _web().preencher, comp.codigo, post={
        'pilar': d.get('pilar'), 'origem': d.get('origem'), 'percentual': d.get('percentual') or '0',
        'arredondar': d.get('arredondar') or '0', 'somente_vazias': '1' if d.get('somente_vazias') else ''})
    return _por_mensagens(resposta, avisos, 'Metas preenchidas.')


@_api(('POST',))
def fechar(request, codigo):
    """Fechar (editor) ou reabrir (só SUPERADMIN) a competência pela view ``fechar``."""
    comp = _competencia(codigo)
    d = request.data if isinstance(request.data, dict) else {}
    acao = 'reabrir' if d.get('acao') == 'reabrir' else 'fechar'
    resposta, avisos = _chamar_view_form(request, _web().fechar, comp.codigo, post={'acao': acao})
    return _por_mensagens(resposta, avisos, 'Competência atualizada.')


@_api()
def historico(request, codigo):
    """Quem mudou o quê (gestao_metas/views.py ``historico``): os 200 mais recentes."""
    from gestao_metas.models import PILARES, HistoricoMeta
    comp = _competencia(codigo)
    itens = HistoricoMeta.objects.filter(competencia=comp)
    pilar = request.query_params.get('pilar') or ''
    if pilar:
        itens = itens.filter(pilar=pilar)
    nomes = dict(PILARES)
    from ..dados import iso
    return Response({'resultados': [{
        'id': h.pk, 'loja': h.loja_nome or None, 'pilar': h.pilar or None, 'pilar_nome': nomes.get(h.pilar) or None,
        'campo': h.campo, 'antes': h.antes or None, 'depois': h.depois or None, 'origem': h.origem or None,
        'por': h.por_nome or None, 'quando': iso(h.quando),
    } for h in itens[:200]]})


@registrar('gestao_metas_excel')
def _excel(user, codigo):
    """A planilha da competência (gestao_metas/views.py ``baixar_excel``), para quem entra no módulo."""
    from gestao_metas import exportar, servicos
    if not pode_entrar(user):
        raise PermissionError
    comp = _competencia(codigo)
    nome = 'METAS {:02d}.{} {}.xlsx'.format(comp.mes.month, comp.mes.year,
                                             servicos.MESES_LONGOS[comp.mes.month - 1].capitalize())
    return (io.BytesIO(exportar.gerar(comp)), nome,
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


urlpatterns = [
    path('', inicio, name='gestao_metas_inicio'),
    path('<str:codigo>/', competencia, name='gestao_metas_competencia'),
    path('<str:codigo>/realizado-agora/', realizado_agora, name='gestao_metas_realizado_agora'),
    path('<str:codigo>/salvar/', salvar, name='gestao_metas_salvar'),
    path('<str:codigo>/dividir/', dividir, name='gestao_metas_dividir'),
    path('<str:codigo>/preencher/', preencher, name='gestao_metas_preencher'),
    path('<str:codigo>/fechar/', fechar, name='gestao_metas_fechar'),
    path('<str:codigo>/historico/', historico, name='gestao_metas_historico'),
]
