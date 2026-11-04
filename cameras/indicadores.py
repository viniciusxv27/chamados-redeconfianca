"""Os números da aba Indicadores: clientes que entraram, matinal e tempo na mesa do vendedor.

Zero só aparece quando a câmera estava analisando. Sem cobertura no período o número é "sem medição":
dizer que uma loja não fez matinal porque o computador estava desligado seria acusar sem prova.
"""
from collections import Counter, defaultdict

from .models import EventoLoja, Gravador

TIPOS_DE_ZONA = {'entradas': 'PORTA', 'mesas': 'MESA', 'matinal': 'TELA'}


def formatar_duracao(segundos):
    if segundos is None:
        return '—'
    segundos = int(round(segundos))
    if segundos >= 3600:
        return f'{segundos // 3600}h{segundos % 3600 // 60:02d}'
    return f'{segundos // 60} min {segundos % 60:02d} s' if segundos >= 60 else f'{segundos} s'


def _zonas_por_tipo(gravador):
    tipos = set()
    for canal in gravador.canais.all():
        if canal.ativo and canal.ia_ativa:
            tipos.update(z.get('tipo', 'MOSTRUARIO') for z in canal.zonas or [])
    return tipos


def indicadores_do_periodo(de, ate, loja_id=None):
    gravadores = Gravador.objects.filter(tem_equipamento=True).prefetch_related('canais')
    if loja_id:
        gravadores = gravadores.filter(pk=loja_id)
    eventos = defaultdict(list)
    for e in EventoLoja.objects.filter(data__gte=de, data__lte=ate, gravador__in=gravadores).order_by('inicio'):
        eventos[e.gravador_id].append(e)
    dias = (ate - de).days + 1

    linhas = []
    for g in gravadores:
        dele = eventos[g.id]
        cobertos = {e.data for e in dele if e.tipo == 'COBERTURA'}
        zonas = _zonas_por_tipo(g)
        entradas = [e for e in dele if e.tipo == 'ENTRADA']
        atendimentos = [e for e in dele if e.tipo == 'ATENDIMENTO']
        matinais = [e for e in dele if e.tipo == 'MATINAL']
        por_hora = Counter(e.hora for e in entradas)
        pico = por_hora.most_common(1)[0] if por_hora else None
        por_mesa = defaultdict(list)
        for e in atendimentos:
            por_mesa[e.zona].append(e.duracao)
        # Janela da matinal coberta: a câmera analisou em alguma hora dentro dela naquele dia.
        horas_janela = range(g.matinal_inicio.hour, g.matinal_fim.hour + (1 if g.matinal_fim.minute else 0))
        dias_com_janela = {e.data for e in dele if e.tipo == 'COBERTURA' and e.hora in horas_janela}
        dias_com_matinal = {e.data for e in matinais}
        media = sum(e.duracao for e in atendimentos) / len(atendimentos) if atendimentos else None

        def situacao(chave):
            if not g.ultimo_contato:
                return 'Agente não instalado'
            if TIPOS_DE_ZONA[chave] not in zonas:
                return {'entradas': 'Marque a porta', 'mesas': 'Marque a mesa', 'matinal': 'Marque a tela'}[chave]
            return 'Sem medição' if not cobertos else ''

        linhas.append({
            'gravador': g, 'cobertos': len(cobertos), 'dias': dias,
            'entradas': len(entradas) if not situacao('entradas') else None, 'entradas_aviso': situacao('entradas'),
            'pico': f'{pico[0]:02d}h ({pico[1]})' if pico else '',
            'mesas_aviso': situacao('mesas'),
            'atendimentos': len(atendimentos) if not situacao('mesas') else None,
            'media': formatar_duracao(media), 'media_segundos': media,
            'maior': formatar_duracao(max((e.duracao for e in atendimentos), default=None)),
            'por_mesa': [{'nome': nome, 'quantos': len(d), 'media': formatar_duracao(sum(d) / len(d)),
                          'maior': formatar_duracao(max(d))} for nome, d in sorted(por_mesa.items())],
            'matinal_aviso': situacao('matinal') or ('' if dias_com_janela or dias_com_matinal else 'Sem medição'),
            'matinal_dias': len(dias_com_matinal), 'matinal_medidos': len(dias_com_janela | dias_com_matinal),
            'matinais': [{'data': e.data, 'inicio': e.inicio, 'duracao': formatar_duracao(e.duracao), 'pessoas': e.pessoas}
                         for e in matinais],
        })

    com_entradas = [l for l in linhas if l['entradas'] is not None]
    todos_atendimentos = [e for dele in eventos.values() for e in dele if e.tipo == 'ATENDIMENTO']
    medidas_matinal = [l for l in linhas if not l['matinal_aviso']]
    return {
        'linhas': sorted(linhas, key=lambda l: (l['entradas'] is None, -(l['entradas'] or 0), l['gravador'].loja)),
        'total_entradas': sum(l['entradas'] for l in com_entradas) if com_entradas else None,
        'lojas_com_medicao': sum(1 for l in linhas if l['cobertos']), 'total_lojas': len(linhas),
        'matinal_com': sum(1 for l in medidas_matinal if l['matinal_dias']), 'matinal_medidas': len(medidas_matinal),
        'total_atendimentos': len(todos_atendimentos) if todos_atendimentos else None,
        'media_geral': formatar_duracao(sum(e.duracao for e in todos_atendimentos) / len(todos_atendimentos)) if todos_atendimentos else '—',
    }
