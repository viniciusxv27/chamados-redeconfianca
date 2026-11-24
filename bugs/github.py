"""Publica a correção aprovada no GitHub: um commit na branch configurada (pela API).

Pela API, e não pelo ``git`` da máquina: o contêiner de produção não tem o repositório
nem o git. O commit sai direto em cima da ponta atual da branch (commit + push num passo só).

As edições são reaplicadas sobre o arquivo que está no GitHub agora: se alguém mudou o
mesmo arquivo depois da análise, a edição ainda entra desde que o trecho antigo continue
lá, igualzinho e uma vez só; se não continuar, a publicação para com "conflito" e o
SUPERADMIN pede uma nova análise.
"""
import base64
import re
from pathlib import Path

import requests
from django.conf import settings

API = 'https://api.github.com'
TEMPO = 20


class ErroPublicacao(Exception):
    pass


def _repo_do_git_local():
    """dono/nome do remoto 'origin' quando o portal roda de um clone (desenvolvimento)."""
    try:
        texto = (Path(settings.BASE_DIR) / '.git' / 'config').read_text(encoding='utf-8')
    except OSError:
        return ''
    m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', texto)
    if not m:
        return ''
    m2 = re.search(r'github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?$', m.group(1))
    return m2.group(1) if m2 else ''


def configuracao():
    """(token, repositorio, branch) — da tela de configuração, com reserva no ambiente."""
    import os

    from .models import ConfigBugs
    cfg = ConfigBugs.objects.filter(pk=1).first()
    token = (cfg.get_token() if cfg else '') or os.environ.get('BUGS_GITHUB_TOKEN', '') or os.environ.get('GITHUB_TOKEN', '')
    repo = (cfg.repositorio if cfg and cfg.repositorio else '') or os.environ.get('BUGS_GITHUB_REPO', '') or _repo_do_git_local()
    branch = (cfg.branch if cfg and cfg.branch else '') or os.environ.get('BUGS_GITHUB_BRANCH', '') or 'main'
    return token.strip(), repo.strip().strip('/'), branch.strip()


class Cliente:
    def __init__(self, token, repo):
        if not token:
            raise ErroPublicacao('Sem token do GitHub: configure em /bugs/configurar/.')
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo or ''):
            raise ErroPublicacao('Repositório do GitHub não configurado (formato dono/nome).')
        self.repo = repo
        self.sessao = requests.Session()
        self.sessao.headers.update({
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'portal-bugs',
        })

    def _url(self, caminho):
        return f'{API}/repos/{self.repo}/{caminho.lstrip("/")}'

    def pedir(self, metodo, caminho, ok=(200, 201), **kw):
        try:
            resp = self.sessao.request(metodo, self._url(caminho), timeout=TEMPO, **kw)
        except requests.RequestException as exc:
            raise ErroPublicacao(f'GitHub fora do ar ou sem rede: {exc}')
        if resp.status_code not in ok:
            try:
                msg = resp.json().get('message', '')
            except ValueError:
                msg = resp.text[:200]
            if resp.status_code == 401:
                msg = 'token inválido ou expirado'
            elif resp.status_code == 403 and 'rate limit' not in msg.lower():
                msg = f'o token não tem permissão de escrita neste repositório ({msg})'
            elif resp.status_code == 404:
                msg = f'não encontrado ({msg}) — confira o repositório, a branch e se o token enxerga o repo'
            erro = ErroPublicacao(f'GitHub respondeu {resp.status_code}: {msg}')
            erro.status = resp.status_code
            raise erro
        return resp

    def testar(self, branch):
        repo = self.pedir('GET', '').json()
        self.pedir('GET', f'git/ref/heads/{branch}')
        perm = repo.get('permissions') or {}
        if perm and not perm.get('push'):
            raise ErroPublicacao('O token enxerga o repositório, mas não tem permissão de push.')
        return repo.get('full_name', self.repo)

    def ponta(self, branch):
        sha = self.pedir('GET', f'git/ref/heads/{branch}').json()['object']['sha']
        arvore = self.pedir('GET', f'git/commits/{sha}').json()['tree']['sha']
        return sha, arvore

    def conteudo(self, caminho, ref):
        resp = self.pedir('GET', f'contents/{caminho}', ok=(200, 404), params={'ref': ref})
        if resp.status_code == 404:
            return None
        dados = resp.json()
        if isinstance(dados, list) or dados.get('type') != 'file':
            raise ErroPublicacao(f'"{caminho}" no GitHub não é um arquivo.')
        if dados.get('encoding') == 'base64' and dados.get('content') is not None and dados.get('size', 0) <= 1_000_000:
            return base64.b64decode(dados['content']).decode('utf-8')
        blob = self.pedir('GET', f'git/blobs/{dados["sha"]}').json()
        return base64.b64decode(blob['content']).decode('utf-8')


def _aplicar(alteracao, remoto):
    """Texto final do arquivo, partindo do que está no GitHub agora."""
    caminho = alteracao['caminho']
    if alteracao['tipo'] == 'criar':
        if remoto is not None and remoto != alteracao['novo']:
            raise ErroPublicacao(f'Conflito: "{caminho}" já existe no GitHub com outro conteúdo.')
        return alteracao['novo']
    if remoto is None:
        raise ErroPublicacao(f'Conflito: "{caminho}" não existe mais no GitHub.')
    if remoto == alteracao.get('original'):
        return alteracao['novo']
    texto = remoto
    for ed in alteracao.get('edicoes') or []:
        vezes = texto.count(ed['de'])
        if vezes != 1:
            raise ErroPublicacao(
                f'Conflito: "{caminho}" mudou no GitHub depois da análise e o trecho a corrigir '
                f'{"não está mais lá" if vezes == 0 else "ficou repetido"}. Peça uma nova análise.')
        texto = texto.replace(ed['de'], ed['para'], 1)
    return texto


def publicar(alteracoes, mensagem):
    """Commit na ponta da branch. Devolve (sha, url do commit)."""
    token, repo, branch = configuracao()
    cli = Cliente(token, repo)
    for tentativa in range(3):
        sha_ponta, arvore_base = cli.ponta(branch)
        entradas = []
        for alt in alteracoes:
            final = _aplicar(alt, cli.conteudo(alt['caminho'], sha_ponta))
            entradas.append({'path': alt['caminho'], 'mode': '100644', 'type': 'blob', 'content': final})
        arvore = cli.pedir('POST', 'git/trees', json={'base_tree': arvore_base, 'tree': entradas}).json()['sha']
        commit = cli.pedir('POST', 'git/commits', json={
            'message': mensagem, 'tree': arvore, 'parents': [sha_ponta]}).json()
        try:
            cli.pedir('PATCH', f'git/refs/heads/{branch}', json={'sha': commit['sha'], 'force': False})
        except ErroPublicacao as exc:
            # Alguém publicou entre a leitura e o push: refaz em cima da ponta nova.
            if getattr(exc, 'status', None) == 422 and tentativa < 2:
                continue
            raise
        return commit['sha'], f'https://github.com/{repo}/commit/{commit["sha"]}'
    raise ErroPublicacao('A branch mudou várias vezes seguidas durante a publicação; tente de novo.')
