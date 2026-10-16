"""Extrato do banco: ler OFX/CSV e casar cada linha com um movimento.

OFX é o que todo internet banking exporta ("Money", "OFX"): cada lançamento
traz FITID, que identifica a linha e evita importar duas vezes. CSV varia de
banco para banco; aqui vale o mínimo comum — uma coluna de data, uma de
descrição e uma de valor (ou crédito e débito separados) —, reconhecidas pelo
nome no cabeçalho.

Sugestão de par: mesmo valor (centavo a centavo), data a até 3 dias e ainda
sem conciliação. Quando sobra um só candidato, ele vem marcado.
"""
import csv
import hashlib
import io
import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from . import auditoria
from .models import ExtratoImportacao, ExtratoLinha, MovimentoBancario

JANELA_DIAS = 3


class ExtratoInvalido(Exception):
    pass


def _sem_acento(texto):
    texto = unicodedata.normalize('NFKD', str(texto or ''))
    return ''.join(c for c in texto if not unicodedata.combining(c)).lower().strip()


def valor_br(texto):
    """'1.234,56' / '-1234.56' / 'R$ 10,00 D' → Decimal."""
    bruto = str(texto or '').strip()
    if not bruto:
        raise InvalidOperation
    negativo = bruto.startswith('-') or bruto.endswith('-') or bruto.upper().endswith(' D') or (
        bruto.startswith('(') and bruto.endswith(')'))
    limpo = re.sub(r'[^0-9,.-]', '', bruto).strip('-')
    if ',' in limpo and '.' in limpo:
        limpo = limpo.replace('.', '').replace(',', '.') if limpo.rfind(',') > limpo.rfind('.') else limpo.replace(',', '')
    elif ',' in limpo:
        limpo = limpo.replace(',', '.')
    numero = Decimal(limpo).quantize(Decimal('0.01'))
    return -numero if negativo else numero


def data_br(texto):
    texto = str(texto or '').strip()[:10]
    for formato in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%d/%m/%y', '%d.%m.%Y'):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    raise ValueError(f'data ilegível: {texto!r}')


# ── Leitura ─────────────────────────────────────────────────────────────────
def ler_ofx(conteudo):
    texto = conteudo.decode('latin-1', errors='replace') if isinstance(conteudo, bytes) else conteudo
    linhas = []
    for bloco in re.findall(r'<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>)|</BANKTRANLIST>)', texto, re.S | re.I):
        def campo(nome):
            achado = re.search(rf'<{nome}>([^<\r\n]*)', bloco, re.I)
            return achado.group(1).strip() if achado else ''
        bruto_data = campo('DTPOSTED')
        if not bruto_data or not campo('TRNAMT'):
            continue
        data = date(int(bruto_data[:4]), int(bruto_data[4:6]), int(bruto_data[6:8]))
        valor = Decimal(campo('TRNAMT').replace(',', '.')).quantize(Decimal('0.01'))
        descricao = ' '.join(x for x in (campo('MEMO'), campo('NAME')) if x) or campo('TRNTYPE')
        fitid = campo('FITID')
        linhas.append({'data': data, 'valor': valor, 'descricao': descricao[:255],
                       'documento': (campo('CHECKNUM') or campo('REFNUM') or fitid)[:80], 'fitid': fitid})
    if not linhas:
        raise ExtratoInvalido('Não achei lançamentos neste OFX (bloco <STMTTRN>).')
    return linhas


COLUNAS = {
    'data': ('data', 'data lancamento', 'data do lancamento', 'dt lancamento', 'date', 'data mov', 'data movimento'),
    'descricao': ('descricao', 'historico', 'lancamento', 'descricao do lancamento', 'memo', 'detalhes', 'description'),
    'valor': ('valor', 'valor (r$)', 'valor r$', 'amount', 'montante'),
    'credito': ('credito', 'entrada', 'creditos', 'credito (r$)'),
    'debito': ('debito', 'saida', 'debitos', 'debito (r$)'),
    'documento': ('documento', 'doc', 'n documento', 'numero documento', 'nr documento', 'id'),
}


def _achar(cabecalho, nomes):
    for i, nome in enumerate(cabecalho):
        if _sem_acento(nome) in nomes:
            return i
    return None


def ler_csv(conteudo):
    texto = conteudo.decode('utf-8-sig', errors='replace') if isinstance(conteudo, bytes) else conteudo
    if texto.count('�') > 3 and isinstance(conteudo, bytes):
        texto = conteudo.decode('latin-1')
    amostra = texto[:4000]
    separador = ';' if amostra.count(';') >= amostra.count(',') else ','
    registros = list(csv.reader(io.StringIO(texto), delimiter=separador))
    for inicio, cab in enumerate(registros[:15]):
        indices = {chave: _achar(cab, nomes) for chave, nomes in COLUNAS.items()}
        if indices['data'] is not None and (indices['valor'] is not None or indices['credito'] is not None):
            break
    else:
        raise ExtratoInvalido('O CSV precisa de cabeçalho com "Data", "Descrição"/"Histórico" e "Valor" '
                              '(ou "Crédito" e "Débito").')
    linhas = []
    for registro in registros[inicio + 1:]:
        if not registro or not any(c.strip() for c in registro):
            continue
        def pega(chave):
            i = indices[chave]
            return registro[i] if i is not None and i < len(registro) else ''
        try:
            data = data_br(pega('data'))
        except ValueError:
            continue  # linha de saldo, rodapé…
        try:
            if indices['valor'] is not None and pega('valor').strip():
                valor = valor_br(pega('valor'))
            else:
                credito = valor_br(pega('credito')) if pega('credito').strip() else Decimal('0')
                debito = valor_br(pega('debito')) if pega('debito').strip() else Decimal('0')
                valor = abs(credito) - abs(debito)
        except InvalidOperation:
            continue
        descricao = pega('descricao').strip()
        if not valor or _sem_acento(descricao).startswith('saldo'):
            continue
        linhas.append({'data': data, 'valor': valor, 'descricao': descricao[:255],
                       'documento': pega('documento').strip()[:80], 'fitid': ''})
    if not linhas:
        raise ExtratoInvalido('Não achei lançamentos no CSV.')
    return linhas


def ler_arquivo(nome, conteudo):
    eh_ofx = nome.lower().endswith(('.ofx', '.qfx')) or b'<OFX>' in conteudo[:5000].upper()
    return ('OFX', ler_ofx(conteudo)) if eh_ofx else ('CSV', ler_csv(conteudo))


def _identificador(linha, repeticao):
    if linha['fitid']:
        return f"F:{linha['fitid']}"[:120]
    base = f"{linha['data']}|{linha['valor']}|{_sem_acento(linha['descricao'])}|{linha['documento']}|{repeticao}"
    return 'H:' + hashlib.sha1(base.encode()).hexdigest()


@transaction.atomic
def importar(conta, nome, conteudo, usuario=None, request=None):
    formato, linhas = ler_arquivo(nome, conteudo)
    imp = ExtratoImportacao.objects.create(
        conta=conta, arquivo_nome=nome[:255], formato=formato, importado_por=usuario,
        inicio=min(l['data'] for l in linhas), fim=max(l['data'] for l in linhas))
    vistos, novas, repetidas = {}, 0, 0
    for linha in linhas:
        chave = (linha['data'], linha['valor'], linha['descricao'], linha['documento'])
        vistos[chave] = vistos.get(chave, 0) + 1
        ident = _identificador(linha, vistos[chave])
        _obj, criada = ExtratoLinha.objects.get_or_create(
            conta=conta, identificador=ident,
            defaults={'importacao': imp, 'data': linha['data'], 'valor': linha['valor'],
                      'descricao': linha['descricao'], 'documento': linha['documento']})
        if criada:
            novas += 1
        else:
            repetidas += 1
    imp.linhas_novas, imp.linhas_repetidas = novas, repetidas
    imp.save(update_fields=['linhas_novas', 'linhas_repetidas'])
    auditoria.registrar(request, 'IMPORTAR', f'Importou extrato {formato} de {conta.nome}: {novas} linha(s) nova(s)',
                        area='conciliacao', obj=imp, loja=conta.loja, usuario=usuario,
                        depois={'arquivo': nome, 'novas': novas, 'repetidas': repetidas})
    conciliados = conciliar_automatico(conta, usuario=usuario, request=request)
    return imp, conciliados


# ── Conciliação ─────────────────────────────────────────────────────────────
def candidatos(linha, livres=None):
    qs = livres if livres is not None else MovimentoBancario.objects.filter(
        conta=linha.conta, estornado=False, conciliado=False)
    return [m for m in qs if m.valor == linha.valor and abs((m.data - linha.data).days) <= JANELA_DIAS]


@transaction.atomic
def conciliar(linha, movimento, usuario=None, request=None, automatico=False):
    linha = ExtratoLinha.objects.select_for_update().get(pk=linha.pk)
    movimento = MovimentoBancario.objects.select_for_update().get(pk=movimento.pk)
    if linha.movimento_id:
        raise ValueError('Esta linha do extrato já está conciliada.')
    if movimento.conciliado or movimento.estornado:
        raise ValueError('Este movimento já está conciliado (ou foi estornado).')
    if movimento.conta_id != linha.conta_id:
        raise ValueError('O movimento é de outra conta.')
    linha.movimento, linha.conciliada_por, linha.conciliada_em, linha.ignorada = movimento, usuario, timezone.now(), False
    linha.save(update_fields=['movimento', 'conciliada_por', 'conciliada_em', 'ignorada'])
    movimento.conciliado = True
    movimento.save(update_fields=['conciliado'])
    auditoria.registrar(request, 'CONCILIAR',
                        f'{"Conciliou sozinho" if automatico else "Conciliou"} "{linha.descricao}" com "{movimento.descricao}"',
                        area='conciliacao', obj=linha, valor=linha.valor, loja=linha.conta.loja, usuario=usuario)


def desconciliar(linha, usuario=None, request=None):
    from .servicos import _desconciliar
    if linha.movimento_id:
        with transaction.atomic():
            _desconciliar(linha.movimento, usuario, request, 'desfeita na tela')


def conciliar_automatico(conta, usuario=None, request=None):
    """Casa as linhas que têm exatamente um candidato (e o candidato, uma linha só)."""
    livres = list(MovimentoBancario.objects.filter(conta=conta, estornado=False, conciliado=False))
    linhas = list(ExtratoLinha.objects.filter(conta=conta, movimento__isnull=True, ignorada=False))
    usados, feitos = set(), 0
    for linha in linhas:
        opcoes = [m for m in candidatos(linha, livres) if m.pk not in usados]
        if len(opcoes) != 1:
            continue
        mov = opcoes[0]
        disputada = [l for l in linhas if l is not linha and not l.movimento_id and l.valor == mov.valor
                     and abs((l.data - mov.data).days) <= JANELA_DIAS]
        if disputada:
            continue
        conciliar(linha, mov, usuario=usuario, request=request, automatico=True)
        usados.add(mov.pk)
        feitos += 1
    return feitos


def criar_movimento_da_linha(linha, plano=None, descricao='', usuario=None, request=None):
    """Tarifa, rendimento… que só apareceu no extrato: vira lançamento já conciliado."""
    from .servicos import lancar_avulso
    with transaction.atomic():
        mov = lancar_avulso(linha.conta, linha.data, linha.valor, descricao or linha.descricao or 'Lançamento do extrato',
                            plano=plano, usuario=usuario, request=request)
        conciliar(linha, mov, usuario=usuario, request=request)
    return mov


def periodo_padrao():
    hoje = timezone.localdate()
    return hoje - timedelta(days=45), hoje
