"""Telas do Financeiro: painel, caixa das lojas, títulos e baixas."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from contagem_caixa.permissions import lojas as todas_as_lojas

from . import servicos
from .models import (Baixa, ContaBancaria, ExtratoLinha, LogFinanceiro, PlanoConta, RecebivelCartao, Titulo)
from .permissoes import AREAS, e_superadmin, exige, filtrar_por_loja, lojas_permitidas, pode, ve_loja

ZERO = Decimal('0.00')
TAMANHO_MAXIMO = 15 * 1024 * 1024
EXTENSOES = ('.pdf', '.jpg', '.jpeg', '.png', '.webp', '.xml')
TIPO_DA_URL = {'pagar': 'PAGAR', 'receber': 'RECEBER'}


# ── Apoio ───────────────────────────────────────────────────────────────────
class Invalido(Exception):
    pass


def dinheiro(texto, obrigatorio=False, campo='valor'):
    bruto = (texto or '').strip().replace('R$', '').replace(' ', '')
    if not bruto:
        if obrigatorio:
            raise Invalido(f'Informe o {campo}.')
        return ZERO
    if ',' in bruto:
        bruto = bruto.replace('.', '').replace(',', '.')
    try:
        return Decimal(bruto).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        raise Invalido(f'O {campo} "{texto}" não é um número.')


def data(texto, obrigatorio=True, campo='data'):
    valor = parse_date((texto or '').strip())
    if valor is None and (texto or '').count('/') == 2:
        d, m, a = texto.strip().split('/')
        try:
            valor = parse_date(f'{a}-{m.zfill(2)}-{d.zfill(2)}')
        except ValueError:
            valor = None
    if valor is None and obrigatorio:
        raise Invalido(f'Informe a {campo} (dd/mm/aaaa).')
    return valor


def arquivo_ok(arquivo):
    if not arquivo:
        return None
    if arquivo.size > TAMANHO_MAXIMO:
        raise Invalido('O arquivo passa de 15 MB.')
    if not arquivo.name.lower().endswith(EXTENSOES):
        raise Invalido('Anexo aceito: PDF, imagem ou XML.')
    return arquivo


def lojas_do(user):
    ids = lojas_permitidas(user)
    qs = todas_as_lojas()
    return qs if ids is None else qs.filter(id__in=ids)


def _voltar(request, padrao):
    destino = request.POST.get('voltar') or request.GET.get('voltar') or ''
    if destino.startswith('/financeiro/'):
        return redirect(destino)
    return redirect(padrao)


def _contexto_base(request, aba):
    user = request.user
    menu = [(chave, rotulo, icone) for chave, rotulo, icone, _d, _a in AREAS
            if chave != 'baixas' and pode(user, chave)]
    return {'aba': aba, 'menu_fin': menu, 'e_superadmin': e_superadmin(user), 'pode': {c.replace('.', '_'): pode(user, c) for c in (
        'pagar.ver', 'pagar.editar', 'pagar.cancelar', 'receber.ver', 'receber.editar', 'receber.cancelar',
        'baixas.baixar', 'baixas.estornar', 'bancos.ver', 'bancos.editar', 'bancos.lancar', 'conciliacao.ver',
        'conciliacao.conciliar', 'cartoes.ver', 'cartoes.conciliar', 'plano.ver', 'plano.editar', 'auditoria.ver',
        'caixa.ver')}, 'pode_baixas': pode(user, 'baixas')}


def _formulario_basico(request, tipo=None):
    return {
        'lojas': lojas_do(request.user),
        'contas': ContaBancaria.objects.filter(ativa=True),
        'planos': PlanoConta.objects.filter(ativa=True, analitica=True,
                                            **({'tipo': 'DESPESA' if tipo == 'PAGAR' else 'RECEITA'} if tipo else {})),
        'formas': Titulo.FORMAS,
        'hoje': timezone.localdate(),
    }


def _titulos_visiveis(user):
    qs = Titulo.objects.all()
    tipos = [t for t, area in (('PAGAR', 'pagar'), ('RECEBER', 'receber')) if pode(user, area)]
    return filtrar_por_loja(qs.filter(tipo__in=tipos), user)


def _titulo_ou_404(request, pk):
    titulo = get_object_or_404(_titulos_visiveis(request.user), pk=pk)
    return titulo


# ── Painel ──────────────────────────────────────────────────────────────────
@login_required
@exige()
def painel(request):
    hoje = timezone.localdate()
    semana = hoje + timedelta(days=7)
    user = request.user
    titulos = _titulos_visiveis(user).filter(status__in=('ABERTO', 'PARCIAL'))

    def resumo(tipo):
        qs = titulos.filter(tipo=tipo)
        def soma(q):
            agg = q.aggregate(v=Sum('valor'), b=Sum('valor_baixado'), n=Count('id'))
            return {'valor': (agg['v'] or ZERO) - (agg['b'] or ZERO), 'n': agg['n'] or 0}
        return {'vencidos': soma(qs.filter(vencimento__lt=hoje)), 'hoje': soma(qs.filter(vencimento=hoje)),
                'semana': soma(qs.filter(vencimento__gt=hoje, vencimento__lte=semana)), 'total': soma(qs)}

    contexto = _contexto_base(request, 'painel')
    contexto.update({
        'hoje': hoje,
        'pagar': resumo('PAGAR') if pode(user, 'pagar') else None,
        'receber': resumo('RECEBER') if pode(user, 'receber') else None,
        'proximos': list(titulos.filter(vencimento__lte=semana).select_related('loja').order_by('vencimento')[:12]),
    })
    if pode(user, 'bancos'):
        contas = list(filtrar_por_loja(ContaBancaria.objects.filter(ativa=True), user))
        for c in contas:
            c.saldo_atual = c.saldo()
        contexto['contas'] = contas
        contexto['saldo_total'] = sum((c.saldo_atual for c in contas), ZERO)
    if pode(user, 'conciliacao'):
        contexto['extrato_pendente'] = filtrar_por_loja(
            ExtratoLinha.objects.filter(movimento__isnull=True, ignorada=False), user, 'conta__loja').count()
    if pode(user, 'cartoes'):
        cartoes = filtrar_por_loja(RecebivelCartao.objects.all(), user)
        contexto['cartoes_pendentes'] = cartoes.filter(status__in=('PENDENTE', 'DIVERGENTE')).count()
    if pode(user, 'auditoria'):
        contexto['ultimos'] = list(LogFinanceiro.objects.select_related('usuario')[:8])
    return render(request, 'financeiro/painel.html', contexto)


# ── Caixa das lojas ─────────────────────────────────────────────────────────
@login_required
@exige('caixa.ver')
def caixa(request):
    user = request.user
    lojas = list(lojas_do(user))
    loja = None
    if (request.GET.get('loja') or '').isdigit():
        loja = next((l for l in lojas if l.id == int(request.GET['loja'])), None)
    if loja is None and len(lojas) == 1:
        loja = lojas[0]
    hoje = timezone.localdate()
    cartoes = []
    if loja:
        titulos = Titulo.objects.filter(loja=loja)
        abertos = titulos.filter(status__in=('ABERTO', 'PARCIAL'))
        trinta = hoje - timedelta(days=30)
        baixas = Baixa.objects.filter(titulo__loja=loja, estornada=False, data__gte=trinta)

        def saldo(qs):
            agg = qs.aggregate(v=Sum('valor'), b=Sum('valor_baixado'), n=Count('id'))
            return (agg['v'] or ZERO) - (agg['b'] or ZERO), agg['n'] or 0

        def pagos(tipo):
            qs = baixas.filter(titulo__tipo=tipo)
            return sum((b.total for b in qs), ZERO), qs.count()

        if pode(user, 'cartoes'):
            rec = RecebivelCartao.objects.filter(loja=loja)
            pend = rec.filter(status__in=('PENDENTE', 'DIVERGENTE')).count()
            cartoes.append({'titulo': 'Conciliação de Cartões', 'texto': 'Conciliação de Cartões.',
                            'icone': 'fa-credit-card', 'cor': 'violet',
                            'numero': f'{pend} a conferir', 'detalhe': f'{rec.count()} venda(s) da adquirente',
                            'url': reverse('financeiro:cartoes') + f'?loja={loja.id}', 'alerta': pend > 0})
        for tipo, area, nome, texto, icone, cor in (
                ('PAGAR', 'pagar', 'Contas a Pagar', 'Baixe ou edite os títulos a pagar.', 'fa-file-invoice-dollar', 'rose'),
                ('RECEBER', 'receber', 'Contas a Receber', 'Baixe ou edite os títulos a receber.', 'fa-hand-holding-dollar', 'emerald')):
            if pode(user, area):
                valor, n = saldo(abertos.filter(tipo=tipo))
                vencidos = abertos.filter(tipo=tipo, vencimento__lt=hoje).count()
                cartoes.append({'titulo': nome, 'texto': texto, 'icone': icone, 'cor': cor, 'valor': valor,
                                'numero': f'{n} em aberto', 'detalhe': f'{vencidos} vencido(s)' if vencidos else 'nada vencido',
                                'url': reverse(f'financeiro:{area}') + f'?loja={loja.id}', 'alerta': vencidos > 0})
        for tipo, area, nome, texto, cor in (
                ('PAGAR', 'pagar', 'Contas Pagas', 'Edite ou cancele os títulos já pagos.', 'sky'),
                ('RECEBER', 'receber', 'Contas Recebidas', 'Edite ou cancele os títulos já recebidos.', 'teal')):
            if pode(user, area):
                valor, n = pagos(tipo)
                cartoes.append({'titulo': nome, 'texto': texto, 'icone': 'fa-circle-check', 'cor': cor, 'valor': valor,
                                'numero': f'{n} nos últimos 30 dias', 'detalhe': '',
                                'url': reverse('financeiro:pagas' if tipo == 'PAGAR' else 'financeiro:recebidas')
                                + f'?loja={loja.id}', 'alerta': False})
    contexto = _contexto_base(request, 'caixa')
    contexto.update({'lojas': lojas, 'loja': loja, 'cartoes': cartoes})
    return render(request, 'financeiro/caixa.html', contexto)


# ── Títulos ─────────────────────────────────────────────────────────────────
SITUACOES = [('abertos', 'Em aberto'), ('vencidos', 'Vencidos'), ('hoje', 'Vencem hoje'), ('semana', 'Próximos 7 dias'),
             ('quitados', 'Quitados'), ('cancelados', 'Cancelados'), ('todos', 'Todos')]


def filtrar_titulos(request, qs):
    hoje = timezone.localdate()
    f = {k: (request.GET.get(k) or '').strip() for k in ('situacao', 'de', 'ate', 'loja', 'plano', 'q', 'conta')}
    f['situacao'] = f['situacao'] or 'abertos'
    s = f['situacao']
    if s == 'abertos':
        qs = qs.filter(status__in=('ABERTO', 'PARCIAL'))
    elif s == 'vencidos':
        qs = qs.filter(status__in=('ABERTO', 'PARCIAL'), vencimento__lt=hoje)
    elif s == 'hoje':
        qs = qs.filter(status__in=('ABERTO', 'PARCIAL'), vencimento=hoje)
    elif s == 'semana':
        qs = qs.filter(status__in=('ABERTO', 'PARCIAL'), vencimento__gte=hoje, vencimento__lte=hoje + timedelta(days=7))
    elif s == 'quitados':
        qs = qs.filter(status='QUITADO')
    elif s == 'cancelados':
        qs = qs.filter(status='CANCELADO')
    de, ate = parse_date(f['de']) if f['de'] else None, parse_date(f['ate']) if f['ate'] else None
    if de:
        qs = qs.filter(vencimento__gte=de)
    if ate:
        qs = qs.filter(vencimento__lte=ate)
    if f['loja'].isdigit():
        qs = qs.filter(loja_id=int(f['loja']))
    elif f['loja'] == 'sem':
        qs = qs.filter(loja__isnull=True)
    if f['plano'].isdigit():
        qs = qs.filter(Q(plano_id=int(f['plano'])) | Q(plano__pai_id=int(f['plano'])))
    if f['q']:
        digitos = ''.join(c for c in f['q'] if c.isdigit())
        filtro = (Q(descricao__icontains=f['q']) | Q(pessoa_nome__icontains=f['q'])
                  | Q(numero_documento__icontains=f['q']) | Q(observacao__icontains=f['q']))
        if len(digitos) >= 4:
            filtro |= Q(pessoa_documento__contains=digitos) | Q(codigo_barras__contains=digitos)
        qs = qs.filter(filtro)
    return qs, f


def _totais(qs):
    agg = qs.aggregate(v=Sum('valor'), b=Sum('valor_baixado'), n=Count('id'))
    valor, baixado = agg['v'] or ZERO, agg['b'] or ZERO
    return {'valor': valor, 'baixado': baixado, 'saldo': valor - baixado, 'n': agg['n'] or 0}


def _lista_titulos(request, tipo, aba, modo='titulos'):
    user = request.user
    qs = filtrar_por_loja(Titulo.objects.filter(tipo=tipo) if tipo else _titulos_visiveis(user), user)
    qs, filtro = filtrar_titulos(request, qs)
    ordem = 'vencimento' if filtro['situacao'] not in ('quitados', 'cancelados', 'todos') else '-vencimento'
    pagina = Paginator(qs.select_related('loja', 'plano', 'conta_prevista').order_by(ordem, 'id'), 100) \
        .get_page(request.GET.get('p'))
    parametros = request.GET.copy()
    parametros.pop('p', None)
    contexto = _contexto_base(request, aba)
    contexto.update(_formulario_basico(request, tipo))
    contexto.update({
        'tipo': tipo, 'modo': modo, 'pagina': pagina, 'titulos': pagina.object_list, 'filtro': filtro,
        'situacoes': SITUACOES, 'totais': _totais(qs), 'query': parametros.urlencode(),
        'area': 'pagar' if tipo == 'PAGAR' else 'receber' if tipo else 'baixas',
        'planos_filtro': PlanoConta.objects.filter(ativa=True, **({'tipo': 'DESPESA' if tipo == 'PAGAR' else 'RECEITA'}
                                                                  if tipo else {})),
        'pode_editar': pode(user, f'{"pagar" if tipo == "PAGAR" else "receber"}.editar') if tipo else False,
    })
    return render(request, 'financeiro/titulos.html', contexto)


@login_required
@exige('pagar.ver')
def pagar(request):
    return _lista_titulos(request, 'PAGAR', 'pagar')


@login_required
@exige('receber.ver')
def receber(request):
    return _lista_titulos(request, 'RECEBER', 'receber')


@login_required
@exige('baixas.baixar')
def baixa_de_titulos(request):
    """Todos os títulos em aberto (pagar e receber) para baixar um a um ou em lote."""
    return _lista_titulos(request, None, 'baixas', modo='baixas')


def _ler_titulo(request, tipo, titulo=None):
    p = request.POST
    descricao = (p.get('descricao') or '').strip()
    if not descricao:
        raise Invalido('Descreva o título.')
    campos = {
        'descricao': descricao[:200],
        'pessoa_nome': (p.get('pessoa_nome') or '').strip()[:200],
        'pessoa_documento': ''.join(c for c in (p.get('pessoa_documento') or '') if c.isdigit())[:14],
        'numero_documento': (p.get('numero_documento') or '').strip()[:60],
        'emissao': data(p.get('emissao'), obrigatorio=False) or timezone.localdate(),
        'vencimento': data(p.get('vencimento'), campo='data de vencimento'),
        'competencia': data(p.get('competencia') + '-01' if len(p.get('competencia') or '') == 7
                            else p.get('competencia'), obrigatorio=False),
        'valor': dinheiro(p.get('valor'), obrigatorio=True),
        'forma': p.get('forma') if p.get('forma') in dict(Titulo.FORMAS) else '',
        'codigo_barras': ''.join(c for c in (p.get('codigo_barras') or '') if c.isdigit())[:60],
        'pix_favorecido': (p.get('pix_favorecido') or '').strip()[:120],
        'observacao': (p.get('observacao') or '').strip(),
    }
    if campos['pessoa_documento'] and len(campos['pessoa_documento']) not in (11, 14):
        raise Invalido('O CNPJ/CPF precisa ter 11 ou 14 dígitos.')
    plano_id = p.get('plano') or ''
    campos['plano'] = None
    if plano_id.isdigit():
        campos['plano'] = PlanoConta.objects.filter(pk=int(plano_id), analitica=True, ativa=True).first()
        if campos['plano'] is None:
            raise Invalido('Escolha uma conta analítica ativa do plano de contas.')
    loja_id = p.get('loja') or ''
    campos['loja'] = None
    if loja_id.isdigit():
        campos['loja'] = lojas_do(request.user).filter(pk=int(loja_id)).first()
        if campos['loja'] is None:
            raise Invalido('Loja fora das suas lojas liberadas.')
    elif lojas_permitidas(request.user) is not None:
        raise Invalido('Escolha a loja do título.')
    conta_id = p.get('conta_prevista') or ''
    campos['conta_prevista'] = ContaBancaria.objects.filter(pk=int(conta_id)).first() if conta_id.isdigit() else None
    return campos


@login_required
@require_POST
def titulo_novo(request, area):
    tipo = TIPO_DA_URL.get(area)
    if not tipo or not pode(request.user, f'{area}.editar'):
        messages.error(request, 'Lançar este tipo de título não está liberado para o seu usuário.')
        return redirect('financeiro:painel')
    try:
        campos = _ler_titulo(request, tipo)
        anexo = arquivo_ok(request.FILES.get('anexo'))
        parcelas = int(request.POST.get('parcelas') or 1)
        intervalo = request.POST.get('intervalo') or 'MENSAL'
        if intervalo not in ('MENSAL', '7', '14', '15', '21', '28', '30'):
            intervalo = 'MENSAL'
        valor_e_da_parcela = request.POST.get('valor_modo') == 'parcela'
        if valor_e_da_parcela and parcelas > 1:
            campos['valor'] = campos['valor'] * parcelas
        criados = servicos.criar_titulos(dict(campos, tipo=tipo), usuario=request.user, request=request,
                                         parcelas=parcelas, intervalo=intervalo, anexo=anexo)
    except (Invalido, servicos.RegraFinanceira, ValueError) as exc:
        messages.error(request, str(exc))
        return _voltar(request, f'financeiro:{area}')
    if len(criados) > 1:
        messages.success(request, f'{len(criados)} parcelas lançadas: {criados[0].descricao}.')
    else:
        messages.success(request, f'Título lançado: {criados[0].descricao}.')
    return _voltar(request, f'financeiro:{area}')


@login_required
@exige('pagar.ver', 'receber.ver', 'baixas.baixar')
def titulo(request, pk):
    titulo = _titulo_ou_404(request, pk)
    area = 'pagar' if titulo.tipo == 'PAGAR' else 'receber'
    contexto = _contexto_base(request, area)
    contexto.update(_formulario_basico(request, titulo.tipo))
    contexto.update({
        'titulo': titulo, 'area': area,
        'baixas': list(titulo.baixas.select_related('conta', 'criado_por', 'estornada_por').order_by('data', 'id')),
        'irmaos': list(Titulo.objects.filter(grupo=titulo.grupo).exclude(pk=titulo.pk).order_by('parcela'))
        if titulo.parcelas > 1 else [],
        'historico': list(LogFinanceiro.objects.filter(
            Q(objeto_tipo='titulo', objeto_id=str(titulo.pk))
            | Q(objeto_tipo='baixa', objeto_id__in=[str(b) for b in titulo.baixas.values_list('pk', flat=True)]))
            .select_related('usuario')[:60]) if pode(request.user, 'auditoria') else None,
        'pode_editar': pode(request.user, f'{area}.editar'),
        'pode_cancelar': pode(request.user, f'{area}.cancelar'),
    })
    return render(request, 'financeiro/titulo.html', contexto)


@login_required
@require_POST
def titulo_editar(request, pk):
    titulo = _titulo_ou_404(request, pk)
    area = 'pagar' if titulo.tipo == 'PAGAR' else 'receber'
    if not pode(request.user, f'{area}.editar'):
        messages.error(request, 'Editar títulos não está liberado para o seu usuário.')
        return redirect('financeiro:titulo', pk)
    try:
        campos = _ler_titulo(request, titulo.tipo, titulo)
        anexo = arquivo_ok(request.FILES.get('anexo'))
        if anexo:
            campos['anexo'], campos['anexo_nome'] = anexo, anexo.name[:255]
        servicos.editar_titulo(titulo, campos, usuario=request.user, request=request)
        messages.success(request, 'Título atualizado.')
    except (Invalido, servicos.RegraFinanceira) as exc:
        messages.error(request, str(exc))
    return redirect('financeiro:titulo', pk)


@login_required
@require_POST
def titulo_cancelar(request, pk):
    titulo = _titulo_ou_404(request, pk)
    area = 'pagar' if titulo.tipo == 'PAGAR' else 'receber'
    if not pode(request.user, f'{area}.cancelar'):
        messages.error(request, 'Cancelar títulos não está liberado para o seu usuário.')
        return redirect('financeiro:titulo', pk)
    try:
        servicos.cancelar_titulo(titulo, request.POST.get('motivo'), usuario=request.user, request=request)
        messages.success(request, 'Título cancelado.')
    except servicos.RegraFinanceira as exc:
        messages.error(request, str(exc))
    return redirect('financeiro:titulo', pk)


@login_required
@require_POST
def titulo_excluir(request, pk):
    titulo = _titulo_ou_404(request, pk)
    area = 'pagar' if titulo.tipo == 'PAGAR' else 'receber'
    if not pode(request.user, f'{area}.cancelar'):
        messages.error(request, 'Excluir títulos não está liberado para o seu usuário.')
        return redirect('financeiro:titulo', pk)
    try:
        servicos.excluir_titulo(titulo, usuario=request.user, request=request)
    except servicos.RegraFinanceira as exc:
        messages.error(request, str(exc))
        return redirect('financeiro:titulo', pk)
    messages.success(request, 'Título excluído.')
    return redirect(f'financeiro:{area}')


def _ler_baixa(request, titulo=None):
    p = request.POST
    conta = ContaBancaria.objects.filter(pk=int(p['conta'])).first() if (p.get('conta') or '').isdigit() else None
    if conta is None:
        raise Invalido('Escolha a conta bancária.')
    forma = p.get('forma') if p.get('forma') in dict(Titulo.FORMAS) else ''
    if not forma:
        raise Invalido('Escolha a forma de pagamento.')
    return {
        'data': data(p.get('data'), campo='data do pagamento'),
        'conta': conta, 'forma': forma,
        'valor': dinheiro(p.get('valor'), obrigatorio=True),
        'juros': dinheiro(p.get('juros'), campo='juros'),
        'multa': dinheiro(p.get('multa'), campo='multa'),
        'desconto': dinheiro(p.get('desconto'), campo='desconto'),
        'observacao': (p.get('observacao') or '').strip()[:255],
    }


@login_required
@exige('baixas.baixar')
@require_POST
def titulo_baixar(request, pk):
    titulo = _titulo_ou_404(request, pk)
    try:
        campos = _ler_baixa(request, titulo)
        comprovante = arquivo_ok(request.FILES.get('comprovante'))
        servicos.baixar(titulo, comprovante=comprovante, usuario=request.user, request=request, **campos)
        messages.success(request, f'Baixa registrada: {titulo.descricao}.')
    except (Invalido, servicos.RegraFinanceira) as exc:
        messages.error(request, str(exc))
    return _voltar(request, reverse('financeiro:titulo', args=[pk]))


@login_required
@exige('baixas.baixar')
@require_POST
def baixar_lote(request):
    """Baixa vários títulos pelo saldo, na mesma data e conta."""
    ids = [int(i) for i in request.POST.getlist('titulos') if i.isdigit()][:300]
    if not ids:
        messages.error(request, 'Marque os títulos que vai baixar.')
        return _voltar(request, 'financeiro:baixas')
    p = request.POST
    try:
        conta = ContaBancaria.objects.filter(pk=int(p.get('conta') or 0), ativa=True).first()
        if conta is None:
            raise Invalido('Escolha a conta bancária.')
        forma = p.get('forma') if p.get('forma') in dict(Titulo.FORMAS) else ''
        if not forma:
            raise Invalido('Escolha a forma de pagamento.')
        quando = data(p.get('data'), campo='data do pagamento')
    except (Invalido, ValueError) as exc:
        messages.error(request, str(exc))
        return _voltar(request, 'financeiro:baixas')
    feitos, erros, total = 0, [], ZERO
    for titulo in _titulos_visiveis(request.user).filter(pk__in=ids, status__in=('ABERTO', 'PARCIAL')):
        try:
            baixa = servicos.baixar(titulo, quando, titulo.saldo, conta, forma, observacao='Baixa em lote',
                                    usuario=request.user, request=request)
            feitos += 1
            total += baixa.total
        except servicos.RegraFinanceira as exc:
            erros.append(f'{titulo.descricao}: {exc}')
    if feitos:
        messages.success(request, f'{feitos} título(s) baixado(s) em {conta.nome} — R$ {total:,.2f}'
                         .replace(',', 'X').replace('.', ',').replace('X', '.'))
    for erro in erros[:10]:
        messages.error(request, erro)
    return _voltar(request, 'financeiro:baixas')


# ── Contas pagas / recebidas ────────────────────────────────────────────────
def _lista_baixas(request, tipo, aba):
    user = request.user
    qs = filtrar_por_loja(Baixa.objects.filter(titulo__tipo=tipo), user, 'titulo__loja')
    f = {k: (request.GET.get(k) or '').strip() for k in ('de', 'ate', 'loja', 'conta', 'q', 'estornadas')}
    hoje = timezone.localdate()
    de = parse_date(f['de']) if f['de'] else hoje.replace(day=1)
    ate = parse_date(f['ate']) if f['ate'] else hoje
    f['de'], f['ate'] = de.isoformat(), ate.isoformat()
    qs = qs.filter(data__gte=de, data__lte=ate)
    if f['estornadas'] == '1':
        qs = qs.filter(estornada=True)
    elif f['estornadas'] != 'todas':
        qs = qs.filter(estornada=False)
    if f['loja'].isdigit():
        qs = qs.filter(titulo__loja_id=int(f['loja']))
    if f['conta'].isdigit():
        qs = qs.filter(conta_id=int(f['conta']))
    if f['q']:
        qs = qs.filter(Q(titulo__descricao__icontains=f['q']) | Q(titulo__pessoa_nome__icontains=f['q'])
                       | Q(titulo__numero_documento__icontains=f['q']) | Q(observacao__icontains=f['q']))
    baixas = list(qs.select_related('titulo', 'titulo__loja', 'conta', 'criado_por', 'estornada_por')
                  .order_by('-data', '-id')[:500])
    contexto = _contexto_base(request, aba)
    contexto.update(_formulario_basico(request, tipo))
    contexto.update({
        'tipo': tipo, 'baixas': baixas, 'filtro': f,
        'total': sum((b.total for b in baixas if not b.estornada), ZERO),
        'total_juros': sum((b.juros + b.multa for b in baixas if not b.estornada), ZERO),
        'total_desconto': sum((b.desconto for b in baixas if not b.estornada), ZERO),
        'limitado': len(baixas) == 500,
    })
    return render(request, 'financeiro/baixas.html', contexto)


@login_required
@exige('pagar.ver')
def pagas(request):
    return _lista_baixas(request, 'PAGAR', 'pagas')


@login_required
@exige('receber.ver')
def recebidas(request):
    return _lista_baixas(request, 'RECEBER', 'recebidas')


def _baixa_ou_404(request, pk):
    qs = filtrar_por_loja(Baixa.objects.select_related('titulo'), request.user, 'titulo__loja')
    baixa = get_object_or_404(qs, pk=pk)
    if not pode(request.user, 'pagar' if baixa.titulo.tipo == 'PAGAR' else 'receber'):
        raise Http404('Baixa não encontrada.')
    return baixa


@login_required
@exige('baixas.estornar')
@require_POST
def baixa_editar(request, pk):
    baixa = _baixa_ou_404(request, pk)
    try:
        campos = _ler_baixa(request)
        comprovante = arquivo_ok(request.FILES.get('comprovante'))
        if comprovante:
            campos['comprovante'], campos['comprovante_nome'] = comprovante, comprovante.name[:255]
        servicos.editar_baixa(baixa, campos, usuario=request.user, request=request)
        messages.success(request, 'Baixa corrigida.')
    except (Invalido, servicos.RegraFinanceira) as exc:
        messages.error(request, str(exc))
    return _voltar(request, reverse('financeiro:titulo', args=[baixa.titulo_id]))


@login_required
@exige('baixas.estornar')
@require_POST
def baixa_estornar(request, pk):
    baixa = _baixa_ou_404(request, pk)
    try:
        servicos.estornar_baixa(baixa, request.POST.get('motivo'), usuario=request.user, request=request)
        messages.success(request, 'Baixa estornada: o título voltou a ficar em aberto.')
    except servicos.RegraFinanceira as exc:
        messages.error(request, str(exc))
    return _voltar(request, reverse('financeiro:titulo', args=[baixa.titulo_id]))


@login_required
@exige()
def anexo(request, tipo, pk):
    """Abre o anexo do título ou o comprovante da baixa (com o mesmo recorte de acesso)."""
    if tipo == 'titulo':
        obj = _titulo_ou_404(request, pk)
        arquivo = obj.anexo
    else:
        obj = _baixa_ou_404(request, pk)
        arquivo = obj.comprovante
    if not arquivo:
        messages.error(request, 'Sem arquivo anexado.')
        return redirect('financeiro:painel')
    return redirect(arquivo.url)
