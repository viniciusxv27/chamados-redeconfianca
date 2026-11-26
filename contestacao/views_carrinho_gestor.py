"""Carrinho do gestor em /contestacao/gerenciar/: marcar várias contestações pendentes como
"de acordo" (aprovar), "aprovar e contestar" ou "negar" (rejeitar) e aplicar tudo de uma vez.

O carrinho fica no servidor (DecisaoNoCarrinho, uma linha por gestor e contestação), então
sobrevive a recarregar a página, trocar de filtro ou de computador. Aplicar faz exatamente o que
os botões de cada contestação fazem (Contestation.approve / reject + histórico) e só tira do
carrinho o que foi aplicado; o que não deu fica lá com o motivo.
"""
import json

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from .models import Contestation, ContestationHistory, DecisaoNoCarrinho
from .views import _apply_sector_visibility_filter, _can_manage_contestations

DECISOES = dict(DecisaoNoCarrinho.DECISOES)
MAXIMO_POR_VEZ = 300


def _corpo(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return {}


def _ids(valor):
    saida = []
    for item in valor or []:
        try:
            saida.append(int(item))
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(saida))[:MAXIMO_POR_VEZ]


def _visiveis(user, ids):
    """As contestações destes ids que o gestor enxerga (o filtro por loja percorre o queryset: vai só o necessário)."""
    return _apply_sector_visibility_filter(Contestation.objects.filter(pk__in=ids).select_related('exclusion'), user)


def _item(linha):
    c, e = linha.contestation, linha.contestation.exclusion
    return {
        'id': c.pk, 'decisao': linha.decisao, 'decisao_rotulo': linha.get_decisao_display(),
        'observacao': linha.observacao, 'pendente': c.status == 'pending',
        'status': c.get_status_display(), 'vendedor': e.vendedor, 'filial': e.filial, 'pilar': e.pilar,
        'receita': str(e.receita) if e.receita is not None else '', 'cliente': e.nome_cliente or '',
        'data_venda': str(e.data_venda or ''), 'valor': e.record_type == 'pagamento',
    }


def _carrinho(user):
    linhas = (DecisaoNoCarrinho.objects.filter(gestor=user)
              .select_related('contestation', 'contestation__exclusion'))
    return [_item(linha) for linha in linhas]


def _resposta(user, **extra):
    itens = _carrinho(user)
    conta = {d: sum(1 for i in itens if i['decisao'] == d) for d in DECISOES}
    return JsonResponse({'ok': True, 'itens': itens, 'conta': conta, 'total': len(itens), **extra})


def _sem_permissao():
    return JsonResponse({'ok': False, 'erro': 'Sem permissão para gerenciar contestações.'}, status=403)


@login_required
@require_GET
def carrinho(request):
    if not _can_manage_contestations(request.user):
        return _sem_permissao()
    return _resposta(request.user)


@login_required
@require_POST
def carrinho_salvar(request):
    """Põe (ou troca a decisão de) várias contestações no carrinho: {ids, decisao, observacao?}."""
    if not _can_manage_contestations(request.user):
        return _sem_permissao()
    dados = _corpo(request)
    decisao = dados.get('decisao') or ''
    if decisao not in DECISOES:
        return JsonResponse({'ok': False, 'erro': 'Escolha de acordo, aprovar e contestar ou negar.'}, status=400)
    ids = _ids(dados.get('ids'))
    if not ids:
        return JsonResponse({'ok': False, 'erro': 'Marque ao menos uma contestação.'}, status=400)
    observacao = dados.get('observacao')
    pendentes = set(_visiveis(request.user, ids).filter(status='pending').values_list('pk', flat=True))
    ignoradas = [i for i in ids if i not in pendentes]
    with transaction.atomic():
        for pk in ids:
            if pk not in pendentes:
                continue
            linha, criada = DecisaoNoCarrinho.objects.get_or_create(
                gestor=request.user, contestation_id=pk,
                defaults={'decisao': decisao, 'observacao': (observacao or '').strip()})
            if not criada:
                linha.decisao = decisao
                if observacao is not None:
                    linha.observacao = observacao.strip()
                linha.save(update_fields=['decisao', 'observacao', 'atualizado_em'])
    return _resposta(request.user, ignoradas=ignoradas)


@login_required
@require_POST
def carrinho_observacao(request):
    """Muda só o parecer de um item: {id, observacao}."""
    if not _can_manage_contestations(request.user):
        return _sem_permissao()
    dados = _corpo(request)
    ids = _ids([dados.get('id')])
    texto = str(dados.get('observacao') or '').strip()[:5000]
    alterados = DecisaoNoCarrinho.objects.filter(gestor=request.user, contestation_id__in=ids).update(observacao=texto)
    if not alterados:
        return JsonResponse({'ok': False, 'erro': 'Esta contestação não está no carrinho.'}, status=404)
    return _resposta(request.user)


@login_required
@require_POST
def carrinho_remover(request):
    """Tira itens do carrinho: {ids} (ou {todos: true})."""
    if not _can_manage_contestations(request.user):
        return _sem_permissao()
    dados = _corpo(request)
    linhas = DecisaoNoCarrinho.objects.filter(gestor=request.user)
    if not dados.get('todos'):
        linhas = linhas.filter(contestation_id__in=_ids(dados.get('ids')))
    linhas.delete()
    return _resposta(request.user)


def _aplicar(c, user, decisao, notas):
    """O mesmo que approve_contestation / approve_and_contest_contestation / reject_contestation."""
    if decisao == DecisaoNoCarrinho.REJEITAR:
        c.reject(user, notas)
        parecer = notas.strip() if notas else 'Sem parecer informado.'
        c.exclusion.observacao = f'Parecer da rejeição: {parecer}'
        c.exclusion.save(update_fields=['observacao'])
        ContestationHistory.objects.create(contestation=c, action='rejected', user=user,
                                           notes=notas, extra_data={'pelo_carrinho': True})
    else:
        c.approve(user, notas, approval_mode=decisao)
        ContestationHistory.objects.create(contestation=c, action=decisao, user=user,
                                           notes=notas, extra_data={'pelo_carrinho': True})


@login_required
@require_POST
def carrinho_aplicar(request):
    """Aplica as decisões do carrinho ({ids} para só algumas; sem ids, todas).

    Cada contestação é travada e conferida na hora: só pendente e dentro do escopo do gestor.
    O parecer é o que está na tela ({observacoes: {id: texto}}) ou o guardado; em branco, {observacao_padrao}.
    """
    if not _can_manage_contestations(request.user):
        return _sem_permissao()
    dados = _corpo(request)
    padrao = str(dados.get('observacao_padrao') or '').strip()
    na_tela = {str(k): str(v or '').strip()[:5000] for k, v in (dados.get('observacoes') or {}).items()} \
        if isinstance(dados.get('observacoes'), dict) else {}
    linhas = DecisaoNoCarrinho.objects.filter(gestor=request.user)
    if dados.get('ids'):
        linhas = linhas.filter(contestation_id__in=_ids(dados.get('ids')))
    linhas = list(linhas.order_by('criado_em', 'id')[:MAXIMO_POR_VEZ])
    visiveis = set(_visiveis(request.user, [ln.contestation_id for ln in linhas]).values_list('pk', flat=True))
    aplicadas, falhas = [], []
    for linha in linhas:
        pk = linha.contestation_id
        try:
            with transaction.atomic():
                c = (Contestation.objects.select_for_update(of=('self',)).select_related('exclusion')
                     .filter(pk=pk).first())
                if c is None or pk not in visiveis:
                    falhas.append({'id': pk, 'motivo': 'Fora do seu escopo (ou não existe mais).'})
                    linha.delete()
                    continue
                if c.status != 'pending':
                    falhas.append({'id': pk, 'motivo': f'Já estava "{c.get_status_display()}"'
                                   + (f' por {c.reviewed_by.full_name}' if c.reviewed_by_id else '') + '.'})
                    linha.delete()
                    continue
                _aplicar(c, request.user, linha.decisao, na_tela.get(str(pk), linha.observacao) or padrao)
                linha.delete()
                aplicadas.append({'id': pk, 'decisao': linha.decisao})
        except Exception as exc:  # noqa: BLE001 — um item com problema não derruba os outros (fica no carrinho)
            falhas.append({'id': pk, 'motivo': f'Não deu para aplicar: {exc}', 'ficou': True})
    return _resposta(request.user, aplicadas=aplicadas, falhas=falhas)
