import json
import os
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db.models import Count, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from tickets.models import Category, Ticket, TicketAttachment, TicketLog
from users.models import User

from .ai import analyze_expense
from .models import Cartao, Gasto
from .permissions import (
    can_access_cartoes,
    can_manage_cartao,
    cartoes_do_usuario,
    is_superadmin,
)

CARTAO_CATEGORY_ID = 99


def _parse_valor(raw):
    """Converte '1.234,56' / '1234,56' / '1234.56' → Decimal. Erro → None."""
    if raw is None:
        return None
    txt = str(raw).replace('R$', '').replace(' ', '').strip()
    if not txt:
        return None
    if ',' in txt and '.' in txt:
        txt = txt.replace('.', '').replace(',', '.')
    elif ',' in txt:
        txt = txt.replace(',', '.')
    try:
        return Decimal(txt)
    except (InvalidOperation, ValueError):
        return None


@login_required
def dashboard(request):
    if not can_access_cartoes(request.user):
        messages.error(request, 'Acesso restrito ao módulo de Cartões.')
        return redirect('home')

    cartoes = list(
        cartoes_do_usuario(request.user).annotate(
            total_gasto=Sum('gastos__valor'),
            num_gastos=Count('gastos'),
        )
    )
    context = {
        'cartoes': cartoes,
        'is_superadmin': is_superadmin(request.user),
        'total_geral': sum((c.total_gasto or 0) for c in cartoes),
    }
    return render(request, 'cartoes/dashboard.html', context)


@login_required
def cartao_create(request):
    if not is_superadmin(request.user):
        messages.error(request, 'Apenas SUPERADMIN pode criar cartões.')
        return redirect('cartoes:dashboard')

    if request.method == 'POST':
        apelido = request.POST.get('apelido', '').strip()
        first4 = request.POST.get('first4', '').strip()
        last4 = request.POST.get('last4', '').strip()
        responsavel_id = request.POST.get('responsavel')
        bandeira = request.POST.get('bandeira', '').strip()
        validade_mes = request.POST.get('validade_mes')
        validade_ano = request.POST.get('validade_ano')

        errors = []
        if not (first4.isdigit() and len(first4) == 4):
            errors.append('Os 4 primeiros dígitos devem ser exatamente 4 números.')
        if not (last4.isdigit() and len(last4) == 4):
            errors.append('Os 4 últimos dígitos devem ser exatamente 4 números.')
        responsavel = User.objects.filter(id=responsavel_id, is_active=True).first() if responsavel_id else None
        if not responsavel:
            errors.append('Selecione o usuário responsável.')
        if bandeira not in dict(Cartao.BANDEIRA_CHOICES):
            errors.append('Selecione a bandeira do cartão.')
        try:
            vm, va = int(validade_mes), int(validade_ano)
            if not (1 <= vm <= 12):
                errors.append('Mês de validade deve ser entre 1 e 12.')
            if va < 2000:
                errors.append('Ano de validade inválido.')
        except (TypeError, ValueError):
            errors.append('Informe mês e ano de validade.')
            vm = va = None

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            Cartao.objects.create(
                apelido=apelido, first4=first4, last4=last4, responsavel=responsavel,
                bandeira=bandeira, validade_mes=vm, validade_ano=va, created_by=request.user,
            )
            messages.success(request, 'Cartão criado com sucesso.')
            return redirect('cartoes:dashboard')

    context = {
        'users': User.objects.filter(is_active=True).order_by('first_name', 'last_name'),
        'bandeiras': Cartao.BANDEIRA_CHOICES,
        'form': request.POST if request.method == 'POST' else {},
        'ano_atual': timezone.localdate().year,
    }
    return render(request, 'cartoes/cartao_form.html', context)


@login_required
def cartao_extrato(request, pk):
    cartao = get_object_or_404(Cartao, pk=pk)
    if not can_manage_cartao(request.user, cartao):
        messages.error(request, 'Você não tem acesso a este cartão.')
        return redirect('cartoes:dashboard')

    gastos = list(cartao.gastos.select_related('ticket', 'criado_por').all())
    total = sum((g.valor for g in gastos), Decimal('0'))
    context = {
        'cartao': cartao,
        'gastos': gastos,
        'total': total,
        'is_superadmin': is_superadmin(request.user),
    }
    return render(request, 'cartoes/extrato.html', context)


@login_required
@require_POST
def gasto_analyze(request, pk):
    """AJAX: analisa foto/texto pela IA e devolve os campos extraídos (sem gravar)."""
    cartao = get_object_or_404(Cartao, pk=pk)
    if not can_manage_cartao(request.user, cartao):
        return JsonResponse({'error': 'Acesso negado.'}, status=403)

    manual_text = request.POST.get('manual_text', '').strip()
    image_bytes = None
    mime = 'image/jpeg'
    f = request.FILES.get('foto')
    if f:
        image_bytes = f.read()
        mime = f.content_type or 'image/jpeg'

    data = analyze_expense(image_bytes=image_bytes, manual_text=manual_text, mime=mime)
    return JsonResponse(data)


@login_required
def gasto_create(request, pk):
    cartao = get_object_or_404(Cartao, pk=pk)
    if not can_manage_cartao(request.user, cartao):
        messages.error(request, 'Você não tem acesso a este cartão.')
        return redirect('cartoes:dashboard')

    if request.method == 'POST':
        estabelecimento = request.POST.get('estabelecimento', '').strip()
        categoria_gasto = request.POST.get('categoria_gasto', '').strip()
        descricao = request.POST.get('descricao', '').strip()
        data_raw = request.POST.get('data_gasto', '').strip()
        valor_dec = _parse_valor(request.POST.get('valor'))
        foto = request.FILES.get('foto')
        ia_dados_raw = request.POST.get('ia_dados', '')

        errors = []
        if valor_dec is None or valor_dec <= 0:
            errors.append('Informe um valor válido para o gasto.')
        if not descricao and not estabelecimento:
            errors.append('Informe ao menos a descrição ou o estabelecimento do gasto.')

        data_gasto = parse_date(data_raw) if data_raw else None
        if not data_gasto:
            data_gasto = timezone.localdate()

        if errors:
            for e in errors:
                messages.error(request, e)
            context = {
                'cartao': cartao,
                'today': timezone.localdate().isoformat(),
                'form': request.POST,
            }
            return render(request, 'cartoes/gasto_form.html', context)

        try:
            ia_dados = json.loads(ia_dados_raw) if ia_dados_raw else {}
            if not isinstance(ia_dados, dict):
                ia_dados = {}
        except (json.JSONDecodeError, TypeError):
            ia_dados = {}

        gasto = Gasto.objects.create(
            cartao=cartao, criado_por=request.user, valor=valor_dec,
            estabelecimento=estabelecimento, data_gasto=data_gasto,
            categoria_gasto=categoria_gasto, descricao=descricao,
            foto=foto, origem=('FOTO' if foto else 'MANUAL'), ia_dados=ia_dados,
        )

        _abrir_chamado(request, cartao, gasto)
        messages.success(request, 'Gasto lançado e chamado aberto com sucesso.')
        return redirect('cartoes:extrato', pk=cartao.pk)

    context = {
        'cartao': cartao,
        'today': timezone.localdate().isoformat(),
        'form': {},
    }
    return render(request, 'cartoes/gasto_form.html', context)


def _abrir_chamado(request, cartao, gasto):
    """Abre um chamado na categoria 99 (Compras no Cartão de Crédito → Financeiro)
    com a descrição padronizada e anexa a foto do comprovante. Vincula ao Gasto."""
    try:
        cat = Category.objects.get(id=CARTAO_CATEGORY_ID)
    except Category.DoesNotExist:
        messages.warning(request, 'Categoria de chamados (99) não encontrada — o gasto foi salvo, mas sem chamado.')
        return

    responsavel_nome = cartao.responsavel.get_full_name() or cartao.responsavel.email
    apelido_txt = f' ({cartao.apelido})' if cartao.apelido else ''
    descricao = (
        'Gasto no cartão de crédito\n'
        f'Cartão: {cartao.get_bandeira_display()} ••••{cartao.last4}{apelido_txt}\n'
        f'Responsável: {responsavel_nome}\n'
        f'Valor: R$ {gasto.valor}\n'
        f'Estabelecimento: {gasto.estabelecimento or "—"}\n'
        f'Data: {gasto.data_gasto.strftime("%d/%m/%Y")}\n'
        f'Categoria: {gasto.categoria_gasto or "—"}\n'
        f'Descrição: {gasto.descricao or "—"}'
    )
    title = f'Compra no cartão {cartao.get_bandeira_display()} ••••{cartao.last4} — {gasto.estabelecimento or "gasto"}'

    ticket = Ticket.objects.create(
        title=title[:200],
        description=descricao,
        sector=cat.sector,            # setor derivado da categoria (Financeiro)
        category=cat,
        created_by=request.user,
        priority='MEDIA',
    )

    if gasto.foto:
        try:
            gasto.foto.open('rb')
            content = gasto.foto.read()
            gasto.foto.close()
            base = os.path.basename(gasto.foto.name)
            ext = os.path.splitext(base)[1].lower()
            content_type = 'image/png' if ext == '.png' else ('image/webp' if ext == '.webp' else 'image/jpeg')
            TicketAttachment.objects.create(
                ticket=ticket,
                file=ContentFile(content, name=base),
                original_filename=base,
                file_size=len(content),
                content_type=content_type,
                uploaded_by=request.user,
            )
        except Exception:
            pass  # anexo é best-effort; não bloqueia a abertura do chamado

    try:
        TicketLog.objects.create(
            ticket=ticket, user=request.user, new_status='ABERTO',
            observation='Chamado criado (Cartões)',
        )
    except Exception:
        pass

    gasto.ticket = ticket
    gasto.save(update_fields=['ticket'])
