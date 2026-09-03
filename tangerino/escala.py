"""Regras de acesso e consultas da Escala (quadro semanal montado no portal).

Três papéis, um só lugar:

* **Gerente** — quem está no grupo GERENTES. Monta a escala da própria loja:
  colaboradores PADRÃO que dividem setor com ele e não são, eles mesmos,
  gerentes.
* **Gestor global** — o SUPERADMIN e quem ele indicar em ``EscalaConfig``.
  Enxerga e edita a escala de qualquer setor.
* **Colaborador** — vê só a própria escala, sem editar (tratado na view).

A escala é independente do Tangerino: não fala com a API, então funciona mesmo
com a integração fora do ar.
"""
import calendar
from datetime import date, timedelta

from django.db.models import Q

# Grupo de comunicação (/users/manage/groups/) que reúne os gerentes de loja.
# Casado por nome, como o resto do sistema faz com GERENTES (ver
# ``User.can_create_contestations``): renomear na tela é raro e o nome é o que
# as pessoas reconhecem.
GERENTES_GROUP = 'GERENTES'


# ─── Papéis ──────────────────────────────────────────────────────────────────

def e_gerente(user):
    """Está no grupo GERENTES (monta a escala da própria loja)."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    return user.communication_groups.filter(name__iexact=GERENTES_GROUP).exists()


def e_gestor_global(user):
    """Enxerga/edita a escala de todos os setores.

    SUPERADMIN, a hierarquia ADMINISTRAÇÃO (ver User.can_manage_rh) e quem for
    indicado em EscalaConfig. O gerente de loja continua vendo só a dele.
    """
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    if getattr(user, 'can_manage_rh', lambda: False)():
        return True
    from .models import EscalaConfig
    return EscalaConfig.get().gestores.filter(pk=user.pk).exists()


def pode_gerenciar(user):
    """Pode montar/editar escala de alguém (gerente de loja ou gestor global)."""
    return e_gestor_global(user) or e_gerente(user)


# ─── Quem cada gestor enxerga ────────────────────────────────────────────────

def setores_do_gerente(user):
    """IDs dos setores do gerente (principal + os do M2M)."""
    ids = set(user.sectors.values_list('id', flat=True))
    if getattr(user, 'sector_id', None):
        ids.add(user.sector_id)
    return ids


def colaboradores_geridos(user, setor_id=None):
    """Colaboradores que ``user`` pode escalar, opcionalmente filtrados por setor.

    Sempre PADRÃO, ativos e fora do grupo GERENTES — ninguém monta a escala de
    outro gerente. O gestor global vê todos; o gerente de loja, só os que
    dividem setor com ele.
    """
    from users.models import User as U

    try:
        setor_id = int(setor_id) if setor_id not in (None, '') else None
    except (TypeError, ValueError):
        setor_id = None

    qs = (U.objects.filter(hierarchy='PADRAO', is_active=True)
          .exclude(communication_groups__name__iexact=GERENTES_GROUP))

    if e_gestor_global(user):
        if setor_id:
            qs = qs.filter(Q(sector_id=setor_id) | Q(sectors__id=setor_id))
    else:
        setores = setores_do_gerente(user)
        if not setores:
            return U.objects.none()
        alvo = {setor_id} if (setor_id and setor_id in setores) else setores
        qs = qs.filter(Q(sector_id__in=alvo) | Q(sectors__id__in=alvo))

    return qs.select_related('sector').distinct().order_by('first_name', 'last_name')


def setores_para_filtro(user):
    """Setores que aparecem no filtro: todos para o global, os dele para o gerente."""
    from users.models import Sector
    if e_gestor_global(user):
        return Sector.objects.order_by('name')
    return Sector.objects.filter(id__in=setores_do_gerente(user)).order_by('name')


# ─── Semanas ─────────────────────────────────────────────────────────────────

def monday_of(d):
    """A segunda-feira da semana que contém ``d``."""
    return d - timedelta(days=d.weekday())


def semanas_do_mes(ano, mes):
    """As semanas (segunda a domingo) que tocam o mês, numeradas 1..N.

    A primeira pode começar no fim do mês anterior — é a "semana 1" que contém
    o dia 1º. A tela mostra o intervalo de datas, então não há ambiguidade.
    """
    primeiro = date(ano, mes, 1)
    ultimo = date(ano, mes, calendar.monthrange(ano, mes)[1])
    seg = monday_of(primeiro)
    semanas, numero = [], 0
    while seg <= ultimo:
        numero += 1
        semanas.append({'inicio': seg, 'fim': seg + timedelta(days=6), 'numero': numero})
        seg += timedelta(days=7)
    return semanas


def mes_de_referencia(semana_inicio):
    """O mês 'dono' da semana: o da quinta-feira (convenção ISO).

    Assim uma semana que começa dia 30 mas cai quase toda no mês seguinte é
    listada no mês certo pelo seletor.
    """
    quinta = semana_inicio + timedelta(days=3)
    return quinta.year, quinta.month
