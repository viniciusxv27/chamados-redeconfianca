"""Os números da aba Indicadores: clientes que entraram, matinal e tempo na mesa do vendedor.

Regras para o número ser assertivo:

- **Zero só com prova.** Cada câmera manda, a cada hora que analisou, um evento COBERTURA. Uma medição só
  vale nas horas em que a câmera *daquela área* (porta, mesa, tela) estava analisando: a câmera do caixa
  ligada não prova nada sobre a porta. Sem cobertura é "Sem medição", nunca zero. Cobertura parcial do
  horário da loja aparece como tal, para o número não ser lido como o dia inteiro.
- **Mesa é por câmera.** Duas câmeras podem ter uma "Mesa 1": cada uma conta separada.
- **Ocupação longa não é atendimento.** Cadeira ocupada por mais de ``ATENDIMENTO_MAXIMO`` é, quase sempre,
  funcionário sentado, bolsa ou objeto: sai da média e aparece à parte.
- **O ponto é a segunda testemunha.** As entradas da porta incluem a equipe; as batidas de entrada no ponto
  (Tangerino) dão a estimativa de clientes. Matinal "não" com ninguém de ponto batido vira "sem equipe".
"""
import csv
import io
import statistics
from collections import Counter, defaultdict
from datetime import timedelta

from .models import EventoLoja, Gravador
from . import ponto

TIPOS_DE_ZONA = {'entradas': 'PORTA', 'mesas': 'MESA', 'matinal': 'TELA'}
ATENDIMENTO_MINIMO = 60
ATENDIMENTO_MAXIMO = 90 * 60
COBERTURA_PARCIAL = 80          # % do horário da loja: abaixo disso o número é parcial


def formatar_duracao(segundos):
    if segundos is None:
        return '—'
    segundos = int(round(segundos))
    if segundos >= 3600:
        return f'{segundos // 3600}h{segundos % 3600 // 60:02d}'
    return f'{segundos // 60} min {segundos % 60:02d} s' if segundos >= 60 else f'{segundos} s'


def _horas_abertas(g):
    fim = g.fecha_as.hour + (1 if g.fecha_as.minute else 0)
    return set(range(g.abre_as.hour, max(g.abre_as.hour + 1, fim)))


def _horas_matinal(g):
    fim = g.matinal_fim.hour + (1 if g.matinal_fim.minute else 0)
    return set(range(g.matinal_inicio.hour, max(g.matinal_inicio.hour + 1, fim)))


def _canais_por_tipo(g):
    """{tipo: {canal_id}} das câmeras ativas e analisando que têm área daquele tipo marcada."""
    saida = defaultdict(set)
    for canal in g.canais.all():
        if canal.ativo and canal.ia_ativa:
            for z in canal.zonas or []:
                saida[z.get('tipo', 'MOSTRUARIO')].add(canal.id)
    return saida


def _dias(de, ate):
    return [de + timedelta(days=i) for i in range((ate - de).days + 1)]


def _mediana(valores):
    return statistics.median(valores) if valores else None


class _Loja:
    """Junta os eventos de uma loja no período e responde as perguntas da tela."""

    def __init__(self, g, de, ate, cobertura, eventos, equipe, ponto_ate=None):
        self.g, self.de, self.ate = g, de, ate
        self.dias = _dias(de, ate)
        self.canais = _canais_por_tipo(g)
        self.abertas = _horas_abertas(g)
        self.equipe = equipe
        # A loja usa o ponto se ele chegou do Tangerino há pouco; um dia depois do último sincronizado não conta.
        self.ponto_ate = ponto_ate
        self.tem_ponto = bool(ponto_ate and ponto_ate >= de - timedelta(days=30))
        self.titulos = {c.id: c.titulo for c in g.canais.all()}
        # cobertura: [(canal_id, data, hora, zona)]; zona traz os tipos medidos (agente 3.1+) ou '' (antigos).
        self.cobertas = defaultdict(set)          # tipo -> {(data, hora)}
        self.alguma = set()
        for canal_id, data, hora, zona in cobertura:
            self.alguma.add((data, hora))
            medidos = set(zona.split(',')) if zona else None
            for tipo, canais in self.canais.items():
                if canal_id in canais and (medidos is None or tipo in medidos):
                    self.cobertas[tipo].add((data, hora))
            for tipo in medidos or ():
                self.cobertas[tipo].add((data, hora))
        self.eventos = eventos

    def cobertura(self, tipo):
        """% das horas abertas do período em que a câmera da área analisou, e os dias com alguma hora coberta."""
        cobertas = {(d, h) for d, h in self.cobertas.get(tipo, ()) if h in self.abertas}
        total = len(self.dias) * len(self.abertas)
        dias = {d for d, _h in cobertas}
        return (round(len(cobertas) * 100 / total) if total else 0), dias

    def aviso(self, chave):
        tipo = TIPOS_DE_ZONA[chave]
        if not self.g.ultimo_contato and not self.alguma:
            return 'Agente não instalado'
        if tipo not in self.canais:
            return {'entradas': 'Marque a porta', 'mesas': 'Marque a mesa', 'matinal': 'Marque a tela'}[chave]
        if not self.cobertas.get(tipo):
            return 'Sem medição'
        return ''

    def linha(self):
        g = self.g
        entradas = [e for e in self.eventos if e[0] == 'ENTRADA']
        atendimentos = [e for e in self.eventos if e[0] == 'ATENDIMENTO']
        matinais = [e for e in self.eventos if e[0] == 'MATINAL']

        # ── entradas ──
        aviso_entradas = self.aviso('entradas')
        cob_porta, dias_porta = self.cobertura('PORTA')
        por_hora = Counter(e[3] for e in entradas)
        pico = por_hora.most_common(1)[0] if por_hora else None
        ponto_cobre = self.tem_ponto and all(self.ponto_vale(d) for d in dias_porta)
        func = sum(self.equipe.get(d, {}).get('entradas', 0) for d in dias_porta) if ponto_cobre else None
        total_entradas = len(entradas) if not aviso_entradas else None
        clientes = max(0, total_entradas - func) if total_entradas is not None and func is not None else None

        # ── mesas ──
        aviso_mesas = self.aviso('mesas')
        cob_mesa, _dias_mesa = self.cobertura('MESA')
        varios_canais = len({e[1] for e in atendimentos}) > 1
        validos = [e for e in atendimentos if ATENDIMENTO_MINIMO <= e[5] <= ATENDIMENTO_MAXIMO]
        longos = [e for e in atendimentos if e[5] > ATENDIMENTO_MAXIMO]
        duracoes = [e[5] for e in validos]
        por_mesa = defaultdict(list)
        for e in validos:
            nome = f'{self.titulos.get(e[1], "?")} · {e[4]}' if varios_canais else e[4]
            por_mesa[nome].append(e[5])

        # ── matinal, dia a dia ──
        aviso_matinal = self.aviso('matinal')
        janela = _horas_matinal(g)
        tela = self.cobertas.get('TELA', set())
        dias_com_matinal = {e[2] for e in matinais}
        status_matinal = {}
        for d in self.dias:
            coberto = any((d, h) in tela for h in janela)
            if d in dias_com_matinal:
                status_matinal[d] = 'sim'
            elif not coberto:
                status_matinal[d] = 'sem_medicao'
            elif self.ponto_vale(d) and not self.equipe.get(d, {}).get('antes_da_matinal'):
                status_matinal[d] = 'sem_equipe'
            else:
                status_matinal[d] = 'nao'
        medidos = [d for d, s in status_matinal.items() if s in ('sim', 'nao')]
        if not aviso_matinal and not medidos and not dias_com_matinal:
            aviso_matinal = 'Sem medição' if not any(s == 'sem_equipe' for s in status_matinal.values()) else 'Sem equipe no horário'

        cob_geral = round(len({(d, h) for d, h in self.alguma if h in self.abertas}) * 100
                          / (len(self.dias) * len(self.abertas))) if self.abertas else 0
        return {
            'gravador': g, 'dias': len(self.dias), 'cobertura': cob_geral,
            'cobertura_porta': cob_porta, 'cobertura_mesa': cob_mesa,
            'parcial_entradas': not aviso_entradas and cob_porta < COBERTURA_PARCIAL,
            'parcial_mesas': not aviso_mesas and cob_mesa < COBERTURA_PARCIAL,
            'entradas': total_entradas, 'entradas_aviso': aviso_entradas,
            'entradas_equipe': func if total_entradas is not None else None, 'clientes': clientes,
            'pico': f'{pico[0]:02d}h ({pico[1]})' if pico else '',
            'entradas_por_hora': dict(por_hora),
            'mesas_aviso': aviso_mesas,
            'atendimentos': len(validos) if not aviso_mesas else None,
            'longos': len(longos), 'curtos': len(atendimentos) - len(validos) - len(longos),
            'media': formatar_duracao(sum(duracoes) / len(duracoes) if duracoes else None),
            'mediana': formatar_duracao(_mediana(duracoes)), 'media_segundos': _mediana(duracoes),
            'maior': formatar_duracao(max(duracoes, default=None)),
            'duracoes': duracoes,
            'atendimentos_por_hora': dict(Counter(e[3] for e in validos)),
            'por_mesa': [{'nome': nome, 'quantos': len(d), 'media': formatar_duracao(sum(d) / len(d)),
                          'mediana': formatar_duracao(_mediana(d)), 'maior': formatar_duracao(max(d))}
                         for nome, d in sorted(por_mesa.items())],
            'matinal_aviso': aviso_matinal,
            'matinal_dias': sum(1 for s in status_matinal.values() if s == 'sim'),
            'matinal_medidos': len(medidos),
            'matinal_sem_equipe': sum(1 for s in status_matinal.values() if s == 'sem_equipe'),
            'matinal_status': status_matinal,
            'matinais': [{'data': e[2], 'inicio': e[6], 'duracao': formatar_duracao(e[5]), 'pessoas': e[7]} for e in matinais],
            'tem_ponto': self.tem_ponto,
            'por_dia': self.por_dia(entradas, validos, status_matinal),
        }

    def ponto_vale(self, d):
        return self.tem_ponto and d <= self.ponto_ate

    def por_dia(self, entradas, validos, status_matinal):
        porta = self.cobertas.get('PORTA', set())
        mesa = self.cobertas.get('MESA', set())
        n_horas = len(self.abertas) or 1
        dias = []
        for d in self.dias:
            ent = sum(1 for e in entradas if e[2] == d)
            dur = [e[5] for e in validos if e[2] == d]
            cob = round(len({h for (dd, h) in self.alguma if dd == d and h in self.abertas}) * 100 / n_horas)
            func = self.equipe.get(d, {}).get('entradas', 0) if self.ponto_vale(d) else None
            porta_d = any((d, h) in porta for h in self.abertas)
            dias.append({
                'data': d, 'cobertura': cob,
                'entradas': ent if 'PORTA' in self.canais and porta_d else None,
                'clientes': max(0, ent - func) if func is not None and 'PORTA' in self.canais and porta_d else None,
                'entradas_equipe': func if 'PORTA' in self.canais and porta_d else None,
                'atendimentos': len(dur) if 'MESA' in self.canais and any((d, h) in mesa for h in self.abertas) else None,
                'mediana': formatar_duracao(_mediana(dur)), 'matinal': status_matinal.get(d),
                'equipe': self.equipe.get(d, {}).get('pessoas', 0) if self.ponto_vale(d) else None,
                'primeira': self.equipe.get(d, {}).get('primeira') if self.ponto_vale(d) else None,
            })
        return dias


def indicadores_do_periodo(de, ate, loja_id=None, com_anterior=True):
    gravadores = list(Gravador.objects.filter(tem_equipamento=True).prefetch_related('canais'))
    if loja_id:
        gravadores = [g for g in gravadores if g.pk == loja_id]
    ids = [g.id for g in gravadores]
    base = EventoLoja.objects.filter(data__gte=de, data__lte=ate, gravador_id__in=ids)
    cobertura = defaultdict(list)
    for gid, canal_id, data, hora, zona in (base.filter(tipo='COBERTURA')
                                            .values_list('gravador_id', 'canal_id', 'data', 'hora', 'zona').distinct()):
        cobertura[gid].append((canal_id, data, hora, zona))
    eventos = defaultdict(list)
    for gid, tipo, canal_id, data, hora, zona, duracao, inicio, pessoas in (
            base.exclude(tipo='COBERTURA').order_by('inicio')
            .values_list('gravador_id', 'tipo', 'canal_id', 'data', 'hora', 'zona', 'duracao', 'inicio', 'pessoas')):
        eventos[gid].append((tipo, canal_id, data, hora, zona, duracao, inicio, pessoas))

    linhas = [_Loja(g, de, ate, cobertura[g.id], eventos[g.id], ponto.por_dia(g, de, ate), ponto.ultimo_dia(g.setor_id)).linha()
              for g in gravadores]

    com_entradas = [l for l in linhas if l['entradas'] is not None]
    com_clientes = [l for l in com_entradas if l['clientes'] is not None]
    duracoes = [d for l in linhas for d in l['duracoes']]
    medidas_matinal = [l for l in linhas if l['matinal_medidos']]
    por_hora_entradas, por_hora_atend = Counter(), Counter()
    for l in linhas:
        por_hora_entradas.update(l['entradas_por_hora'])
        por_hora_atend.update(l['atendimentos_por_hora'])
    horas = sorted(set(por_hora_entradas) | set(por_hora_atend))
    maior_e, maior_a = max(por_hora_entradas.values(), default=0), max(por_hora_atend.values(), default=0)
    dados = {
        'linhas': sorted(linhas, key=lambda l: (l['entradas'] is None and l['atendimentos'] is None,
                                                -(l['entradas'] or 0), -(l['atendimentos'] or 0), l['gravador'].loja)),
        'total_entradas': sum(l['entradas'] for l in com_entradas) if com_entradas else None,
        'total_clientes': sum(l['clientes'] for l in com_clientes) if com_clientes else None,
        'lojas_com_medicao': sum(1 for l in linhas if l['cobertura']), 'total_lojas': len(linhas),
        'cobertura_media': round(sum(l['cobertura'] for l in linhas if l['cobertura'])
                                 / max(1, sum(1 for l in linhas if l['cobertura']))),
        'matinal_dias': sum(l['matinal_dias'] for l in medidas_matinal),
        'matinal_medidos': sum(l['matinal_medidos'] for l in medidas_matinal),
        'matinal_com': sum(1 for l in medidas_matinal if l['matinal_dias']), 'matinal_medidas': len(medidas_matinal),
        'total_atendimentos': len(duracoes) if duracoes else None,
        'total_longos': sum(l['longos'] for l in linhas),
        'media_geral': formatar_duracao(sum(duracoes) / len(duracoes)) if duracoes else '—',
        'mediana_geral': formatar_duracao(_mediana(duracoes)),
        'por_hora': [{'hora': h, 'entradas': por_hora_entradas.get(h, 0), 'atendimentos': por_hora_atend.get(h, 0),
                      'pe': round(por_hora_entradas.get(h, 0) * 100 / maior_e) if maior_e else 0,
                      'pa': round(por_hora_atend.get(h, 0) * 100 / maior_a) if maior_a else 0} for h in horas],
        'atendimento_maximo_min': ATENDIMENTO_MAXIMO // 60,
    }
    if com_anterior:
        dias = (ate - de).days + 1
        anterior = indicadores_do_periodo(de - timedelta(days=dias), de - timedelta(days=1), loja_id, com_anterior=False)
        dados['anterior'] = {'de': de - timedelta(days=dias), 'ate': de - timedelta(days=1),
                             'entradas': _variacao(dados['total_entradas'], anterior['total_entradas']),
                             'atendimentos': _variacao(dados['total_atendimentos'], anterior['total_atendimentos'])}
    return dados


def _variacao(agora, antes):
    if agora is None or not antes:
        return None
    return round((agora - antes) * 100 / antes)


ROTULO_MATINAL = {'sim': 'Sim', 'nao': 'Não', 'sem_medicao': 'Sem medição', 'sem_equipe': 'Sem equipe'}


def csv_do_periodo(dados):
    """Uma linha por loja e dia, com o que foi medido e o que não foi (vazio = sem medição)."""
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=';')
    escritor.writerow(['Loja', 'Data', 'Cobertura do horário (%)', 'Entradas pela porta', 'Entradas da equipe (ponto)',
                       'Clientes estimados', 'Atendimentos na mesa', 'Mediana do atendimento', 'Matinal',
                       'Equipe com ponto', 'Primeira batida'])
    for l in dados['linhas']:
        for d in l['por_dia']:
            equipe_entradas = d['entradas_equipe']
            escritor.writerow([l['gravador'].loja, d['data'].strftime('%d/%m/%Y'), d['cobertura'],
                               '' if d['entradas'] is None else d['entradas'],
                               '' if equipe_entradas is None else equipe_entradas,
                               '' if d['clientes'] is None else d['clientes'],
                               '' if d['atendimentos'] is None else d['atendimentos'],
                               d['mediana'] if d['atendimentos'] else '',
                               ROTULO_MATINAL.get(d['matinal'], ''),
                               '' if d['equipe'] is None else d['equipe'],
                               d['primeira'].strftime('%H:%M') if d['primeira'] else ''])
    return '﻿' + saida.getvalue()
