"""Vendas (/vendas/) no assistente — leitura completa, com o recorte do módulo.

Porta e recorte são os de vendas/permissions.py: só entra quem tem "Ver módulo"
(SUPERADMIN ou AcessoVendas liberado em /vendas/acessos/); sem "Ver a rede
inteira" a pessoa vê só as vendas em que é a vendedora ou que ela lançou
(``vendas_do_usuario``) e os clientes da loja dela (``clientes_do_usuario``,
loja = setor principal). Os filtros e os números do topo saem das mesmas funções
da tela (``_filtrar_vendas`` e ``_indicadores``), então o assistente e a tela
contam a mesma coisa.

SÓ LEITURA: lançar venda, importar ou criar produto, parametrizar, sincronizar
o Vivo GO, exportar arquivo e mandar orçamento por WhatsApp continuam só na
tela. Orçamentos saíram para /orcamentos/ (use ler_pagina).
"""
import logging
from decimal import Decimal

from django.http import QueryDict

from .comum import Invalido, _cortar, _dh, _int, _limite, _normal, _obj, _txt, _url

logger = logging.getLogger(__name__)


def _moeda(v):
    if v is None:
        return '—'
    texto = f'{Decimal(v):,.2f}'
    return 'R$ ' + texto.replace(',', '_').replace('.', ',').replace('_', '.')


def _exige(user, chave='modulo.ver'):
    from vendas.permissions import ROTULOS, can_access_vendas, pode

    if not can_access_vendas(user):
        raise Invalido('O módulo de Vendas não está liberado para o seu usuário (quem libera é o SUPERADMIN em '
                       '/vendas/acessos/).')
    if not pode(user, chave):
        raise Invalido(f'Esta parte de Vendas ("{ROTULOS.get(chave, chave)}") não está liberada para o seu usuário.')


class _Pedido:
    """Só o que ``_filtrar_vendas`` lê do request: GET e user."""

    def __init__(self, user, get):
        self.user = user
        self.GET = get


def _filtros(user, args):
    from vendas.views import _filtrar_vendas

    get = QueryDict(mutable=True)
    mapa = {'busca': 'search', 'de': 'date_from', 'ate': 'date_to', 'loja_id': 'loja', 'vendedor_id': 'vendedor',
            'tipo_venda': 'tipo_venda', 'comprovante': 'comprovante', 'servico': 'servico'}
    for arg, campo in mapa.items():
        if args.get(arg) not in (None, ''):
            get[campo] = str(args[arg])
    if args.get('numero_fake'):
        get['fake'] = '1'
    return _filtrar_vendas(_Pedido(user, get))


def _desc(filtros):
    nomes = {'search': 'busca', 'date_from': 'de', 'date_to': 'até', 'loja': 'loja_id', 'vendedor': 'vendedor_id',
             'tipo_venda': 'tipo', 'comprovante': 'comprovante', 'servico': 'serviço', 'fake': 'número fictício'}
    partes = [f'{nomes.get(k, k)}={v}' for k, v in filtros.items() if v]
    return ' · filtros: ' + ', '.join(partes) if partes else ''


def _recorte(user):
    from vendas.permissions import ve_rede

    return 'a rede inteira' if ve_rede(user) else 'as vendas em que você é o vendedor ou que você lançou'


def _ler_painel(user, args):
    from vendas.views import _indicadores

    _exige(user)
    qs, filtros, _ = _filtros(user, args)
    k = _indicadores(qs, user)
    linhas = [f'Vendas — recorte: {_recorte(user)}{_desc(filtros)}',
              f'{k["kpi_quantidade"]} venda(s) · total {_moeda(k["kpi_total"])} (produtos {_moeda(k["kpi_produtos"])}, '
              f'serviços {_moeda(k["kpi_servicos"])}) · ticket médio {_moeda(k["kpi_ticket"])} · {k["kpi_pecas"]} peça(s)',
              f'No mês: {k["kpi_no_mes"]} · hoje: {k["kpi_hoje"]} · com número fictício: {k["kpi_fake"]} · delta dos '
              f'serviços {_moeda(k["kpi_delta"])} · valores editados à mão: {k["kpi_editados"]}']
    serie = [x for x in k['serie_dias'] if x['n']]
    if serie:
        linhas.append('Vendas por dia (últimos 30, só dias com venda): '
                      + ', '.join(f'{x["dia"]:%d/%m} {x["n"]}' for x in serie))
    if k['top_produtos']:
        linhas.append('Produtos que mais faturaram: ' + '; '.join(
            f'{p["nome_produto"]} ({p["n"]} un., {_moeda(p["total"])})' for p in k['top_produtos']))
    if k['por_tipo_servico']:
        linhas.append('Serviços por tipo: ' + '; '.join(
            f'{s["tipo"]} {s["n"]} (delta {_moeda(s["delta"])})' for s in k['por_tipo_servico']))
    if k['top_servicos']:
        linhas.append('Serviços que mais faturaram: ' + '; '.join(
            f'{s["servico"]} ({s["n"]}, {_moeda(s["total"])})' for s in k['top_servicos']))
    if k.get('por_loja'):
        linhas.append('Ranking por loja (qtd): ' + '; '.join(f'{x["nome"]} {x["n"]}' for x in k['por_loja']))
    if k.get('por_vendedor'):
        linhas.append('Ranking por vendedor (qtd): ' + '; '.join(f'{x["nome"]} {x["n"]}' for x in k['por_vendedor']))
    return '\n'.join(linhas)


def _ler_lista(user, args):
    _exige(user)
    qs, filtros, _ = _filtros(user, args)
    qs = qs.order_by('-data_venda', '-id').prefetch_related('produtos', 'servicos')
    total = qs.count()
    limite = _limite(args, 20, 200)
    linhas = [f'Vendas ({total}) — recorte: {_recorte(user)}{_desc(filtros)}']
    for v in qs[:limite]:
        prods = list(v.produtos.all())
        servs = list(v.servicos.all())
        valor = sum((p.valor_venda or 0) * (p.qtde or 0) for p in prods) + sum((s.valor_plano or 0) for s in servs)
        itens = [f'{p.qtde}× {p.nome_produto}' for p in prods[:3]] + [s.servico for s in servs[:3]]
        linhas.append(f'  #{v.pk} · {_dh(v.data_venda)} · {v.loja.name if v.loja_id else v.pdv_nome or "—"} · vendedor '
                      f'{(v.vendedor.get_full_name() if v.vendedor_id else "—")} · cliente {v.cliente_nome or "—"} · '
                      f'{_moeda(valor)} · {_cortar(", ".join(itens), 160) or "sem itens"}'
                      + (' · nº fictício' if v.numero_fake else ''))
    if total > limite:
        linhas.append(f'… e mais {total - limite}. Refine com de/ate/loja_id/vendedor_id/busca.')
    return '\n'.join(linhas)


def _ler_venda(user, args):
    from vendas.models import Venda
    from vendas.permissions import vendas_do_usuario

    _exige(user)
    try:
        pk = int(args.get('venda_id'))
    except (TypeError, ValueError):
        raise Invalido('Informe venda_id (o número da venda).') from None
    v = (vendas_do_usuario(user, Venda.objects.select_related('loja', 'vendedor', 'created_by'))
         .prefetch_related('produtos', 'servicos', 'pagamentos').filter(pk=pk).first())
    if v is None:
        raise Invalido('Venda não encontrada — ou fora do que você pode ver em Vendas.')
    linhas = [f'Venda #{v.pk} · {_dh(v.data_venda)} · loja {v.loja.name if v.loja_id else v.pdv_nome or "—"}'
              + (f'/{v.uf}' if v.uf else ''),
              f'Vendedor: {v.vendedor.get_full_name() if v.vendedor_id else "—"} · lançada por '
              f'{v.created_by.get_full_name() if v.created_by_id else "—"} em {_dh(v.created_at)}',
              f'Cliente: {v.cliente_nome or "—"} · CPF/CNPJ {v.cliente_cpf or "—"}'
              + (f' · tel. {v.cliente_telefone}' if v.cliente_telefone else ''),
              f'Tipo: {v.tipo_venda or "—"} · comprovante {v.get_comprovante_fiscal_display() if v.comprovante_fiscal else "—"}'
              + (' · estoque avançado' if v.estoque_avancado else '') + (' · número fictício' if v.numero_fake else '')]
    pagamentos = list(v.pagamentos.all())
    if pagamentos:
        linhas.append('Pagamento: ' + '; '.join(
            f'{p.get_forma_display()} {_moeda(p.valor)}' + (f' em {p.parcelas}x' if p.parcelas else '') for p in pagamentos))
    elif v.forma_pagamento:
        linhas.append(f'Pagamento: {v.forma_pagamento}' + (f' em {v.parcelas}x' if v.parcelas else ''))
    if v.vivo_mais:
        linhas.append(f'Pago pelo Vivo+ ({v.vivo_mais_percentual or 0}%).')
    for p in v.produtos.all():
        linhas.append(f'  Produto: {p.qtde}× {p.nome_produto}' + (f' ({p.cor})' if p.cor else '')
                      + (f' · SKU {p.sku}' if p.sku else ' · sem SKU') + (f' · serial {p.serial}' if p.serial else '')
                      + f' · {_moeda(p.valor_venda)}' + (f' (sugerido {_moeda(p.valor_sugerido)}, {p.regra_preco})' if p.valor_sugerido is not None else '')
                      + (' · VALOR EDITADO À MÃO' if p.valor_editado else '') + (f' · pilar {p.pilar}' if p.pilar else ''))
    for s in v.servicos.all():
        linhas.append(f'  Serviço: {s.servico}' + (f' · {s.get_tipo_servico_display()}' if s.tipo_servico else '')
                      + (f' · plano {s.plano_novo}' if s.plano_novo else '') + f' · {_moeda(s.valor_plano)}'
                      + (f' · anterior {s.plano_anterior_nome} {_moeda(s.valor_anterior)}' if s.plano_anterior_nome else '')
                      + (f' · delta {_moeda(s.delta)}' if s.delta is not None else '')
                      + (f' · acesso {s.numero_acesso}' if s.numero_acesso else '')
                      + (' · portabilidade' if getattr(s, 'portabilidade', False) else ''))
    if v.observacao:
        linhas.append(f'Observação: {_cortar(v.observacao, 300)}')
    linhas.append('Tela: ' + _url(f'/vendas/{v.pk}/'))
    return '\n'.join(linhas)


def _linha_preco(item, resumo):
    from vendas import slv

    skus = slv.skus_do_item(item)
    sku_txt = (' · SKUs: ' + ', '.join(f'{x["sku"]}' + (f' ({x["cor"]})' if x['cor'] else '') for x in skus[:6])
               + (f' e mais {len(skus) - 6}' if len(skus) > 6 else '')) if skus else (
        f' · cód. SAP {item.cod_sap}' if item.cod_sap else '')
    pix = f' · PIX {_moeda(resumo["pix"])}' if resumo.get('pix') else ''
    return (f'  #{item.pk} · {item.nome} [{item.categoria}] · a partir de {_moeda(resumo["a_partir"])}{pix}{sku_txt}')


def _ler_precos(user, args):
    from vendas.models import ItemPreco
    from vendas.views import _resumo_de_preco

    _exige(user)
    base = ItemPreco.objects.filter(ativo=True).exclude(categoria='PLANOS')
    if args.get('categoria'):
        base = base.filter(categoria__iexact=str(args['categoria']).strip())
    busca = str(args.get('busca') or '').strip()
    if busca:
        from vendas.busca import buscar

        achados = buscar(busca, limite=200, categoria=args.get('categoria') or None)
        por_id = base.in_bulk(achados)
        itens = [por_id[i] for i in achados if i in por_id]
    else:
        itens = list(base.order_by('categoria', 'nome')[:500])
    limite = _limite(args, 15, 100)
    linhas = [f'Tabela de preços — {len(itens)} item(ns)' + (f' para "{busca}"' if busca else '')
              + (f' em {args["categoria"]}' if args.get('categoria') else '')]
    if not busca and not args.get('categoria'):
        from django.db.models import Count

        cats = ItemPreco.objects.filter(ativo=True).values_list('categoria').annotate(n=Count('id')).order_by('categoria')
        linhas.append('Categorias: ' + ', '.join(f'{c} ({n})' for c, n in cats))
    linhas += [_linha_preco(i, _resumo_de_preco(i)) for i in itens[:limite]]
    if len(itens) > limite:
        linhas.append(f'… e mais {len(itens) - limite}. Refine a busca ou peça o produto pelo id (o_que=produto).')
    return '\n'.join(linhas)


def _ler_produto(user, args):
    from vendas import slv
    from vendas.models import ItemPreco
    from vendas.views import _resumo_de_preco

    _exige(user)
    try:
        item = ItemPreco.objects.get(pk=int(args.get('item_id')))
    except (TypeError, ValueError, ItemPreco.DoesNotExist):
        raise Invalido('Produto não encontrado: passe item_id (de o_que=precos).') from None
    r = _resumo_de_preco(item)
    extra = item.extra if isinstance(item.extra, dict) else {}
    linhas = [f'#{item.pk} · {item.nome} · aba {item.categoria}' + (' (inativo)' if not item.ativo else ''),
              f'Categoria SLV: {slv.categoria_slv(item)} · fabricante {extra.get("FABRICANTE") or extra.get("MARCA") or "—"}'
              f' · tipo {extra.get("CATEGORIA") or "—"} · cód. SAP {item.cod_sap or "—"}'
              + (f' · plano {item.plano}' if item.plano else '') + (f' · grupamento {item.grupamento}' if item.grupamento else ''),
              f'A partir de {_moeda(r["a_partir"])}' + (f' · menor PIX {_moeda(r["pix"])}' if r.get('pix') else '')]
    skus = slv.skus_do_item(item)
    if skus:
        linhas.append('SKUs (um por cor): ' + ', '.join(f'{x["sku"]}' + (f' {x["cor"]}' if x['cor'] else '') for x in skus))
    if r['linhas']:
        colunas = [c['rotulo'] for c in r['colunas']]
        linhas.append('Tabela (grupo do plano × condição): ' + ' | '.join(colunas))
        for l in r['linhas'][:20]:
            linhas.append(f'  {l["grupo"]}: ' + ' | '.join(_moeda(c['valor']) if c['valor'] is not None else '—'
                                                          for c in l['celulas']))
    elif item.valor is not None:
        linhas.append(f'Valor: {_moeda(item.valor)}')
    if item.importado_em:
        linhas.append(f'Importado em {_dh(item.importado_em)}.')
    return '\n'.join(linhas)


def _ler_clientes(user, args):
    from datetime import timedelta

    from django.db.models import Q
    from django.utils import timezone

    from vendas import slv
    from vendas.permissions import ve_rede
    from vendas.views_clientes import clientes_do_usuario

    _exige(user, 'clientes.ver')
    base = clientes_do_usuario(user)
    qs = base
    q = str(args.get('busca') or '').strip()
    if q:
        digitos = slv.so_digitos(q)
        filtro = Q(nome__icontains=q)
        if len(digitos) >= 4:
            filtro |= Q(cpf__startswith=digitos) | Q(telefone__contains=digitos) | Q(linha__contains=digitos)
        qs = qs.filter(filtro)
    hoje = timezone.localdate()
    situacao = _normal(args.get('situacao'))
    if situacao == 'novos':
        qs = qs.filter(primeira_compra__gte=hoje.replace(day=1))
    elif situacao == 'ativos':
        qs = qs.filter(ultima_compra__gte=hoje - timedelta(days=30))
    elif situacao == 'sumidos':
        qs = qs.filter(ultima_compra__lt=hoje - timedelta(days=180))
    elif situacao == 'recorrentes':
        qs = qs.filter(qtd_vendas__gte=2)
    total = qs.count()
    limite = _limite(args, 15, 100)
    linhas = [f'Clientes — recorte: {"a rede inteira" if ve_rede(user) else "os da sua loja"} · {total} encontrado(s)'
              f' de {base.count()} na base']
    for c in qs.order_by('-ultima_compra', 'nome')[:limite]:
        linhas.append(f'  {c.nome} · {c.cpf} · {c.qtd_vendas} venda(s), {_moeda(c.total_gasto)} · última '
                      f'{c.ultima_compra:%d/%m/%Y}' if c.ultima_compra else f'  {c.nome} · {c.cpf} · sem compra registrada')
    if total > limite:
        linhas.append(f'… e mais {total - limite}.')
    return '\n'.join(linhas)


def _ler_cliente(user, args):
    from vendas import slv
    from vendas.models import CompraVivoGo, Venda
    from vendas.views_clientes import clientes_do_usuario

    _exige(user, 'clientes.ver')
    cpf = slv.so_digitos(args.get('cpf') or '')
    c = clientes_do_usuario(user).filter(cpf=cpf).first() if cpf else None
    if c is None:
        raise Invalido('Cliente não encontrado — ou fora do recorte da sua loja.')
    linhas = [f'{c.nome} · CPF/CNPJ {c.cpf} · tel. {slv.formatar_telefone(c.telefone) or "—"} · linha '
              f'{slv.formatar_telefone(c.linha) or "—"}',
              f'{c.cidade or "—"}/{c.uf or "—"} · plano {c.plano_nome or c.plano_vivogo or "—"} · {c.qtd_vendas} venda(s), '
              f'{_moeda(c.total_gasto)} · primeira {c.primeira_compra or "—"} · última {c.ultima_compra or "—"} · '
              f'último PDV {c.pdv_ultimo or "—"} · último vendedor {c.vendedor_ultimo or "—"}']
    compras = list(CompraVivoGo.objects.filter(cpf=c.cpf).order_by('-data_venda', '-data_insercao')[:15])
    if compras:
        linhas.append('Últimas compras (Vivo GO):')
        linhas += [f'  {x.data_venda:%d/%m/%Y} · {x.item or "—"} · {x.pdv or "—"} · {x.vendedor or "—"}'
                   if x.data_venda else f'  {x.item or "—"} · {x.pdv or "—"}' for x in compras]
    portal = list(Venda.objects.filter(cliente=c).order_by('-data_venda')[:10])
    if portal:
        linhas.append('Vendas no portal: ' + ', '.join(f'#{v.pk} ({v.data_venda:%d/%m/%Y})' for v in portal))
    return '\n'.join(linhas)


def _ler_acessos(user, args):
    from vendas.permissions import FUNCOES, chaves_de, is_superadmin

    chaves = chaves_de(user)
    if not chaves:
        return 'Você não tem acesso ao módulo de Vendas (quem libera é o SUPERADMIN em /vendas/acessos/).'
    linhas = ['Seu acesso em Vendas' + (' (SUPERADMIN: tudo)' if is_superadmin(user) else '') + ':']
    linhas += [f'  {"✓" if c in chaves else "✗"} {r} — {d}' for c, r, d, _ in FUNCOES]
    return '\n'.join(linhas)


LEITURAS = {'painel': _ler_painel, 'lista': _ler_lista, 'venda': _ler_venda, 'precos': _ler_precos,
            'produto': _ler_produto, 'clientes': _ler_clientes, 'cliente': _ler_cliente, 'acessos': _ler_acessos}
APELIDOS = {'dashboard': 'painel', 'indicadores': 'painel', 'vendas': 'lista', 'tabela': 'precos', 'preco': 'precos',
            'tabela_de_precos': 'precos', 'item': 'produto', 'sku': 'precos', 'permissoes': 'acessos'}


def _vendas_consultar(user, args):
    o_que = _normal(args.get('o_que') or 'painel').replace(' ', '_').replace('-', '_')
    if o_que == 'orcamentos':
        return 'Orçamentos saíram de Vendas: ficam em /orcamentos/ (use ler_pagina com /orcamentos/).'
    leitura = LEITURAS.get(APELIDOS.get(o_que, o_que))
    if leitura is None:
        raise Invalido('o_que inválido. Use: ' + ' | '.join(LEITURAS) + '.')
    return leitura(user, args)


TOOLS = {
    'vendas_consultar': {
        'fn': _vendas_consultar,
        'description': (
            'Vendas (/vendas/), SÓ LEITURA, com o recorte que o usuário tem no módulo (AcessoVendas: sem "Ver a rede '
            'inteira" ele vê só as vendas dele — vendedor ou quem lançou — e os clientes da loja dele). o_que: painel '
            '(totais, ticket, série de 30 dias, top produtos e serviços, ranking por loja/vendedor para quem vê a rede) '
            '| lista (vendas com filtros) | venda (detalhe: itens com SKU/serial, serviços com delta, pagamento; '
            'venda_id) | precos (tabela de preços: busca por nome ou SKU, categoria; a partir de, PIX e SKUs) | produto '
            '(matriz completa grupo × condição; item_id) | clientes (busca, situacao) | cliente (cpf) | acessos (o '
            'que ele pode no módulo). Filtros: de/ate (AAAA-MM-DD), loja_id, vendedor_id, busca, tipo_venda, '
            'comprovante, servico, numero_fake. Lançar, importar e parametrizar só pela tela.'),
        'input_schema': _obj(
            ['o_que'],
            o_que=_txt('painel | lista | venda | precos | produto | clientes | cliente | acessos.'),
            de=_txt('Data inicial AAAA-MM-DD.'), ate=_txt('Data final AAAA-MM-DD.'),
            loja_id=_int('Id da loja (setor).'), vendedor_id=_int('Id do vendedor.'),
            busca=_txt('Texto: cliente/CPF/PDV/nº da venda; em precos, nome ou SKU; em clientes, nome/CPF/telefone.'),
            tipo_venda=_txt('Tipo de venda.'), comprovante=_txt('Comprovante fiscal.'),
            servico=_txt('Tipo de serviço.'), numero_fake={'type': 'boolean', 'description': 'Só vendas com número fictício.'},
            venda_id=_int('Número da venda.'), item_id=_int('Id do produto na tabela de preços.'),
            categoria=_txt('Aba da tabela de preços (ex.: SMARTPHONES).'),
            situacao=_txt('clientes: novos | ativos | sumidos | recorrentes.'), cpf=_txt('CPF/CNPJ do cliente.'),
            limite=_int('Quantas linhas listar.')),
    },
}
