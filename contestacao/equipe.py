"""Contestação feita pela equipe da loja: cada CN responde as próprias vendas e
só o gerente envia — e só quando todos terminaram.

Pedido do dono (17/11/2026): "Cada CN (Vendedor) faz a sua, mas só o gerente
envia e ele só envia quando todos fizerem, além disso se o cara marcar em não
ele tem que dar uma justificativa. Porém o Gerente pode fazer pelo CN (deixando
sempre o histórico de forma correta com quem fez)."

**Quem é quem**

- *CN*: o vendedor da venda. A base da Vivo traz só o NOME
  (``ExclusionRecord.vendedor``); o CN é o usuário ATIVO do portal com esse nome
  (sem acento, sem caixa — a mesma comparação que o app já usava para o gerente
  da planilha). Nome que bate com mais de um usuário fica com o da mesma loja;
  se ainda empatar, a venda fica sem CN.
- *Gerente*: quem já podia contestar (``User.can_create_contestations``: PADRÃO
  do grupo GERENTES, ou SUPERVISOR para cima). Vê a loja toda, responde por
  qualquer CN e é quem envia.
- *Gestão central* (``_can_manage_contestations``: ADMIN, SUPERADMIN,
  superusuário, liberação global da contestação): continua como antes, envia sem
  esperar os CNs.

**A resposta de cada venda** (``situacao_das_vendas``)

- ``contestada``: já virou contestação (qualquer status);
- ``sim``: está num carrinho com motivo e evidência (de quem for);
- ``nao``: respondida como Não, com justificativa;
- ``sim_incompleto``: está num carrinho, mas falta motivo ou evidência;
- ``pendente``: ninguém respondeu.

**Quando a loja libera o envio**: todo CN ativo com venda na base marcou
"pronto" e continua sem venda pendente. Venda de vendedor sem cadastro ativo no
portal (quem saiu, nome escrito diferente) não trava — o gerente responde por
ela se quiser, como já fazia.
"""
from collections import defaultdict

from django.contrib.auth import get_user_model
from django.core.cache import cache

from .models import (Contestation, ContestationCartDraft, ExclusionRecord, ParteDoCN,
                     RespostaNaoContesta)

CACHE_DONOS = 'contestacao:donos:v1:{}'
CACHE_E_CN = 'contestacao:e_cn:v1:{}'
TEMPO_CACHE = 300            # 5 min: venda nova no lote ou usuário novo aparece logo
MIN_JUSTIFICATIVA = 5        # "." ou "não" não explicam nada
RESPONDIDAS = ('contestada', 'sim', 'nao')

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
    """Quem responde por uma venda: o próprio CN ou quem pode contestar na loja dela."""
    if venda is None:
        return False
    if dono_id is None:
        dono_id = dono_da_venda(venda)
    if dono_id and dono_id == user.pk:
        return True
    v = _views()
    if not v._can_create_contestations(user):
        return False
    return v._apply_exclusion_scope_for_user(
        ExclusionRecord.objects.filter(pk=venda.pk), user).exists()


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
    """``{filial: loja}`` com cada vendedor da base e se a loja já pode enviar.

    ``loja['liberada']`` é a trava do envio: todo CN ativo com venda está
    pronto. ``loja['participantes']`` traz, por vendedor, quantas vendas tem em
    cada situação, o CN do portal (ou None) e quem marcou "pronto".
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
    donos = donos_para(lote, {(f, vd) for _, f, vd in vendas})
    situacoes = situacao_das_vendas([i for i, _, _ in vendas])
    partes = {(p.filial, p.vendedor): p for p in ParteDoCN.objects.filter(
        lote=lote, filial__in={f for _, f, _ in vendas}).select_related('marcado_por', 'cn')}
    usuarios = {u.pk: u for u in get_user_model().objects.filter(
        pk__in={d for d in donos.values() if d})}

    lojas = {}
    for eid, filial, vendedor in vendas:
        loja = lojas.setdefault(filial, {'filial': filial, 'por_vendedor': {}})
        p = loja['por_vendedor'].setdefault(vendedor, {
            'filial': filial, 'vendedor': vendedor,
            'cn': usuarios.get(donos.get((filial, vendedor))),
            'total': 0, 'contestada': 0, 'sim': 0, 'nao': 0, 'sim_incompleto': 0, 'pendente': 0,
            'ids': [], 'ids_sim': [],
        })
        situacao = situacoes[eid]
        p['total'] += 1
        p[situacao] += 1
        p['ids'].append(eid)
        if situacao == 'sim':
            p['ids_sim'].append(eid)

    for filial, loja in lojas.items():
        participantes = []
        for vendedor, p in loja.pop('por_vendedor').items():
            parte = partes.get((filial, vendedor))
            p['faltam'] = p['pendente'] + p['sim_incompleto']
            p['parte'] = parte
            p['tudo_enviado'] = p['contestada'] == p['total']
            p['pronto'] = p['faltam'] == 0 and (parte is not None or p['tudo_enviado'])
            # Marcou pronto e depois mexeu (tirou um Não, deixou um Sim sem
            # evidência): volta a pendente, mas a tela diz o porquê.
            p['mudou_depois'] = parte is not None and p['faltam'] > 0
            p['conta_para_envio'] = p['cn'] is not None
            participantes.append(p)
        participantes.sort(key=lambda p: (not p['conta_para_envio'], p['pronto'],
                                          v._normalize_person_name(p['vendedor'])))
        cns = [p for p in participantes if p['conta_para_envio']]
        loja['participantes'] = participantes
        loja['cns'] = len(cns)
        loja['cns_prontos'] = sum(1 for p in cns if p['pronto'])
        loja['cns_faltando'] = [p for p in cns if not p['pronto']]
        loja['sem_cadastro'] = len(participantes) - len(cns)
        loja['liberada'] = loja['cns_prontos'] == loja['cns']
        loja['ids_para_enviar'] = [eid for p in participantes for eid in p['ids_sim']]
        loja['para_enviar'] = len(loja['ids_para_enviar'])
    return lojas


def filiais_liberadas(vendas):
    """Das vendas dadas, as chaves ``(lote_id, filial)`` cuja loja pode enviar."""
    from .models import ExclusionSyncBatch
    por_lote = defaultdict(set)
    for venda in vendas:
        if venda.sync_batch_id:
            por_lote[venda.sync_batch_id].add(venda.filial)
    liberadas = set()
    for lote in ExclusionSyncBatch.objects.filter(pk__in=list(por_lote)):
        for filial, loja in estado_das_lojas(lote, por_lote[lote.pk]).items():
            if loja['liberada']:
                liberadas.add((lote.pk, filial))
    return liberadas


def venda_liberada(venda, liberadas):
    """A venda pode ser enviada? Venda sem lote não tem equipe a esperar."""
    if not venda.sync_batch_id:
        return True
    return (venda.sync_batch_id, venda.filial) in liberadas


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
