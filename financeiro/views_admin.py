"""Plano de contas, conciliação de cartões, rastreabilidade e acessos."""
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from . import auditoria, cartoes as conc_cartoes
from .models import (AcessoFinanceiro, EstabelecimentoCartao, LogFinanceiro, PlanoConta, RecebivelCartao)
from .permissoes import AREAS, CHAVES, ROTULO_AREA, e_superadmin, exige, filtrar_por_loja, lojas_permitidas
from .plano_padrao import criar_plano_padrao
from .views import _contexto_base, _voltar, lojas_do

User = get_user_model()
ZERO = Decimal('0.00')


# ── Plano de contas ─────────────────────────────────────────────────────────
@login_required
@exige('plano.ver')
def plano(request):
    contas = list(PlanoConta.objects.annotate(n_titulos=Count('titulos')).order_by('codigo'))
    contexto = _contexto_base(request, 'plano')
    contexto.update({'contas': contas, 'sinteticas': [c for c in contas if not c.analitica],
                     'mostrar_inativas': request.GET.get('inativas') == '1'})
    return render(request, 'financeiro/plano.html', contexto)


@login_required
@exige('plano.editar')
@require_POST
def plano_salvar(request, pk=None):
    conta = get_object_or_404(PlanoConta, pk=pk) if pk else None
    p = request.POST
    codigo = (p.get('codigo') or '').strip().strip('.')
    nome = (p.get('nome') or '').strip()
    if not codigo or not all(parte.isdigit() for parte in codigo.split('.')):
        messages.error(request, 'O código é feito de números separados por ponto (ex.: 2.2.07).')
        return redirect('financeiro:plano')
    if not nome:
        messages.error(request, 'Dê um nome para a conta.')
        return redirect('financeiro:plano')
    pai = PlanoConta.objects.filter(codigo=codigo.rsplit('.', 1)[0]).first() if '.' in codigo else None
    if '.' in codigo and pai is None:
        messages.error(request, f'Crie antes a conta superior {codigo.rsplit(".", 1)[0]}.')
        return redirect('financeiro:plano')
    tipo = p.get('tipo') if p.get('tipo') in ('RECEITA', 'DESPESA') else (pai.tipo if pai else 'DESPESA')
    antes = auditoria.foto(conta) if conta else None
    try:
        with transaction.atomic():
            if conta is None:
                conta = PlanoConta.objects.create(codigo=codigo, nome=nome[:120], tipo=pai.tipo if pai else tipo,
                                                  pai=pai, analitica=True,
                                                  descricao=(p.get('descricao') or '').strip()[:255])
                if pai and pai.analitica:
                    if pai.titulos.exists():
                        raise ValueError(f'A conta {pai} já tem títulos: não pode virar grupo de outras.')
                    pai.analitica = False
                    pai.save(update_fields=['analitica'])
                auditoria.registrar(request, 'CRIAR', f'Criou a conta {conta}', area='plano', obj=conta,
                                    depois=auditoria.foto(conta))
            else:
                conta.nome, conta.descricao = nome[:120], (p.get('descricao') or '').strip()[:255]
                if not conta.pai:
                    conta.tipo = tipo
                conta.save()
                a, d = auditoria.diferenca(antes, auditoria.foto(conta))
                if d:
                    auditoria.registrar(request, 'EDITAR', f'Editou a conta {conta}', area='plano', obj=conta,
                                        antes=a, depois=d)
    except IntegrityError:
        messages.error(request, f'Já existe uma conta com o código {codigo}.')
        return redirect('financeiro:plano')
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('financeiro:plano')
    messages.success(request, f'Conta {conta} salva.')
    return redirect('financeiro:plano')


@login_required
@exige('plano.editar')
@require_POST
def plano_ativar(request, pk):
    conta = get_object_or_404(PlanoConta, pk=pk)
    conta.ativa = not conta.ativa
    conta.save(update_fields=['ativa'])
    auditoria.registrar(request, 'EDITAR', f'{"Reativou" if conta.ativa else "Desativou"} a conta {conta}',
                        area='plano', obj=conta, antes={'ativa': not conta.ativa}, depois={'ativa': conta.ativa})
    messages.success(request, f'Conta {conta} {"reativada" if conta.ativa else "desativada (some dos formulários)"}.')
    return redirect('financeiro:plano')


@login_required
@exige('plano.editar')
@require_POST
def plano_padrao(request):
    n = criar_plano_padrao(usuario=request.user, request=request)
    if n:
        messages.success(request, f'Plano de contas padrão criado: {n} contas. Ajuste à vontade.')
    else:
        messages.info(request, 'O plano de contas já tem contas — o padrão só entra num plano vazio.')
    return redirect('financeiro:plano')


# ── Conciliação de cartões ──────────────────────────────────────────────────
@login_required
@exige('cartoes.ver')
def cartoes(request):
    user = request.user
    qs = filtrar_por_loja(RecebivelCartao.objects.all(), user)
    hoje = timezone.localdate()
    f = {k: (request.GET.get(k) or '').strip() for k in ('de', 'ate', 'loja', 'status', 'q')}
    de = parse_date(f['de']) if f['de'] else hoje - timedelta(days=30)
    ate = parse_date(f['ate']) if f['ate'] else hoje
    f['de'], f['ate'] = de.isoformat(), ate.isoformat()
    qs = qs.filter(data_venda__gte=de, data_venda__lte=ate)
    if f['loja'].isdigit():
        qs = qs.filter(loja_id=int(f['loja']))
    if f['q']:
        qs = qs.filter(Q(nsu__icontains=f['q']) | Q(autorizacao__icontains=f['q']) | Q(bandeira__icontains=f['q']))
    contagem = dict(qs.values('status').annotate(n=Count('id')).values_list('status', 'n'))
    if f['status'] in dict(RecebivelCartao.STATUS):
        qs = qs.filter(status=f['status'])
    totais = qs.aggregate(bruto=Sum('valor_bruto'), liquido=Sum('valor_liquido'), taxa=Sum('taxa'), n=Count('id'))
    pagina = Paginator(qs.select_related('loja', 'venda', 'venda__loja').order_by('-data_venda', 'id'), 100) \
        .get_page(request.GET.get('p'))

    # Vendas do portal no cartão, no período, que nenhuma linha da adquirente cobriu.
    vendas = conc_cartoes.vendas_no_cartao().filter(data_venda__date__gte=de, data_venda__date__lte=ate) \
        .exclude(recebiveis_cartao__isnull=False).select_related('loja')
    ids = lojas_permitidas(user)
    if ids is not None:
        vendas = vendas.filter(loja_id__in=ids)
    if f['loja'].isdigit():
        vendas = vendas.filter(loja_id=int(f['loja']))
    parametros = request.GET.copy()
    parametros.pop('p', None)
    contexto = _contexto_base(request, 'cartoes')
    contexto.update({
        'filtro': f, 'pagina': pagina, 'recebiveis': pagina.object_list, 'contagem': contagem,
        'totais': totais, 'status_choices': RecebivelCartao.STATUS, 'lojas': lojas_do(user),
        'vendas_sem_par': list(vendas.order_by('-data_venda')[:200]),
        'estabelecimentos': list(EstabelecimentoCartao.objects.select_related('loja')),
        'query': parametros.urlencode(),
    })
    return render(request, 'financeiro/cartoes.html', contexto)


@login_required
@exige('cartoes.conciliar')
@require_POST
def cartoes_importar(request):
    arquivo = request.FILES.get('arquivo')
    loja = None
    if (request.POST.get('loja') or '').isdigit():
        loja = lojas_do(request.user).filter(pk=int(request.POST['loja'])).first()
    if lojas_permitidas(request.user) is not None and loja is None:
        messages.error(request, 'Escolha a loja do relatório.')
        return redirect('financeiro:cartoes')
    if not arquivo:
        messages.error(request, 'Escolha o relatório da adquirente (CSV ou Excel).')
        return redirect('financeiro:cartoes')
    if arquivo.size > 15 * 1024 * 1024:
        messages.error(request, 'O relatório passa de 15 MB.')
        return redirect('financeiro:cartoes')
    try:
        novas, repetidas, conciliadas = conc_cartoes.importar(
            arquivo.name, arquivo.read(), adquirente=(request.POST.get('adquirente') or '').strip(), loja=loja,
            usuario=request.user, request=request)
        messages.success(request, f'{novas} venda(s) importada(s)' + (f', {repetidas} já estavam' if repetidas else '')
                         + f'; {conciliadas} casaram com vendas do portal pelo NSU.')
    except conc_cartoes.RelatorioInvalido as exc:
        messages.error(request, str(exc))
    return redirect('financeiro:cartoes')


def _recebivel(request, pk):
    return get_object_or_404(filtrar_por_loja(RecebivelCartao.objects.all(), request.user), pk=pk)


@login_required
@exige('cartoes.conciliar')
@require_POST
def cartoes_marcar(request, pk):
    rec = _recebivel(request, pk)
    status = request.POST.get('status')
    if status not in dict(RecebivelCartao.STATUS):
        messages.error(request, 'Situação inválida.')
    else:
        if status == 'CONCILIADO' and not rec.venda_id and not (request.POST.get('observacao') or '').strip():
            messages.error(request, 'Sem venda ligada, diga por que está conciliado (ex.: venda fora do portal).')
            return _voltar(request, 'financeiro:cartoes')
        conc_cartoes.marcar(rec, status, request.POST.get('observacao') or '', usuario=request.user, request=request)
        messages.success(request, f'NSU {rec.nsu}: {rec.get_status_display().lower()}.')
    return _voltar(request, 'financeiro:cartoes')


@login_required
@exige('cartoes.conciliar')
@require_POST
def cartoes_vincular(request, pk):
    from vendas.models import Venda
    rec = _recebivel(request, pk)
    venda = Venda.objects.filter(pk=int(request.POST.get('venda') or 0)).first()
    if venda is None:
        messages.error(request, 'Informe o número da venda do portal.')
    else:
        try:
            conc_cartoes.vincular(rec, venda, usuario=request.user, request=request)
            messages.success(request, f'NSU {rec.nsu} ligado à venda #{venda.pk}.')
        except ValueError as exc:
            messages.error(request, str(exc))
    return _voltar(request, 'financeiro:cartoes')


@login_required
@exige('cartoes.conciliar')
@require_POST
def cartoes_estabelecimento(request):
    codigo = ''.join(c for c in (request.POST.get('codigo') or '') if c.isalnum())[:40]
    loja = lojas_do(request.user).filter(pk=int(request.POST.get('loja') or 0)).first() \
        if (request.POST.get('loja') or '').isdigit() else None
    if request.POST.get('remover'):
        EstabelecimentoCartao.objects.filter(codigo=codigo).delete()
        auditoria.registrar(request, 'EXCLUIR', f'Tirou o estabelecimento {codigo} das lojas', area='cartoes')
        messages.success(request, f'Estabelecimento {codigo} removido.')
        return redirect('financeiro:cartoes')
    if not codigo or loja is None:
        messages.error(request, 'Informe o código do estabelecimento e a loja.')
        return redirect('financeiro:cartoes')
    EstabelecimentoCartao.objects.update_or_create(
        codigo=codigo, defaults={'loja': loja, 'adquirente': (request.POST.get('adquirente') or '').strip()[:40]})
    atualizados = RecebivelCartao.objects.filter(estabelecimento=codigo, loja__isnull=True).update(loja=loja)
    auditoria.registrar(request, 'EDITAR', f'Ligou o estabelecimento {codigo} à {loja.name}', area='cartoes', loja=loja)
    messages.success(request, f'Estabelecimento {codigo} → {loja.name}' + (f' ({atualizados} venda(s) atualizadas).' if atualizados else '.'))
    return redirect('financeiro:cartoes')


@login_required
@exige('cartoes.conciliar')
@require_POST
def cartoes_reconciliar(request):
    feitos = conc_cartoes.conciliar_pendentes(filtrar_por_loja(RecebivelCartao.objects.all(), request.user),
                                              usuario=request.user, request=request)
    messages.success(request, f'{feitos} venda(s) casaram agora pelo NSU.' if feitos else 'Nenhum par novo pelo NSU.')
    return redirect('financeiro:cartoes')


# ── Rastreabilidade ─────────────────────────────────────────────────────────
@login_required
@exige('auditoria.ver')
def auditoria_lista(request):
    qs = LogFinanceiro.objects.all()
    ids = lojas_permitidas(request.user)
    if ids is not None:
        qs = qs.filter(Q(loja_id__in=ids) | Q(usuario=request.user))
    f = {k: (request.GET.get(k) or '').strip() for k in ('de', 'ate', 'usuario', 'acao', 'area', 'q', 'objeto', 'id')}
    if f['de'] and parse_date(f['de']):
        qs = qs.filter(quando__date__gte=parse_date(f['de']))
    if f['ate'] and parse_date(f['ate']):
        qs = qs.filter(quando__date__lte=parse_date(f['ate']))
    if f['usuario'].isdigit():
        qs = qs.filter(usuario_id=int(f['usuario']))
    if f['acao'] in dict(LogFinanceiro.ACOES):
        qs = qs.filter(acao=f['acao'])
    if f['area'] in ROTULO_AREA:
        qs = qs.filter(area=f['area'])
    if f['objeto'] and f['id']:
        qs = qs.filter(objeto_tipo=f['objeto'], objeto_id=f['id'])
    if f['q']:
        qs = qs.filter(Q(descricao__icontains=f['q']) | Q(usuario_nome__icontains=f['q']) | Q(ip__startswith=f['q']))
    pagina = Paginator(qs.select_related('usuario', 'loja'), 100).get_page(request.GET.get('p'))
    parametros = request.GET.copy()
    parametros.pop('p', None)
    pessoas = User.objects.filter(id__in=LogFinanceiro.objects.values('usuario').distinct()).order_by('first_name')
    contexto = _contexto_base(request, 'auditoria')
    contexto.update({'pagina': pagina, 'registros': pagina.object_list, 'filtro': f, 'acoes': LogFinanceiro.ACOES,
                     'areas': [(a[0], a[1]) for a in AREAS], 'pessoas': pessoas, 'query': parametros.urlencode()})
    return render(request, 'financeiro/auditoria.html', contexto)


# ── Acessos (só o SUPERADMIN) ───────────────────────────────────────────────
@login_required
def acessos(request):
    if not e_superadmin(request.user):
        messages.error(request, 'Só o SUPERADMIN define quem entra no Financeiro.')
        return redirect('financeiro:painel')
    if request.method == 'POST':
        return _salvar_acesso(request)
    liberados = list(AcessoFinanceiro.objects.select_related('user', 'user__sector', 'concedido_por')
                     .prefetch_related('lojas').order_by('-ativo', 'user__first_name'))
    for a in liberados:
        a.chaves = set(a.permissoes or [])
        a.ids_lojas = {l.id for l in a.lojas.all()}
    ja = {a.user_id for a in liberados}
    candidatos = (User.objects.filter(is_active=True).exclude(id__in=ja)
                  .exclude(Q(is_superuser=True) | Q(hierarchy='SUPERADMIN'))
                  .select_related('sector').order_by('first_name', 'last_name'))
    contexto = _contexto_base(request, 'acessos')
    contexto.update({'liberados': liberados, 'candidatos': candidatos, 'areas': AREAS,
                     'lojas': lojas_do(request.user)})
    return render(request, 'financeiro/acessos.html', contexto)


def _salvar_acesso(request):
    p = request.POST
    pessoa = User.objects.filter(pk=int(p.get('user') or 0), is_active=True).first() \
        if (p.get('user') or '').isdigit() else None
    if pessoa is None:
        messages.error(request, 'Escolha a pessoa.')
        return redirect('financeiro:acessos')
    if e_superadmin(pessoa):
        messages.info(request, f'{pessoa.get_full_name() or pessoa.email} é SUPERADMIN e já tem o Financeiro inteiro.')
        return redirect('financeiro:acessos')
    acesso = AcessoFinanceiro.objects.filter(user=pessoa).first()
    if p.get('remover'):
        if acesso:
            antes = {'permissoes': acesso.permissoes, 'lojas': sorted(l.name for l in acesso.lojas.all())}
            acesso.delete()
            auditoria.registrar(request, 'ACESSO', f'Tirou o acesso de {pessoa.get_full_name() or pessoa.email} ao Financeiro',
                                area='acessos', antes=antes)
            messages.success(request, f'{pessoa.get_full_name() or pessoa.email} não entra mais no Financeiro.')
        return redirect('financeiro:acessos')
    chaves = sorted(c for c in p.getlist('permissoes') if c in CHAVES)
    lojas = list(lojas_do(request.user).filter(pk__in=[int(i) for i in p.getlist('lojas') if i.isdigit()]))
    if not chaves:
        messages.error(request, 'Marque ao menos uma permissão (ou use "Tirar acesso").')
        return redirect(reverse('financeiro:acessos') + (f'#acesso-{acesso.pk}' if acesso else ''))
    antes = ({'permissoes': acesso.permissoes, 'lojas': sorted(l.name for l in acesso.lojas.all()), 'ativo': acesso.ativo}
             if acesso else None)
    with transaction.atomic():
        if acesso is None:
            acesso = AcessoFinanceiro.objects.create(user=pessoa, concedido_por=request.user)
        acesso.permissoes = chaves
        acesso.ativo = p.get('ativo', 'on') == 'on'
        acesso.observacao = (p.get('observacao') or '').strip()[:200]
        acesso.concedido_por = request.user
        acesso.save()
        acesso.lojas.set(lojas)
    depois = {'permissoes': chaves, 'lojas': sorted(l.name for l in lojas), 'ativo': acesso.ativo}
    auditoria.registrar(request, 'ACESSO', f'{"Liberou" if antes is None else "Mudou"} o acesso de '
                        f'{pessoa.get_full_name() or pessoa.email} ao Financeiro', area='acessos',
                        antes=antes, depois=depois)
    messages.success(request, f'Acesso de {pessoa.get_full_name() or pessoa.email} salvo: {len(chaves)} permissão(ões)'
                     + (f', {len(lojas)} loja(s).' if lojas else ', todas as lojas.'))
    return redirect(reverse('financeiro:acessos') + f'#acesso-{acesso.pk}')
