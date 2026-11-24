"""Contestação da loja: o gerente responde cada venda e o CN só dá o de acordo
nas que o gerente marcou Não.

Pedido do dono (23/11/2026), invertendo o fluxo de 17/11 (em que cada CN
respondia as próprias vendas e o gerente só enviava quando todos terminassem):
"O gerente faz a contestação e o CN dá o de acordo dele apenas nos que o
Gerente marcar como NÃO. O resto deve seguir a contestação." E mais: "prazo de
24hrs para o CN (Consultor) dar o de acordo, caso passe o prazo colocar o de
acordo automático e sinalizar 'De acordo automático por prazo expirado'" e
"quando tiver o parecer do Gestor, o CN ter visão, mas quem pode questionar ao
time de conferência de venda é apenas o gerente".

**Quem é quem**

- *CN*: o vendedor da venda. A base da Vivo traz só o NOME
  (``ExclusionRecord.vendedor``); o CN é o usuário ATIVO do portal com esse nome
  (sem acento, sem caixa). Nome que bate com mais de um usuário fica com o da
  mesma loja; se ainda empatar, a venda fica sem CN.
- *Gerente*: quem pode contestar (``User.can_create_contestations``). Responde
  toda venda da loja: Sim (motivo e evidência) ou Não (com justificativa), e
  só envia com a loja toda respondida (``trava_do_envio``) — mas o envio não
  espera os de acordo dos Não.
- *Gestão central* (``_can_manage_contestations``): como o gerente, no escopo dela.

**O de acordo do CN no Não** (``RespostaNaoContesta.situacao_cn``)

- ``aguardando``: o CN tem até ``prazo_cn`` (24 h da resposta do gerente);
- ``de_acordo``: o CN concordou;
- ``automatico``: o prazo passou sem resposta — "De acordo automático por
  prazo expirado" (``expirar_prazos``, chamado por quem lê);
- ``discorda``: o CN discordou, com o motivo; a venda volta para o gerente, que
  contesta (Sim — o Não sai) ou mantém o Não com uma resposta (``mantido``);
- ``sem_cn``: venda do próprio gerente: não há de quem esperar.
- 24/11/2026: CN que não está ativo (desligado, inativo, afastado, de férias ou sem cadastro) não dá de acordo:
  o Não já nasce ``automatico`` sem prazo, e um Não aguardando vira automático se o CN deixar de estar ativo.

**A resposta de cada venda** (``situacao_das_vendas``): ``contestada``, ``sim``,
``nao``, ``sim_incompleto`` ou ``pendente``.
"""
from collections import defaultdict
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (Contestation, ContestationCartDraft, ContestationHistory, ExclusionRecord,
                     RespostaNaoContesta)

CACHE_DONOS = 'contestacao:donos:v1:{}'
CACHE_E_CN = 'contestacao:e_cn:v1:{}'
TEMPO_CACHE = 300            # 5 min: venda nova no lote ou usuário novo aparece logo
MIN_JUSTIFICATIVA = 5        # "." ou "não" não explicam nada
RESPONDIDAS = ('contestada', 'sim', 'nao')
PRAZO_CN = timedelta(hours=24)
ROTULO_AUTOMATICO = 'De acordo automático por prazo expirado'
ROTULO_CN_INATIVO = 'De acordo automático: CN não está ativo no portal'

ROTULO_RESPOSTA = {
    'contestada': 'Já enviada',
    'sim': 'Sim (pronta)',
    'nao': 'Não (justificada)',
    'sim_incompleto': 'Sim, falta motivo ou evidência',
    'pendente': 'Sem resposta',
}


def _views():
    # Import tardio: views importa este módulo, e as regras de nome/escopo moram lá.
    from . import views
    return views


def _cache_get(chave):
    try:
        return cache.get(chave)
    except Exception:                                       # noqa: BLE001 — Redis fora: calcula de novo
        return None


def _cache_set(chave, valor):
    try:
        cache.set(chave, valor, TEMPO_CACHE)
    except Exception:                                       # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Quem é o dono (CN) de cada venda
# ---------------------------------------------------------------------------

def resolver_donos(pares):
    """``{(filial, vendedor): user_id | None}`` para os pares pedidos."""
    pares = set(pares)
    if not pares:
        return {}
    v = _views()
    por_nome = defaultdict(dict)
    for u in get_user_model().objects.filter(is_active=True).select_related('sector'):
        for nome in v._build_user_name_candidates(u):
            por_nome[nome][u.pk] = u
    resultado = {}
    for filial, vendedor in pares:
        candidatos = list(por_nome.get(v._normalize_person_name(vendedor), {}).values())
        if len(candidatos) > 1:
            candidatos = [u for u in candidatos
                          if u.sector and v._match_sector_to_filial([u.sector.name], filial)]
        resultado[(filial, vendedor)] = candidatos[0].pk if len(candidatos) == 1 else None
    return resultado


def donos_do_lote(lote):
    """O dono de cada (filial, vendedor) do lote. Guardado 5 min por lote."""
    if lote is None:
        return {}
    chave = CACHE_DONOS.format(lote.pk)
    guardado = _cache_get(chave)
    if guardado is not None:
        return guardado
    pares = set(_views()._exclusoes().filter(sync_batch=lote)
                .values_list('filial', 'vendedor').distinct())
    donos = resolver_donos(pares)
    _cache_set(chave, donos)
    return donos


def donos_para(lote, pares):
    """Como ``donos_do_lote``, mas resolve na hora o par que o cache não conhece
    (venda entrou pelo "Atualizar sem reiniciar" depois do cálculo)."""
    pares = set(pares)
    donos = dict(donos_do_lote(lote)) if lote is not None else {}
    faltam = pares - set(donos)
    if faltam:
        donos.update(resolver_donos(faltam))
    return {par: donos.get(par) for par in pares}


def dono_da_venda(venda):
    """O id do CN dono da venda, ou None."""
    if venda is None:
        return None
    par = (venda.filial, venda.vendedor)
    return donos_para(venda.sync_batch, [par]).get(par)


def e_cn(user):
    """É dono de alguma venda na base do ciclo atual. Guardado 5 min por pessoa —
    o menu pergunta isto em toda página."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    chave = CACHE_E_CN.format(user.pk)
    guardado = _cache_get(chave)
    if guardado is not None:
        return guardado
    lote = _views()._get_latest_sync_batch()
    resposta = bool(lote is not None and user.pk in set(donos_do_lote(lote).values()))
    _cache_set(chave, resposta)
    return resposta


def papel(user):
    """'central', 'gerente', 'cn' ou 'outro' — o que a pessoa faz nesta tela."""
    v = _views()
    if v._can_manage_contestations(user):
        return 'central'
    if v._can_create_contestations(user):
        return 'gerente'
    if e_cn(user):
        return 'cn'
    return 'outro'


def pode_responder(user, venda, dono_id=None):
    """Quem responde Sim/Não por uma venda: quem pode contestar na loja dela (o
    gerente). O CN não responde — ele só dá o de acordo nos Não."""
    if venda is None or not user or not getattr(user, 'is_authenticated', False):
        return False
    v = _views()
    if not v._can_create_contestations(user):
        return False
    return v._apply_exclusion_scope_for_user(
        ExclusionRecord.objects.filter(pk=venda.pk), user).exists()


def pode_dar_de_acordo_no_nao(user, resposta):
    """O de acordo (ou a discordância) do Não é do CN dono da venda, dentro do prazo."""
    return bool(resposta is not None and user and resposta.cn_id
                and resposta.cn_id == user.pk
                and resposta.situacao_cn == RespostaNaoContesta.AGUARDANDO
                and (resposta.prazo_cn is None or resposta.prazo_cn > timezone.now()))


def cn_ativo(user_ou_id):
    """O CN pode dar o de acordo: usuário ativo no portal e com situação Ativo (não inativo, afastado nem de férias)."""
    if not user_ou_id:
        return False
    user = user_ou_id
    if not hasattr(user, 'is_active'):
        user = get_user_model().objects.filter(pk=user_ou_id).only('is_active', 'status').first()
    return bool(user and user.is_active and (getattr(user, 'status', '') or 'ATIVO') == 'ATIVO')


def situacao_cn_inicial(dono_id, quem_respondeu, agora=None):
    """``(situacao_cn, prazo_cn)`` de um Não que acabou de ser dado.

    24/11/2026: o de acordo só é pedido a CN ativo; sem CN ativo no portal (desligado, inativo, afastado,
    de férias ou sem cadastro) o de acordo já nasce automático, sem prazo.
    """
    agora = agora or timezone.now()
    if dono_id and dono_id == quem_respondeu.pk:
        # O gerente respondendo a própria venda.
        return RespostaNaoContesta.SEM_CN, None
    if not cn_ativo(dono_id):
        return RespostaNaoContesta.AUTOMATICO, None
    return RespostaNaoContesta.AGUARDANDO, agora + PRAZO_CN


def expirar_prazos(agora=None, ids=None):
    """De acordo automático nos Não cujo prazo do CN passou. Devolve quantos.

    Chamado por quem lê (a lista, o painel, o de acordo): o estado fica certo
    sem depender de rotina agendada. O histórico registra cada um, com a hora
    do prazo — não a da tela que percebeu.
    """
    agora = agora or timezone.now()
    # Prazo vencido, ou o CN deixou de estar ativo (24/11/2026: de acordo só de CN ativo). O "sem CN"
    # antigo de venda sem dono no portal também vira automático; o da própria venda do gerente fica.
    vencidas = Q(situacao_cn=RespostaNaoContesta.AGUARDANDO, prazo_cn__isnull=False, prazo_cn__lte=agora)
    cn_inativo = (Q(situacao_cn=RespostaNaoContesta.AGUARDANDO)
                  & (Q(cn__isnull=True) | Q(cn__is_active=False) | ~Q(cn__status='ATIVO')))
    sem_dono = Q(situacao_cn=RespostaNaoContesta.SEM_CN, cn__isnull=True)
    qs = RespostaNaoContesta.objects.filter(vencidas | cn_inativo | sem_dono)
    if ids is not None:
        qs = qs.filter(exclusion_id__in=ids)
    feitos = 0
    for pk in list(qs.values_list('pk', flat=True)[:2000]):
        with transaction.atomic():
            r = (RespostaNaoContesta.objects.select_for_update(of=('self',)).select_related('exclusion', 'cn')
                 .filter(pk=pk, situacao_cn__in=(RespostaNaoContesta.AGUARDANDO, RespostaNaoContesta.SEM_CN))
                 .first())
            if r is None:
                continue
            por_prazo = (r.situacao_cn == RespostaNaoContesta.AGUARDANDO and r.prazo_cn is not None
                         and r.prazo_cn <= agora and cn_ativo(r.cn))
            if r.situacao_cn == RespostaNaoContesta.SEM_CN and r.cn_id:
                continue
            if r.situacao_cn == RespostaNaoContesta.AGUARDANDO and not por_prazo and cn_ativo(r.cn):
                continue
            r.situacao_cn = RespostaNaoContesta.AUTOMATICO
            r.cn_respondido_em = r.prazo_cn if por_prazo else agora
            if not por_prazo:
                r.prazo_cn = None       # sem prazo = automático porque não havia CN ativo (a tela mostra assim)
            r.cn_respondido_por = None
            r.save(update_fields=['situacao_cn', 'cn_respondido_em', 'cn_respondido_por', 'prazo_cn', 'atualizado_em'])
            venda = r.exclusion
            ContestationHistory.objects.create(
                action='cn_de_acordo_auto', user=None, notes=ROTULO_AUTOMATICO if por_prazo else ROTULO_CN_INATIVO,
                extra_data={
                    'exclusion_id': venda.pk, 'lote_id': venda.sync_batch_id,
                    'filial': venda.filial, 'vendedor': venda.vendedor,
                    'para_cn_id': r.cn_id, 'prazo': r.prazo_cn.isoformat() if r.prazo_cn else '',
                    'automatico': True, 'cn_inativo': not por_prazo,
                })
            feitos += 1
    return feitos


# ---------------------------------------------------------------------------
# A resposta de cada venda e o estado de cada loja
# ---------------------------------------------------------------------------

def situacao_das_vendas(ids):
    """``{id: 'contestada' | 'sim' | 'nao' | 'sim_incompleto' | 'pendente'}``.

    Sim pronto (motivo + evidência) vale mais que Não: as duas coisas só
    convivem num caso raro (carrinho antigo de outro navegador), e no lado do
    dinheiro o seguro é contestar.
    """
    ids = list({i for i in ids if i})
    if not ids:
        return {}
    contestadas = set(Contestation.objects.filter(exclusion_id__in=ids)
                      .values_list('exclusion_id', flat=True))
    naos = set(RespostaNaoContesta.objects.filter(exclusion_id__in=ids).exclude(justificativa='')
               .values_list('exclusion_id', flat=True))
    com_rascunho, prontos = set(), set()
    for eid, motivo, anexo in (ContestationCartDraft.objects.filter(exclusion_id__in=ids)
                               .values_list('exclusion_id', 'reason', 'attachment')):
        com_rascunho.add(eid)
        if (motivo or '').strip() and anexo:
            prontos.add(eid)
    situacoes = {}
    for eid in ids:
        if eid in contestadas:
            situacoes[eid] = 'contestada'
        elif eid in prontos:
            situacoes[eid] = 'sim'
        elif eid in naos:
            situacoes[eid] = 'nao'
        elif eid in com_rascunho:
            situacoes[eid] = 'sim_incompleto'
        else:
            situacoes[eid] = 'pendente'
    return situacoes


def estado_das_lojas(lote, filiais=None):
    """``{filial: loja}``: o que o gerente já respondeu e como andam os de acordo.

    ``loja['participantes']`` traz, por vendedor, quantas vendas tem em cada
    situação (Sim, Não, pendente...) e, dos Não, quantos estão esperando o CN,
    de acordo, automáticos, com discordância ou mantidos. ``loja['ids_para_enviar']``
    são as vendas com Sim pronto — o envio não espera os de acordo.
    ``loja['ids_faltam']`` são as sem resposta (o "Faltam" da tela, que trava o
    envio do gerente); ``loja['ids_incompletos']``, as delas que têm Sim sem
    motivo ou evidência.
    """
    if lote is None:
        return {}
    v = _views()
    qs = v._exclusoes().filter(sync_batch=lote)
    if filiais is not None:
        filiais = list({f for f in filiais if f})
        if not filiais:
            return {}
        qs = qs.filter(filial__in=filiais)
    vendas = list(qs.values_list('id', 'filial', 'vendedor'))
    if not vendas:
        return {}
    ids = [i for i, _, _ in vendas]
    expirar_prazos(ids=ids)
    donos = donos_para(lote, {(f, vd) for _, f, vd in vendas})
    situacoes = situacao_das_vendas(ids)
    acordos = dict(RespostaNaoContesta.objects.filter(exclusion_id__in=ids)
                   .exclude(justificativa='').values_list('exclusion_id', 'situacao_cn'))
    usuarios = {u.pk: u for u in get_user_model().objects.filter(
        pk__in={d for d in donos.values() if d})}
    chave_do_acordo = {
        RespostaNaoContesta.AGUARDANDO: 'cn_aguardando',
        RespostaNaoContesta.DE_ACORDO: 'cn_de_acordo',
        RespostaNaoContesta.AUTOMATICO: 'cn_automatico',
        RespostaNaoContesta.DISCORDA: 'cn_discorda',
        RespostaNaoContesta.MANTIDO: 'cn_mantido',
    }

    lojas = {}
    for eid, filial, vendedor in vendas:
        loja = lojas.setdefault(filial, {'filial': filial, 'por_vendedor': {}, 'ids_incompletos': set()})
        p = loja['por_vendedor'].setdefault(vendedor, {
            'filial': filial, 'vendedor': vendedor,
            'cn': usuarios.get(donos.get((filial, vendedor))),
            'total': 0, 'contestada': 0, 'sim': 0, 'nao': 0, 'sim_incompleto': 0, 'pendente': 0,
            'cn_aguardando': 0, 'cn_de_acordo': 0, 'cn_automatico': 0, 'cn_discorda': 0,
            'cn_mantido': 0,
            'ids': [], 'ids_sim': [], 'ids_faltam': [],
        })
        situacao = situacoes[eid]
        p['total'] += 1
        p[situacao] += 1
        p['ids'].append(eid)
        if situacao == 'sim':
            p['ids_sim'].append(eid)
        elif situacao not in RESPONDIDAS:
            p['ids_faltam'].append(eid)
            if situacao == 'sim_incompleto':
                loja['ids_incompletos'].add(eid)
        if situacao == 'nao':
            chave = chave_do_acordo.get(acordos.get(eid))
            if chave:
                p[chave] += 1

    for filial, loja in lojas.items():
        participantes = list(loja.pop('por_vendedor').values())
        for p in participantes:
            p['faltam'] = p['pendente'] + p['sim_incompleto']
            p['tudo_enviado'] = p['contestada'] == p['total']
            p['respondido'] = p['faltam'] == 0
        participantes.sort(key=lambda p: (-p['cn_discorda'], p['respondido'],
                                          v._normalize_person_name(p['vendedor'])))
        loja['participantes'] = participantes
        loja['faltam'] = sum(p['faltam'] for p in participantes)
        loja['cn_aguardando'] = sum(p['cn_aguardando'] for p in participantes)
        loja['cn_discorda'] = sum(p['cn_discorda'] for p in participantes)
        loja['cn_automatico'] = sum(p['cn_automatico'] for p in participantes)
        loja['ids_para_enviar'] = [eid for p in participantes for eid in p['ids_sim']]
        loja['ids_faltam'] = [eid for p in participantes for eid in p['ids_faltam']]
        loja['para_enviar'] = len(loja['ids_para_enviar'])
    return lojas


# ---------------------------------------------------------------------------
# A trava do envio: o gerente só envia com a loja toda respondida
# ---------------------------------------------------------------------------
#
# Pedido de 23/11/2026: "O gerente na hora de enviar deve enviar todas
# obrigatoriamente com Sim ou Não, enquanto todas não estiverem no carrinho
# deve estar travado". "A responder" é o mesmo universo que a tela mostra ao
# gerente: as vendas do lote atual no escopo dele (``_apply_exclusion_scope_for_user``),
# e "respondida" é o mesmo ``RESPONDIDAS`` do painel ("Faltam" = pendente +
# Sim sem motivo ou evidência). Já contestada conta como respondida (qualquer
# contestação da venda, aberta ou decidida); Não com discordância do CN também
# (o Não continua lá; o envio não espera os de acordo). A trava é por loja: a
# loja toda respondida envia, mesmo que outra loja do mesmo gerente ainda não.
# A gestão central não tem trava (como antes): o escopo dela é a rede.

ITENS_NA_TRAVA = 8           # quantas vendas sem resposta a tela lista por loja


def isento_da_trava(user):
    """Gestão central envia sem esperar a loja responder tudo."""
    return bool(_views()._can_manage_contestations(user))


def trava_do_envio(user, lote, lojas=None, escopo=None, respondidas_agora=()):
    """O que ainda trava o envio deste gerente.

    Devolve ``{'isento', 'travado', 'faltam', 'lojas': [{'filial', 'faltam',
    'ids', 'ids_incompletos'}], 'filiais': set}`` — só as lojas com venda sem
    resposta entram em ``lojas``.

    ``lojas`` (de ``estado_das_lojas``) e ``escopo`` (``[(id, filial)]`` das
    vendas do lote que o usuário vê) poupam consultas a quem já os tem.
    ``respondidas_agora``: vendas que o próprio pedido está respondendo com Sim
    completo (motivo + evidência no formulário, ou arquivo que ainda só está
    no navegador) — contam como respondidas.
    """
    resultado = {'isento': False, 'travado': False, 'faltam': 0, 'lojas': [], 'filiais': set()}
    if isento_da_trava(user):
        return dict(resultado, isento=True)
    if lote is None:
        return resultado
    v = _views()
    if escopo is None:
        escopo = list(v._apply_exclusion_scope_for_user(
            v._exclusoes().filter(sync_batch=lote), user).values_list('pk', 'filial'))
    ids_escopo = {eid for eid, _ in escopo}
    if lojas is None:
        lojas = estado_das_lojas(lote, {f for _, f in escopo})
    respondidas_agora = {int(i) for i in respondidas_agora if str(i).isdigit()}
    for filial in sorted(lojas):
        loja = lojas[filial]
        ids = [eid for eid in loja.get('ids_faltam', [])
               if eid in ids_escopo and eid not in respondidas_agora]
        if ids:
            resultado['lojas'].append({
                'filial': filial, 'faltam': len(ids), 'ids': ids,
                'ids_incompletos': [eid for eid in ids if eid in loja.get('ids_incompletos', ())],
            })
    resultado['faltam'] = sum(loja['faltam'] for loja in resultado['lojas'])
    resultado['filiais'] = {loja['filial'] for loja in resultado['lojas']}
    resultado['travado'] = bool(resultado['lojas'])
    return resultado


def detalhar_trava(trava, limite=ITENS_NA_TRAVA):
    """Acrescenta a cada loja travada ``itens``: as primeiras vendas sem
    resposta, com o que a tela precisa para levar a pessoa até elas. Uma
    consulta só, para todas as lojas."""
    escolhidos = {eid for loja in trava.get('lojas', []) for eid in loja['ids'][:limite]}
    dados = {}
    if escolhidos:
        for linha in (ExclusionRecord.objects.filter(pk__in=escolhidos)
                      .values('pk', 'vendedor', 'nome_cliente', 'receita', 'pilar', 'filial')):
            dados[linha['pk']] = linha
    for loja in trava.get('lojas', []):
        incompletos = set(loja.get('ids_incompletos', ()))
        itens = []
        for eid in loja['ids'][:limite]:
            linha = dados.get(eid)
            if linha is None:
                continue
            situacao = 'sim_incompleto' if eid in incompletos else 'pendente'
            itens.append({
                'id': eid,
                'vendedor': linha['vendedor'] or '',
                'cliente': linha['nome_cliente'] or '',
                'pilar': linha['pilar'] or '',
                'valor': f"R$ {linha['receita']:.2f}" if linha['receita'] is not None else '',
                'situacao': situacao,
                'rotulo': ROTULO_RESPOSTA[situacao],
            })
        loja['itens'] = itens
        loja['mais'] = max(0, loja['faltam'] - len(itens))
    return trava


def trava_para_json(trava):
    """A trava sem os ``set`` (para o JsonResponse)."""
    return {
        'isento': trava['isento'], 'travado': trava['travado'], 'faltam': trava['faltam'],
        'lojas': [{'filial': loja['filial'], 'faltam': loja['faltam'],
                   'itens': loja.get('itens', []), 'mais': loja.get('mais', 0)}
                  for loja in trava['lojas']],
    }


def ids_sem_resposta(ids):
    """Das vendas pedidas, as que ainda não têm Sim completo, Não nem contestação."""
    return [eid for eid, situacao in situacao_das_vendas(ids).items() if situacao not in RESPONDIDAS]


def naos_do_cn(user, lote):
    """Os Não que o gerente deu nas vendas deste CN, no lote — o painel dele."""
    if lote is None or not user or not getattr(user, 'is_authenticated', False):
        return []
    qs = (RespostaNaoContesta.objects.filter(cn=user, exclusion__sync_batch=lote)
          .exclude(justificativa=''))
    expirar_prazos(ids=list(qs.values_list('exclusion_id', flat=True)))
    ordem = {RespostaNaoContesta.AGUARDANDO: 0, RespostaNaoContesta.DISCORDA: 1}
    respostas = list(qs.select_related('exclusion', 'preenchido_por', 'mantido_por'))
    respostas.sort(key=lambda r: (ordem.get(r.situacao_cn, 2), r.prazo_cn or r.atualizado_em))
    return respostas


def discordancias(lote, filiais):
    """Os Não em que o CN discordou e que esperam o gerente decidir."""
    if lote is None:
        return []
    filiais = [f for f in (filiais or []) if f]
    if not filiais:
        return []
    return list(RespostaNaoContesta.objects.filter(
        exclusion__sync_batch=lote, exclusion__filial__in=filiais,
        situacao_cn=RespostaNaoContesta.DISCORDA)
        .select_related('exclusion', 'cn', 'preenchido_por', 'cn_respondido_por')
        .order_by('cn_respondido_em'))


def rascunho_para_envio(eid, rascunhos, quem_envia_id, dono_id):
    """Qual carrinho vale para a venda, entre os de várias pessoas.

    O de quem está enviando primeiro (é o que ele está vendo), depois o do CN
    dono da venda, depois o mais recente — e só rascunho completo (motivo +
    evidência), para um carrinho pela metade não esconder um pronto.
    """
    candidatos = [d for d in rascunhos.get(eid, []) if (d.reason or '').strip() and d.attachment]
    if not candidatos:
        return None
    for d in candidatos:
        if d.user_id == quem_envia_id:
            return d
    for d in candidatos:
        if dono_id and d.user_id == dono_id:
            return d
    return max(candidatos, key=lambda d: (d.updated_at, d.pk))


def historico_extra(venda, dono_id, quem_fez, **mais):
    """O pedaço do histórico que diz de quem é a venda e quem fez por ela."""
    dono = get_user_model().objects.filter(pk=dono_id).first() if dono_id else None
    extra = {
        'exclusion_id': venda.pk,
        'lote_id': venda.sync_batch_id,
        'filial': venda.filial,
        'vendedor': venda.vendedor,
        'para_cn_id': dono_id,
        'para_cn_nome': (dono.get_full_name() or dono.username) if dono else '',
        'feito_por_id': quem_fez.pk,
        'feito_por_nome': quem_fez.get_full_name() or quem_fez.username,
        # Verdadeiro também para vendedor sem cadastro: quem fez não é o dono.
        'em_nome_do_cn': dono_id != quem_fez.pk,
    }
    extra.update(mais)
    return extra
