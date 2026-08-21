import hmac
import json
import os
import re
import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.error import URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from tickets.models import Category, Ticket, TicketAttachment, TicketLog
from users.models import User

from .ai import analyze_expense
from .exportacao import conciliacao_excel, extrato_excel
from .fatura import TOLERANCIA_DIAS, conciliar, ler_fatura
from .models import Cartao, Gasto
from .permissions import (
    can_access_cartoes,
    can_manage_cartao,
    cartoes_do_usuario,
    is_superadmin,
)

logger = logging.getLogger(__name__)

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


def _periodo_do_request(request, padrao_dias=90):
    """Início e fim vindos da querystring, com um padrão sensato."""
    hoje = timezone.localdate()
    inicio = parse_date(request.GET.get('de') or '') or (hoje - timedelta(days=padrao_dias))
    fim = parse_date(request.GET.get('ate') or '') or hoje
    if inicio > fim:
        inicio, fim = fim, inicio
    return inicio, fim


@login_required
def dashboard(request):
    if not can_access_cartoes(request.user):
        messages.error(request, 'Acesso restrito ao módulo de Cartões.')
        return redirect('home')

    inicio, fim = _periodo_do_request(request)
    visiveis = cartoes_do_usuario(request.user)

    # Filtros da tela. O período vale para os números; responsável, bandeira e
    # situação recortam quais cartões aparecem.
    responsavel_id = request.GET.get('responsavel') or ''
    bandeira = request.GET.get('bandeira') or ''
    situacao = request.GET.get('situacao') or ''
    busca = (request.GET.get('q') or '').strip()

    if responsavel_id.isdigit():
        visiveis = visiveis.filter(responsavel_id=int(responsavel_id))
    if bandeira in dict(Cartao.BANDEIRA_CHOICES):
        visiveis = visiveis.filter(bandeira=bandeira)
    if situacao == 'ativos':
        visiveis = visiveis.filter(ativo=True)
    elif situacao == 'inativos':
        visiveis = visiveis.filter(ativo=False)
    if busca:
        visiveis = visiveis.filter(
            Q(apelido__icontains=busca) | Q(last4__icontains=busca)
            | Q(responsavel__first_name__icontains=busca)
            | Q(responsavel__last_name__icontains=busca))

    no_periodo = Q(gastos__data_gasto__gte=inicio, gastos__data_gasto__lte=fim)
    cartoes = list(visiveis.annotate(
        total_gasto=Sum('gastos__valor', filter=no_periodo),
        num_gastos=Count('gastos', filter=no_periodo),
    ))

    gastos = Gasto.objects.filter(
        cartao__in=[c.id for c in cartoes], data_gasto__gte=inicio, data_gasto__lte=fim)

    resumo = gastos.aggregate(total=Sum('valor'), quantidade=Count('id'))
    total = resumo['total'] or Decimal('0')
    quantidade = resumo['quantidade'] or 0

    por_categoria = list(
        gastos.exclude(categoria_gasto='')
        .values('categoria_gasto').annotate(total=Sum('valor'), n=Count('id'))
        .order_by('-total')[:6])
    maior = total and max((c['total'] for c in por_categoria), default=Decimal('0'))
    for linha in por_categoria:
        linha['fatia'] = round(linha['total'] / maior * 100) if maior else 0

    context = {
        'cartoes': cartoes,
        'is_superadmin': is_superadmin(request.user),
        'total_geral': total,
        'quantidade_gastos': quantidade,
        'ticket_medio': (total / quantidade) if quantidade else Decimal('0'),
        'sem_comprovante': gastos.filter(foto='').count(),
        'por_categoria': por_categoria,
        'ultimos': list(gastos.select_related('cartao', 'criado_por')
                        .order_by('-data_gasto', '-created_at')[:8]),
        'inicio': inicio, 'fim': fim,
        'responsaveis': User.objects.filter(cartoes__isnull=False).distinct()
                                    .order_by('first_name', 'last_name'),
        'bandeiras': Cartao.BANDEIRA_CHOICES,
        'filtro': {'responsavel': responsavel_id, 'bandeira': bandeira,
                   'situacao': situacao, 'q': busca},
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
def extrato_exportar(request, pk):
    """Extrato do cartão em Excel, no período escolhido."""
    cartao = get_object_or_404(Cartao, pk=pk)
    if not can_manage_cartao(request.user, cartao):
        messages.error(request, 'Você não tem acesso a este cartão.')
        return redirect('cartoes:dashboard')

    inicio, fim = _periodo_do_request(request, padrao_dias=365)
    gastos = (cartao.gastos.select_related('criado_por', 'ticket')
              .filter(data_gasto__gte=inicio, data_gasto__lte=fim)
              .order_by('data_gasto', 'id'))
    return extrato_excel(cartao, list(gastos), inicio, fim)


def _fatura_da_sessao(request, cartao):
    """Relatório guardado na sessão pela última conciliação deste cartão.

    A conciliação não grava nada: é uma conferência. Guardar na sessão evita
    pedir o PDF de novo só para exportar o mesmo relatório em Excel.
    """
    guardado = (request.session.get('cartoes_conciliacao') or {}).get(str(cartao.pk))
    if not guardado:
        return None
    try:
        referencia = date.fromisoformat(guardado['referencia'])
        lancamentos = [{
            'last4': i['last4'],
            'data': date.fromisoformat(i['data']),
            'estabelecimento': i['estabelecimento'],
            'valor': Decimal(i['valor']),
            'parcela': i.get('parcela', ''),
        } for i in guardado['lancamentos']]
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return None
    return referencia, lancamentos


def _guardar_fatura(request, cartao, referencia, lancamentos):
    guardadas = request.session.get('cartoes_conciliacao') or {}
    guardadas[str(cartao.pk)] = {
        'referencia': referencia.isoformat(),
        'lancamentos': [{
            'last4': i['last4'], 'data': i['data'].isoformat(),
            'estabelecimento': i['estabelecimento'], 'valor': str(i['valor']),
            'parcela': i['parcela'],
        } for i in lancamentos],
    }
    request.session['cartoes_conciliacao'] = guardadas


def _gastos_do_periodo(cartao, lancamentos):
    """Gastos do portal na janela coberta pela fatura, com folga da tolerância."""
    if not lancamentos:
        return []
    datas = [i['data'] for i in lancamentos]
    folga = timedelta(days=TOLERANCIA_DIAS)
    return list(cartao.gastos.select_related('criado_por')
                .filter(data_gasto__gte=min(datas) - folga,
                        data_gasto__lte=max(datas) + folga)
                .order_by('data_gasto', 'id'))


@login_required
def fatura_conciliar(request, pk):
    """Sobe a fatura em PDF e mostra o que bate, o que diverge e o que falta."""
    cartao = get_object_or_404(Cartao, pk=pk)
    if not can_manage_cartao(request.user, cartao):
        messages.error(request, 'Você não tem acesso a este cartão.')
        return redirect('cartoes:dashboard')

    contexto = {'cartao': cartao, 'is_superadmin': is_superadmin(request.user),
                'hoje': timezone.localdate()}

    if request.method == 'POST':
        arquivo = request.FILES.get('fatura')
        if not arquivo:
            messages.error(request, 'Escolha o PDF da fatura.')
            return redirect('cartoes:fatura_conciliar', pk=cartao.pk)

        referencia = parse_date(request.POST.get('referencia') or '') or \
            timezone.localdate().replace(day=1)

        try:
            leitura = ler_fatura(arquivo, referencia=referencia)
        except Exception as exc:
            logger.warning('Falha lendo a fatura do cartão %s: %s', cartao.pk, exc)
            messages.error(request, f'Não consegui ler esta fatura: {exc}')
            return redirect('cartoes:fatura_conciliar', pk=cartao.pk)

        do_cartao = [i for i in leitura['lancamentos'] if i['last4'] == cartao.last4]
        conferencia = leitura['cartoes'].get(cartao.last4)

        if not do_cartao:
            messages.warning(
                request,
                f'A fatura não tem lançamentos do final {cartao.last4}. '
                f'Cartões encontrados no arquivo: '
                f"{', '.join(sorted(leitura['cartoes'])) or 'nenhum'}.")
            return redirect('cartoes:fatura_conciliar', pk=cartao.pk)

        _guardar_fatura(request, cartao, referencia, do_cartao)
        contexto.update({
            'relatorio': conciliar(do_cartao, _gastos_do_periodo(cartao, do_cartao)),
            'referencia': referencia,
            'conferencia': conferencia,
            'arquivo': arquivo.name,
        })
        if conferencia and not conferencia['confere']:
            messages.warning(
                request,
                'A soma dos lançamentos lidos não bateu com o total que a fatura '
                f"declara para o final {cartao.last4} (diferença de "
                f"R$ {conferencia['diferenca']}). Confira o relatório antes de usá-lo.")

    return render(request, 'cartoes/fatura_conciliar.html', contexto)


@login_required
def fatura_exportar(request, pk):
    """Exporta em Excel a última conciliação feita para este cartão."""
    cartao = get_object_or_404(Cartao, pk=pk)
    if not can_manage_cartao(request.user, cartao):
        messages.error(request, 'Você não tem acesso a este cartão.')
        return redirect('cartoes:dashboard')

    guardado = _fatura_da_sessao(request, cartao)
    if not guardado:
        messages.error(request, 'Envie a fatura primeiro para gerar o relatório.')
        return redirect('cartoes:fatura_conciliar', pk=cartao.pk)

    referencia, lancamentos = guardado
    relatorio = conciliar(lancamentos, _gastos_do_periodo(cartao, lancamentos))
    return conciliacao_excel(cartao, relatorio, referencia)


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

        ticket = abrir_chamado_do_gasto(cartao, gasto, request.user)
        if ticket:
            messages.success(request, 'Gasto lançado e chamado aberto com sucesso.')
        else:
            messages.warning(request, 'Gasto lançado, mas a categoria de chamados (99) não foi encontrada — chamado não aberto.')
        return redirect('cartoes:extrato', pk=cartao.pk)

    context = {
        'cartao': cartao,
        'today': timezone.localdate().isoformat(),
        'form': {},
    }
    return render(request, 'cartoes/gasto_form.html', context)


def abrir_chamado_do_gasto(cartao, gasto, ator_user):
    """Abre um chamado na categoria 99 (Compras no Cartão de Crédito → Financeiro)
    com a descrição padronizada e anexa a foto do comprovante. Vincula ao Gasto.

    Sem dependência de request/messages: serve tanto a tela quanto o endpoint.
    ``ator_user`` é quem consta como autor do chamado. Devolve o Ticket criado
    (ou None se a categoria 99 não existir)."""
    try:
        cat = Category.objects.get(id=CARTAO_CATEGORY_ID)
    except Category.DoesNotExist:
        return None

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
        created_by=ator_user,
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
                uploaded_by=ator_user,
            )
        except Exception:
            pass  # anexo é best-effort; não bloqueia a abertura do chamado

    try:
        TicketLog.objects.create(
            ticket=ticket, user=ator_user, new_status='ABERTO',
            observation='Chamado criado (Cartões)',
        )
    except Exception:
        pass

    gasto.ticket = ticket
    gasto.save(update_fields=['ticket'])
    return ticket


# ---------------------------------------------------------------------------
# Endpoint programático: lançar gasto por API (foto_url + descrição + telefone)
# ---------------------------------------------------------------------------

def _only_digits(value):
    return re.sub(r'\D', '', value or '')


def _norm_br_phone(value):
    """Só dígitos, removendo o código do país (55) quando presente."""
    d = _only_digits(value)
    if len(d) >= 12 and d.startswith('55'):
        d = d[2:]
    return d


def _check_api_token(request):
    """Valida o token estático (Authorization: Bearer <token> ou X-API-Key)."""
    expected = getattr(settings, 'CARTOES_API_TOKEN', '') or ''
    if not expected:
        return False  # sem token configurado, o endpoint fica desligado
    provided = ''
    auth = request.headers.get('Authorization', '') or ''
    if auth.startswith('Bearer '):
        provided = auth[7:].strip()
    if not provided:
        provided = (request.headers.get('X-API-Key', '') or '').strip()
    return bool(provided) and hmac.compare_digest(provided, expected)


def _download_image(url, max_bytes=10 * 1024 * 1024, timeout=15):
    """Baixa uma imagem de uma URL http/https (best-effort). (bytes, mime) ou (None, None)."""
    if not url or not isinstance(url, str):
        return None, None
    if not (url.startswith('http://') or url.startswith('https://')):
        return None, None
    try:
        req = Request(url, headers={'User-Agent': 'redeconfianca-cartoes/1.0'})
        with urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            if ctype and not ctype.startswith('image/'):
                return None, None
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                return None, None
            return data, (ctype or 'image/jpeg')
    except (URLError, ValueError, OSError):
        return None, None


@csrf_exempt
@require_POST
def api_lancar_gasto(request):
    """Lança um gasto e abre o chamado automaticamente, via API.

    Auth: token estático no header. Corpo (JSON ou form):
      - telefone (ou numero): telefone do usuário responsável (obrigatório)
      - foto_url (ou foto): link do comprovante (opcional)
      - descricao: descrição da compra (opcional; obrigatório se não houver foto)
      - valor: valor do gasto (opcional; fallback se a IA não extrair)
      - cartao_last4: desempate quando o usuário tem mais de um cartão
    """
    if not _check_api_token(request):
        return JsonResponse({'error': 'Não autorizado.'}, status=401)

    if (request.content_type or '').startswith('application/json'):
        try:
            payload = json.loads(request.body or b'{}')
            if not isinstance(payload, dict):
                payload = {}
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'JSON inválido.'}, status=400)
    else:
        payload = request.POST

    telefone = (payload.get('telefone') or payload.get('numero') or '').strip()
    descricao = (payload.get('descricao') or '').strip()
    foto_url = (payload.get('foto_url') or payload.get('foto') or '').strip()
    cartao_last4 = (payload.get('cartao_last4') or '').strip()
    valor_payload = _parse_valor(payload.get('valor'))

    if not telefone:
        return JsonResponse({'error': 'Informe o telefone do usuário.'}, status=400)
    if not foto_url and not descricao:
        return JsonResponse({'error': 'Informe foto_url ou descricao.'}, status=400)

    # Identifica o usuário pelo telefone (comparando só os dígitos).
    alvo = _norm_br_phone(telefone)
    match_id = None
    if len(alvo) >= 8:
        for uid, phone in User.objects.filter(is_active=True).exclude(phone='').values_list('id', 'phone'):
            if _norm_br_phone(phone) == alvo:
                match_id = uid
                break
    user = User.objects.filter(id=match_id).first() if match_id else None
    if not user:
        return JsonResponse({'error': 'Usuário não encontrado para este telefone.'}, status=404)

    # Cartão do usuário (responsável).
    qs = Cartao.objects.filter(responsavel=user, ativo=True)
    if cartao_last4:
        qs = qs.filter(last4=cartao_last4)
    cartoes = list(qs[:3])
    if not cartoes:
        return JsonResponse({'error': 'Nenhum cartão ativo para este usuário.'}, status=400)
    if len(cartoes) > 1:
        return JsonResponse({'error': 'Usuário tem mais de um cartão; informe cartao_last4.'}, status=400)
    cartao = cartoes[0]

    # Baixa a foto (best-effort — não trava se falhar).
    image_bytes, mime = None, 'image/jpeg'
    if foto_url:
        image_bytes, dl_mime = _download_image(foto_url)
        if dl_mime:
            mime = dl_mime

    # IA (degrada graciosamente; a chave pode estar indisponível).
    ia = analyze_expense(image_bytes=image_bytes, manual_text=descricao, mime=mime)
    ia_ok = not ia.get('error')

    # Valor: IA -> payload -> 0 (a confirmar).
    valor = _parse_valor(ia.get('valor')) if ia_ok else None
    aviso = None
    if valor is None or valor <= 0:
        valor = valor_payload
    if valor is None or valor <= 0:
        valor = Decimal('0')
        aviso = 'Valor não identificado — chamado aberto com "valor a confirmar".'

    estabelecimento = (ia.get('estabelecimento') if ia_ok else '') or ''
    categoria_gasto = (ia.get('categoria') if ia_ok else '') or ''
    data_ia = ia.get('data') if ia_ok else ''
    data_gasto = parse_date(data_ia) if data_ia else None
    if not data_gasto:
        data_gasto = timezone.localdate()

    descricao_final = descricao or ((ia.get('descricao') if ia_ok else '') or '')
    if aviso:
        descricao_final = (descricao_final + '\n[Valor a confirmar]').strip()

    foto_file = None
    if image_bytes:
        ext = '.png' if mime == 'image/png' else ('.webp' if mime == 'image/webp' else '.jpg')
        foto_file = ContentFile(image_bytes, name=f'comprovante{ext}')

    gasto = Gasto.objects.create(
        cartao=cartao, criado_por=user, valor=valor,
        estabelecimento=estabelecimento, data_gasto=data_gasto,
        categoria_gasto=categoria_gasto, descricao=descricao_final,
        foto=foto_file, origem=('FOTO' if image_bytes else 'MANUAL'),
        ia_dados=(ia if isinstance(ia, dict) else {}),
    )

    ticket = abrir_chamado_do_gasto(cartao, gasto, user)

    return JsonResponse({
        'success': True,
        'gasto_id': gasto.id,
        'ticket_id': ticket.id if ticket else None,
        'valor': str(valor),
        'usuario': user.get_full_name() or user.username,
        'cartao': f'••••{cartao.last4}',
        'ia_ok': ia_ok,
        'aviso': aviso,
    })
