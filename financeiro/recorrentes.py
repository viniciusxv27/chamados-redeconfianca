"""A grade das despesas recorrentes: só leitura (o que escreve está em servicos.py).

- A coluna é o mês: com título vinculado, valor e situação vêm do título; sem título,
  do que foi anotado à mão; sem nada, é o valor esperado, marcado como estimativa.
- A variação compara o valor do mês com a média de até 6 meses anteriores que têm
  valor de verdade (estimativa não entra). Sem histórico, compara com o valor esperado.
- Reajuste avisa uma vez só: se o valor ficou igual ao do mês anterior, o patamar novo
  já foi avisado e o mês não acende de novo, mesmo a média ainda estando abaixo.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

from .importacao_titulos import chave_texto
from .models import DespesaMes, Titulo
from .servicos import somar_meses

ZERO = Decimal('0.00')
JANELA_MEDIA = 6
LIMITE_PADRAO = Decimal('10')
DIAS_A_VENCER = 7
IGUAL_AO_ANTERIOR = Decimal('1')       # % abaixo disto conta como "mesmo valor do mês anterior"
SEQUENCIA_MINIMA = 2                   # aumentos seguidos para a linha ganhar o selo

MESES_CURTOS = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
TIPOS_SUGERIDOS = ['Água', 'Aluguel', 'Condomínio', 'Contabilidade', 'Energia', 'Internet', 'IPTU', 'Limpeza',
                   'Plano de saúde', 'Segurança', 'Seguro', 'Software', 'Telefonia']

# situação: (rótulo, ícone)
SITUACOES = {
    'PAGO': ('Paga', 'fa-check'),
    'PARCIAL': ('Paga em parte', 'fa-circle-half-stroke'),
    'VENCIDO': ('Vencida', 'fa-circle-exclamation'),
    'A_VENCER': ('Vence em breve', 'fa-clock'),
    'PENDENTE': ('Lançada, a pagar', 'fa-file-invoice'),
    'PREVISTO': ('Prevista', ''),
    'SEM_COBRANCA': ('Sem cobrança', 'fa-minus'),
    'FORA': ('', ''),
}


def primeiro_dia(dia):
    return dia.replace(day=1)


def texto_br(valor):
    return f'{valor:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def pct_br(valor):
    return '' if valor is None else f'{valor:+.1f}%'.replace('.', ',')


@dataclass
class Celula:
    despesa: object
    competencia: date
    registro: object = None
    titulos: list = field(default_factory=list)
    situacao: str = 'FORA'
    valor: Decimal = None          # o que aparece: real ou, se `estimado`, o valor esperado
    estimado: bool = False
    vencimento: date = None
    saldo: Decimal = ZERO          # o que falta pagar
    parcial: bool = False
    pago_valor: Decimal = None
    pago_em: date = None
    dias_atraso_pago: int = 0
    media: Decimal = None
    base_esperado: bool = False
    variacao: Decimal = None
    alerta: str = ''               # 'ALTA', 'QUEDA' ou vazio
    candidatos: list = field(default_factory=list)
    sugestao: object = None

    @property
    def valor_real(self):
        return None if self.estimado or self.situacao in ('FORA', 'SEM_COBRANCA') else self.valor

    @property
    def rotulo(self):
        return SITUACOES[self.situacao][0]

    @property
    def icone(self):
        return SITUACOES[self.situacao][1]

    @property
    def texto(self):
        return texto_br(self.valor) if self.valor is not None else ''

    @property
    def variacao_abs(self):
        return abs(self.variacao) if self.variacao is not None else None

    @property
    def variacao_txt(self):
        return pct_br(self.variacao)

    @property
    def dica(self):
        partes = [self.despesa.nome, f'{self.competencia:%m/%Y}', self.rotulo]
        if self.estimado and self.valor is not None:
            partes.append('valor esperado, conta ainda não lançada')
        if self.vencimento and self.situacao != 'PAGO':
            partes.append(f'vence {self.vencimento:%d/%m/%Y}')
        if self.pago_em:
            partes.append(f'paga em {self.pago_em:%d/%m/%Y}')
        if self.dias_atraso_pago:
            partes.append(f'{self.dias_atraso_pago} dia(s) depois do vencimento')
        if self.alerta and self.variacao is not None:
            base = 'valor esperado' if self.base_esperado else 'média dos meses anteriores'
            partes.append(f'{self.variacao:+.1f}% sobre a {base} (R$ {texto_br(self.media)})')
        return ' · '.join(partes)


def montar_celula(despesa, competencia, registro, hoje):
    """A situação de um mês. `registro` é o DespesaMes com vinculos e baixas já carregados, ou None."""
    c = Celula(despesa=despesa, competencia=competencia, registro=registro)
    vinculos = list(registro.vinculos.all()) if registro is not None else []
    c.titulos = [v.titulo for v in vinculos if v.titulo.status != 'CANCELADO']
    c.vencimento = (registro.vencimento if registro is not None else None) or despesa.vencimento_em(competencia)

    if c.titulos:
        c.valor = sum((t.valor for t in c.titulos), ZERO)
        baixas = [b for t in c.titulos for b in t.baixas.all() if not b.estornada]
        c.pago_valor = (sum((b.total for b in baixas), ZERO) if baixas
                        else sum((t.valor_baixado for t in c.titulos), ZERO)) or None
        abertos = [t for t in c.titulos if t.status != 'QUITADO']
        c.vencimento = min(t.vencimento for t in (abertos or c.titulos))
        if not abertos:
            c.situacao = 'PAGO'
            c.pago_em = max((b.data for b in baixas), default=None)
        else:
            c.saldo = sum((t.saldo for t in abertos), ZERO)
            c.parcial = any(t.valor_baixado > ZERO for t in c.titulos)
            c.situacao = _em_aberto(c, hoje, 'PARCIAL' if c.parcial else 'PENDENTE')
    elif registro is not None and registro.sem_cobranca:
        c.situacao = 'SEM_COBRANCA'
    elif registro is not None and registro.pago_em is not None:
        c.situacao, c.valor = 'PAGO', registro.valor
        c.pago_em, c.pago_valor = registro.pago_em, registro.valor_pago
    elif registro is not None and registro.valor is not None:
        c.valor, c.saldo = registro.valor, registro.valor
        c.situacao = _em_aberto(c, hoje, 'PENDENTE')
    elif despesa.ativa and competencia >= primeiro_dia(despesa.inicio):
        c.valor, c.saldo, c.estimado = despesa.valor_esperado, despesa.valor_esperado, True
        c.situacao = _em_aberto(c, hoje, 'PREVISTO')
    else:
        c.vencimento = None

    if c.situacao == 'PAGO' and c.pago_em and c.vencimento and c.pago_em > c.vencimento:
        c.dias_atraso_pago = (c.pago_em - c.vencimento).days
    return c


def _em_aberto(c, hoje, senao):
    if c.vencimento < hoje:
        return 'VENCIDO'
    if c.vencimento <= hoje + timedelta(days=DIAS_A_VENCER):
        return 'A_VENCER'
    return senao


def calcular_variacoes(celulas, limite):
    """Preenche média, variação e alerta. `celulas` em ordem de mês; devolve os valores reais na ordem."""
    historico = []
    for c in celulas:
        valor = c.valor_real
        if valor is None:
            continue
        janela = historico[-JANELA_MEDIA:]
        c.media = sum(janela, ZERO) / len(janela) if janela else c.despesa.valor_esperado
        c.base_esperado = not janela
        if c.media:
            c.variacao = ((valor - c.media) / c.media * 100).quantize(Decimal('0.1'))
            mudou = not janela or abs(valor - janela[-1]) / janela[-1] * 100 >= IGUAL_AO_ANTERIOR
            if abs(c.variacao) >= limite and mudou:
                c.alerta = 'ALTA' if c.variacao > 0 else 'QUEDA'
        historico.append(valor)
    return historico


def aumentos_seguidos(valores):
    """Quantos aumentos em sequência terminam no último valor."""
    n = 0
    for anterior, atual in zip(reversed(valores[:-1]), reversed(valores[1:])):
        if anterior and (atual - anterior) / anterior * 100 >= IGUAL_AO_ANTERIOR:
            n += 1
        else:
            break
    return n


def titulo_casa(despesa, titulo):
    """O título parece ser desta despesa? Mesma loja, mais fornecedor, plano ou a palavra combinada."""
    if despesa.loja_id != titulo.loja_id:
        return False
    if despesa.termo_titulo and chave_texto(despesa.termo_titulo) not in chave_texto(titulo.descricao):
        return False
    if despesa.plano_id and (titulo.plano_id is None
                             or chave_texto(despesa.plano.nome) != chave_texto(titulo.plano.nome)):
        # O plano importado repete o mesmo nome em grupos diferentes: vale o nome, não o código.
        return False
    documento = despesa.fornecedor_documento
    if documento:
        return (titulo.pessoa_documento[:8] == documento[:8] if len(documento) == 14
                else titulo.pessoa_documento == documento)
    if despesa.fornecedor:
        a, b = chave_texto(despesa.fornecedor), chave_texto(titulo.pessoa_nome)
        return bool(b) and (a in b or b in a)
    return bool(despesa.plano_id or despesa.termo_titulo)


def titulos_livres(de, ate, user):
    """Títulos a pagar sem despesa, por mês de vencimento, dentro das lojas da pessoa."""
    from .permissoes import filtrar_por_loja
    qs = (filtrar_por_loja(Titulo.objects.filter(tipo='PAGAR'), user)
          .filter(vencimento__gte=de, vencimento__lte=ate, vinculo_recorrente__isnull=True)
          .exclude(status='CANCELADO').select_related('plano', 'loja').order_by('vencimento', 'id'))
    por_mes = defaultdict(list)
    for t in qs:
        por_mes[primeiro_dia(t.vencimento)].append(t)
    return por_mes


def candidatos_do_mes(despesa, livres):
    """Os títulos do mês que combinam, do valor mais próximo do esperado ao mais distante."""
    achados = [t for t in livres if titulo_casa(despesa, t)]
    achados.sort(key=lambda t: (abs(t.valor - despesa.valor_esperado), t.vencimento, t.id))
    return achados


def _sugestao(despesa, candidatos):
    """Só sugere o que não deixa dúvida: um candidato só, ou um só com o valor exato."""
    if len(candidatos) == 1:
        return candidatos[0]
    exatos = [t for t in candidatos if t.valor == despesa.valor_esperado]
    return exatos[0] if len(exatos) == 1 else None


def carregar_registros(despesas, de=None):
    qs = DespesaMes.objects.filter(despesa__in=despesas).prefetch_related(
        'vinculos__titulo__baixas')
    if de is not None:
        qs = qs.filter(competencia__gte=de)
    por_despesa = defaultdict(dict)
    for r in qs:
        por_despesa[r.despesa_id][r.competencia] = r
    return por_despesa


def _meses(de, ate):
    atual = de
    while atual <= ate:
        yield atual
        atual = somar_meses(atual, 1)


@dataclass
class Grade:
    ano: int
    mes: int
    linhas: list = field(default_factory=list)
    grupos: list = field(default_factory=list)
    colunas: list = field(default_factory=list)
    kpi: dict = field(default_factory=dict)
    atencao: list = field(default_factory=list)
    variacoes: list = field(default_factory=list)
    sugestoes: list = field(default_factory=list)


def montar_grade(despesas, ano, mes, hoje, limite=LIMITE_PADRAO, livres=None, so=''):
    """`despesas` já vem filtrada. `livres` (títulos sem despesa) só vem para quem pode ver Contas a Pagar."""
    despesas = list(despesas)
    grade = Grade(ano=ano, mes=mes)
    jan, dez = date(ano, 1, 1), date(ano, 12, 1)
    selecionado = date(ano, mes, 1)
    anterior = somar_meses(selecionado, -1)
    registros = carregar_registros(despesas)

    # Vai desde o mês mais antigo: conta vencida de outro ano continua vencida.
    inicio = somar_meses(jan, -JANELA_MEDIA)
    for d in despesas:
        inicio = min(inicio, primeiro_dia(d.inicio), *(registros[d.id].keys() or [inicio]))
    fim = max(dez, primeiro_dia(hoje + timedelta(days=DIAS_A_VENCER)))
    meses_todos = list(_meses(inicio, fim))
    meses_ano = list(_meses(jan, dez))

    todas = []
    for d in despesas:
        celulas = {m: montar_celula(d, m, registros[d.id].get(m), hoje) for m in meses_todos}
        valores = calcular_variacoes(list(celulas.values()), limite)
        do_ano = [celulas[m] for m in meses_ano]
        reais = [c.valor_real for c in do_ano if c.valor_real is not None]
        linha = {
            'despesa': d, 'celulas': do_ano, 'todas': celulas, 'atual': celulas[selecionado],
            'anterior': celulas.get(anterior),
            'total_ano': sum((c.valor for c in do_ano if c.valor is not None), ZERO),
            'var_ano': (((reais[-1] - reais[0]) / reais[0] * 100).quantize(Decimal('0.1'))
                        if len(reais) >= 2 and reais[0] else None),
            'sequencia': aumentos_seguidos(valores),
        }
        linha['tem_sequencia'] = linha['sequencia'] >= SEQUENCIA_MINIMA
        linha['var_ano_txt'] = pct_br(linha['var_ano'])
        todas.append(linha)

    # Sugestão de título: só nos meses sem nada resolvido da grade que está na tela.
    if livres is not None:
        pedidos, exatos = Counter(), Counter()
        for linha in todas:
            for c in linha['celulas']:
                if c.titulos or c.situacao in ('SEM_COBRANCA', 'FORA', 'PAGO'):
                    continue
                c.candidatos = candidatos_do_mes(linha['despesa'], livres.get(c.competencia, []))
                c.sugestao = _sugestao(linha['despesa'], c.candidatos)
                if c.sugestao is not None:
                    pedidos[c.sugestao.id] += 1
                    exatos[c.sugestao.id] += c.sugestao.valor == linha['despesa'].valor_esperado
        for linha in todas:
            for c in linha['celulas']:
                # Duas despesas querendo o mesmo título: leva a do valor exato; sem isso, a pessoa decide.
                if c.sugestao is not None and pedidos[c.sugestao.id] > 1 and not (
                        exatos[c.sugestao.id] == 1 and c.sugestao.valor == linha['despesa'].valor_esperado):
                    c.sugestao = None
                if c.sugestao is not None:
                    grade.sugestoes.append(c)

    # KPIs e listas valem para o que está no filtro, antes do "só com...".
    vencidas, a_vencer = [], []
    for linha in todas:
        for c in linha['todas'].values():
            if c.situacao == 'VENCIDO':
                vencidas.append(c)
            elif c.situacao == 'A_VENCER':
                a_vencer.append(c)
    pagas = [ln['atual'] for ln in todas if ln['atual'].situacao == 'PAGO']
    total_mes = sum((ln['atual'].valor for ln in todas if ln['atual'].valor is not None), ZERO)
    estimado_mes = sum((ln['atual'].valor for ln in todas if ln['atual'].estimado), ZERO)
    total_anterior = sum((ln['anterior'].valor for ln in todas if ln['anterior'] and ln['anterior'].valor is not None), ZERO)
    grade.variacoes = sorted((ln['atual'] for ln in todas if ln['atual'].alerta),
                             key=lambda c: -abs(c.variacao))
    grade.atencao = sorted(vencidas + a_vencer, key=lambda c: (c.vencimento, c.despesa.nome))
    grade.kpi = {
        'vencidas': {'n': len(vencidas), 'valor': sum((c.saldo for c in vencidas), ZERO)},
        'a_vencer': {'n': len(a_vencer), 'valor': sum((c.saldo for c in a_vencer), ZERO)},
        'pagas': {'n': len(pagas), 'valor': sum((c.pago_valor or c.valor or ZERO for c in pagas), ZERO),
                  'atrasadas': sum(1 for c in pagas if c.dias_atraso_pago)},
        'total_mes': total_mes, 'estimado_mes': estimado_mes, 'total_anterior': total_anterior,
        'delta': total_mes - total_anterior,
        'delta_pct': ((total_mes - total_anterior) / total_anterior * 100).quantize(Decimal('0.1'))
        if total_anterior else None,
        'alertas': len(grade.variacoes),
    }
    grade.kpi['delta_pct_txt'] = pct_br(grade.kpi['delta_pct'])

    if so == 'alerta':
        todas = [ln for ln in todas if ln['atual'].alerta]
    elif so == 'atraso':
        todas = [ln for ln in todas if any(c.situacao == 'VENCIDO' for c in ln['todas'].values())]
    grade.linhas = todas

    por_tipo = defaultdict(list)
    for linha in todas:
        por_tipo[linha['despesa'].tipo].append(linha)
    grade.grupos = [{'tipo': tipo, 'linhas': linhas} for tipo, linhas in sorted(por_tipo.items(), key=lambda x: chave_texto(x[0]))]

    for i, m in enumerate(meses_ano):
        total = sum((ln['celulas'][i].valor for ln in todas if ln['celulas'][i].valor is not None), ZERO)
        grade.colunas.append({'n': i + 1, 'rotulo': MESES_CURTOS[i], 'ativo': m == selecionado,
                              'corrente': m == primeiro_dia(hoje), 'total': total, 'total_txt': texto_br(total)})
    return grade


# ── Excel ───────────────────────────────────────────────────────────────────
CORES_EXCEL = {'PAGO': 'FFDCFCE7', 'PARCIAL': 'FFFEF3C7', 'VENCIDO': 'FFFEE2E2', 'A_VENCER': 'FFFFEDD5',
               'PENDENTE': 'FFDBEAFE', 'PREVISTO': 'FFF3F4F6', 'SEM_COBRANCA': 'FFE5E7EB'}


def _texto_seguro(texto):
    """Nome digitado que começa com = + - @ viraria fórmula no Excel."""
    texto = str(texto or '')
    return "'" + texto if texto[:1] in ('=', '+', '-', '@') else texto


def planilha(grade):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    verde = PatternFill('solid', fgColor='FF065F46')
    branco = Font(bold=True, color='FFFFFFFF')
    dinheiro = 'R$ #,##0.00'
    livro = Workbook()

    folha = livro.active
    folha.title = f'Grade {grade.ano}'
    cabecalho = ['Despesa', 'Tipo', 'Fornecedor', 'Loja', 'Vencimento (dia)', 'Valor esperado'] + \
        [f'{c["rotulo"]}/{grade.ano}' for c in grade.colunas] + ['Total no ano']
    folha.append(cabecalho)
    primeira_mes = 7
    for linha in grade.linhas:
        d = linha['despesa']
        folha.append([_texto_seguro(d.nome), _texto_seguro(d.tipo), _texto_seguro(d.fornecedor),
                      d.loja.name if d.loja else 'Escritório', d.dia_vencimento, d.valor_esperado]
                     + [c.valor for c in linha['celulas']] + [linha['total_ano']])
        n = folha.max_row
        folha.cell(n, 6).number_format = dinheiro
        folha.cell(n, primeira_mes + 12).number_format = dinheiro
        for i, c in enumerate(linha['celulas']):
            celula = folha.cell(n, primeira_mes + i)
            celula.number_format = dinheiro
            if c.situacao in CORES_EXCEL:
                celula.fill = PatternFill('solid', fgColor=CORES_EXCEL[c.situacao])
            if c.estimado:
                celula.font = Font(italic=True, color='FF6B7280')
    if grade.linhas:
        n = folha.max_row + 1
        folha.cell(n, 1, 'Total').font = Font(bold=True)
        for col in range(primeira_mes, primeira_mes + 13):
            letra = get_column_letter(col)
            celula = folha.cell(n, col, f'=SUM({letra}2:{letra}{n - 1})')
            celula.font, celula.number_format = Font(bold=True), dinheiro

    n = folha.max_row + 2
    folha.cell(n, 1, 'Cores').font = Font(bold=True)
    for i, (situacao, cor) in enumerate(CORES_EXCEL.items(), start=1):
        folha.cell(n + i, 1, SITUACOES[situacao][0]).fill = PatternFill('solid', fgColor=cor)
    folha.cell(n + len(CORES_EXCEL) + 1, 1, 'Em itálico: valor esperado (a conta ainda não foi lançada).').font = Font(italic=True)

    meses = livro.create_sheet('Mês a mês')
    meses.append(['Despesa', 'Tipo', 'Loja', 'Mês', 'Vencimento', 'Valor', 'Estimado', 'Situação', 'Pago em', 'Valor pago',
                  'Dias de atraso no pagamento', 'Variação sobre a média (%)', 'Alerta', 'Títulos vinculados'])
    for linha in grade.linhas:
        d = linha['despesa']
        for c in linha['celulas']:
            if c.situacao == 'FORA':
                continue
            meses.append([_texto_seguro(d.nome), _texto_seguro(d.tipo), d.loja.name if d.loja else 'Escritório',
                          f'{c.competencia:%m/%Y}', c.vencimento, c.valor, 'Sim' if c.estimado else 'Não', c.rotulo,
                          c.pago_em, c.pago_valor, c.dias_atraso_pago or None, c.variacao,
                          {'ALTA': 'Acima do limite', 'QUEDA': 'Abaixo do limite'}.get(c.alerta, ''),
                          ', '.join(str(t.pk) for t in c.titulos)])
            n = meses.max_row
            meses.cell(n, 5).number_format = meses.cell(n, 9).number_format = 'DD/MM/YYYY'
            meses.cell(n, 6).number_format = meses.cell(n, 10).number_format = dinheiro

    for planilha_ in (folha, meses):
        for celula in planilha_[1]:
            celula.fill, celula.font = verde, branco
            celula.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        planilha_.freeze_panes = 'B2'
        for col in range(1, planilha_.max_column + 1):
            planilha_.column_dimensions[get_column_letter(col)].width = 28 if col == 1 else 15

    saida = BytesIO()
    livro.save(saida)
    return saida.getvalue()
