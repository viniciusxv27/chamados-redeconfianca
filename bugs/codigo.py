"""O código do portal como o Claude enxerga durante a análise de um bug.

Lê os arquivos do próprio servidor (é a versão que está rodando, onde o bug apareceu)
e guarda as edições numa camada em memória (``Rascunho``): nada é gravado na pasta do
portal. Os testes rodam numa cópia (``bugs/sandbox.py``) e a publicação vai direto para
o GitHub (``bugs/github.py``).

Regras de acesso — o relato vem de qualquer colaborador, então o texto dele não pode
virar caminho para segredo nem para mexer no que não é código da aplicação:
- só dentro da raiz do projeto, sem pastas pesadas/geradas (venv, media, .git...);
- segredos nunca são lidos (.env, chaves, certificados);
- edição só em código-fonte (.py, .html, .js, .css...), fora de migrações, settings,
  deploy, dependências e do próprio módulo de bugs.
"""
import difflib
import fnmatch
import os
import re
from pathlib import Path

from django.conf import settings

RAIZ = Path(settings.BASE_DIR).resolve()

# Pastas que não são código da aplicação (ou são enormes / geradas / de outro projeto).
PASTAS_FORA = {
    'venv', '.venv', 'env', '.git', 'node_modules', '__pycache__', 'media', 'staticfiles', 'logs',
    'mobile', 'agente_cameras', 'dist', 'build', '.claude', '.idea', '.vscode', 'jitsi',
}
# Segredos e binários: nunca são lidos.
ARQUIVOS_SECRETOS = (
    '.env', '.env.*', '*.pem', '*.key', '*.crt', '*.pfx', '*.p12', 'vapid*', '*.sqlite3', '*.db',
    'id_rsa*', '*.keystore', '*.jks', 'credentials*.json', '*service_account*.json', '*.so', '*.pyc',
)
TEXTO = {'.py', '.html', '.htm', '.js', '.css', '.txt', '.md', '.json', '.yml', '.yaml', '.sh', '.cfg',
         '.ini', '.toml', '.xml', '.svg', '.sql', '.csv'}
EDITAVEIS = {'.py', '.html', '.htm', '.js', '.css', '.txt', '.md', '.json', '.svg'}
# Não se mexe nestes nem por correção de bug: são do deploy, do banco ou da trava de aprovação.
EDICAO_PROIBIDA = (
    '*/migrations/*', 'redeconfianca/settings*.py', 'Dockerfile', 'docker-compose*', 'deploy*', '*.sh',
    'requirements*.txt', 'runtime.txt', 'manage.py', 'bugs/*', 'templates/bugs/*', '.github/*',
    'redeconfianca/wsgi.py', 'redeconfianca/asgi.py',
)
MAX_LEITURA = 400_000        # bytes de um arquivo
MAX_ARQUIVOS_ALTERADOS = 12
MAX_TAMANHO_NOVO = 300_000


class ErroCodigo(ValueError):
    """Pedido recusado — a mensagem volta para o Claude como resultado da ferramenta."""


def normalizar(caminho):
    """Caminho relativo à raiz ('tickets/views.py') ou ErroCodigo."""
    caminho = str(caminho or '').strip().replace('\\', '/')
    if caminho.startswith(str(RAIZ)):
        caminho = caminho[len(str(RAIZ)):]
    caminho = caminho.lstrip('/').removeprefix('./')
    if not caminho:
        return ''
    alvo = (RAIZ / caminho).resolve()
    try:
        rel = alvo.relative_to(RAIZ)
    except ValueError:
        raise ErroCodigo('Só arquivos dentro do projeto do portal.')
    partes = rel.parts
    if any(p in PASTAS_FORA or (p.startswith('.') and p not in ('.',)) for p in partes[:-1]):
        raise ErroCodigo(f'A pasta de "{rel}" não faz parte do código analisável.')
    if partes and partes[-1] in PASTAS_FORA:
        raise ErroCodigo(f'"{rel}" não faz parte do código analisável.')
    return rel.as_posix()


def secreto(rel):
    nome = rel.rsplit('/', 1)[-1].lower()
    return any(fnmatch.fnmatch(nome, p) for p in ARQUIVOS_SECRETOS)


def pode_editar(rel):
    if secreto(rel):
        return False, 'Arquivo de segredo: não se edita.'
    if Path(rel).suffix.lower() not in EDITAVEIS:
        return False, 'Só se edita código-fonte (.py, .html, .js, .css, .json, .md, .txt, .svg).'
    for padrao in EDICAO_PROIBIDA:
        if fnmatch.fnmatch(rel, padrao) or fnmatch.fnmatch('/' + rel, '*/' + padrao.lstrip('*/')):
            return False, (f'"{rel}" está fora do que a correção automática pode mudar (migrações, settings, deploy, '
                           'dependências e o próprio módulo de bugs). Se a correção depende dele, explique no '
                           'diagnóstico e conclua sem correção.')
    return True, ''


def _ler_disco(rel):
    alvo = RAIZ / rel
    if not alvo.is_file():
        return None
    if alvo.stat().st_size > MAX_LEITURA:
        raise ErroCodigo(f'"{rel}" é grande demais para ler inteiro ({alvo.stat().st_size // 1024} KB).')
    dados = alvo.read_bytes()
    if b'\x00' in dados[:4096]:
        raise ErroCodigo(f'"{rel}" é binário.')
    return dados.decode('utf-8', errors='replace')


class Rascunho:
    """Os arquivos como ficam com a correção (camada em memória sobre o disco)."""

    def __init__(self, alteracoes=None):
        # caminho → {'original': texto | None (arquivo novo), 'novo': texto, 'edicoes': [{de, para}]}
        self.arquivos = {}
        for a in alteracoes or []:
            self.arquivos[a['caminho']] = {'original': a.get('original'), 'novo': a['novo'],
                                           'edicoes': list(a.get('edicoes') or [])}
        self.versao = 0   # sobe a cada mudança: o teste aprovado precisa ser da versão final

    # ---------------------------------------------------------------- leitura
    def ler(self, caminho):
        rel = normalizar(caminho)
        if not rel:
            raise ErroCodigo('Informe o arquivo.')
        if secreto(rel):
            raise ErroCodigo('Esse arquivo guarda segredo e não é lido.')
        if rel in self.arquivos:
            return rel, self.arquivos[rel]['novo']
        if Path(rel).suffix.lower() not in TEXTO:
            raise ErroCodigo(f'"{rel}" não é arquivo de texto/código.')
        texto = _ler_disco(rel)
        if texto is None:
            raise ErroCodigo(f'Arquivo não encontrado: {rel}')
        return rel, texto

    def existe(self, rel):
        return rel in self.arquivos or (RAIZ / rel).is_file()

    # ---------------------------------------------------------------- escrita
    def editar(self, caminho, de, para):
        rel = normalizar(caminho)
        ok, motivo = pode_editar(rel)
        if not ok:
            raise ErroCodigo(motivo)
        if not de:
            raise ErroCodigo('Informe o trecho atual (exatamente como está no arquivo).')
        if de == para:
            raise ErroCodigo('O trecho novo é igual ao atual.')
        rel, atual = self.ler(rel)
        vezes = atual.count(de)
        if vezes == 0:
            raise ErroCodigo('O trecho atual não foi encontrado no arquivo (confira espaços e recuo; leia de novo).')
        if vezes > 1:
            raise ErroCodigo(f'O trecho aparece {vezes} vezes: inclua mais linhas ao redor para ficar único.')
        self._garantir_limite(rel)
        item = self.arquivos.setdefault(rel, {'original': atual, 'novo': atual, 'edicoes': []})
        item['novo'] = atual.replace(de, para, 1)
        item['edicoes'].append({'de': de, 'para': para})
        self._limpar_se_voltou(rel)
        self.versao += 1
        return rel

    def criar(self, caminho, conteudo):
        rel = normalizar(caminho)
        ok, motivo = pode_editar(rel)
        if not ok:
            raise ErroCodigo(motivo)
        if self.existe(rel) and (rel not in self.arquivos or self.arquivos[rel]['original'] is not None):
            raise ErroCodigo('O arquivo já existe: use editar_arquivo.')
        if len(conteudo or '') > MAX_TAMANHO_NOVO:
            raise ErroCodigo('Arquivo novo grande demais.')
        self._garantir_limite(rel)
        self.arquivos[rel] = {'original': None, 'novo': conteudo or '', 'edicoes': []}
        self.versao += 1
        return rel

    def desfazer(self, caminho):
        rel = normalizar(caminho)
        if self.arquivos.pop(rel, None) is None:
            raise ErroCodigo('Esse arquivo não tem alteração.')
        self.versao += 1
        return rel

    def _garantir_limite(self, rel):
        if rel not in self.arquivos and len(self.arquivos) >= MAX_ARQUIVOS_ALTERADOS:
            raise ErroCodigo(f'Correção grande demais (mais de {MAX_ARQUIVOS_ALTERADOS} arquivos).')

    def _limpar_se_voltou(self, rel):
        item = self.arquivos.get(rel)
        if item and item['original'] is not None and item['novo'] == item['original']:
            self.arquivos.pop(rel)

    # ---------------------------------------------------------------- saída
    def alteracoes(self):
        return [{'caminho': rel, 'tipo': 'criar' if a['original'] is None else 'editar',
                 'original': a['original'], 'novo': a['novo'], 'edicoes': a['edicoes']}
                for rel, a in sorted(self.arquivos.items())]

    def diff(self):
        partes = []
        for rel, a in sorted(self.arquivos.items()):
            antes = (a['original'] or '').splitlines(keepends=True)
            depois = a['novo'].splitlines(keepends=True)
            partes.extend(difflib.unified_diff(
                antes, depois, fromfile='/dev/null' if a['original'] is None else f'a/{rel}', tofile=f'b/{rel}', n=3))
        texto = ''.join(p if p.endswith('\n') else p + '\n' for p in partes)
        return texto


# -------------------------------------------------------------------- navegação
def listar(pasta=''):
    rel = normalizar(pasta)
    alvo = RAIZ / rel if rel else RAIZ
    if not alvo.is_dir():
        raise ErroCodigo(f'Pasta não encontrada: {rel or "/"}')
    pastas, arquivos = [], []
    for item in sorted(alvo.iterdir(), key=lambda p: p.name.lower()):
        if item.name in PASTAS_FORA or item.name.startswith('.'):
            continue
        if item.is_dir():
            pastas.append(item.name + '/')
        elif not secreto(item.name):
            arquivos.append(f'{item.name} ({max(1, item.stat().st_size // 1024)} KB)')
    return {'pasta': rel or '/', 'pastas': pastas[:300], 'arquivos': arquivos[:500]}


def _arquivos_de(pasta, filtro):
    base = RAIZ / pasta if pasta else RAIZ
    for raiz, dirs, nomes in os.walk(base):
        dirs[:] = [d for d in dirs if d not in PASTAS_FORA and not d.startswith('.')]
        for nome in nomes:
            if secreto(nome) or Path(nome).suffix.lower() not in TEXTO:
                continue
            if filtro and not fnmatch.fnmatch(nome, filtro):
                continue
            yield Path(raiz) / nome


def buscar(padrao, pasta='', filtro='', rascunho=None, limite=80):
    """grep: linhas que casam com a expressão regular (sem diferenciar maiúsculas)."""
    try:
        regex = re.compile(padrao, re.IGNORECASE)
    except re.error as exc:
        raise ErroCodigo(f'Expressão regular inválida: {exc}')
    rel_pasta = normalizar(pasta)
    achados, arquivos_com = [], 0
    for arq in _arquivos_de(rel_pasta, filtro):
        rel = arq.relative_to(RAIZ).as_posix()
        try:
            if rascunho and rel in rascunho.arquivos:
                texto = rascunho.arquivos[rel]['novo']
            else:
                if arq.stat().st_size > MAX_LEITURA:
                    continue
                texto = arq.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        teve = False
        for n, linha in enumerate(texto.splitlines(), 1):
            if regex.search(linha):
                teve = True
                if len(achados) < limite:
                    achados.append(f'{rel}:{n}: {linha.strip()[:220]}')
        arquivos_com += teve
    saida = {'resultados': achados, 'arquivos_com_resultado': arquivos_com}
    if len(achados) >= limite:
        saida['observacao'] = f'Mostrando só os {limite} primeiros: refine a busca (pasta ou filtro como "*.py").'
    return saida


def trecho(texto, de=1, ate=None):
    linhas = texto.splitlines()
    total = len(linhas)
    de = max(1, int(de or 1))
    ate = min(total, int(ate or de + 399))
    if ate - de > 800:
        ate = de + 800
    corpo = '\n'.join(f'{n:>5}\t{linhas[n - 1]}' for n in range(de, ate + 1)) if total else ''
    return {'linhas': f'{de}-{ate} de {total}', 'conteudo': corpo,
            **({'continua': f'Há mais: peça de {ate + 1} em diante.'} if ate < total else {})}


def rota_da_url(url):
    """O que o Django executa naquele endereço: view, arquivo e templates citados nela."""
    import inspect
    from urllib.parse import urlsplit

    from django.urls import Resolver404, resolve

    partes = urlsplit(str(url or '').strip())
    caminho = partes.path or '/'
    try:
        rota = resolve(caminho)
    except Resolver404:
        if not caminho.endswith('/'):
            try:
                rota = resolve(caminho + '/')
                caminho += '/'
            except Resolver404:
                return {'erro': f'Nenhuma rota do portal atende {caminho}.'}
        else:
            return {'erro': f'Nenhuma rota do portal atende {caminho}.'}
    func = rota.func
    alvo = getattr(func, 'view_class', None) or func
    while hasattr(alvo, '__wrapped__'):
        alvo = alvo.__wrapped__
    saida = {'caminho': caminho, 'query': partes.query, 'nome_da_rota': rota.view_name,
             'argumentos': {**rota.kwargs, **({'args': list(rota.args)} if rota.args else {})},
             'view': f'{getattr(alvo, "__module__", "?")}.{getattr(alvo, "__qualname__", getattr(alvo, "__name__", "?"))}'}
    try:
        arquivo = Path(inspect.getsourcefile(alvo)).resolve()
        linhas, inicio = inspect.getsourcelines(alvo)
        saida['arquivo'] = arquivo.relative_to(RAIZ).as_posix()
        saida['linhas'] = f'{inicio}-{inicio + len(linhas) - 1}'
        fonte = ''.join(linhas)
        saida['templates_citados'] = sorted(set(re.findall(r"['\"]([\w./-]+\.html)['\"]", fonte)))
    except (TypeError, OSError, ValueError):
        pass
    return saida
