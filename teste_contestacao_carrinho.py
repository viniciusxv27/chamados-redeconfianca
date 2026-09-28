"""Contestação: dupla conferência do carrinho e envio em pedaços.

Pedidos:

- "quando o usuário envia, às vezes acontece de chegar menos vendas do que
  realmente o carrinho do usuário — faça uma dupla conferência antes de enviar";
- "direto dá erro de conexão e as vendas chegam corretamente para algumas
  pessoas, consegue corrigir?".

As duas coisas tinham a mesma raiz: a tela mandava UM POST com todas as vendas
e todas as fotos. Pedido grande estourava o proxy (o tal "erro de conexão") e,
quando passava, o servidor criava só o que dava — item sem motivo ou sem
evidência era descartado calado — e a tela limpava o carrinho inteiro assim
mesmo. Daí "mandei 40 e chegaram 36", sem ninguém saber quais.

O que este teste cobre:

- a conferência (`/contestacao/carrinho/conferir/`): o que está pronto e o que
  não está, item a item e com o motivo;
- o envio em pedaços (JSON com ids): usa o motivo e o anexo já guardados no
  rascunho do servidor e responde o que criou e o que ignorou;
- o carrinho só perde o que virou contestação de verdade;
- reenviar o mesmo item depois de um "erro de conexão" não duplica nada;
- o caminho antigo (FormData) continua funcionando;
- a tela: confere antes de enviar e não manda mais tudo de uma vez.

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
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ct-carrinho'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ct-carrinho-2'},
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
from django.utils import timezone

from communications.models import CommunicationGroup
from contestacao.models import (Contestation, ContestationCartDraft, ContestationHistory,
                                ExclusionRecord, ExclusionSyncBatch, TipoBase)
from users.models import Sector

User = get_user_model()
ok = fail = 0
subidos = []
CONFERIR = '/contestacao/carrinho/conferir/'
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
    setor = Sector.objects.create(name='ZZ Loja Carrinho')
    chefe = User.objects.create_user(
        username='zzcc.chefe', email='zzcc.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)
    gerentes, _ = CommunicationGroup.objects.get_or_create(name='GERENTES', defaults={'created_by': chefe})
    gerente = User.objects.create_user(
        username='zzcc.gerente', email='zzcc.gerente@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Gerente', hierarchy='PADRAO', sector=setor)
    gerente.communication_groups.add(gerentes)
    fora = User.objects.create_user(
        username='zzcc.fora', email='zzcc.fora@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Fora', hierarchy='PADRAO', sector=setor)

    # A janela de contestação abre com uma sincronização recente.
    lote = ExclusionSyncBatch.objects.create(record_type=TipoBase.EXCLUSAO, record_count=6,
                                             created_by=chefe)
    ContestationHistory.objects.create(action='synced', user=chefe, notes='ZZ sync do teste')

    def venda(numero, filial='ZZ LOJA CARRINHO'):
        return ExclusionRecord.objects.create(
            sync_batch=lote, filial=filial, vendedor=f'ZZ Vendedor {numero}', receita=100 + numero,
            pilar='ZZ', numero_venda=f'ZZC{numero}', data_venda='01/09/2026',
            nome_cliente=f'ZZ Cliente {numero}', record_type=TipoBase.EXCLUSAO)

    pronta1, pronta2, pronta3 = venda(1), venda(2), venda(3)
    sem_motivo = venda(4)
    sem_anexo = venda(5)
    de_outra_loja = venda(6, filial='ZZ OUTRA FILIAL')

    def rascunho(registro, motivo='ZZ motivo', com_anexo=True):
        d = ContestationCartDraft.objects.create(user=gerente, exclusion=registro, reason=motivo)
        if com_anexo:
            d.attachment.save(f'ev{registro.pk}.jpg', foto(), save=True)
            subidos.append(d.attachment.name)
        return d

    for registro in (pronta1, pronta2, pronta3):
        rascunho(registro)
    rascunho(sem_motivo, motivo='')
    rascunho(sem_anexo, com_anexo=False)
    rascunho(de_outra_loja)

    c = Client(); c.force_login(gerente)
    cf = Client(); cf.force_login(fora)

    def conferir(ids, com_arquivo_local=(), cliente=None):
        return (cliente or c).post(CONFERIR, data=json.dumps({
            'ids': list(ids), 'com_arquivo_local': list(com_arquivo_local)}),
            content_type='application/json')

    print('== A DUPLA CONFERÊNCIA ==')
    ids = [pronta1.pk, pronta2.pk, sem_motivo.pk, sem_anexo.pk, de_outra_loja.pk, 99999999]
    r = conferir(ids)
    dados = r.json()
    t('a conferência responde', r.status_code == 200 and dados['success'], r.content[:200])
    situacao = {item['id']: item['situacao'] for item in dados['itens']}
    t('item com motivo e anexo está pronto',
      situacao[pronta1.pk] == 'pronta' and situacao[pronta2.pk] == 'pronta', situacao)
    t('sem motivo é apontado', situacao[sem_motivo.pk] == 'sem_motivo')
    t('sem evidência também', situacao[sem_anexo.pk] == 'sem_evidencia')
    t('venda de outra loja fica fora do escopo', situacao[de_outra_loja.pk] == 'fora_do_escopo')
    t('id que não existe é dito como não encontrado', situacao[99999999] == 'nao_encontrada')
    t('a lista de prontas traz só as prontas',
      sorted(dados['prontas']) == sorted([pronta1.pk, pronta2.pk]), dados['prontas'])
    t('e diz o tamanho do pedaço do envio', isinstance(dados['lote'], int) and dados['lote'] >= 1,
      dados.get('lote'))
    dentro = {'pronta', 'sem_motivo', 'sem_evidencia', 'ja_contestada'}
    t('a linha do que está no escopo traz vendedor, filial e valor para a tela',
      all(i['vendedor'] and i['filial'] and i['valor'] for i in dados['itens'] if i['situacao'] in dentro),
      [i for i in dados['itens'] if i['situacao'] in dentro and not i['vendedor']])
    t('e a de fora do escopo não devolve dado da venda (a tela usa o que já tinha)',
      all(not i['vendedor'] for i in dados['itens'] if i['situacao'] not in dentro))

    r = conferir([sem_anexo.pk], com_arquivo_local=[sem_anexo.pk])
    t('arquivo que ainda só está no navegador conta como evidência',
      r.json()['itens'][0]['situacao'] == 'pronta', r.json()['itens'][0])

    t('quem não pode contestar não confere', conferir([pronta1.pk], cliente=cf).status_code == 403)

    print('\n== O ENVIO EM PEDAÇOS ==')
    r = c.post(ENVIAR, data=json.dumps({'ids': [pronta1.pk, pronta2.pk]}),
               content_type='application/json')
    dados = r.json()
    t('o envio por JSON funciona', r.status_code == 200 and dados['success'], r.content[:200])
    t('criou as duas', dados['created'] == 2 and sorted(dados['criadas']) == sorted([pronta1.pk, pronta2.pk]),
      dados)
    t('usando o motivo e o anexo do rascunho do servidor',
      Contestation.objects.filter(exclusion=pronta1).first().reason == 'ZZ motivo'
      and bool(Contestation.objects.filter(exclusion=pronta1).first().attachment))
    for c_ in Contestation.objects.filter(exclusion__in=[pronta1, pronta2]):
        if c_.attachment:
            subidos.append(c_.attachment.name)
    t('e tirou do carrinho só o que foi criado',
      not ContestationCartDraft.objects.filter(user=gerente, exclusion__in=[pronta1, pronta2]).exists()
      and ContestationCartDraft.objects.filter(user=gerente).count() == 4)

    r = c.post(ENVIAR, data=json.dumps({'ids': [sem_motivo.pk, sem_anexo.pk, de_outra_loja.pk]}),
               content_type='application/json')
    dados = r.json()
    motivos = {i['id']: i['motivo'] for i in dados['ignoradas']}
    t('o que não está pronto volta com o motivo, item a item',
      dados['created'] == 0 and motivos[sem_motivo.pk] == 'sem_motivo'
      and motivos[sem_anexo.pk] == 'sem_evidencia'
      and motivos[de_outra_loja.pk] == 'fora_do_escopo', dados)
    t('e continua no carrinho, com o que já foi digitado',
      ContestationCartDraft.objects.filter(user=gerente, exclusion=sem_anexo).exists())

    print('\n== "DEU ERRO DE CONEXÃO, MAS CHEGOU" ==')
    r = c.post(ENVIAR, data=json.dumps({'ids': [pronta1.pk]}), content_type='application/json')
    dados = r.json()
    t('reenviar o mesmo item não cria outra contestação',
      dados['created'] == 0 and Contestation.objects.filter(exclusion=pronta1).count() == 1, dados)
    t('e a resposta diz que já estava contestada',
      dados['ignoradas'][0]['motivo'] == 'ja_contestada', dados['ignoradas'])
    r = conferir([pronta1.pk])
    t('a conferência depois do reenvio também diz isso (a tela tira do carrinho)',
      r.json()['itens'][0]['situacao'] == 'ja_contestada')

    print('\n== O CAMINHO ANTIGO CONTINUA ==')
    antiga = venda(7)
    r = c.post(ENVIAR, {'count': 1, 'exclusion_id_0': antiga.pk,
                        'reason_0': 'ZZ motivo do formulário', 'file_0': foto('antiga.jpg')})
    dados = r.json()
    criada = Contestation.objects.filter(exclusion=antiga).first()
    if criada and criada.attachment:
        subidos.append(criada.attachment.name)
    t('FormData ainda cria a contestação', dados.get('created') == 1 and criada is not None, dados)
    t('com o motivo que veio no formulário', criada.reason == 'ZZ motivo do formulário')

    r = c.post(ENVIAR, data=json.dumps({'ids': []}), content_type='application/json')
    t('lista vazia é recusada com aviso', r.status_code == 400 and not r.json()['success'])

    print('\n== A TELA ==')
    html = c.get('/contestacao/').content.decode()
    t('o carrinho confere antes de enviar', 'carrinho/conferir' in html
      and 'conferirCarrinhoNoServidor' in html)
    t('e mostra a janela de conferência', 'cartCheckModal' in html and 'Conferência do pedido' in html)
    t('o envio vai em pedaços', 'confirmarEnvioConferido' in html and 'dados.lote' in html)
    t('cada evidência sobe sozinha antes do envio', 'garantirEvidenciasNoServidor' in html)
    t('e o envio tenta de novo quando a rede falha', 'comTentativas' in html)
    t('não manda mais tudo de uma vez',
      'formData.append(\'count\', ids.length)' not in html and 'file_\' + i' not in html)
    t('o que falhou é reconferido no servidor', 'falhados' in html and 'ja_contestada' in html)

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
            erros.append(saida.stderr[:200])
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
