"""Importa contas a pagar do "Relatório de Contas a pagar e já pagas" (CSV com ponto e vírgula).

O arquivo traz um cabeçalho de relatório antes da linha de colunas; a leitura procura essa linha.

- Reimportar não duplica: a chave é Nº do movimento + vencimento (sem movimento, fornecedor +
  vencimento + valor + descrição).
- Linha repetida igual na planilha entra uma vez e aparece no relatório.
- Conta já paga fica de fora e aparece no relatório: a baixa mexe no saldo do banco, e isso
  não se faz por planilha.
- Quem tem acesso só a algumas lojas importa só o que é dessas lojas.
"""
import csv
import io
import re
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction

from . import auditoria
from .models import ContaBancaria, PlanoConta, Titulo

COLUNAS = ['Situação', 'Data de lançamento', 'Data de vencimento', 'Data de pagamento', 'Forma pagamento', 'Filial',
           'CNPJ Raiz', 'Plano de Contas', 'Despesa', 'Obs. despesa', 'Centro de Custo', 'Status Pagamento Banco',
           'Fornecedor', 'CNPJ/CPF Fornecedor', 'Crédito fornecedor', 'Vendedor', 'Nº do movimento',
           'Nº do documento / Linha de pagamento', 'Nota fiscal', 'Observações', 'Motivo reversão', 'Data reversão',
           'Usuário baixa/reversão', 'Usuário inserção', 'Débito', 'Valor total', 'Juros (%)', 'Juros (R$)', 'Descontos',
           'Valor líquido', 'Valor Pago']
OBRIGATORIAS = ('data de vencimento', 'fornecedor', 'valor total')
SEM_GRUPO = 'SEM GRUPO'
NAMESPACE_MOVIMENTO = uuid.UUID('6f1c3f0e-3b0a-4f7e-9a52-2d7a1c9e4b11')
PARCELA = re.compile(r'PARC\w*\.?\s*(\d{1,3})\s*(?:/|DE)\s*(\d{1,3})|\((\d{1,3})\s*DE\s*(\d{1,3})\)', re.I)
CARTAO = re.compile(r'\D-\s*\d{4}\s*$')
CONTA_DEBITO = re.compile(r'CC:\s*(\d+)')


class ArquivoInvalido(Exception):
    pass


@dataclass
class Analise:
    novos: list = field(default_factory=list)
    ja_existem: int = 0
    repetidas: list = field(default_factory=list)       # (linha, fornecedor, vencimento, valor)
    pagas: list = field(default_factory=list)
    rejeitadas: list = field(default_factory=list)      # (linha, motivo)
    sem_loja: Counter = field(default_factory=Counter)  # filial que não é loja do portal → linhas
    planos_novos: set = field(default_factory=set)

    @property
    def filiais_sem_loja(self):
        return sorted(self.sem_loja.items())     # no template, Counter.items viraria counter['items'] = 0

    @property
    def total(self):
        return sum((n['valor'] for n in self.novos), Decimal('0'))


def chave_texto(texto):
    texto = unicodedata.normalize('NFKD', str(texto or ''))
    return ' '.join(''.join(c for c in texto if not unicodedata.combining(c)).lower().split())


def decodificar(conteudo):
    """O relatório sai em Latin-1; quem salva pelo Excel pode mandar UTF-8."""
    try:
        return conteudo.decode('utf-8-sig')
    except UnicodeDecodeError:
        return conteudo.decode('latin-1')


def _data(texto):
    partes = (texto or '').strip().split('/')
    if len(partes) != 3:
        return None
    try:
        return date(int(partes[2]), int(partes[1]), int(partes[0]))
    except ValueError:
        return None


def _dinheiro(texto):
    bruto = (texto or '').replace('R$', '').strip()
    if not bruto:
        return None
    if ',' in bruto:
        bruto = bruto.replace('.', '').replace(',', '.')
    try:
        return Decimal(bruto).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return None


def _forma(texto):
    chave = chave_texto(texto)
    if chave == 'boleto':
        return 'BOLETO'
    if chave == 'pix':
        return 'PIX'
    if chave == 'dinheiro':
        return 'DINHEIRO'
    if chave == 'cheque':
        return 'CHEQUE'
    if 'debito' in chave:
        return 'DEBITO'
    if 'transfer' in chave or 'depos' in chave or chave in ('ted', 'doc'):
        return 'TED'
    if CARTAO.search(texto or ''):        # "Itau - 9503": banco e os 4 finais do cartão
        return 'CARTAO'
    return 'OUTRO'


def _parcela(*textos):
    for texto in textos:
        m = PARCELA.search(texto or '')
        if m:
            atual, total = [int(x) for x in m.groups() if x]
            if 1 <= atual <= total <= 120 and total >= 2:
                return atual, total
    return 1, 1


def _linhas(texto):
    """As linhas do relatório como dicionários (colunas sem acento e minúsculas), com o nº da linha no arquivo."""
    linhas = texto.splitlines()
    inicio = next((i for i, l in enumerate(linhas) if all(c in chave_texto(l) for c in OBRIGATORIAS)), None)
    if inicio is None:
        raise ArquivoInvalido('Não achei a linha de colunas. Use o modelo: o CSV do relatório de contas a pagar, '
                              'separado por ponto e vírgula.')
    leitor = csv.reader(io.StringIO('\n'.join(linhas[inicio:])), delimiter=';')
    cabecalho = [chave_texto(c) for c in next(leitor)]
    for n, valores in enumerate(leitor, start=inicio + 2):
        if any(v.strip() for v in valores):
            yield n, {c: v.strip() for c, v in zip(cabecalho, valores)}


def _lojas_por_nome():
    from auditoria_sap.permissions import normalizar_loja
    from contagem_caixa.permissions import lojas
    return {normalizar_loja(l.name): l for l in lojas()}, normalizar_loja


def _observacao(d, descricao, forma, loja):
    partes = []
    if forma in ('CARTAO', 'TED', 'OUTRO') and d.get('forma pagamento'):
        partes.append(f"Forma na planilha: {d['forma pagamento']}")
    if not loja and d.get('filial'):
        partes.append(f"Filial: {d['filial']}")
    for coluna, rotulo in (('obs. despesa', 'Obs. despesa'), ('observacoes', 'Observações')):
        if d.get(coluna) and d[coluna] != descricao:
            partes.append(f'{rotulo}: {d[coluna]}')
    for coluna, rotulo in (('centro de custo', 'Centro de custo'), ('nota fiscal', 'Nota fiscal'),
                           ('debito', 'Débito'), ('usuario insercao', 'Lançado por'),
                           ('motivo reversao', 'Motivo da reversão'), ('data reversao', 'Data da reversão')):
        if d.get(coluna) and d[coluna] != '?':
            partes.append(f'{rotulo}: {d[coluna]}')
    return '\n'.join(partes)


def analisar(texto, lojas_permitidas=None):
    """Lê o arquivo sem gravar nada. ``lojas_permitidas``: None = todas; senão os ids que a pessoa vê."""
    a = Analise()
    lojas, normalizar = _lojas_por_nome()
    contas = {c.conta: c for c in ContaBancaria.objects.all() if c.conta}
    ja_movimento = set(Titulo.objects.filter(tipo='PAGAR').exclude(numero_movimento='')
                       .values_list('numero_movimento', 'vencimento'))
    ja_sem_movimento = set(Titulo.objects.filter(tipo='PAGAR', origem='IMPORTACAO', numero_movimento='')
                           .values_list('pessoa_documento', 'vencimento', 'valor', 'descricao'))
    planos = {(chave_texto(p.pai.nome), chave_texto(p.nome)) for p in
              PlanoConta.objects.filter(tipo='DESPESA', pai__isnull=False).select_related('pai')}
    vistos = set()

    for n, d in _linhas(texto):
        vencimento = _data(d.get('data de vencimento'))
        valor = _dinheiro(d.get('valor liquido')) or _dinheiro(d.get('valor total'))
        fornecedor = d.get('fornecedor', '')
        if vencimento is None:
            a.rejeitadas.append((n, f'vencimento inválido: "{d.get("data de vencimento", "")}"'))
            continue
        if valor is None or valor <= 0:
            a.rejeitadas.append((n, f'valor inválido: "{d.get("valor total", "")}"'))
            continue
        if not fornecedor:
            a.rejeitadas.append((n, 'sem fornecedor'))
            continue
        pago = _dinheiro(d.get('valor pago')) or Decimal('0')
        situacao = chave_texto(d.get('situacao'))
        if d.get('data de pagamento') or pago > 0 or (situacao and situacao != 'a pagar'):
            a.pagas.append((n, fornecedor, vencimento, valor))
            continue
        loja = lojas.get(normalizar(d.get('filial')))
        if lojas_permitidas is not None and (loja is None or loja.id not in lojas_permitidas):
            a.rejeitadas.append((n, f'filial fora das suas lojas: "{d.get("filial", "")}"'))
            continue
        if loja is None and d.get('filial'):
            a.sem_loja[d['filial']] += 1

        documento = re.sub(r'\D', '', d.get('cnpj/cpf fornecedor', ''))[:14]
        movimento = d.get('no do movimento', '')[:30]
        descricao = (d.get('observacoes') or d.get('obs. despesa') or d.get('despesa') or fornecedor)[:200]
        chave = (movimento, vencimento) if movimento else (documento, vencimento, valor, descricao)
        if chave in vistos:
            a.repetidas.append((n, fornecedor, vencimento, valor))
            continue
        vistos.add(chave)
        if chave in (ja_movimento if movimento else ja_sem_movimento):
            a.ja_existem += 1
            continue

        forma = _forma(d.get('forma pagamento'))
        grupo_plano = d.get('plano de contas') or SEM_GRUPO
        despesa = d.get('despesa', '')
        if despesa and (chave_texto(grupo_plano), chave_texto(despesa)) not in planos:
            a.planos_novos.add((grupo_plano, despesa))
        parcela, parcelas = _parcela(d.get('observacoes'), d.get('obs. despesa'))
        debito = CONTA_DEBITO.search(d.get('debito', ''))
        numero = d.get('no do documento / linha de pagamento', '').lstrip("'")[:60] or d.get('nota fiscal', '')[:60]
        a.novos.append({
            'tipo': 'PAGAR', 'descricao': descricao, 'pessoa_nome': fornecedor[:200], 'pessoa_documento': documento,
            'numero_documento': numero, 'numero_movimento': movimento, 'loja': loja,
            'conta_prevista': contas.get(debito.group(1)) if debito else None, 'forma': forma,
            'emissao': _data(d.get('data de lancamento')) or vencimento, 'vencimento': vencimento, 'valor': valor,
            'parcela': parcela, 'parcelas': parcelas, 'origem': 'IMPORTACAO',
            'grupo': uuid.uuid5(NAMESPACE_MOVIMENTO, movimento) if movimento else uuid.uuid4(),
            'observacao': _observacao(d, descricao, forma, loja),
            '_plano': (grupo_plano, despesa) if despesa else None,
        })
    return a


def _proximo_codigo(prefixo, usados):
    n = 1
    while f'{prefixo}.{n:02d}' in usados:
        n += 1
    return f'{prefixo}.{n:02d}'


def _criar_planos(pares):
    """Plano de Contas → Despesa, debaixo de "2" (despesas). Devolve {(grupo, despesa): PlanoConta}."""
    usados = set(PlanoConta.objects.values_list('codigo', flat=True))
    raiz = PlanoConta.objects.filter(codigo='2').first()
    if raiz is None:
        raiz = PlanoConta.objects.create(codigo='2', nome='Despesas', tipo='DESPESA', analitica=False)
        usados.add('2')
    grupos, filhos = {}, {}
    for p in PlanoConta.objects.filter(tipo='DESPESA', pai__isnull=False).select_related('pai'):
        filhos[(chave_texto(p.pai.nome), chave_texto(p.nome))] = p
        if p.pai_id == raiz.id:
            grupos[chave_texto(p.nome)] = p
    saida = {}
    for grupo_nome, despesa in sorted(pares):
        chave = (chave_texto(grupo_nome), chave_texto(despesa))
        if chave not in filhos:
            grupo = grupos.get(chave[0])
            if grupo is None:
                codigo = _proximo_codigo('2', usados)
                usados.add(codigo)
                grupo = PlanoConta.objects.create(codigo=codigo, nome=grupo_nome[:120], tipo='DESPESA', pai=raiz, analitica=False)
                grupos[chave[0]] = grupo
            codigo = _proximo_codigo(grupo.codigo, usados)
            usados.add(codigo)
            filhos[chave] = PlanoConta.objects.create(codigo=codigo, nome=despesa[:120], tipo='DESPESA', pai=grupo, analitica=True)
        saida[(grupo_nome, despesa)] = filhos[chave]
    return saida


@transaction.atomic
def importar(analise, nome_arquivo='', usuario=None, request=None):
    """Grava as contas novas da análise e devolve quantas entraram."""
    planos = _criar_planos({n['_plano'] for n in analise.novos if n['_plano']})
    titulos = []
    for n in analise.novos:
        campos = {k: v for k, v in n.items() if k != '_plano'}
        titulos.append(Titulo(plano=planos.get(n['_plano']), criado_por=usuario, **campos))
    Titulo.objects.bulk_create(titulos, batch_size=200)
    if titulos:
        auditoria.registrar(request, 'IMPORTAR', f'Importou {len(titulos)} conta(s) a pagar de {nome_arquivo or "planilha"}'[:300],
                            area='pagar', valor=analise.total, usuario=usuario)
    return len(titulos)


def modelo_csv():
    """O arquivo modelo: as colunas do relatório e uma linha de exemplo, em Latin-1 como o original."""
    exemplo = {'Situação': 'A Pagar', 'Data de lançamento': '01/11/2026', 'Data de vencimento': '10/11/2026',
               'Forma pagamento': 'Boleto', 'Filial': 'GLÓRIA', 'Plano de Contas': 'ALUGUEL', 'Despesa': 'ALUGUEL',
               'Fornecedor': 'FORNECEDOR EXEMPLO LTDA', 'CNPJ/CPF Fornecedor': '00.000.000/0000-00',
               'Nº do movimento': '123456', 'Nº do documento / Linha de pagamento': '98765',
               'Observações': 'ALUGUEL DA LOJA - PARCELA 1 DE 12', 'Débito': 'Caixa', 'Valor total': '1.500,00',
               'Valor líquido': '1.500,00'}
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=';', lineterminator='\r\n')
    escritor.writerow(COLUNAS)
    escritor.writerow([exemplo.get(c, '') for c in COLUNAS])
    return saida.getvalue().encode('latin-1')
