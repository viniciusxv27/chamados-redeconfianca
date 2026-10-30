"""Fechamento de caixa: quem vê, até quando muda e o que o sistema já sabe do dia.

- SUPERADMIN vê e muda tudo, sempre, e é o único que libera acesso.
- Gestor (liberado pelo SUPERADMIN): todas as lojas; preenche, edita, bloqueia e desbloqueia.
- Visualizador: só a loja que o SUPERADMIN escolheu, só leitura.

O dia D fica aberto até a hora limite (10h) de D+1. Com "bloquear depois do
prazo" ligado, passou disso só o SUPERADMIN altera. O botão Bloquear fecha o
dia antes do prazo.
"""
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from .permissoes import e_superadmin

ZERO = Decimal('0.00')
SUPERADMIN, GESTOR, VISUALIZADOR = 'SUPERADMIN', 'GESTOR', 'VISUALIZADOR'


def acesso(user):
    """O AcessoFechamento da pessoa (guardado no request), ou None."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return None
    if not hasattr(user, '_fin_fechamento'):
        from .models import AcessoFechamento
        try:
            user._fin_fechamento = AcessoFechamento.objects.select_related('loja').filter(user=user).first()
        except Exception:  # noqa: BLE001 — tabela ainda não migrada noutro servidor
            user._fin_fechamento = None
    return user._fin_fechamento


def papel(user):
    if e_superadmin(user):
        return SUPERADMIN
    a = acesso(user)
    if a is None:
        return None
    if a.papel == GESTOR:
        return GESTOR
    return VISUALIZADOR if a.loja_id else None      # visualizador sem loja não vê nada


def pode_ver(user):
    return papel(user) is not None


def pode_editar(user):
    return papel(user) in (SUPERADMIN, GESTOR)


def lojas_de(user):
    from contagem_caixa.permissions import lojas
    p = papel(user)
    if p in (SUPERADMIN, GESTOR):
        return lojas()
    if p == VISUALIZADOR:
        return lojas().filter(pk=acesso(user).loja_id)
    return lojas().none()


# ── Prazo ────────────────────────────────────────────────────────────────────
def prazo_do_dia(dia, config):
    """Até quando o fechamento do dia muda: a hora limite do dia seguinte."""
    return timezone.make_aware(datetime.combine(dia + timedelta(days=1), config.hora_limite))


def situacao(dia, fechamento, config, agora=None):
    """('futuro' | 'aberto' | 'vencido' | 'bloqueado', prazo)."""
    agora = agora or timezone.now()
    prazo = prazo_do_dia(dia, config)
    if dia > timezone.localdate(agora):
        return 'futuro', prazo
    if fechamento is not None and fechamento.bloqueado:
        return 'bloqueado', prazo
    if agora > prazo and config.travar_apos_prazo:
        return 'vencido', prazo
    return 'aberto', prazo


def pode_alterar(user, dia, fechamento, config, agora=None):
    """Pode gravar o fechamento agora? O SUPERADMIN passa por cima do prazo e do bloqueio."""
    if not pode_editar(user):
        return False
    estado, _ = situacao(dia, fechamento, config, agora)
    if estado == 'futuro':
        return False
    return estado == 'aberto' or papel(user) == SUPERADMIN


# ── O que o sistema já sabe do dia (para conferir) ───────────────────────────
def sugestoes(lojas, dia):
    """{loja_id: {...}} com o que a Contagem de Caixa, as Sangrias e as vendas registraram."""
    from contagem_caixa.models import ContagemCaixaDia
    ids = [l.id for l in lojas]
    saida = {i: {'entradas': None, 'sangrias': None, 'caixa_fisico': None, 'agoracred': None, 'renova_allied_qtd': 0,
                 'renova_vini_valor': ZERO, 'renova_vini_qtd': 0}
             for i in ids}
    for c in ContagemCaixaDia.objects.filter(loja_id__in=ids, data=dia):
        saida[c.loja_id].update({'entradas': c.entrada, 'sangrias': c.sangrias_total,
                                 'caixa_fisico': c.valor_real if c.valor_real else None, 'agoracred': c.agoracred})
    try:
        from vendas.models import VendaProduto
        inicio = timezone.make_aware(datetime.combine(dia, datetime.min.time()))
        do_dia = VendaProduto.objects.filter(venda__loja_id__in=ids, venda__data_venda__gte=inicio,
                                             venda__data_venda__lt=inicio + timedelta(days=1))
        for r in do_dia.filter(renova_alied=True).values('venda__loja_id').annotate(n=Count('id')):
            saida[r['venda__loja_id']]['renova_allied_qtd'] = r['n']
        for r in do_dia.filter(renova_vini=True).values('venda__loja_id').annotate(n=Count('id'), v=Sum('renova_vini_valor')):
            saida[r['venda__loja_id']].update(renova_vini_qtd=r['n'], renova_vini_valor=r['v'] or ZERO)
    except Exception:  # noqa: BLE001 — sem vendas, a sugestão fica zerada
        pass
    return saida
