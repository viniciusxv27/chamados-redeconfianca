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
from django.db import DatabaseError
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.evolution import enviar_texto
from tickets.models import Category, Ticket, TicketAttachment, TicketLog
from users.models import User

from .ai import analyze_expense, e_pdf, paginas_do_pdf
from .exportacao import conciliacao_excel, conciliacao_geral_excel, extrato_excel
from .fatura import TOLERANCIA_DIAS, compra_antiga, conciliar, janela_do_portal, ler_fatura
from .models import AcessoCartoes, Cartao, Gasto
from .permissions import (
    can_access_cartoes,
    can_manage_cartao,
    pode_administrar_acessos,
    pode_gerir_cartoes,
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

    # Por cartão: a fatia do total e o que falta comprovar, numa consulta só.
    sem_foto = dict(gastos.filter(Q(foto='') | Q(foto__isnull=True)).values('cartao_id')
                    .annotate(n=Count('id')).values_list('cartao_id', 'n'))
    for cartao in cartoes:
        cartao.fatia = round((cartao.total_gasto or 0) * 100 / total) if total else 0
        cartao.sem_comprovante = sem_foto.get(cartao.id, 0)

    hoje = timezone.localdate()
    context = {
        'cartoes': cartoes,
        'cartoes_ativos': sum(1 for c in cartoes if c.ativo),
        'atalhos': [
            ('Este mês', hoje.replace(day=1), hoje),
            ('Mês passado', (hoje.replace(day=1) - timedelta(days=1)).replace(day=1),
             hoje.replace(day=1) - timedelta(days=1)),
            ('90 dias', hoje - timedelta(days=90), hoje),
            ('Este ano', hoje.replace(month=1, day=1), hoje),
        ],
        'tem_fatura_lida': bool(request.session.get('cartoes_fatura_geral')),
        # `pode_gerir` abre os botões de gestão; `is_superadmin` fica só para o
        # que é dele mesmo — dizer quem mais cuida dos cartões.
        'pode_gerir': pode_gerir_cartoes(request.user),
        'is_superadmin': is_superadmin(request.user),
        'total_geral': total,
        'quantidade_gastos': quantidade,
        'ticket_medio': (total / quantidade) if quantidade else Decimal('0'),
        'sem_comprovante': gastos.filter(Q(foto='') | Q(foto__isnull=True)).count(),
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
def acessos(request):
    """Quem cuida dos cartões — a lista que o SUPERADMIN mantém.

    Adicionar alguém aqui é dar a mesma mão que o SUPERADMIN tem no módulo:
    ver todos os cartões, criar, lançar gasto e conciliar fatura. Por isso a
    própria lista continua sendo só dele.
    """
    if not pode_administrar_acessos(request.user):
        messages.error(request, 'Só o SUPERADMIN define quem cuida dos cartões.')
        return redirect('cartoes:dashboard')

    if request.method == 'POST':
        escolhido = User.objects.filter(
            id=(request.POST.get('user') or '0') if (request.POST.get('user') or '').isdigit() else 0,
            is_active=True).first()
        if not escolhido:
            messages.error(request, 'Escolha a pessoa que vai cuidar dos cartões.')
            return redirect('cartoes:acessos')
        if is_superadmin(escolhido):
            messages.info(request, f'{escolhido.get_full_name() or escolhido.email} já é '
                                   f'SUPERADMIN e cuida dos cartões.')
            return redirect('cartoes:acessos')
        acesso, criado = AcessoCartoes.objects.get_or_create(
            user=escolhido,
            defaults={'liberado_por': request.user,
                      'observacao': (request.POST.get('observacao') or '').strip()[:200]})
        if criado:
            _notify_acesso(escolhido, request.user)
            messages.success(request, f'{escolhido.get_full_name() or escolhido.email} agora '
                                      f'cuida dos cartões.')
        else:
            messages.info(request, 'Esta pessoa já estava na lista.')
        return redirect('cartoes:acessos')

    # A tabela é nova: no servidor que ainda não rodou o migrate, a tela avisa
    # em vez de estourar 500.
    try:
        liberados = list(AcessoCartoes.objects.select_related('user', 'user__sector', 'liberado_por')
                         .order_by('user__first_name', 'user__last_name'))
        sem_tabela = False
    except DatabaseError:
        liberados, sem_tabela = [], True
    ja_liberados = {a.user_id for a in liberados}
    candidatos = (User.objects.filter(is_active=True)
                  .exclude(id__in=ja_liberados)
                  .select_related('sector')
                  .order_by('sector__name', 'first_name', 'last_name'))
    return render(request, 'cartoes/acessos.html', {
        'liberados': liberados,
        'sem_tabela': sem_tabela,
        'candidatos': [] if sem_tabela else candidatos,
        'is_superadmin': True,
        'pode_gerir': True,
    })


@login_required
@require_POST
def acesso_remover(request, pk):
    """Tira alguém da lista de quem cuida dos cartões."""
    if not pode_administrar_acessos(request.user):
        messages.error(request, 'Só o SUPERADMIN define quem cuida dos cartões.')
        return redirect('cartoes:dashboard')
    acesso = get_object_or_404(AcessoCartoes.objects.select_related('user'), pk=pk)
    nome = acesso.user.get_full_name() or acesso.user.email
    acesso.delete()
    messages.success(request, f'{nome} não cuida mais dos cartões.')
    return redirect('cartoes:acessos')


def _notify_acesso(pessoa, quem_liberou):
    """Avisa quem ganhou a chave do módulo — sem derrubar a tela se falhar.

    Mesmo caminho do resto do portal (`NotificationMixin`), para o aviso cair
    no sino junto com os outros.
    """
    try:
        from core.models import NotificationMixin
        NotificationMixin.create_notifications_for_users(
            users=[pessoa],
            title='Você agora cuida dos cartões',
            message=(f'{quem_liberou.get_full_name() or quem_liberou.email} liberou seu acesso '
                     f'aos cartões corporativos: você pode criar cartões, lançar gastos e '
                     f'conciliar faturas.'),
            notification_type='SYSTEM', related_url='/cartoes/',
        )
    except Exception:                                       # noqa: BLE001
        logger.warning('Não deu para avisar %s sobre o acesso aos cartões', pessoa, exc_info=True)


@login_required
def cartao_create(request):
    if not pode_gerir_cartoes(request.user):
        messages.error(request, 'Só quem cuida dos cartões pode criar um.')
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

    # Sem período na URL o extrato mostra tudo, como sempre mostrou; os atalhos
    # e o filtro recortam.
    todos = cartao.gastos.select_related('ticket', 'criado_por')
    inicio = parse_date(request.GET.get('de') or '')
    fim = parse_date(request.GET.get('ate') or '')
    if inicio and fim and inicio > fim:
        inicio, fim = fim, inicio
    qs = todos
    if inicio:
        qs = qs.filter(data_gasto__gte=inicio)
    if fim:
        qs = qs.filter(data_gasto__lte=fim)
    gastos = list(qs.order_by('-data_gasto', '-created_at'))
    total = sum((g.valor for g in gastos), Decimal('0'))

    categorias = {}
    meses = {}
    for g in gastos:
        chave = g.categoria_gasto or 'Sem categoria'
        categorias[chave] = categorias.get(chave, Decimal('0')) + g.valor
        mes = g.data_gasto.replace(day=1)
        meses[mes] = meses.get(mes, Decimal('0')) + g.valor
    por_categoria = [{'categoria': c, 'total': v, 'fatia': round(v * 100 / total) if total else 0}
                     for c, v in sorted(categorias.items(), key=lambda kv: -kv[1])]
    maior_mes = max(meses.values(), default=Decimal('0'))
    por_mes = [{'mes': m, 'total': v, 'altura': round(v * 100 / maior_mes) if maior_mes else 0}
               for m, v in sorted(meses.items())][-8:]

    hoje = timezone.localdate()
    context = {
        'cartao': cartao,
        'gastos': gastos,
        'total': total,
        'quantidade': len(gastos),
        'ticket_medio': (total / len(gastos)) if gastos else Decimal('0'),
        'sem_comprovante': sum(1 for g in gastos if not g.foto),
        'sem_chamado': sum(1 for g in gastos if not g.ticket_id),
        'por_categoria': por_categoria,
        'por_mes': por_mes,
        'categorias': sorted(categorias),
        'inicio': inicio, 'fim': fim,
        'atalhos': [
            ('Este mês', hoje.replace(day=1), hoje),
            ('Mês passado', (hoje.replace(day=1) - timedelta(days=1)).replace(day=1),
             hoje.replace(day=1) - timedelta(days=1)),
            ('90 dias', hoje - timedelta(days=90), hoje),
            ('Este ano', hoje.replace(month=1, day=1), hoje),
        ],
        'ultima_conciliacao': bool(_fatura_da_sessao(request, cartao)),
        'pode_gerir': pode_gerir_cartoes(request.user),
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


CAMPOS_DO_LANCAMENTO = ('last4', 'estabelecimento', 'parcela', 'categoria', 'cidade', 'detalhe')


def _serializar(lancamentos):
    return [dict({c: i.get(c, '') for c in CAMPOS_DO_LANCAMENTO},
                 data=i['data'].isoformat(), valor=str(i['valor']),
                 internacional=bool(i.get('internacional')), iof=bool(i.get('iof')))
            for i in lancamentos]


def _desserializar(itens):
    return [dict({c: i.get(c, '') for c in CAMPOS_DO_LANCAMENTO},
                 data=date.fromisoformat(i['data']), valor=Decimal(i['valor']),
                 internacional=bool(i.get('internacional')), iof=bool(i.get('iof')))
            for i in itens]


def _resumo_serializavel(cartoes):
    """O resumo da leitura por final, com Decimal virando texto (vai para a sessão)."""
    return {final: {k: (str(v) if isinstance(v, Decimal) else v) for k, v in dados.items()}
            for final, dados in cartoes.items()}


def _resumo_lido(cartoes):
    def dec(v):
        return Decimal(v) if isinstance(v, str) else v
    return {final: {k: dec(v) if k.startswith(('declarado', 'lido', 'diferenca')) else v
                    for k, v in dados.items()}
            for final, dados in cartoes.items()}


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
        lancamentos = _desserializar(guardado['lancamentos'])
        leitura = _resumo_lido(guardado.get('leitura') or {}).get(cartao.last4)
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return None
    return referencia, lancamentos, leitura, guardado.get('arquivo', '')


def _guardar_fatura(request, cartao, referencia, lancamentos, leitura=None, arquivo=''):
    guardadas = request.session.get('cartoes_conciliacao') or {}
    guardadas[str(cartao.pk)] = {
        'referencia': referencia.isoformat(),
        'lancamentos': _serializar(lancamentos),
        'leitura': _resumo_serializavel({cartao.last4: leitura}) if leitura else {},
        'arquivo': arquivo,
    }
    request.session['cartoes_conciliacao'] = guardadas


def _fatura_geral_da_sessao(request):
    """A última fatura inteira lida na conciliação geral (todos os cartões)."""
    guardado = request.session.get('cartoes_fatura_geral')
    if not guardado:
        return None
    try:
        return {
            'referencia': date.fromisoformat(guardado['referencia']),
            'vencimento': date.fromisoformat(guardado['vencimento']) if guardado.get('vencimento') else None,
            'arquivo': guardado.get('arquivo', ''),
            'lancamentos': _desserializar(guardado['lancamentos']),
            'cartoes': _resumo_lido(guardado['cartoes']),
            'total_declarado': Decimal(guardado['total_declarado']) if guardado.get('total_declarado') else None,
            'total_lido': Decimal(guardado['total_lido']),
        }
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return None


def _guardar_fatura_geral(request, leitura, arquivo):
    request.session['cartoes_fatura_geral'] = {
        'referencia': leitura['referencia'].isoformat(),
        'vencimento': leitura['vencimento'].isoformat() if leitura.get('vencimento') else '',
        'arquivo': arquivo,
        'lancamentos': _serializar(leitura['lancamentos']),
        'cartoes': _resumo_serializavel(leitura['cartoes']),
        'total_declarado': str(leitura['total_declarado']) if leitura.get('total_declarado') is not None else '',
        'total_lido': str(leitura['total_lido']),
    }


def _gastos_do_periodo(cartao, lancamentos):
    """Gastos do portal para conciliar estes lançamentos.

    Os do período da fatura e, para cada parcela de compra antiga (02/xx em
    diante), os lançados perto da data da compra original — a fatura repete
    essa data, e a compra foi lançada no portal naquele mês.
    """
    inicio, fim = janela_do_portal(lancamentos)
    if inicio is None:
        return []
    filtro = Q(data_gasto__gte=inicio, data_gasto__lte=fim)
    folga = timedelta(days=TOLERANCIA_DIAS)
    for dia in sorted({i['data'] for i in lancamentos if compra_antiga(i)}):
        filtro |= Q(data_gasto__gte=dia - folga, data_gasto__lte=dia + folga)
    return list(cartao.gastos.select_related('criado_por', 'ticket')
                .filter(filtro).order_by('data_gasto', 'id'))


def _conciliar_cartao(cartao, lancamentos):
    return conciliar(lancamentos, _gastos_do_periodo(cartao, lancamentos), janela_do_portal(lancamentos))


def _ler_pdf_enviado(request, destino_erro):
    """Lê o PDF do formulário. Devolve (leitura, None) ou (None, redirect)."""
    arquivo = request.FILES.get('fatura')
    if not arquivo:
        messages.error(request, 'Escolha o PDF da fatura.')
        return None, redirect(*destino_erro)
    # Sem mês informado, vale o vencimento impresso na própria fatura.
    referencia = parse_date(request.POST.get('referencia') or '')
    try:
        leitura = ler_fatura(arquivo, referencia=referencia.replace(day=1) if referencia else None)
    except Exception as exc:
        logger.warning('Falha lendo a fatura: %s', exc)
        messages.error(request, f'Não consegui ler esta fatura: {exc}')
        return None, redirect(*destino_erro)
    if not leitura['lancamentos']:
        messages.error(request, 'Não encontrei lançamentos neste PDF. É a fatura do cartão (Itaú)?')
        return None, redirect(*destino_erro)
    leitura['arquivo'] = arquivo.name
    return leitura, None


def _cartoes_por_final(user, finais):
    """{final: [cartões visíveis com esse final]} — ativos primeiro."""
    mapa = {}
    for cartao in (cartoes_do_usuario(user).filter(last4__in=finais)
                   .order_by('-ativo', 'apelido')):
        mapa.setdefault(cartao.last4, []).append(cartao)
    return mapa


def _outros_cartoes_da_fatura(user, leitura_cartoes, final_atual):
    """Os outros finais da mesma fatura, com o cartão do portal quando houver."""
    mapa = _cartoes_por_final(user, list(leitura_cartoes))
    return [{'final': final, 'nome': dados.get('nome', ''), 'total': dados.get('lido'),
             'cartao': (mapa.get(final) or [None])[0]}
            for final, dados in sorted(leitura_cartoes.items()) if final != final_atual]


@login_required
def fatura_conciliar(request, pk):
    """Concilia um cartão — mesmo que a fatura traga vários: vale o final dele."""
    cartao = get_object_or_404(Cartao, pk=pk)
    if not can_manage_cartao(request.user, cartao):
        messages.error(request, 'Você não tem acesso a este cartão.')
        return redirect('cartoes:dashboard')

    contexto = {'cartao': cartao, 'pode_gerir': pode_gerir_cartoes(request.user),
                'is_superadmin': is_superadmin(request.user),
                'hoje': timezone.localdate()}

    leitura_cartoes, referencia, arquivo, do_cartao = None, None, '', None
    if request.method == 'POST':
        leitura, erro = _ler_pdf_enviado(request, ('cartoes:fatura_conciliar', cartao.pk))
        if erro:
            return erro
        leitura_cartoes, referencia, arquivo = leitura['cartoes'], leitura['referencia'], leitura['arquivo']
        do_cartao = [i for i in leitura['lancamentos'] if i['last4'] == cartao.last4]
        # A fatura inteira também fica guardada: dá para abrir os outros cartões
        # dela sem subir o PDF de novo.
        if pode_gerir_cartoes(request.user):
            _guardar_fatura_geral(request, leitura, arquivo)
    elif request.GET.get('geral'):
        # Vindo da conciliação geral: a fatura já foi lida.
        geral = _fatura_geral_da_sessao(request)
        if geral:
            leitura_cartoes, referencia, arquivo = geral['cartoes'], geral['referencia'], geral['arquivo']
            do_cartao = [i for i in geral['lancamentos'] if i['last4'] == cartao.last4]
    else:
        guardado = _fatura_da_sessao(request, cartao)
        if guardado and request.GET.get('ultima'):
            referencia, do_cartao, leitura_um, arquivo = guardado
            leitura_cartoes = {cartao.last4: leitura_um} if leitura_um else {}

    if do_cartao is not None:
        if not do_cartao:
            messages.warning(
                request,
                f'A fatura não tem lançamentos do final {cartao.last4}. '
                f"Cartões encontrados no arquivo: {', '.join(sorted(leitura_cartoes or {})) or 'nenhum'}.")
            return redirect('cartoes:fatura_conciliar', pk=cartao.pk)

        conferencia = (leitura_cartoes or {}).get(cartao.last4)
        _guardar_fatura(request, cartao, referencia, do_cartao, conferencia, arquivo)
        relatorio = _conciliar_cartao(cartao, do_cartao)
        inicio, fim = janela_do_portal(do_cartao)
        contexto.update({
            'relatorio': relatorio,
            'referencia': referencia,
            'conferencia': conferencia,
            'arquivo': arquivo,
            'janela': (inicio, fim),
            'internacionais': sum(1 for i in do_cartao if i.get('internacional')),
            'outros_cartoes': (_outros_cartoes_da_fatura(request.user, leitura_cartoes, cartao.last4)
                               if leitura_cartoes and len(leitura_cartoes) > 1 else []),
            'contagem': {s: sum(1 for l in relatorio['linhas'] if l['situacao'] == s)
                         for s in ('conferido', 'divergente', 'nao_lancado', 'sem_cobranca')},
        })
        if conferencia and not conferencia['confere']:
            messages.warning(
                request,
                'A soma dos lançamentos lidos não bateu com o total que a fatura '
                f"declara para o final {cartao.last4} (diferença de "
                f"R$ {conferencia['diferenca']}). Confira o relatório antes de usá-lo.")

    return render(request, 'cartoes/fatura_conciliar.html', contexto)


@login_required
def fatura_geral(request):
    """Conciliação geral: uma fatura com vários cartões, cada final no seu cartão."""
    if not pode_gerir_cartoes(request.user):
        messages.error(request, 'A conciliação geral é de quem gere os cartões.')
        return redirect('cartoes:dashboard')

    if request.method == 'POST':
        leitura, erro = _ler_pdf_enviado(request, ('cartoes:fatura_geral',))
        if erro:
            return erro
        _guardar_fatura_geral(request, leitura, leitura['arquivo'])
        if not leitura['confere_total'] and leitura.get('total_declarado') is not None:
            messages.warning(
                request,
                f"A leitura somou R$ {leitura['total_lido']}, mas a fatura declara "
                f"R$ {leitura['total_declarado']}. Confira os cartões marcados antes de usar o relatório.")
        return redirect('cartoes:fatura_geral')

    contexto = {'pode_gerir': True, 'is_superadmin': is_superadmin(request.user),
                'hoje': timezone.localdate()}
    geral = _fatura_geral_da_sessao(request)
    if geral:
        contexto.update(_conciliacao_geral(request.user, geral))
    return render(request, 'cartoes/fatura_geral.html', contexto)


def _conciliacao_geral(user, geral):
    """Concilia cada final da fatura com o cartão do portal que tem esse final."""
    finais = sorted(geral['cartoes'])
    mapa = _cartoes_por_final(user, finais)
    linhas, total_portal, total_conferido = [], Decimal('0'), Decimal('0')
    for final in finais:
        leitura = geral['cartoes'][final]
        lancamentos = [i for i in geral['lancamentos'] if i['last4'] == final]
        candidatos = mapa.get(final, [])
        cartao = candidatos[0] if candidatos else None
        relatorio = _conciliar_cartao(cartao, lancamentos) if cartao else None
        if relatorio:
            total_portal += relatorio['total_extrato']
            total_conferido += relatorio['total_conferido']
        linhas.append({
            'final': final, 'leitura': leitura, 'cartao': cartao,
            'ambiguo': len(candidatos) > 1, 'candidatos': candidatos,
            'relatorio': relatorio, 'lancamentos': lancamentos,
        })
    # Cartões do portal ativos que não aparecem nesta fatura (outro banco, outra conta).
    fora = list(cartoes_do_usuario(user).filter(ativo=True).exclude(last4__in=finais)
                .order_by('apelido', 'last4'))
    total = geral['total_lido']
    return {
        'geral': geral,
        'linhas_geral': linhas,
        'cadastrados': [l for l in linhas if l['cartao']],
        'nao_cadastrados': [l for l in linhas if not l['cartao']],
        'fora_da_fatura': fora,
        'total_portal': total_portal,
        'total_conferido': total_conferido,
        'percentual_conciliado': round(float(total_conferido * 100 / total), 1) if total else 0,
        'total_nao_cadastrados': sum((l['leitura']['lido'] for l in linhas if not l['cartao']), Decimal('0')),
    }


@login_required
@require_POST
def fatura_limpar(request):
    """Limpa a conciliação guardada: a fatura inteira ou, com ``cartao``, só a de um cartão.

    A leitura vive na sessão (a conciliação não grava nada no banco); limpar
    tira da tela o resultado de uma fatura que não vale mais — e da
    exportação, que sai dele.
    """
    if not is_superadmin(request.user):
        messages.error(request, 'Só o SUPERADMIN limpa a conciliação.')
        return redirect('cartoes:dashboard')

    cartao_id = (request.POST.get('cartao') or '').strip()
    guardadas = request.session.get('cartoes_conciliacao') or {}
    if cartao_id.isdigit():
        guardadas.pop(cartao_id, None)
        request.session['cartoes_conciliacao'] = guardadas
        messages.success(request, 'Conciliação deste cartão limpa.')
        return redirect('cartoes:fatura_conciliar', pk=int(cartao_id))

    request.session.pop('cartoes_fatura_geral', None)
    request.session.pop('cartoes_conciliacao', None)
    messages.success(request, 'Conciliação da fatura limpa. Envie outra fatura para conciliar de novo.')
    return redirect('cartoes:fatura_geral')


@login_required
def fatura_geral_exportar(request):
    if not pode_gerir_cartoes(request.user):
        messages.error(request, 'A conciliação geral é de quem gere os cartões.')
        return redirect('cartoes:dashboard')
    geral = _fatura_geral_da_sessao(request)
    if not geral:
        messages.error(request, 'Envie a fatura primeiro para gerar o relatório.')
        return redirect('cartoes:fatura_geral')
    return conciliacao_geral_excel(_conciliacao_geral(request.user, geral))


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

    referencia, lancamentos, leitura, arquivo = guardado
    relatorio = _conciliar_cartao(cartao, lancamentos)
    return conciliacao_excel(cartao, relatorio, referencia, leitura=leitura, arquivo=arquivo,
                             janela=janela_do_portal(lancamentos))


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
        # O navegador manda `application/pdf`, mas alguns clientes mandam
        # genérico: o conteúdo é quem decide (a IA lê PDF de outro jeito).
        if e_pdf(image_bytes, mime):
            mime = 'application/pdf'

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

        # Comprovante em PDF entra como a primeira página renderizada: o campo
        # é ImageField e é essa imagem que a tela do gasto mostra.
        comprovante = _comprovante_para_o_campo(foto)

        gasto = Gasto.objects.create(
            cartao=cartao, criado_por=request.user, valor=valor_dec,
            estabelecimento=estabelecimento, data_gasto=data_gasto,
            categoria_gasto=categoria_gasto, descricao=descricao,
            foto=comprovante, origem=('FOTO' if comprovante else 'MANUAL'), ia_dados=ia_dados,
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
        f'- Aberto por Robo de Cartões'
    )
    title = f'COMPRA: {gasto.descricao} — R${gasto.valor}'

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


def _comprovante_para_o_campo(arquivo):
    """O arquivo enviado, pronto para o ``ImageField`` do gasto.

    Imagem vai como está; PDF vira a primeira página em PNG (o campo não
    guarda PDF, e é essa imagem que aparece no extrato).
    """
    if not arquivo:
        return None
    nome = (getattr(arquivo, 'name', '') or '').lower()
    tipo = (getattr(arquivo, 'content_type', '') or '')
    if not (nome.endswith('.pdf') or tipo == 'application/pdf'):
        return arquivo
    try:
        arquivo.seek(0)
    except Exception:                                           # noqa: BLE001
        pass
    conteudo = arquivo.read()
    paginas = paginas_do_pdf(conteudo, limite=1)
    if not paginas:
        return None
    return ContentFile(paginas[0], name='comprovante.png')


def _baixar_comprovante(url, max_bytes=10 * 1024 * 1024, timeout=15):
    """Baixa o comprovante de uma URL http/https. (bytes, mime) ou (None, None).

    Imagem **ou PDF**: o cliente manda os dois pelo WhatsApp, e o PDF era
    recusado aqui — chegava à IA como se fosse JPEG e nada era identificado.
    O tipo é confirmado pelo conteúdo, porque o `Content-Type` que vem do
    WhatsApp costuma ser genérico (`application/octet-stream`).
    """
    if not url or not isinstance(url, str):
        return None, None
    if not (url.startswith('http://') or url.startswith('https://')):
        return None, None
    try:
        req = Request(url, headers={'User-Agent': 'redeconfianca-cartoes/1.0'})
        with urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                return None, None
            if data[:5] == b'%PDF-':
                return data, 'application/pdf'
            if ctype and not (ctype.startswith('image/') or ctype == 'application/pdf'):
                # Tipo genérico continua valendo: quem decide é o conteúdo, e a
                # IA descarta o que não for imagem legível.
                if ctype not in ('application/octet-stream', 'binary/octet-stream'):
                    return None, None
                ctype = ''
            return data, (ctype or 'image/jpeg')
    except (URLError, ValueError, OSError):
        return None, None


MENSAGEM_ESCOLHA_ENVIADA = 'Mensagem enviada ao cliente'


def texto_escolha_do_cartao(cartoes):
    """A lista que o cliente recebe no WhatsApp para escolher o cartão."""
    linhas = [f'[ {n:02d} ] Final {c.last4} {c.get_bandeira_display()}'
              for n, c in enumerate(cartoes, start=1)]
    return 'Escolha o cartão:\n\n' + '\n'.join(linhas)


def _pedir_escolha_do_cartao(numero, cartoes):
    """Mais de um cartão: manda a lista pelo WhatsApp em vez de adivinhar qual é."""
    ok, detalhe = enviar_texto(numero, texto_escolha_do_cartao(cartoes))
    if not ok:
        logger.warning('Escolha de cartão não enviada ao cliente: %s', detalhe)
        return JsonResponse({
            'error': ('Usuário tem mais de um cartão e a mensagem para escolher não pôde ser enviada; '
                      'informe cartao_opcao ou cartao_last4.'),
            'detalhe': detalhe[:200],
        }, status=502)
    return JsonResponse({
        'success': True,
        'status': 200,
        'mensagem': MENSAGEM_ESCOLHA_ENVIADA,
        'aguardando_escolha': True,
        'cartoes': [{'opcao': f'{n:02d}', 'last4': c.last4, 'bandeira': c.get_bandeira_display()}
                    for n, c in enumerate(cartoes, start=1)],
    })


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
      - cartao_opcao: o número escolhido na lista mandada ao cliente (ex.: "01")

    Com mais de um cartão ativo e sem desempate, o gasto não é lançado: o cliente
    recebe no WhatsApp (Evolution API) a lista para escolher e a resposta é
    200 "Mensagem enviada ao cliente". A escolha volta nesta mesma API, com
    ``cartao_opcao`` (ou ``cartao_last4``).
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
    cartao_opcao = str(payload.get('cartao_opcao') or payload.get('opcao') or '').strip()
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

    # Cartão do usuário (responsável). A ordem é a mesma da lista mandada ao
    # cliente: o "01" que ele responde é o primeiro desta lista.
    ativos = list(Cartao.objects.filter(responsavel=user, ativo=True).order_by('apelido', 'last4', 'id'))
    cartoes = [c for c in ativos if c.last4 == cartao_last4] if cartao_last4 else ativos
    if cartao_opcao and not cartao_last4 and len(ativos) > 1:
        numero_opcao = int(_only_digits(cartao_opcao) or 0)
        if not 1 <= numero_opcao <= len(ativos):
            return JsonResponse(
                {'error': f'Opção de cartão inválida: escolha de 01 a {len(ativos):02d}.'}, status=400)
        cartoes = [ativos[numero_opcao - 1]]
    if not cartoes:
        return JsonResponse({'error': 'Nenhum cartão ativo para este usuário.'}, status=400)
    if len(cartoes) > 1:
        # A lista vai sempre com todos os cartões ativos, na ordem acima.
        return _pedir_escolha_do_cartao(telefone, ativos)
    cartao = cartoes[0]

    # Baixa o comprovante (best-effort — não trava se falhar). Pode ser PDF.
    image_bytes, mime = None, 'image/jpeg'
    if foto_url:
        image_bytes, dl_mime = _baixar_comprovante(foto_url)
        if dl_mime:
            mime = dl_mime

    # IA (degrada graciosamente; a chave pode estar indisponível). Ela insiste
    # sozinha enquanto faltar valor, categoria ou descrição.
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
        if e_pdf(image_bytes, mime):
            # `Gasto.foto` é ImageField: o PDF entra como a primeira página
            # renderizada, que é o que a tela do gasto mostra. O arquivo
            # original continua na origem (a URL que o cliente mandou).
            paginas = paginas_do_pdf(image_bytes, limite=1)
            if paginas:
                foto_file = ContentFile(paginas[0], name='comprovante.png')
        else:
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
        'ia_tentativas': ia.get('tentativas'),
        'ia_faltou': ia.get('faltou') or [],
        'comprovante': ia.get('anexo') or ('imagem' if image_bytes else ''),
        'categoria': categoria_gasto,
        'aviso': aviso,
    })
