"""Módulo Mercadinho no app (/api/mobile/v1/m/mercadinho/): o /prizes/ do portal no celular.

Regras de onde vieram (prizes/views.py e templates):
- Entrada: ``marketplace_view`` só tem @login_required (prizes/views.py:21) e o item
  "Mercadinho" do menu aparece para todo logado (templates/base.html:488, sem {% if %}).
- Vitrine: ``Prize.objects.filter(is_active=True)`` com os filtros de categoria, busca por
  nome e a ordenação da tela (prizes/views.py:24-46); categorias ativas (views.py:23).
- Saldo: a tela /prizes/ mostra ``request.user.balance_cs`` para TODO mundo
  (views.py:56 + marketplace.html:53-60, sem condição de hierarquia) — diferente da home.
- Resgatar: a PRÓPRIA view da web (``redeem_prize``, views.py:67): disponível
  (ativo, validade, estoque), saldo ≥ valor final, cria o Redemption PENDENTE, debita o
  saldo e grava o CSTransaction REDEMPTION. A confirmação é do app (o modal da web,
  marketplace.html:313).
- Meus resgates: ``Redemption.objects.filter(user=request.user)`` (views.py:274). Prazo de
  retirada na SEDE: ``pickup_deadline`` + ``pickup_notified_at`` (o popup da home,
  core/views.py:110).
- Cancelar: a PRÓPRIA view da web (``cancel_redemption``, views.py:568). A tela da web só
  oferece o botão no PENDENTE (redemption_history.html:133); aqui também.
- Sugerir produto: a PRÓPRIA view da web (``sugerir_produto``, views.py:829).

Gestão (cadastrar prêmio, estoque, aprovar/entregar resgate, avisar retirada, descontos,
sugestões) fica no portal: o app só mostra o atalho para quem ``can_manage_prizes``.
"""
from django.contrib.messages import get_messages
from django.contrib.messages.storage.cookie import CookieStorage
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.urls import path
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from prizes import views as web
from prizes.models import STATUS_QUE_OCUPAM_ESTOQUE, Prize, PrizeCategory, Redemption

from .. import dados
from ..autenticacao import TokenMobileAuthentication
from . import permissao


def pode_entrar(user):
    # prizes/views.py:21 — só @login_required; o menu (base.html:488) aparece para todo logado.
    return bool(user and user.is_authenticated and user.is_active)


MODULO = {'rotulo': 'Mercadinho', 'descricao': 'Troque suas C$ por prêmios',
          'icone': 'storefront-outline', 'cor': '#27AE60', 'pode_entrar': pode_entrar}

AUTH = [TokenMobileAuthentication]
PERM = [permissao('mercadinho')]
POR_PAGINA = 20

# A ordenação da tela (prizes/views.py:39-44).
ORDENACAO = {
    'relevancia': ('-has_discount', '-priority', 'name'),
    'menor_valor': ('value_cs', 'name'),
    'maior_valor': ('-value_cs', 'name'),
    'recentes': ('-created_at',),
}
# A mesma régua da web: pendente, aprovado e entregue ocupam o estoque (prizes/models.py).
STATUS_CONTA_ESTOQUE = STATUS_QUE_OCUPAM_ESTOQUE


# ── Apoio ─────────────────────────────────────────────────────────────────────

def _num(valor):
    return None if valor is None else float(valor)


def _pagina(request, itens):
    try:
        numero = max(int(request.query_params.get('pagina') or 1), 1)
    except ValueError:
        numero = 1
    paginador = Paginator(itens, POR_PAGINA)
    if numero > paginador.num_pages:
        return [], {'pagina': numero, 'total': paginador.count, 'tem_mais': False}
    pagina = paginador.page(numero)
    return list(pagina.object_list), {'pagina': numero, 'total': paginador.count, 'tem_mais': pagina.has_next()}


def _chamar_web(request, view, *args):
    """Chama a view do portal com o HttpRequest desta chamada. As mensagens (messages.*)
    que ela deixa vão para um armazenamento só desta chamada — não sobram para a próxima
    tela da web — e voltam junto com a resposta."""
    req = request._request
    original = getattr(req, '_messages', None)
    req._messages = CookieStorage(req)
    try:
        resposta = view(req, *args)
        mensagens = [(m.level_tag, str(m)) for m in get_messages(req)]
    finally:
        if original is None:
            del req._messages
        else:
            req._messages = original
    return resposta, mensagens


def _json_da_web(resposta):
    import json
    try:
        return json.loads(resposta.content or b'{}')
    except ValueError:
        return {}


def _categoria(cat):
    return {'id': cat.pk, 'nome': cat.name} if cat else None


def _restantes(premio, aprovados):
    """Quantos ainda dá para resgatar (None = ilimitado). Mesma conta de Prize.available."""
    if premio.unlimited_stock:
        return None
    return max(premio.stock - aprovados, 0)


def _premio_resumo(request, p, aprovados, saldo):
    disponivel = p.available
    return {
        'id': p.pk,
        'nome': p.name,
        'categoria': _categoria(p.category),
        'imagem': dados.url_absoluta(request, p.image),
        'valor': _num(p.value_cs),
        'valor_final': _num(p.final_value),
        'desconto_percentual': _num(p.discount_percentage) if (p.has_discount and p.discounted_value) else None,
        'destaque': p.priority in ('ALTA', 'DESTAQUE'),
        'restantes': _restantes(p, aprovados),
        'disponivel': disponivel,
        # O botão da web: disponível e saldo ≥ valor final (marketplace.html:236-237).
        'saldo_suficiente': saldo >= p.final_value,
        'valido_ate': p.valid_until.isoformat() if p.valid_until else None,
    }


def _aprovados(premio_ids):
    linhas = (Prize.objects.filter(pk__in=premio_ids)
              .annotate(n=Count('redemption', filter=Q(redemption__status__in=STATUS_CONTA_ESTOQUE)))
              .values_list('pk', 'n'))
    return dict(linhas)


def _premio_visivel(pk):
    # O resgate só acha prêmio ativo (prizes/views.py:69); o detalhe segue a mesma régua.
    premio = Prize.objects.visiveis().select_related('category').filter(pk=pk, is_active=True).first()
    if premio is None:
        raise Http404('Prêmio não encontrado.')
    return premio


def _aguardando_retirada(user):
    # O popup da home (core/views.py:110-115).
    return (Redemption.objects.filter(user=user, status='APROVADO', pickup_notified_at__isnull=False,
                                      pickup_deadline__isnull=False)
            .select_related('prize').order_by('pickup_deadline'))


STATUS_ROTULO = dict(Redemption.STATUS_CHOICES)


def _resgate(request, r):
    valor = r.final_value if r.final_value is not None else r.prize.value_cs
    return {
        'id': r.pk,
        'premio': {'id': r.prize_id, 'nome': r.prize.name, 'imagem': dados.url_absoluta(request, r.prize.image)},
        'status': r.status,
        'status_nome': STATUS_ROTULO.get(r.status, r.status),
        'valor': _num(valor),
        'resgatado_em': dados.iso(r.redeemed_at),
        'aprovado_em': dados.iso(r.approved_at),
        'entregue_em': dados.iso(r.delivered_at),
        'retirar_ate': r.pickup_deadline.isoformat() if r.pickup_deadline else None,
        'aguardando_retirada': r.awaiting_pickup,
        'observacao': (r.delivery_notes or r.notes or '').strip() or None,
        # A tela da web só oferece cancelar no PENDENTE (redemption_history.html:133).
        'pode_cancelar': r.status == 'PENDENTE',
    }


# ── Vitrine ───────────────────────────────────────────────────────────────────

@api_view(['GET'])
@authentication_classes(AUTH)
@permission_classes(PERM)
def vitrine(request):
    """?busca= &categoria=<id> &ordem=relevancia|menor_valor|maior_valor|recentes &pagina="""
    user = request.user
    qs = Prize.objects.visiveis().filter(is_active=True).select_related('category')
    categoria = (request.query_params.get('categoria') or '').strip()
    if categoria.isdigit():
        qs = qs.filter(category_id=int(categoria))
    busca = (request.query_params.get('busca') or '').strip()
    if busca:
        qs = qs.filter(name__icontains=busca)
    ordem = request.query_params.get('ordem') or 'relevancia'
    if ordem not in ORDENACAO:
        ordem = 'relevancia'
    qs = qs.order_by(*ORDENACAO[ordem])

    itens, meta = _pagina(request, qs)
    aprovados = _aprovados([p.pk for p in itens])
    saldo = user.balance_cs
    saida = {
        'resultados': [_premio_resumo(request, p, aprovados.get(p.pk, 0), saldo) for p in itens],
        **meta,
    }
    if meta['pagina'] == 1:
        retirar = list(_aguardando_retirada(user))
        saida.update({
            'saldo': _num(saldo),
            'categorias': [_categoria(c) for c in PrizeCategory.objects.filter(active=True)],
            'aguardando_retirada': [{'id': r.pk, 'premio': r.prize.name,
                                     'retirar_ate': r.pickup_deadline.isoformat()} for r in retirar],
            'pendentes': Redemption.objects.filter(user=user, status='PENDENTE').count(),
            'pode_gerenciar': bool(user.can_manage_prizes()),
        })
    return Response(saida)


@api_view(['GET'])
@authentication_classes(AUTH)
@permission_classes(PERM)
def premio(request, pk):
    p = _premio_visivel(pk)
    saldo = request.user.balance_cs
    saida = _premio_resumo(request, p, _aprovados([p.pk]).get(p.pk, 0), saldo)
    saida.update({
        'descricao': p.description or None,
        'termos': p.terms or None,
        'saldo': _num(saldo),
        'caminho_web': '/prizes/',
    })
    return Response(saida)


@api_view(['POST'])
@authentication_classes(AUTH)
@permission_classes(PERM)
def resgatar(request, pk):
    """Resgata pela view da web (prizes/views.py:67): mesmas validações, débito e movimentação de C$."""
    _premio_visivel(pk)                         # inativo/inexistente → 404 (a web dá 404 também)
    resposta, _msgs = _chamar_web(request, web.redeem_prize, pk)
    corpo = _json_da_web(resposta)
    if resposta.status_code != 200 or not corpo.get('success'):
        return Response({'erro': corpo.get('error') or 'Não foi possível resgatar.'}, status=status.HTTP_400_BAD_REQUEST)
    request.user.refresh_from_db(fields=['balance_cs'])
    ultimo = (Redemption.objects.filter(user=request.user, prize_id=pk).select_related('prize')
              .order_by('-redeemed_at', '-pk').first())
    return Response({
        'mensagem': 'Prêmio resgatado! Agora é aguardar a aprovação.',
        'saldo': _num(request.user.balance_cs),
        'resgate': _resgate(request, ultimo) if ultimo else None,
    }, status=status.HTTP_201_CREATED)


# ── Meus resgates ─────────────────────────────────────────────────────────────

@api_view(['GET'])
@authentication_classes(AUTH)
@permission_classes(PERM)
def resgates(request):
    """Os resgates da própria pessoa (prizes/views.py:274). ?situacao=PENDENTE|APROVADO|ENTREGUE|CANCELADO"""
    base = Redemption.objects.filter(user=request.user).select_related('prize').order_by('-redeemed_at', '-pk')
    situacao = (request.query_params.get('situacao') or '').strip().upper()
    qs = base.filter(status=situacao) if situacao in STATUS_ROTULO else base
    itens, meta = _pagina(request, qs)
    saida = {'resultados': [_resgate(request, r) for r in itens], **meta}
    if meta['pagina'] == 1:
        contagem = dict(base.order_by().values_list('status').annotate(n=Count('pk')))
        saida['contagem'] = {s: contagem.get(s, 0) for s in STATUS_ROTULO}
        saida['saldo'] = _num(request.user.balance_cs)
    return Response(saida)


@api_view(['POST'])
@authentication_classes(AUTH)
@permission_classes(PERM)
def cancelar(request, pk):
    """Cancela o próprio resgate pendente pela view da web (prizes/views.py:568), que devolve o C$."""
    r = Redemption.objects.filter(pk=pk, user=request.user).first()
    if r is None:
        raise Http404('Resgate não encontrado.')
    if r.status != 'PENDENTE':
        return Response({'erro': 'Só dá para cancelar resgate pendente.'}, status=status.HTTP_400_BAD_REQUEST)
    resposta, _msgs = _chamar_web(request, web.cancel_redemption, pk)
    corpo = _json_da_web(resposta)
    if resposta.status_code != 200 or not corpo.get('success'):
        return Response({'erro': corpo.get('error') or 'Não foi possível cancelar.'}, status=status.HTTP_400_BAD_REQUEST)
    request.user.refresh_from_db(fields=['balance_cs'])
    r = Redemption.objects.select_related('prize').get(pk=pk)
    return Response({'mensagem': corpo.get('message') or 'Resgate cancelado.',
                     'saldo': _num(request.user.balance_cs), 'resgate': _resgate(request, r)})


# ── Sugerir produto ───────────────────────────────────────────────────────────

@api_view(['POST'])
@authentication_classes(AUTH)
@permission_classes(PERM)
def sugerir(request):
    """{link, nome?, observacao?} pela view da web (prizes/views.py:829): link http(s), até 5 em análise."""
    req = request._request
    original_post = req.POST
    from django.http import QueryDict
    corpo = QueryDict(mutable=True)
    for campo in ('link', 'nome', 'observacao'):
        corpo[campo] = str(request.data.get(campo) or '')
    req.POST = corpo
    try:
        _resposta, mensagens = _chamar_web(request, web.sugerir_produto)
    finally:
        req.POST = original_post
    erro = next((texto for nivel, texto in mensagens if nivel == 'error'), None)
    if erro:
        return Response({'erro': erro}, status=status.HTTP_400_BAD_REQUEST)
    sucesso = next((texto for nivel, texto in mensagens if nivel == 'success'), 'Sugestão enviada.')
    return Response({'mensagem': sucesso}, status=status.HTTP_201_CREATED)


urlpatterns = [
    path('', vitrine, name='m_mercadinho'),
    path('premios/<int:pk>/', premio, name='m_mercadinho_premio'),
    path('premios/<int:pk>/resgatar/', resgatar, name='m_mercadinho_resgatar'),
    path('resgates/', resgates, name='m_mercadinho_resgates'),
    path('resgates/<int:pk>/cancelar/', cancelar, name='m_mercadinho_cancelar'),
    path('sugerir/', sugerir, name='m_mercadinho_sugerir'),
]
