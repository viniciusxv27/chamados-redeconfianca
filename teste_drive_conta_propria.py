"""Drive: conectar a conta Google do dono (sem Workspace) e ver o Drive inteiro.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
Não fala com o Google — o cliente é substituído por dublês.
"""
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from drive import gdrive
from drive.models import DriveAuditLog, DriveConfig
from users.models import Sector

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


marcador = transaction.atomic()
marcador.__enter__()
try:
    area = Sector.objects.create(name='ZZ Area Drive')

    def novo(u, **kw):
        return User.objects.create_user(
            username=u, email=f'{u}@exemplo-teste.local', password='S3nha!teste',
            sector=area, first_name=u.split('.')[1].title(), last_name='T', **kw)

    chefe = novo('zzd.chefe', is_superuser=True, is_staff=True, hierarchy='SUPERADMIN')
    gente = novo('zzd.gente')

    cfg = DriveConfig.get()
    original = {c: getattr(cfg, c) for c in
                ('modo', 'oauth_client_id', 'oauth_client_secret',
                 'oauth_refresh_token', 'oauth_email', 'oauth_conectado_em')}

    print('== O ESTADO INICIAL ==')
    cfg.modo = DriveConfig.Modo.SA
    cfg.oauth_client_id = ''
    cfg.oauth_client_secret = ''
    cfg.oauth_refresh_token = ''
    cfg.oauth_email = ''
    cfg.save()
    t('nasce em conta de serviço', not cfg.usa_conta_propria)
    t('e sem OAuth pronto', not cfg.oauth_pronto)

    c = Client(); c.force_login(chefe)
    html = c.get('/drive/configuracao/').content.decode()
    t('a tela explica que sem Workspace não dá pela conta de serviço',
      'Workspace' in html and 'Conectar a minha conta Google' in html)
    t('mostra o endereço de retorno para colar no Google',
      '/drive/oauth/callback/' in html)
    t('o botão nasce desabilitado sem client id/secret',
      'Salve o ID e o segredo do cliente' in html)

    print('\n== O ENDEREÇO DE RETORNO ==')
    from django.test import RequestFactory

    from drive.views import _redirect_uri
    for h in ('chamados.redeconfianca.com.br', 'testserver'):
        if h not in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS.append(h)

    def uri(host, seguro=False):
        return _redirect_uri(RequestFactory().get(
            '/drive/configuracao/', HTTP_HOST=host, secure=seguro))

    # O portal roda atrás de um proxy que termina o TLS: o Django recebe http e
    # montaria http://…, que o Google recusa (redirect_uri_mismatch).
    t('atrás do proxy, força https',
      uri('chamados.redeconfianca.com.br') ==
      'https://chamados.redeconfianca.com.br/drive/oauth/callback/',
      uri('chamados.redeconfianca.com.br'))
    t('já em https, não mexe',
      uri('chamados.redeconfianca.com.br', True) ==
      'https://chamados.redeconfianca.com.br/drive/oauth/callback/')
    t('em 127.0.0.1 mantém http (o Google aceita)',
      uri('127.0.0.1:8009').startswith('http://127.0.0.1:8009/'))
    t('em localhost também', uri('localhost:8009').startswith('http://localhost:8009/'))
    t('o consentimento e o callback usam a MESMA função',
      uri('chamados.redeconfianca.com.br') == uri('chamados.redeconfianca.com.br', True))

    print('\n== SALVAR O CLIENTE OAUTH ==')
    r = c.post('/drive/configuracao/', {
        'oauth_client_id': '123-abc.apps.googleusercontent.com',
        'oauth_client_secret': 'GOCSPX-segredo-de-teste',
        'ativo': 'on', 'allowed_extensions': 'pdf', 'max_file_mb': '100',
        'storage_cap_gb': '0', 'trash_retention_days': '30'}, follow=True)
    cfg.refresh_from_db()
    t('guarda o client id', cfg.oauth_client_id.startswith('123-abc'))
    t('guarda o segredo', cfg.oauth_client_secret == 'GOCSPX-segredo-de-teste')

    html = c.get('/drive/configuracao/').content.decode()
    t('o segredo NUNCA volta para a tela', 'GOCSPX-segredo-de-teste' not in html)
    t('mas a tela diz que ele existe', 'já salvo' in html)
    t('o botão liberou', 'Conectar minha conta Google' in html)

    r = c.post('/drive/configuracao/', {
        'oauth_client_id': '123-abc.apps.googleusercontent.com',
        'oauth_client_secret': '',
        'ativo': 'on', 'allowed_extensions': 'pdf', 'max_file_mb': '100',
        'storage_cap_gb': '0', 'trash_retention_days': '30'}, follow=True)
    cfg.refresh_from_db()
    t('salvar com o segredo vazio MANTÉM o segredo',
      cfg.oauth_client_secret == 'GOCSPX-segredo-de-teste')

    print('\n== O CONSENTIMENTO ==')
    r = c.post('/drive/oauth/conectar/')
    t('redireciona para o Google', r.status_code == 302, r.status_code)
    destino = r['Location']
    t('o redirect_uri enviado ao Google é o mesmo da tela',
      'drive%2Foauth%2Fcallback' in destino or 'drive/oauth/callback' in destino,
      destino[:120])
    t('para o endpoint certo', destino.startswith(gdrive.OAUTH_AUTH_URL), destino[:60])
    t('pede acesso offline (senão o token morre em 1h)', 'access_type=offline' in destino)
    t('força o consentimento (senão não vem refresh token)',
      'prompt=consent' in destino)
    t('pede o escopo do Drive', 'auth%2Fdrive' in destino or 'auth/drive' in destino)
    t('leva o client id', '123-abc' in destino)
    t('e um state', 'state=' in destino)
    t('o state ficou na sessão', bool(c.session.get('drive_oauth_state')))

    state = c.session['drive_oauth_state']

    print('\n== O RETORNO ==')
    r = c.get('/drive/oauth/callback/', {'code': 'x', 'state': 'state-forjado'}, follow=True)
    cfg.refresh_from_db()
    t('state que não confere é recusado', not cfg.oauth_refresh_token)
    t('e a tela avisa', 'não confere com esta sessão' in r.content.decode())

    # refaz o state (o callback consome)
    c.post('/drive/oauth/conectar/')
    state = c.session['drive_oauth_state']
    r = c.get('/drive/oauth/callback/',
              {'error': 'access_denied', 'state': state}, follow=True)
    cfg.refresh_from_db()
    t('cancelar no Google não conecta', not cfg.oauth_refresh_token)
    t('e a tela explica', 'cancelada no Google' in r.content.decode())

    c.post('/drive/oauth/conectar/')
    state = c.session['drive_oauth_state']
    falso = {'access_token': 'at', 'refresh_token': 'rt-de-teste',
             'expires_in': 3599, 'token_type': 'Bearer'}
    sobre = mock.MagicMock()
    sobre.about.return_value.get.return_value.execute.return_value = {
        'user': {'emailAddress': 'dono@exemplo-teste.local'}}
    with mock.patch.object(gdrive, 'trocar_codigo', return_value=falso), \
         mock.patch.object(gdrive, 'service', return_value=sobre):
        r = c.get('/drive/oauth/callback/',
                  {'code': 'codigo-bom', 'state': state}, follow=True)
    cfg.refresh_from_db()
    t('conecta e guarda o refresh token', cfg.oauth_refresh_token == 'rt-de-teste')
    t('vira modo conta própria', cfg.usa_conta_propria)
    t('descobre a conta pela API, não pelo que foi digitado',
      cfg.oauth_email == 'dono@exemplo-teste.local')
    t('registra quem conectou', cfg.oauth_conectado_por_id == chefe.id)
    t('e quando', cfg.oauth_conectado_em is not None)
    t('a tela confirma', 'todos os arquivos do seu Drive' in r.content.decode())
    t('fica na auditoria',
      DriveAuditLog.objects.filter(acao='PERM',
                                   detalhe__icontains='Conta Google conectada').exists())

    html = c.get('/drive/configuracao/').content.decode()
    t('o refresh token NUNCA aparece na tela', 'rt-de-teste' not in html)
    t('mas a tela mostra a conta', 'dono@exemplo-teste.local' in html)

    print('\n== O CLIENTE USA A CONTA CONECTADA ==')
    t('o portal se considera configurado', gdrive.configurado())
    cred = gdrive._credenciais()
    t('monta credencial de usuário, não de conta de serviço',
      cred.__class__.__module__.endswith('oauth2.credentials'),
      cred.__class__)
    t('com o refresh token', cred.refresh_token == 'rt-de-teste')
    t('e o escopo do Drive', 'https://www.googleapis.com/auth/drive' in (cred.scopes or []))

    marca = gdrive._marca()
    t('a marca identifica o modo OAuth', marca.startswith('oauth:'), marca)
    t('e NÃO carrega o segredo', 'rt-de-teste' not in marca
      and 'GOCSPX' not in marca, marca)

    print('\n== MEU DRIVE: SÓ O SUPERADMIN ==')
    itens = [{'id': 'f1', 'name': 'ZZ Pasta', 'mimeType': gdrive.FOLDER_MIME},
             {'id': 'a1', 'name': 'ZZ Contrato.pdf', 'mimeType': 'application/pdf',
              'size': '2048', 'modifiedTime': '2026-09-01T10:00:00Z'}]
    with mock.patch.object(gdrive, 'listar', return_value=(itens, None)), \
         mock.patch.object(gdrive, 'caminho', return_value=[]):
        html = c.get('/drive/meu-drive/').content.decode()
    t('o superadmin navega o Meu Drive', 'ZZ Contrato.pdf' in html)
    t('mostra as pastas', 'ZZ Pasta' in html)
    t('e diz de quem é a conta', 'dono@exemplo-teste.local' in html)
    t('avisa que está fora do recorte por setor',
      'não estão ligados a nenhum setor' in html)

    cg = Client(); cg.force_login(gente)
    r = cg.get('/drive/meu-drive/', follow=True)
    corpo = r.content.decode()
    t('usuário comum NÃO entra no Meu Drive',
      'ZZ Contrato.pdf' not in corpo)
    t('e é avisado', 'exclusiva do SUPERADMIN' in corpo)

    r = cg.get('/drive/meu-drive/a/a1/')
    t('nem no arquivo por URL direta (403)', r.status_code == 403, r.status_code)
    r = cg.get('/drive/meu-drive/a/a1/conteudo/')
    t('nem no conteúdo (403)', r.status_code == 403, r.status_code)
    t('a recusa vai para a auditoria',
      DriveAuditLog.objects.filter(user=gente, acao='DENY').exists())

    print('\n== O ATALHO NA HOME DO DRIVE ==')
    html = c.get('/drive/').content.decode()
    t('o superadmin vê o botão do Meu Drive', 'Meu Drive' in html)
    html = cg.get('/drive/').content.decode()
    t('o usuário comum não vê', 'Meu Drive' not in html)

    print('\n== DESCONECTAR ==')
    r = cg.post('/drive/oauth/desconectar/', follow=True)
    cfg.refresh_from_db()
    t('usuário comum não desconecta', cfg.oauth_refresh_token == 'rt-de-teste')

    with mock.patch.object(gdrive, 'revogar') as revogou:
        r = c.post('/drive/oauth/desconectar/', follow=True)
    cfg.refresh_from_db()
    t('o superadmin desconecta', cfg.oauth_refresh_token == '')
    t('revoga no Google também', revogou.called)
    t('volta para conta de serviço', cfg.modo == DriveConfig.Modo.SA)
    t('limpa a conta', cfg.oauth_email == '')
    t('fica na auditoria',
      DriveAuditLog.objects.filter(acao='PERM',
                                   detalhe__icontains='desconectada').exists())

    with mock.patch.object(gdrive, 'listar', return_value=([], None)):
        r = c.get('/drive/meu-drive/', follow=True)
    t('sem conta conectada o Meu Drive fecha',
      'Conecte a sua conta Google' in r.content.decode())

    print('\n== NÃO DÁ PARA LIGAR O MODO SEM CONECTAR ==')
    r = c.post('/drive/configuracao/', {
        'modo': 'OAUTH', 'oauth_client_id': '123-abc.apps.googleusercontent.com',
        'oauth_client_secret': '', 'ativo': 'on', 'allowed_extensions': 'pdf',
        'max_file_mb': '100', 'storage_cap_gb': '0',
        'trash_retention_days': '30'}, follow=True)
    cfg.refresh_from_db()
    t('modo OAUTH sem conta conectada é recusado',
      cfg.modo == DriveConfig.Modo.SA, cfg.modo)
    t('e a tela explica', 'Conecte a sua conta Google antes' in r.content.decode())

    print('\n== O CONSENTIMENTO NÃO ABRE SEM CREDENCIAL ==')
    cfg.oauth_client_id = ''
    cfg.save()
    r = c.post('/drive/oauth/conectar/', follow=True)
    t('sem client id não vai para o Google',
      'Preencha o ID e o segredo' in r.content.decode())

    for campo, valor in original.items():
        setattr(cfg, campo, valor)
    cfg.save()

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    gdrive.resetar()
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
