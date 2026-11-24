"""Ranking dos analistas da Rotina ADM (12/11/2026) e o que o espelho no Kanban precisa.

Pontos (24/11/2026): o ranking virou um jogo de pontos com quatro frentes — check-list,
pendências fiscais, contagem de estoque e assiduidade (ver ``PONTOS`` e ``pontos_da_linha``).
A ordem é pelo total de pontos do mês; as estatísticas do check-list abaixo seguem
valendo para o broche. Os pontos contam até agora (o que se faz hoje já soma); as
porcentagens do check-list e a assiduidade, só até ontem (dia fechado).

Check-list diário da loja: para cada analista conta, no mês escolhido:

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
import hashlib
import logging
from collections import Counter, defaultdict
from datetime import date, timedelta

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from . import servicos

logger = logging.getLogger(__name__)

# Quanto vale cada coisa no ranking (24/11/2026).
PONTOS = {
    'item_no_prazo': 2,        # item do check-list marcado até o horário dele (ou item sem horário)
    'item_atrasado': 1,        # item marcado depois do horário
    'dia_100': 3,              # bônus: o analista fez todos os itens previstos da loja no dia
    'fiscal': 10,              # venda "Sem NF" da Visão SAP que o analista deu como resolvida
    'estoque': 10,             # categoria do estoque LVUT contada na semana (uma vez por categoria e semana)
}
# Assiduidade do mês (absenteísmo do ponto, dias fechados): até o limite (%) vale os pontos.
ASSIDUIDADE = ((0, 150), (2, 100), (5, 50))
# Níveis pelo total de pontos do mês.
NIVEIS = (('Bronze', 0, 'fa-medal', 'bronze'), ('Prata', 150, 'fa-medal', 'prata'),
          ('Ouro', 300, 'fa-crown', 'ouro'), ('Diamante', 500, 'fa-gem', 'diamante'))
FRENTES = (('checklist', 'Check-list', 'fa-list-check', 'verde'),
           ('fiscal', 'Pendências fiscais', 'fa-file-invoice', 'ambar'),
           ('estoque', 'Contagem de estoque', 'fa-cubes', 'roxo'),
           ('assiduidade', 'Assiduidade', 'fa-user-clock', 'azul'))


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


def pontos_assiduidade(pct):
    """Pontos do mês pelo absenteísmo (%): 0% = 150, até 2% = 100, até 5% = 50, acima = 0."""
    if pct is None:
        return 0
    for limite, pontos in ASSIDUIDADE:
        if pct <= limite:
            return pontos
    return 0


def nivel(pontos):
    """O nível do analista pelo total do mês e quanto falta para o próximo."""
    atual = NIVEIS[0]
    for n in NIVEIS:
        if pontos >= n[1]:
            atual = n
    proximo = next((n for n in NIVEIS if n[1] > pontos), None)
    progresso = 100
    if proximo:
        progresso = round((pontos - atual[1]) * 100 / (proximo[1] - atual[1]))
    return {'nome': atual[0], 'icone': atual[2], 'classe': atual[3],
            'proximo': proximo[0] if proximo else '', 'falta': (proximo[1] - pontos) if proximo else 0,
            'progresso': progresso}


def _fiscal(pids, inicio, limite):
    """{pessoa: (pontuadas, não confirmadas)} das vendas "Sem NF" que cada um deu como resolvidas no mês.

    Não pontua a marcada como resolvida que o SAP leu de novo e continua mostrando (não saiu de verdade).
    None se a Visão SAP estiver fora.
    """
    try:
        from auditoria_sap.models import (LinhaAuditoria, filtro_resolvidas_pendentes, inicio_da_ultima_leitura,
                                          q_da_tela)
        base = LinhaAuditoria.objects.filter(q_da_tela('sem_nf'), resolvida=True, resolvida_por_id__in=list(pids),
                                             resolvida_em__date__gte=inicio, resolvida_em__date__lte=limite)
        pendentes = filtro_resolvidas_pendentes(inicio_da_ultima_leitura())
        saida = defaultdict(lambda: [0, 0])
        for quem in base.exclude(pendentes).values_list('resolvida_por_id', flat=True):
            saida[quem][0] += 1
        for quem in base.filter(pendentes).values_list('resolvida_por_id', flat=True):
            saida[quem][1] += 1
        return {k: tuple(v) for k, v in saida.items()}
    except Exception:  # noqa: BLE001 — o ranking não cai se a Visão SAP estiver fora
        logger.warning('Rotina ADM: pendências fiscais fora do ranking', exc_info=True)
        return None


def _estoque(inicio, limite):
    """{pessoa: [(loja, categoria, segunda da semana)]} das contagens que pontuam no período.

    Cada categoria pontua uma vez por loja e semana, para quem a contou primeiro.
    """
    from .models import ContagemEstoque
    vistos, saida = set(), defaultdict(list)
    for quem, loja_id, categoria, dia in (ContagemEstoque.objects.filter(data__gte=inicio, data__lte=limite)
                                          .order_by('feito_em', 'id')
                                          .values_list('feito_por_id', 'loja_id', 'categoria', 'data')):
        chave = (loja_id, categoria, dia - timedelta(days=dia.weekday()))
        if chave in vistos or not quem:
            continue
        vistos.add(chave)
        saida[quem].append(chave)
    return saida


def _assiduidade(pessoas, inicio, ate):
    """{pessoa: absenteísmo do mês} de quem bate ponto, nos dias fechados (guardado por 10 min)."""
    com_ponto = [u for u in pessoas if getattr(u, 'tangerino_employee_id', None)]
    if not com_ponto or ate < inicio:
        return {}
    assinatura = hashlib.sha1(','.join(str(u.pk) for u in sorted(com_ponto, key=lambda u: u.pk)).encode()).hexdigest()[:12]
    chave = f'rotina_adm:ranking:ponto:v1:{inicio:%Y%m%d}:{ate:%Y%m%d}:{assinatura}'
    guardado = cache.get(chave)
    if guardado is not None:
        return guardado
    try:
        from tangerino import pendencias
        linhas = pendencias.linhas_do_periodo(inicio, ate, usuarios=com_ponto, buscar=False)
        valor = pendencias.absenteismo_das_linhas(linhas)
    except Exception:  # noqa: BLE001 — sem o ponto, a assiduidade fica sem dado
        logger.warning('Rotina ADM: ponto fora do ranking', exc_info=True)
        return {}
    cache.set(chave, valor, 600)
    return valor


def calcular(mes_texto=None, cfg=None, hoje=None):
    """Linhas do ranking do mês, já ordenadas, e o resumo. Ver o docstring do módulo."""
    from users.models import User

    from .models import ConfiguracaoRotinaAdm, MarcacaoChecklist
    cfg = cfg or ConfiguracaoRotinaAdm.get()
    hoje = hoje or timezone.localdate()
    inicio, fim, ate = periodo(mes_texto, hoje)
    limite = min(fim, hoje)                         # os pontos contam até hoje; as porcentagens, até ontem
    marcacoes = list(MarcacaoChecklist.objects.filter(data__gte=inicio, data__lte=limite)
                     .values_list('feito_por_id', 'item_id', 'item__loja_id', 'item__horario', 'data', 'feito_em'))
    estoque = _estoque(inicio, limite)

    pessoas = {u.id: u for u in analistas_com_acesso()}
    faltam = ({m[0] for m in marcacoes if m[0]} | set(estoque)) - set(pessoas)
    for u in User.objects.filter(id__in=faltam).select_related('sector'):
        pessoas[u.id] = u

    # Loja de cada um: setor principal; quem não é de loja fica com a loja em que mais marcou/contou.
    marcou_em = defaultdict(Counter)
    for quem, _i, loja_id, *_ in marcacoes:
        if quem:
            marcou_em[quem][loja_id] += 1
    contou_em = defaultdict(Counter)
    for quem, chaves in estoque.items():
        for loja_id, *_ in chaves:
            contou_em[quem][loja_id] += 1
    loja_de = {}
    for pid, u in pessoas.items():
        if servicos.e_loja(u.sector):
            loja_de[pid] = u.sector_id
        elif marcou_em[pid] or contou_em[pid]:
            loja_de[pid] = (marcou_em[pid] + contou_em[pid]).most_common(1)[0][0]

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

    def no_prazo(horario, feito_em):
        return horario is None or timezone.localtime(feito_em).time() <= horario

    fiscal = _fiscal(loja_de.keys(), inicio, limite)
    ponto = _assiduidade([pessoas[pid] for pid in loja_de], inicio, ate)

    from users.models import Sector
    nomes_lojas = dict(Sector.objects.filter(id__in=set(loja_de.values())).values_list('id', 'name'))
    linhas = []
    for pid, loja_id in loja_de.items():
        u = pessoas[pid]
        todas = da_pessoa.get((pid, loja_id), [])
        feitas = [f for f in todas if f[2] <= ate]  # as porcentagens só olham dia fechado
        total_previsto = sum(len(previstos_do_dia(loja_id, d)) for d in dias)
        prazo = sum(1 for _i, horario, _d, feito_em in feitas if no_prazo(horario, feito_em))
        por_dia = defaultdict(set)
        for item_id, _h, d, _f in todas:
            por_dia[d].add(item_id)
        completos = [d for d, itens in por_dia.items()
                     if previstos_do_dia(loja_id, d) and previstos_do_dia(loja_id, d) <= itens]
        dias_100 = sum(1 for d in completos if d <= ate)
        pct = round(len(feitas) * 100 / total_previsto, 1) if total_previsto else 0.0
        pct = min(pct, 100.0)
        pct_prazo = round(prazo * 100 / len(feitas), 1) if feitas else 0.0
        outras = sum(n for loja, n in marcou_em[pid].items() if loja != loja_id)

        # ── Pontos ──
        itens_prazo = sum(1 for _i, horario, _d, feito_em in todas if no_prazo(horario, feito_em))
        pts_check = (itens_prazo * PONTOS['item_no_prazo'] + (len(todas) - itens_prazo) * PONTOS['item_atrasado']
                     + len(completos) * PONTOS['dia_100'])
        resolvidas, nao_confirmadas = (fiscal or {}).get(pid, (0, 0))
        categorias = len(estoque.get(pid, ()))
        absenteismo = ponto.get(pid)
        pontos = {
            'checklist': pts_check,
            'fiscal': resolvidas * PONTOS['fiscal'],
            'estoque': categorias * PONTOS['estoque'],
            'assiduidade': pontos_assiduidade(absenteismo['pct'] if absenteismo else None),
        }
        total = sum(pontos.values())
        linhas.append({
            'pessoa': u, 'nome': u.get_full_name() or u.username, 'loja_id': loja_id,
            'loja': nomes_lojas.get(loja_id, ''), 'previstos': total_previsto, 'feitos': len(feitas),
            'no_prazo': prazo, 'pct': pct, 'pct_prazo': pct_prazo, 'dias_100': dias_100,
            'dias_com_entrega': len(por_dia), 'outras_lojas': outras,
            'elegivel': bool(total_previsto and pct >= cfg.broche_pct_minimo
                             and pct_prazo >= cfg.broche_no_prazo_minimo),
            'pontos': pontos, 'total': total, 'nivel': nivel(total),
            'detalhe': {
                'itens': len(todas), 'itens_prazo': itens_prazo, 'dias_completos': len(completos),
                'resolvidas': resolvidas, 'nao_confirmadas': nao_confirmadas, 'fiscal_fora': fiscal is None,
                'categorias': categorias, 'absenteismo': absenteismo,
                'bate_ponto': bool(getattr(u, 'tangerino_employee_id', None)),
            },
        })

    linhas.sort(key=chave_do_ranking)
    for n, linha in enumerate(linhas, start=1):
        linha['posicao'] = n
    colegas = Counter(linha['loja_id'] for linha in linhas)
    for linha in linhas:
        linha['divide_loja'] = colegas[linha['loja_id']] > 1
    lider = linhas[0]['total'] if linhas else 0
    for linha in linhas:
        linha['pct_do_lider'] = round(linha['total'] * 100 / lider) if lider else 0
    return {
        'inicio': inicio, 'fim': fim, 'ate': ate, 'dias_contados': len(dias), 'linhas': linhas,
        'elegiveis': sum(1 for linha in linhas if linha['elegivel']), 'cfg': cfg,
        'fiscal_fora': fiscal is None,
    }


def chave_do_ranking(linha):
    """Mais pontos; empate: maior % de conclusão, mais no prazo, mais dias 100%, mais itens, nome.

    No empate, quem não tem check-list montado na loja (nada previsto) fica atrás.
    """
    return (-linha['total'], not linha['previstos'], -linha['pct'], -linha['pct_prazo'], -linha['dias_100'],
            -linha['feitos'], linha['nome'].lower())
