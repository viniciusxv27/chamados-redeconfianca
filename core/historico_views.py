"""Telas do Histórico de Atividades. As regras ficam em `core/historico.py`."""
import csv
import json
from datetime import datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import historico
from .models import LimpezaHistorico, SystemLog

User = get_user_model()

POR_PAGINA = 50


def _negar(request):
    messages.error(request, 'Você não tem permissão para acessar esta área.')
    return redirect('home')


def _ip(request):
    encaminhado = request.META.get('HTTP_X_FORWARDED_FOR')
    return (encaminhado.split(',')[0].strip() if encaminhado else request.META.get('REMOTE_ADDR')) or None


def _estatisticas():
    """Tudo pelo índice de `created_at`, numa consulta só; o total é a única varredura."""
    agora = timezone.localtime()
    hoje = timezone.make_aware(datetime.combine(agora.date(), time.min))
    ontem = hoje - timedelta(days=1)
    semana = hoje - timedelta(days=agora.weekday())
    mes = hoje.replace(day=1)

    numeros = SystemLog.objects.filter(created_at__gte=min(ontem, semana, mes)).aggregate(
        today=Count('id', filter=Q(created_at__gte=hoje)),
        yesterday=Count('id', filter=Q(created_at__gte=ontem, created_at__lt=hoje)),
        this_week=Count('id', filter=Q(created_at__gte=semana)),
        this_month=Count('id', filter=Q(created_at__gte=mes)),
    )
    numeros['total_logs'] = SystemLog.objects.count()
    return numeros


def _anexar_posicoes(logs):
    """{id do log: posição}. Só é chamada para quem pode ver o mapa."""
    from maps.servicos import posicoes_perto
    return posicoes_perto(
        (log.id, log.user_id, log.created_at) for log in logs if log.user_id)


def _resumo_da_pessoa(pessoa_id, ver_mapa):
    """Cabeçalho do filtro por pessoa. A última posição só vem para quem vê o mapa."""
    pessoa = User.objects.select_related('sector').filter(id=pessoa_id).first()
    if not pessoa:
        return None
    resumo = {'pessoa': pessoa, 'ultima_posicao': None}
    if ver_mapa:
        from maps.servicos import ultima_posicao
        resumo['ultima_posicao'] = ultima_posicao(pessoa.id)
    return resumo


@login_required
def activity_history(request):
    if not historico.pode_ver_historico(request.user):
        return _negar(request)

    from maps.permissions import pode_ver_mapa
    ver_mapa = pode_ver_mapa(request.user)

    logs, filtros = historico.filtrar(
        SystemLog.objects.select_related('user', 'user__sector'), request.GET)
    logs = logs.order_by('-created_at', '-id')

    pagina = Paginator(logs, POR_PAGINA).get_page(request.GET.get('page'))
    itens = list(pagina.object_list)

    do_cadastro = historico.diferencas_de_cadastro(itens)
    # A localização nem é consultada sem permissão: o que não sai do banco não vaza
    # por descuido do template.
    posicoes = _anexar_posicoes(itens) if ver_mapa else {}

    for log in itens:
        log.acao = historico.dados_da_acao(log.action_type)
        titulo, mudancas = historico.diferencas_do_texto(log.description)
        log.titulo = titulo
        log.mudancas = do_cadastro.get(log.id) or mudancas
        log.posicao = posicoes.get(log.id)
        log.em_param = timezone.localtime(log.created_at).strftime('%Y-%m-%dT%H:%M:%S')

    trinta_dias = timezone.now() - timedelta(days=30)
    recentes = SystemLog.objects.filter(created_at__gte=trinta_dias)
    action_stats = [
        {**historico.dados_da_acao(a['action_type']), 'count': a['count']}
        for a in recentes.values('action_type').annotate(count=Count('id')).order_by('-count')[:8]
    ]
    maior = action_stats[0]['count'] if action_stats else 0
    for a in action_stats:
        a['largura'] = round(a['count'] * 100 / maior) if maior else 0

    top_users = list(recentes.filter(user__isnull=False)
                     .values('user__id', 'user__first_name', 'user__last_name', 'user__username')
                     .annotate(activity_count=Count('id')).order_by('-activity_count')[:8])

    # Pelo `last_login` do cadastro, não pelo log: depois de uma limpeza, quem não
    # entra há mais de 1 ano apareceria como "nunca entrou".
    ativos = User.objects.filter(is_active=True)
    nunca = ativos.filter(last_login__isnull=True).select_related('sector').order_by('date_joined')
    limite_inativo = timezone.now() - timedelta(days=30)
    inativos = ativos.filter(last_login__lt=limite_inativo).select_related('sector').order_by('last_login')
    agora = timezone.now()
    inativos_top = [{'pessoa': p, 'dias': (agora - p.last_login).days} for p in inativos[:10]]

    pessoas = list(ativos.order_by('first_name', 'last_name'))
    resumo_pessoa = _resumo_da_pessoa(int(filtros['user']), ver_mapa) if filtros['user'] else None
    # Ex-colaborador continua no log: sem isso, o seletor perderia a pessoa filtrada.
    if resumo_pessoa and not resumo_pessoa['pessoa'].is_active:
        pessoas.insert(0, resumo_pessoa['pessoa'])

    parametros = request.GET.copy()
    parametros.pop('page', None)
    hoje = timezone.localdate()

    return render(request, 'core/activity_history.html', {
        'dias': historico.montar_linha_do_tempo(itens, hoje=hoje),
        'page_obj': pagina,
        'stats': _estatisticas(),
        'nunca_total': nunca.count(),
        'nunca_lista': nunca[:10],
        'inativos_total': inativos.count(),
        'inativos_top': inativos_top,
        'action_stats': action_stats,
        'top_users': top_users,
        'pessoas': pessoas,
        'grupos_acoes': historico.acoes_por_modulo(),
        'modulos': [(c, r) for c, r, _p in historico.MODULOS] + [historico.OUTROS],
        'filtros': filtros,
        'tem_filtro': any(filtros.values()),
        'qs_filtros': parametros.urlencode(),
        'resumo_pessoa': resumo_pessoa,
        'ver_mapa': ver_mapa,
        'pode_limpar': historico.pode_limpar_historico(request.user),
        'atalhos_periodo': [
            ('Hoje', hoje, hoje),
            ('7 dias', hoje - timedelta(days=6), hoje),
            ('30 dias', hoje - timedelta(days=29), hoje),
        ],
    })


@login_required
def export_activity_history(request):
    """CSV com os filtros da tela. Não leva localização: o arquivo sai do controle do mapa."""
    if not historico.pode_ver_historico(request.user):
        return _negar(request)

    logs, _filtros = historico.filtrar(SystemLog.objects.select_related('user'), request.GET)
    logs = logs.order_by('-created_at', '-id')

    resposta = HttpResponse(content_type='text/csv; charset=utf-8')
    resposta['Content-Disposition'] = (
        f'attachment; filename="historico_atividades_{datetime.now():%Y%m%d_%H%M%S}.csv"')
    resposta.write('﻿')  # para o Excel reconhecer UTF-8

    escritor = csv.writer(resposta)
    escritor.writerow(['Data/Hora', 'Usuário', 'Módulo', 'Tipo de Ação', 'Descrição', 'IP', 'User Agent'])
    for log in logs.iterator(chunk_size=2000):
        acao = historico.dados_da_acao(log.action_type)
        escritor.writerow([
            timezone.localtime(log.created_at).strftime('%d/%m/%Y %H:%M:%S'),
            log.user.get_full_name() if log.user else 'Sistema',
            acao['modulo_rotulo'],
            acao['rotulo'],
            log.description,
            log.ip_address or '',
            log.user_agent[:100] if log.user_agent else '',
        ])
    return resposta


# ─── Limpeza ──────────────────────────────────────────────────────────────────

@login_required
def limpar_historico(request):
    """Prévia. Abrir esta tela não apaga nada; só o POST confirmado começa."""
    if not historico.pode_limpar_historico(request.user):
        return _negar(request)

    return render(request, 'core/activity_history_limpar.html', {
        'previa': historico.previa_da_limpeza(),
        'palavra': historico.PALAVRA_CONFIRMACAO,
        'lote': historico.LOTE_LIMPEZA,
        'anteriores': LimpezaHistorico.objects.select_related('executado_por')[:10],
    })


@login_required
@require_POST
def limpar_iniciar(request):
    if not historico.pode_limpar_historico(request.user):
        return JsonResponse({'erro': 'Acesso restrito.'}, status=403)

    try:
        dados = json.loads(request.body or b'{}')
    except ValueError:
        dados = {}

    if dados.get('confirmacao') != historico.PALAVRA_CONFIRMACAO:
        return JsonResponse({'erro': f'Digite {historico.PALAVRA_CONFIRMACAO} para confirmar.'}, status=400)

    # A tela confirmou um corte; virou o dia entre abrir e confirmar, a prévia mudou
    # e a pessoa precisa ver o número novo antes de apagar.
    corte = historico.corte_da_limpeza()
    if dados.get('corte') != timezone.localtime(corte).strftime('%Y-%m-%d'):
        return JsonResponse({'erro': 'A prévia ficou antiga. Recarregue a tela e confira de novo.'}, status=409)

    ativa = historico.encerrar_interrompidas(request.user)
    if ativa:
        return JsonResponse(
            {'erro': f'{ativa.executado_por_nome} está limpando o histórico agora. Aguarde terminar.'},
            status=409)

    if not historico.registros_a_limpar(corte).exists():
        return JsonResponse({'erro': 'Não há registros com mais de 1 ano.'}, status=400)

    limpeza = historico.iniciar_limpeza(
        request.user, ip=_ip(request), user_agent=request.META.get('HTTP_USER_AGENT', ''))
    return JsonResponse({'id': limpeza.id, 'previsto': limpeza.previsto, 'removidos': 0})


@login_required
@require_POST
def limpar_lote(request, limpeza_id):
    """Um lote por requisição: a tela repete até a resposta dizer que acabou."""
    if not historico.pode_limpar_historico(request.user):
        return JsonResponse({'erro': 'Acesso restrito.'}, status=403)

    limpeza = LimpezaHistorico.objects.filter(
        pk=limpeza_id, executado_por=request.user,
        situacao=LimpezaHistorico.Situacao.ANDAMENTO).first()
    if not limpeza:
        return JsonResponse({'erro': 'Esta limpeza não está mais em andamento.'}, status=409)

    removidos = historico.apagar_um_lote(limpeza)
    return JsonResponse({
        'lote': removidos,
        'removidos': limpeza.removidos,
        'previsto': limpeza.previsto,
        'concluido': limpeza.situacao == LimpezaHistorico.Situacao.CONCLUIDA,
    })
