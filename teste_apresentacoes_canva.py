"""Assistente de Apresentações — integração com o Canva (OAuth com PKCE e importação do .pptx).

Nada sai para a rede: o cliente HTTP do módulo é trocado por um httpx.MockTransport que registra
cada requisição e responde como a API do Canva (e o transporte real do httpx é bloqueado). Roda
dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import base64
import hashlib
import json
import logging
import os
import re
import sys
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

import httpx
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apresentacoes import canva
from apresentacoes.models import ConexaoCanva, ConfiguracaoApresentacoes
from assistente.crypto import cifrar, decifrar

User = get_user_model()
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


CLIENT_ID = 'OC-AZZteste-cliente'
SEGREDO = 'SEGREDO-da-integracao-9f8e7d6c5b4a'
AT1, RT1 = 'ACESSO-um-1111111111aaaaaaaaaa', 'RENOVA-um-1111111111bbbbbbbbbb'
AT2, RT2 = 'ACESSO-dois-222222222cccccccccc', 'RENOVA-dois-222222222dddddddddd'
AT3, RT3 = 'ACESSO-tres-333333333eeeeeeeeee', 'RENOVA-tres-333333333ffffffffff'
RT4 = 'RENOVA-quatro-44444444gggggggggg'
CODIGO = 'CODIGO-autorizacao-5555555555hhhh'
REDIRECT = 'https://portal.exemplo-teste.local/apresentacoes/canva/retorno/'
SEGREDOS = [SEGREDO, AT1, RT1, AT2, RT2, AT3, RT3, RT4, CODIGO]
TITULO_LONGO = 'Resultados-do-Trimestre-Vivo-Total-Lojas-Norte-e-Nordeste-2026-versao-final'


class CanvaFalso:
    """Responde como a API do Canva, na ordem em que as respostas foram programadas."""

    def __init__(self):
        self.pedidos = []
        self.fila = []

    def responder(self, metodo, caminho, status=200, corpo=None, erro=None):
        self.fila.append((metodo, caminho, status, corpo, erro))

    def __call__(self, request):
        self.pedidos.append(request)
        for indice, (metodo, caminho, status, corpo, erro) in enumerate(self.fila):
            if metodo == request.method and request.url.path == caminho:
                self.fila.pop(indice)
                if erro is not None:
                    raise erro(f'falha simulada em {caminho}', request=request)
                return httpx.Response(status, json=corpo if corpo is not None else {})
        raise AssertionError(f'requisição não programada: {request.method} {request.url}')


servidor = CanvaFalso()


def cliente_falso(tempo_limite=canva.TEMPO_LIMITE):
    return httpx.Client(transport=httpx.MockTransport(servidor), timeout=tempo_limite)


def rede_real(*args, **kwargs):
    raise AssertionError('o teste tentou usar a rede de verdade')


class Coletor(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.mensagens = []

    def emit(self, record):
        self.mensagens.append(record.getMessage())


coletor = Coletor()
registro = logging.getLogger('apresentacoes.canva')
registro.addHandler(coletor)
registro.setLevel(logging.DEBUG)
mensagens_erro = []


def espera_erro(funcao, *args, **kwargs):
    """Executa e devolve a exceção levantada (ou None). Guarda a mensagem para a checagem de segredos."""
    try:
        funcao(*args, **kwargs)
    except Exception as exc:                            # noqa: BLE001
        mensagens_erro.append(str(exc))
        return exc
    return None


def form(request):
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def basic_esperado():
    return 'Basic ' + base64.b64encode(f'{CLIENT_ID}:{SEGREDO}'.encode()).decode()


def token_ok(acesso, renova, validade=14400):
    return {'access_token': acesso, 'refresh_token': renova, 'token_type': 'Bearer', 'expires_in': validade,
            'scope': canva.ESCOPOS}


patches = [
    mock.patch.object(canva, '_cliente', cliente_falso),
    mock.patch.object(httpx.HTTPTransport, 'handle_request', rede_real),
]
for p in patches:
    p.start()

marcador = transaction.atomic()
marcador.__enter__()
try:
    cfg = ConfiguracaoApresentacoes.get()
    cfg.canva_client_id = CLIENT_ID
    cfg.set_canva_secret(SEGREDO)
    cfg.save()
    pessoa = User.objects.create_user(username='zzcanva.pessoa', email='zzcanva.pessoa@exemplo-teste.local',
                                      password='S3nha!teste', first_name='ZZCanva', last_name='Pessoa')
    outra = User.objects.create_user(username='zzcanva.outra', email='zzcanva.outra@exemplo-teste.local',
                                     password='S3nha!teste', first_name='ZZCanva', last_name='Outra')

    print('== CONFIGURAÇÃO ==')
    t('configurado com client id e segredo', canva.configurado())
    t('segredo guardado cifrado', cfg.canva_client_secret_cifrado and SEGREDO not in cfg.canva_client_secret_cifrado)
    t('escopos mínimos (importar + reabrir design)', canva.ESCOPOS == 'design:content:write design:meta:read')

    print('\n== URL DE AUTORIZAÇÃO (PKCE) ==')
    url = canva.url_de_autorizacao(pessoa, REDIRECT, '/apresentacoes/5/')
    partes = urlsplit(url)
    parametros = {k: v[0] for k, v in parse_qs(partes.query).items()}
    t('vai para www.canva.com/api/oauth/authorize', f'{partes.scheme}://{partes.netloc}{partes.path}'
      == 'https://www.canva.com/api/oauth/authorize', url)
    t('client_id, response_type e redirect_uri', parametros.get('client_id') == CLIENT_ID
      and parametros.get('response_type') == 'code' and parametros.get('redirect_uri') == REDIRECT, parametros)
    t('scope separado por espaço e codificado com %20', parametros.get('scope') == canva.ESCOPOS
      and 'scope=design%3Acontent%3Awrite%20design%3Ameta%3Aread' in partes.query, partes.query)
    t('code_challenge_method=S256', parametros.get('code_challenge_method') == 'S256')
    conexao = ConexaoCanva.objects.get(user=pessoa)
    verificador = decifrar(conexao.verificador_cifrado)
    t('state guardado em texto e igual ao da URL', conexao.estado and conexao.estado == parametros.get('state'))
    t('verificador tem 43–128 caracteres permitidos', 43 <= len(verificador) <= 128
      and re.fullmatch(r'[A-Za-z0-9._~-]+', verificador) is not None, len(verificador))
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b'=').decode()
    t('code_challenge = S256 do verificador (base64url sem "=")', parametros.get('code_challenge') == desafio
      and '=' not in desafio)
    t('verificador guardado cifrado (não em texto puro)', verificador not in conexao.verificador_cifrado
      and conexao.verificador_cifrado != verificador)
    t('volta guardada', conexao.volta == '/apresentacoes/5/')
    t('nenhuma chamada ao Canva só para montar a URL', servidor.pedidos == [])
    canva.url_de_autorizacao(outra, REDIRECT, 'https://site-malicioso.exemplo/')
    t('volta externa é descartada', ConexaoCanva.objects.get(user=outra).volta == '')
    canva.url_de_autorizacao(outra, REDIRECT, '//site-malicioso.exemplo/x')
    t('volta "//host" também', ConexaoCanva.objects.get(user=outra).volta == '')
    url2 = canva.url_de_autorizacao(pessoa, REDIRECT, '/apresentacoes/5/')
    estado2 = parse_qs(urlsplit(url2).query)['state'][0]
    t('cada pedido gera state e verificador novos', estado2 != parametros['state']
      and decifrar(ConexaoCanva.objects.get(user=pessoa).verificador_cifrado) != verificador)
    verificador = decifrar(ConexaoCanva.objects.get(user=pessoa).verificador_cifrado)

    print('\n== RETORNO: STATE ERRADO ==')
    erro = espera_erro(canva.concluir_autorizacao, pessoa, CODIGO, 'state-errado', REDIRECT)
    t('state errado é recusado com CanvaErro', isinstance(erro, canva.CanvaErro)
      and not isinstance(erro, (canva.CanvaNaoConectado, canva.CanvaNaoConfigurado)), repr(erro))
    t('sem chamar o Canva', servidor.pedidos == [])
    erro = espera_erro(canva.concluir_autorizacao, pessoa, CODIGO, '', REDIRECT)
    t('state vazio também é recusado', isinstance(erro, canva.CanvaErro))
    t('o fluxo legítimo continua pendente', ConexaoCanva.objects.get(user=pessoa).estado == estado2)

    print('\n== RETORNO: TROCA DO CÓDIGO ==')
    servidor.responder('POST', '/rest/v1/oauth/token', 200, token_ok(AT1, RT1))
    antes = timezone.now()
    conexao = canva.concluir_autorizacao(pessoa, CODIGO, estado2, REDIRECT)
    pedido = servidor.pedidos[-1]
    dados = form(pedido)
    t('POST em api.canva.com/rest/v1/oauth/token', pedido.method == 'POST'
      and str(pedido.url) == 'https://api.canva.com/rest/v1/oauth/token', str(pedido.url))
    t('Authorization: Basic base64(client_id:client_secret)', pedido.headers.get('authorization') == basic_esperado())
    t('corpo form-urlencoded', pedido.headers.get('content-type', '').startswith('application/x-www-form-urlencoded'))
    t('grant_type=authorization_code com code, code_verifier e redirect_uri',
      dados == {'grant_type': 'authorization_code', 'code': CODIGO, 'code_verifier': verificador,
                'redirect_uri': REDIRECT}, dados)
    t('segredo não vai no corpo', SEGREDO not in pedido.content.decode())
    conexao = ConexaoCanva.objects.get(user=pessoa)
    campos = ' '.join([conexao.access_token_cifrado, conexao.refresh_token_cifrado, conexao.estado,
                       conexao.verificador_cifrado, conexao.escopos, conexao.volta])
    t('tokens gravados cifrados (nada em texto puro)', AT1 not in campos and RT1 not in campos
      and conexao.access_token_cifrado and conexao.refresh_token_cifrado)
    t('e decifram para os tokens do Canva', decifrar(conexao.access_token_cifrado) == AT1
      and decifrar(conexao.refresh_token_cifrado) == RT1)
    t('expira_em ≈ agora + expires_in', antes + timedelta(seconds=14340) <= conexao.expira_em
      <= timezone.now() + timedelta(seconds=14460), conexao.expira_em)
    t('escopos concedidos guardados', conexao.escopos == canva.ESCOPOS)
    t('state e verificador limpos', conexao.estado == '' and conexao.verificador_cifrado == '')
    t('conexão ativa', conexao.conectado)
    erro = espera_erro(canva.concluir_autorizacao, pessoa, CODIGO, estado2, REDIRECT)
    t('o mesmo state não serve duas vezes', isinstance(erro, canva.CanvaErro) and len(servidor.pedidos) == 1)

    print('\n== TOKEN DE ACESSO E RENOVAÇÃO ==')
    t('token ainda válido volta sem chamar o Canva', canva.token_de_acesso(pessoa) == AT1
      and len(servidor.pedidos) == 1)
    ConexaoCanva.objects.filter(user=pessoa).update(expira_em=timezone.now() + timedelta(minutes=3))
    servidor.responder('POST', '/rest/v1/oauth/token', 200, token_ok(AT2, RT2))
    token = canva.token_de_acesso(pessoa)
    pedido = servidor.pedidos[-1]
    t('faltando menos de 5 min, renova', token == AT2 and len(servidor.pedidos) == 2, token)
    t('renovação: grant_type=refresh_token com o refresh atual e Basic',
      form(pedido) == {'grant_type': 'refresh_token', 'refresh_token': RT1}
      and pedido.headers.get('authorization') == basic_esperado(), form(pedido))
    conexao = ConexaoCanva.objects.get(user=pessoa)
    t('refresh token rotacionado (uso único) e cifrado', decifrar(conexao.refresh_token_cifrado) == RT2
      and RT2 not in conexao.refresh_token_cifrado and decifrar(conexao.access_token_cifrado) == AT2)
    t('validade renovada', conexao.expira_em > timezone.now() + timedelta(hours=3))

    print('\n== IMPORTAÇÃO: 401 → RENOVA E REPETE ==')
    servidor.responder('POST', '/rest/v1/imports', 401, {'code': 'invalid_access_token', 'message': 'expirado'})
    servidor.responder('POST', '/rest/v1/oauth/token', 200, token_ok(AT3, RT3))
    servidor.responder('POST', '/rest/v1/imports', 200, {'job': {'id': 'job-123', 'status': 'in_progress'}})
    conteudo = b'PK\x03\x04' + b'pptx-de-teste' * 50
    inicio = len(servidor.pedidos)
    job = canva.iniciar_importacao(pessoa, TITULO_LONGO, conteudo)
    pedidos = servidor.pedidos[inicio:]
    t('devolve o job', job == {'id': 'job-123', 'status': 'in_progress'}, job)
    t('três chamadas: importa, renova, importa de novo', [(p.method, p.url.path) for p in pedidos] == [
        ('POST', '/rest/v1/imports'), ('POST', '/rest/v1/oauth/token'), ('POST', '/rest/v1/imports')],
      [(p.method, p.url.path) for p in pedidos])
    t('primeira tentativa com o token antigo', pedidos[0].headers.get('authorization') == f'Bearer {AT2}')
    t('renovou com o refresh da vez', form(pedidos[1]).get('refresh_token') == RT2)
    importacao = pedidos[2]
    t('repetição com o token novo', importacao.headers.get('authorization') == f'Bearer {AT3}')
    t('Content-Type: application/octet-stream', importacao.headers.get('content-type') == 'application/octet-stream')
    metadados = json.loads(importacao.headers.get('import-metadata') or '{}')
    t('Import-Metadata com mime_type do pptx', metadados.get('mime_type')
      == 'application/vnd.openxmlformats-officedocument.presentationml.presentation', metadados)
    titulo = base64.b64decode(metadados.get('title_base64', '')).decode()
    t('title_base64 = título truncado em 50 caracteres', titulo == TITULO_LONGO[:50] and len(titulo) == 50, titulo)
    t('corpo = bytes do .pptx', importacao.content == conteudo)
    t('refresh rotacionado de novo', decifrar(ConexaoCanva.objects.get(user=pessoa).refresh_token_cifrado) == RT3)
    t('título vazio vira "Apresentação"', canva.titulo_do_design('  \n ') == 'Apresentação')
    t('título conta em UTF-16 (emoji não estoura)', len(canva.titulo_do_design('🚀' * 40).encode('utf-16-le')) // 2 <= 50)

    print('\n== CONSULTAR E AGUARDAR ==')
    for status in ('in_progress', 'in_progress'):
        servidor.responder('GET', '/rest/v1/imports/job-123', 200, {'job': {'id': 'job-123', 'status': status}})
    design = {'id': 'DAF-design-1', 'title': 'Resultados', 'urls': {
        'edit_url': 'https://www.canva.com/design/DAF-design-1/edit', 'view_url': 'https://www.canva.com/design/DAF-design-1/view'}}
    servidor.responder('GET', '/rest/v1/imports/job-123', 200,
                       {'job': {'id': 'job-123', 'status': 'success', 'result': {'designs': [design]}}})
    esperas = []
    inicio = len(servidor.pedidos)
    job = canva.aguardar_importacao(pessoa, 'job-123', tentativas=5, espera=0.5, dormir=esperas.append)
    t('aguardar devolve o job em success', job.get('status') == 'success'
      and job['result']['designs'][0]['urls']['edit_url'].endswith('/edit'), job)
    t('consultou 3 vezes e dormiu 2 (sem thread)', len(servidor.pedidos) - inicio == 3 and esperas == [0.5, 0.5],
      (len(servidor.pedidos) - inicio, esperas))
    t('consulta é GET /v1/imports/{id} com Bearer', all(
        p.method == 'GET' and str(p.url) == 'https://api.canva.com/rest/v1/imports/job-123'
        and p.headers.get('authorization') == f'Bearer {AT3}' for p in servidor.pedidos[inicio:]))
    servidor.responder('GET', '/rest/v1/imports/job-123', 200, {'job': {'id': 'job-123', 'status': 'in_progress'}})
    t('consultar_importacao devolve o job', canva.consultar_importacao(pessoa, 'job-123')['status'] == 'in_progress')
    antes_pedidos = len(servidor.pedidos)
    erro = espera_erro(canva.consultar_importacao, pessoa, '../oauth/token')
    t('id de job inválido nem chega ao Canva', isinstance(erro, canva.CanvaErro) and len(servidor.pedidos) == antes_pedidos)

    servidor.responder('GET', '/rest/v1/imports/job-falho', 200, {'job': {
        'id': 'job-falho', 'status': 'failed', 'error': {'code': 'invalid_file', 'message': 'arquivo ruim'}}})
    erro = espera_erro(canva.aguardar_importacao, pessoa, 'job-falho', tentativas=5, espera=0, dormir=lambda s: None)
    t('failed vira CanvaErro com mensagem amigável', isinstance(erro, canva.CanvaErro)
      and str(erro) == canva.MENSAGENS_FALHA_IMPORTACAO['invalid_file'], repr(erro))
    for _ in range(3):
        servidor.responder('GET', '/rest/v1/imports/job-lento', 200, {'job': {'id': 'job-lento', 'status': 'in_progress'}})
    esperas = []
    erro = espera_erro(canva.aguardar_importacao, pessoa, 'job-lento', tentativas=3, espera=1, dormir=esperas.append)
    t('demorou demais → CanvaErro (sem dormir depois da última)', isinstance(erro, canva.CanvaErro) and esperas == [1, 1])

    servidor.responder('GET', '/rest/v1/designs/DAF-design-1', 200, {'design': design})
    t('obter_design devolve os links do design', canva.obter_design(pessoa, 'DAF-design-1')['urls'] == design['urls'])

    print('\n== ERROS DE REDE E DA API ==')
    servidor.responder('POST', '/rest/v1/imports', erro=httpx.ConnectError)
    erro = espera_erro(canva.iniciar_importacao, pessoa, 'Teste', conteudo)
    t('falha de rede → CanvaErro amigável', isinstance(erro, canva.CanvaErro) and str(erro) == canva.MSG_REDE, repr(erro))
    servidor.responder('POST', '/rest/v1/imports', 429, {'code': 'too_many_requests', 'message': 'calma'})
    erro = espera_erro(canva.iniciar_importacao, pessoa, 'Teste', conteudo)
    t('429 → mensagem de limite', isinstance(erro, canva.CanvaErro) and 'limitando' in str(erro), repr(erro))
    servidor.responder('POST', '/rest/v1/imports', 403, {'code': 'permission_denied', 'message': 'scope'})
    erro = espera_erro(canva.iniciar_importacao, pessoa, 'Teste', conteudo)
    t('403 → aponta o escopo', isinstance(erro, canva.CanvaErro) and 'design:content:write' in str(erro), repr(erro))
    erro = espera_erro(canva.iniciar_importacao, pessoa, 'Teste', b'')
    t('arquivo vazio nem é enviado', isinstance(erro, canva.CanvaErro))

    print('\n== CREDENCIAL RECUSADA NÃO DERRUBA A CONEXÃO ==')
    ConexaoCanva.objects.filter(user=pessoa).update(expira_em=timezone.now() - timedelta(minutes=1))
    servidor.responder('POST', '/rest/v1/oauth/token', 401, {'code': 'invalid_client', 'message': 'client'})
    erro = espera_erro(canva.token_de_acesso, pessoa)
    conexao = ConexaoCanva.objects.get(user=pessoa)
    t('invalid_client → CanvaErro para o SUPERADMIN', isinstance(erro, canva.CanvaErro)
      and not isinstance(erro, canva.CanvaNaoConectado) and 'SUPERADMIN' in str(erro), repr(erro))
    t('tokens da pessoa continuam lá', decifrar(conexao.refresh_token_cifrado) == RT3)

    print('\n== REFRESH RECUSADO → PRECISA CONECTAR DE NOVO ==')
    servidor.responder('POST', '/rest/v1/oauth/token', 400, {'code': 'invalid_grant', 'message': 'refresh usado'})
    erro = espera_erro(canva.token_de_acesso, pessoa)
    conexao = ConexaoCanva.objects.get(user=pessoa)
    t('invalid_grant → CanvaNaoConectado', isinstance(erro, canva.CanvaNaoConectado), repr(erro))
    t('tokens apagados', conexao.access_token_cifrado == '' and conexao.refresh_token_cifrado == ''
      and conexao.expira_em is None and not conexao.conectado)

    print('\n== 401 DUAS VEZES → DESCONECTA ==')
    ConexaoCanva.objects.filter(user=pessoa).update(
        access_token_cifrado=cifrar(AT1), refresh_token_cifrado=cifrar(RT1), expira_em=timezone.now() + timedelta(hours=2))
    servidor.responder('POST', '/rest/v1/imports', 401, {'code': 'revoked_access_token'})
    servidor.responder('POST', '/rest/v1/oauth/token', 200, token_ok(AT2, RT2))
    servidor.responder('POST', '/rest/v1/imports', 401, {'code': 'revoked_access_token'})
    erro = espera_erro(canva.iniciar_importacao, pessoa, 'Teste', conteudo)
    t('renova só uma vez e pede para conectar de novo', isinstance(erro, canva.CanvaNaoConectado)
      and not ConexaoCanva.objects.get(user=pessoa).conectado, repr(erro))

    print('\n== NÃO CONECTADO ==')
    antes_pedidos = len(servidor.pedidos)
    erro = espera_erro(canva.token_de_acesso, outra)
    t('sem tokens → CanvaNaoConectado', isinstance(erro, canva.CanvaNaoConectado), repr(erro))
    sem_conexao = User.objects.create_user(username='zzcanva.nunca', email='zzcanva.nunca@exemplo-teste.local',
                                           password='S3nha!teste', first_name='ZZCanva', last_name='Nunca')
    erro = espera_erro(canva.iniciar_importacao, sem_conexao, 'Teste', conteudo)
    t('importar sem conexão → CanvaNaoConectado (subclasse de CanvaErro)', isinstance(erro, canva.CanvaNaoConectado)
      and isinstance(erro, canva.CanvaErro), repr(erro))
    t('sem nenhuma chamada ao Canva', len(servidor.pedidos) == antes_pedidos)

    print('\n== DESCONECTAR ==')
    ConexaoCanva.objects.filter(user=pessoa).update(
        access_token_cifrado=cifrar(AT3), refresh_token_cifrado=cifrar(RT4), expira_em=timezone.now() + timedelta(hours=2))
    servidor.responder('POST', '/rest/v1/oauth/revoke', 200, {})
    t('desconectar devolve True', canva.desconectar(pessoa) is True)
    pedido = servidor.pedidos[-1]
    t('revoga o refresh token no Canva com Basic', str(pedido.url) == 'https://api.canva.com/rest/v1/oauth/revoke'
      and form(pedido) == {'token': RT4} and pedido.headers.get('authorization') == basic_esperado(), form(pedido))
    t('conexão apagada do portal', not ConexaoCanva.objects.filter(user=pessoa).exists())
    t('desconectar de novo devolve False', canva.desconectar(pessoa) is False)
    erro = espera_erro(canva.token_de_acesso, pessoa)
    t('depois de desconectar → CanvaNaoConectado', isinstance(erro, canva.CanvaNaoConectado))
    ConexaoCanva.objects.create(user=pessoa, access_token_cifrado=cifrar(AT1), refresh_token_cifrado=cifrar(RT1),
                                expira_em=timezone.now() + timedelta(hours=1))
    servidor.responder('POST', '/rest/v1/oauth/revoke', erro=httpx.ConnectTimeout)
    t('sem rede para revogar, desconecta do portal mesmo assim', canva.desconectar(pessoa) is True
      and not ConexaoCanva.objects.filter(user=pessoa).exists())

    print('\n== NÃO CONFIGURADO ==')
    cfg.canva_client_id = ''
    cfg.save()
    antes_pedidos = len(servidor.pedidos)
    t('configurado() False sem client id', canva.configurado() is False)
    erro = espera_erro(canva.url_de_autorizacao, pessoa, REDIRECT, '/')
    t('url_de_autorizacao → CanvaNaoConfigurado', isinstance(erro, canva.CanvaNaoConfigurado), repr(erro))
    erro = espera_erro(canva.concluir_autorizacao, pessoa, CODIGO, 'x', REDIRECT)
    t('concluir_autorizacao → CanvaNaoConfigurado', isinstance(erro, canva.CanvaNaoConfigurado), repr(erro))
    erro = espera_erro(canva.iniciar_importacao, pessoa, 'Teste', conteudo)
    t('iniciar_importacao → CanvaNaoConfigurado', isinstance(erro, canva.CanvaNaoConfigurado), repr(erro))
    cfg.canva_client_id = CLIENT_ID
    cfg.set_canva_secret('')
    cfg.save()
    t('sem segredo também não está configurado', canva.configurado() is False
      and isinstance(espera_erro(canva.token_de_acesso, pessoa), canva.CanvaNaoConfigurado))
    t('nenhuma chamada ao Canva sem configuração', len(servidor.pedidos) == antes_pedidos)

    print('\n== NENHUM SEGREDO EM MENSAGEM OU LOG ==')
    vazou = [s for s in SEGREDOS for texto in mensagens_erro + coletor.mensagens if s in texto]
    t('mensagens de erro e log sem token, código ou segredo', not vazou and mensagens_erro and coletor.mensagens,
      vazou)
    t('fila do Canva falso consumida por inteiro', servidor.fila == [], servidor.fila)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    for p in patches:
        p.stop()
    registro.removeHandler(coletor)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
