"""Aba Clientes (/vendas/clientes/): a base do Vivo GO espelhada (vendas/vivogo.py) + o cadastro do portal.

Recorte ([Segurança.NF003]): o SUPERADMIN vê a base inteira; o vendedor vê os
clientes que compraram na loja dele (PDV do Vivo GO = loja do cadastro) ou que
foram lançados no portal pela loja dele.
"""
import re
import unicodedata
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import slv, vivogo
from .models import Cliente, CompraVivoGo, RegistroAlteracao, SincronizacaoVivoGo, Venda
from .permissions import can_access_vendas, is_superadmin, pode_gerenciar_precos


def _normal(texto):
    texto = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode().upper()
    texto = re.sub(r'^LOJA\s+', '', texto.strip())
    return ' '.join(re.sub(r'[^A-Z0-9]+', ' ', texto).split())


def pdv_da_loja(loja):
    """O nome do PDV no Vivo GO para a loja do portal ("Loja Norte Sul" → "NORTE SUL")."""
    if not loja:
        return None
    alvo = _normal(loja.name)
    for pdv in CompraVivoGo.objects.values_list('pdv', flat=True).distinct():
        if _normal(pdv) == alvo:
            return pdv
    return None


def clientes_do_usuario(user):
    qs = Cliente.objects.all()
    if is_superadmin(user):
        return qs
    loja = getattr(user, 'sector', None)
    pdv = pdv_da_loja(loja)
    filtro = Q(vendas__loja=loja) if loja else Q(pk__in=[])
    if pdv:
        filtro |= Q(cpf__in=CompraVivoGo.objects.filter(pdv=pdv).values('cpf'))
    return qs.filter(filtro).distinct()


@login_required
def clientes(request):
    if not can_access_vendas(request.user):
        messages.error(request, 'Acesso restrito.')
        return redirect('home')
    base = clientes_do_usuario(request.user)
    qs = base
    q = (request.GET.get('q') or '').strip()
    if q:
        digitos = slv.so_digitos(q)
        filtro = Q(nome__icontains=q)
        if len(digitos) >= 4:
            filtro |= Q(cpf__startswith=digitos) | Q(telefone__contains=digitos) | Q(linha__contains=digitos)
        qs = qs.filter(filtro)
    hoje = timezone.localdate()
    inicio_mes = hoje.replace(day=1)
    situacao = request.GET.get('situacao') or ''
    if situacao == 'novos':
        qs = qs.filter(primeira_compra__gte=inicio_mes)
    elif situacao == 'ativos':
        qs = qs.filter(ultima_compra__gte=hoje - timedelta(days=30))
    elif situacao == 'sumidos':
        qs = qs.filter(ultima_compra__lt=hoje - timedelta(days=180))
    elif situacao == 'recorrentes':
        qs = qs.filter(qtd_vendas__gte=2)
    elif situacao == 'portal':
        qs = qs.filter(origem='PORTAL')
    pdv = request.GET.get('pdv') or ''
    if pdv and is_superadmin(request.user):
        qs = qs.filter(pdv_ultimo=pdv)
    ordem = request.GET.get('ordem') or 'recentes'
    ordens = {'recentes': ['-ultima_compra', 'nome'], 'receita': ['-total_gasto', 'nome'], 'nome': ['nome'],
              'vendas': ['-qtd_vendas', 'nome']}
    qs = qs.order_by(*ordens.get(ordem, ordens['recentes']))
    pagina = Paginator(qs, 30).get_page(request.GET.get('page'))
    parametros = request.GET.copy()
    parametros.pop('page', None)
    sinc = SincronizacaoVivoGo.get()
    return render(request, 'vendas/clientes.html', {
        'aba': 'clientes',
        'is_superadmin': is_superadmin(request.user),
        'pode_gerenciar_precos': pode_gerenciar_precos(request.user),
        'pagina': pagina,
        'filtro': {'q': q, 'situacao': situacao, 'pdv': pdv, 'ordem': ordem},
        'query': parametros.urlencode(),
        'total_base': base.count(),
        'kpi_novos': base.filter(primeira_compra__gte=inicio_mes).count(),
        'kpi_ativos': base.filter(ultima_compra__gte=hoje - timedelta(days=30)).count(),
        'kpi_recorrentes': base.filter(qtd_vendas__gte=2).count(),
        'pdvs': (list(Cliente.objects.exclude(pdv_ultimo='').values_list('pdv_ultimo', flat=True).distinct()
                      .order_by('pdv_ultimo')) if is_superadmin(request.user) else []),
        'sinc': sinc,
        'vivogo_configurado': vivogo.configurado(),
    })


@login_required
def cliente_detalhe(request, cpf):
    if not can_access_vendas(request.user):
        messages.error(request, 'Acesso restrito.')
        return redirect('home')
    cpf = slv.so_digitos(cpf)
    cliente = get_object_or_404(clientes_do_usuario(request.user), cpf=cpf)
    compras = list(CompraVivoGo.objects.filter(cpf=cpf).order_by('-data_venda', '-data_insercao')[:300])
    # Agrupa por venda do Vivo GO: uma venda pode ter produto e serviço.
    vendas_vivogo, indice = [], {}
    for c in compras:
        chave = c.id_venda or c.chave
        if chave not in indice:
            indice[chave] = {'id': c.id_venda, 'data': c.data_venda, 'pdv': c.pdv, 'vendedor': c.vendedor, 'itens': []}
            vendas_vivogo.append(indice[chave])
        indice[chave]['itens'].append(c)
    return render(request, 'vendas/cliente_detalhe.html', {
        'aba': 'clientes',
        'is_superadmin': is_superadmin(request.user),
        'pode_gerenciar_precos': pode_gerenciar_precos(request.user),
        'cliente': cliente,
        'vendas_vivogo': vendas_vivogo,
        'vendas_portal': Venda.objects.filter(cliente=cliente).prefetch_related('produtos', 'servicos')
        .select_related('loja', 'vendedor').order_by('-data_venda')[:50],
        'historico': RegistroAlteracao.objects.filter(tipo='CLIENTE', objeto_id=cliente.pk)[:100],
        'telefone': slv.formatar_telefone(cliente.telefone),
        'linha': slv.formatar_telefone(cliente.linha),
    })


@login_required
@require_POST
def clientes_sincronizar(request):
    """O SUPERADMIN atualiza na hora (o automático roda de 5 em 5 minutos)."""
    if not is_superadmin(request.user):
        raise Http404
    try:
        resumo = vivogo.sincronizar()
        messages.success(request, f'Base do Vivo GO atualizada — {resumo}.')
    except vivogo.VivoGoIndisponivel as exc:
        messages.error(request, f'Não deu para ler o Vivo GO agora: {exc}')
    return redirect('vendas:clientes')
