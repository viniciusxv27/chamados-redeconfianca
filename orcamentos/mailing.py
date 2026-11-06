"""Mailing da loja: ler a planilha, sugerir as colunas e transformar as linhas em contatos.

O fluxo tem duas etapas para ninguém importar a coluna errada:
1. ``ler_arquivo`` lê o CSV/Excel e guarda as linhas numa base em rascunho;
2. na conferência a pessoa escolhe a coluna do número (e a do nome) e a loja, e
   ``confirmar`` cria os contatos — o número passa pelo mesmo normalizador do
   resto do portal; número inválido ou repetido fica de fora (e é contado).
"""
import csv
import io
import unicodedata

from django.db import transaction
from django.utils import timezone

from core.telefone import normalizar

from .models import BaseMailing, ContatoMailing

LIMITE_LINHAS = 20000
LIMITE_BYTES = 10 * 1024 * 1024


class ErroMailing(Exception):
    """Mensagem para a tela."""


def _norm(t):
    t = unicodedata.normalize('NFKD', str(t or ''))
    return ''.join(c for c in t if not unicodedata.combining(c)).lower().strip()


def _texto(v):
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _ler_csv(conteudo):
    for cod in ('utf-8-sig', 'latin-1'):
        try:
            texto = conteudo.decode(cod)
            break
        except UnicodeDecodeError:
            continue
    try:
        separador = csv.Sniffer().sniff(texto[:5000], delimiters=';,\t|').delimiter
    except csv.Error:
        separador = ';' if texto.count(';') > texto.count(',') else ','
    return [[_texto(c) for c in linha] for linha in csv.reader(io.StringIO(texto), delimiter=separador)]


def _ler_excel(conteudo):
    import openpyxl
    try:
        livro = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ErroMailing(f'Não deu para abrir a planilha: {exc}')
    folha = livro.active
    linhas = []
    for linha in folha.iter_rows(values_only=True):
        linhas.append([_texto(c) for c in linha])
        if len(linhas) > LIMITE_LINHAS + 1:
            break
    return linhas


def ler_arquivo(arquivo, nome, usuario):
    """Lê a planilha e cria a base em rascunho. Devolve a base."""
    if not arquivo:
        raise ErroMailing('Escolha o arquivo (CSV ou Excel).')
    if arquivo.size > LIMITE_BYTES:
        raise ErroMailing('O arquivo passa de 10 MB.')
    conteudo = arquivo.read()
    ext = (arquivo.name or '').lower().rsplit('.', 1)[-1]
    if ext in ('xlsx', 'xlsm'):
        linhas = _ler_excel(conteudo)
    elif ext in ('csv', 'txt'):
        linhas = _ler_csv(conteudo)
    else:
        raise ErroMailing('Envie um arquivo .xlsx ou .csv.')
    linhas = [l for l in linhas if any(c for c in l)]
    if len(linhas) < 2:
        raise ErroMailing('A planilha precisa de um cabeçalho e pelo menos uma linha.')
    if len(linhas) - 1 > LIMITE_LINHAS:
        raise ErroMailing(f'A planilha passa de {LIMITE_LINHAS:,} linhas. Divida em partes.'.replace(',', '.'))
    largura = max(len(l) for l in linhas)
    cabecalho = [(c or f'Coluna {i + 1}')[:80] for i, c in enumerate(linhas[0] + [''] * (largura - len(linhas[0])))]
    corpo = [l + [''] * (largura - len(l)) for l in linhas[1:]]
    return BaseMailing.objects.create(
        nome=(nome or arquivo.name or 'Mailing')[:120], arquivo_nome=(arquivo.name or '')[:255], colunas=cabecalho,
        linhas=corpo, total_linhas=len(corpo), importado_por=usuario)


def sugerir_colunas(base):
    """(coluna do número, coluna do nome) mais prováveis.

    Número: entre as colunas com cara de telefone pelo cabeçalho, a que tem mais
    números válidos nas primeiras linhas ("ID Contato" não ganha de "Celular");
    sem cabeçalho que ajude, a coluna com mais números válidos.
    """
    chaves_num = ('whats', 'celular', 'telefone', 'fone', 'tel', 'numero', 'contato', 'msisdn', 'linha')
    amostra = base.linhas[:200]
    validos = {i: sum(1 for l in amostra if i < len(l) and normalizar(l[i])) for i in range(len(base.colunas))}
    cabecalhos = [_norm(c) for c in base.colunas]
    candidatas = [i for i, n in enumerate(cabecalhos) if any(k in n for k in chaves_num)]
    numero = None
    if any(validos[i] for i in candidatas):
        numero = max(candidatas, key=lambda i: (validos[i], -i))
    elif validos and max(validos.values()):
        numero = max(validos, key=lambda i: (validos[i], -i))
    elif candidatas:
        numero = candidatas[0]
    nome = next((i for i, n in enumerate(cabecalhos) if i != numero and ('nome' in n or 'cliente' in n or 'razao' in n)), None)
    return numero, nome


def previa(base, n=8):
    return base.linhas[:n]


@transaction.atomic
def confirmar(base, loja, coluna_numero, coluna_nome, usuario):
    """Cria os contatos na loja. Devolve a base (com os números da importação)."""
    if base.status != BaseMailing.RASCUNHO:
        raise ErroMailing('Esta base já foi importada.')
    if loja is None:
        raise ErroMailing('Escolha a loja que vai receber o mailing.')
    if coluna_numero is None or not 0 <= coluna_numero < len(base.colunas):
        raise ErroMailing('Escolha a coluna do número.')
    if coluna_nome is not None and not 0 <= coluna_nome < len(base.colunas):
        coluna_nome = None
    # Número que já está "a trabalhar" em outra base ativa da mesma loja não entra de novo.
    ja_na_loja = set(ContatoMailing.objects.filter(loja=loja, status=ContatoMailing.NOVO,
                                                   base__status=BaseMailing.ATIVA).values_list('numero', flat=True))
    vistos, novos = set(), []
    invalidos = duplicados = 0
    for linha in base.linhas:
        numero = normalizar(linha[coluna_numero])
        if not numero:
            invalidos += 1
            continue
        if numero in vistos or numero in ja_na_loja:
            duplicados += 1
            continue
        vistos.add(numero)
        nome = ' '.join(str(linha[coluna_nome]).split())[:200] if coluna_nome is not None else ''
        dados = {base.colunas[i]: v for i, v in enumerate(linha) if v and i not in (coluna_numero, coluna_nome)}
        novos.append(ContatoMailing(base=base, loja=loja, numero=numero, nome=nome.title() if nome.isupper() else nome,
                                    dados=dados))
    ContatoMailing.objects.bulk_create(novos, batch_size=1000)
    base.loja = loja
    base.coluna_numero, base.coluna_nome = coluna_numero, coluna_nome
    base.importados, base.duplicados, base.invalidos = len(novos), duplicados, invalidos
    base.status = BaseMailing.ATIVA
    base.confirmado_em = timezone.now()
    base.importado_por = base.importado_por or usuario
    base.linhas = []
    base.save()
    return base


def encerrar(base):
    base.status = BaseMailing.ENCERRADA
    base.encerrado_em = timezone.now()
    base.save(update_fields=['status', 'encerrado_em'])


def marcar_trabalhado(contato, usuario, orcamento=None):
    contato.status = ContatoMailing.TRABALHADO
    contato.trabalhado_por = usuario
    contato.trabalhado_em = timezone.now()
    if orcamento is not None:
        contato.orcamento = orcamento
    contato.save(update_fields=['status', 'trabalhado_por', 'trabalhado_em', 'orcamento'])
