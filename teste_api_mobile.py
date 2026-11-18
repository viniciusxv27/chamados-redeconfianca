"""API do app mobile (/api/mobile/v1/): token por aparelho e as regras do portal.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
# Cache em memória: o `default` é o Redis remoto do dev e limpar ele seria um FLUSHDB do ambiente todo.
import redeconfianca.settings as _cfg  # noqa: E402
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'} for n in _cfg.CACHES}
django.setup()

from django.conf import settings
from django.core.cache import caches

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client

from api_mobile.models import TokenMobile, hash_do_token
from communications.models import Communication, CommunicationRead
from core.models import Notification
from tickets.models import Category, Ticket, TicketComment
from users.models import Sector

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


for nome in settings.CACHES:
    caches[nome].clear()

marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Setor App')
    outro = Sector.objects.create(name='ZZ Outro Setor App')
    cat = Category.objects.create(sector=setor, name='ZZ Categoria App', default_solution_time_hours=8)

    def novo(apelido, hierarquia='PADRAO', setor_=setor, **kw):
        return User.objects.create_user(
            username=f'zzapp.{apelido}', email=f'zzapp.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZ{apelido.title()}', last_name='Teste', sector=setor_, hierarchy=hierarquia, **kw)

    ana = novo('ana', 'SUPERVISOR')
    beto = novo('beto', 'SUPERVISOR', outro)
    chefe = novo('chefe', 'SUPERADMIN', outro)
    inativo = novo('inativo', is_active=False)

    def entrar(email, senha='S3nha!teste'):
        c = Client()
        r = c.post(f'{BASE}/auth/login/', {'email': email, 'senha': senha, 'plataforma': 'android',
                                            'dispositivo': 'ZZ Pixel'}, content_type='application/json')
        return r

    print('== LOGIN ==')
    r = entrar(ana.email)
    t('login certo devolve token e usuário', r.status_code == 200 and r.json().get('token')
      and r.json()['usuario']['email'] == ana.email, r.content[:200])
    token = r.json().get('token', '')
    reg = TokenMobile.objects.filter(user=ana).first()
    t('o banco guarda só o hash do token', reg is not None and reg.chave_hash == hash_do_token(token)
      and token not in (reg.chave_hash, reg.prefixo) and reg.plataforma == 'android')
    t('senha errada: 401', entrar(ana.email, 'errada').status_code == 401)
    t('usuário inativo não entra', entrar(inativo.email).status_code == 401)
    t('sem e-mail: 400', Client().post(f'{BASE}/auth/login/', {}, content_type='application/json').status_code == 400)

    def api(tok):
        c = Client(HTTP_AUTHORIZATION=f'Bearer {tok}')
        return c

    ca = api(token)
    cb = api(entrar(beto.email).json()['token'])
    cc = api(entrar(chefe.email).json()['token'])

    print('\n== SESSÃO ==')
    t('sem token: 401/403', Client().get(f'{BASE}/eu/').status_code in (401, 403))
    t('token inventado: 401', api('xyz').get(f'{BASE}/eu/').status_code == 401)
    r = ca.get(f'{BASE}/eu/')
    t('/eu/ traz o perfil', r.status_code == 200 and r.json()['usuario']['nome'] == 'ZZAna Teste'
      and r.json()['bloqueio'] is None, r.content[:200])
    t('sem cookie de sessão: a API não depende de CSRF',
      ca.post(f'{BASE}/notificacoes/lidas/').status_code == 200)

    print('\n== CHAMADOS ==')
    meu = Ticket.objects.create(title='ZZ meu chamado', description='x', sector=outro, created_by=ana)
    do_setor = Ticket.objects.create(title='ZZ do setor da Ana', description='x', sector=setor, created_by=beto)
    alheio = Ticket.objects.create(title='ZZ alheio', description='x', sector=outro, created_by=beto)
    r = ca.get(f'{BASE}/chamados/', {'situacao': 'todos', 'busca': 'ZZ'})
    ids = {c['id'] for c in r.json().get('resultados', [])}
    t('lista: o que abriu e o do setor', {meu.pk, do_setor.pk} <= ids, ids)
    t('lista: o de outro setor não', alheio.pk not in ids)
    r = ca.get(f'{BASE}/chamados/', {'situacao': 'todos', 'escopo': 'meus', 'busca': 'ZZ'})
    t('escopo "meus"', {c['id'] for c in r.json()['resultados']} == {meu.pk})
    r = ca.get(f'{BASE}/chamados/', {'situacao': 'todos', 'busca': f'#{alheio.pk}'})
    t('busca por número não fura a visibilidade', r.json()['total'] == 0)
    r = cc.get(f'{BASE}/chamados/', {'situacao': 'todos', 'busca': 'ZZ alheio'})
    t('SUPERADMIN vê todos', alheio.pk in {c['id'] for c in r.json()['resultados']})

    r = ca.get(f'{BASE}/chamados/{do_setor.pk}/')
    t('detalhe abre', r.status_code == 200 and r.json()['titulo'] == 'ZZ do setor da Ana'
      and r.json()['pode_comentar'] is True, r.content[:200])
    t('detalhe de chamado alheio: 404', ca.get(f'{BASE}/chamados/{alheio.pk}/').status_code == 404)

    r = ca.post(f'{BASE}/chamados/{do_setor.pk}/comentarios/', {'texto': 'ZZ comentei pelo app'},
                content_type='application/json')
    t('comentar', r.status_code == 201 and TicketComment.objects.filter(ticket=do_setor, user=ana).exists(),
      r.content[:200])
    t('comentário vazio: 400', ca.post(f'{BASE}/chamados/{do_setor.pk}/comentarios/', {'texto': ' '},
                                        content_type='application/json').status_code == 400)
    t('comentar em alheio: 403', ca.post(f'{BASE}/chamados/{alheio.pk}/comentarios/', {'texto': 'x'},
                                          content_type='application/json').status_code == 403)
    r = ca.get(f'{BASE}/chamados/{do_setor.pk}/')
    t('o comentário aparece no detalhe', any(c['texto'] == 'ZZ comentei pelo app' and c['sou_eu']
                                            for c in r.json()['comentarios']))

    print('\n== ABRIR CHAMADO ==')
    r = ca.get(f'{BASE}/chamados/setores/')
    t('setores com categoria ativa', any(s['id'] == setor.pk for s in r.json())
      and not any(s['id'] == outro.pk for s in r.json()))
    r = ca.get(f'{BASE}/chamados/categorias/', {'setor': setor.pk})
    t('categorias do setor', [c['id'] for c in r.json()] == [cat.pk])
    r = ca.post(f'{BASE}/chamados/', {'titulo': '', 'descricao': ''}, content_type='application/json')
    t('campos faltando: 400 com o motivo de cada um', r.status_code == 400
      and {'titulo', 'descricao', 'setor'} <= set(r.json().get('campos', {})), r.content[:300])
    r = ca.post(f'{BASE}/chamados/', {'titulo': 'ZZ Impressora parou', 'descricao': 'Não liga',
                                       'setor': setor.pk, 'categoria': cat.pk, 'prioridade': 'ALTA',
                                       'anexos': SimpleUploadedFile('zz.txt', b'ola', 'text/plain')})
    novo_t = Ticket.objects.filter(title='ZZ Impressora parou').first()
    t('abre o chamado (multipart, com anexo)', r.status_code == 201 and novo_t is not None
      and novo_t.created_by_id == ana.pk and novo_t.priority == 'ALTA'
      and novo_t.solution_time_hours == 8 and novo_t.logs.filter(new_status='ABERTO').exists(), r.content[:300])
    anexo = novo_t.attachments.first() if novo_t else None
    t('o anexo foi salvo', anexo is not None and anexo.original_filename == 'zz.txt')
    if anexo:
        anexo.file.delete(save=False)   # MinIO é compartilhado: não deixa lixo

    print('\n== COMUNICADOS ==')
    todos = Communication.objects.create(title='ZZ Para todos', message='<p>Olá <b>rede</b></p><p>Linha 2</p>',
                                         sender=chefe, send_to_all=True)
    so_beto = Communication.objects.create(title='ZZ Só Beto', message='x', sender=chefe)
    so_beto.recipients.add(beto)
    r = ca.get(f'{BASE}/comunicados/')
    ids = {c['id'] for c in r.json()['resultados']}
    t('vê o comunicado para todos', todos.pk in ids)
    t('não vê o de outra pessoa', so_beto.pk not in ids)
    t('detalhe do de outra pessoa: 404', ca.get(f'{BASE}/comunicados/{so_beto.pk}/').status_code == 404)
    r = ca.get(f'{BASE}/comunicados/{todos.pk}/')
    t('texto sem HTML, com parágrafos', r.status_code == 200 and r.json()['texto'] == 'Olá rede\nLinha 2',
      r.json().get('texto'))
    t('abrir marca como visualizado', CommunicationRead.objects.filter(communication=todos, user=ana).exists())
    r = ca.post(f'{BASE}/comunicados/{todos.pk}/situacao/', {'situacao': 'ESTOU_CIENTE'},
                content_type='application/json')
    t('estou ciente', r.status_code == 200 and CommunicationRead.objects.get(
        communication=todos, user=ana).status == 'ESTOU_CIENTE')
    r = ca.post(f'{BASE}/comunicados/{todos.pk}/curtir/')
    t('curtir', r.json() == {'curti': True, 'curtidas': 1})
    r = ca.post(f'{BASE}/comunicados/{todos.pk}/curtir/')
    t('descurtir', r.json() == {'curti': False, 'curtidas': 0})

    print('\n== INÍCIO E NOTIFICAÇÕES ==')
    Notification.objects.create(user=ana, title='ZZ aviso', message='ZZ mensagem', notification_type='SYSTEM')
    r = ca.get(f'{BASE}/inicio/')
    j = r.json()
    t('início responde', r.status_code == 200 and 'chamados' in j and j['usuario']['id'] == ana.pk, r.content[:300])
    t('conta os chamados abertos por mim', j['chamados']['abertos_por_mim'] >= 2)
    t('traz o comunicado novo', any(c['id'] == todos.pk for c in j['comunicados']))
    from compliments.models import Compliment
    elogio = Compliment.objects.create(from_user=chefe, to_user=ana, rating=5, comment='ZZ Mandou muito bem no atendimento')
    j = ca.get(f'{BASE}/inicio/').json()
    e = next((x for x in j.get('elogios', []) if x['id'] == elogio.pk), None)
    t('elogios recentes, como na home do portal', e is not None and e['estrelas'] == 5 and e['para_mim'] and e['url_web'], j.get('elogios'))
    eb = next((x for x in cb.get(f'{BASE}/inicio/').json().get('elogios', []) if x['id'] == elogio.pk), None)
    # A home só mostra "Ver detalhes" a quem recebeu (para_mim); o link existe para quem a tela de
    # detalhe aceita (o Beto é SUPERVISOR e gerencia usuários — compliments/views.py:183).
    t('quem não é o destinatário vê o elogio, sem o "Ver detalhes" da home', eb is not None and not eb['para_mim'], eb)
    with mock.patch.object(type(beto), 'can_manage_users', lambda self: False):
        eb2 = next((x for x in cb.get(f'{BASE}/inicio/').json().get('elogios', []) if x['id'] == elogio.pk), None)
    t('e quem não pode abrir o detalhe não recebe o link', eb2 is not None and eb2['url_web'] is None, eb2)
    t('saldo C$ escondido de SUPERVISOR (regra da home do portal)', j.get('saldo_cs') is None)
    t('saldo C$ aparece para SUPERADMIN', cc.get(f'{BASE}/inicio/').json().get('saldo_cs') is not None)
    # Só para a Ana: comunicado "para todos" dispara aviso para a rede inteira (lento).
    fixado = Communication.objects.create(title='ZZ Fixado', message='x', sender=chefe, is_pinned=True)
    fixado.recipients.add(ana)
    feed = ca.get(f'{BASE}/comunicados/', {'fixados': 'nao'}).json()['resultados']
    t('o feed da home não repete os fixados', all(not c['fixado'] for c in feed) and any(c['id'] == todos.pk for c in feed))
    t('o post traz texto do feed e imagens', 'texto_feed' in feed[0] and isinstance(feed[0]['imagens'], list))
    r = ca.get(f'{BASE}/notificacoes/')
    aviso = next((n for n in r.json()['resultados'] if n['titulo'] == 'ZZ aviso'), None)
    t('notificação do sistema antigo aparece', aviso is not None and aviso['id'].startswith('c-'))
    if aviso:
        r = ca.post(f'{BASE}/notificacoes/{aviso["id"]}/lida/')
        t('marcar como lida', r.status_code == 200
          and Notification.objects.get(pk=int(aviso['id'][2:])).is_read)
    t('notificação de outra pessoa: 404', cb.post(f'{BASE}/notificacoes/{aviso["id"]}/lida/').status_code == 404
      if aviso else False)

    print('\n== BLOQUEIOS DO PORTAL ==')
    with mock.patch('api_mobile.permissoes.bloqueio_atual',
                    return_value={'tipo': 'ferias', 'titulo': 'Você está de férias', 'mensagem': 'Férias',
                                  'caminho_web': '/ferias/'}):
        r = ca.get(f'{BASE}/chamados/')
        t('bloqueado: 423 com o motivo', r.status_code == 423 and r.json()['bloqueio']['tipo'] == 'ferias',
          (r.status_code, r.content[:200]))
        r = ca.get(f'{BASE}/eu/')
        t('o /eu/ responde mesmo bloqueado', r.status_code == 200 and r.json()['bloqueio']['tipo'] == 'ferias')
    from api_mobile.bloqueios import bloqueio_atual
    t('o bloqueio real roda os middlewares sem erro', bloqueio_atual(ana, usar_cache=False) is None)

    print('\n== SAIR ==')
    r = ca.post(f'{BASE}/auth/logout/')
    t('logout', r.status_code == 200)
    t('depois do logout o token não vale', ca.get(f'{BASE}/eu/').status_code == 401)
    t('o outro aparelho continua', cb.get(f'{BASE}/eu/').status_code == 200)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
