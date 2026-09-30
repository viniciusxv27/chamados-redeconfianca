"""Contestação: "deve mostrar pro usuário o que foi contestado e o que ficou lá".

A loja mandava o carrinho e chegavam menos vendas do que ela contou. A janela
de conferência já explicava isso *no momento do envio*, mas quem abria a tela
depois não tinha onde ver o balanço — e recontava na mão.

O que este teste cobre:

- o painel do topo (`/contestacao/`): quantas viraram contestação neste ciclo
  (com o valor) e quantas ficaram no carrinho, separando prontas de paradas e
  dizendo o motivo de cada parada em português;
- o carrinho (`/contestacao/carrinho/rascunho/`): a situação de cada item e o
  resumo, sem precisar clicar em Enviar;
- o redesenho do painel (`/contestacao/balanco/`) e a assinatura que evita
  pedir de novo o que já está na tela;
- a tela: selo por item, resumo na barra do carrinho e o JS no node --check.

Roda dentro de uma transação desfeita. O que subir para o MinIO é apagado.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ct-balanco'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ct-balanco-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client

from communications.models import CommunicationGroup
from contestacao.models import (Contestation, ContestationCartDraft, ContestationHistory,
                                ExclusionRecord, ExclusionSyncBatch, TipoBase)
from users.models import Sector

User = get_user_model()
ok = fail = 0
subidos = []
RASCUNHO = '/contestacao/carrinho/rascunho/'
BALANCO = '/contestacao/balanco/'
ENVIAR = '/contestacao/contestar-lote/'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def foto(nome='evidencia.jpg'):
    return SimpleUploadedFile(nome, b'\xff\xd8\xff\xe0zz-evidencia' + bytes(range(64)),
                              content_type='image/jpeg')


marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Loja Balanço')
    chefe = User.objects.create_user(
        username='zzbl.chefe', email='zzbl.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)
    gerentes, _ = CommunicationGroup.objects.get_or_create(name='GERENTES', defaults={'created_by': chefe})
    gerente = User.objects.create_user(
        username='zzbl.gerente', email='zzbl.gerente@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Gerente', hierarchy='PADRAO', sector=setor)
    gerente.communication_groups.add(gerentes)

    lote = ExclusionSyncBatch.objects.create(record_type=TipoBase.EXCLUSAO, record_count=6,
                                             created_by=chefe)
    ContestationHistory.objects.create(action='synced', user=chefe, notes='ZZ sync do teste')

    def venda(numero, receita, filial='ZZ LOJA BALANÇO'):
        return ExclusionRecord.objects.create(
            sync_batch=lote, filial=filial, vendedor=f'ZZ Vendedor {numero}', receita=receita,
            pilar='ZZ', numero_venda=f'ZZB{numero}', data_venda='01/09/2026',
            nome_cliente=f'ZZ Cliente {numero}', record_type=TipoBase.EXCLUSAO)

    pronta1, pronta2 = venda(1, 100), venda(2, 200)
    sem_motivo = venda(3, 300)
    sem_anexo = venda(4, 400)
    de_outra_loja = venda(5, 500, filial='ZZ OUTRA FILIAL')

    def rascunho(registro, motivo='ZZ motivo', com_anexo=True):
        d = ContestationCartDraft.objects.create(user=gerente, exclusion=registro, reason=motivo)
        if com_anexo:
            d.attachment.save(f'ev{registro.pk}.jpg', foto(), save=True)
            subidos.append(d.attachment.name)
        return d

    for registro in (pronta1, pronta2):
        rascunho(registro)
    rascunho(sem_motivo, motivo='')
    rascunho(sem_anexo, com_anexo=False)
    rascunho(de_outra_loja)

    c = Client(); c.force_login(gerente)

    print('== O CARRINHO DIZ O QUE VAI E O QUE FICA ==')
    dados = c.get(RASCUNHO).json()
    situacao = {int(i['exclusion_id']): i['situacao'] for i in dados['items']}
    t('cada item do carrinho volta com a situação', len(situacao) == 4, situacao)
    t('a venda de outra loja não é listada (o carrinho só mostra o que é seu)',
      de_outra_loja.pk not in situacao, situacao)
    t('as prontas são prontas',
      situacao[pronta1.pk] == 'pronta' and situacao[pronta2.pk] == 'pronta', situacao)
    t('sem motivo é apontado', situacao[sem_motivo.pk] == 'sem_motivo')
    t('sem evidência também', situacao[sem_anexo.pk] == 'sem_evidencia')
    resumo = dados['resumo']
    t('e vem o resumo do carrinho', resumo['total'] == 4 and resumo['prontas'] == 2, resumo)
    t('com as paradas contadas por motivo',
      resumo['paradas'] == {'sem_motivo': 1, 'sem_evidencia': 1}, resumo)

    print('\n== O PAINEL DO TOPO ==')
    r = c.get('/contestacao/')
    b = r.context['balanco']
    t('a tela abre', r.status_code == 200)
    t('nada enviado ainda', b['enviadas'] == 0 and b['valor_enviado'] == 0, b)
    # O painel conta os rascunhos do banco — inclusive o da venda que saiu da
    # sua loja, que a lista esconde. É justamente o que explica a diferença.
    t('cinco no carrinho, duas prontas e três paradas',
      b['no_carrinho'] == 5 and b['prontas'] == 2 and b['paradas'] == 3, b)
    rotulos = [l['rotulo'] for l in b['paradas_detalhe']]
    t('o motivo de cada parada vem em português',
      set(rotulos) == {'Falta escrever o motivo', 'Falta anexar a evidência',
                       'Fora da sua loja/base'}, rotulos)
    html = r.content.decode()
    t('e a tela mostra o painel', 'Você já contestou' in html and 'Ficou no carrinho' in html)
    t('com o número do carrinho e o que falta',
      '>5<' in html and 'Falta anexar a evidência' in html)
    t('e o link para a lista das contestadas', '/contestacao/minhas/' in html)

    print('\n== DEPOIS DE ENVIAR ==')
    r = c.post(ENVIAR, data=json.dumps({'ids': [pronta1.pk, pronta2.pk]}),
               content_type='application/json')
    t('o envio funciona', r.status_code == 200 and r.json()['created'] == 2, r.content[:200])
    for feita in Contestation.objects.filter(exclusion__in=[pronta1, pronta2]):
        if feita.attachment:
            subidos.append(feita.attachment.name)

    b = c.get('/contestacao/').context['balanco']
    t('o painel passa a mostrar as duas contestadas',
      b['enviadas'] == 2 and float(b['valor_enviado']) == 300.0, b)
    t('e o carrinho fica com as três que não passaram',
      b['no_carrinho'] == 3 and b['prontas'] == 0 and b['paradas'] == 3, b)

    print('\n== O REDESENHO DO PAINEL ==')
    r = c.get(BALANCO)
    fragmento = r.content.decode()
    t('o fragmento responde', r.status_code == 200)
    t('e é só o painel, sem a página toda',
      'Ficou no carrinho' in fragmento and '<html' not in fragmento.lower(), fragmento[:120])
    assinatura = re.search(r'id="balancoAssinatura"[^>]*data-v="([^"]*)"', fragmento)
    t('traz a assinatura do carrinho', bool(assinatura and assinatura.group(1)),
      fragmento[:200])
    antes = assinatura.group(1) if assinatura else ''
    t('a assinatura fala de cada item que ficou',
      antes.count('|') == 2 and f'{sem_anexo.pk}:sem_evidencia' in antes, antes)

    ContestationCartDraft.objects.filter(user=gerente, exclusion=de_outra_loja).delete()
    depois = re.search(r'data-v="([^"]*)"', c.get(BALANCO).content.decode()).group(1)
    t('e muda quando o carrinho muda', depois != antes and depois.count('|') == 1, depois)

    t('o painel exige login', Client().get(BALANCO).status_code in (302, 403))

    print('\n== O QUE O PAINEL CUSTA ==')
    from django.test.utils import CaptureQueriesContext
    from django.db import connection
    from contestacao.views import _balanco_do_ciclo, _get_sync_window_state
    estado = _get_sync_window_state(gerente)
    with CaptureQueriesContext(connection) as cheio:
        _balanco_do_ciclo(gerente, estado)
    ContestationCartDraft.objects.filter(user=gerente).exclude(exclusion=sem_anexo).delete()
    with CaptureQueriesContext(connection) as magro:
        _balanco_do_ciclo(gerente, estado)
    t('o balanço cabe em poucas consultas', len(cheio) <= 10, len(cheio))
    t('e o custo não cresce com o tamanho do carrinho',
      len(cheio) == len(magro), (len(cheio), len(magro)))

    print('\n== A TELA ==')
    html = c.get('/contestacao/').content.decode()
    t('a barra do carrinho resume o que vai e o que fica',
      'bulkResumo' in html and 'ficam' in html and 'vão ser enviadas' in html)
    t('cada item do carrinho ganha o selo da situação',
      'situacaoDoItem' in html and 'Vai ser enviada' in html
      and 'Fica no carrinho: ' in html)
    t('a situação que só o servidor sabe vem do rascunho',
      'window.pendingSituacoes' in html and 'item.situacao' in html)
    t('e o rascunho sem motivo também entra no carrinho da tela',
      'window.pendingReasons[id] === undefined' in html)
    t('o painel se redesenha sem recarregar a página',
      'atualizarBalanco' in html and BALANCO in html)
    t('e não pede o painel quando nada mudou',
      'assinaturaDoCarrinho' in html and 'marca.dataset.v' in html)
    t('os textos dos motivos não estão em duas cópias no JS',
      html.count('MOTIVOS_CONFERENCIA = {') == 1)

    limpo = re.sub(r'\{%.*?%\}', 'tag', html, flags=re.S)
    limpo = re.sub(r'\{\{.*?\}\}', 'var', limpo, flags=re.S)
    blocos = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', limpo, flags=re.S)
    erros = []
    for bloco in blocos:
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as tmp:
            tmp.write(bloco)
            caminho = tmp.name
        saida = subprocess.run(['node', '--check', caminho], capture_output=True, text=True)
        if saida.returncode != 0:
            erros.append(saida.stderr[:300])
        os.unlink(caminho)
    t('o JavaScript da tela passa no node --check', not erros, erros[:1])
finally:
    apagados = 0
    from django.core.files.storage import default_storage
    for nome in subidos:
        try:
            if default_storage.exists(nome):
                default_storage.delete(nome)
                apagados += 1
        except Exception as exc:                                  # noqa: BLE001
            print(f'  ATENÇÃO: não deu para apagar {nome}: {exc}')
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print(f'\nrollback: nada gravado no banco; {apagados} arquivo(s) de teste apagados do MinIO.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
