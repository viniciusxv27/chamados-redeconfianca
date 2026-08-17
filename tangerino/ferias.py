"""Situação de férias por colaborador.

O Tangerino guarda férias como lançamentos de afastamento (motivo FÉRIAS) com
início e fim. Ele **não** devolve período aquisitivo nem saldo — isso é
calculado aqui, a partir da data de admissão, segundo a CLT:

* **Período aquisitivo**: cada 12 meses trabalhados dão direito a 30 dias.
* **Período concessivo**: os 12 meses seguintes ao fim do aquisitivo. As férias
  precisam ser gozadas dentro dele; passou disso, estão *vencidas* (e o
  empregador paga em dobro — art. 137 da CLT).

Duas simplificações conscientes, porque a API não fornece os dados:

1. O direito é sempre considerado 30 dias. Faltas injustificadas podem reduzir
   para 24/18/12 dias (art. 130), e isso o Tangerino não informa por aqui.
2. Os lançamentos não dizem a que período aquisitivo pertencem, então são
   alocados em ordem cronológica, do período mais antigo para o mais novo.

Ou seja: serve para **alertar e acompanhar**, não para substituir o cálculo do
departamento pessoal.
"""
import logging
from datetime import date, timedelta

from django.core.cache import caches
from django.utils import timezone

from .client import TangerinoError, de_millis, listar_ferias, listar_funcionarios

logger = logging.getLogger(__name__)

DIAS_POR_PERIODO = 30
DIAS_ALERTA_VENCENDO = 90      # concessivo terminando em até 3 meses
STATUS_VALIDOS = ('APROVADO', 'PENDENTE')


def _mais_um_ano(d, anos=1):
    """Mesma data no ano seguinte, tratando 29/02."""
    try:
        return d.replace(year=d.year + anos)
    except ValueError:
        return d.replace(year=d.year + anos, day=28)


def periodos_aquisitivos(admissao, hoje=None):
    """Períodos aquisitivos já iniciados, do mais antigo para o mais novo."""
    hoje = hoje or timezone.localdate()
    if not admissao or admissao > hoje:
        return []

    periodos, inicio, n = [], admissao, 1
    while inicio <= hoje and n <= 60:      # trava de segurança
        fim = _mais_um_ano(inicio) - timedelta(days=1)
        periodos.append({
            'numero': n,
            'inicio': inicio,
            'fim': fim,
            'completo': hoje > fim,
            'concessivo_inicio': fim + timedelta(days=1),
            'concessivo_fim': _mais_um_ano(fim + timedelta(days=1)) - timedelta(days=1),
            'direito': DIAS_POR_PERIODO,
            'gozados': 0,
            'lancamentos': [],
        })
        inicio = _mais_um_ano(inicio)
        n += 1
    return periodos


def _dias(lancamento):
    ini, fim = lancamento['inicio'], lancamento['fim']
    return max(1, (fim - ini).days + 1)


def normalizar_lancamentos(brutos):
    """Lançamentos crus da API -> dicionários com datas de verdade."""
    saida = []
    for item in brutos or []:
        ini, fim = de_millis(item.get('startDate')), de_millis(item.get('endDate'))
        if not ini:
            continue
        emp = item.get('employeeDTO') or {}
        saida.append({
            'id': item.get('id'),
            'employee_id': emp.get('id'),
            'nome': emp.get('name') or '',
            'inicio': ini.date(),
            'fim': (fim or ini).date(),
            'status': (item.get('status') or '').upper(),
            'observacao': item.get('observation') or '',
            'origem': item.get('origem') or '',
        })
    return sorted(saida, key=lambda x: x['inicio'])


def inicio_da_cobertura(todos_lancamentos=None):
    """A partir de quando o histórico do Tangerino é confiável.

    A empresa migrou as férias para o Sólides em determinado momento; férias
    gozadas antes disso simplesmente não existem na API. Sem esta trava, quem
    foi admitido em 2017 apareceria com 230 dias "vencidos" — um número falso,
    porque o gozo daqueles anos está em papel/outro sistema.

    A régua é o lançamento mais antigo que existe na base.
    """
    if todos_lancamentos is None:
        todos_lancamentos = normalizar_lancamentos(listar_ferias())
    if not todos_lancamentos:
        return None
    return min(l['inicio'] for l in todos_lancamentos)


def situacao(admissao, lancamentos, hoje=None, cobertura=None):
    """Monta a situação de férias de uma pessoa."""
    hoje = hoje or timezone.localdate()
    periodos = periodos_aquisitivos(admissao, hoje)
    validos = [l for l in lancamentos if l['status'] in STATUS_VALIDOS]

    # Marca ANTES de alocar os períodos anteriores ao histórico disponível.
    # Precisa ser antes: se um período "sem histórico" participasse da
    # alocação, ele engoliria as férias realmente gozadas e o saldo visível
    # ficaria alto demais — foi exatamente o que aconteceu com quem tem
    # admissão antiga e tirou férias este ano.
    for p in periodos:
        p['sem_dados'] = bool(cobertura and p['concessivo_fim'] < cobertura)

    # Aloca cada lançamento no período aquisitivo mais antigo com saldo.
    for lanc in validos:
        for p in periodos:
            if p['sem_dados'] or p['gozados'] >= p['direito']:
                continue
            if lanc['inicio'] < p['concessivo_inicio']:
                continue          # gozo antecipado: pertence a um período posterior
            p['gozados'] += _dias(lanc)
            p['lancamentos'].append(lanc)
            break

    vencidos, vencendo, sem_dados = [], [], []
    for p in periodos:
        p['saldo'] = max(0, p['direito'] - p['gozados'])
        p['dias_para_vencer'] = (p['concessivo_fim'] - hoje).days
        if p['sem_dados']:
            p['vencido'] = p['vencendo'] = False
            sem_dados.append(p)
            continue
        p['vencido'] = p['completo'] and p['saldo'] > 0 and hoje > p['concessivo_fim']
        p['vencendo'] = (p['completo'] and p['saldo'] > 0 and not p['vencido']
                         and 0 <= p['dias_para_vencer'] <= DIAS_ALERTA_VENCENDO)
        if p['vencido']:
            vencidos.append(p)
        if p['vencendo']:
            vencendo.append(p)

    em_gozo = next((l for l in validos if l['inicio'] <= hoje <= l['fim']), None)
    programadas = [l for l in validos if l['inicio'] > hoje]
    passadas = [l for l in validos if l['fim'] < hoje]

    return {
        'admissao': admissao,
        'periodos': periodos,
        'vencidos': vencidos,
        'vencendo': vencendo,
        'sem_dados': sem_dados,
        'cobertura': cobertura,
        'saldo_total': sum(p['saldo'] for p in periodos
                           if p['completo'] and not p['sem_dados']),
        'dias_vencidos': sum(p['saldo'] for p in vencidos),
        'em_gozo': em_gozo,
        'dias_em_gozo': _dias(em_gozo) if em_gozo else 0,
        'volta_em': em_gozo['fim'] + timedelta(days=1) if em_gozo else None,
        'dias_restantes_gozo': (em_gozo['fim'] - hoje).days + 1 if em_gozo else 0,
        'programadas': programadas,
        'proxima': programadas[0] if programadas else None,
        'passadas': passadas,
        'total_lancamentos': len(validos),
        'precisa_alertar': bool(vencidos or vencendo or em_gozo or programadas),
    }


# ─── Acesso conveniente ──────────────────────────────────────────────────────

def _mapa_admissoes():
    return {f['id']: (de_millis(f.get('admissionDate')).date()
                      if f.get('admissionDate') else None)
            for f in listar_funcionarios()}


def situacao_do_usuario(usuario, hoje=None):
    """Situação de férias de um usuário do portal. Nunca levanta exceção."""
    eid = getattr(usuario, 'tangerino_employee_id', None)
    if not eid:
        return {'disponivel': False, 'motivo': 'sem_vinculo'}
    try:
        todos = normalizar_lancamentos(listar_ferias())
        lancamentos = [l for l in todos if l['employee_id'] == eid]
        admissao = _mapa_admissoes().get(eid) or getattr(usuario, 'admission_date', None)
        dados = situacao(admissao, lancamentos, hoje, cobertura=inicio_da_cobertura(todos))
        dados['disponivel'] = True
        return dados
    except TangerinoError as exc:
        logger.warning('Férias indisponíveis para %s: %s', usuario, exc)
        return {'disponivel': False, 'motivo': 'indisponivel'}


def esta_de_ferias(usuario, hoje=None):
    """Lançamento de férias em curso hoje, ou None.

    Chamado pelo middleware em TODA requisição, então a resposta fica guardada
    no cache ``local`` (memória do processo). O cache 'default' é um Redis
    remoto de ~200ms: consultá-lo a cada página custaria mais que o benefício.
    Assim, no pior caso, é uma verificação a cada 3 minutos por processo.

    Conservador de propósito: qualquer falha de leitura devolve None (libera o
    portal). Ninguém fica trancado do lado de fora por instabilidade da API.
    """
    eid = getattr(usuario, 'tangerino_employee_id', None)
    if not eid:
        return None
    hoje = hoje or timezone.localdate()

    chave = f'tangerino:de-ferias:{eid}:{hoje}'
    guardado = caches['local'].get(chave)
    if guardado is not None:
        return guardado or None          # '' = já checamos e não está de férias

    achado = None
    try:
        for lanc in normalizar_lancamentos(listar_ferias()):
            if (lanc['employee_id'] == eid and lanc['status'] == 'APROVADO'
                    and lanc['inicio'] <= hoje <= lanc['fim']):
                achado = lanc
                break
    except TangerinoError as exc:
        logger.warning('Não deu para checar férias de %s: %s', usuario, exc)
        return None                      # falha não é cacheada: tenta de novo depois

    caches['local'].set(chave, achado or '', 180)
    return achado


def panorama_da_empresa(hoje=None):
    """Visão consolidada para o SuperAdmin."""
    hoje = hoje or timezone.localdate()
    lancamentos = normalizar_lancamentos(listar_ferias())
    cobertura = inicio_da_cobertura(lancamentos)
    admissoes = _mapa_admissoes()
    funcionarios = {f['id']: f for f in listar_funcionarios()}

    por_pessoa = {}
    for lanc in lancamentos:
        por_pessoa.setdefault(lanc['employee_id'], []).append(lanc)

    linhas = []
    for eid, func in funcionarios.items():
        dados = situacao(admissoes.get(eid), por_pessoa.get(eid, []), hoje, cobertura=cobertura)
        linhas.append({
            'employee_id': eid,
            'nome': func.get('name') or '',
            'admissao': admissoes.get(eid),
            'situacao': dados,
        })

    em_gozo = [l for l in linhas if l['situacao']['em_gozo']]
    vencidas = [l for l in linhas if l['situacao']['vencidos']]
    vencendo = [l for l in linhas if l['situacao']['vencendo']]
    programadas = [l for l in linhas if l['situacao']['programadas']]
    return {
        'hoje': hoje,
        'cobertura': cobertura,
        'linhas': sorted(linhas, key=lambda l: l['nome']),
        'em_gozo': em_gozo,
        'vencidas': sorted(vencidas, key=lambda l: -l['situacao']['dias_vencidos']),
        'vencendo': vencendo,
        'programadas': programadas,
        'total_pessoas': len(linhas),
    }
