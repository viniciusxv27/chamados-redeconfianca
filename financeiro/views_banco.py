"""Contas bancárias, extrato do portal e conciliação com o extrato do banco."""
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from . import auditoria, extrato, servicos
from .models import ContaBancaria, ExtratoLinha, MovimentoBancario, PlanoConta
from .permissoes import exige, filtrar_por_loja, pode
from .views import Invalido, _contexto_base, _voltar, data, dinheiro, lojas_do

ZERO = Decimal('0.00')


def _contas(user):
    return filtrar_por_loja(ContaBancaria.objects.all(), user)


@login_required
@exige('bancos.ver')
def bancos(request):
    contas = list(_contas(request.user).select_related('loja'))
    hoje = timezone.localdate()
    pendentes = dict(ExtratoLinha.objects.filter(movimento__isnull=True, ignorada=False)
                     .values('conta').annotate(n=Count('id')).values_list('conta', 'n'))
    for c in contas:
        c.saldo_atual = c.saldo()
        c.pendentes = pendentes.get(c.id, 0)
    contexto = _contexto_base(request, 'bancos')
    contexto.update({'contas': contas, 'lojas': lojas_do(request.user), 'tipos': ContaBancaria.TIPOS, 'hoje': hoje,
                     'saldo_total': sum((c.saldo_atual for c in contas if c.ativa), ZERO),
                     'planos': PlanoConta.objects.filter(ativa=True, analitica=True)})
    return render(request, 'financeiro/bancos.html', contexto)


def _com_sinal(texto):
    """Saldo pode ser negativo (cheque especial): '-1.234,56'."""
    bruto = (texto or '').strip()
    if bruto.startswith('-'):
        return -dinheiro(bruto[1:], campo='saldo inicial')
    return dinheiro(bruto, campo='saldo inicial')


def _ler_conta(request):
    p = request.POST
    nome = (p.get('nome') or '').strip()
    if not nome:
        raise Invalido('Dê um apelido para a conta (ex.: "Itaú Matriz").')
    loja = None
    if (p.get('loja') or '').isdigit():
        loja = lojas_do(request.user).filter(pk=int(p['loja'])).first()
    documento = ''.join(c for c in (p.get('titular_documento') or '') if c.isdigit())
    if documento and len(documento) not in (11, 14):
        raise Invalido('O CNPJ/CPF do titular precisa ter 11 ou 14 dígitos.')
    return {
        'nome': nome[:80], 'tipo': p.get('tipo') if p.get('tipo') in dict(ContaBancaria.TIPOS) else 'CORRENTE',
        'banco_codigo': (p.get('banco_codigo') or '').strip()[:5], 'banco_nome': (p.get('banco_nome') or '').strip()[:80],
        'agencia': (p.get('agencia') or '').strip()[:10], 'conta': (p.get('conta') or '').strip()[:20],
        'digito': (p.get('digito') or '').strip()[:2], 'titular_documento': documento,
        'titular_nome': (p.get('titular_nome') or '').strip()[:150], 'loja': loja,
        'chave_pix': (p.get('chave_pix') or '').strip()[:120],
        'saldo_inicial': _com_sinal(p.get('saldo_inicial')),
        'data_saldo_inicial': data(p.get('data_saldo_inicial'), obrigatorio=False) or timezone.localdate(),
        'ativa': p.get('ativa', 'on') == 'on',
        'observacao': (p.get('observacao') or '').strip(),
    }


@login_required
@exige('bancos.editar')
@require_POST
def conta_salvar(request, pk=None):
    conta = get_object_or_404(_contas(request.user), pk=pk) if pk else None
    try:
        campos = _ler_conta(request)
    except Invalido as exc:
        messages.error(request, str(exc))
        return _voltar(request, 'financeiro:bancos')
    if conta is None:
        conta = ContaBancaria.objects.create(**campos)
        auditoria.registrar(request, 'CRIAR', f'Cadastrou a conta bancária {conta.nome}', area='bancos', obj=conta,
                            valor=conta.saldo_inicial, depois=auditoria.foto(conta))
        messages.success(request, f'Conta {conta.nome} cadastrada.')
    else:
        antes = auditoria.foto(conta)
        for nome, valor in campos.items():
            setattr(conta, nome, valor)
        conta.save()
        a, d = auditoria.diferenca(antes, auditoria.foto(conta))
        if d:
            auditoria.registrar(request, 'EDITAR', f'Editou a conta bancária {conta.nome}', area='bancos', obj=conta,
                                antes=a, depois=d)
        messages.success(request, f'Conta {conta.nome} atualizada.')
    return _voltar(request, reverse('financeiro:conta', args=[conta.pk]))


def _serie_saldo(de, saldo_anterior, linhas):
    pontos = {de.isoformat(): float(saldo_anterior)}
    for m in linhas:
        if not m.estornado:
            pontos[m.data.isoformat()] = float(m.saldo_corrente)
    return [{'d': d, 'v': v} for d, v in sorted(pontos.items())]


@login_required
@exige('bancos.ver')
def conta(request, pk):
    conta = get_object_or_404(_contas(request.user).select_related('loja'), pk=pk)
    hoje = timezone.localdate()
    de = parse_date(request.GET.get('de') or '') or hoje.replace(day=1)
    ate = parse_date(request.GET.get('ate') or '') or hoje
    movs = conta.movimentos.filter(data__gte=de, data__lte=ate)
    if request.GET.get('estornados') != '1':
        movs = movs.filter(estornado=False)
    saldo_anterior = conta.saldo(ate=de - timedelta(days=1)) if de > conta.data_saldo_inicial else conta.saldo_inicial
    linhas = list(movs.select_related('baixa__titulo', 'plano', 'criado_por').order_by('data', 'id'))
    corrente = saldo_anterior
    for m in linhas:
        if not m.estornado and m.data >= conta.data_saldo_inicial:
            corrente += m.valor
        m.saldo_corrente = corrente
    entradas = sum((m.valor for m in linhas if m.valor > 0 and not m.estornado), ZERO)
    saidas = sum((m.valor for m in linhas if m.valor < 0 and not m.estornado), ZERO)
    contexto = _contexto_base(request, 'bancos')
    contexto.update({
        'conta': conta, 'movimentos': list(reversed(linhas)), 'de': de, 'ate': ate,
        'saldo_anterior': saldo_anterior, 'saldo_final': corrente, 'saldo_hoje': conta.saldo(),
        'entradas': entradas, 'saidas': saidas, 'hoje': hoje,
        'contas': ContaBancaria.objects.filter(ativa=True).exclude(pk=conta.pk),
        'lojas': lojas_do(request.user), 'tipos': ContaBancaria.TIPOS,
        'planos': PlanoConta.objects.filter(ativa=True, analitica=True),
        'mostrar_estornados': request.GET.get('estornados') == '1',
        # Só para o gráfico da evolução do saldo (um ponto por dia, o último saldo do dia).
        'serie_saldo': _serie_saldo(de, saldo_anterior, linhas),
    })
    return render(request, 'financeiro/conta.html', contexto)


@login_required
@exige('bancos.lancar')
@require_POST
def conta_avulso(request, pk):
    conta = get_object_or_404(_contas(request.user), pk=pk)
    p = request.POST
    try:
        valor = dinheiro(p.get('valor'), obrigatorio=True)
        if p.get('sentido') == 'saida':
            valor = -abs(valor)
        plano = PlanoConta.objects.filter(pk=int(p['plano'])).first() if (p.get('plano') or '').isdigit() else None
        servicos.lancar_avulso(conta, data(p.get('data')), valor, p.get('descricao'), plano=plano,
                               usuario=request.user, request=request)
        messages.success(request, 'Lançamento registrado.')
    except (Invalido, servicos.RegraFinanceira) as exc:
        messages.error(request, str(exc))
    return redirect('financeiro:conta', pk)


@login_required
@exige('bancos.lancar')
@require_POST
def conta_transferir(request, pk):
    origem = get_object_or_404(_contas(request.user), pk=pk)
    p = request.POST
    try:
        destino = ContaBancaria.objects.filter(pk=int(p.get('destino') or 0), ativa=True).first()
        if destino is None:
            raise Invalido('Escolha a conta de destino.')
        servicos.transferir(origem, destino, data(p.get('data')), dinheiro(p.get('valor'), obrigatorio=True),
                            p.get('descricao'), usuario=request.user, request=request)
        messages.success(request, f'Transferência para {destino.nome} registrada.')
    except (Invalido, servicos.RegraFinanceira, ValueError) as exc:
        messages.error(request, str(exc))
    return redirect('financeiro:conta', pk)


@login_required
@exige('bancos.lancar')
@require_POST
def movimento_estornar(request, pk):
    mov = get_object_or_404(MovimentoBancario.objects.filter(conta__in=_contas(request.user)), pk=pk)
    try:
        servicos.estornar_movimento(mov, request.POST.get('motivo'), usuario=request.user, request=request)
        messages.success(request, 'Lançamento estornado.')
    except servicos.RegraFinanceira as exc:
        messages.error(request, str(exc))
    return redirect('financeiro:conta', mov.conta_id)


# ── Conciliação ─────────────────────────────────────────────────────────────
@login_required
@exige('conciliacao.ver')
def conciliacao(request):
    contas = list(_contas(request.user).filter(ativa=True))
    conta = None
    if (request.GET.get('conta') or '').isdigit():
        conta = next((c for c in contas if c.id == int(request.GET['conta'])), None)
    if conta is None and contas:
        # Sem escolha, abre a conta com mais linhas do extrato por conciliar.
        pendentes = dict(ExtratoLinha.objects.filter(conta__in=contas, movimento__isnull=True, ignorada=False)
                         .values('conta').annotate(n=Count('id')).values_list('conta', 'n'))
        conta = max(contas, key=lambda c: pendentes.get(c.id, 0))
    contexto = _contexto_base(request, 'conciliacao')
    contexto.update({'contas': contas, 'conta': conta})
    if conta is None:
        return render(request, 'financeiro/conciliacao.html', contexto)

    situacao = request.GET.get('situacao') or 'pendentes'
    linhas = ExtratoLinha.objects.filter(conta=conta)
    if situacao == 'pendentes':
        linhas = linhas.filter(movimento__isnull=True, ignorada=False)
    elif situacao == 'conciliadas':
        linhas = linhas.filter(movimento__isnull=False)
    elif situacao == 'ignoradas':
        linhas = linhas.filter(ignorada=True)
    pagina = Paginator(linhas.select_related('movimento', 'conciliada_por').order_by('-data', 'id'), 80) \
        .get_page(request.GET.get('p'))
    livres = list(MovimentoBancario.objects.filter(conta=conta, estornado=False, conciliado=False)
                  .select_related('baixa__titulo').order_by('-data', '-id')[:400])
    for linha in pagina.object_list:
        linha.sugestoes = extrato.candidatos(linha, livres) if not linha.movimento_id and not linha.ignorada else []
    contexto.update({
        'situacao': situacao, 'pagina': pagina, 'linhas': pagina.object_list, 'livres': livres,
        'n_pendentes': ExtratoLinha.objects.filter(conta=conta, movimento__isnull=True, ignorada=False).count(),
        'n_conciliadas': ExtratoLinha.objects.filter(conta=conta, movimento__isnull=False).count(),
        'n_ignoradas': ExtratoLinha.objects.filter(conta=conta, ignorada=True).count(),
        'movimentos_sem_extrato': len(livres),
        'importacoes': list(conta.importacoes.select_related('importado_por')[:5]),
        'planos': PlanoConta.objects.filter(ativa=True, analitica=True),
        'saldo_portal': conta.saldo(),
    })
    return render(request, 'financeiro/conciliacao.html', contexto)


@login_required
@exige('conciliacao.conciliar')
@require_POST
def conciliacao_importar(request, pk):
    conta = get_object_or_404(_contas(request.user), pk=pk)
    arquivo = request.FILES.get('arquivo')
    if not arquivo:
        messages.error(request, 'Escolha o arquivo do extrato (OFX ou CSV).')
    elif arquivo.size > 10 * 1024 * 1024:
        messages.error(request, 'O extrato passa de 10 MB.')
    else:
        try:
            imp, auto = extrato.importar(conta, arquivo.name, arquivo.read(), usuario=request.user, request=request)
            messages.success(request, f'Extrato importado: {imp.linhas_novas} linha(s) nova(s)'
                             + (f', {imp.linhas_repetidas} já estavam' if imp.linhas_repetidas else '')
                             + (f'; {auto} conciliada(s) sozinha(s).' if auto else '.'))
        except extrato.ExtratoInvalido as exc:
            messages.error(request, str(exc))
    return redirect(reverse('financeiro:conciliacao') + f'?conta={conta.pk}')


def _linha(request, pk):
    return get_object_or_404(ExtratoLinha.objects.filter(conta__in=_contas(request.user)).select_related('conta'), pk=pk)


@login_required
@exige('conciliacao.conciliar')
@require_POST
def linha_conciliar(request, pk):
    linha = _linha(request, pk)
    mov = MovimentoBancario.objects.filter(pk=int(request.POST.get('movimento') or 0), conta=linha.conta).first()
    if mov is None:
        messages.error(request, 'Escolha o movimento do portal que corresponde a esta linha.')
    else:
        try:
            extrato.conciliar(linha, mov, usuario=request.user, request=request)
            if mov.valor != linha.valor:
                messages.warning(request, 'Conciliado com valor diferente do extrato — confira.')
            else:
                messages.success(request, 'Linha conciliada.')
        except ValueError as exc:
            messages.error(request, str(exc))
    return _voltar(request, reverse('financeiro:conciliacao') + f'?conta={linha.conta_id}')


@login_required
@exige('conciliacao.conciliar')
@require_POST
def linha_desfazer(request, pk):
    linha = _linha(request, pk)
    extrato.desconciliar(linha, usuario=request.user, request=request)
    messages.success(request, 'Conciliação desfeita.')
    return _voltar(request, reverse('financeiro:conciliacao') + f'?conta={linha.conta_id}')


@login_required
@exige('conciliacao.conciliar')
@require_POST
def linha_ignorar(request, pk):
    linha = _linha(request, pk)
    if linha.movimento_id:
        messages.error(request, 'Linha conciliada não se ignora — desfaça a conciliação antes.')
    else:
        linha.ignorada = not linha.ignorada
        linha.save(update_fields=['ignorada'])
        auditoria.registrar(request, 'CONCILIAR', f'{"Ignorou" if linha.ignorada else "Voltou a considerar"} '
                            f'a linha do extrato "{linha.descricao}"', area='conciliacao', obj=linha,
                            valor=linha.valor, loja=linha.conta.loja)
        messages.success(request, 'Linha ignorada.' if linha.ignorada else 'Linha de volta às pendentes.')
    return _voltar(request, reverse('financeiro:conciliacao') + f'?conta={linha.conta_id}')


@login_required
@exige('conciliacao.conciliar')
@require_POST
def linha_lancar(request, pk):
    """Tarifa ou rendimento que só existe no extrato: vira lançamento já conciliado."""
    linha = _linha(request, pk)
    if not pode(request.user, 'bancos.lancar'):
        messages.error(request, 'Lançar na conta precisa da permissão "Lançar avulso" das contas bancárias.')
        return _voltar(request, reverse('financeiro:conciliacao') + f'?conta={linha.conta_id}')
    plano = PlanoConta.objects.filter(pk=int(request.POST['plano'])).first() \
        if (request.POST.get('plano') or '').isdigit() else None
    try:
        extrato.criar_movimento_da_linha(linha, plano=plano, descricao=(request.POST.get('descricao') or '').strip(),
                                         usuario=request.user, request=request)
        messages.success(request, 'Lançado na conta e conciliado.')
    except (servicos.RegraFinanceira, ValueError) as exc:
        messages.error(request, str(exc))
    return _voltar(request, reverse('financeiro:conciliacao') + f'?conta={linha.conta_id}')


@login_required
@exige('conciliacao.conciliar')
@require_POST
def conciliacao_automatica(request, pk):
    conta = get_object_or_404(_contas(request.user), pk=pk)
    feitos = extrato.conciliar_automatico(conta, usuario=request.user, request=request)
    messages.success(request, f'{feitos} linha(s) conciliada(s) automaticamente.' if feitos
                     else 'Nenhum par certo (mesmo valor, até 3 dias, um candidato só) para conciliar sozinho.')
    return redirect(reverse('financeiro:conciliacao') + f'?conta={conta.pk}')
