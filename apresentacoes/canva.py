"""Integração com o Canva Connect: conectar a conta (OAuth 2.0 + PKCE) e importar o .pptx.

Fluxo: `url_de_autorizacao` guarda o `state` e o verificador PKCE (cifrado) na `ConexaoCanva` e
manda a pessoa ao Canva; na volta, `concluir_autorizacao` confere o `state`, troca o código pelos
tokens e grava tudo cifrado. Para abrir a apresentação no Canva, a tarefa em segundo plano gera o
.pptx, chama `iniciar_importacao` e espera com `aguardar_importacao`.

Conferido na documentação do Canva Connect (canva.dev):
- escopos: POST/GET /v1/imports pedem `design:content:write`; GET /v1/designs/{id} pede
  `design:meta:read` (o link de edição do design vale 30 dias e é renovado por ali). Não usamos
  assets nem perfil — a integração no Developer Portal precisa ter só esses dois escopos ligados;
- o refresh token é de USO ÚNICO: cada renovação devolve outro, gravado sob select_for_update
  para duas requisições ao mesmo tempo não gastarem o mesmo token;
- o token de acesso vale ~4 h (`expires_in`); renovamos quando faltam menos de 5 minutos.

Exceções: `CanvaErro` (base) e as específicas `CanvaNaoConfigurado` e `CanvaNaoConectado`. A
mensagem de todas é para a pessoa ler — nunca leva token, código ou segredo (nem no log).
Capture as específicas antes de `CanvaErro`.
"""
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from datetime import timedelta
from urllib.parse import quote, urlencode, urlsplit

import httpx
from django.db import transaction
from django.utils import timezone

from assistente.crypto import cifrar, decifrar

from .models import ConexaoCanva, ConfiguracaoApresentacoes

logger = logging.getLogger(__name__)

URL_AUTORIZACAO = 'https://www.canva.com/api/oauth/authorize'
URL_API = 'https://api.canva.com/rest/v1'
URL_TOKEN = f'{URL_API}/oauth/token'
URL_REVOGAR = f'{URL_API}/oauth/revoke'
URL_IMPORTACOES = f'{URL_API}/imports'
URL_DESIGNS = f'{URL_API}/designs'

ESCOPOS = 'design:content:write design:meta:read'
MIME_PPTX = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
TITULO_MAXIMO = 50                                   # caracteres, antes do base64
MARGEM_RENOVACAO = timedelta(minutes=5)
VALIDADE_PADRAO = 4 * 3600                           # se o Canva não mandar expires_in

TEMPO_LIMITE = httpx.Timeout(20.0, connect=10.0)
TEMPO_LIMITE_ENVIO = httpx.Timeout(180.0, connect=10.0)   # upload do .pptx (pode ter vídeo)

MSG_NAO_CONFIGURADO = ('O Canva ainda não foi configurado no portal. Peça ao SUPERADMIN para cadastrar '
                       'o Client ID e o segredo da integração.')
MSG_NAO_CONECTADO = 'Conecte sua conta do Canva para continuar.'
MSG_CONEXAO_EXPIRADA = 'A conexão com o Canva expirou. Conecte sua conta de novo.'
MSG_REDE = 'Não foi possível falar com o Canva agora. Tente de novo em instantes.'
MSG_INESPERADA = 'O Canva respondeu de um jeito inesperado. Tente de novo.'

MENSAGENS_FALHA_IMPORTACAO = {
    'invalid_file': 'O Canva não conseguiu ler o arquivo da apresentação.',
    'duplicate_import': 'Essa apresentação acabou de ser enviada ao Canva. Aguarde e confira seus designs.',
    'design_creation_throttled': 'O Canva limitou a criação de designs agora. Tente de novo mais tarde.',
    'design_import_throttled': 'O Canva limitou as importações agora. Tente de novo mais tarde.',
    'fetch_failed': 'O Canva não conseguiu receber o arquivo. Tente de novo.',
    'internal_error': 'O Canva teve um problema ao importar. Tente de novo em alguns minutos.',
}

_RE_ID = re.compile(r'^[A-Za-z0-9_-]{1,128}$')


class CanvaErro(Exception):
    """Falha ao falar com o Canva. A mensagem é amigável e não carrega token nem segredo."""


class CanvaNaoConfigurado(CanvaErro):
    """O SUPERADMIN ainda não cadastrou o Client ID e o segredo da integração."""


class CanvaNaoConectado(CanvaErro):
    """A pessoa não conectou a conta do Canva, ou a conexão expirou/foi revogada."""


class _TokenRecusado(CanvaErro):
    """O Canva recusou o código ou o refresh token (invalid_grant e afins)."""


# ----------------------------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------------------------

def _cliente(tempo_limite=TEMPO_LIMITE):
    # Ponto único de saída para a rede: os testes trocam por um httpx.MockTransport.
    return httpx.Client(timeout=tempo_limite, follow_redirects=False)


def _caminho(url):
    return urlsplit(url).path


def _requisitar(metodo, url, *, tempo_limite=TEMPO_LIMITE, **kwargs):
    try:
        with _cliente(tempo_limite) as cliente:
            return cliente.request(metodo, url, **kwargs)
    except httpx.HTTPError as exc:
        # Só o tipo da falha: a exceção do httpx carrega a requisição (e os cabeçalhos com token).
        logger.warning('Canva: falha de rede em %s %s (%s)', metodo, _caminho(url), exc.__class__.__name__)
        raise CanvaErro(MSG_REDE) from None


def _json(resposta):
    try:
        dados = resposta.json()
    except ValueError:
        return {}
    return dados if isinstance(dados, dict) else {}


def _codigo_erro(resposta):
    dados = _json(resposta)
    return re.sub(r'[^A-Za-z0-9_.:-]', '', str(dados.get('code') or dados.get('error') or ''))[:60]


def _basic(client_id, segredo):
    return 'Basic ' + base64.b64encode(f'{client_id}:{segredo}'.encode('utf-8')).decode('ascii')


def _decifrar(valor):
    if not valor:
        return None
    try:
        return decifrar(valor)
    except Exception:                                   # noqa: BLE001 — SECRET_KEY trocada
        return None


# ----------------------------------------------------------------------------------------------
# configuração e tokens
# ----------------------------------------------------------------------------------------------

def _configuracao():
    cfg = ConfiguracaoApresentacoes.get()
    client_id = (cfg.canva_client_id or '').strip()
    return client_id, (cfg.get_canva_secret() if client_id else '')


def configurado() -> bool:
    """Client ID e segredo cadastrados (e o segredo decifra com o SECRET_KEY atual)."""
    client_id, segredo = _configuracao()
    return bool(client_id and segredo)


def _credenciais():
    client_id, segredo = _configuracao()
    if not (client_id and segredo):
        raise CanvaNaoConfigurado(MSG_NAO_CONFIGURADO)
    return client_id, segredo


def _pedir_token(client_id, segredo, campos):
    resposta = _requisitar('POST', URL_TOKEN, data=campos, headers={
        'Authorization': _basic(client_id, segredo),
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
    })
    if resposta.status_code == 200:
        dados = _json(resposta)
        if dados.get('access_token'):
            return dados
        logger.warning('Canva: resposta de token sem access_token (%s)', campos.get('grant_type'))
        raise CanvaErro(MSG_INESPERADA)
    codigo = _codigo_erro(resposta)
    logger.warning('Canva: pedido de token recusado (%s, HTTP %s, código %s)',
                   campos.get('grant_type'), resposta.status_code, codigo or '-')
    if resposta.status_code == 429:
        raise CanvaErro('O Canva pediu para esperar um pouco. Tente de novo em um minuto.')
    if resposta.status_code >= 500:
        raise CanvaErro('O Canva está instável agora. Tente de novo em alguns minutos.')
    if resposta.status_code == 401 or codigo == 'invalid_client':
        raise CanvaErro('O Canva recusou as credenciais da integração cadastradas no portal. Peça ao SUPERADMIN '
                        'para conferir o Client ID e o segredo.')
    raise _TokenRecusado(MSG_CONEXAO_EXPIRADA)


def _gravar_tokens(conexao, dados):
    conexao.access_token_cifrado = cifrar(str(dados['access_token']))
    if dados.get('refresh_token'):
        conexao.refresh_token_cifrado = cifrar(str(dados['refresh_token']))
    try:
        validade = int(dados.get('expires_in') or VALIDADE_PADRAO)
    except (TypeError, ValueError):
        validade = VALIDADE_PADRAO
    conexao.expira_em = timezone.now() + timedelta(seconds=max(validade, 60))
    if dados.get('scope'):
        conexao.escopos = str(dados['scope'])[:300]
    conexao.save(update_fields=['access_token_cifrado', 'refresh_token_cifrado', 'expira_em', 'escopos',
                                'atualizado_em'])


def _limpar_tokens(conexao):
    conexao.access_token_cifrado = ''
    conexao.refresh_token_cifrado = ''
    conexao.expira_em = None
    conexao.save(update_fields=['access_token_cifrado', 'refresh_token_cifrado', 'expira_em', 'atualizado_em'])


def _fresco(conexao):
    return bool(conexao.access_token_cifrado and conexao.expira_em
                and conexao.expira_em - timezone.now() > MARGEM_RENOVACAO)


def _volta_segura(volta):
    volta = str(volta or '').strip()
    if (not volta.startswith('/') or volta.startswith('//') or '\\' in volta or len(volta) > 300
            or re.search(r'[\x00-\x20\x7f]', volta)):
        return ''
    return volta


# ----------------------------------------------------------------------------------------------
# OAuth
# ----------------------------------------------------------------------------------------------

def desafio_pkce(verificador):
    """code_challenge S256: SHA-256 do verificador em base64url sem '='."""
    return base64.urlsafe_b64encode(hashlib.sha256(verificador.encode('ascii')).digest()).rstrip(b'=').decode('ascii')


def url_de_autorizacao(user, redirect_uri, volta='') -> str:
    """Prepara o PKCE da pessoa e devolve a URL do Canva para ela autorizar o portal."""
    client_id, _segredo = _credenciais()
    verificador = secrets.token_urlsafe(72)             # 96 caracteres de [A-Za-z0-9_-] (limite: 43–128)
    estado = secrets.token_urlsafe(32)
    with transaction.atomic():
        conexao, _criada = ConexaoCanva.objects.select_for_update().get_or_create(user=user)
        conexao.estado = estado
        conexao.verificador_cifrado = cifrar(verificador)
        conexao.volta = _volta_segura(volta)
        conexao.save(update_fields=['estado', 'verificador_cifrado', 'volta', 'atualizado_em'])
    parametros = {
        'code_challenge': desafio_pkce(verificador),
        'code_challenge_method': 'S256',
        'scope': ESCOPOS,
        'response_type': 'code',
        'client_id': client_id,
        'state': estado,
        'redirect_uri': redirect_uri,
    }
    return f'{URL_AUTORIZACAO}?{urlencode(parametros, quote_via=quote)}'


def concluir_autorizacao(user, code, state, redirect_uri) -> ConexaoCanva:
    """Volta do Canva: confere o state, troca o código pelos tokens e grava tudo cifrado."""
    client_id, segredo = _credenciais()
    state = str(state or '')
    with transaction.atomic():
        conexao = ConexaoCanva.objects.select_for_update().filter(user=user).first()
        if (conexao is None or not conexao.estado or not state
                or not hmac.compare_digest(conexao.estado.encode('utf-8'), state.encode('utf-8'))):
            logger.warning('Canva: retorno de autorização com state que não confere (usuário %s)', user.pk)
            raise CanvaErro('Não foi possível confirmar a conexão com o Canva (o link expirou ou foi aberto em '
                            'outra aba). Tente conectar de novo.')
        verificador = _decifrar(conexao.verificador_cifrado)
        # State e verificador valem uma vez só: somem antes da troca, mesmo que ela falhe.
        conexao.estado = ''
        conexao.verificador_cifrado = ''
        conexao.save(update_fields=['estado', 'verificador_cifrado', 'atualizado_em'])
    if not verificador:
        raise CanvaErro('A autorização do Canva expirou. Tente conectar de novo.')
    code = str(code or '').strip()
    if not code or len(code) > 4000:
        raise CanvaErro('O Canva não devolveu a autorização. Tente conectar de novo.')
    try:
        dados = _pedir_token(client_id, segredo, {
            'grant_type': 'authorization_code', 'code': code, 'code_verifier': verificador,
            'redirect_uri': redirect_uri,
        })
    except _TokenRecusado:
        raise CanvaErro('O Canva não aceitou a autorização (ela pode ter expirado). Tente conectar de novo.') from None
    with transaction.atomic():
        conexao = ConexaoCanva.objects.select_for_update().get(pk=conexao.pk)
        _gravar_tokens(conexao, dados)
    return conexao


def _renovar(user, cifrado_visto):
    """Troca o refresh token (uso único) por um par novo, com a linha travada."""
    client_id, segredo = _credenciais()
    with transaction.atomic():
        conexao = ConexaoCanva.objects.select_for_update().filter(user=user).first()
        if conexao is None or not conexao.refresh_token_cifrado:
            raise CanvaNaoConectado(MSG_CONEXAO_EXPIRADA if conexao is not None and conexao.conectado
                                    else MSG_NAO_CONECTADO)
        if _fresco(conexao) and conexao.access_token_cifrado != cifrado_visto:
            # Outra requisição renovou enquanto esta esperava a trava: aproveita o token novo.
            token = _decifrar(conexao.access_token_cifrado)
            if token:
                return token, conexao.access_token_cifrado
        refresh = _decifrar(conexao.refresh_token_cifrado)
        if refresh:
            try:
                dados = _pedir_token(client_id, segredo, {'grant_type': 'refresh_token', 'refresh_token': refresh})
            except _TokenRecusado:
                dados = None
            if dados:
                _gravar_tokens(conexao, dados)
                return str(dados['access_token']), conexao.access_token_cifrado
        # Refresh recusado (ou ilegível): a conexão morreu, a pessoa precisa conectar de novo.
        logger.info('Canva: conexão do usuário %s descartada (refresh recusado)', user.pk)
        _limpar_tokens(conexao)
    raise CanvaNaoConectado(MSG_CONEXAO_EXPIRADA)


def _token(user):
    _credenciais()
    conexao = ConexaoCanva.objects.filter(user=user).first()
    if conexao is None or not conexao.conectado:
        raise CanvaNaoConectado(MSG_NAO_CONECTADO)
    if _fresco(conexao):
        token = _decifrar(conexao.access_token_cifrado)
        if token:
            return token, conexao.access_token_cifrado
    return _renovar(user, conexao.access_token_cifrado)


def token_de_acesso(user) -> str:
    """Token de acesso válido da pessoa (renova sozinho quando faltam menos de 5 minutos)."""
    return _token(user)[0]


def desconectar(user) -> bool:
    """Revoga no Canva (melhor esforço) e apaga a conexão do portal. True se havia conexão."""
    conexao = ConexaoCanva.objects.filter(user=user).first()
    if conexao is None:
        return False
    token = _decifrar(conexao.refresh_token_cifrado) or _decifrar(conexao.access_token_cifrado)
    client_id, segredo = _configuracao()
    if token and client_id and segredo:
        try:
            resposta = _requisitar('POST', URL_REVOGAR, data={'token': token}, headers={
                'Authorization': _basic(client_id, segredo),
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json',
            })
            if resposta.status_code != 200:
                logger.info('Canva: revogação respondeu HTTP %s (%s)', resposta.status_code,
                            _codigo_erro(resposta) or '-')
        except CanvaErro:
            pass                                        # sem rede: a conexão sai do portal do mesmo jeito
    conexao.delete()
    return True


# ----------------------------------------------------------------------------------------------
# API (importação e design)
# ----------------------------------------------------------------------------------------------

def _chamar_api(user, metodo, url, *, headers=None, tempo_limite=TEMPO_LIMITE, **kwargs):
    token, cifrado = _token(user)
    cabecalhos = {'Accept': 'application/json', **(headers or {})}
    resposta = _requisitar(metodo, url, tempo_limite=tempo_limite,
                           headers={**cabecalhos, 'Authorization': f'Bearer {token}'}, **kwargs)
    if resposta.status_code != 401:
        return resposta
    logger.info('Canva: token recusado (401) em %s %s; renovando uma vez', metodo, _caminho(url))
    token, _cifrado = _renovar(user, cifrado)
    resposta = _requisitar(metodo, url, tempo_limite=tempo_limite,
                           headers={**cabecalhos, 'Authorization': f'Bearer {token}'}, **kwargs)
    if resposta.status_code == 401:
        with transaction.atomic():
            conexao = ConexaoCanva.objects.select_for_update().filter(user=user).first()
            if conexao is not None:
                _limpar_tokens(conexao)
        raise CanvaNaoConectado(MSG_CONEXAO_EXPIRADA)
    return resposta


def _erro_api(resposta, acao):
    status, codigo = resposta.status_code, _codigo_erro(resposta)
    logger.warning('Canva: %s falhou (HTTP %s, código %s)', acao, status, codigo or '-')
    if status == 403:
        return CanvaErro('O Canva não autorizou a operação. Confira se a integração tem o escopo '
                         '"design:content:write" e conecte sua conta de novo.')
    if status == 404:
        return CanvaErro('O Canva não encontrou o que foi pedido (a importação ou o design pode ter sido removido).')
    if status == 413:
        return CanvaErro('A apresentação ficou grande demais para o Canva. Tente sem vídeos pesados.')
    if status == 429:
        return CanvaErro('O Canva está limitando os envios agora. Espere um minuto e tente de novo.')
    if status >= 500:
        return CanvaErro('O Canva está instável agora. Tente de novo em alguns minutos.')
    return CanvaErro('O Canva recusou o pedido. Tente de novo; se continuar, baixe o PowerPoint e importe no Canva.')


def _job(resposta, acao):
    if not 200 <= resposta.status_code < 300:
        raise _erro_api(resposta, acao)
    job = _json(resposta).get('job')
    if not isinstance(job, dict) or not job.get('id'):
        logger.warning('Canva: %s sem job na resposta', acao)
        raise CanvaErro(MSG_INESPERADA)
    return job


def titulo_do_design(titulo):
    """Título aceito pelo Canva: sem controle, até 50 caracteres (contados em UTF-16, como o Canva)."""
    texto = re.sub(r'\s+', ' ', re.sub(r'[\x00-\x1f\x7f]', ' ', str(titulo or ''))).strip()
    while len(texto.encode('utf-16-le')) // 2 > TITULO_MAXIMO:
        texto = texto[:-1]
    return texto.rstrip() or 'Apresentação'


def iniciar_importacao(user, titulo, conteudo: bytes) -> dict:
    """Envia o .pptx ao Canva e devolve o job ({'id', 'status', ...})."""
    if not conteudo:
        raise CanvaErro('O arquivo da apresentação saiu vazio. Tente exportar de novo.')
    metadados = {
        'title_base64': base64.b64encode(titulo_do_design(titulo).encode('utf-8')).decode('ascii'),
        'mime_type': MIME_PPTX,
    }
    resposta = _chamar_api(user, 'POST', URL_IMPORTACOES, tempo_limite=TEMPO_LIMITE_ENVIO, content=bytes(conteudo),
                           headers={'Content-Type': 'application/octet-stream',
                                    'Import-Metadata': json.dumps(metadados, separators=(',', ':'))})
    return _job(resposta, 'importação')


def consultar_importacao(user, job_id) -> dict:
    """Situação do job de importação: status in_progress | success | failed."""
    job_id = str(job_id or '').strip()
    if not _RE_ID.match(job_id):
        raise CanvaErro('Importação do Canva inválida.')
    resposta = _chamar_api(user, 'GET', f'{URL_IMPORTACOES}/{quote(job_id, safe="")}')
    return _job(resposta, 'consulta da importação')


def aguardar_importacao(user, job_id, tentativas=90, espera=2.0, dormir=time.sleep) -> dict:
    """Consulta até o job terminar. Devolve o job em `success`; `failed` ou demora demais → CanvaErro.

    Feita para a tarefa em segundo plano (90 × 2 s ≈ 3 min por padrão).
    """
    tentativas = max(int(tentativas or 1), 1)
    for tentativa in range(tentativas):
        job = consultar_importacao(user, job_id)
        status = str(job.get('status') or '').lower()
        if status == 'success':
            return job
        if status == 'failed':
            erro = job.get('error') if isinstance(job.get('error'), dict) else {}
            codigo = str(erro.get('code') or '')
            logger.warning('Canva: importação %s falhou (código %s)', job_id, codigo or '-')
            raise CanvaErro(MENSAGENS_FALHA_IMPORTACAO.get(
                codigo, 'O Canva não conseguiu importar a apresentação. Tente de novo.'))
        if tentativa < tentativas - 1:
            dormir(espera)
    raise CanvaErro('O Canva ainda está importando a apresentação. Tente de novo em alguns minutos.')


def obter_design(user, design_id) -> dict:
    """Metadados do design (título e links edit_url/view_url, que valem 30 dias). Pede design:meta:read."""
    design_id = str(design_id or '').strip()
    if not _RE_ID.match(design_id):
        raise CanvaErro('Design do Canva inválido.')
    resposta = _chamar_api(user, 'GET', f'{URL_DESIGNS}/{quote(design_id, safe="")}')
    if not 200 <= resposta.status_code < 300:
        raise _erro_api(resposta, 'consulta do design')
    design = _json(resposta).get('design')
    if not isinstance(design, dict):
        raise CanvaErro(MSG_INESPERADA)
    return design
