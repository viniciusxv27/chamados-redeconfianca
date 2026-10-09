"""Aba Sangrias da Contagem de Caixa: registrar, conferir e acompanhar os gastos."""
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from users.module_access import fechado_para_padrao

from . import sangrias as svc
from .models import CategoriaSangria, Sangria
from .permissions import e_gestor as _e_gestor
from .permissions import lojas_do_usuario as _lojas_do_usuario
from .views import ValorInvalido, _para_decimal

TAMANHO_MAXIMO_COMPROVANTE = 10 * 1024 * 1024


def _filtros(request, lojas):
    hoje = timezone.localdate()
    inicio = parse_date(request.GET.get('de') or '') or hoje.replace(day=1)
    fim = parse_date(request.GET.get('ate') or '') or hoje
    if inicio > fim:
        inicio, fim = fim, inicio
    loja = request.GET.get('loja') or ''
    categoria = request.GET.get('categoria') or ''
    situacao = request.GET.get('situacao') or ''
    return {
        'inicio': inicio, 'fim': fim,
        'loja': loja if loja.isdigit() and lojas.filter(id=int(loja)).exists() else '',
        'categoria': categoria if categoria.isdigit() else '',
        'situacao': situacao if situacao in ('a_conferir', 'conferidas', 'sem_comprovante') else '',
        'q': (request.GET.get('q') or '').strip(),
    }


def _consulta(f, lojas):
    qs = Sangria.objects.filter(loja__in=lojas, data__gte=f['inicio'], data__lte=f['fim'])
    if f['loja']:
        qs = qs.filter(loja_id=int(f['loja']))
    if f['categoria']:
        qs = qs.filter(categoria_id=int(f['categoria']))
    if f['situacao'] == 'a_conferir':
        qs = qs.filter(conferida=False)
    elif f['situacao'] == 'conferidas':
        qs = qs.filter(conferida=True)
    elif f['situacao'] == 'sem_comprovante':
        qs = qs.filter(Q(comprovante='') | Q(comprovante__isnull=True))
    if f['q']:
        qs = qs.filter(Q(descricao__icontains=f['q']) | Q(favorecido__icontains=f['q']))
    return qs


def _atalhos(hoje):
    inicio_mes = hoje.replace(day=1)
    fim_mes_passado = inicio_mes - timedelta(days=1)
    return [
        ('Hoje', hoje, hoje),
        ('Este mês', inicio_mes, hoje),
        ('Mês passado', fim_mes_passado.replace(day=1), fim_mes_passado),
        ('90 dias', hoje - timedelta(days=90), hoje),
        ('Este ano', hoje.replace(month=1, day=1), hoje),
    ]


@login_required
@fechado_para_padrao('caixa')
def sangrias(request):
    lojas = _lojas_do_usuario(request.user)
    if not lojas.exists():
        messages.error(request, 'Você não tem loja no caixa para registrar sangrias.')
        return redirect('contagem_caixa:dashboard')

    f = _filtros(request, lojas)
    qs = _consulta(f, lojas)
    numeros = svc.resumo(qs)
    gestor = _e_gestor(request.user)
    lista = list(qs.select_related('loja', 'categoria', 'registrada_por', 'conferida_por')
                 .order_by('-data', '-criada_em')[:500])
    for s in lista:
        s.pode_alterar = svc.pode_alterar(request.user, s)
    hoje = timezone.localdate()

    parametros = request.GET.copy()
    return render(request, 'contagem_caixa/sangrias.html', {
        'aba': 'sangrias',
        'e_gestor': gestor,
        'lojas': lojas,
        'categorias': CategoriaSangria.objects.filter(ativa=True),
        'todas_categorias': CategoriaSangria.objects.all() if gestor else None,
        'filtro': f,
        'filtro_ativo': any(f[k] for k in ('loja', 'categoria', 'situacao', 'q')),
        'atalhos': _atalhos(hoje),
        'hoje': hoje,
        'numeros': numeros,
        'por_categoria': svc.por_categoria(qs, numeros['total']),
        'por_loja': svc.por_loja(qs, numeros['total']) if lojas.count() > 1 else [],
        'por_mes': svc.por_mes(qs),
        'recorrentes': svc.recorrentes(qs),
        'fora_do_padrao': svc.fora_do_padrao(qs),
        'sangrias': lista,
        'cortada': numeros['quantidade'] > len(lista),
        'query': parametros.urlencode(),
        'loja_padrao': (request.user.sector_id if lojas.filter(id=request.user.sector_id or 0).exists()
                        else (lojas.first().id if lojas.count() == 1 else '')),
    })


def _ler_formulario(request, lojas, sangria=None):
    """Valida o formulário. Devolve (dados, erro)."""
    loja = lojas.filter(id=request.POST.get('loja') or 0).first()
    if loja is None:
        return None, 'Escolha uma loja em que você tem acesso ao caixa.'
    data = parse_date(request.POST.get('data') or '')
    if data is None:
        return None, 'Informe a data da sangria.'
    if data > timezone.localdate():
        return None, 'A data da sangria não pode ser no futuro.'
    try:
        valor = _para_decimal(request.POST.get('valor'))
    except ValorInvalido:
        return None, f'"{request.POST.get("valor")}" não é um valor válido.'
    if valor <= 0:
        return None, 'Informe o valor retirado do caixa.'
    categoria = CategoriaSangria.objects.filter(id=request.POST.get('categoria') or 0).first()
    if categoria is None or (not categoria.ativa and (sangria is None or sangria.categoria_id != categoria.id)):
        return None, 'Escolha a categoria do gasto.'
    descricao = (request.POST.get('descricao') or '').strip()
    if not descricao:
        return None, 'Descreva o gasto: o que foi comprado ou pago.'
    arquivo = request.FILES.get('comprovante')
    if arquivo and arquivo.size > TAMANHO_MAXIMO_COMPROVANTE:
        return None, 'O comprovante passa de 10 MB.'
    return {'loja': loja, 'data': data, 'valor': valor, 'categoria': categoria,
            'descricao': descricao[:2000], 'favorecido': (request.POST.get('favorecido') or '').strip()[:120],
            'comprovante': arquivo}, ''


def _voltar(request):
    destino = request.POST.get('voltar') or ''
    return redirect(destino if destino.startswith('/contagem-caixa/') else reverse('contagem_caixa:sangrias'))


@login_required
@fechado_para_padrao('caixa')
@require_POST
def sangria_registrar(request):
    lojas = _lojas_do_usuario(request.user)
    dados, erro = _ler_formulario(request, lojas)
    if erro:
        messages.error(request, erro)
        return _voltar(request)
    arquivo = dados.pop('comprovante')
    with transaction.atomic():
        sangria = Sangria(registrada_por=request.user, **dados)
        if arquivo:
            sangria.comprovante = arquivo
        sangria.save()
        svc.sincronizar_dia(sangria.loja_id, sangria.data)
    messages.success(request, f'Sangria de R$ {sangria.valor} registrada em {sangria.loja.name} '
                              f'({sangria.data:%d/%m}) — já descontada do caixa do dia.')
    return _voltar(request)


@login_required
@fechado_para_padrao('caixa')
@require_POST
def sangria_editar(request, pk):
    sangria = get_object_or_404(Sangria, pk=pk)
    if not svc.pode_alterar(request.user, sangria):
        messages.error(request, 'Esta sangria não pode mais ser alterada por você.')
        return _voltar(request)
    dados, erro = _ler_formulario(request, _lojas_do_usuario(request.user), sangria)
    if erro:
        messages.error(request, erro)
        return _voltar(request)
    antes = (sangria.loja_id, sangria.data)
    arquivo = dados.pop('comprovante')
    with transaction.atomic():
        for campo, valor in dados.items():
            setattr(sangria, campo, valor)
        if arquivo:
            sangria.comprovante = arquivo
        if sangria.conferida and not _e_gestor(request.user):
            sangria.conferida = False
        sangria.save()
        # Mudou de dia ou de loja: os dois caixas precisam ser refeitos.
        if antes != (sangria.loja_id, sangria.data):
            svc.sincronizar_dia(*antes)
        svc.sincronizar_dia(sangria.loja_id, sangria.data)
    messages.success(request, 'Sangria atualizada e caixa do dia recalculado.')
    return _voltar(request)


@login_required
@fechado_para_padrao('caixa')
@require_POST
def sangria_apagar(request, pk):
    sangria = get_object_or_404(Sangria, pk=pk)
    if not svc.pode_alterar(request.user, sangria):
        messages.error(request, 'Esta sangria não pode mais ser apagada por você.')
        return _voltar(request)
    loja_id, data = sangria.loja_id, sangria.data
    with transaction.atomic():
        sangria.delete()
        svc.sincronizar_dia(loja_id, data)
    messages.success(request, 'Sangria apagada e caixa do dia recalculado.')
    return _voltar(request)


@login_required
@fechado_para_padrao('caixa')
@require_POST
def sangria_conferir(request):
    """Marca (ou desmarca) uma ou várias sangrias como conferidas. Só o gestor."""
    if not svc.pode_conferir(request.user):
        messages.error(request, 'Só o gestor do caixa confere sangrias.')
        return _voltar(request)
    ids = [int(i) for i in request.POST.getlist('sangria') if str(i).isdigit()]
    conferir = (request.POST.get('acao') or 'conferir') == 'conferir'
    qs = Sangria.objects.filter(id__in=ids, loja__in=_lojas_do_usuario(request.user))
    if conferir:
        n = qs.filter(conferida=False).update(
            conferida=True, conferida_por=request.user, conferida_em=timezone.now(),
            observacao_conferencia=(request.POST.get('observacao') or '').strip()[:255])
        messages.success(request, f'{n} sangria(s) conferida(s).' if n else 'Nada novo para conferir.')
    else:
        n = qs.update(conferida=False, conferida_por=None, conferida_em=None)
        messages.success(request, f'{n} sangria(s) voltaram para "a conferir".')
    return _voltar(request)


@login_required
@fechado_para_padrao('caixa')
@require_POST
def sangria_categorias(request):
    """O gestor cria, renomeia, ativa e desativa categorias."""
    if not _e_gestor(request.user):
        messages.error(request, 'Só o gestor do caixa mexe nas categorias.')
        return _voltar(request)
    nova = (request.POST.get('nova') or '').strip()[:60]
    if nova:
        _, criada = CategoriaSangria.objects.get_or_create(
            nome=nova, defaults={'ordem': CategoriaSangria.objects.count()})
        messages.success(request, f'Categoria "{nova}" criada.' if criada else f'"{nova}" já existe.')
    for categoria in CategoriaSangria.objects.all():
        nome = (request.POST.get(f'nome_{categoria.id}') or '').strip()[:60]
        if f'nome_{categoria.id}' not in request.POST:
            continue
        ativa = request.POST.get(f'ativa_{categoria.id}') == 'on'
        if nome and (nome != categoria.nome or ativa != categoria.ativa):
            if CategoriaSangria.objects.filter(nome=nome).exclude(id=categoria.id).exists():
                messages.error(request, f'Já existe uma categoria chamada "{nome}".')
                continue
            categoria.nome, categoria.ativa = nome, ativa
            categoria.save(update_fields=['nome', 'ativa'])
    return _voltar(request)


@login_required
@fechado_para_padrao('caixa')
def sangrias_exportar(request):
    from .exportacao_sangrias import planilha_sangrias

    lojas = _lojas_do_usuario(request.user)
    f = _filtros(request, lojas)
    qs = _consulta(f, lojas)
    numeros = svc.resumo(qs)
    return planilha_sangrias(
        f, list(qs.select_related('loja', 'categoria', 'registrada_por', 'conferida_por')
                .order_by('data', 'loja__name')),
        numeros, svc.por_categoria(qs, numeros['total']), svc.por_loja(qs, numeros['total']),
        svc.recorrentes(qs), svc.fora_do_padrao(qs))
