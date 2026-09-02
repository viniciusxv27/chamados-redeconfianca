"""Telas da Contagem de Caixa."""
import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, F, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from users.models import Sector

from .models import (ConfiguracaoContagem, ContagemCaixaDia, ImportacaoContagem,
                     SaldoInicialMes)
from .permissions import e_gestor as _e_gestor
from .permissions import lojas_do_usuario as _lojas_do_usuario
from .servicos import (importar, notificar_atencao, previa, recalcular_saldos,
                      saldo_de_abertura)

logger = logging.getLogger(__name__)
ZERO = Decimal('0.00')

# Campos que a tela preenche. O Valor SAP não entra: ele vem da importação.
# A Entrada saiu daqui: virou conta (SAP − parceiros − sangria − transferências).
CAMPOS_EDITAVEIS = ('valor_vivogo', 'allied', 'recarga', 'agoracred', 'renova',
                    'sangria_erro', 'transferencias', 'valor_real', 'deposito')

# Campo em branco aqui significa "ainda não contei", e não "contei e deu zero".
# É o que separa um dia pendente de uma divergência que alerta o gerente.
CAMPOS_QUE_ACEITAM_VAZIO = ('valor_vivogo',)


class ValorInvalido(ValueError):
    pass


def _para_decimal(texto):
    """Aceita '1.234,56', '1234.56' e vazio (que vira zero).

    O que não der para ler vira erro em vez de zero: num controle de caixa,
    um valor digitado errado que silenciosamente vira R$ 0,00 some do radar.
    """
    bruto = (texto or '').strip()
    if not bruto:
        return ZERO
    bruto = bruto.replace('R$', '').replace(' ', '')
    if ',' in bruto:
        bruto = bruto.replace('.', '').replace(',', '.')
    try:
        return Decimal(bruto).quantize(ZERO)
    except (InvalidOperation, ValueError):
        raise ValorInvalido(texto)


@login_required
def dashboard(request):
    """Visão geral: números do período e situação de cada loja."""
    lojas = list(_lojas_do_usuario(request.user))
    hoje = timezone.localdate()

    try:
        dias = max(1, min(365, int(request.GET.get('dias') or 30)))
    except ValueError:
        dias = 30
    inicio = hoje - timedelta(days=dias)

    base = ContagemCaixaDia.objects.filter(loja__in=lojas, data__gte=inicio, data__lte=hoje)

    resumo = base.aggregate(
        total_sap=Sum('valor_sap'), total_vivogo=Sum('valor_vivogo'),
        total_real=Sum('valor_real'), total_deposito=Sum('deposito'), dias=Count('id'))

    # Status é calculado. O banco já separa os dias divergentes, então só os
    # que interessam sobem para a memória. Dia sem Vivo go lançado não é
    # divergência: é dia que a loja ainda não contou.
    #
    # A conta precisa ser a mesma da property `divergencia`: o SAP comparável
    # desconta Agoracred, Renova e Transferências, que o Vivo go não registra.
    # Se as duas divergirem, a tela mostra um número e o alerta usa outro.
    sap_comparavel = F('valor_sap') - F('agoracred') - F('renova') - F('transferencias')
    divergentes = list(base.filter(valor_vivogo__isnull=False)
                       .exclude(valor_vivogo=sap_comparavel).select_related('loja'))
    a_contar = base.filter(valor_vivogo__isnull=True).exclude(valor_sap=ZERO)

    por_dia_da_loja = {}
    for d in divergentes:
        por_dia_da_loja.setdefault(d.loja_id, []).append(d)

    contagem = dict(base.values_list('loja_id').annotate(n=Count('id')))
    pendentes = dict(a_contar.values_list('loja_id').annotate(n=Count('id')))
    ultimos_por_loja = {}
    for loja in lojas:
        ultimos_por_loja[loja.id] = (
            ContagemCaixaDia.objects.filter(loja=loja).order_by('-data').first())

    por_loja = []
    for loja in lojas:
        em_atencao = por_dia_da_loja.get(loja.id, [])
        ultimo = ultimos_por_loja.get(loja.id)
        por_loja.append({
            'loja': loja,
            'dias': contagem.get(loja.id, 0),
            'atencao': len(em_atencao),
            'a_contar': pendentes.get(loja.id, 0),
            'divergencia_total': sum((d.divergencia for d in em_atencao), ZERO),
            'saldo': ultimo.saldo if ultimo else ZERO,
            'ultima_data': ultimo.data if ultimo else None,
        })

    ultimos = sorted(divergentes, key=lambda d: d.data, reverse=True)[:15]

    return render(request, 'contagem_caixa/dashboard.html', {
        'lojas': lojas,
        'por_loja': sorted(por_loja, key=lambda x: (-x['atencao'], x['loja'].name)),
        'resumo': resumo,
        'total_atencao': sum(x['atencao'] for x in por_loja),
        'total_a_contar': sum(x['a_contar'] for x in por_loja),
        'saldo_total': sum((x['saldo'] for x in por_loja), ZERO),
        'ultimos_alertas': ultimos,
        'dias': dias,
        'inicio': inicio,
        'hoje': hoje,
        'aba': 'dashboard',
        'e_gestor': _e_gestor(request.user),
        'ultima_importacao': ImportacaoContagem.objects.first(),
    })


@login_required
def loja_detalhe(request, loja_id):
    """A planilha da loja: uma linha por dia, com os campos preenchíveis."""
    loja = get_object_or_404(Sector, id=loja_id)
    if loja not in _lojas_do_usuario(request.user):
        messages.error(request, 'Você não tem acesso ao caixa desta loja.')
        return redirect('contagem_caixa:dashboard')

    hoje = timezone.localdate()
    try:
        mes = int(request.GET.get('mes') or hoje.month)
        ano = int(request.GET.get('ano') or hoje.year)
        if not 1 <= mes <= 12:
            raise ValueError
    except ValueError:
        mes, ano = hoje.month, hoje.year

    import calendar
    primeiro = date(ano, mes, 1)
    ultimo = date(ano, mes, calendar.monthrange(ano, mes)[1])

    existentes = {d.data: d for d in
                  ContagemCaixaDia.objects.filter(loja=loja, data__gte=primeiro, data__lte=ultimo)}

    abertura = SaldoInicialMes.do_mes(loja.id, ano, mes)
    saldo_anterior, abertura_fixada = saldo_de_abertura(loja.id, ano, mes)

    # O fechamento do mês anterior é mostrado junto mesmo quando a abertura foi
    # fixada: é a diferença entre os dois que explica por que o valor mudou.
    ultimo_do_anterior = (ContagemCaixaDia.objects.filter(loja=loja, data__lt=primeiro)
                          .order_by('-data').first())
    fechamento_anterior = ultimo_do_anterior.saldo if ultimo_do_anterior else ZERO

    # Todo dia do mês aparece, mesmo sem lançamento — a planilha original é
    # assim e é o que deixa o buraco visível. Dia sem lançamento mantém o saldo
    # do dia anterior: o dinheiro continua no caixa mesmo sem venda.
    dias = []
    saldo = saldo_anterior
    atual = primeiro
    while atual <= ultimo:
        dia = existentes.get(atual)
        if dia is None:
            dia = ContagemCaixaDia(loja=loja, data=atual)
            dia.saldo = saldo
        saldo = dia.saldo
        dias.append(dia)
        atual += timedelta(days=1)

    return render(request, 'contagem_caixa/loja.html', {
        'loja': loja,
        'dias': dias,
        'mes': mes, 'ano': ano,
        'primeiro': primeiro, 'ultimo': ultimo,
        'hoje': hoje,
        'saldo_anterior': saldo_anterior,
        'abertura': abertura,
        'abertura_fixada': abertura_fixada,
        'fechamento_anterior': fechamento_anterior,
        'meses': list(enumerate(
            ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho',
             'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'], start=1)),
        'anos': list(range(hoje.year - 2, hoje.year + 1)),
        'lojas': _lojas_do_usuario(request.user),
        'e_gestor': _e_gestor(request.user),
        'total_atencao': sum(1 for d in dias if d.pk and d.em_atencao),
    })


@login_required
@require_POST
def salvar_dia(request, loja_id):
    """Grava os campos preenchidos de um dia e recalcula o saldo em diante."""
    loja = get_object_or_404(Sector, id=loja_id)
    if loja not in _lojas_do_usuario(request.user):
        return JsonResponse({'ok': False, 'erro': 'Sem acesso a esta loja.'}, status=403)

    try:
        dia_data = date.fromisoformat(request.POST.get('data') or '')
    except ValueError:
        return JsonResponse({'ok': False, 'erro': 'Data inválida.'}, status=400)

    # Lê tudo antes de encostar no banco: um valor ilegível no meio não pode
    # deixar o dia gravado pela metade.
    novos = {}
    for campo in CAMPOS_EDITAVEIS:
        if campo not in request.POST:
            continue
        bruto = (request.POST.get(campo) or '').strip()
        if not bruto and campo in CAMPOS_QUE_ACEITAM_VAZIO:
            novos[campo] = None
            continue
        try:
            novos[campo] = _para_decimal(bruto)
        except ValorInvalido:
            rotulo = ContagemCaixaDia._meta.get_field(campo).verbose_name
            return JsonResponse(
                {'ok': False, 'campo': campo,
                 'erro': f'“{bruto}” não é um valor válido em {rotulo}.'},
                status=400)

    registro, _ = ContagemCaixaDia.objects.get_or_create(loja=loja, data=dia_data)
    for campo, valor in novos.items():
        setattr(registro, campo, valor)
    registro.observacao = (request.POST.get('observacao') or '')[:2000]
    registro.atualizado_por = request.user
    registro.save()

    recalcular_saldos(loja.id, desde=dia_data)
    registro.refresh_from_db()

    # Divergência nova avisa o gerente da loja.
    avisados = notificar_atencao(registro)

    return JsonResponse({
        'ok': True,
        'entrada': str(registro.entrada),
        'divergencia': str(registro.divergencia),
        'status': registro.status,
        'status_rotulo': registro.status_rotulo,
        'contado': registro.contado,
        'diferenca': str(registro.diferenca),
        'saldo': str(registro.saldo),
        'avisados': [u.full_name for u in avisados],
    })


@login_required
def importacao(request):
    """Sobe a base de vendas: mostra a prévia e só grava quando confirmado."""
    if not _e_gestor(request.user):
        messages.error(request, 'Apenas administradores importam a base.')
        return redirect('contagem_caixa:dashboard')

    config = ConfiguracaoContagem.get()
    contexto = {
        'config': config,
        'modos': ConfiguracaoContagem.Modo.choices,
        'lojas': _lojas_do_usuario(request.user),
        'aba': 'importacao',
        'historico': ImportacaoContagem.objects.all()[:10],
        'e_gestor': True,
    }

    if request.method == 'POST':
        arquivo = request.FILES.get('planilha')
        if not arquivo:
            messages.error(request, 'Escolha a planilha.')
            return redirect('contagem_caixa:importacao')

        # Ajustes do recorte vêm junto com o arquivo.
        for campo in ('modo', 'forma_pagamento', 'colunas_condicao', 'coluna_pedido',
                      'coluna_valor', 'coluna_codigo', 'coluna_loja', 'coluna_data',
                      'aba', 'filtro_coluna', 'filtro_valor'):
            if campo in request.POST:
                setattr(config, campo, (request.POST.get(campo) or '').strip())
        if config.modo not in dict(ConfiguracaoContagem.Modo.choices):
            config.modo = ConfiguracaoContagem.Modo.FORMA_PGTO
        config.notificar_gerente = request.POST.get('notificar_gerente') == 'on'
        config.save()

        confirmar = request.POST.get('confirmar') == '1'
        try:
            if confirmar:
                registro = ImportacaoContagem(arquivo=arquivo.name, executada_por=request.user)
                resultado = importar(arquivo, usuario=request.user, config=config)
                registro.linhas_lidas = resultado['linhas']
                registro.dias_criados = resultado['criados']
                registro.dias_atualizados = resultado['atualizados']
                registro.lojas_sem_setor = '\n'.join(resultado['sem_setor'])
                registro.sucesso = True
                registro.save()
                messages.success(
                    request,
                    f"Importado: {resultado['criados']} dias novos e "
                    f"{resultado['atualizados']} atualizados em {resultado['lojas']} loja(s).")
                if resultado['sem_setor']:
                    messages.warning(
                        request,
                        f"{len(resultado['sem_setor'])} loja(s) da planilha não têm setor "
                        f"no portal e ficaram de fora: {', '.join(resultado['sem_setor'][:5])}"
                        + ('…' if len(resultado['sem_setor']) > 5 else ''))
                return redirect('contagem_caixa:dashboard')

            contexto['previa'] = previa(arquivo, config)
            messages.info(request, 'Confira a prévia abaixo antes de confirmar a importação.')
        except Exception as exc:
            logger.warning('Falha ao ler a planilha de contagem: %s', exc)
            messages.error(request, f'Não consegui ler a planilha: {exc}')

    return render(request, 'contagem_caixa/importacao.html', contexto)

@login_required
@require_POST
def salvar_saldo_inicial(request, loja_id):
    """Fixa (ou solta) o saldo com que o mês começa.

    Só gestor: mexer aqui desloca o saldo de todos os dias dali para frente, e
    a corrente segue para os meses seguintes.
    """
    loja = get_object_or_404(Sector, id=loja_id)
    if not _e_gestor(request.user):
        messages.error(request, 'Só a gestão define o saldo inicial do mês.')
        return redirect(_url_loja(loja.id, request))
    if loja not in _lojas_do_usuario(request.user):
        messages.error(request, 'Você não tem acesso ao caixa desta loja.')
        return redirect('contagem_caixa:dashboard')

    hoje = timezone.localdate()
    try:
        mes = int(request.POST.get('mes') or hoje.month)
        ano = int(request.POST.get('ano') or hoje.year)
        if not (1 <= mes <= 12 and 2000 <= ano <= 2100):
            raise ValueError
    except (TypeError, ValueError):
        messages.error(request, 'Mês ou ano inválido.')
        return redirect(_url_loja(loja.id, request))

    origem = request.POST.get('origem')

    if origem == 'anterior':
        # Voltar a puxar do mês anterior é apagar a linha: sem linha, a
        # corrente segue normalmente.
        apagadas, _ = SaldoInicialMes.objects.filter(loja=loja, ano=ano, mes=mes).delete()
        recalcular_saldos(loja.id, desde=date(ano, mes, 1))
        messages.success(
            request,
            f'{mes:02d}/{ano} volta a puxar o saldo do fechamento do mês anterior.'
            if apagadas else f'{mes:02d}/{ano} já puxava do mês anterior.')
        return redirect(_url_loja(loja.id, request, ano, mes))

    bruto = (request.POST.get('valor') or '').strip()
    try:
        valor = _para_decimal(bruto) if bruto else ZERO
    except ValorInvalido:
        messages.error(request, f'“{bruto}” não é um valor válido.')
        return redirect(_url_loja(loja.id, request, ano, mes))

    SaldoInicialMes.objects.update_or_create(
        loja=loja, ano=ano, mes=mes,
        defaults={'valor': valor,
                  'motivo': (request.POST.get('motivo') or '')[:200],
                  'definido_por': request.user})
    recalcular_saldos(loja.id, desde=date(ano, mes, 1))
    messages.success(request, f'{mes:02d}/{ano} passa a começar com {valor}.')
    return redirect(_url_loja(loja.id, request, ano, mes))


def _url_loja(loja_id, request, ano=None, mes=None):
    """Volta para a mesma loja e mês que estavam na tela."""
    base = reverse('contagem_caixa:loja_detalhe', args=[loja_id])
    mes = mes or request.POST.get('mes')
    ano = ano or request.POST.get('ano')
    return f'{base}?mes={mes}&ano={ano}' if (mes and ano) else base
