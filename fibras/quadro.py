"""O quadro de Fibras (kanban e lista) montado uma vez só, com cache (10/11/2026).

Antes, cada cartão/linha fazia a sua consulta (`f.incidentes.first` e, para o autor,
mais uma para ver se havia resposta nova): o kanban do mês levava ~28 s (359 consultas)
e "todos" ~5 min (4.800 consultas), com o banco remoto a ~60 ms por ida e volta.
Agora:

- as vendas vêm numa consulta e o último incidente de cada uma em outra;
- contagem e total de cada coluna são somados em Python (os itens já estão na mão);
- kanban e lista usam os MESMOS filtros (`ler_filtros`/`aplicar_filtros`), o mesmo
  período padrão (mês corrente) e a MESMA regra da coluna "Não localizado" — antes a
  lista abria sem período, não tinha o B2B nem procurava na ORDEM vinculada, e por
  isso os números não batiam;
- o resultado fica no cache (Redis) por alguns minutos. A chave leva a "versão do
  quadro" e o SQL da consulta — que já carrega o recorte da pessoa (rede, loja ou só
  as dela) e os filtros —, então ninguém recebe o quadro de outra pessoa. Toda escrita
  em vendas/incidentes sobe a versão (`invalidar`), e o cache antigo deixa de valer.
"""
import hashlib
import logging
from datetime import date
from decimal import Decimal

from django.core.cache import cache
from django.db.models import F, Max, Q, Value
from django.db.models.functions import Length, Replace
from django.utils import timezone

logger = logging.getLogger(__name__)

CHAVE_VERSAO = 'fibras:quadro:versao'
TEMPO = 300                # 5 min: a versão já derruba o cache a cada escrita; isto é só a rede de segurança
MAX_ITENS_NO_CACHE = 2500  # "todos" (~4.800 vendas) passa disso: vai direto ao banco, sem inflar o Redis


# ---------------------------------------------------------------------------
# Versão do quadro
# ---------------------------------------------------------------------------
def versao():
    v = cache.get(CHAVE_VERSAO)
    if v is None:
        v = timezone.now().strftime('%Y%m%d%H%M%S%f')
        cache.set(CHAVE_VERSAO, v, None)
    return v


def invalidar(*_args, **_kwargs):
    """Sobe a versão: tudo que estava no cache do quadro deixa de valer. Nunca levanta."""
    try:
        cache.set(CHAVE_VERSAO, timezone.now().strftime('%Y%m%d%H%M%S%f'), None)
    except Exception:  # noqa: BLE001
        logger.warning('Fibras: não deu para invalidar o cache do quadro', exc_info=True)


def _chave(prefixo, qs=None, extra=''):
    base = f'{versao()}|{extra}'
    if qs is not None:
        try:
            base += '|' + str(qs.query)
        except Exception:  # noqa: BLE001 — consulta que não vira texto: sem cache
            return None
    return f'fibras:{prefixo}:' + hashlib.sha1(base.encode('utf-8')).hexdigest()


def em_cache(prefixo, calcular, qs=None, extra='', cabe=lambda valor: True):
    chave = _chave(prefixo, qs, extra)
    if chave is not None:
        valor = cache.get(chave)
        if valor is not None:
            return valor
    valor = calcular()
    if chave is not None and cabe(valor):
        try:
            cache.set(chave, valor, TEMPO)
        except Exception:  # noqa: BLE001
            pass
    return valor


# ---------------------------------------------------------------------------
# Filtros — os mesmos no kanban e na lista
# ---------------------------------------------------------------------------
def ler_filtros(request):
    g = request.GET
    return {
        'pdv': (g.get('pdv') or '').strip(),
        'vendedor': (g.get('vendedor') or '').strip(),
        'venda': (g.get('venda') or '').strip(),
        'ordem': (g.get('ordem') or '').strip(),
        'status': (g.get('status') or '').strip(),
        # B2B: documento (CPF/CNPJ) com mais de 11 caracteres = empresa.
        'b2b': (g.get('b2b') or '').strip() in ('1', 'true', 'on', 'sim'),
    }


def aplicar_filtros(qs, f):
    if f['b2b']:
        # Só os DÍGITOS contam: o CPF vem formatado ("123.456.789-00" tem 14 caracteres) e
        # passava por CNPJ — o filtro B2B devolvia quase todas as vendas.
        doc = F('cpf')
        for sinal in ('.', '-', '/', ' '):
            doc = Replace(doc, Value(sinal), Value(''))
        qs = qs.annotate(_doc_len=Length(doc)).filter(_doc_len__gt=11)
    if f['pdv']:
        qs = qs.filter(pdv__icontains=f['pdv'])
    if f['vendedor']:
        qs = qs.filter(vendedor__icontains=f['vendedor'])
    if f['venda']:
        qs = qs.filter(numero_da_venda__icontains=f['venda'])
    if f['ordem']:
        # Nº de ORDEM/protocolo da planilha, ORDEM vinculada pela Esteira ou a posição na planilha.
        cond = Q(numero_protocolo__icontains=f['ordem']) | Q(ordem_vinculada__icontains=f['ordem'])
        if f['ordem'].isdigit():
            cond |= Q(ordem_planilha=int(f['ordem']))
        qs = qs.filter(cond)
    return qs


# ---------------------------------------------------------------------------
# A coluna "Não localizado" — uma regra só
# ---------------------------------------------------------------------------
NAO_LOCALIZADO = 'nao_localizado'
ROTULO_NAO_LOCALIZADO = 'Não localizado'


def ultimo_import():
    from .models import Fibra
    return em_cache('ultimo_import', lambda: Fibra.objects.aggregate(m=Max('last_planilha_at'))['m'] or False) or None


def e_nao_localizado(f, ultimo):
    """Venda de Fixa, nem cancelada nem instalada, que a última planilha não trouxe."""
    from .models import Fibra
    pilar = (f.pilar or '').lower()
    if pilar and 'fixa' not in pilar:
        return False
    if f.status in (Fibra.STATUS_CANCELADO, Fibra.STATUS_INSTALADO):
        return False
    if ultimo:
        return f.last_planilha_at is None or f.last_planilha_at < ultimo
    return f.last_planilha_at is None


def _ordem_quadro(f):
    # ordem_planilha ASC (vazio no fim, como o NULLS LAST do Postgres) e data da venda DESC.
    d = f.data_da_venda or date.min
    return (f.ordem_planilha is None, f.ordem_planilha or 0, -d.toordinal(), -f.id)


def _ordem_recente(f):
    d = f.data_da_venda or date.min
    return (-d.toordinal(), -f.id)


# ---------------------------------------------------------------------------
# Carregar as vendas (1 consulta) + último incidente de cada uma (1 consulta)
# ---------------------------------------------------------------------------
def carregar(qs, user):
    """Lista de vendas com `inc` (último incidente) e `nova_resposta` (para o autor)."""
    from .models import FibraIncidente

    def _buscar():
        itens = list(qs.order_by('ordem_planilha', '-data_da_venda', '-id'))
        ultimos = {}
        ids = [f.id for f in itens]
        for i in range(0, len(ids), 2000):
            for inc in (FibraIncidente.objects.filter(fibra_id__in=ids[i:i + 2000])
                        .only('id', 'fibra_id', 'aberto_por_id', 'aberto_em', 'support_chat_id',
                              'last_opener_view_at', 'status')
                        .order_by('fibra_id', '-aberto_em', '-id')):
                ultimos.setdefault(inc.fibra_id, inc)
        for f in itens:
            f.inc = ultimos.get(f.id)
        return itens

    itens = em_cache('vendas', _buscar, qs=qs, cabe=lambda v: len(v) <= MAX_ITENS_NO_CACHE)
    marcar_respostas_novas(itens, user)
    return itens


def marcar_respostas_novas(itens, user):
    """`f.nova_resposta`: há resposta que o autor do incidente ainda não viu.

    Mesma regra de FibraIncidente.has_unread_reply_for_opener, em lote (2 consultas no
    total). Fica fora do cache: é por pessoa e muda a cada leitura. Só vale para os
    incidentes que ESTA pessoa abriu — e a Ilha abre muitos: um por um eram ~2 consultas
    por cartão.
    """
    from django.db.models import Max

    from projects.models_chat import SupportChatMessage

    from .models import FibraChatMessage

    uid = getattr(user, 'id', None)
    meus = [f for f in itens if f.inc is not None and uid and f.inc.aberto_por_id == uid]
    for f in itens:
        f.nova_resposta = False
    if not meus:
        return
    try:
        com_chat = {f.inc.support_chat_id: f for f in meus if f.inc.support_chat_id}
        ultimas = dict(SupportChatMessage.objects.filter(chat_id__in=list(com_chat)).exclude(user_id=uid)
                       .values('chat_id').annotate(u=Max('created_at')).values_list('chat_id', 'u')) if com_chat else {}
        sem_chat = {f.id: f for f in meus if not f.inc.support_chat_id}
        antigas = dict(FibraChatMessage.objects.filter(chat__fibra_id__in=list(sem_chat)).exclude(autor_id=uid)
                       .values('chat__fibra_id').annotate(u=Max('criado_em'))
                       .values_list('chat__fibra_id', 'u')) if sem_chat else {}
    except Exception:  # noqa: BLE001 — o selo é enfeite: sem ele o quadro abre igual
        logger.warning('Fibras: selo de resposta nova indisponível', exc_info=True)
        return
    for f in meus:
        ultima = ultimas.get(f.inc.support_chat_id) if f.inc.support_chat_id else antigas.get(f.id)
        visto = f.inc.last_opener_view_at
        f.nova_resposta = bool(ultima and (visto is None or ultima > visto))


def colunas(itens, status_choices):
    """As colunas do kanban: uma por status + "Não localizado", sem a venda aparecer em duas."""
    ultimo = ultimo_import()
    nao, por_status = [], {k: [] for k, _ in status_choices}
    for f in itens:
        f.coluna = NAO_LOCALIZADO if e_nao_localizado(f, ultimo) else f.status
        if f.coluna == NAO_LOCALIZADO:
            nao.append(f)
        else:
            por_status.setdefault(f.status, []).append(f)
    saida = []
    for key, label in status_choices:
        lista = sorted(por_status.get(key, []), key=_ordem_quadro)
        saida.append({'key': key, 'label': label, 'items': lista, 'count': len(lista),
                      'total': sum((x.valor or Decimal('0') for x in lista), Decimal('0'))})
    nao.sort(key=_ordem_recente)
    saida.append({'key': NAO_LOCALIZADO, 'label': ROTULO_NAO_LOCALIZADO, 'items': nao, 'count': len(nao),
                  'total': sum((x.valor or Decimal('0') for x in nao), Decimal('0'))})
    return saida


def invalida_quadro(fn):
    """Decorador: ao fim da função (deu certo ou não), o cache do quadro deixa de valer.

    Para as escritas em massa (bulk_create/bulk_update/update), que não disparam sinais.
    """
    import functools

    @functools.wraps(fn)
    def _envolta(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        finally:
            invalidar()
    return _envolta


def ligar_sinais():
    """Toda gravação/remoção nos modelos do quadro derruba o cache depois do commit."""
    from django.db import transaction
    from django.db.models.signals import post_delete, post_save

    from . import models as m

    def _depois_do_commit(**_kwargs):
        try:
            transaction.on_commit(invalidar)
        except Exception:  # noqa: BLE001
            invalidar()

    # weak=False: a função é local — com a referência fraca padrão ela seria descartada ao
    # sair daqui e o sinal deixaria de disparar (o cache ficava velho depois de gravar).
    for modelo in (m.Fibra, m.FibraIncidente, m.PlanilhaOrdemInconsistente, m.EsteiraImportacao,
                   m.Cruzamento, m.FibraStatusHistory):
        post_save.connect(_depois_do_commit, sender=modelo, weak=False, dispatch_uid=f'fibras-quadro-save-{modelo.__name__}')
        post_delete.connect(_depois_do_commit, sender=modelo, weak=False, dispatch_uid=f'fibras-quadro-del-{modelo.__name__}')
