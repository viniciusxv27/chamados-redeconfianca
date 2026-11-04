"""Despesas recorrentes: a grade por mês, o cadastro, o mês de cada despesa e o Excel."""
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import auditoria, recorrentes as regra, servicos
from .models import DespesaMesTitulo, DespesaRecorrente, PlanoConta, Titulo
from .permissoes import exige, filtrar_por_loja, lojas_permitidas, pode
from .views import Invalido, _contexto_base, _voltar, data, dinheiro, lojas_do

ERROS = (Invalido, servicos.RegraFinanceira, ValueError)


def _quer_fragmento(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _despesas(user):
    return filtrar_por_loja(DespesaRecorrente.objects.select_related('loja', 'plano'), user)


def _competencia(ano, mes):
    if not (2000 <= ano <= 2100 and 1 <= mes <= 12):
        raise Http404('Mês inválido.')
    return date(ano, mes, 1)


def _caminho(request):
    """Para onde voltar: só dentro do Financeiro (o valor vai parar num href da página)."""
    destino = request.GET.get('voltar') or ''
    return destino if destino.startswith('/financeiro/') else reverse('financeiro:recorrentes')


def _livres(request, de, ate):
    return regra.titulos_livres(de, ate, request.user) if pode(request.user, 'pagar.ver') else None


def _filtros(request, hoje):
    g = request.GET
    ano = int(g['ano']) if (g.get('ano') or '').isdigit() and len(g['ano']) == 4 and 2000 <= int(g['ano']) <= 2100 else hoje.year
    if (g.get('mes') or '').isdigit() and 1 <= int(g['mes']) <= 12:
        mes = int(g['mes'])
    else:
        mes = hoje.month if ano == hoje.year else (12 if ano < hoje.year else 1)
    try:
        limite = Decimal((g.get('limite') or '').replace(',', '.').strip() or regra.LIMITE_PADRAO)
    except InvalidOperation:
        limite = regra.LIMITE_PADRAO
    if not limite.is_finite():
        limite = regra.LIMITE_PADRAO
    limite = min(max(limite, Decimal('1')), Decimal('500')).quantize(Decimal('0.1'))
    limite = limite.to_integral_value() if limite == limite.to_integral_value() else limite
    return {'ano': ano, 'mes': mes, 'limite': limite, 'tipo': (g.get('tipo') or '').strip(),
            'loja': (g.get('loja') or '').strip(), 'q': (g.get('q') or '').strip(),
            'so': g.get('so') if g.get('so') in ('alerta', 'atraso') else '',
            'inativas': g.get('inativas') == '1'}


def _filtrar(qs, f):
    if not f['inativas']:
        qs = qs.filter(ativa=True)
    if f['tipo']:
        qs = qs.filter(tipo=f['tipo'])
    if f['loja'].isdigit():
        qs = qs.filter(loja_id=int(f['loja']))
    elif f['loja'] == 'sem':
        qs = qs.filter(loja__isnull=True)
    if f['q']:
        qs = qs.filter(Q(nome__icontains=f['q']) | Q(fornecedor__icontains=f['q']))
    return qs


def _query(f, *sem):
    """A busca atual, sem as chaves pedidas (para montar os links)."""
    valores = {'ano': f['ano'], 'mes': f['mes'], 'tipo': f['tipo'], 'loja': f['loja'], 'q': f['q'], 'so': f['so'],
               'inativas': '1' if f['inativas'] else '', 'limite': f['limite'] if f['limite'] != regra.LIMITE_PADRAO else ''}
    return urlencode({k: v for k, v in valores.items() if v not in ('', None) and k not in sem})


def _grade(request, f, hoje, sugerir=True):
    livres = None
    if sugerir:
        livres = _livres(request, date(f['ano'], 1, 1), date(f['ano'], 12, 31))
    return regra.montar_grade(_filtrar(_despesas(request.user), f), f['ano'], f['mes'], hoje, limite=f['limite'],
                              livres=livres, so=f['so'])


def _tipos(user):
    existentes = _despesas(user).order_by('tipo').values_list('tipo', flat=True).distinct()
    return list(existentes)


def _formulario(request):
    usados = list(DespesaRecorrente.objects.order_by('tipo').values_list('tipo', flat=True).distinct())
    chaves = {regra.chave_texto(t) for t in usados}
    return {'lojas': lojas_do(request.user),
            'planos': PlanoConta.objects.filter(ativa=True, analitica=True, tipo='DESPESA'),
            'tipos_lista': sorted(usados + [t for t in regra.TIPOS_SUGERIDOS if regra.chave_texto(t) not in chaves],
                                  key=regra.chave_texto)}


# ── A grade ─────────────────────────────────────────────────────────────────
@login_required
@exige('recorrentes.ver')
def recorrentes(request):
    hoje = timezone.localdate()
    f = _filtros(request, hoje)
    grade = _grade(request, f, hoje)
    contexto = _contexto_base(request, 'recorrentes')
    contexto.update(_formulario(request))
    contexto.update({
        'grade': grade, 'filtro': f, 'hoje': hoje, 'tipos': _tipos(request.user),
        'mes_rotulo': f'{regra.MESES_CURTOS[f["mes"] - 1]}/{f["ano"]}',
        'mes_anterior_rotulo': regra.MESES_CURTOS[f['mes'] - 2] if f['mes'] > 1 else f'Dez/{f["ano"] - 1}',
        'q_tudo': _query(f), 'q_sem_tipo': _query(f, 'tipo'), 'q_sem_ano': _query(f, 'ano', 'mes'), 'q_sem_mes': _query(f, 'mes'),
        'q_sem_so': _query(f, 'so'), 'ano_anterior': f['ano'] - 1, 'ano_seguinte': f['ano'] + 1,
        'legenda': [(k, v[0]) for k, v in regra.SITUACOES.items() if k != 'FORA'],
        'atencao': grade.atencao[:30], 'atencao_resto': max(len(grade.atencao) - 30, 0),
        'pode_editar': pode(request.user, 'recorrentes.editar'),
        'pode_vincular': pode(request.user, 'recorrentes.editar') and pode(request.user, 'pagar.ver'),
        'caminho': request.get_full_path(), 'lojas_restritas': lojas_permitidas(request.user) is not None,
        'hoje_mes': hoje.replace(day=1),
    })
    return render(request, 'financeiro/recorrentes.html', contexto)


@login_required
@exige('recorrentes.ver')
def excel(request):
    hoje = timezone.localdate()
    f = _filtros(request, hoje)
    grade = _grade(request, f, hoje, sugerir=False)
    auditoria.registrar(request, 'EXPORTAR', f'Exportou a grade de despesas recorrentes de {f["ano"]} '
                        f'({len(grade.linhas)} despesa(s))', area='recorrentes')
    resposta = HttpResponse(regra.planilha(grade),
                            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resposta['Content-Disposition'] = f'attachment; filename="despesas_recorrentes_{f["ano"]}.xlsx"'
    return resposta


# ── Cadastro ────────────────────────────────────────────────────────────────
def _ler_despesa(request):
    p = request.POST
    inicio = (p.get('inicio') or '').strip()
    campos = {
        'nome': (p.get('nome') or '').strip()[:120],
        'tipo': (p.get('tipo') or '').strip(),
        'fornecedor': (p.get('fornecedor') or '').strip()[:200],
        'fornecedor_documento': ''.join(c for c in (p.get('fornecedor_documento') or '') if c.isdigit())[:14],
        'termo_titulo': (p.get('termo_titulo') or '').strip()[:80],
        'valor_esperado': dinheiro(p.get('valor_esperado'), obrigatorio=True, campo='valor esperado'),
        'inicio': data(inicio + '-01' if len(inicio) == 7 else inicio, campo='mês de início'),
        'ativa': p.get('ativa', 'on') == 'on',
        'observacao': (p.get('observacao') or '').strip(),
    }
    dia = (p.get('dia_vencimento') or '').strip()
    if not dia.isdigit():
        raise Invalido('Informe o dia do vencimento.')
    campos['dia_vencimento'] = int(dia)
    campos['plano'] = None
    if (p.get('plano') or '').isdigit():
        campos['plano'] = PlanoConta.objects.filter(pk=int(p['plano']), analitica=True, ativa=True).first()
        if campos['plano'] is None:
            raise Invalido('Escolha uma conta analítica ativa do plano de contas.')
    campos['loja'] = None
    if (p.get('loja') or '').isdigit():
        campos['loja'] = lojas_do(request.user).filter(pk=int(p['loja'])).first()
        if campos['loja'] is None:
            raise Invalido('Loja fora das suas lojas liberadas.')
    elif lojas_permitidas(request.user) is not None:
        raise Invalido('Escolha a loja da despesa.')
    return campos


@login_required
@exige('recorrentes.editar')
@require_POST
def despesa_nova(request):
    try:
        despesa = servicos.criar_despesa(_ler_despesa(request), usuario=request.user, request=request)
        messages.success(request, f'Despesa cadastrada: {despesa.nome}.')
    except ERROS as exc:
        messages.error(request, str(exc))
    return _voltar(request, 'financeiro:recorrentes')


@login_required
@exige('recorrentes.editar')
def despesa_editar(request, pk):
    despesa = get_object_or_404(_despesas(request.user), pk=pk)
    if request.method == 'POST':
        try:
            servicos.editar_despesa(despesa, _ler_despesa(request), usuario=request.user, request=request)
            messages.success(request, f'Despesa salva: {request.POST.get("nome", "").strip()}.')
        except ERROS as exc:
            messages.error(request, str(exc))
        return _voltar(request, 'financeiro:recorrentes')
    contexto = _contexto_base(request, 'recorrentes')
    contexto.update(_formulario(request))
    contexto.update({'d': despesa, 'caminho': _caminho(request),
                     'hoje_mes': timezone.localdate().replace(day=1)})
    modelo = 'financeiro/_recorrente_form.html' if _quer_fragmento(request) else 'financeiro/recorrente_pagina.html'
    contexto.update({'fragmento': 'financeiro/_recorrente_form.html', 'em_pagina': not _quer_fragmento(request)})
    return render(request, modelo, contexto)


@login_required
@exige('recorrentes.editar')
@require_POST
def despesa_excluir(request, pk):
    despesa = get_object_or_404(_despesas(request.user), pk=pk)
    try:
        nome = despesa.nome
        servicos.excluir_despesa(despesa, usuario=request.user, request=request)
        messages.success(request, f'Despesa excluída: {nome}.')
    except ERROS as exc:
        messages.error(request, str(exc))
    return _voltar(request, 'financeiro:recorrentes')


# ── O mês de uma despesa ────────────────────────────────────────────────────
def _celula(despesa, competencia, hoje, limite):
    """A célula com a variação calculada a partir do histórico da própria despesa."""
    registros = regra.carregar_registros([despesa])[despesa.id]
    de = min([regra.primeiro_dia(despesa.inicio), competencia, regra.somar_meses(competencia, -regra.JANELA_MEDIA)]
             + list(registros))
    celulas = [regra.montar_celula(despesa, m, registros.get(m), hoje) for m in regra._meses(de, competencia)]
    regra.calcular_variacoes(celulas, limite)
    return celulas[-1], [c for c in celulas[:-1] if c.valor_real is not None][-regra.JANELA_MEDIA:]


def _salvar_mes(request, despesa, competencia):
    p = request.POST
    tem_titulo = DespesaMesTitulo.objects.filter(mes__despesa=despesa, mes__competencia=competencia).exclude(
        titulo__status='CANCELADO').exists()
    def valor_de(nome, rotulo):
        return dinheiro(p.get(nome), campo=rotulo) if (p.get(nome) or '').strip() else None
    if tem_titulo:
        servicos.salvar_mes_despesa(despesa, competencia, observacao=p.get('observacao'), usuario=request.user, request=request)
        return
    if p.get('limpar'):
        servicos.salvar_mes_despesa(despesa, competencia, usuario=request.user, request=request)
        return
    servicos.salvar_mes_despesa(
        despesa, competencia, valor=valor_de('valor', 'valor da conta'),
        vencimento=data(p.get('vencimento'), obrigatorio=False),
        pago_em=data(p.get('pago_em'), obrigatorio=False), valor_pago=valor_de('valor_pago', 'valor pago'),
        sem_cobranca=p.get('sem_cobranca') == 'on', observacao=p.get('observacao'),
        usuario=request.user, request=request)


@login_required
@exige('recorrentes.ver')
def mes_detalhe(request, pk, ano, mes):
    despesa = get_object_or_404(_despesas(request.user), pk=pk)
    competencia = _competencia(ano, mes)
    if request.method == 'POST':
        if not pode(request.user, 'recorrentes.editar'):
            messages.error(request, 'Anotar nas despesas recorrentes não está liberado para o seu usuário.')
            return redirect('financeiro:recorrentes')
        try:
            _salvar_mes(request, despesa, competencia)
            messages.success(request, f'{despesa.nome}: {competencia:%m/%Y} salvo.')
        except ERROS as exc:
            messages.error(request, str(exc))
        return _voltar(request, 'financeiro:recorrentes')

    hoje = timezone.localdate()
    try:
        limite = Decimal((request.GET.get('limite') or '').replace(',', '.') or regra.LIMITE_PADRAO)
        limite = limite if limite.is_finite() and limite >= 1 else regra.LIMITE_PADRAO
    except InvalidOperation:
        limite = regra.LIMITE_PADRAO
    celula, historico = _celula(despesa, competencia, hoje, limite)
    pode_editar = pode(request.user, 'recorrentes.editar')
    ve_pagar = pode(request.user, 'pagar.ver')
    pode_vincular = pode_editar and ve_pagar
    candidatos, achados, busca = [], [], (request.GET.get('buscar') or '').strip()
    if pode_vincular:
        fim = regra.somar_meses(competencia, 1)
        livres = filtrar_por_loja(Titulo.objects.filter(tipo='PAGAR', vinculo_recorrente__isnull=True), request.user) \
            .exclude(status='CANCELADO').select_related('plano', 'loja')
        candidatos = regra.candidatos_do_mes(despesa, list(livres.filter(vencimento__gte=competencia, vencimento__lt=fim)))[:12]
        if busca:
            filtro = Q(descricao__icontains=busca) | Q(pessoa_nome__icontains=busca) | Q(numero_documento__icontains=busca)
            digitos = ''.join(c for c in busca if c.isdigit())
            if len(digitos) >= 4:
                filtro |= Q(pessoa_documento__contains=digitos)
            achados = list(livres.filter(filtro, vencimento__gte=regra.somar_meses(competencia, -2),
                                         vencimento__lt=regra.somar_meses(competencia, 3)).order_by('vencimento', 'id')[:20])
    vinculos = list(DespesaMesTitulo.objects.filter(mes__despesa=despesa, mes__competencia=competencia)
                    .select_related('titulo', 'vinculado_por').order_by('titulo__vencimento'))
    contexto = _contexto_base(request, 'recorrentes')
    contexto.update({
        'd': despesa, 'c': celula, 'historico': historico, 'vinculos': vinculos, 'candidatos': candidatos,
        'achados': achados, 'busca': busca, 'pode_editar': pode_editar, 'pode_vincular': pode_vincular, 've_pagar': ve_pagar,
        'pode_lancar': pode_editar and pode(request.user, 'pagar.editar') and not celula.titulos and celula.situacao != 'PAGO',
        'tem_titulo': bool(celula.titulos), 'registro': celula.registro, 'competencia': competencia,
        'caminho': _caminho(request),
        'esperado_diverge': (celula.valor_real is not None and celula.valor_real != despesa.valor_esperado),
        'diferenca_esperado': (celula.valor_real - despesa.valor_esperado) if celula.valor_real is not None else None,
    })
    contexto.update({'fragmento': 'financeiro/_recorrente_mes.html', 'em_pagina': not _quer_fragmento(request)})
    modelo = 'financeiro/_recorrente_mes.html' if _quer_fragmento(request) else 'financeiro/recorrente_pagina.html'
    return render(request, modelo, contexto)


@login_required
@exige('recorrentes.editar')
@require_POST
def vincular(request, pk, ano, mes):
    despesa = get_object_or_404(_despesas(request.user), pk=pk)
    competencia = _competencia(ano, mes)
    if not pode(request.user, 'pagar.ver'):
        messages.error(request, 'Vincular título precisa de acesso a Contas a pagar.')
        return _voltar(request, 'financeiro:recorrentes')
    titulo_id = request.POST.get('titulo') or ''
    titulo = filtrar_por_loja(Titulo.objects.all(), request.user).filter(pk=int(titulo_id)).first() if titulo_id.isdigit() else None
    if titulo is None:
        messages.error(request, 'Título não encontrado nas suas lojas.')
        return _voltar(request, 'financeiro:recorrentes')
    try:
        servicos.vincular_titulo(despesa, competencia, titulo, usuario=request.user, request=request)
        messages.success(request, f'Título vinculado a {despesa.nome} ({competencia:%m/%Y}).')
    except ERROS as exc:
        messages.error(request, str(exc))
    return _voltar(request, 'financeiro:recorrentes')


@login_required
@exige('recorrentes.editar')
@require_POST
def desvincular(request, pk):
    vinculo = get_object_or_404(DespesaMesTitulo.objects.filter(mes__despesa__in=_despesas(request.user)), pk=pk)
    servicos.desvincular_titulo(vinculo, usuario=request.user, request=request)
    messages.success(request, 'Título desvinculado. Ele continua em Contas a Pagar.')
    return _voltar(request, 'financeiro:recorrentes')


@login_required
@exige('recorrentes.editar')
@require_POST
def vincular_sugestoes(request):
    """Liga de uma vez os títulos que não deixam dúvida, no filtro que está na tela."""
    if not pode(request.user, 'pagar.ver'):
        messages.error(request, 'Vincular título precisa de acesso a Contas a pagar.')
        return _voltar(request, 'financeiro:recorrentes')
    hoje = timezone.localdate()
    f = _filtros(request, hoje)
    grade = _grade(request, f, hoje)
    feitos = erros = 0
    for c in grade.sugestoes:
        try:
            servicos.vincular_titulo(c.despesa, c.competencia, c.sugestao, usuario=request.user, request=request)
            feitos += 1
        except servicos.RegraFinanceira:
            erros += 1
    messages.success(request, f'{feitos} título(s) vinculado(s).' + (f' {erros} ficaram de fora: abra o mês para ver.' if erros else ''))
    return _voltar(request, 'financeiro:recorrentes')


@login_required
@exige('recorrentes.editar')
@require_POST
def lancar(request, pk, ano, mes):
    """Abre a conta a pagar do mês, com o clique de alguém, e já a deixa vinculada."""
    despesa = get_object_or_404(_despesas(request.user), pk=pk)
    competencia = _competencia(ano, mes)
    if not pode(request.user, 'pagar.editar'):
        messages.error(request, 'Lançar em Contas a Pagar não está liberado para o seu usuário.')
        return _voltar(request, 'financeiro:recorrentes')
    try:
        titulo = servicos.lancar_titulo_da_despesa(
            despesa, competencia, dinheiro(request.POST.get('valor'), obrigatorio=True),
            data(request.POST.get('vencimento'), campo='data de vencimento'), usuario=request.user, request=request)
        messages.success(request, f'Conta a pagar lançada e vinculada: {titulo.descricao}.')
    except ERROS as exc:
        messages.error(request, str(exc))
    return _voltar(request, 'financeiro:recorrentes')
