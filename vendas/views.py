import csv
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.db.models import Count, DecimalField, F, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from users.models import Sector, User

from . import slv
from .models import (SEGMENTACOES, Cliente, ConfiguracaoVendas, ImportacaoPrecos, ItemPreco, Plano,
                     RegistroAlteracao, ServicoAdicional, Venda, VendaProduto, VendaServico)
from .permissions import (can_access_vendas, is_superadmin, pode_gerenciar_precos,
                          vendas_do_usuario)
from .clientes import buscar_historico
from .services import importar_tabela_precos


def _deny(request):
    messages.error(request, 'Acesso restrito.')
    return redirect('home')


def _parse_decimal(raw):
    if raw in (None, ''):
        return None
    txt = str(raw).replace('R$', '').replace(' ', '').strip()
    if ',' in txt and '.' in txt:
        txt = txt.replace('.', '').replace(',', '.')
    elif ',' in txt:
        txt = txt.replace(',', '.')
    try:
        return Decimal(txt)
    except (InvalidOperation, ValueError):
        return None


def _filtrar_vendas(request):
    """Aplica os filtros de GET e devolve (queryset, filtros_dict, filter_query_string)."""
    # O recorte vem antes de qualquer filtro: vendedor só enxerga o que é dele,
    # e nenhum parâmetro de URL contorna isso.
    qs = vendas_do_usuario(
        request.user, Venda.objects.select_related('loja', 'vendedor').all())

    search = request.GET.get('search', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    loja_id = request.GET.get('loja', '').strip()
    vendedor_id = request.GET.get('vendedor', '').strip()
    tipo_venda = request.GET.get('tipo_venda', '').strip()
    comprovante = request.GET.get('comprovante', '').strip()
    tipo_servico = request.GET.get('servico', '').strip()
    fake = request.GET.get('fake', '').strip()

    if search:
        qs = qs.filter(
            Q(cliente_nome__icontains=search) | Q(cliente_cpf__icontains=search)
            | Q(pdv_nome__icontains=search) | Q(id__icontains=search)
        )
    if date_from and parse_date(date_from):
        qs = qs.filter(data_venda__date__gte=parse_date(date_from))
    if date_to and parse_date(date_to):
        qs = qs.filter(data_venda__date__lte=parse_date(date_to))
    if loja_id.isdigit():
        qs = qs.filter(loja_id=int(loja_id))
    if vendedor_id.isdigit():
        qs = qs.filter(vendedor_id=int(vendedor_id))
    if tipo_venda:
        qs = qs.filter(tipo_venda=tipo_venda)
    if comprovante:
        qs = qs.filter(comprovante_fiscal=comprovante)
    if tipo_servico in dict(VendaServico.TIPOS):
        qs = qs.filter(servicos__tipo_servico=tipo_servico).distinct()
    if fake == '1':
        # [Venda de serviços.RF001] conferência das vendas com número fictício.
        qs = qs.filter(numero_fake=True)

    filtros = {
        'search': search, 'date_from': date_from, 'date_to': date_to,
        'loja': loja_id, 'vendedor': vendedor_id, 'tipo_venda': tipo_venda,
        'comprovante': comprovante, 'servico': tipo_servico, 'fake': fake,
    }
    filter_query_string = urlencode({k: v for k, v in filtros.items() if v})
    return qs, filtros, filter_query_string


@login_required
def dashboard(request):
    if not can_access_vendas(request.user):
        return _deny(request)

    qs, filtros, filter_query_string = _filtrar_vendas(request)

    export_format = request.GET.get('export', '')
    if export_format in ('csv', 'xlsx'):
        return venda_export(request)

    qs = qs.order_by('-data_venda', '-id')
    lista = qs.prefetch_related('produtos', 'servicos')

    try:
        per_page = int(request.GET.get('per_page', '25'))
        if per_page not in (25, 50, 100, 200):
            per_page = 25
    except (ValueError, TypeError):
        per_page = 25

    paginator = Paginator(lista, per_page)
    page = request.GET.get('page')
    try:
        vendas_page = paginator.page(page)
    except PageNotAnInteger:
        vendas_page = paginator.page(1)
    except EmptyPage:
        vendas_page = paginator.page(paginator.num_pages)

    context = {
        'vendas': vendas_page,
        'paginator': paginator,
        'filtros': filtros,
        'filter_query_string': filter_query_string,
        'per_page': per_page,
        'lojas': Sector.objects.all().order_by('name'),
        'vendedores': User.objects.filter(is_active=True).order_by('first_name', 'last_name'),
        'comprovante_choices': Venda.COMPROVANTE_CHOICES,
        'tipos_servico': VendaServico.TIPOS,
        'is_superadmin': is_superadmin(request.user),
        'pode_gerenciar_precos': pode_gerenciar_precos(request.user),
        'aba': 'vendas',
    }
    context.update(_indicadores(qs, request.user))
    return render(request, 'vendas/dashboard.html', context)


def _indicadores(qs, user):
    """Os números do topo do dashboard, já no recorte de quem está olhando.

    Tudo sai do mesmo queryset filtrado da listagem — o painel e a tabela
    contam a mesma coisa, senão o total do topo brigaria com a soma da tabela.
    """
    ids = list(qs.values_list('id', flat=True))

    produtos = VendaProduto.objects.filter(venda_id__in=ids)
    servicos = VendaServico.objects.filter(venda_id__in=ids)

    receita_produtos = produtos.aggregate(
        t=Sum(F('valor_venda') * F('qtde'), output_field=DecimalField()))['t'] or Decimal('0')
    receita_servicos = servicos.aggregate(t=Sum('valor_plano'))['t'] or Decimal('0')
    total = receita_produtos + receita_servicos

    quantidade = len(ids)
    pecas = produtos.aggregate(n=Sum('qtde'))['n'] or 0

    hoje = timezone.localdate()
    inicio_mes = hoje.replace(day=1)

    # Série dos últimos 30 dias, para o gráfico de barras.
    serie = {}
    for linha in (qs.filter(data_venda__date__gte=hoje - timedelta(days=29))
                  .annotate(dia=TruncDate('data_venda'))
                  .values('dia').annotate(n=Count('id')).order_by('dia')):
        serie[linha['dia']] = linha['n']
    dias_serie = []
    for i in range(29, -1, -1):
        d = hoje - timedelta(days=i)
        dias_serie.append({'dia': d, 'n': serie.get(d, 0)})
    pico = max((x['n'] for x in dias_serie), default=0) or 1
    for x in dias_serie:
        x['altura'] = round(x['n'] / pico * 100)

    def _ranking(campo, rotulo, limite=6):
        linhas = list(qs.exclude(**{f'{campo}__isnull': True})
                      .values(campo, rotulo).annotate(n=Count('id')).order_by('-n')[:limite])
        maior = max((l['n'] for l in linhas), default=0) or 1
        for l in linhas:
            l['fatia'] = round(l['n'] / maior * 100)
            l['nome'] = l.get(rotulo) or '—'
        return linhas

    indicadores = {
        'kpi_total': total,
        'kpi_quantidade': quantidade,
        'kpi_ticket': (total / quantidade) if quantidade else Decimal('0'),
        'kpi_pecas': pecas,
        'kpi_produtos': receita_produtos,
        'kpi_servicos': receita_servicos,
        'kpi_no_mes': qs.filter(data_venda__date__gte=inicio_mes).count(),
        'kpi_hoje': qs.filter(data_venda__date=hoje).count(),
        'kpi_fake': qs.filter(numero_fake=True).count(),
        'kpi_delta': servicos.aggregate(t=Sum('delta'))['t'] or Decimal('0'),
        'kpi_editados': produtos.filter(valor_editado=True).count(),
        'serie_dias': dias_serie,
        'top_produtos': list(produtos.values('nome_produto')
                             .annotate(n=Sum('qtde'),
                                       total=Sum(F('valor_venda') * F('qtde'),
                                                 output_field=DecimalField()))
                             .order_by('-total')[:6]),
        'por_tipo_servico': [{'tipo': dict(VendaServico.TIPOS).get(l['tipo_servico'], l['tipo_servico'] or 'Outro'),
                              'n': l['n'], 'delta': l['delta'] or Decimal('0')}
                             for l in servicos.values('tipo_servico').annotate(n=Count('id'), delta=Sum('delta'))
                             .order_by('-n')],
        'top_servicos': list(servicos.values('servico')
                             .annotate(n=Count('id'), total=Sum('valor_plano'))
                             .order_by('-total')[:6]),
    }
    # O ranking de gente e de loja só faz sentido para quem vê mais de uma.
    if is_superadmin(user):
        indicadores['por_loja'] = _ranking('loja', 'loja__name')
        indicadores['por_vendedor'] = _ranking('vendedor', 'vendedor__first_name')
    return indicadores


@login_required
def venda_detail(request, pk):
    if not can_access_vendas(request.user):
        return _deny(request)
    # O recorte vale aqui também: sem isso, trocar o número na URL abriria a
    # venda de qualquer um.
    venda = get_object_or_404(
        vendas_do_usuario(request.user, Venda.objects.select_related('loja', 'vendedor')
                          .prefetch_related('produtos', 'produtos__editado_por', 'servicos', 'servicos__plano',
                                            'servicos__plano_anterior', 'servicos__servico_adicional')), pk=pk,
    )
    return render(request, 'vendas/venda_detail.html', {
        'venda': venda,
        'is_superadmin': is_superadmin(request.user),
        'pode_gerenciar_precos': pode_gerenciar_precos(request.user),
        'aba': 'vendas',
    })


@login_required
def venda_create(request):
    """[Início da venda.RF001] Nova venda: PDV e vendedor vêm do cadastro do portal.

    A tela manda a venda inteira em JSON; o servidor recalcula e valida tudo
    (slv.registrar_venda) e devolve todos os erros de uma vez.
    """
    if not can_access_vendas(request.user):
        return _deny(request)

    if request.method == 'POST':
        try:
            dados = json.loads(request.body or b'{}')
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({'ok': False, 'erros': ['Envio inválido.']}, status=400)
        loja, vendedor = _pdv_e_vendedor(request, dados)
        try:
            venda = slv.registrar_venda(dados, request.user, loja, vendedor)
        except slv.VendaInvalida as exc:
            return JsonResponse({'ok': False, 'erros': exc.erros})
        messages.success(request, f'Venda #{venda.id} lançada com sucesso.')
        return JsonResponse({'ok': True, 'id': venda.id, 'url': reverse('vendas:venda_detail', args=[venda.id])})

    return render(request, 'vendas/venda_form.html', _venda_form_context(request))


def _pdv_e_vendedor(request, dados):
    """[Segurança.NF001] Vendedor e PDV saem do cadastro — o POST não muda isso.

    O PDV é o setor principal do usuário (é a loja dele no portal). Só o
    superadmin lança em nome de outra loja ou de outro vendedor.
    """
    if is_superadmin(request.user):
        loja = Sector.objects.filter(pk=dados.get('loja_id')).first() if str(dados.get('loja_id') or '').isdigit() else None
        vendedor = (User.objects.filter(pk=dados.get('vendedor_id'), is_active=True).first()
                    if str(dados.get('vendedor_id') or '').isdigit() else None)
        return loja or request.user.sector, vendedor or request.user
    return request.user.sector, request.user


def _lojas_do_vendedor(user):
    """Os PDVs que a pessoa pode escolher: os setores dela.

    Deixar a lista dos 38 setores aberta convidava a lançar venda na loja
    errada — e venda na loja errada é comissão na pessoa errada. O superadmin
    continua vendo todas, porque lança em nome de qualquer loja.
    """
    if is_superadmin(user):
        return Sector.objects.all().order_by('name')
    ids = {s.id for s in user.sectors.all()}
    if user.sector_id:
        ids.add(user.sector_id)
    return Sector.objects.filter(id__in=ids).order_by('name')


def _venda_form_context(request):
    planos = [{'id': p.id, 'nome': p.nome, 'segmentacao': p.segmentacao, 'valor': str(p.valor)}
              for p in Plano.objects.filter(ativo=True)]
    adicionais = [{'id': a.id, 'tipo': a.tipo, 'nome': a.nome, 'valor': str(a.valor)}
                  for a in ServicoAdicional.objects.filter(ativo=True)]
    return {
        'aba': 'nova',
        'is_superadmin': is_superadmin(request.user),
        'pode_gerenciar_precos': pode_gerenciar_precos(request.user),
        'agora': timezone.localtime(),
        'loja': request.user.sector,
        'lojas': _lojas_do_vendedor(request.user),
        'vendedores': User.objects.filter(is_active=True).order_by('first_name', 'last_name') if is_superadmin(request.user) else [],
        'slv': {
            'planos': planos, 'adicionais': adicionais,
            'segmentacoes': SEGMENTACOES, 'tipos_servico': VendaServico.TIPOS,
            'vivo_mais': str(ConfiguracaoVendas.atual().vivo_mais_percentual),
        },
    }


# ---------------------------------------------------------------------------
# Exportação (reproduz os relatórios anexados: produto e serviço analítico)
# ---------------------------------------------------------------------------

_PRODUTO_HEADERS = ['Filial', 'UF', 'Produto', 'Tipo Produto', 'Categoria', 'Subcategoria',
                    'Nº Venda', 'Marca', 'Modelo', 'SKU', 'Serial', 'Cor', 'Nome Cliente',
                    'CPF/CNPJ', 'Vendedor', 'Plano', 'Tabela de preço', 'Data da venda',
                    'Qtde', 'Valor de Venda', 'Pilar']

_SERVICO_HEADERS = ['Filial', 'UF', 'Serviço', 'Serviço Técnico', 'Nº Venda', 'Data',
                    'Vendedor', 'Nome Cliente', 'CPF/CNPJ', 'Plano', 'Tipo do Plano',
                    'Grupamento', 'Nº de Acesso', 'Valor do Plano', 'Receita',
                    'Status do Serviço', 'Pilar']


def _produto_rows(vendas_qs):
    for vp in VendaProduto.objects.filter(venda__in=vendas_qs).select_related('venda', 'venda__loja', 'venda__vendedor'):
        v = vp.venda
        yield [
            v.loja.name if v.loja else v.pdv_nome, v.uf, vp.nome_produto, vp.tipo_produto,
            vp.categoria, vp.subcategoria, v.id, vp.marca, vp.modelo, vp.sku, vp.serial, vp.cor,
            v.cliente_nome, v.cliente_cpf,
            (v.vendedor.get_full_name() if v.vendedor else ''), vp.plano, vp.tabela_preco,
            v.data_venda.strftime('%d/%m/%Y %H:%M') if v.data_venda else '',
            vp.qtde, vp.valor_venda, vp.pilar,
        ]


def _servico_rows(vendas_qs):
    for vs in VendaServico.objects.filter(venda__in=vendas_qs).select_related('venda', 'venda__loja', 'venda__vendedor'):
        v = vs.venda
        yield [
            v.loja.name if v.loja else v.pdv_nome, v.uf, vs.servico, vs.servico_tecnico, v.id,
            v.data_venda.strftime('%d/%m/%Y %H:%M') if v.data_venda else '',
            (v.vendedor.get_full_name() if v.vendedor else ''), v.cliente_nome, v.cliente_cpf,
            vs.plano_novo, vs.tipo_plano, vs.grupamento, vs.numero_acesso,
            vs.valor_plano, vs.receita, vs.status_servico, vs.pilar,
        ]


@login_required
def venda_export(request):
    if not can_access_vendas(request.user):
        return _deny(request)

    qs, _filtros, _qsstr = _filtrar_vendas(request)
    tipo = request.GET.get('tipo', 'produto')
    fmt = request.GET.get('export', 'csv')
    headers = _PRODUTO_HEADERS if tipo == 'produto' else _SERVICO_HEADERS
    rows = _produto_rows(qs) if tipo == 'produto' else _servico_rows(qs)
    stamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    base = f'vendas_{tipo}_{stamp}'

    if fmt == 'xlsx':
        wb = Workbook()
        ws = wb.active
        ws.title = tipo.capitalize()
        header_fill = PatternFill(start_color='6D28D9', end_color='6D28D9', fill_type='solid')
        header_font = Font(color='FFFFFF', bold=True)
        for col, h in enumerate(headers, start=1):
            c = ws.cell(row=1, column=col, value=h)
            c.fill = header_fill
            c.font = header_font
            c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        r = 2
        for row in rows:
            for col, val in enumerate(row, start=1):
                ws.cell(row=r, column=col, value=(float(val) if isinstance(val, Decimal) else val))
            r += 1
        for col in range(1, len(headers) + 1):
            ws.column_dimensions[get_column_letter(col)].width = 20
        ws.freeze_panes = 'A2'
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        resp = HttpResponse(buf.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename="{base}.xlsx"'
        return resp

    # CSV padrão brasileiro (delimitador ';', BOM para o Excel).
    resp = HttpResponse(content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="{base}.csv"'
    resp.write('﻿')
    writer = csv.writer(resp, delimiter=';')
    writer.writerow(headers)
    for row in rows:
        writer.writerow(['' if v is None else v for v in row])
    return resp


# ---------------------------------------------------------------------------
# Tabela de preços
# ---------------------------------------------------------------------------

@login_required
def precos(request):
    if not can_access_vendas(request.user):
        return _deny(request)

    itens = ItemPreco.objects.all()
    categoria = request.GET.get('categoria', '').strip()
    search = request.GET.get('search', '').strip()
    if categoria:
        itens = itens.filter(categoria=categoria)
    if search:
        itens = itens.filter(Q(nome__icontains=search) | Q(plano__icontains=search) | Q(cod_sap__icontains=search))
    itens = itens.order_by('categoria', 'nome')

    paginator = Paginator(itens, 50)
    try:
        page = paginator.page(request.GET.get('page'))
    except PageNotAnInteger:
        page = paginator.page(1)
    except EmptyPage:
        page = paginator.page(paginator.num_pages)

    context = {
        'aba': 'precos',
        'pode_gerenciar_precos': pode_gerenciar_precos(request.user),
        'itens': page,
        'paginator': paginator,
        'categoria': categoria,
        'search': search,
        'categorias': list(ItemPreco.objects.values_list('categoria', flat=True).distinct().order_by('categoria')),
        'total': ItemPreco.objects.count(),
    }
    return render(request, 'vendas/precos_list.html', context)


@login_required
def precos_import(request):
    if not pode_gerenciar_precos(request.user):
        return _deny(request)

    if request.method == 'POST':
        f = request.FILES.get('arquivo')
        if not f or not (f.name or '').lower().endswith(('.xlsx', '.xlsm')):
            messages.error(request, 'Envie a planilha (.xlsx).')
            return redirect('vendas:precos_import')
        registro = ImportacaoPrecos(usuario=request.user, arquivo=f.name[:255])
        try:
            resumo = importar_tabela_precos(f)
            registro.incluidos, registro.alterados = resumo['incluidos'], resumo['alterados']
            registro.sem_mudanca, registro.rejeitados = resumo['sem_mudanca'], resumo['rejeitados'][:500]
            registro.save()
            messages.success(request, f"Importação concluída: {resumo['incluidos']} incluído(s), "
                                      f"{resumo['alterados']} alterado(s), {len(resumo['rejeitados'])} rejeitado(s).")
        except Exception as exc:  # noqa: BLE001 — [Confiabilidade.NF003] a tabela vigente fica como estava
            registro.erro = str(exc)[:2000]
            registro.save()
            messages.error(request, f'Falha ao importar — a tabela vigente não foi alterada: {exc}')
        return redirect('vendas:precos_import')

    return render(request, 'vendas/precos_import.html', {'aba': 'precos', 'pode_gerenciar_precos': True, 'importacoes': ImportacaoPrecos.objects.select_related('usuario')[:15]})


@login_required
def precos_create(request):
    if not pode_gerenciar_precos(request.user):
        return _deny(request)

    if request.method == 'POST':
        nome = request.POST.get('nome', '').strip()
        categoria = request.POST.get('categoria', '').strip()
        if not nome or not categoria:
            messages.error(request, 'Informe categoria e nome.')
        else:
            ItemPreco.objects.create(
                categoria=categoria[:60], nome=nome[:200],
                plano=request.POST.get('plano', '').strip()[:200],
                sistema=request.POST.get('sistema', '').strip()[:60],
                grupamento=request.POST.get('grupamento', '').strip()[:120],
                cod_sap=request.POST.get('cod_sap', '').strip()[:40],
                cod_sistema=request.POST.get('cod_sistema', '').strip()[:40],
                valor=_parse_decimal(request.POST.get('valor')),
            )
            messages.success(request, 'Item de preço cadastrado.')
            return redirect('vendas:precos')

    return render(request, 'vendas/precos_form.html', {
        'aba': 'precos',
        'categorias': list(ItemPreco.objects.values_list('categoria', flat=True).distinct().order_by('categoria')),
    })


@login_required
def cliente_historico(request):
    """JSON com o que este CPF já comprou na rede — do espelho do Vivo GO no Postgres (vendas/vivogo.py)."""
    if not can_access_vendas(request.user):
        return JsonResponse({'disponivel': False, 'erro': 'Acesso restrito.'}, status=403)
    from .models import CompraVivoGo
    cpf = slv.so_digitos(request.GET.get('cpf', ''))
    if len(cpf) not in (11, 14):
        return JsonResponse({'disponivel': True, 'encontrado': False})
    compras = list(CompraVivoGo.objects.filter(cpf=cpf).order_by('-data_venda', '-data_insercao')[:40])
    datas = list(CompraVivoGo.objects.filter(cpf=cpf).order_by('data_venda').values_list('data_venda', flat=True))
    return JsonResponse({
        'disponivel': True, 'encontrado': bool(compras), 'cpf': cpf,
        'nome': compras[0].nome_cliente if compras else '',
        'total': CompraVivoGo.objects.filter(cpf=cpf).count(),
        'primeira': datas[0].strftime('%d/%m/%Y') if datas and datas[0] else '',
        'ultima': datas[-1].strftime('%d/%m/%Y') if datas and datas[-1] else '',
        'compras': [{'data': c.data_venda.strftime('%d/%m/%Y') if c.data_venda else '', 'item': c.item or '—',
                     'tipo': 'PRODUTO' if c.tipo == 'P' else 'SERVIÇO', 'pdv': c.pdv or '—', 'qtde': c.qtd,
                     'valor': float(c.receita or 0), 'vendedor': c.vendedor} for c in compras],
    })


@login_required
def precos_buscar(request):
    """Autocomplete da tabela de preços (JSON) para o formulário de venda."""
    if not can_access_vendas(request.user):
        return HttpResponse(status=403)
    from django.http import JsonResponse

    q = request.GET.get('q', '').strip()
    ids = [int(i) for i in (request.GET.get('ids') or '').split(',') if i.strip().isdigit()][:50]
    if len(q) < 2 and not ids:
        return JsonResponse({'results': []})
    qs = ItemPreco.objects.filter(pk__in=ids) if ids else (
        ItemPreco.objects.filter(ativo=True)
        .exclude(categoria='PLANOS')
        .filter(Q(nome__icontains=q) | Q(plano__icontains=q) | Q(cod_sap__icontains=q)
                | Q(cod_sistema__icontains=q) | Q(**{'extra__ID DPGC__icontains': q}))
        .order_by('categoria', 'nome')[:20]
    )
    segmentacao = request.GET.get('segmentacao', '')
    grupamento = request.GET.get('grupamento', '')
    results = []
    for it in qs:
        sugerido, regra = slv.preco_sugerido(it, segmentacao, grupamento)
        extra = it.extra if isinstance(it.extra, dict) else {}
        results.append({
            'id': it.id, 'categoria': it.categoria, 'nome': it.nome, 'plano': it.plano,
            'sistema': it.sistema, 'grupamento': it.grupamento, 'cod_sap': it.cod_sap,
            'sku': it.cod_sap or it.cod_sistema or extra.get('ID DPGC', ''),
            'valor': (str(it.valor) if it.valor is not None else ''),
            'categoria_slv': slv.categoria_slv(it),
            'valor_sugerido': str(sugerido) if sugerido is not None else '', 'regra': regra,
            'fabricante': extra.get('FABRICANTE', ''),
        })
    return JsonResponse({'results': results})



# ---------------------------------------------------------------------------
# SLV — consultas da tela de venda
# ---------------------------------------------------------------------------
def _cliente_json(cliente):
    return {'cpf': cliente.cpf, 'cpf_formatado': cliente.cpf_formatado, 'nome': cliente.nome,
            'telefone': cliente.telefone, 'telefone_formatado': slv.formatar_telefone(cliente.telefone),
            'cep': cliente.cep, 'logradouro': cliente.logradouro, 'numero': cliente.numero,
            'complemento': cliente.complemento, 'bairro': cliente.bairro, 'cidade': cliente.cidade, 'uf': cliente.uf,
            'plano_id': cliente.plano_id or _plano_pelo_nome(cliente.plano_nome), 'plano_nome': cliente.plano_nome,
            'segmentacao': cliente.segmentacao,
            'valor_pago': str(cliente.valor_pago) if cliente.valor_pago is not None else '',
            'origem': cliente.origem, 'linha': cliente.linha, 'pdv_ultimo': cliente.pdv_ultimo,
            'ultima_compra': cliente.ultima_compra.strftime('%d/%m/%Y') if cliente.ultima_compra else '',
            'qtd_vendas': cliente.qtd_vendas}


def _plano_pelo_nome(nome):
    """O plano do Vivo GO, se houver um plano cadastrado no portal com o mesmo nome."""
    if not nome:
        return None
    return Plano.objects.filter(nome__iexact=nome.strip(), ativo=True).values_list('id', flat=True).first()


@login_required
def api_cliente(request):
    """[Início da venda.RF002/RF004] Busca pelo CPF (qualquer formato) + pré-análise.

    Só o cadastro do portal (Postgres): responde na hora ([Desempenho.NF001]).
    As compras antigas do Vivo GO (MySQL, lento) vêm à parte, por /vendas/cliente/.
    """
    if not can_access_vendas(request.user):
        return JsonResponse({'ok': False, 'erro': 'Acesso restrito.'}, status=403)
    cpf = slv.so_digitos(request.GET.get('cpf'))
    if not slv.cpf_valido(cpf):
        return JsonResponse({'ok': False, 'erro': 'CPF inválido: confira os 11 números.'})
    cliente = Cliente.objects.select_related('plano').filter(cpf=cpf).first()
    if not cliente:
        return JsonResponse({'ok': True, 'encontrado': False, 'cpf': cpf, 'cpf_formatado': slv.formatar_cpf(cpf)})
    analise = slv.pre_analise(cliente)
    return JsonResponse({'ok': True, 'encontrado': True, 'cliente': _cliente_json(cliente), 'pre_analise': {
        'vazio': analise['vazio'],
        'plano': ({**analise['plano'], 'valor': str(analise['plano']['valor'] or ''),
                   'segmentacao_nome': dict(SEGMENTACOES).get(analise['plano']['segmentacao'], '')}
                  if analise['plano'] else None),
        'compras': [{'data': timezone.localtime(c['data']).strftime('%d/%m/%Y'), 'produto': c['produto'],
                     'valor': str(c['valor']), 'venda': c['venda'], 'pdv': c.get('pdv', '')} for c in analise['compras']],
    }})


@login_required
@require_POST
def api_cliente_salvar(request):
    """[Início da venda.RF002 (novo) / RF003] Grava cadastro; o anterior vai para o histórico."""
    if not can_access_vendas(request.user):
        return JsonResponse({'ok': False, 'erro': 'Acesso restrito.'}, status=403)
    try:
        dados = json.loads(request.body or b'{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'erro': 'Envio inválido.'}, status=400)
    cpf = slv.so_digitos(dados.get('cpf'))
    if not slv.cpf_valido(cpf):
        return JsonResponse({'ok': False, 'erro': 'CPF inválido.'})
    if not str(dados.get('nome') or '').strip():
        return JsonResponse({'ok': False, 'erro': 'Informe o nome do cliente.'})
    telefone = slv.so_digitos(dados.get('telefone'))
    if telefone and not slv.telefone_valido(telefone):
        return JsonResponse({'ok': False, 'erro': 'Telefone: use DDD + número.'})
    loja = request.user.sector
    cliente, criado, mudou = slv.salvar_cliente(cpf, dados, request.user, pdv=loja.name if loja else '')
    return JsonResponse({'ok': True, 'criado': criado, 'mudou': mudou, 'cliente': _cliente_json(cliente)})


@login_required
def api_renova(request):
    """Confere o Código do Renova Vini contra os checklists do portal."""
    if not can_access_vendas(request.user):
        return JsonResponse({'ok': False}, status=403)
    renova = slv.renova_existe(request.GET.get('codigo'))
    if not renova:
        return JsonResponse({'ok': False, 'erro': 'Código do Renova não encontrado no portal.'})
    ja_usado = VendaProduto.objects.filter(renova_codigo=renova.codigo).exists()
    return JsonResponse({'ok': True, 'codigo': renova.codigo, 'aparelho': str(getattr(renova, 'aparelho', '') or ''),
                         'ja_usado': ja_usado})


# ---------------------------------------------------------------------------
# SLV — parametrização ([Parametrização.RF001/RF002], [Venda de serviços.RF008])
# ---------------------------------------------------------------------------
@login_required
def parametros(request):
    if not pode_gerenciar_precos(request.user):
        return _deny(request)
    planos = list(Plano.objects.all())
    grupos = [{'chave': k, 'rotulo': r, 'planos': [p for p in planos if p.segmentacao == k]} for k, r in SEGMENTACOES]
    config = ConfiguracaoVendas.atual()
    return render(request, 'vendas/parametros.html', {
        'aba': 'parametros',
        'grupos': grupos,
        'segmentacoes': SEGMENTACOES,
        'config': config,
        'adicionais': ServicoAdicional.objects.all(),
        'historico': RegistroAlteracao.objects.exclude(tipo='CLIENTE').select_related('usuario')[:60],
        'pode_gerenciar_precos': True,
    })


def _voltar_parametros():
    return redirect('vendas:parametros')


@login_required
@require_POST
def parametros_plano(request):
    """Novo plano ou edição; inativar no lugar de excluir."""
    if not pode_gerenciar_precos(request.user):
        return _deny(request)
    plano = Plano.objects.filter(pk=request.POST.get('id')).first() if (request.POST.get('id') or '').isdigit() else None
    nome = (request.POST.get('nome') or '').strip()[:200]
    segmentacao = request.POST.get('segmentacao') or ''
    valor = slv.dinheiro(request.POST.get('valor'))
    ativo = request.POST.get('ativo') == 'on' if plano else True
    if not nome or segmentacao not in dict(SEGMENTACOES) or valor is None or valor <= 0:
        messages.error(request, 'Plano não gravado: informe nome, segmentação e um valor maior que zero.')
        return _voltar_parametros()
    if plano is None:
        plano = Plano.objects.create(nome=nome, segmentacao=segmentacao, valor=valor)
        slv.registrar_alteracoes('PLANO', plano, [('criado', 'Plano criado', '', f'{plano} · R$ {valor}')], request.user)
        messages.success(request, f'Plano {nome} cadastrado.')
        return _voltar_parametros()
    segmentos = dict(SEGMENTACOES)
    mudancas = [('nome', 'Nome', plano.nome, nome),
                ('segmentacao', 'Segmentação', segmentos.get(plano.segmentacao), segmentos.get(segmentacao)),
                ('valor', 'Valor', plano.valor, valor),
                ('ativo', 'Situação', 'ativo' if plano.ativo else 'inativo', 'ativo' if ativo else 'inativo')]
    plano.nome, plano.segmentacao, plano.valor, plano.ativo = nome, segmentacao, valor, ativo
    plano.save()
    n = slv.registrar_alteracoes('PLANO', plano, mudancas, request.user)
    messages.success(request, f'Plano {nome} atualizado.' if n else 'Nada mudou no plano.')
    return _voltar_parametros()


@login_required
@require_POST
def parametros_vivo_mais(request):
    if not pode_gerenciar_precos(request.user):
        return _deny(request)
    percentual = slv.dinheiro((request.POST.get('percentual') or '').replace('%', ''))
    if percentual is None or not (0 <= percentual <= 100):
        messages.error(request, 'O percentual do Vivo+ precisa estar entre 0% e 100%.')
        return _voltar_parametros()
    config = ConfiguracaoVendas.atual()
    antes = config.vivo_mais_percentual
    config.vivo_mais_percentual, config.atualizado_por = percentual, request.user
    config.save()
    slv.registrar_alteracoes('VIVO_MAIS', config, [('vivo_mais_percentual', 'Desconto Vivo+', f'{antes}%', f'{percentual}%')],
                             request.user, rotulo='Vivo+')
    messages.success(request, f'Desconto Vivo+ agora é {percentual}% — vale para as vendas lançadas a partir de agora.')
    return _voltar_parametros()


@login_required
@require_POST
def parametros_adicional(request):
    if not pode_gerenciar_precos(request.user):
        return _deny(request)
    item = (ServicoAdicional.objects.filter(pk=request.POST.get('id')).first()
            if (request.POST.get('id') or '').isdigit() else None)
    nome = (request.POST.get('nome') or '').strip()[:200]
    tipo = request.POST.get('tipo') or ''
    valor = slv.dinheiro(request.POST.get('valor')) or Decimal('0')
    ativo = request.POST.get('ativo') == 'on' if item else True
    if not nome or tipo not in dict(ServicoAdicional.TIPOS) or valor < 0:
        messages.error(request, 'Informe o tipo (Seguro ou SVA) e o nome.')
        return _voltar_parametros()
    if item is None:
        item = ServicoAdicional.objects.create(tipo=tipo, nome=nome, valor=valor)
        slv.registrar_alteracoes('SERVICO_ADICIONAL', item, [('criado', 'Criado', '', str(item))], request.user)
    else:
        mudancas = [('nome', 'Nome', item.nome, nome), ('tipo', 'Tipo', item.tipo, tipo), ('valor', 'Valor', item.valor, valor),
                    ('ativo', 'Situação', 'ativo' if item.ativo else 'inativo', 'ativo' if ativo else 'inativo')]
        item.nome, item.tipo, item.valor, item.ativo = nome, tipo, valor, ativo
        item.save()
        slv.registrar_alteracoes('SERVICO_ADICIONAL', item, mudancas, request.user)
    messages.success(request, f'{item.get_tipo_display()} {nome} gravado.')
    return _voltar_parametros()
