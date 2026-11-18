"""Importação em lote da planilha do Quadro de HC — em duas etapas.

1. **Ler** (``ler_entrada``): o .xlsx/.csv enviado ou o link do Google Sheets
   (o servidor baixa o CSV da aba do ``gid``; só leitura) vira uma lista de
   blocos (loja / bloco especial / departamento do escritório) com as linhas de
   cada um. Isso — e só isso — fica guardado na sessão: é JSON simples.
2. **Prévia** (``montar_previa``): casa cada bloco com um ``Sector`` e cada
   pessoa com um usuário, e lista o que mudaria — sem gravar nada. É refeita a
   cada pedido, com o banco do momento, então a prévia nunca envelhece.
3. **Aplicar** (``aplicar_importacao``): refaz a prévia e grava só o que veio
   marcado, numa transação: cadastro com ``UserChangeLog`` (o mesmo
   ``users.auditoria`` da edição de usuário), observação, vaga, carteira (o
   mesmo ``servicos.salvar_carteira`` da tela) e loja/cargo pela movimentação
   do quadro (``servicos.registrar_movimentacao`` com a data de hoje).

Layout da planilha que o leitor entende (coluna A vazia, mas não importa onde
o bloco começa): título do bloco sozinho numa linha ("NORTE SUL - ES",
"SNIPERS"); "SERRÃO - COORDENAÇÃO - JOÃO" vale para as lojas de baixo até o
próximo; cabeçalho Cargo, Nome, CIDADE ONDE RESIDE, Login, Nascimento,
Contratação, …, OBS, NÚMERO DO COLABORADOR; "VAGA EM ABERTO (NOME)" é vaga; o
bloco ESCRITÓRIO tem Cargo, Nome, Setor e vira um bloco por setor.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone

from users.models import Sector, User

from . import servicos
from .models import AjusteHC, Carteira, ObservacaoHC, VagaHC
from .servicos import ErroQuadro, _norm, e_loja, grupo_do_cargo, nome_completo

LIMITE_BYTES = 5 * 1024 * 1024
LIMITE_LINHAS = 5000
GRUPOS_GENERICOS = ('Outros', 'Sem cargo')
OBS_MOVIMENTACAO = 'Importação da planilha do quadro'


# ---------------------------------------------------------------------------
# Entrada: arquivo ou link do Google Sheets
# ---------------------------------------------------------------------------
_RE_PLANILHA = re.compile(r'^https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{20,})(?:[/?#]|$)')
_RE_GID = re.compile(r'[#?&]gid=(\d+)')


def link_de_exportacao(link: str) -> str:
    """O link da planilha → o endereço de exportação em CSV da aba do gid (só docs.google.com)."""
    link = (link or '').strip()
    m = _RE_PLANILHA.match(link)
    if not m:
        raise ErroQuadro('Cole o link de uma planilha do Google Sheets (https://docs.google.com/spreadsheets/d/…).')
    g = _RE_GID.search(link)
    return f'https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv&gid={g.group(1) if g else "0"}'


def baixar_google(link: str) -> bytes:
    """Baixa (só leitura) o CSV da aba. A planilha precisa estar aberta a quem tem o link."""
    import requests

    url = link_de_exportacao(link)
    try:
        r = requests.get(url, timeout=25, stream=True, headers={'User-Agent': 'portal-quadro-hc'})
    except requests.RequestException as e:
        raise ErroQuadro(f'Não consegui baixar a planilha do Google ({type(e).__name__}).')
    try:
        if r.status_code != 200:
            raise ErroQuadro(f'O Google não entregou a planilha (HTTP {r.status_code}). Confira o link e se ela está '
                             'compartilhada como "qualquer pessoa com o link".')
        corpo = r.raw.read(LIMITE_BYTES + 1, decode_content=True)
    finally:
        r.close()
    if len(corpo) > LIMITE_BYTES:
        raise ErroQuadro('A planilha passou de 5 MB.')
    inicio = corpo.lstrip()[:20].lower()
    if 'text/html' in (r.headers.get('Content-Type') or '') or inicio.startswith((b'<!doctype', b'<html')):
        raise ErroQuadro('O Google pediu login: a planilha não está pública. Compartilhe como "qualquer pessoa com '
                         'o link" ou baixe a aba em .xlsx/.csv e envie o arquivo.')
    return corpo


def _decodificar(corpo: bytes) -> str:
    for enc in ('utf-8-sig', 'cp1252'):
        try:
            return corpo.decode(enc)
        except UnicodeDecodeError:
            continue
    return corpo.decode('latin-1')


def linhas_csv(corpo: bytes) -> list[list[str]]:
    texto = _decodificar(corpo)
    amostra = texto[:5000]
    sep = ';' if amostra.count(';') > amostra.count(',') else ','
    return [list(r) for r in csv.reader(io.StringIO(texto), delimiter=sep)]


def _celula(v) -> str:
    if v is None:
        return ''
    if isinstance(v, (datetime, date)):
        return v.strftime('%d/%m/%Y')
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def linhas_xlsx(corpo: bytes) -> list[list[str]]:
    """A aba que mais parece o quadro (mais cabeçalhos "Cargo/Nome")."""
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(corpo), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 — arquivo corrompido ou que não é xlsx
        raise ErroQuadro('Não consegui abrir o arquivo .xlsx.')
    melhor, pontos = [], -1
    try:
        for ws in wb.worksheets:
            linhas = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= LIMITE_LINHAS:
                    break
                linhas.append([_celula(v) for v in row])
            p = sum(1 for linha in linhas if _cabecalho([_limpo(c) for c in linha]))
            if p > pontos:
                melhor, pontos = linhas, p
    finally:
        wb.close()
    return melhor


def ler_entrada(arquivo=None, link='') -> dict:
    """O que veio da tela (arquivo ou link) → os blocos da planilha (JSON, para a sessão)."""
    if arquivo:
        if arquivo.size > LIMITE_BYTES:
            raise ErroQuadro('O arquivo passou de 5 MB.')
        nome = (arquivo.name or '').lower()
        corpo = arquivo.read()
        if nome.endswith('.xlsx') or corpo[:2] == b'PK':
            linhas = linhas_xlsx(corpo)
        elif nome.endswith(('.csv', '.txt')):
            linhas = linhas_csv(corpo)
        else:
            raise ErroQuadro('Envie a planilha em .xlsx ou .csv.')
        origem = arquivo.name
    elif (link or '').strip():
        linhas = linhas_csv(baixar_google(link))
        origem = link.strip()
    else:
        raise ErroQuadro('Envie o arquivo ou cole o link da planilha.')
    dados = ler_planilha(linhas, origem)
    if not any(b['linhas'] for b in dados['blocos']):
        raise ErroQuadro('Não achei nenhum bloco com cabeçalho "Cargo / Nome" na planilha.')
    return dados


# ---------------------------------------------------------------------------
# Leitura do layout
# ---------------------------------------------------------------------------
def _limpo(texto) -> str:
    """Sem espaços duplos nem caracteres invisíveis (o telefone vinha com U+202C)."""
    texto = ''.join(c for c in str(texto or '') if unicodedata.category(c) != 'Cf')
    return ' '.join(texto.split())


def _chave_coluna(n: str):
    if n == 'CARGO' or n == 'FUNCAO':
        return 'cargo'
    if n == 'NOME' or n == 'COLABORADOR':
        return 'nome'
    if n.startswith('CIDADE') or 'RESIDE' in n or n.startswith('BAIRRO'):
        return 'cidade'
    if n.startswith('LOGIN'):
        return 'login'
    if n.startswith('NASCIMENTO'):
        return 'nascimento'
    if n.startswith('CONTRATA') or n.startswith('ADMISS'):
        return 'contratacao'
    if n.startswith('OBS'):
        return 'obs'
    if 'NUMERO' in n or 'TELEFONE' in n or 'CELULAR' in n or 'WHATS' in n:
        return 'telefone'
    if n == 'SETOR' or n.startswith('DEPARTAMENTO'):
        return 'setor'
    return None


def _cabecalho(celulas: list[str]) -> dict:
    """{coluna: chave} se a linha é o cabeçalho (tem "Cargo" e "Nome"); senão {}."""
    normas = [_norm(c) for c in celulas]
    if 'CARGO' not in normas or 'NOME' not in normas:
        return {}
    mapa = {}
    for col, n in enumerate(normas):
        chave = _chave_coluna(n) if n else None
        if chave and chave not in mapa.values():
            mapa[col] = chave
    return mapa


_RE_DATA = re.compile(r'^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})$')
_RE_ISO = re.compile(r'^(\d{4})-(\d{2})-(\d{2})')


def _data(texto: str):
    """'15/1/2025' → '2025-01-15' (ISO, para a sessão); '' se não for data."""
    texto = (texto or '').strip()
    m = _RE_DATA.match(texto)
    try:
        if m:
            d, mes, a = (int(x) for x in m.groups())
            if a < 100:
                a += 2000 if a <= 40 else 1900
            return date(a, mes, d).isoformat()
        m = _RE_ISO.match(texto)
        if m:
            return date(*(int(x) for x in m.groups())).isoformat()
    except ValueError:
        return ''
    return ''


_RE_COORD = re.compile(r'\s*[-–:]?\s*\bCOORDENA\w*\s*[-–:]?\s*', re.IGNORECASE)


def _carteira_do_titulo(texto: str) -> tuple[str, str]:
    """'SERRÃO - COORDENAÇÃO - JOÃO' → ('SERRÃO', 'JOÃO')."""
    partes = [p.strip(' -–:') for p in _RE_COORD.split(texto, maxsplit=1)]
    nome = partes[0] if partes else texto
    coord = partes[1] if len(partes) > 1 else ''
    return nome or texto, coord


def _linha(n: int, reg: dict) -> dict:
    nome_bruto = reg.get('nome', '')
    nn = _norm(nome_bruto)
    parenteses = [p.strip() for p in re.findall(r'\(([^)]*)\)', nome_bruto) if p.strip()]
    sem_parenteses = _limpo(re.sub(r'\([^)]*\)', ' ', nome_bruto))
    vaga = nn.startswith('VAGA EM ABERTO') or nn == 'VAGA'
    login_txt = reg.get('login', '')
    login = servicos._login(login_txt)
    obs = reg.get('obs', '')
    return {
        'n': n, 'cargo': reg.get('cargo', ''), 'nome': nome_bruto if vaga else sem_parenteses,
        'vaga': vaga, 'vaga_obs': ' · '.join(parenteses + ([obs] if obs else [])) if vaga else '',
        'marca_vaga': (not vaga) and 'VAGA EM ABERTO' in nn,
        'cidade': reg.get('cidade', ''), 'login': login if login.isdigit() else '', 'login_txt': login_txt,
        'nascimento': _data(reg.get('nascimento', '')), 'nascimento_txt': reg.get('nascimento', ''),
        'contratacao': _data(reg.get('contratacao', '')), 'contratacao_txt': reg.get('contratacao', ''),
        'obs': obs, 'telefone': reg.get('telefone', ''), 'setor': reg.get('setor', ''),
    }


def ler_planilha(linhas: list[list], origem: str = '') -> dict:
    """As linhas cruas → {'blocos': [...], 'carteiras': [...]}, tudo JSON."""
    linhas = [[_limpo(c) for c in linha] for linha in linhas[:LIMITE_LINHAS]]
    blocos, carteiras = [], []
    estado = {'carteira': None, 'titulo': '', 'cab': {}, 'bloco': None, 'escritorio': False, 'titulo_escritorio': ''}
    por_departamento = {}

    def novo_bloco(titulo, busca=None, escritorio=False):
        b = {'i': len(blocos), 'titulo': titulo or '(bloco sem título)', 'busca': busca if busca is not None else titulo,
             'carteira': None if escritorio else estado['carteira'], 'escritorio': escritorio, 'linhas': []}
        blocos.append(b)
        if b['carteira'] is not None:
            carteiras[b['carteira']]['blocos'].append(b['i'])
        return b

    def proxima(i):
        for j in range(i + 1, len(linhas)):
            if any(linhas[j]):
                return linhas[j]
        return None

    for i, cel in enumerate(linhas):
        n = i + 1
        cheias = [(c, v) for c, v in enumerate(cel) if v]
        if not cheias:
            continue
        cab = _cabecalho(cel)
        if cab:
            estado['cab'] = cab
            estado['escritorio'] = 'setor' in cab.values()
            if estado['escritorio']:
                estado['bloco'] = None
                estado['titulo_escritorio'] = estado['titulo'] or 'ESCRITÓRIO'
            else:
                estado['bloco'] = novo_bloco(estado['titulo'])
            estado['titulo'] = ''
            continue
        if len(cheias) == 1:
            col, texto = cheias[0]
            if 'COORDENA' in _norm(texto):
                nome, coord = _carteira_do_titulo(texto)
                carteiras.append({'i': len(carteiras), 'titulo': texto, 'nome': nome, 'coord': coord, 'blocos': [],
                                  'n': n})
                estado.update(carteira=len(carteiras) - 1, bloco=None, titulo='')
                continue
            seguinte = proxima(i)
            col_nome = next((c for c, k in estado['cab'].items() if k == 'nome'), None)
            if (seguinte is not None and _cabecalho(seguinte)) or not estado['cab']:
                estado.update(titulo=texto, bloco=None)
                continue
            if col_nome is None or col < col_nome:
                # Título sem cabeçalho logo abaixo: novo bloco com o mesmo cabeçalho.
                estado['bloco'] = None if estado['escritorio'] else novo_bloco(texto)
                continue
        if not estado['cab']:
            continue
        reg = {k: (cel[c] if c < len(cel) else '') for c, k in estado['cab'].items()}
        if not reg.get('nome') or _norm(reg.get('nome')) == 'NOME':
            continue
        if estado['escritorio']:
            depto = reg.get('setor') or 'sem setor'
            chave = _norm(depto)
            if chave not in por_departamento:
                por_departamento[chave] = novo_bloco(f'{estado["titulo_escritorio"]} · {depto}', busca=depto,
                                                     escritorio=True)
            alvo = por_departamento[chave]
        else:
            alvo = estado['bloco'] or novo_bloco('')
            estado['bloco'] = alvo
        alvo['linhas'].append(_linha(n, reg))
    blocos = [b for b in blocos if b['linhas']]
    vivos = {b['i'] for b in blocos}
    for c in carteiras:
        c['blocos'] = [i for i in c['blocos'] if i in vivos]
    return {'origem': origem, 'lido_em': timezone.now().isoformat(), 'blocos': blocos, 'carteiras': carteiras,
            'pessoas': sum(1 for b in blocos for x in b['linhas'] if not x['vaga']),
            'vagas': sum(1 for b in blocos for x in b['linhas'] if x['vaga'])}


# ---------------------------------------------------------------------------
# Casamentos
# ---------------------------------------------------------------------------
_ALIAS = {'VIX': 'VITORIA', 'RH': 'RECURSOS HUMANOS', 'DP': 'DEPARTAMENTO PESSOAL', 'VV': 'VILA VELHA'}
_VAZIAS = {'LOJA', 'DE', 'DA', 'DO', 'DAS', 'DOS', 'E', 'SHOPPING'}
_UF = {'ES', 'RJ', 'SP', 'MG', 'BA'}


def _tokens(texto) -> frozenset:
    toks = re.sub(r'[^A-Z0-9 ]', ' ', _norm(texto)).split()
    while toks and toks[-1] in _UF:
        toks.pop()
    saida = set()
    for t in toks:
        for p in _ALIAS.get(t, t).split():
            if p in _VAZIAS:
                continue
            saida.add(p[:-1] if len(p) > 3 and p.endswith('S') else p)
    return frozenset(saida)


def casar_setor(texto, setores) -> tuple:
    """(setor ou None, candidatos): igual > o nome do setor dentro do título > o título dentro do nome."""
    alvo = _tokens(texto)
    if not alvo:
        return None, []
    marcas = [(s, _tokens(s.name)) for s in setores]
    marcas = [(s, t) for s, t in marcas if t]
    for teste in (lambda t: t == alvo, lambda t: t <= alvo, lambda t: alvo <= t):
        achados = [s for s, t in marcas if teste(t)]
        if len(achados) == 1:
            return achados[0], []
        if len(achados) > 1:
            return None, achados
    return None, []


def _digitos(texto) -> str:
    d = re.sub(r'\D', '', str(texto or ''))
    if len(d) in (12, 13) and d.startswith('55'):
        d = d[2:]
    return d


def _telefone(d: str) -> str:
    return f'({d[:2]}) {d[2:-4]}-{d[-4:]}'


_LUGAR_ABREV = {'VV': 'VILA VELHA', 'VIX': 'VITORIA', 'SP': 'SAO PAULO', 'I': '1', 'II': '2', 'III': '3', 'IV': '4'}


def _lugar(texto) -> str:
    """Bairro/cidade comparável: sem acento, sem pontuação, VV = VILA VELHA, II = 2."""
    toks = re.sub(r'[^A-Z0-9 ]', ' ', _norm(texto)).split()
    return ' '.join(_LUGAR_ABREV.get(t, t) for t in toks)


def _mesmo_lugar(planilha, cadastro) -> bool:
    """Igual, a planilha abrevia o cadastro ("EMBU" × "Embu das Artes", "LARANJEIRAS" × "Parque Residencial
    Laranjeiras") ou só erra a digitação ("SÃO FRANSCISCO" × "São Francisco"): o cadastro fica como está."""
    from difflib import SequenceMatcher

    a, b = _lugar(planilha), _lugar(cadastro)
    if not a or not b:
        return a == b
    if a == b or b.startswith(a) or set(a.split()) <= set(b.split()):
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.85


def _separar_cidade(texto: str, cidades=frozenset()) -> tuple[str, str]:
    """'COLINA DE LARANJEIRAS - SERRA' → ('COLINA DE LARANJEIRAS', 'SERRA').

    Uma parte só é cidade quando é uma cidade que o cadastro já conhece ("SERRA"); senão é bairro
    ("RESIDENCIAL JACARAIPE").
    """
    partes = [p.strip() for p in re.split(r'\s+[-–]\s*|\s*[-–]\s+|\s*–\s*', texto or '') if p.strip(' -–')]
    if not partes:
        return '', ''
    if len(partes) == 1:
        unica = partes[0]
        if _lugar(unica) in cidades or _norm(unica) in _LUGAR_ABREV:
            return '', unica
        return unica, ''
    return ' - '.join(partes[:-1]), partes[-1]


def _fmt(d) -> str:
    return d.strftime('%d/%m/%Y') if d else '—'


class _Contexto:
    """O que a prévia consulta uma vez só (usuários ativos, cargos, vagas, ajustes)."""

    def __init__(self, hoje):
        self.hoje = hoje
        self.setores = list(Sector.objects.order_by('name'))
        self.por_id = {s.id: s for s in self.setores}
        ativos = list(User.objects.filter(is_active=True).select_related('sector'))
        self.por_login, self.por_nome = defaultdict(list), defaultdict(list)
        for u in ativos:
            login = servicos._login(u.login_code)
            if login:
                self.por_login[login].append(u)
            self.por_nome[_norm(nome_completo(u))].append(u)
        self.ativos = ativos
        self.ajustes = {a.colaborador_id: a for a in AjusteHC.objects.all()}
        self.gerentes = servicos.ids_gerentes_por_grupo()
        self.obs = dict(ObservacaoHC.objects.values_list('colaborador_id', 'texto'))
        self.cargos = {_norm(c): c for c in servicos.cargos_disponiveis()}
        self.cargos_canonicos = {}
        for c in self.cargos.values():
            self.cargos_canonicos.setdefault(_cargo_canonico(c), c)
        contagem_cidades = Counter(_lugar(u.city) for u in ativos if u.city)
        self.cidades = {c for c, q in contagem_cidades.items() if q >= 2 and not c.isdigit()}
        self.cargo_do_grupo = {}
        for c, _q in Counter(' '.join(u.job_title.split()) for u in ativos if u.job_title).most_common():
            self.cargo_do_grupo.setdefault(grupo_do_cargo(c)[1], c)
        self.vagas = defaultdict(Counter)
        for v in VagaHC.objects.filter(fechada_em__isnull=True):
            self.vagas[v.setor_id][_chave_vaga(v.cargo)] += 1
        self.logins_todos = None

    def outro_com_login(self, login, u):
        if self.logins_todos is None:
            self.logins_todos = defaultdict(list)
            for pk, lc in User.objects.exclude(login_code='').values_list('pk', 'login_code'):
                self.logins_todos[servicos._login(lc)].append(pk)
        return [pk for pk in self.logins_todos.get(login, []) if pk != u.pk]


def _chave_vaga(cargo) -> str:
    grupo = grupo_do_cargo(cargo)[1]
    return grupo if grupo not in GRUPOS_GENERICOS else _norm(cargo)


def _rotulo_usuario(u) -> str:
    login = servicos._login(u.login_code)
    return f'{nome_completo(u)} ({login})' if login else nome_completo(u)


def casar_pessoa(linha: dict, ctx: _Contexto) -> tuple:
    """(usuário, como, pendência): primeiro pelo login, depois pelo nome normalizado único entre os ativos."""
    nome = _norm(linha['nome'])
    if linha['login']:
        achados = ctx.por_login.get(linha['login'], [])
        if len(achados) == 1:
            return achados[0], 'login', ''
        if len(achados) > 1:
            pelo_nome = [u for u in achados if _norm(nome_completo(u)) == nome]
            if len(pelo_nome) == 1:
                return pelo_nome[0], 'login', ''
            return None, '', ('Login ' + linha['login'] + ' está em mais de um cadastro ativo: '
                              + ', '.join(_rotulo_usuario(u) for u in achados))
    achados = ctx.por_nome.get(nome, [])
    if len(achados) == 1:
        return achados[0], 'nome', ''
    if len(achados) > 1:
        return None, '', 'Nome em mais de um cadastro ativo: ' + ', '.join(_rotulo_usuario(u) for u in achados)
    parecidos = [u for k, lista in ctx.por_nome.items() if nome and (k.startswith(nome + ' ') or nome.startswith(k + ' '))
                 for u in lista][:3]
    motivo = 'Não achei no cadastro ativo (nem pelo login nem pelo nome).'
    if parecidos:
        motivo += ' Parecidos: ' + ', '.join(_rotulo_usuario(u) for u in parecidos) + '.'
    return None, '', motivo


_CARGO_ABREV = {'JUNIOR': 'JR', 'PLENO': 'PL', 'SENIOR': 'SR', 'AUX': 'AUXILIAR', 'ASSIST': 'ASSISTENTE',
                'RECURSOS HUMANOS': 'RH', 'ADM': 'ADMINISTRATIVO'}
_CARGO_VAZIAS = {'DE', 'DA', 'DO', 'DAS', 'DOS', 'E', 'A', '(A)'}


def _cargo_canonico(cargo) -> tuple:
    """'Analista de Treinamentos Jr.' e 'ANALISTA DE TREINAMENTO JUNIOR' → ('ANALISTA', 'TREINAMENTO', 'JR')."""
    n = re.sub(r'[^A-Z0-9 ]', ' ', _norm(cargo).replace('(A)', ' '))
    n = ' ' + ' '.join(n.split()) + ' '
    for longo, curto in _CARGO_ABREV.items():
        n = n.replace(f' {longo} ', f' {curto} ')
    saida = []
    for t in n.split():
        if t in _CARGO_VAZIAS:
            continue
        saida.append(t[:-1] if len(t) > 3 and t.endswith('S') else t)
    return tuple(saida)


def _mesmo_cargo(planilha, cadastro) -> bool:
    """Igual, ou a planilha diz menos que o cadastro ("ESTAGIÁRIO" × "ESTAGIÁRIO ANÁLISE DE DADOS")."""
    a, b = _cargo_canonico(planilha), _cargo_canonico(cadastro)
    if not a:
        return False
    # "Consultor(a)" × "CONSULTOR DE VENDAS PL": cabe, mas o grupo muda (pleno → consultor) — não é o mesmo.
    return a == b or (set(a) <= set(b) and grupo_do_cargo(planilha)[1] == grupo_do_cargo(cadastro)[1])


def _texto_livre(cargo: str) -> bool:
    """Frase na coluna do cargo ("Quer ser GO Montserrat ou Serra Sede."): não é cargo."""
    palavras = cargo.split()
    if len(palavras) > 6 or cargo.rstrip().endswith(('?', '!')):
        return True
    # Ponto final depois de palavra inteira ("gerente.") é frase; depois de sigla ("JR.", "ADM.") não.
    return cargo.rstrip().endswith('.') and len(palavras) >= 3 and len(palavras[-1].strip('.')) > 3


def avaliar_cargo(cargo_planilha: str, u, ctx: _Contexto) -> tuple[str, str]:
    """(cargo novo para o cadastro, pendência). Mesmo grupo do quadro = não muda."""
    cargo = _limpo(cargo_planilha)
    if not cargo:
        return '', ''
    if _texto_livre(cargo):
        return '', f'Cargo "{cargo}" não reconhecido (no cadastro: {u.job_title or "sem cargo"}) — não aplicado.'
    nc = _norm(cargo)
    if nc == _norm(u.job_title) or _mesmo_cargo(cargo, u.job_title):
        return '', ''
    grupo = grupo_do_cargo(cargo)[1]
    generico = grupo in GRUPOS_GENERICOS
    grupo_quadro = servicos.funcao_da_pessoa(u, ctx.ajustes.get(u.id), ctx.gerentes)[1]
    if not generico and grupo == grupo_quadro:
        return '', ''
    if nc in ctx.cargos:
        return ctx.cargos[nc], ''
    if not generico and grupo in ctx.cargo_do_grupo:
        return ctx.cargo_do_grupo[grupo], ''  # "Consultor(a)" → o cargo de consultor mais usado no cadastro
    if _cargo_canonico(cargo) in ctx.cargos_canonicos:
        return ctx.cargos_canonicos[_cargo_canonico(cargo)], ''
    return '', f'Cargo "{cargo}" não reconhecido (no cadastro: {u.job_title or "sem cargo"}) — não aplicado.'


def _cargo_da_vaga(cargo_planilha: str, ctx: _Contexto) -> str:
    cargo = _limpo(cargo_planilha)
    nc = _norm(cargo)
    if not cargo or _texto_livre(cargo):
        return ''
    if nc in ctx.cargos:
        return ctx.cargos[nc]
    grupo = grupo_do_cargo(cargo)[1]
    if grupo not in GRUPOS_GENERICOS:
        return ctx.cargo_do_grupo.get(grupo, cargo)
    return ctx.cargos_canonicos.get(_cargo_canonico(cargo), '')


def _mesma_pessoa(planilha: str, atual: str) -> bool:
    a, b = set(_norm(planilha).split()), set(_norm(atual).split())
    return bool(a) and a <= b


def _coordenador(texto: str, ctx: _Contexto):
    alvo = set(_norm(texto).split())
    if not alvo:
        return None
    achados = [u for u in ctx.ativos if ('COORDENADOR' in _norm(u.job_title) or 'SUPERVISOR' in _norm(u.job_title))
               and alvo <= set(_norm(nome_completo(u)).split())]
    return achados[0] if len(achados) == 1 else None


# ---------------------------------------------------------------------------
# Prévia
# ---------------------------------------------------------------------------
def montar_previa(dados: dict, escolhas: dict | None = None, hoje=None) -> dict:
    """O que a importação faria — nada é gravado. ``escolhas``: {bloco: '' (automático) | '0' (sem loja) | id}."""
    escolhas = escolhas or {}
    hoje = hoje or timezone.localdate()
    ctx = _Contexto(hoje)
    pendencias, blocos = [], []
    propostos = defaultdict(set)      # user_id → campos já propostos por outra linha
    com_movimento = {}                # user_id → linha que já move a pessoa
    vistos = {}                       # user_id → (bloco, linha) da 1ª vez
    usados_vaga = defaultdict(Counter)
    citados = set()                   # candidatos de casamento ambíguo

    for b in dados.get('blocos', []):
        auto, candidatos = casar_setor(b['busca'], ctx.setores)
        escolha = str(escolhas.get(str(b['i']), '') or '')
        if escolha == '0':
            setor = None
        elif escolha.isdigit() and int(escolha) in ctx.por_id:
            setor = ctx.por_id[int(escolha)]
        else:
            setor, escolha = auto, ''
        loja = bool(setor and e_loja(setor))
        bloco = {'i': b['i'], 'titulo': b['titulo'], 'setor': setor, 'auto': auto, 'candidatos': candidatos,
                 'escolha': escolha, 'loja': loja, 'escritorio': b['escritorio'], 'pessoas': [], 'vagas': [],
                 'ausentes': [], 'carteira': (dados['carteiras'][b['carteira']]['nome']
                                              if b.get('carteira') is not None else '')}
        onde = setor.name if setor else b['titulo']

        for linha in b['linhas']:
            n = linha['n']
            if linha['vaga']:
                bloco['vagas'].append(_vaga(linha, setor, ctx, usados_vaga, pendencias, onde))
                continue
            p = {'n': n, 'nome': linha['nome'], 'cargo': linha['cargo'], 'user': None, 'como': '', 'pendencia': '',
                 'campos': [], 'mov': None, 'avisos': [], 'linha': linha}
            bloco['pessoas'].append(p)
            u, como, pend = casar_pessoa(linha, ctx)
            if not u:
                p['pendencia'] = pend
                # Homônimos/login repetido: não vão para a lista de "fora da planilha".
                citados.update(x.id for x in ctx.por_login.get(linha['login'], []) + ctx.por_nome.get(_norm(linha['nome']), []))
                pendencias.append({'tipo': 'pessoa', 'onde': onde, 'n': n, 'nome': linha['nome'], 'motivo': pend})
                continue
            p.update(user=u, como=como, cadastro=_rotulo_usuario(u), cargo_cadastro=u.job_title or '',
                     loja_cadastro=servicos._loja(u.sector))
            if como == 'login' and _norm(nome_completo(u)) != _norm(linha['nome']):
                p['avisos'].append(f'Casou pelo login; no cadastro o nome é {nome_completo(u)}.')
            if u.id in vistos:
                p['avisos'].append(f'Também aparece na linha {vistos[u.id][1]} ({vistos[u.id][0]}): '
                                   'o que aquela linha já propõe não se repete aqui.')
            else:
                vistos[u.id] = (onde, n)
            if linha['marca_vaga']:
                p['avisos'].append('A planilha marca "VAGA EM ABERTO" junto do nome: a pessoa segue no quadro; '
                                   'abra a vaga quando ela sair.')
            _propor_campos(p, u, linha, ctx, propostos)

            # Loja e cargo: pela movimentação do quadro.
            aj = ctx.ajustes.get(u.id)
            setor_quadro = aj.setor_id if aj and aj.setor_id else u.sector_id
            novo_setor = setor if setor and setor.id != setor_quadro else None
            novo_cargo, pend_cargo = avaliar_cargo(linha['cargo'], u, ctx)
            if pend_cargo:
                p['avisos'].append(pend_cargo)
                pendencias.append({'tipo': 'cargo', 'onde': onde, 'n': n, 'nome': linha['nome'], 'motivo': pend_cargo})
            if novo_setor or novo_cargo:
                if u.id in com_movimento:
                    p['avisos'].append(f'Movimentação já proposta na linha {com_movimento[u.id]}: esta não entra.')
                else:
                    com_movimento[u.id] = n
                    partes = []
                    if novo_setor:
                        partes.append(f'loja {servicos._loja(u.sector)} → {novo_setor.name}')
                    if novo_cargo:
                        partes.append(f'cargo {u.job_title or "sem cargo"} → {novo_cargo}')
                    p['mov'] = {'chave': f'm:{n}', 'novo_setor': novo_setor, 'novo_cargo': novo_cargo,
                                'texto': '; '.join(partes),
                                # Escritório/departamento: o setor principal mexe em acesso; vem desmarcado.
                                'marcado': not (novo_setor and not e_loja(novo_setor))}
        blocos.append(bloco)

    # Quem o quadro mostra numa loja da planilha e não aparece em lugar nenhum dela (só aviso).
    casados = {p['user'].id for b in blocos for p in b['pessoas'] if p['user']}
    na_planilha = casados | citados
    pessoas, _fora, ajustes = servicos.pessoas_do_quadro(hoje)
    por_setor = defaultdict(list)
    for u in pessoas:
        aj = ajustes.get(u.id)
        por_setor[aj.setor_id if aj and aj.setor_id else u.sector_id].append(u)
    ja = set()
    for bloco in blocos:
        s = bloco['setor']
        if not bloco['loja'] or s.id in ja:
            continue
        ja.add(s.id)
        bloco['ausentes'] = [{'nome': nome_completo(u), 'cargo': u.job_title or '', 'login': servicos._login(u.login_code)}
                             for u in sorted(por_setor.get(s.id, []), key=lambda x: _norm(nome_completo(x)))
                             if u.id not in na_planilha]

    for bloco in blocos:
        for p in bloco['pessoas']:
            p['destaque'] = bool(p['campos'] or p['mov'] or p['pendencia'] or p['avisos'])
        bloco['com_algo'] = [p for p in bloco['pessoas'] if p['destaque']]
        bloco['sem_mudanca'] = [p for p in bloco['pessoas'] if not p['destaque']]
        bloco['itens'] = (sum(len(p['campos']) + bool(p['mov']) for p in bloco['pessoas'])
                          + sum(1 for v in bloco['vagas'] if v['chave']))

    carteiras = _previa_carteiras(dados, blocos, ctx)
    itens =[c for b in blocos for p in b['pessoas'] for c in p['campos']]
    movs = [p['mov'] for b in blocos for p in b['pessoas'] if p['mov']]
    vagas = [v for b in blocos for v in b['vagas'] if v.get('chave')]
    return {
        'blocos': blocos, 'carteiras': carteiras, 'pendencias': pendencias, 'origem': dados.get('origem', ''),
        'setores': ctx.setores,
        'totais': {
            'blocos': len(blocos), 'pessoas': sum(len(b['pessoas']) for b in blocos),
            'casadas': len(casados), 'campos': sum(1 for c in itens if c['campo'] != 'observacao'),
            'observacoes': sum(1 for c in itens if c['campo'] == 'observacao'), 'movimentacoes': len(movs),
            'vagas': len(vagas), 'carteiras': sum(1 for c in carteiras if c.get('chave')),
            'pendencias': len(pendencias), 'ausentes': sum(len(b['ausentes']) for b in blocos),
            'sem_setor': sum(1 for b in blocos if not b['setor']),
        },
    }


def _propor_campos(p, u, linha, ctx, propostos):
    hoje = ctx.hoje

    def propor(campo, rotulo, antes, depois, valor):
        if campo in propostos[u.id]:
            return
        propostos[u.id].add(campo)
        p['campos'].append({'chave': f'c:{linha["n"]}:{campo}', 'campo': campo, 'rotulo': rotulo,
                            'antes': antes or '—', 'depois': depois, 'valor': valor})

    bairro, cidade = _separar_cidade(linha['cidade'], ctx.cidades)
    if cidade and not _mesmo_lugar(cidade, u.city):
        propor('city', 'Cidade', u.city, cidade[:100], cidade[:100])
    if bairro and not _mesmo_lugar(bairro, u.neighborhood):
        propor('neighborhood', 'Bairro', u.neighborhood, bairro[:100], bairro[:100])

    if linha['login']:
        if linha['login'] != servicos._login(u.login_code):
            propor('login_code', 'Login', servicos._login(u.login_code), linha['login'], linha['login'][:20])
            outros = ctx.outro_com_login(linha['login'], u)
            if outros:
                p['avisos'].append(f'O login {linha["login"]} também está noutro cadastro (inativo).')
    elif linha['login_txt']:
        p['avisos'].append(f'Login na planilha: "{linha["login_txt"]}" (não é código; o cadastro não muda).')

    if linha['nascimento']:
        d = date.fromisoformat(linha['nascimento'])
        if not (date(1930, 1, 1) <= d <= hoje - timedelta(days=14 * 365)):
            p['avisos'].append(f'Nascimento {linha["nascimento_txt"]} não parece certo: ignorado.')
        elif d != u.birth_date:
            propor('birth_date', 'Nascimento', _fmt(u.birth_date), _fmt(d), d)
    elif linha['nascimento_txt']:
        p['avisos'].append(f'Nascimento "{linha["nascimento_txt"]}" não é data: ignorado.')

    if linha['contratacao']:
        d = date.fromisoformat(linha['contratacao'])
        if not (date(1990, 1, 1) <= d <= hoje + timedelta(days=120)):
            p['avisos'].append(f'Contratação {linha["contratacao_txt"]} não parece certa: ignorada.')
        elif d != u.admission_date:
            propor('admission_date', 'Contratação', _fmt(u.admission_date), _fmt(d), d)
    elif linha['contratacao_txt']:
        p['avisos'].append(f'Contratação "{linha["contratacao_txt"]}" não é data: ignorada.')

    if linha['telefone']:
        d = _digitos(linha['telefone'])
        if len(d) in (10, 11):
            if d != _digitos(u.phone):
                propor('phone', 'Telefone', u.phone, _telefone(d), _telefone(d))
        else:
            p['avisos'].append(f'Telefone "{linha["telefone"]}" ilegível: ignorado.')

    obs = linha['obs'][:500]
    if obs and _limpo(obs) != _limpo(ctx.obs.get(u.id, '')):
        propor('observacao', 'Observação do quadro', ctx.obs.get(u.id, ''), obs, obs)


def _vaga(linha, setor, ctx, usados, pendencias, onde) -> dict:
    n = linha['n']
    v = {'n': n, 'cargo_planilha': linha['cargo'], 'cargo': '', 'observacao': linha['vaga_obs'][:300], 'chave': '',
         'situacao': ''}
    cargo = _cargo_da_vaga(linha['cargo'], ctx)
    v['cargo'] = cargo
    if not setor:
        v['situacao'] = 'Sem loja escolhida: a vaga não abre.'
        pendencias.append({'tipo': 'vaga', 'onde': onde, 'n': n, 'nome': linha['nome'], 'motivo': v['situacao']})
    elif not cargo:
        v['situacao'] = (f'Cargo "{linha["cargo"]}" não reconhecido: a vaga não abre.' if linha['cargo']
                         else 'Vaga sem cargo na planilha: não abre.')
        pendencias.append({'tipo': 'vaga', 'onde': onde, 'n': n, 'nome': linha['nome'], 'motivo': v['situacao']})
    else:
        chave = _chave_vaga(cargo)
        if usados[setor.id][chave] < ctx.vagas[setor.id][chave]:
            usados[setor.id][chave] += 1
            v['situacao'] = 'Já está aberta no quadro.'
        else:
            v['chave'] = f'v:{n}'
    return v


def _previa_carteiras(dados, blocos, ctx) -> list[dict]:
    por_i = {b['i']: b for b in blocos}
    existentes = {_norm(c.nome): c for c in Carteira.objects.prefetch_related('lojas').select_related('coordenador')}
    dono = {}
    for c in existentes.values():
        for s in c.lojas.all():
            dono[s.id] = c
    saida = []
    for ct in dados.get('carteiras', []):
        lojas = []
        for i in ct['blocos']:
            b = por_i.get(i)
            if b and b['loja'] and b['setor'] not in lojas:
                lojas.append(b['setor'])
        c = existentes.get(_norm(ct['nome']))
        atual = c.coordenacao if c else ''
        mudar_coord = bool(ct['coord']) and not _mesma_pessoa(ct['coord'], atual)
        coord_user = _coordenador(ct['coord'], ctx) if mudar_coord else None
        ids_atuais = {s.id for s in c.lojas.all()} if c else set()
        entrar = [{'setor': s, 'de': dono[s.id].nome if s.id in dono and dono[s.id] != c else ''}
                  for s in lojas if s.id not in ids_atuais]
        fora = [s for s in (c.lojas.all() if c else []) if s.id not in {x.id for x in lojas}]
        item = {'i': ct['i'], 'titulo': ct['titulo'], 'nome': c.nome if c else ct['nome'], 'existente': c,
                'coord_antes': atual, 'coord_depois': (nome_completo(coord_user) if coord_user else ct['coord']),
                'coord_user': coord_user, 'mudar_coord': mudar_coord, 'entrar': entrar, 'fora': fora,
                'lojas': lojas, 'chave': ''}
        if (not c and lojas) or mudar_coord or entrar:
            item['chave'] = f'k:{ct["i"]}'
        saida.append(item)
    return saida


# ---------------------------------------------------------------------------
# Aplicar
# ---------------------------------------------------------------------------
def aplicar_importacao(dados: dict, escolhas: dict, marcados, autor, ip=None) -> dict:
    """Refaz a prévia e grava só o que veio marcado, numa transação. Devolve o resumo."""
    from users.auditoria import fotografar, registrar

    marcados = set(marcados or [])
    hoje = timezone.localdate()
    previa = montar_previa(dados, escolhas, hoje)
    feito = {'pessoas': 0, 'campos': 0, 'logs': 0, 'observacoes': 0, 'movimentacoes': 0, 'vagas': 0, 'carteiras': 0,
             'linhas': [], 'erros': [], 'nao_marcados': 0}
    with transaction.atomic():
        for b in previa['blocos']:
            for p in b['pessoas']:
                u = p['user']
                if not u:
                    continue
                nome = nome_completo(u)
                cad = [c for c in p['campos'] if c['chave'] in marcados and c['campo'] != 'observacao']
                obs = [c for c in p['campos'] if c['chave'] in marcados and c['campo'] == 'observacao']
                feito['nao_marcados'] += sum(1 for c in p['campos'] if c['chave'] not in marcados)
                mexeu = False
                if cad:
                    u = User.objects.select_for_update().get(pk=u.pk)
                    antes = fotografar(u)
                    for c in cad:
                        setattr(u, c['campo'], c['valor'])
                    u.save(update_fields=[c['campo'] for c in cad])
                    feito['logs'] += registrar(u, antes, autor, ip=ip)
                    feito['campos'] += len(cad)
                    feito['linhas'].append(f'{nome}: ' + '; '.join(f'{c["rotulo"]} {c["antes"]} → {c["depois"]}'
                                                                  for c in cad))
                    mexeu = True
                if obs:
                    ObservacaoHC.objects.update_or_create(colaborador=u, defaults={'texto': obs[0]['valor'],
                                                                                  'atualizado_por': autor})
                    feito['observacoes'] += 1
                    feito['linhas'].append(f'{nome}: observação "{obs[0]["valor"]}"')
                    mexeu = True
                m = p['mov']
                if m and m['chave'] in marcados:
                    try:
                        with transaction.atomic():
                            mov, _sim = servicos.registrar_movimentacao({
                                'colaborador': u.pk, 'data': hoje.isoformat(),
                                'novo_setor': m['novo_setor'].pk if m['novo_setor'] else '',
                                'novo_cargo': m['novo_cargo'], 'observacao': OBS_MOVIMENTACAO}, autor, ip=ip)
                        feito['movimentacoes'] += 1
                        feito['linhas'].append(f'Movimentação: {mov.resumo}')
                        mexeu = True
                    except ErroQuadro as e:
                        feito['erros'].append(f'{nome}: {e}')
                elif m:
                    feito['nao_marcados'] += 1
                feito['pessoas'] += int(mexeu)
            for v in b['vagas']:
                if not v['chave']:
                    continue
                if v['chave'] not in marcados:
                    feito['nao_marcados'] += 1
                    continue
                VagaHC.objects.create(setor=b['setor'], cargo=v['cargo'][:100], observacao=v['observacao'],
                                      criado_por=autor)
                feito['vagas'] += 1
                feito['linhas'].append(f'Vaga de {v["cargo"]} aberta na {b["setor"].name}'
                                       + (f' ({v["observacao"]})' if v['observacao'] else ''))
        for c in previa['carteiras']:
            if not c['chave']:
                continue
            if c['chave'] not in marcados:
                feito['nao_marcados'] += 1
                continue
            existente = c['existente']
            ids = sorted({s.id for s in (existente.lojas.all() if existente else [])} | {s.id for s in c['lojas']})
            if c['mudar_coord']:
                coordenador = c['coord_user'].pk if c['coord_user'] else None
                coordenador_nome = '' if c['coord_user'] else c['coord_depois']
            else:
                coordenador = existente.coordenador_id if existente else None
                coordenador_nome = existente.coordenador_nome if existente else ''
            ordem = existente.ordem if existente else (Carteira.objects.order_by('-ordem')
                                                       .values_list('ordem', flat=True).first() or 0) + 1
            carteira = servicos.salvar_carteira({'id': existente.pk if existente else None, 'nome': c['nome'],
                                                 'coordenador': coordenador, 'coordenador_nome': coordenador_nome,
                                                 'ordem': ordem, 'lojas': ids})
            feito['carteiras'] += 1
            feito['linhas'].append(f'Carteira {carteira.nome}: coordenação {carteira.coordenacao or "—"}; '
                                   + ', '.join(s.name for s in carteira.lojas.order_by('name')))
    feito['pendencias'] = previa['pendencias']
    feito['ausentes'] = [(b['setor'].name, a) for b in previa['blocos'] for a in b['ausentes']]
    return feito
