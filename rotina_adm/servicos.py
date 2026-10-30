"""O que a central mostra: chamados da loja, pendências fiscais do SAP, check-list e a agenda."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

STATUS_FECHADOS = ['RESOLVIDO', 'FECHADO', 'ENCERRADO_SEM_SOLUCAO', 'REJEITADO']


def lojas():
    from users.models import Sector
    return Sector.objects.filter(name__istartswith='Loja').order_by('name')


def e_loja(setor):
    return bool(setor and setor.name.lower().startswith('loja'))


# ── Chamados ─────────────────────────────────────────────────────────────────
def chamados_abertos(loja):
    """Chamados abertos por gente da loja (setor principal) que ainda não foram resolvidos."""
    from tickets.models import Ticket
    return (Ticket.objects.filter(created_by__sector=loja).exclude(status__in=STATUS_FECHADOS)
            .select_related('sector', 'created_by', 'assigned_to').order_by('-created_at'))


# ── Pendências fiscais (Visão SAP, aba "Sem NF") ─────────────────────────────
def pendencias_fiscais(loja):
    """Linhas "Sem NF" da loja ainda em aberto no SAP, como a Visão SAP conta.

    Devolve ``{'total', 'mes', 'pdvs', 'disponivel'}``: total de todos os meses e o
    do mês corrente. A loja do portal ("Loja Glória") casa com o PDV do SAP
    ("GLÓRIA", "L220") pela mesma normalização da Visão SAP.
    """
    try:
        from auditoria_sap.models import LinhaAuditoria, q_da_tela, q_valor_desprezivel
        from auditoria_sap.permissions import normalizar_loja
        alvo = normalizar_loja(loja.name)
        # order_by() vazio: a ordenação padrão do modelo entraria no DISTINCT e repetiria o PDV.
        pdvs = sorted(p for p in LinhaAuditoria.objects.exclude(pdv='').order_by().values_list('pdv', flat=True).distinct()
                      if normalizar_loja(p) == alvo)
        qs = (LinhaAuditoria.objects.filter(q_da_tela('sem_nf'), pdv__in=pdvs, resolvida=False, ativa=True)
              .exclude(q_valor_desprezivel()))
        hoje = timezone.localdate()
        return {'disponivel': True, 'pdvs': pdvs, 'total': qs.count(),
                'mes': qs.filter(data_venda__year=hoje.year, data_venda__month=hoje.month).count(),
                'pdv': pdvs[0] if pdvs else ''}
    except Exception:  # noqa: BLE001 — a central não cai se o espelho do SAP estiver fora
        return {'disponivel': False, 'pdvs': [], 'total': 0, 'mes': 0, 'pdv': ''}


# ── Check-list ───────────────────────────────────────────────────────────────
def itens_do_dia(loja, dia):
    """Itens ativos da loja com a marcação do dia (feito, por quem, a que horas)."""
    from .models import ItemChecklist, MarcacaoChecklist
    itens = list(ItemChecklist.objects.filter(loja=loja, ativo=True))
    feitos = {m.item_id: m for m in MarcacaoChecklist.objects.filter(item__in=itens, data=dia).select_related('feito_por')}
    agora = timezone.localtime()
    linhas = []
    for item in itens:
        m = feitos.get(item.id)
        atrasado = (not m and item.horario and dia == agora.date() and agora.time() > item.horario) or \
                   (not m and dia < agora.date())
        linhas.append({'item': item, 'marcacao': m, 'atrasado': bool(atrasado)})
    return linhas


def copiar_checklist(origem, destino, por, substituir=False):
    """Copia os itens ativos de uma loja para outra. Devolve quantos itens entraram."""
    from .models import ItemChecklist
    with transaction.atomic():
        if substituir:
            ItemChecklist.objects.filter(loja=destino, ativo=True).update(ativo=False)
        base = (ItemChecklist.objects.filter(loja=destino, ativo=True).order_by('-ordem')
                .values_list('ordem', flat=True).first() or 0)
        existentes = {t.lower() for t in ItemChecklist.objects.filter(loja=destino, ativo=True).values_list('titulo', flat=True)}
        novos = []
        for i, item in enumerate(ItemChecklist.objects.filter(loja=origem, ativo=True), start=1):
            if item.titulo.lower() in existentes:
                continue
            novos.append(ItemChecklist(loja=destino, titulo=item.titulo, horario=item.horario,
                                       ordem=base + i, criado_por=por))
        ItemChecklist.objects.bulk_create(novos)
    return len(novos)


# ── Agenda (Rotina Gerencial) ────────────────────────────────────────────────
DIAS_CURTOS = ['SEG', 'TER', 'QUA', 'QUI', 'SEX', 'SÁB', 'DOM']


def agenda_da_semana(pessoa, segunda):
    """As atividades da Rotina Gerencial da pessoa na semana que começa em ``segunda``.

    ``None`` quando a pessoa não tem rotina ativa. Cada dia traz as atividades
    em ordem de horário, com a conclusão daquele dia.
    """
    from rotina.models import CORES_CATEGORIA, ConclusaoAtividade, RotinaGerencial
    rotina = RotinaGerencial.objects.filter(user=pessoa, ativa=True).first()
    if rotina is None:
        return None
    ultimo = 6 if (rotina.com_domingo or rotina.atividades.filter(dia_semana=6).exists()) else 5
    fim = segunda + timedelta(days=ultimo)
    atividades = list(rotina.atividades.order_by('dia_semana', 'inicio'))
    feitas = set(ConclusaoAtividade.objects.filter(atividade__rotina=rotina, data__range=(segunda, fim))
                 .values_list('atividade_id', 'data'))
    hoje = timezone.localdate()
    dias = []
    for n in range(ultimo + 1):
        data = segunda + timedelta(days=n)
        itens = [{'atividade': a, 'cor': CORES_CATEGORIA.get(a.categoria, {}).get('cor', '#9CA3AF'),
                  'feita': (a.id, data) in feitas,
                  'perdida': (a.id, data) not in feitas and data < hoje}
                 for a in atividades if a.dia_semana == n]
        dias.append({'data': data, 'curto': DIAS_CURTOS[n], 'hoje': data == hoje, 'itens': itens})
    return {'rotina': rotina, 'dias': dias, 'inicio': segunda, 'fim': fim,
            'total': sum(len(d['itens']) for d in dias),
            'feitas': sum(1 for d in dias for i in d['itens'] if i['feita'])}


# ── Contagem LVUT (fictícia até existir a fonte) ─────────────────────────────
LVUT_EXEMPLO = [
    {'nome': 'Smartphones', 'icone': 'fa-mobile-alt', 'cor': 'laranja', 'status': 'Saudável', 'tom': 'verde',
     'pct': 82, 'nota': 'Estoque dentro do esperado.'},
    {'nome': 'Acessórios', 'icone': 'fa-headphones', 'cor': 'verde', 'status': 'Atenção', 'tom': 'amarelo',
     'pct': 58, 'nota': 'Verificar divergências.'},
    {'nome': 'Chips', 'icone': 'fa-sim-card', 'cor': 'azul', 'status': 'Crítico', 'tom': 'vermelho',
     'pct': 24, 'nota': 'Nível abaixo do ideal.'},
    {'nome': 'Outros', 'icone': 'fa-box-open', 'cor': 'cinza', 'status': 'Em conferência', 'tom': 'azul',
     'pct': 40, 'nota': 'Em processo de contagem.'},
]
