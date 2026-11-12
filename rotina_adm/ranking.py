"""Ranking dos analistas da Rotina ADM (12/11/2026) e o que o espelho no Kanban precisa.

A entrega da rotina ADM é o check-list diário da loja. Para cada analista conta,
no mês escolhido:

- **itens feitos**: marcações dele no check-list da loja dele;
- **% de conclusão**: itens feitos ÷ itens previstos da loja nos dias contados;
- **% no prazo**: dos feitos, os marcados até o horário do item (item sem horário
  vale no prazo, porque só se marca no próprio dia);
- **dias 100%**: dias em que ele mesmo marcou todos os itens previstos.

A loja do analista é o setor principal (regra do portal); quem não é de loja e
marcou (um Gestor, por exemplo) entra na loja em que mais marcou.

Dias contados: do 1º dia do mês até ontem (o dia de hoje entra quando virar).
Domingo só conta se a loja marcou alguma coisa nele — loja fechada não perde ponto.

Itens previstos num dia: os que já existiam (criado até o dia) e não tinham sido
removidos (inativo depois do dia), mais qualquer item marcado naquele dia. Item
removido vira inativo e não muda depois — por isso ``atualizado_em`` serve de data
da remoção. Toda porta que desativa item precisa gravar ``atualizado_em`` (o
``update()`` de ``servicos.copiar_checklist`` com "substituir" grava à mão).

Nada disto toca a pontuação do Impulso: o espelho no Kanban é só leitura.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta

from django.db.models import Q
from django.utils import timezone

from . import servicos


MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
         'outubro', 'novembro', 'dezembro']


def _dia(valor):
    return timezone.localtime(valor).date() if valor else None


class Previstos:
    """Quais itens cada loja tinha para fazer em cada dia (carrega os itens das lojas uma vez)."""

    def __init__(self, lojas_ids):
        from .models import ItemChecklist
        self.por_loja = defaultdict(list)
        for item_id, loja_id, criado, ativo, atualizado in (
                ItemChecklist.objects.filter(loja_id__in=list(lojas_ids))
                .values_list('id', 'loja_id', 'criado_em', 'ativo', 'atualizado_em')):
            self.por_loja[loja_id].append((item_id, _dia(criado), ativo, _dia(atualizado)))

    def do_dia(self, loja_id, dia, marcados=()):
        ids = {i for i, criado, ativo, removido in self.por_loja.get(loja_id, ())
               if criado and criado <= dia and (ativo or (removido and removido > dia))}
        return ids | set(marcados)


def periodo(mes_texto, hoje=None):
    """(início, fim do mês, último dia contado). ``mes_texto`` é 'AAAA-MM'; inválido = mês atual."""
    hoje = hoje or timezone.localdate()
    try:
        ano, mes = (int(x) for x in (mes_texto or '').split('-'))
        inicio = date(ano, mes, 1)
    except (TypeError, ValueError):
        inicio = date(hoje.year, hoje.month, 1)
    if inicio > hoje:
        inicio = date(hoje.year, hoje.month, 1)
    fim = (date(inicio.year + (inicio.month == 12), inicio.month % 12 + 1, 1) - timedelta(days=1))
    return inicio, fim, min(fim, hoje - timedelta(days=1))


def meses_para_escolher(hoje=None, quantos=12):
    """Os últimos meses (o atual primeiro), como ('AAAA-MM', 'mês/AAAA')."""
    hoje = hoje or timezone.localdate()
    ano, mes, saida = hoje.year, hoje.month, []
    for _ in range(quantos):
        saida.append((f'{ano:04d}-{mes:02d}', f'{MESES[mes - 1]}/{ano}'))
        ano, mes = (ano - 1, 12) if mes == 1 else (ano, mes - 1)
    return saida


def analistas_com_acesso():
    """Quem tem a Rotina ADM liberada (pessoa ou grupo) e é de loja: os analistas do ranking."""
    from users.models import User

    from .models import AcessoRotinaAdm
    pessoas = AcessoRotinaAdm.objects.filter(user__isnull=False).values('user_id')
    grupos = AcessoRotinaAdm.objects.filter(grupo__isnull=False).values('grupo_id')
    return (User.objects.filter(Q(id__in=pessoas) | Q(communication_groups__in=grupos), is_active=True,
                                sector__name__istartswith='Loja')
            .select_related('sector').distinct())


def calcular(mes_texto=None, cfg=None, hoje=None):
    """Linhas do ranking do mês, já ordenadas, e o resumo. Ver o docstring do módulo."""
    from users.models import User

    from .models import ConfiguracaoRotinaAdm, MarcacaoChecklist
    cfg = cfg or ConfiguracaoRotinaAdm.get()
    inicio, fim, ate = periodo(mes_texto, hoje)
    marcacoes = []
    if ate >= inicio:
        marcacoes = list(MarcacaoChecklist.objects.filter(data__gte=inicio, data__lte=ate)
                         .values_list('feito_por_id', 'item_id', 'item__loja_id', 'item__horario', 'data', 'feito_em'))

    pessoas = {u.id: u for u in analistas_com_acesso()}
    faltam = {m[0] for m in marcacoes if m[0] and m[0] not in pessoas}
    for u in User.objects.filter(id__in=faltam).select_related('sector'):
        pessoas[u.id] = u

    # Loja de cada um: setor principal; quem não é de loja fica com a loja em que mais marcou.
    marcou_em = defaultdict(Counter)
    for quem, _i, loja_id, *_ in marcacoes:
        if quem:
            marcou_em[quem][loja_id] += 1
    loja_de = {}
    for pid, u in pessoas.items():
        if servicos.e_loja(u.sector):
            loja_de[pid] = u.sector_id
        elif marcou_em[pid]:
            loja_de[pid] = marcou_em[pid].most_common(1)[0][0]

    previstos = Previstos(set(loja_de.values()))
    marcados_no_dia = defaultdict(set)              # (loja, dia) -> itens marcados por qualquer um
    da_pessoa = defaultdict(list)                   # (pessoa, loja) -> marcações
    for quem, item_id, loja_id, horario, dia, feito_em in marcacoes:
        marcados_no_dia[(loja_id, dia)].add(item_id)
        if quem:
            da_pessoa[(quem, loja_id)].append((item_id, horario, dia, feito_em))

    dias = [inicio + timedelta(days=n) for n in range(max((ate - inicio).days + 1, 0))]
    previsto_cache = {}

    def previstos_do_dia(loja_id, dia):
        chave = (loja_id, dia)
        if chave not in previsto_cache:
            marcados = marcados_no_dia.get(chave, set())
            if dia.weekday() == 6 and not marcados:
                previsto_cache[chave] = set()       # domingo sem nada marcado: loja fechada
            else:
                previsto_cache[chave] = previstos.do_dia(loja_id, dia, marcados)
        return previsto_cache[chave]

    from users.models import Sector
    nomes_lojas = dict(Sector.objects.filter(id__in=set(loja_de.values())).values_list('id', 'name'))
    linhas = []
    for pid, loja_id in loja_de.items():
        u = pessoas[pid]
        feitas = da_pessoa.get((pid, loja_id), [])
        total_previsto = sum(len(previstos_do_dia(loja_id, d)) for d in dias)
        no_prazo = sum(1 for _i, horario, _d, feito_em in feitas
                       if horario is None or timezone.localtime(feito_em).time() <= horario)
        por_dia = defaultdict(set)
        for item_id, _h, d, _f in feitas:
            por_dia[d].add(item_id)
        dias_100 = sum(1 for d, itens in por_dia.items()
                       if previstos_do_dia(loja_id, d) and previstos_do_dia(loja_id, d) <= itens)
        pct = round(len(feitas) * 100 / total_previsto, 1) if total_previsto else 0.0
        pct = min(pct, 100.0)
        pct_prazo = round(no_prazo * 100 / len(feitas), 1) if feitas else 0.0
        outras = sum(n for loja, n in marcou_em[pid].items() if loja != loja_id)
        linhas.append({
            'pessoa': u, 'nome': u.get_full_name() or u.username, 'loja_id': loja_id,
            'loja': nomes_lojas.get(loja_id, ''), 'previstos': total_previsto, 'feitos': len(feitas),
            'no_prazo': no_prazo, 'pct': pct, 'pct_prazo': pct_prazo, 'dias_100': dias_100,
            'dias_com_entrega': len(por_dia), 'outras_lojas': outras,
            'elegivel': bool(total_previsto and pct >= cfg.broche_pct_minimo
                             and pct_prazo >= cfg.broche_no_prazo_minimo),
        })

    linhas.sort(key=chave_do_ranking)
    for n, linha in enumerate(linhas, start=1):
        linha['posicao'] = n
    colegas = Counter(linha['loja_id'] for linha in linhas)
    for linha in linhas:
        linha['divide_loja'] = colegas[linha['loja_id']] > 1
    return {
        'inicio': inicio, 'fim': fim, 'ate': ate, 'dias_contados': len(dias), 'linhas': linhas,
        'elegiveis': sum(1 for linha in linhas if linha['elegivel']), 'cfg': cfg,
    }


def chave_do_ranking(linha):
    """Maior % de conclusão; empate: mais no prazo, mais dias 100%, mais itens, nome.

    Quem não tem check-list montado na loja (nada previsto) vai para o fim.
    """
    return (not linha['previstos'], -linha['pct'], -linha['pct_prazo'], -linha['dias_100'],
            -linha['feitos'], linha['nome'].lower())
