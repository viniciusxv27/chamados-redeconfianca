"""O dia de ponto de cada pessoa, linha a linha, com a pendência em português.

É o mesmo cálculo para as duas saídas: o relatório exportável (/ponto/relatorio/,
com atalho em /folha-ponto/admin/) e a análise que vai pelo WhatsApp
(``tangerino/analise.py``). Um lugar só, para os dois nunca discordarem.

De onde vem cada coluna:

- as batidas, o trabalhado e o previsto saem de ``MarcacaoPonto`` — a tabela que
  a sincronização do Tangerino mantém, um registro por pessoa e dia;
- o intervalo é o tempo entre a 2ª e a 3ª batida (saída e volta do almoço);
- as horas extras são o que passou do previsto do dia, nunca negativo;
- a loja é o setor principal da pessoa (``user.sector``), como no resto do portal.

**Dia sem batida nenhuma não tem linha em ``MarcacaoPonto``** — a API só entrega
o que foi marcado. Para saber se a pessoa deveria ter trabalhado, o previsto é
procurado em três fontes, nesta ordem:

1. a jornada contratada no Tangerino (a mesma que a sincronização usa);
2. a escala montada no portal (/ponto/escala/), quando a jornada não veio;
3. o costume da própria pessoa: o previsto que aparece nas marcações dela
   naquele dia da semana, nas últimas semanas.

Sem nenhuma das três, o dia fica "sem previsão" e não vira cobrança — melhor
não avisar do que acusar folga de falta.

As pendências são macro, em texto pronto para ler no relatório e no WhatsApp:
sem batida nenhuma, falta de uma batida específica, dia em aberto, almoço curto,
dia sem intervalo e batidas além das quatro.
"""
import logging
from collections import OrderedDict
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from . import feriados
from .models import CoberturaPonto, EscalaDia, MarcacaoPonto, minutos_do_dia

logger = logging.getLogger(__name__)
User = get_user_model()

INTERVALO_MINIMO = 60 * 60                 # CLT: jornada acima de 6h pede 1 hora de intervalo
JORNADA_QUE_EXIGE_INTERVALO = 6 * 60 * 60
# "Costume da pessoa": quantas semanas olhar para trás e quantas vezes o mesmo
# previsto precisa aparecer num dia da semana para valer como jornada dela.
SEMANAS_DE_COSTUME = 8
REPETICOES_PARA_COSTUME = 2


def hhmm(segundos):
    """Segundos em HH:MM. Vazio vira '00:00'; negativo nunca acontece aqui."""
    segundos = max(0, int(segundos or 0))
    return f'{segundos // 3600:02d}:{(segundos % 3600) // 60:02d}'


def _hora(valor):
    return timezone.localtime(valor).strftime('%H:%M') if valor else ''


def dias_do_periodo(inicio, fim):
    dia = inicio
    while dia <= fim:
        yield dia
        dia += timedelta(days=1)


# ---------------------------------------------------------------------------
# Previsto do dia
# ---------------------------------------------------------------------------
def grades_do_tangerino():
    """{employee_id: {dia_da_semana: segundos}} da jornada contratada.

    Vem do mesmo caminho da sincronização. Se a API estiver fora do ar, devolve
    vazio: o relatório continua saindo com as outras fontes de previsto.
    """
    try:
        from .sync import _grades_por_funcionario
        return _grades_por_funcionario()
    except Exception as exc:                                        # noqa: BLE001
        logger.warning('Jornadas do Tangerino não vieram (%s); o previsto usa a escala do portal.', exc)
        return {}


def _escalas_do_portal(pessoas, inicio, fim):
    """{(user_id, data): segundos previstos} pela escala montada em /ponto/escala/."""
    ids = [p.id for p in pessoas]
    if not ids:
        return {}
    dias = (EscalaDia.objects
            .filter(escala__colaborador_id__in=ids, data__gte=inicio, data__lte=fim)
            .select_related('escala'))
    saida = {}
    for dia in dias:
        minutos = minutos_do_dia(dia.entrada, dia.saida_almoco, dia.volta_almoco, dia.saida, dia.folga)
        saida[(dia.escala.colaborador_id, dia.data)] = minutos * 60
    return saida


def _costume(marcacoes):
    """{(employee_id, dia_da_semana): segundos} — o previsto que se repete naquele dia da semana."""
    contagem = {}
    for m in marcacoes:
        if not m.previsto_segundos:
            continue
        contagem.setdefault((m.employee_id, m.data.weekday()), []).append(m.previsto_segundos)
    saida = {}
    for chave, valores in contagem.items():
        mais_comum = max(set(valores), key=valores.count)
        if valores.count(mais_comum) >= REPETICOES_PARA_COSTUME:
            saida[chave] = mais_comum
    return saida


# ---------------------------------------------------------------------------
# A linha de um dia
# ---------------------------------------------------------------------------
def _batidas(marcacao):
    """As quatro batidas do cartão (entrada, saída para o almoço, volta, saída)."""
    if marcacao is None:
        return ['', '', '', '']
    return [_hora(marcacao.entrada1), _hora(marcacao.saida1),
            _hora(marcacao.entrada2), _hora(marcacao.saida2)]


def _batidas_extras(marcacao):
    """O 3º par e o que passou dele: aparece na pendência, nunca some."""
    if marcacao is None:
        return []
    extras = [_hora(marcacao.entrada3), _hora(marcacao.saida3)]
    return [x for x in extras if x] + list(marcacao.marcacoes_extras or [])


def intervalo_segundos(marcacao):
    """Tempo entre a 2ª e a 3ª batida — o almoço. None quando o dia não tem as duas."""
    if marcacao is None or not (marcacao.saida1 and marcacao.entrada2):
        return None
    return max(0, int((marcacao.entrada2 - marcacao.saida1).total_seconds()))


def _sobreposicao(marcacao):
    """Dois períodos do mesmo dia que se cruzam, em texto. '' quando está tudo certo.

    Acontece quando o Tangerino guarda dois pares aprovados para o mesmo turno
    (correção lançada sem apagar a batida original). É raro — 4 dos 5.563 dias
    espelhados — mas o dia fica com hora a mais, e quem vê o cartão precisa
    saber por quê.
    """
    pares = [(marcacao.entrada1, marcacao.saida1), (marcacao.entrada2, marcacao.saida2),
             (marcacao.entrada3, marcacao.saida3)]
    pares = sorted((a, b) for a, b in pares if a and b)
    cruza = any(pares[i - 1][1] > a for i, (a, _) in enumerate(pares) if i)
    if not cruza:
        return ''
    # Mostra o dia inteiro, e não só o par que cruza: é a comparação entre os
    # dois horários que explica o problema para quem vai corrigir.
    return ' e '.join(f'{_hora(a)}–{_hora(b)}' for a, b in pares)


def pendencias_do_dia(marcacao, previsto):
    """Os motivos, em texto, do que está torto no dia. Lista vazia = dia certo."""
    motivos = []
    if marcacao is None:
        if previsto:
            motivos.append('Não houve nenhuma batida no dia')
        return motivos

    marcadas = [marcacao.entrada1, marcacao.saida1, marcacao.entrada2, marcacao.saida2]
    if not any(marcadas) and not _batidas_extras(marcacao):
        motivos.append('Não houve nenhuma batida no dia')
        return motivos

    # Entrada sem saída: o dia ficou em aberto e a batida que falta tem nome.
    if marcacao.entrada1 and not marcacao.saida1:
        motivos.append('Faltou a 2ª batida (saída)')
    elif marcacao.entrada2 and not marcacao.saida2:
        motivos.append('Faltou a 4ª batida (saída)')
    elif marcacao.entrada3 and not marcacao.saida3:
        motivos.append('Faltou a saída do 3º período')
    elif marcacao.em_aberto:
        motivos.append('Dia em aberto: entrada sem saída')

    intervalo = intervalo_segundos(marcacao)
    exige_intervalo = (previsto or 0) > JORNADA_QUE_EXIGE_INTERVALO
    if intervalo is None:
        if exige_intervalo and marcacao.entrada1 and marcacao.saida1 and not marcacao.entrada2:
            motivos.append('Sem intervalo registrado (faltaram a 3ª e a 4ª batidas)')
    elif intervalo < INTERVALO_MINIMO and exige_intervalo:
        motivos.append(f'Almoço inferior a uma hora ({intervalo // 60} min)')

    sobrepostas = _sobreposicao(marcacao)
    if sobrepostas:
        motivos.append(f'Batidas sobrepostas no dia ({sobrepostas})')

    extras = _batidas_extras(marcacao)
    if extras:
        motivos.append(f'Mais de 4 batidas no dia ({", ".join(extras)})')
    return motivos


def _linha(pessoa, dia, marcacao, previsto):
    trabalhado = int(getattr(marcacao, 'total_segundos', 0) or 0)
    intervalo = intervalo_segundos(marcacao)
    motivos = pendencias_do_dia(marcacao, previsto)
    return {
        'usuario': pessoa,
        'nome': pessoa.full_name or pessoa.get_username(),
        'loja': pessoa.sector.name if getattr(pessoa, 'sector_id', None) else 'Sem loja',
        'data': dia,
        'batidas': _batidas(marcacao),
        'extras': _batidas_extras(marcacao),
        'previsto_segundos': int(previsto or 0),
        'trabalhado_segundos': trabalhado,
        'intervalo_segundos': intervalo,
        'extras_segundos': max(0, trabalhado - int(previsto or 0)),
        'previsto': hhmm(previsto),
        'trabalhado': hhmm(trabalhado),
        'intervalo': hhmm(intervalo) if intervalo is not None else '',
        'horas_extras': hhmm(max(0, trabalhado - int(previsto or 0))),
        'pendencias': motivos,
        'pendencia': '; '.join(motivos),
        'tem_pendencia': bool(motivos),
    }


# ---------------------------------------------------------------------------
# O período inteiro
# ---------------------------------------------------------------------------
def pessoas_do_ponto(setor_id=None, usuarios=None):
    """Quem tem ponto no Tangerino, na ordem em que o relatório lista (loja, nome)."""
    pessoas = (User.objects.filter(is_active=True, tangerino_employee_id__isnull=False)
               .select_related('sector'))
    if setor_id:
        pessoas = pessoas.filter(sector_id=setor_id)
    if usuarios:
        pessoas = pessoas.filter(pk__in=[u.pk if hasattr(u, 'pk') else u for u in usuarios])
    return sorted(pessoas, key=lambda p: ((p.sector.name if p.sector_id else 'zzz').upper(),
                                          (p.full_name or p.get_username()).upper()))


def garantir_cobertura(employee_ids, inicio, fim):
    """Busca no Tangerino o que o espelho local ainda não tem para o período.

    A sincronização diária volta 30 dias. Um relatório de seis meses atrás não
    tinha o que ler na tabela e saía acusando falta em dia trabalhado — 102 dos
    135 dias, no pedido que mostrou o problema. Então o relatório busca o que
    falta, uma chamada por pessoa (em paralelo), e guarda a faixa buscada.

    Falha da API não derruba a tela: devolve o erro para quem chamou avisar, e
    o período não coberto fica de fora da conta em vez de virar cobrança.
    """
    from core.utils import processo_de_teste

    from .client import integracao_ativa

    faltando = CoberturaPonto.falta_buscar(employee_ids, inicio, fim)
    if not faltando:
        return {'buscou': 0, 'erro': ''}
    if not integracao_ativa():
        return {'buscou': 0, 'erro': 'A integração com o Tangerino está desligada.'}
    # Um teste que pedisse um período antigo varreria a empresa inteira na API
    # de verdade (um pedido por pessoa). Quem quiser exercitar a busca troca
    # `sync.sincronizar_periodo` por um dublê, como nos testes deste módulo.
    if processo_de_teste():
        return {'buscou': 0, 'erro': ''}

    # Uma faixa só para todo mundo: a API cobra por pessoa, não por dia.
    de = min(f[0] for f in faltando.values())
    ate = max(f[1] for f in faltando.values())
    try:
        from . import sync
        resultado = sync.sincronizar_periodo(de, ate, employee_ids=list(faltando))
    except Exception as exc:                            # noqa: BLE001 — tela não cai por causa da API
        logger.warning('Não foi possível completar o ponto de %s a %s: %s', de, ate, exc)
        return {'buscou': 0, 'erro': f'Não foi possível buscar no Tangerino: {exc}'}
    logger.info('Relatório de ponto completou %s pessoa(s) de %s a %s: %s dia(s).',
                len(faltando), de, ate, resultado.get('dias'))
    return {'buscou': len(faltando), 'de': de, 'ate': ate,
            'dias': resultado.get('dias', 0), 'erro': ''}


def linhas_do_periodo(inicio, fim, setor_id=None, usuarios=None, apenas_com_pendencia=False,
                      buscar=True, aviso=None):
    """Uma linha por pessoa e dia do período, já com a pendência em texto.

    Dia de folga sem batida nenhuma não vira linha: o relatório fala dos dias em
    que a pessoa deveria estar lá ou em que houve alguma marcação.

    Antes de montar as linhas, completa no Tangerino o que falta do período
    (``buscar=False`` desliga isso). Dia fora do que já foi buscado não entra:
    não dá para dizer que faltou batida num dia que ninguém foi conferir. Quem
    passa ``aviso`` (um dicionário) recebe de volta o que aconteceu na busca,
    para a tela contar.
    """
    pessoas = pessoas_do_ponto(setor_id, usuarios)
    if not pessoas:
        return []
    ids = [p.tangerino_employee_id for p in pessoas]

    resultado_busca = garantir_cobertura(ids, inicio, fim) if buscar else {'buscou': 0, 'erro': ''}
    if aviso is not None:
        aviso.update(resultado_busca)
    cobertura = CoberturaPonto.mapa(ids)

    marcacoes = list(MarcacaoPonto.objects.filter(employee_id__in=ids, data__gte=inicio, data__lte=fim))
    por_dia = {(m.employee_id, m.data): m for m in marcacoes}
    # O costume olha um pedaço maior do que o período pedido: um relatório de
    # um dia só não teria repetição nenhuma para aprender.
    historico = MarcacaoPonto.objects.filter(
        employee_id__in=ids, data__gte=inicio - timedelta(weeks=SEMANAS_DE_COSTUME), data__lte=fim)
    costume = _costume(historico)
    grades = grades_do_tangerino()
    escalas = _escalas_do_portal(pessoas, inicio, fim)
    abonos = abonos_do_periodo(inicio, fim)

    linhas = []
    for pessoa in pessoas:
        eid = pessoa.tangerino_employee_id
        for dia in dias_do_periodo(inicio, fim):
            marcacao = por_dia.get((eid, dia))
            previsto = _previsto_do_dia(eid, pessoa, dia, marcacao, grades, escalas,
                                        costume, abonos)
            if marcacao is None and not previsto:
                continue                               # folga sem batida: não é assunto do relatório
            if marcacao is None and not _coberto(cobertura, eid, dia):
                continue                               # dia que ninguém buscou: não é falta, é desconhecido
            linha = _linha(pessoa, dia, marcacao, previsto)
            if apenas_com_pendencia and not linha['tem_pendencia']:
                continue
            linhas.append(linha)
    return linhas


def _coberto(cobertura, eid, dia):
    """O dia está dentro do que já foi buscado no Tangerino para essa pessoa?"""
    faixa = cobertura.get(eid)
    return bool(faixa and faixa[0] <= dia <= faixa[1])


def abonos_do_periodo(inicio, fim):
    """Feriado, férias, atestado e folga do período: {(employee_id, dia): segundos}.

    É a mesma fonte que a sincronização usa para o previsto de um dia COM
    batida. Sem ela, o dia sem batida caía no previsto cheio da jornada e o
    feriado virava falta — foi o que sobrou no relatório de seis meses: as
    quatro "faltas" eram 03/04, 21/04, 01/05 e 04/06.

    Falha da API não derruba a tela: sem abono nenhum o relatório continua de
    pé, só mais rigoroso.
    """
    from .jornada import carregar_abonos

    try:
        return carregar_abonos(inicio, fim)
    except Exception as exc:                            # noqa: BLE001 — leitura auxiliar
        logger.warning('Abonos do período %s a %s indisponíveis: %s', inicio, fim, exc)
        return {}


def _previsto_do_dia(eid, pessoa, dia, marcacao, grades, escalas, costume, abonos=None):
    """O previsto do dia, na ordem: marcação sincronizada, jornada, escala do portal, costume.

    O dia abonado (feriado, férias, atestado) não tem previsto: a pessoa não
    devia estar lá, e cobrar batida dela é a falta que não existe.
    """
    if marcacao is not None:
        return int(marcacao.previsto_segundos or 0)

    abonado = (abonos or {}).get((eid, dia), 0)
    if abonado is None:
        return 0                                   # dia inteiro abonado
    if feriados.e_feriado(dia):
        return 0                                   # feriado nacional: não se cobra batida

    from .jornada import previsto_no_dia

    grade = (grades or {}).get(eid)
    if grade:
        bruto = int(previsto_no_dia(grade, dia) or 0)
    elif (pessoa.id, dia) in escalas:
        bruto = int(escalas[(pessoa.id, dia)] or 0)
    else:
        bruto = int(costume.get((eid, dia.weekday()), 0) or 0)
    return max(0, bruto - int(abonado or 0))


def por_loja(linhas):
    """As linhas agrupadas por loja, na ordem da loja e do nome — como a mensagem pede."""
    grupos = OrderedDict()
    for linha in linhas:
        grupos.setdefault(linha['loja'], []).append(linha)
    return grupos


def resumo(linhas):
    """Os números do topo do relatório."""
    com_pendencia = [l for l in linhas if l['tem_pendencia']]
    return {
        'dias': len(linhas),
        'pessoas': len({l['usuario'].id for l in linhas}),
        'com_pendencia': len(com_pendencia),
        'sem_batida': sum(1 for l in linhas if 'Não houve nenhuma batida no dia' in l['pendencias']),
        'previsto': hhmm(sum(l['previsto_segundos'] for l in linhas)),
        'trabalhado': hhmm(sum(l['trabalhado_segundos'] for l in linhas)),
        'horas_extras': hhmm(sum(l['extras_segundos'] for l in linhas)),
    }

