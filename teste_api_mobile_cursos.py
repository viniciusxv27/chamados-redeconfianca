"""Módulo Cursos no app (/api/mobile/v1/m/cursos/): meus cursos, detalhe, comprovante e quadro do gestor.

Roda dentro de uma transação desfeita no fim: não grava nada no banco. Os comprovantes
vão para um storage em memória (nada sobe para o MinIO). Nada aqui manda WhatsApp.
"""
import os
import sys
from datetime import timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
# Caches em memória ANTES do setup: o 'default' é o Redis remoto compartilhado (clear() nele = FLUSHDB).
import redeconfianca.settings as _cfg  # noqa: E402
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'}
               for n in _cfg.CACHES}
django.setup()

from django.conf import settings
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import caches
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client
from django.utils import timezone

from cursos.models import AtribuicaoCurso, Comprovante, ConfiguracaoCursos, Curso
from users.models import Sector, UserModuleAccess

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
M = f'{BASE}/m/cursos'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


# Nada sobe para o MinIO: o comprovante fica em memória.
memoria = InMemoryStorage()
Comprovante._meta.get_field('arquivo').storage = memoria

for nome in settings.CACHES:
    caches[nome].clear()

# A cobrança automática no WhatsApp é acordada pelo middleware do bloqueio: aqui ela nunca roda.
_sem_cobranca = mock.patch('cursos.cobranca.disparar_se_esta_na_hora', return_value=False)
_sem_cobranca.start()

marcador = transaction.atomic()
marcador.__enter__()
try:
    hoje = timezone.localdate()
    setor = Sector.objects.create(name='ZZ Setor Cursos App')

    def novo(apelido, hierarquia='PADRAO', **kw):
        return User.objects.create_user(
            username=f'zzcur.{apelido}', email=f'zzcur.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZ{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia, **kw)

    colab = novo('colab', pdv='ZZ Loja Cursos')
    capac = novo('capac', pdv='ZZ Loja Cursos')
    preso = novo('preso', pdv='ZZ Loja Cursos')
    fora = novo('fora')
    liberado = novo('liberado')
    gestor = novo('gestor', 'SUPERVISOR')

    cfg = ConfiguracaoCursos.get()
    cfg.usuarios.add(colab, preso)
    cfg.gestores.add(gestor)
    UserModuleAccess.objects.create(user=liberado, module_key='cursos')

    aberto = Curso.objects.create(tipo=Curso.FOCO, titulo='ZZ Foco aberto', link='https://exemplo-teste.local/curso',
                                  orientacoes='Faça até o fim.', prazo=hoje + timedelta(days=10), publicado=True)
    vencido = Curso.objects.create(tipo=Curso.FOCO, titulo='ZZ Foco vencido', prazo=hoje - timedelta(days=3),
                                   publicado=True)
    rascunho = Curso.objects.create(tipo=Curso.FOCO, titulo='ZZ Rascunho', prazo=hoje + timedelta(days=5))
    capacitacao = Curso.objects.create(tipo=Curso.CAPACITACAO, titulo='ZZ Capacitação',
                                       prazo=hoje + timedelta(days=5), publicado=True)
    AtribuicaoCurso.objects.create(curso=capacitacao, colaborador=capac)

    def arquivo(nome='print.png', tamanho=200):
        return SimpleUploadedFile(nome, b'x' * tamanho, content_type='image/png')

    # Cursos reais publicados e vencidos travariam os usuários de teste: quitamos com um envio pendente.
    reais_vencidos = list(Curso.objects.filter(publicado=True, tipo=Curso.FOCO, prazo__lt=hoje)
                          .exclude(pk=vencido.pk))
    for u in (colab, preso):
        for c in reais_vencidos:
            Comprovante.objects.create(curso=c, colaborador=u, arquivo=arquivo(), nome_original='real.png', tamanho=200)
    Comprovante.objects.create(curso=vencido, colaborador=colab, arquivo=arquivo('aprovado.pdf'),
                               nome_original='aprovado.pdf', tamanho=200, status=Comprovante.APROVADO)

    def entrar(user):
        r = Client().post(f'{BASE}/auth/login/', {'email': user.email, 'senha': 'S3nha!teste', 'plataforma': 'ios',
                                                   'dispositivo': 'ZZ iPhone'}, content_type='application/json')
        return Client(HTTP_AUTHORIZATION=f'Bearer {r.json()["token"]}')

    c_colab, c_capac, c_fora, c_lib, c_gestor = (entrar(u) for u in (colab, capac, fora, liberado, gestor))

    def chaves(c):
        return [m['chave'] for m in c.get(f'{BASE}/modulos/').json()]

    print('== ACESSO ==')
    t('no escopo: vê o módulo', 'cursos' in chaves(c_colab))
    t('só com capacitação atribuída: vê o módulo', 'cursos' in chaves(c_capac))
    t('liberação individual: vê o módulo', 'cursos' in chaves(c_lib) and c_lib.get(f'{M}/').status_code == 200)
    t('gestor: vê o módulo', 'cursos' in chaves(c_gestor))
    t('fora de tudo: não vê o módulo', 'cursos' not in chaves(c_fora))
    t('fora de tudo: 403 no módulo', c_fora.get(f'{M}/').status_code == 403)
    t('sem token: 401/403', Client().get(f'{M}/').status_code in (401, 403))

    print('\n== MEUS CURSOS ==')
    r = c_colab.get(f'{M}/')
    corpo = r.json()
    meus = {c['id']: c for c in corpo.get('cursos', [])}
    t('lista: 200', r.status_code == 200, r.content[:300])
    t('lista: os Foco publicados', {aberto.pk, vencido.pk} <= set(meus), list(meus))
    t('lista: sem rascunho nem capacitação de outro', rascunho.pk not in meus and capacitacao.pk not in meus)
    t('vencido aprovado: situação APROVADO, não atrasado',
      meus.get(vencido.pk, {}).get('situacao') == 'APROVADO' and not meus[vencido.pk]['atrasado'])
    t('aberto: A enviar com dias', meus.get(aberto.pk, {}).get('situacao') == 'NAO_FEZ' and meus[aberto.pk]['dias'] == 10)
    t('não é gestor', corpo.get('gestor') is False)
    r = c_capac.get(f'{M}/')
    t('capacitação: só o atribuído (e nenhum Foco)', {c['id'] for c in r.json()['cursos']} == {capacitacao.pk},
      r.json().get('cursos'))

    print('\n== DETALHE ==')
    r = c_colab.get(f'{M}/{aberto.pk}/')
    d = r.json()
    t('detalhe: link, orientações e prazo', r.status_code == 200 and d['link'] == aberto.link
      and d['orientacoes'] == 'Faça até o fim.' and d['prazo'] == aberto.prazo.isoformat() and d['pode_enviar'], d)
    t('rascunho: 404', c_colab.get(f'{M}/{rascunho.pk}/').status_code == 404)
    t('capacitação de outra pessoa: 404', c_colab.get(f'{M}/{capacitacao.pk}/').status_code == 404)
    t('Foco para quem não está no escopo: 404', c_capac.get(f'{M}/{aberto.pk}/').status_code == 404)
    t('curso inexistente: 404', c_colab.get(f'{M}/999999999/').status_code == 404)

    print('\n== ENVIAR COMPROVANTE ==')
    r = c_colab.post(f'{M}/{aberto.pk}/comprovante/', {})
    t('sem arquivo: 400 com a mensagem da web', r.status_code == 400 and 'Anexe' in r.json().get('erro', ''), r.content)
    with mock.patch('cursos.views.TAMANHO_MAXIMO', 100):
        r = c_colab.post(f'{M}/{aberto.pk}/comprovante/', {'arquivo': arquivo(tamanho=500)})
    t('acima do limite: 400', r.status_code == 400 and 'limite' in r.json().get('erro', ''), r.content)
    r = c_colab.post(f'{M}/{aberto.pk}/comprovante/', {'arquivo': arquivo('certificado', 300)})
    d = r.json()
    t('envia (qualquer tipo, sem extensão): 201 e entra em conferência',
      r.status_code == 201 and d['situacao'] == 'CONFERIR' and d['ultimo_envio']['nome'] == 'certificado', r.content[:300])
    envio = Comprovante.objects.filter(curso=aberto, colaborador=colab).first()
    t('gravou como na web (nome, tamanho, pendente)', envio and envio.status == Comprovante.PENDENTE
      and envio.tamanho == 300 and envio.nome_original == 'certificado')
    t('Foco para quem não está no escopo: 404', c_capac.post(f'{M}/{aberto.pk}/comprovante/',
                                                             {'arquivo': arquivo()}).status_code == 404)
    t('rascunho: 404', c_colab.post(f'{M}/{rascunho.pk}/comprovante/', {'arquivo': arquivo()}).status_code == 404)
    t('ninguém além dele ganhou comprovante', not Comprovante.objects.filter(curso=aberto).exclude(colaborador=colab).exists())
    r = c_capac.post(f'{M}/{capacitacao.pk}/comprovante/', {'arquivo': arquivo('cap.pdf')})
    t('capacitação atribuída: envia', r.status_code == 201)

    print('\n== LINK DO COMPROVANTE ==')
    url = c_colab.get(f'{M}/{aberto.pk}/').json()['ultimo_envio']['url']
    caminho = url.split('testserver', 1)[-1]
    r = Client().get(caminho)
    t('dono abre o próprio comprovante', r.status_code == 200 and b''.join(r.streaming_content) == b'x' * 300)
    alheio = signing.dumps({'u': capac.pk, 't': 'cursos_comprovante', 'i': str(envio.pk)},
                           salt='api_mobile.arquivo', compress=True)
    t('link no nome de outra pessoa (não gestora): 404', Client().get(f'{BASE}/arquivo/{alheio}/').status_code == 404)
    do_gestor = signing.dumps({'u': gestor.pk, 't': 'cursos_comprovante', 'i': str(envio.pk)},
                              salt='api_mobile.arquivo', compress=True)
    t('gestor abre o comprovante de quem ele confere', Client().get(f'{BASE}/arquivo/{do_gestor}/').status_code == 200)

    print('\n== QUADRO DO GESTOR ==')
    t('colaborador: 403 no quadro', c_colab.get(f'{M}/gestao/').status_code == 403)
    r = c_gestor.get(f'{M}/gestao/', {'curso': aberto.pk})
    q = r.json()
    t('gestor: 200 no curso escolhido', r.status_code == 200 and q['curso']['id'] == aberto.pk, r.content[:300])
    loja = next((l for l in q.get('lojas', []) if l['nome'] == 'ZZ Loja Cursos'), None)
    pessoas = {p['id']: p for p in (loja or {}).get('pessoas', [])}
    t('loja pelo PDV, com quem fez e quem não fez', loja is not None and pessoas.get(colab.pk, {}).get('situacao') == 'CONFERIR'
      and pessoas.get(preso.pk, {}).get('situacao') == 'NAO_FEZ', loja)
    t('capacitação não entra no quadro do Foco', capac.pk not in pessoas)
    t('fila de aprovação em primeiro na loja', loja and loja['pessoas'][0]['situacao'] == 'CONFERIR' and loja['conferir'] >= 1)
    t('comprovante com link no quadro', pessoas.get(colab.pk, {}).get('envio', {}).get('url'))
    r = c_gestor.get(f'{M}/gestao/', {'curso': capacitacao.pk})
    ids = {p['id'] for l in r.json()['lojas'] for p in l['pessoas']}
    t('capacitação: só os atribuídos', ids == {capac.pk}, ids)

    print('\n== BLOQUEIO POR CURSO VENCIDO ==')
    c_preso = entrar(preso)
    trava = ConfiguracaoCursos.get()
    trava.bloquear_navegacao = True   # só em memória: o banco não muda
    with mock.patch.object(ConfiguracaoCursos, 'get', classmethod(lambda cls: trava)):
        caches['local'].clear()
        r = c_preso.get(f'{M}/')
        t('travado pelo curso vencido: 423 também no módulo cursos (regra atual do app)',
          r.status_code == 423 and r.json().get('bloqueio', {}).get('tipo') == 'curso', r.content[:200])
    solto = ConfiguracaoCursos.get()
    solto.bloquear_navegacao = False
    with mock.patch.object(ConfiguracaoCursos, 'get', classmethod(lambda cls: solto)):
        caches['local'].clear()
        r = c_preso.post(f'{M}/{vencido.pk}/comprovante/', {'arquivo': arquivo('vencido.jpg')})
        t('sem a trava: envia o do curso vencido', r.status_code == 201)
    with mock.patch.object(ConfiguracaoCursos, 'get', classmethod(lambda cls: trava)):
        r = c_preso.get(f'{M}/')
        t('com a trava ligada de novo, quem já mandou o comprovante entra', r.status_code == 200, r.content[:200])
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    _sem_cobranca.stop()

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
