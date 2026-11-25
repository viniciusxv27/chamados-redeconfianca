"""Assiduidade e marcação correta do ponto na rede inteira — mês a mês, por loja e por pessoa.

Pedido (25/11/2026), projeto de incentivo à marcação correta: o DP quer acompanhar mês a
mês a redução de esquecimentos e a evolução de cada colaborador e de cada loja. A diretoria
pediu "as métricas que já usamos para assiduidade, para toda a rede, para dar uma visão" —
a premiação estruturada fica para o plano de cargos e salários de 2027, então aqui não há
pagamento de Confianças: só a visão, com quem se destaca.

As métricas são as que o portal já usa, sobre as mesmas linhas do relatório
(``tangerino/pendencias.py``):

- **absenteísmo** (Plano de Carreira e ranking da Rotina ADM): horas que faltaram ÷ horas
  previstas. Dia sem batida conta inteiro; atraso/saída antes conta o que passa de 10 min;
  folga, feriado, férias e atestado não têm horas previstas e não entram;
- **faixa** da Rotina ADM: 0% excelente, até 2% boa (o limite do Plano de Carreira), até 5%
  regular, acima disso atenção;
- **faltas**: dias previstos sem batida nenhuma;
- **esquecimentos**: dias trabalhados com batida faltando (saída, volta do almoço ou dia
  em aberto) — o que vira ajuste manual;
- **marcação correta**: dias trabalhados sem esquecimento ÷ dias trabalhados;
- **ajustes**: pedidos de ajuste de ponto feitos pelo portal no mês (qualquer situação,
  menos cancelado).

A conta lê só o que já está sincronizado (``buscar=False``): olhar a rede inteira não
dispara busca no Tangerino. O resultado de cada mês fica no cache (mês fechado 6 h, o mês
corrente 15 min).
"""
import calendar
from datetime import date
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone

from . import pendencias as pendencias_svc

TOLERANCIA = pendencias_svc.TOLERANCIA_ABSENTEISMO
FAIXAS = ((Decimal('0'), 'Excelente', 'excelente'), (Decimal('2'), 'Boa', 'boa'),
          (Decimal('5'), 'Regular', 'regular'))
ESQUECIMENTOS = ('Faltou', 'em aberto', 'Sem intervalo registrado')
MESES = ('jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez')
VERSAO = 1


def faixa(pct):
    """(rótulo, classe) da faixa de absenteísmo — a mesma régua da Rotina ADM."""
    if pct is None:
        return '—', 'sem'
    for limite, rotulo, classe in FAIXAS:
        if pct <= limite:
            return rotulo, classe
    return 'Atenção', 'atencao'


def rotulo_do_mes(ano, mes):
    return f'{MESES[mes - 1]}/{str(ano)[2:]}'


def meses_ate(hoje, quantos):
    """[(ano, mes)] dos ``quantos`` meses até o de hoje, do mais antigo ao atual."""
    total = hoje.year * 12 + hoje.month - 1
    return [((t // 12), t % 12 + 1) for t in range(total - quantos + 1, total + 1)]


def _vazio():
    return {'previsto': 0, 'faltou': 0, 'dias_previstos': 0, 'trabalhados': 0, 'faltas': 0,
            'esquecimentos': 0, 'corretos': 0, 'ajustes': 0}


def _somar(destino, origem):
    for k, v in origem.items():
        destino[k] += v


def _metricas(c):
    """Os números de uma conta (pessoa, loja ou rede)."""
    pct = (Decimal(c['faltou'] * 100) / Decimal(c['previsto'])).quantize(Decimal('0.01')) if c['previsto'] else None
    correta = (Decimal(c['corretos'] * 100) / Decimal(c['trabalhados'])).quantize(Decimal('0.1')) \
        if c['trabalhados'] else None
    rotulo, classe = faixa(pct)
    return dict(c, absenteismo=pct, marcacao_correta=correta, faixa=rotulo, faixa_classe=classe)


def _contar_linhas(linhas):
    """{user_id: conta} a partir das linhas do relatório."""
    contas = {}
    for l in linhas:
        if not l['previsto_segundos'] and not l['batidas']:
            continue
        c = contas.setdefault(l['usuario'].pk, _vazio())
        motivos = ' '.join(l['pendencias'])
        previsto = l['previsto_segundos']
        if 'nenhuma batida' in motivos:
            c['faltas'] += 1
            c['dias_previstos'] += 1
            c['previsto'] += previsto
            c['faltou'] += previsto
            continue
        c['trabalhados'] += 1
        esqueceu = any(e in motivos for e in ESQUECIMENTOS)
        if esqueceu:
            c['esquecimentos'] += 1
        else:
            c['corretos'] += 1
        if not previsto:
            continue
        c['dias_previstos'] += 1
        if 'Faltou' in motivos or 'em aberto' in motivos:
            continue                    # batida esquecida: as horas não dizem nada (mesma regra do Progresso)
        c['previsto'] += previsto
        atraso = previsto - l['trabalhado_segundos']
        if atraso > TOLERANCIA:
            c['faltou'] += atraso
    return contas


def _ajustes(inicio, fim):
    from .models import SolicitacaoAjustePonto
    saida = {}
    for uid in (SolicitacaoAjustePonto.objects.filter(dia__gte=inicio, dia__lte=fim)
                .exclude(status=SolicitacaoAjustePonto.CANCELADA).values_list('usuario_id', flat=True)):
        saida[uid] = saida.get(uid, 0) + 1
    return saida


def mes(ano, mes_, hoje=None):
    """{user_id: {conta + loja_id}} de um mês (até ontem, se for o corrente). Cacheado."""
    hoje = hoje or timezone.localdate()
    inicio = date(ano, mes_, 1)
    fim = min(date(ano, mes_, calendar.monthrange(ano, mes_)[1]), hoje - timezone.timedelta(days=1))
    if fim < inicio:
        return {}
    corrente = (ano, mes_) == (hoje.year, hoje.month)
    chave = f'tangerino:assiduidade:v{VERSAO}:{ano}-{mes_:02d}:{fim:%Y%m%d}'
    guardado = cache.get(chave)
    if guardado is not None:
        return guardado
    linhas = pendencias_svc.linhas_do_periodo(inicio, fim, buscar=False)
    contas = _contar_linhas(linhas)
    lojas = {l['usuario'].pk: l['usuario'].sector_id for l in linhas}
    for uid, n in _ajustes(inicio, fim).items():
        if uid in contas:
            contas[uid]['ajustes'] = n
    for uid, c in contas.items():
        c['loja_id'] = lojas.get(uid)
    cache.set(chave, contas, 15 * 60 if corrente else 6 * 3600)
    return contas


def _delta(atual, anterior):
    if atual is None or anterior is None:
        return None
    return (atual - anterior).quantize(Decimal('0.1'))


def visao(meses=3, hoje=None, setor_id=None):
    """A visão da rede: meses (antigo → atual), lojas, pessoas e destaques do último mês fechado."""
    from users.models import Sector, User

    hoje = hoje or timezone.localdate()
    lista = meses_ate(hoje, max(2, min(int(meses or 3), 6)))
    por_mes = [(a, m, mes(a, m, hoje)) for a, m in lista]

    ids = {uid for _a, _m, contas in por_mes for uid in contas}
    usuarios = {u.pk: u for u in User.objects.filter(pk__in=ids).select_related('sector')}
    nomes_lojas = dict(Sector.objects.values_list('id', 'name'))

    rede = []
    lojas = {}
    pessoas = {}
    todas_as_lojas = set()
    for i, (a, m, contas) in enumerate(por_mes):
        total = _vazio()
        for uid, c in contas.items():
            conta = {k: v for k, v in c.items() if k != 'loja_id'}
            _somar(total, conta)
            loja_id = c.get('loja_id')
            todas_as_lojas.add(loja_id)
            if setor_id and loja_id != setor_id:
                continue
            loja = lojas.setdefault(loja_id, {'id': loja_id, 'nome': nomes_lojas.get(loja_id, 'Sem loja'),
                                              'meses': [_vazio() for _ in por_mes], 'pessoas': set()})
            _somar(loja['meses'][i], conta)
            loja['pessoas'].add(uid)
            pessoa = pessoas.setdefault(uid, {'user': usuarios.get(uid), 'loja': nomes_lojas.get(loja_id, 'Sem loja'),
                                              'loja_id': loja_id, 'meses': [None for _ in por_mes]})
            pessoa['meses'][i] = _metricas(conta)
        rede.append(dict(_metricas(total), rotulo=rotulo_do_mes(a, m)))

    rotulos = [rotulo_do_mes(a, m) for a, m, _c in por_mes]
    # O último mês fechado é o de referência (o corrente ainda está andando).
    ref = len(por_mes) - 2 if len(por_mes) >= 2 else 0
    saida_lojas = []
    for loja in lojas.values():
        metricas = [_metricas(c) for c in loja['meses']]
        atual, anterior = metricas[ref], metricas[ref - 1] if ref >= 1 else None
        saida_lojas.append({
            'id': loja['id'], 'nome': loja['nome'], 'pessoas': len(loja['pessoas']), 'meses': metricas,
            'evolucao_correta': _delta(atual['marcacao_correta'], anterior and anterior['marcacao_correta']),
            'evolucao_absenteismo': _delta(atual['absenteismo'], anterior and anterior['absenteismo']),
            'esquecimentos_antes': anterior['esquecimentos'] if anterior else None,
        })
    saida_lojas.sort(key=lambda l: l['nome'].upper())

    saida_pessoas = []
    for p in pessoas.values():
        if p['user'] is None:
            continue
        atual = p['meses'][ref]
        anterior = p['meses'][ref - 1] if ref >= 1 else None
        p['nome'] = (p['user'].get_full_name() or p['user'].get_username()).strip()
        p['ref'] = atual
        p['evolucao_correta'] = _delta(atual and atual['marcacao_correta'],
                                       anterior and anterior['marcacao_correta'])
        saida_pessoas.append(p)
    saida_pessoas.sort(key=lambda p: (p['loja'].upper(), p['nome'].upper()))

    # Regularidade exemplar no mês de referência: veio, bateu tudo certo e não pediu ajuste.
    destaques = [p for p in saida_pessoas
                 if p['ref'] and p['ref']['dias_previstos'] >= 10 and not p['ref']['faltas']
                 and not p['ref']['esquecimentos'] and not p['ref']['ajustes'] and p['ref']['absenteismo'] == 0]
    destaques.sort(key=lambda p: (-p['ref']['trabalhados'], p['nome'].upper()))

    def chave_evolucao(l):
        return (l['evolucao_correta'] is None, -(l['evolucao_correta'] or 0), l['nome'].upper())
    evolucao = [l for l in sorted(saida_lojas, key=chave_evolucao) if l['evolucao_correta'] is not None]

    return {'rotulos': rotulos, 'ref': ref, 'ref_rotulo': rotulos[ref], 'rede': rede,
            'lojas': saida_lojas, 'pessoas': saida_pessoas, 'destaques': destaques,
            'evolucao': evolucao[:5], 'piora': [l for l in reversed(evolucao) if (l['evolucao_correta'] or 0) < 0][:5],
            'setores': sorted(((i, nomes_lojas[i]) for i in todas_as_lojas if i in nomes_lojas),
                              key=lambda s: s[1].upper()),
            'gerado_em': timezone.localtime()}
