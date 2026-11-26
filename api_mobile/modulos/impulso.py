"""Módulo Impulso no app: minha pontuação, ranking do mês, Kanban de metas e as ações do card.

As regras são as do portal, chamadas daqui — nada reescrito:

- entrada: ``impulso.utils.is_impulso_member`` (o mesmo do menu: impulso/context_processors.py:10 e
  users/module_access.py GATES['impulso']);
- recorte do Kanban: ``impulso.views._metas_do_kanban`` (que usa ``_metas_do_usuario``: o gestor vê a equipe);
- quem abre a meta: ``impulso.views._pode_ver_meta`` (fora dela → 404);
- mover de status, marcar item do to-do, comentar, responder convite e avaliar: a própria view do portal
  é chamada (``_chamar_view_do_portal``) — as travas, os avisos e a recorrência são os da web.
"""
import json

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.http import Http404, QueryDict
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, parser_classes, permission_classes
from rest_framework.parsers import FormParser, JSONParser
from rest_framework.response import Response

from impulso.utils import is_impulso_member

from ..autenticacao import TokenMobileAuthentication
from . import impulso_dados as fmt
from . import permissao

User = get_user_model()


def pode_entrar(user):
    return is_impulso_member(user)


MODULO = {'rotulo': 'Impulso', 'descricao': 'Metas, pontuação e ranking', 'icone': 'rocket-outline',
          'cor': '#FF6B35', 'pode_entrar': pode_entrar}

# O ranking ao vivo calcula a pontuação de todo o Escritório (38 pessoas, ~660 consultas: 26 s no banco
# de dev). O portal faz a conta a cada abertura do acompanhamento; no celular ela fica pronta por 3 min.
RANKING_SEGUNDOS = 180


def _api(metodos=('GET',)):
    def decorar(funcao):
        funcao = parser_classes([JSONParser, FormParser])(funcao)
        funcao = permission_classes([permissao('impulso')])(funcao)
        funcao = authentication_classes([TokenMobileAuthentication])(funcao)
        return api_view(list(metodos))(funcao)
    return decorar


def _erro(mensagem, codigo=status.HTTP_400_BAD_REQUEST):
    return Response({'erro': mensagem}, status=codigo)


def _chamar_view_do_portal(request, view, *args, post=None):
    """Roda a view web do Impulso com a pessoa do token e devolve (resposta, [(nível, texto)]).

    É o jeito de usar exatamente a regra do portal quando ela está escrita dentro da view (permissão,
    validação, avisos, recorrência). As mensagens que a view deixaria na tela são lidas (e consumidas)
    aqui: nada fica guardado na sessão.
    """
    dj = request._request
    dj.user = request.user
    dj.method = 'POST'
    corpo = QueryDict(mutable=True)
    for chave, valor in (post or {}).items():
        corpo[chave] = '' if valor is None else str(valor)
    dj.POST = corpo
    resposta = view(dj, *args)
    avisos = [(m.level_tag, str(m)) for m in messages.get_messages(dj)]
    return resposta, avisos


def _resposta_json_da_view(resposta):
    try:
        conteudo = json.loads(resposta.content or b'{}')
    except ValueError:
        conteudo = {}
    if resposta.status_code == 200 and conteudo.get('ok'):
        return None, conteudo
    return conteudo.get('error') or 'Não foi possível concluir agora.', conteudo


def _meta_visivel(request, meta_id, qs=None):
    """A meta, se esta pessoa pode abri-la no portal (impulso/views.py `_pode_ver_meta`); senão 404."""
    from impulso.models import Meta
    from impulso.views import _pode_ver_meta

    meta = get_object_or_404(qs if qs is not None else Meta.objects.all(), pk=meta_id)
    if not _pode_ver_meta(request.user, meta):
        raise Http404
    return meta


# ── Resumo e ranking ──────────────────────────────────────────────────────────

def _periodo():
    from impulso.views import _periodo_do_acompanhamento

    inicio, fim, mes_ciclo = _periodo_do_acompanhamento()
    return inicio, fim, mes_ciclo, {
        'inicio': inicio.isoformat(), 'fim': fim.isoformat(), 'rotulo': fmt.nome_do_mes(inicio),
        # "Mês em conferência": o ciclo ainda não finalizou o mês que já acabou (acompanhamento.html:17).
        'em_conferencia': bool(mes_ciclo and fim < timezone.localdate()),
    }


def _ranking_do_periodo(inicio, fim):
    """O ranking do acompanhamento (impulso/views.py `acompanhamento`): mesma gente, mesma conta, mesma ordem."""
    chave = f'api_mobile:impulso:ranking:{inicio:%Y%m%d}:{fim:%Y%m%d}'
    guardado = cache.get(chave)
    if guardado is not None:
        return guardado

    from impulso.scoring import calcular_pontuacao, ordenar_ranking
    from impulso.utils import get_colaboradores

    linhas = ordenar_ranking([{'user': c, 'dados': calcular_pontuacao(c, inicio=inicio, fim=fim)}
                              for c in get_colaboradores()])
    pronto = {
        'calculado_em': timezone.now().isoformat(),
        'linhas': [{
            'id': l['user'].pk, 'nome': l['user'].get_full_name() or l['user'].email,
            'percentual': fmt.numero(l['dados']['percentual']), 'faixa': l['dados']['faixa'],
            'confiar': fmt.numero(l['dados']['confiar']), 'conectar': fmt.numero(l['dados']['conectar']),
            'inovar': fmt.numero(l['dados']['inovar']), 'total': fmt.numero(l['dados']['total']),
            'empatou': l['empatou'], 'desempate': l['dados']['desempate'],
        } for l in linhas],
    }
    cache.set(chave, pronto, RANKING_SEGUNDOS)
    return pronto


@_api()
def resumo(request):
    """Minha pontuação do mês em conferência (a mesma do topo de /impulso/acompanhamento/) e o meu Kanban em números."""
    from django.db.models import Q

    from impulso.models import Meta, ParceriaMeta
    from impulso.scoring import calcular_pontuacao
    from impulso.utils import get_colaboradores, is_impulso_manager

    user = request.user
    inicio, fim, _mes, periodo = _periodo()
    gestor = is_impulso_manager(user)
    hoje = timezone.localdate()

    minhas = (Meta.objects.filter(Q(colaborador=user) | Q(participantes=user), aprovacao=Meta.Aprovacao.APROVADA)
              .exclude(status=Meta.Status.CONCLUIDA).distinct())
    contagem = {
        'abertas': minhas.count(),
        'atrasadas': minhas.filter(prazo__lt=hoje).count(),
        'vencem_hoje': minhas.filter(prazo=hoje).count(),
        'entregues': minhas.filter(status=Meta.Status.ENTREGUE).count(),
    }
    # Contador do sino do Kanban (impulso/views.py `metas_kanban`): o gestor vê o que decide; o colaborador, o que pediu.
    pendentes = Meta.objects.filter(aprovacao=Meta.Aprovacao.PENDENTE)
    if gestor:
        pendentes = pendentes if user.is_superuser else pendentes.filter(gestor=user)
    else:
        pendentes = pendentes.filter(solicitada_por=user)

    return Response({
        'periodo': periodo,
        'minha': fmt.pontuacao(calcular_pontuacao(user, inicio=inicio, fim=fim)),
        'faixas': fmt.faixas(),
        'metas': contagem,
        'para_avaliar': (Meta.objects.filter(gestor=user, status=Meta.Status.ENTREGUE).count() if gestor else 0),
        'convites': (ParceriaMeta.objects.filter(convidado=user, status=ParceriaMeta.Status.PENDENTE)
                     .exclude(meta__aprovacao=Meta.Aprovacao.RECUSADA).count()),
        'solicitacoes_pendentes': pendentes.count(),
        'is_gestor': gestor,
        # Só quem está no ranking do acompanhamento tem posição (get_colaboradores: Escritório e ADM's LOJAS).
        'no_ranking': get_colaboradores().filter(pk=user.pk).exists(),
    })


@_api()
def ranking(request):
    """Ranking ao vivo do mês — o de /impulso/acompanhamento/, que o portal mostra a todo membro do Impulso."""
    inicio, fim, _mes, periodo = _periodo()
    pronto = _ranking_do_periodo(inicio, fim)
    fotos = {u.pk: u for u in User.objects.filter(pk__in=[l['id'] for l in pronto['linhas']]).only('id', 'profile_picture')}
    linhas, minha = [], None
    for posicao, l in enumerate(pronto['linhas'], start=1):
        sou_eu = l['id'] == request.user.pk
        if sou_eu:
            minha = posicao
        u = fotos.get(l['id'])
        linhas.append({**l, 'posicao': posicao, 'sou_eu': sou_eu, 'faixa': fmt.faixa(l['faixa']),
                       'foto': fmt.dados.url_absoluta(request, getattr(u, 'profile_picture', None)) if u else None})
    return Response({'periodo': periodo, 'calculado_em': pronto['calculado_em'], 'minha_posicao': minha,
                     'total': len(linhas), 'linhas': linhas})


# ── Kanban ────────────────────────────────────────────────────────────────────

def _preparar(metas, user):
    """Novidades e parceria dos cards em lote — as consultas de `impulso.views._marcar_cards`, sem as regras de
    excluir/duplicar que o app não mostra. O to-do vem do prefetch (`itens`)."""
    from impulso.models import Meta, MetaVisualizacao
    from impulso.views import _preparar_cards

    if not metas:
        return
    vistas = {v.meta_id: v.visto_em for v in MetaVisualizacao.objects.filter(user=user, meta__in=[m.id for m in metas])}
    _preparar_cards(metas, user, vistas)
    em_parceria = set(Meta.participantes.through.objects
                      .filter(user_id=user.id, meta_id__in=[m.id for m in metas]).values_list('meta_id', flat=True))
    for m in metas:
        m.em_parceria = m.id in em_parceria and m.colaborador_id != user.id


def _cards(request, metas):
    hoje = timezone.localdate()
    return [fmt.card(request, m, hoje) for m in metas]


def _consulta_do_kanban(request):
    from impulso.views import _metas_do_kanban

    request._request.user = request.user
    qs, f, colaborador_id = _metas_do_kanban(request._request)
    return qs.select_related('colaborador', 'gestor').prefetch_related('itens'), f, colaborador_id


@_api()
def metas(request):
    """O Kanban de /impulso/metas/: mesmos filtros (mes, q, colaborador, criadas_por_mim), mesma ordem e colunas."""
    from impulso import filtros as filtros_impulso
    from impulso.models import Meta
    from impulso.utils import get_colaboradores, is_impulso_manager
    from impulso.views import (CONCLUIDAS_POR_VEZ, _convites_pendentes, _ordem_das_concluidas, _por_entrega_e_prioridade,
                               _por_prazo)

    user = request.user
    gestor = is_impulso_manager(user)
    qs, f, colaborador_id = _consulta_do_kanban(request)

    abertas = _por_entrega_e_prioridade(qs.exclude(status=Meta.Status.CONCLUIDA))
    concluidas_qs = _ordem_das_concluidas(qs.filter(status=Meta.Status.CONCLUIDA))
    total_concluidas = concluidas_qs.count()
    concluidas = list(concluidas_qs[:CONCLUIDAS_POR_VEZ])
    _preparar(abertas + concluidas, user)

    colunas = []
    for st in Meta.KANBAN_STATUSES:
        do_status = concluidas if st == Meta.Status.CONCLUIDA else [m for m in abertas if m.status == st.value]
        e_concluida = st == Meta.Status.CONCLUIDA
        colunas.append({
            'status': st.value,
            'rotulo': fmt.ROTULO_COLUNA[st],
            'total': total_concluidas if e_concluida else len(do_status),
            'restantes': (total_concluidas - len(do_status)) if e_concluida else 0,
            'metas': _cards(request, do_status),
            # "A fazer" repartido por vencimento, como no portal (_por_prazo).
            'grupos': ([{'chave': g['chave'], 'titulo': g['titulo'], 'ids': [m.id for m in g['metas']]}
                        for g in _por_prazo(do_status)] if st == Meta.Status.A_FAZER else None),
        })

    convites = _convites_pendentes(user)
    return Response({
        'colunas': colunas,
        'convites': [{
            'id': c.pk, 'meta': {'id': c.meta_id, 'titulo': c.meta.titulo, 'prazo': c.meta.prazo.isoformat() if c.meta.prazo else None},
            'dono': fmt.pessoa(request, c.meta.colaborador), 'convidado_por': fmt.pessoa(request, c.convidado_por),
            'mensagem': c.mensagem or None,
        } for c in convites],
        'is_gestor': gestor,
        'filtros': {
            'mes': f['mes'], 'q': f['nome'], 'colaborador': colaborador_id,
            'criadas_por_mim': request.query_params.get('criadas_por_mim') == '1',
            'meses': [{'valor': v, 'rotulo': r} for v, r in filtros_impulso.meses_disponiveis()],
            # O seletor de colaborador do portal só aparece para o gestor (metas_kanban: 'colaboradores').
            'colaboradores': ([{'id': c.pk, 'nome': c.get_full_name() or c.email} for c in get_colaboradores()]
                              if gestor else None),
        },
        'url_web': '/impulso/metas/',
    })


@_api()
def metas_concluidas(request):
    """As próximas concluídas (o "Mostrar mais" de impulso/views.py `metas_kanban_concluidas`)."""
    from impulso.models import Meta
    from impulso.views import CONCLUIDAS_POR_VEZ, _ordem_das_concluidas

    try:
        inicio = max(int(request.query_params.get('inicio') or 0), 0)
    except ValueError:
        inicio = 0
    qs, _f, _c = _consulta_do_kanban(request)
    concluidas_qs = _ordem_das_concluidas(qs.filter(status=Meta.Status.CONCLUIDA))
    total = concluidas_qs.count()
    lote = list(concluidas_qs[inicio:inicio + CONCLUIDAS_POR_VEZ])
    _preparar(lote, request.user)
    return Response({'metas': _cards(request, lote), 'proximo': inicio + len(lote),
                     'restantes': max(total - inicio - len(lote), 0)})


# ── Meta ──────────────────────────────────────────────────────────────────────

def _detalhe(request, meta_id, marcar_visto=True):
    from impulso.models import Meta, MetaVisualizacao, ParceriaMeta
    from impulso.utils import e_superadmin

    user = request.user
    meta = _meta_visivel(request, meta_id, Meta.objects.select_related(
        'colaborador', 'gestor', 'avaliado_por', 'created_by'))
    if marcar_visto:
        # Abrir a meta apaga o aviso de novidade do card, como no portal (impulso/views.py `meta_detail`).
        MetaVisualizacao.objects.update_or_create(meta=meta, user=user, defaults={'visto_em': timezone.now()})

    participantes = list(meta.participantes.all())
    responsaveis = {meta.colaborador_id} | {p.id for p in participantes}
    regras = {
        # Kanban: todo card fora da Concluída arrasta; o servidor confere aprovação e quem vê (meta_update_status).
        'pode_mover': meta.vale_pontos and meta.status != Meta.Status.CONCLUIDA,
        'status_possiveis': ([s for s in (Meta.Status.A_FAZER, Meta.Status.EM_ANDAMENTO, Meta.Status.ENTREGUE)
                              if s != meta.status] if meta.vale_pontos and meta.status != Meta.Status.CONCLUIDA else []),
        # A régua de impulso/views.py `meta_item_toggle` (responsáveis, gestor da meta ou superuser).
        'pode_marcar_itens': user.id in responsaveis or meta.gestor_id == user.id or user.is_superuser,
        'pode_comentar': True,                         # quem abre a meta comenta (meta_add_comentario)
        # A régua do formulário "Dar o check" (meta_detail.html:194 + `meta_avaliar`).
        'pode_avaliar': ((meta.gestor_id == user.id or e_superadmin(user))
                         and not meta.is_avaliada and meta.vale_pontos),
    }
    return fmt.meta_detalhe(
        request, meta,
        itens=list(meta.itens.select_related('concluido_por')),
        anexos=list(meta.anexos.select_related('enviado_por')),
        comentarios=list(meta.comentarios.select_related('autor')),
        participantes=participantes,
        parcerias=list(ParceriaMeta.objects.filter(meta=meta).select_related('convidado', 'convidado_por')),
        regras=regras,
    )


@_api()
def meta(request, meta_id):
    return Response(_detalhe(request, meta_id))


@_api(['POST'])
def meta_status(request, meta_id):
    """Mover o card (o arrastar do Kanban): chama impulso.views.meta_update_status."""
    from impulso.models import Meta
    from impulso.views import meta_update_status

    m = _meta_visivel(request, meta_id)
    if m.status == Meta.Status.CONCLUIDA:
        # O portal não deixa arrastar o card concluído (_card_meta.html:9); a view sozinha aceitaria.
        return _erro('Meta concluída não muda de coluna.')
    resposta, _avisos = _chamar_view_do_portal(request, meta_update_status, m.pk,
                                               post={'status': request.data.get('status') or ''})
    erro, conteudo = _resposta_json_da_view(resposta)
    if erro:
        return _erro(erro, resposta.status_code if resposta.status_code >= 400 else 400)
    return Response({'meta': _detalhe(request, m.pk, marcar_visto=False), 'proxima': conteudo.get('proxima') or None})


@_api(['POST'])
def item_marcar(request, item_id):
    """Marca/desmarca um passo do to-do: chama impulso.views.meta_item_toggle."""
    from impulso.models import MetaItem
    from impulso.views import meta_item_toggle

    item = get_object_or_404(MetaItem.objects.select_related('meta'), pk=item_id)
    _meta_visivel(request, item.meta_id)
    resposta, _avisos = _chamar_view_do_portal(request, meta_item_toggle, item.pk)
    erro, conteudo = _resposta_json_da_view(resposta)
    if erro:
        return _erro('Você não pode marcar os passos desta meta.' if resposta.status_code == 403 else erro,
                     resposta.status_code if resposta.status_code >= 400 else 400)
    return Response({'id': item.pk, 'concluido': conteudo['concluido'], 'feitos': conteudo['feitos'],
                     'total': conteudo['total']})


def _primeiro_erro(avisos):
    return next((texto for nivel, texto in avisos if nivel == 'error'), None)


@_api(['POST'])
def meta_comentar(request, meta_id):
    """Comentário na meta: chama impulso.views.meta_add_comentario (avisa a outra ponta como no portal)."""
    from impulso.models import MetaComentario
    from impulso.views import meta_add_comentario

    m = _meta_visivel(request, meta_id)
    texto = (request.data.get('mensagem') or '').strip()
    if not texto:
        return _erro('Escreva o comentário.')
    antes = set(MetaComentario.objects.filter(meta=m, autor=request.user).values_list('id', flat=True))
    _resposta, avisos = _chamar_view_do_portal(request, meta_add_comentario, m.pk, post={'mensagem': texto})
    erro = _primeiro_erro(avisos)
    novo = (MetaComentario.objects.filter(meta=m, autor=request.user).exclude(id__in=antes)
            .select_related('autor').order_by('-id').first())
    if erro or novo is None:
        return _erro(erro or 'Não foi possível comentar agora.')
    return Response(fmt.comentario(request, novo), status=status.HTTP_201_CREATED)


@_api(['POST'])
def meta_avaliar(request, meta_id):
    """O "Dar o check" do gestor (notas 0–5): chama impulso.views.meta_avaliar."""
    from impulso.views import meta_avaliar as view_avaliar

    m = _meta_visivel(request, meta_id)
    if m.is_avaliada:
        return _erro('Esta meta já foi avaliada.')
    _resposta, avisos = _chamar_view_do_portal(request, view_avaliar, m.pk, post={
        'nota_qualidade': request.data.get('nota_qualidade'),
        'nota_prazo': request.data.get('nota_prazo'),
        'avaliacao_comentario': request.data.get('comentario') or '',
    })
    erro = _primeiro_erro(avisos)
    if erro:                                   # quem não avalia, nota fora de 0–5… (a mensagem é a do portal)
        return _erro(erro)
    sucesso = next((texto for nivel, texto in avisos if nivel == 'success'), 'Avaliação registrada.')
    return Response({'mensagem': sucesso, 'meta': _detalhe(request, m.pk, marcar_visto=False)})


@_api(['POST'])
def convite_responder(request, convite_id):
    """Aceitar ou recusar o convite de parceria: chama impulso.views.meta_responder_parceria."""
    from impulso.models import ParceriaMeta
    from impulso.views import meta_responder_parceria

    convite = get_object_or_404(ParceriaMeta, pk=convite_id, convidado=request.user)
    resposta_pedida = request.data.get('resposta')
    if resposta_pedida not in ('aceitar', 'recusar'):
        return _erro('Responda aceitar ou recusar.')
    if not convite.esta_pendente:
        return _erro('Este convite já foi respondido.')
    _resposta, avisos = _chamar_view_do_portal(request, meta_responder_parceria, convite.pk,
                                               post={'resposta': resposta_pedida})
    convite.refresh_from_db()
    erro = _primeiro_erro(avisos)
    if erro or convite.esta_pendente:
        return _erro(erro or 'Não foi possível responder agora.')
    return Response({'id': convite.pk, 'status': convite.status, 'meta_id': convite.meta_id,
                     'mensagem': next((t for n, t in avisos if n == 'success'), '')})



# ── Cartão do Impulso na home (impulso/resumo_home.py, 25/11/2026) ───────────────
# Font Awesome do portal → Ionicons do app (o que não estiver aqui vai como "rocket-outline").
ICONES_DO_APP = {
    'fa-bolt': 'flash', 'fa-trophy': 'trophy', 'fa-medal': 'medal', 'fa-award': 'ribbon',
    'fa-handshake': 'people', 'fa-graduation-cap': 'school', 'fa-lightbulb': 'bulb',
    'fa-triangle-exclamation': 'warning', 'fa-clock': 'time', 'fa-clipboard-check': 'clipboard',
    'fa-bullseye': 'locate', 'fa-star': 'star', 'fa-comments': 'chatbubbles', 'fa-play-circle': 'play-circle',
    'fa-diagram-project': 'git-network', 'fa-rocket': 'rocket', 'fa-user-clock': 'finger-print',
}


def _icone_do_app(fa):
    nome = next((parte for parte in str(fa or '').split() if parte.startswith('fa-') and parte != 'fa-solid'), '')
    return ICONES_DO_APP.get(nome, 'rocket-outline')


@_api()
def home(request):
    """O cartão do Impulso da home do portal: pontuação do mês, faixa, posição e as próximas dicas.

    Pedido à parte (como no portal) para a pontuação não atrasar a home; a posição vem do ranking já
    calculado (se não estiver pronto, ele é calculado em segundo plano e aparece na próxima vez).
    """
    from impulso.resumo_home import contexto_do_cartao
    try:
        cartao = contexto_do_cartao(request.user)
    except Exception:  # noqa: BLE001 — a home do app segue sem o cartão, como a do portal
        import logging
        logging.getLogger(__name__).exception('Impulso: cartão da home do app não montado para %s', request.user.pk)
        return Response(status=status.HTTP_204_NO_CONTENT)
    faixa = cartao['faixa']
    return Response({
        'mes': cartao['mes'],
        'em_conferencia': cartao['em_conferencia'],
        'percentual': cartao['percentual'],
        'total': cartao['total'],
        'maximo': cartao['maximo'],
        'faixa': {'nome': faixa['nome'], 'cor': faixa['cor'], 'icone': _icone_do_app(faixa['icone'])},
        'proxima': cartao['proxima'],
        'posicao': cartao['posicao'],
        'participantes': cartao['participantes'],
        'blocos': [{**b, 'icone': _icone_do_app(b['icone'])} for b in cartao['blocos']],
        'metas': cartao['metas'],
        'dicas': [{'icone': _icone_do_app(d['icone']), 'tom': d['tom'], 'texto': d['texto'],
                   'url': d.get('url') or None, 'pontos': d.get('pontos')} for d in cartao['dicas']],
    })


urlpatterns = [
    path('resumo/', resumo, name='impulso_resumo'),
    path('home/', home, name='impulso_home'),
    path('ranking/', ranking, name='impulso_ranking'),
    path('metas/', metas, name='impulso_metas'),
    path('metas/concluidas/', metas_concluidas, name='impulso_metas_concluidas'),
    path('metas/<int:meta_id>/', meta, name='impulso_meta'),
    path('metas/<int:meta_id>/status/', meta_status, name='impulso_meta_status'),
    path('metas/<int:meta_id>/comentarios/', meta_comentar, name='impulso_meta_comentar'),
    path('metas/<int:meta_id>/avaliar/', meta_avaliar, name='impulso_meta_avaliar'),
    path('metas/itens/<int:item_id>/marcar/', item_marcar, name='impulso_item_marcar'),
    path('convites/<int:convite_id>/responder/', convite_responder, name='impulso_convite_responder'),
]
