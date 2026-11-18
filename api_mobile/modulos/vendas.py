"""Módulo Vendas no app (/api/mobile/v1/m/vendas/).

O que vai para o balcão, pelo celular:

- **Resumo** (``/``): o que a pessoa pode fazer em Vendas + os números do topo do
  dashboard da web (``vendas.views._indicadores``) no período escolhido.
- **Vendas** (``/vendas/``, ``/vendas/<id>/``): a lista e o detalhe, no recorte da web
  (``vendas.permissions.vendas_do_usuario`` via ``vendas.views._filtrar_vendas``).
- **Tabela de preços** (``/precos/``, ``/precos/<id>/``): o catálogo da web
  (``vendas.views.precos``): busca de ``vendas/busca.py``, preço e matriz de
  ``vendas.views._resumo_de_preco``, SKU por cor de ``vendas.slv.skus_do_item``.
- **Clientes** (``/clientes/``, ``/clientes/<cpf>/``): só com "Consultar clientes",
  no recorte de ``vendas.views_clientes.clientes_do_usuario``.
- **Orçamento** (``/orcamentos/``): o mesmo do /orcamentos/ do portal
  (``vendas/orcamentos.py``: ler_campos → ja_enviado → criar). O servidor só grava;
  quem manda é o WhatsApp do próprio aparelho (``Orcamento.link_whatsapp_app``).

- **Lançar venda** (``/lancar/…``): em ``vendas_lancar.py`` — o mesmo caminho da tela /vendas/nova/.
"""
import copy

from django.core.paginator import Paginator
from django.db.models import Case, Count, IntegerField, Q, When
from django.http import QueryDict
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from vendas.permissions import can_access_vendas, is_superadmin, pode, ve_rede

from .. import dados
from ..autenticacao import TokenMobileAuthentication
from . import permissao


def pode_entrar(user):
    """A porta da web: ``@exige('modulo.ver')`` do dashboard (vendas/views.py:103) e o
    ``vendas_liberado`` do menu (vendas/context_processors.py:12) — SUPERADMIN ou AcessoVendas
    com "Ver módulo" (vendas/permissions.py:93)."""
    return can_access_vendas(user)


MODULO = {'rotulo': 'Vendas', 'descricao': 'Tabela de preços, orçamento e suas vendas',
          'icone': 'cart-outline', 'cor': '#F39C12', 'pode_entrar': pode_entrar}

AUTH = [TokenMobileAuthentication]
PERM = [permissao('vendas')]
POR_PAGINA = 20


# ── Apoio ─────────────────────────────────────────────────────────────────────

def _num(valor):
    return None if valor is None else float(valor)


def _pagina(request, itens):
    """Lista ou queryset → (itens da página, meta) no formato das outras telas do app."""
    try:
        numero = max(int(request.query_params.get('pagina') or 1), 1)
    except ValueError:
        numero = 1
    paginador = Paginator(itens, POR_PAGINA)
    if numero > paginador.num_pages:
        return [], {'pagina': numero, 'total': paginador.count, 'tem_mais': False}
    pagina = paginador.page(numero)
    return list(pagina.object_list), {'pagina': numero, 'total': paginador.count, 'tem_mais': pagina.has_next()}


def _pedido_web(request, **params):
    """O HttpRequest da web com outro GET: para chamar o filtro da tela do portal sem reescrevê-lo."""
    web = copy.copy(request._request)
    q = QueryDict(mutable=True)
    q.update({k: str(v) for k, v in params.items() if v not in (None, '')})
    web.GET = q
    web.user = request.user
    return web


def _periodo(valor):
    """hoje | mes (padrão) | todos → date_from do filtro da web."""
    hoje = timezone.localdate()
    if valor == 'hoje':
        return 'hoje', hoje
    if valor == 'todos':
        return 'todos', None
    return 'mes', hoje.replace(day=1)


def _escopo(request):
    """Quem vê a rede escolhe: minhas, minha loja ou a rede. Quem não vê fica no recorte dele.

    São os filtros ``vendedor`` e ``loja`` da tela da web, aplicados DEPOIS do recorte
    (vendas/views.py:56): nenhum parâmetro alarga o que a pessoa enxerga.
    """
    user = request.user
    if not ve_rede(user):
        return 'minhas', {}
    escolha = request.query_params.get('escopo') or 'rede'
    if escolha == 'minhas':
        return 'minhas', {'vendedor': user.pk}
    if escolha == 'loja' and user.sector_id:
        return 'loja', {'loja': user.sector_id}
    return 'rede', {}


def _vendas_filtradas(request):
    from vendas.views import _filtrar_vendas
    periodo, desde = _periodo(request.query_params.get('periodo'))
    escopo, extra = _escopo(request)
    qs, _f, _q = _filtrar_vendas(_pedido_web(
        request, search=(request.query_params.get('busca') or '').strip(),
        date_from=desde.isoformat() if desde else '', **extra))
    return qs, periodo, escopo


def _pode(user):
    from orcamentos.permissoes import pode_usar
    return {
        'rede': ve_rede(user),
        'clientes': pode(user, 'clientes.ver'),
        'lancar': pode(user, 'vendas.lancar'),
        # Orçamento é o módulo /orcamentos/ do portal: vale a porta dele (orcamentos/views.py:39).
        'orcamentos': pode_usar(user),
        'superadmin': is_superadmin(user),
    }


def _sem_permissao(mensagem):
    return Response({'erro': mensagem}, status=status.HTTP_403_FORBIDDEN)


# ── Resumo ────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@authentication_classes(AUTH)
@permission_classes(PERM)
def resumo(request):
    """Os números do topo do dashboard (vendas.views._indicadores), no período e no recorte."""
    from vendas.models import ImportacaoPrecos, VendaServico
    from vendas.views import _indicadores

    user = request.user
    qs, periodo, escopo = _vendas_filtradas(request)
    ind = _indicadores(qs, user)
    tipos = dict(VendaServico.TIPOS)
    ultima = ImportacaoPrecos.objects.filter(erro='').order_by('-quando').first()
    saida = {
        'pode': _pode(user),
        'loja': user.sector.name if user.sector_id else None,
        'periodo': periodo,
        'escopo': escopo,
        'tabela_atualizada_em': dados.iso(ultima.quando) if ultima else None,
        'indicadores': {
            'total': _num(ind['kpi_total']), 'quantidade': ind['kpi_quantidade'], 'ticket': _num(ind['kpi_ticket']),
            'pecas': ind['kpi_pecas'], 'produtos': _num(ind['kpi_produtos']), 'servicos': _num(ind['kpi_servicos']),
            'hoje': ind['kpi_hoje'], 'no_mes': ind['kpi_no_mes'], 'fake': ind['kpi_fake'],
            'delta': _num(ind['kpi_delta']),
        },
        'serie': [{'dia': d['dia'].isoformat(), 'n': d['n']} for d in ind['serie_dias'][-14:]],
        'top_produtos': [{'nome': p['nome_produto'], 'n': p['n'], 'total': _num(p['total'])}
                         for p in ind['top_produtos']],
        'por_tipo_servico': [{'tipo': s['tipo'], 'n': s['n'], 'delta': _num(s['delta'])}
                             for s in ind['por_tipo_servico']],
        'tipos_servico': [{'valor': k, 'rotulo': v} for k, v in tipos.items()],
    }
    if 'por_loja' in ind:               # o ranking só sai para quem vê a rede (vendas/views.py:218)
        saida['por_loja'] = [{'nome': x['nome'], 'n': x['n']} for x in ind['por_loja']]
        saida['por_vendedor'] = [{'nome': x['nome'], 'n': x['n']} for x in ind['por_vendedor']]
    return Response(saida)


# ── Vendas ────────────────────────────────────────────────────────────────────

def _venda_resumo(v, rede):
    produtos = list(v.produtos.all())
    servicos = list(v.servicos.all())
    itens = [p.nome_produto for p in produtos] + [s.plano_novo or s.servico for s in servicos]
    return {
        'id': v.pk,
        'data': dados.iso(v.data_venda),
        'cliente': v.cliente_nome or 'Sem cliente',
        'loja': (v.loja.name if v.loja_id else v.pdv_nome) or None,
        'vendedor': (v.vendedor.get_full_name() if v.vendedor_id else None) if rede else None,
        'total': _num(v.total),
        'total_servicos': _num(v.total_servicos) if servicos else None,
        'forma_pagamento': v.get_forma_pagamento_display() if v.forma_pagamento else None,
        'tipo_venda': v.tipo_venda or None,
        'itens': itens[:3],
        'mais_itens': max(len(itens) - 3, 0),
        'numero_fake': v.numero_fake,
    }


@api_view(['GET'])
@authentication_classes(AUTH)
@permission_classes(PERM)
def vendas(request):
    qs, periodo, escopo = _vendas_filtradas(request)
    qs = qs.order_by('-data_venda', '-id').prefetch_related('produtos', 'servicos')
    itens, meta = _pagina(request, qs)
    rede = ve_rede(request.user)
    return Response({'resultados': [_venda_resumo(v, rede) for v in itens], 'periodo': periodo,
                     'escopo': escopo, 'rede': rede, **meta})


def _historico(venda):
    from vendas.models import RegistroAlteracao
    from vendas.views import _lotes_do_historico
    from vendas import edicao
    lotes = _lotes_do_historico(RegistroAlteracao.objects.filter(venda_id=venda.pk, tipo__in=edicao.TIPOS_VENDA))
    return [{'quando': dados.iso(l['quando']), 'por': l['usuario_nome'], 'motivo': l['motivo'],
             'linhas': [{'campo': h.rotulo, 'item': getattr(h, 'item', ''), 'antes': h.antes, 'depois': h.depois}
                        for h in l['linhas']]}
            for l in lotes if not l['excluida']]


@api_view(['GET'])
@authentication_classes(AUTH)
@permission_classes(PERM)
def venda(request, pk):
    """O detalhe da web (vendas/views.py:226): mesmo recorte, então id alheio dá 404."""
    from vendas.models import Venda
    from vendas.permissions import vendas_do_usuario

    user = request.user
    v = get_object_or_404(
        vendas_do_usuario(user, Venda.objects.select_related('loja', 'vendedor', 'created_by')
                          .prefetch_related('produtos', 'servicos', 'pagamentos')), pk=pk)
    # Histórico: SUPERADMIN, quem vê a rede ou o vendedor/quem lançou (vendas/views.py:238).
    ve_historico = is_superadmin(user) or ve_rede(user) or user.pk in (v.vendedor_id, v.created_by_id)
    return Response({
        **_venda_resumo(v, True),
        'cliente_cpf': v.cliente_cpf or None,
        'cliente_telefone': v.cliente_telefone or None,
        'lancada_por': v.created_by.get_full_name() if v.created_by_id else None,
        'comprovante': v.get_comprovante_fiscal_display(),
        'desconto_pix': _num(v.desconto_pix) if v.desconto_pix else None,
        'vivo_mais': v.vivo_mais,
        'vivo_mais_percentual': _num(v.vivo_mais_percentual),
        'plano_anterior': v.plano_anterior_nome or None,
        'observacao': v.observacao or None,
        'produtos': [{
            'id': p.pk, 'nome': p.nome_produto, 'sku': p.sku or None, 'cor': p.cor or None,
            'serial': p.serial or None, 'qtde': p.qtde, 'valor': _num(p.valor_venda),
            'valor_sugerido': _num(p.valor_sugerido), 'regra': p.regra_preco or None,
            'valor_editado': p.valor_editado, 'prateleira_infinita': p.prateleira_infinita,
            'renova': [r for r in (
                f'Renova Vini {p.renova_codigo}'.strip() if p.renova_vini else '',
                'Renova Alied' if p.renova_alied else '', 'Renova Assurant' if p.renova_assurant else '') if r],
        } for p in v.produtos.all()],
        'servicos': [{
            'id': s.pk, 'servico': s.servico, 'plano': s.plano_novo or None, 'valor': _num(s.valor_plano),
            'plano_anterior': s.plano_anterior_nome or None, 'valor_anterior': _num(s.valor_anterior),
            'delta': _num(s.delta), 'linha': s.numero_acesso or None, 'status': s.status_servico or None,
            'portabilidade': s.portabilidade, 'numero_fake': s.numero_fake,
        } for s in v.servicos.all()],
        'pagamentos': [{'forma': pg.get_forma_display(), 'valor': _num(pg.valor), 'parcelas': pg.parcelas}
                       for pg in v.pagamentos.all()],
        'historico': _historico(v) if ve_historico else None,
        'url_web': f'/vendas/{v.pk}/',
    })


# ── Tabela de preços ──────────────────────────────────────────────────────────

def _abas():
    from vendas.views import ABAS_PRECOS
    return [{'valor': k, 'rotulo': r} for k, r, _icone in ABAS_PRECOS]


def _produto_resumo(item, resumo, consulta=''):
    from vendas import slv
    from vendas.busca import sku_digitado
    extra = item.extra if isinstance(item.extra, dict) else {}
    variantes = slv.skus_do_item(item)
    digitado = sku_digitado(consulta, variantes) if consulta and variantes else None
    return {
        'id': item.pk,
        'nome': item.nome,
        'categoria': item.categoria,
        'marca': extra.get('FABRICANTE') or extra.get('MARCA') or None,
        'tipo': extra.get('CATEGORIA') or None,
        'a_partir': _num(resumo['a_partir']),
        'pix': _num(resumo['pix']),
        'cores': len(variantes),
        'sku_digitado': digitado,
    }


@api_view(['GET'])
@authentication_classes(AUTH)
@permission_classes(PERM)
def precos(request):
    """O catálogo da web (vendas/views.py:635): ativos, sem PLANOS; busca de vendas/busca.py."""
    from vendas.busca import buscar
    from vendas.models import ItemPreco
    from vendas.views import _resumo_de_preco

    categoria = (request.query_params.get('categoria') or '').strip()
    consulta = (request.query_params.get('busca') or '').strip()
    base = ItemPreco.objects.filter(ativo=True).exclude(categoria='PLANOS')
    if categoria:
        base = base.filter(categoria=categoria)
    if consulta:
        achados = buscar(consulta, limite=600, categoria=categoria or None)
        por_id = base.in_bulk(achados)
        itens = [por_id[i] for i in achados if i in por_id]
    else:
        itens = base.order_by(Case(When(categoria='SMARTPHONES', then=0),
                                   When(categoria='ELETRÔNICOS_LP_Conectados', then=1),
                                   default=2, output_field=IntegerField()), 'nome')
    pagina, meta = _pagina(request, itens)
    contagem = dict(ItemPreco.objects.filter(ativo=True).values_list('categoria').annotate(n=Count('id')))
    return Response({
        'resultados': [_produto_resumo(i, _resumo_de_preco(i), consulta) for i in pagina],
        'abas': [{**a, 'n': contagem.get(a['valor'], 0)} for a in _abas()],
        **meta,
    })


@api_view(['GET'])
@authentication_classes(AUTH)
@permission_classes(PERM)
def preco(request, pk):
    from vendas import slv
    from vendas.models import ItemPreco
    from vendas.views import _resumo_de_preco

    item = get_object_or_404(ItemPreco.objects.filter(ativo=True).exclude(categoria='PLANOS'), pk=pk)
    resumo = _resumo_de_preco(item)
    extra = item.extra if isinstance(item.extra, dict) else {}
    sku, cor = slv.sku_e_cor(item)
    return Response({
        **_produto_resumo(item, resumo),
        'cod_sap': item.cod_sap or None,
        'sku': sku or None,
        'cor': cor or None,
        'skus': slv.skus_do_item(item),
        'valor': _num(item.valor),
        'plano': item.plano or None,
        'grupamento': item.grupamento or None,
        'portfolio': extra.get('PORTFÓLIO') or None,
        'categoria_slv': slv.categoria_slv(item),
        'exige_serial': slv.exige_serial(item),
        'colunas': resumo['colunas'],
        'linhas': [{'grupo': l['grupo'], 'celulas': [{'valor': _num(c['valor']), 'menor': c['menor']}
                                                      for c in l['celulas']]} for l in resumo['linhas']],
        'atualizado_em': dados.iso(item.importado_em or item.updated_at),
    })


# ── Clientes ──────────────────────────────────────────────────────────────────

def _cliente_resumo(c):
    from vendas import slv
    return {
        'cpf': c.cpf, 'cpf_formatado': c.cpf_formatado, 'nome': c.nome,
        'telefone': slv.formatar_telefone(c.telefone) or None,
        'ultima_compra': c.ultima_compra.isoformat() if c.ultima_compra else None,
        'qtd_vendas': c.qtd_vendas, 'total_gasto': _num(c.total_gasto),
        'pdv_ultimo': c.pdv_ultimo or None, 'origem': c.origem,
    }


SITUACOES_CLIENTE = ('novos', 'ativos', 'sumidos', 'recorrentes', 'portal')


@api_view(['GET'])
@authentication_classes(AUTH)
@permission_classes(PERM)
def clientes(request):
    """A aba Clientes (vendas/views_clientes.py:56): exige "Consultar clientes" e o mesmo recorte."""
    from datetime import timedelta

    from vendas import slv
    from vendas.views_clientes import clientes_do_usuario

    if not pode(request.user, 'clientes.ver'):
        return _sem_permissao('Consultar clientes não está liberado para o seu usuário.')
    qs = clientes_do_usuario(request.user)
    consulta = (request.query_params.get('busca') or '').strip()
    if consulta:
        digitos = slv.so_digitos(consulta)
        filtro = Q(nome__icontains=consulta)
        if len(digitos) >= 4:
            filtro |= Q(cpf__startswith=digitos) | Q(telefone__contains=digitos) | Q(linha__contains=digitos)
        qs = qs.filter(filtro)
    hoje = timezone.localdate()
    situacao = request.query_params.get('situacao') or ''
    if situacao == 'novos':
        qs = qs.filter(primeira_compra__gte=hoje.replace(day=1))
    elif situacao == 'ativos':
        qs = qs.filter(ultima_compra__gte=hoje - timedelta(days=30))
    elif situacao == 'sumidos':
        qs = qs.filter(ultima_compra__lt=hoje - timedelta(days=180))
    elif situacao == 'recorrentes':
        qs = qs.filter(qtd_vendas__gte=2)
    elif situacao == 'portal':
        qs = qs.filter(origem='PORTAL')
    itens, meta = _pagina(request, qs.order_by('-ultima_compra', 'nome'))
    return Response({'resultados': [_cliente_resumo(c) for c in itens], **meta})


@api_view(['GET'])
@authentication_classes(AUTH)
@permission_classes(PERM)
def cliente(request, cpf):
    """A ficha da web (vendas/views_clientes.py:108): CPF fora do recorte dá 404."""
    from vendas import slv
    from vendas.models import CompraVivoGo, Venda
    from vendas.permissions import vendas_do_usuario
    from vendas.views_clientes import clientes_do_usuario

    user = request.user
    if not pode(user, 'clientes.ver'):
        return _sem_permissao('Consultar clientes não está liberado para o seu usuário.')
    cpf = slv.so_digitos(cpf)
    c = get_object_or_404(clientes_do_usuario(user), cpf=cpf)
    compras = list(CompraVivoGo.objects.filter(cpf=cpf).order_by('-data_venda', '-data_insercao')[:100])
    grupos, indice = [], {}
    for x in compras:
        chave = x.id_venda or x.chave
        if chave not in indice:
            indice[chave] = {'id': x.id_venda or None, 'data': x.data_venda.isoformat() if x.data_venda else None,
                             'pdv': x.pdv or None, 'vendedor': x.vendedor or None, 'itens': []}
            grupos.append(indice[chave])
        indice[chave]['itens'].append({'item': x.item or '—', 'tipo': 'Produto' if x.tipo == 'P' else 'Serviço',
                                       'qtde': x.qtd, 'valor': _num(x.receita)})
    portal = list(Venda.objects.filter(cliente=c).select_related('loja', 'vendedor')
                  .prefetch_related('produtos', 'servicos').order_by('-data_venda')[:30])
    # A web lista todas as vendas do cliente; abrir o detalhe segue o recorte de vendas.
    abriveis = set(vendas_do_usuario(user, Venda.objects.filter(pk__in=[v.pk for v in portal]))
                   .values_list('pk', flat=True))
    return Response({
        **_cliente_resumo(c),
        'linha': slv.formatar_telefone(c.linha) or None,
        'plano': c.plano_nome or c.plano_vivogo or None,
        'endereco': c.endereco or None,
        'primeira_compra': c.primeira_compra.isoformat() if c.primeira_compra else None,
        'vendas_portal': [{**_venda_resumo(v, True), 'pode_abrir': v.pk in abriveis} for v in portal],
        'compras_vivogo': grupos,
    })


# ── Orçamento ─────────────────────────────────────────────────────────────────

def _orcamento(o):
    return {
        'id': o.pk, 'cliente': o.cliente_nome, 'pilar': o.pilar, 'pilar_nome': o.get_pilar_display(),
        'numero': o.numero_formatado, 'texto': o.texto, 'mensagem': o.mensagem,
        'status': o.status, 'status_nome': o.get_status_display(), 'criado_em': dados.iso(o.criado_em),
        'loja': o.loja.name if o.loja_id else None,
        'link_whatsapp': o.link_whatsapp_app,
    }


@api_view(['GET', 'POST'])
@authentication_classes(AUTH)
@permission_classes(PERM)
def orcamentos(request):
    """O /orcamentos/ do portal (orcamentos/views.py:57) sem o mailing.

    GET: modelo da mensagem (para a prévia), pilares e os orçamentos da própria pessoa.
    POST: ``ler_campos`` → ``ja_enviado`` (clique duplo não duplica) → ``criar`` com status
    ABERTO. Nada sai do servidor: o app abre ``link_whatsapp`` no WhatsApp do aparelho.
    """
    from orcamentos.permissoes import pode_usar
    from vendas import orcamentos as svc
    from vendas.models import Orcamento

    user = request.user
    if not pode_usar(user):
        return _sem_permissao('O módulo de Orçamentos é para quem está lotado em loja.')
    if request.method == 'POST':
        campos, erros = svc.ler_campos({k: str(request.data.get(k) or '')
                                        for k in ('cliente_nome', 'pilar', 'numero', 'texto')})
        if erros:
            return Response({'erro': '\n'.join(erros), 'erros': erros}, status=status.HTTP_400_BAD_REQUEST)
        orcamento = svc.ja_enviado(user, campos) or svc.criar(user, campos)
        return Response(_orcamento(orcamento), status=status.HTTP_201_CREATED)

    lista = Orcamento.objects.select_related('loja').filter(vendedor=user)
    consulta = (request.query_params.get('busca') or '').strip()
    if consulta:
        digitos = ''.join(ch for ch in consulta if ch.isdigit())
        filtro = Q(cliente_nome__icontains=consulta) | Q(texto__icontains=consulta)
        lista = lista.filter(filtro | Q(numero__contains=digitos) if digitos else filtro)
    itens, meta = _pagina(request, lista)
    return Response({
        'modelo': svc.modelo_do_vendedor(user),
        'pilares': [{'valor': k, 'rotulo': r} for k, r in Orcamento.PILARES],
        'limite': svc.LIMITE_TEXTO,
        'hoje': Orcamento.objects.filter(vendedor=user, criado_em__date=timezone.localdate()).count(),
        'resultados': [_orcamento(o) for o in itens],
        **meta,
    })


from . import vendas_lancar  # noqa: E402 — usa AUTH, PERM e _pedido_web daqui

urlpatterns = [
    path('', resumo, name='vendas_resumo'),
    path('lancar/', vendas_lancar.lancar, name='vendas_lancar'),
    path('lancar/cliente/', vendas_lancar.cliente, name='vendas_lancar_cliente'),
    path('lancar/produtos/', vendas_lancar.produtos, name='vendas_lancar_produtos'),
    path('lancar/renova/', vendas_lancar.renova, name='vendas_lancar_renova'),
    path('lancar/planos-fora/', vendas_lancar.planos_fora, name='vendas_lancar_planos_fora'),
    path('vendas/', vendas, name='vendas_lista'),
    path('vendas/<int:pk>/', venda, name='vendas_detalhe'),
    path('precos/', precos, name='vendas_precos'),
    path('precos/<int:pk>/', preco, name='vendas_preco'),
    path('clientes/', clientes, name='vendas_clientes'),
    path('clientes/<str:cpf>/', cliente, name='vendas_cliente'),
    path('orcamentos/', orcamentos, name='vendas_orcamentos'),
]
