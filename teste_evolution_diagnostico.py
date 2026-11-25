"""Evolution API: falha explicada e conferência da instância (manage.py conferir_evolution).

Em 19/11/2026 o WhatsApp da instância "alex" desconectou e em 24/11 a instância
foi apagada no servidor (a "robozinho" entrou no lugar), mas EVOLUTION_INSTANCE
continuou "alex": rotina, análise do ponto e cobrança de cursos gravaram só o
JSON cru da API ("Connection Closed" e depois "The "alex" instance does not
exist") e ninguém percebeu. Aqui se prova que o retorno passa a dizer o que
fazer e que a conferência acha a instância conectada e os grupos de loja dela.

Nada sai daqui: a rede é trocada por um dublê em todos os casos.
"""
import io
import json
import os
import sys
from unittest import mock
from urllib import error as urlerror

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.core.management import call_command
from django.test.utils import override_settings

import core.evolution as evolution

ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


CONFIG = dict(EVOLUTION_API_URL='https://evolution.exemplo', EVOLUTION_API_KEY='chave-falsa',
              EVOLUTION_INSTANCE='alex')
JID = '5500900000101@s.whatsapp.net'


def erro_http(codigo, corpo):
    def levanta(req, timeout=10):
        raise urlerror.HTTPError(req.full_url, codigo, 'x', {}, io.BytesIO(json.dumps(corpo).encode()))
    return levanta


class Resposta:
    def __init__(self, dados):
        self.status = 200
        self._dados = json.dumps(dados).encode()

    def read(self):
        return self._dados

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


fora_de_teste = mock.patch.object(sys, 'argv', ['manage.py', 'runserver'])

print('== FALHA EXPLICADA NO RETORNO DO ENVIO ==')
with fora_de_teste, override_settings(**CONFIG):
    sem_instancia = {'status': 404, 'error': 'Not Found',
                     'response': {'message': ['The "alex" instance does not exist']}}
    with mock.patch.object(evolution.urlrequest, 'urlopen', side_effect=erro_http(404, sem_instancia)):
        enviado, detalhe = evolution.enviar_texto(JID, 'x')
    t('instância apagada: não enviou', enviado is False)
    t('diz que a instância não existe e o nome dela', 'instância "alex" não existe' in detalhe, detalhe)
    t('aponta EVOLUTION_INSTANCE', 'EVOLUTION_INSTANCE' in detalhe, detalhe)
    t('cabe nos registros (até 160)', len(detalhe) <= 160, len(detalhe))

    for codigo, corpo in ((400, {'status': 400, 'response': {'message': ['Error: Connection Closed']}}),
                          (500, {'status': 500, 'response': {'message': 'Connection Closed'}})):
        with mock.patch.object(evolution.urlrequest, 'urlopen', side_effect=erro_http(codigo, corpo)):
            enviado, detalhe = evolution.enviar_texto(JID, 'x')
        t(f'WhatsApp desconectado (HTTP {codigo}): diz para ler o QR Code',
          enviado is False and 'desconectado' in detalhe and 'QR Code' in detalhe and '"alex"' in detalhe, detalhe)
        t(f'cabe nos registros (HTTP {codigo})', len(detalhe) <= 160, len(detalhe))

    numero_sem_whats = {'status': 400, 'response': {'message': [{'exists': False, 'number': '5500900000101'}]}}
    with mock.patch.object(evolution.urlrequest, 'urlopen', side_effect=erro_http(400, numero_sem_whats)):
        enviado, detalhe = evolution.enviar_texto(JID, 'x')
    t('número sem WhatsApp continua com o corpo cru (falha definitiva da rotina)',
      enviado is False and detalhe.startswith('HTTP 400:') and '"exists": false' in detalhe, detalhe)
    from rotina.whatsapp import falha_definitiva
    t('e a rotina ainda reconhece como definitiva', falha_definitiva(detalhe), detalhe)

print('== ESTADO DA INSTÂNCIA ==')
with fora_de_teste, override_settings(**CONFIG):
    with mock.patch.object(evolution.urlrequest, 'urlopen', side_effect=erro_http(404, sem_instancia)):
        r = evolution.estado_instancia()
    t('instância apagada: existe=False', r['existe'] is False and r['instancia'] == 'alex', r)
    with mock.patch.object(evolution.urlrequest, 'urlopen',
                           return_value=Resposta({'instance': {'instanceName': 'alex', 'state': 'close'}})):
        r = evolution.estado_instancia()
    t('instância fechada: existe e estado close', r['existe'] is True and r['estado'] == 'close', r)
    pedidos = []

    def responde_aberta(req, timeout=10):
        pedidos.append(req)
        return Resposta({'instance': {'instanceName': 'robozinho', 'state': 'open'}})

    with mock.patch.object(evolution.urlrequest, 'urlopen', side_effect=responde_aberta):
        r = evolution.estado_instancia('robozinho')
    t('consulta outra instância pelo nome', r['estado'] == 'open'
      and pedidos[0].full_url == 'https://evolution.exemplo/instance/connectionState/robozinho'
      and pedidos[0].get_method() == 'GET', pedidos and pedidos[0].full_url)

    lista = [{'name': 'alex 2', 'connectionStatus': 'close', 'ownerJid': '5521900000000@s.whatsapp.net'},
             {'name': 'robozinho', 'connectionStatus': 'open', 'ownerJid': '5527900000000@s.whatsapp.net',
              'profileName': 'Robozinho', 'createdAt': '2026-11-24T10:35:58.017Z'}]
    with mock.patch.object(evolution.urlrequest, 'urlopen', return_value=Resposta(lista)):
        sucesso, instancias, _ = evolution.listar_instancias()
    t('lista as instâncias com estado e número', sucesso and [i['nome'] for i in instancias] == ['alex 2', 'robozinho']
      and instancias[1]['estado'] == 'open' and instancias[1]['numero'] == '5527900000000', instancias)

    with override_settings(EVOLUTION_API_KEY=''), \
            mock.patch.object(evolution.urlrequest, 'urlopen', side_effect=AssertionError('tocou a rede')):
        r = evolution.estado_instancia()
    t('sem configuração, nem consulta', r['existe'] is None and 'não configurada' in r['detalhe'], r)

print('== manage.py conferir_evolution ==')


class GrupoFalso:
    def __init__(self, setor, grupo):
        self.setor, self.grupo = setor, grupo


grupos_loja = [GrupoFalso('Loja A', '120363000000000001'), GrupoFalso('Loja B', '120363000000000002@g.us')]
saida = io.StringIO()
with override_settings(**CONFIG), \
        mock.patch.object(evolution, 'estado_instancia',
                          return_value={'instancia': 'alex', 'existe': False, 'estado': '', 'detalhe': 'x'}), \
        mock.patch.object(evolution, 'listar_instancias', return_value=(True, instancias, '')), \
        mock.patch.object(evolution, 'listar_grupos',
                          return_value=(True, [{'id': '120363000000000001@g.us', 'nome': 'A', 'membros': 3}], '')) as grupos, \
        mock.patch('core.management.commands.conferir_evolution.grupos_de_loja', return_value=grupos_loja):
    call_command('conferir_evolution', stdout=saida)
texto = saida.getvalue()
t('diz que a instância configurada não existe', 'alex' in texto and 'não existe' in texto, texto)
t('sugere a conectada', 'robozinho' in texto and 'EVOLUTION_INSTANCE=robozinho' in texto, texto)
t('conta os grupos de loja da conectada', '1 de 2 grupos de loja' in texto, texto)
t('e diz de qual grupo ela não participa', 'Loja B' in texto, texto)
t('consultou os grupos da conectada',
  any(c.kwargs.get('instancia') == 'robozinho' for c in grupos.call_args_list), grupos.call_args_list)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
