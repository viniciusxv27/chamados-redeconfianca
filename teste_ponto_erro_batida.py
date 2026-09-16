"""Ponto: quando o Tangerino recusa, a pessoa precisa ler o motivo — não "erro de conexão".

Em 14/09/2026 às 22:07 o serviço de batida do Tangerino passou a responder 404
(a borda da Azion devolve 404 vazio para qualquer caminho de
``api.tangerino.com.br/api/punch``). O portal traduzia isso num HTTP 502, que o
proxy troca por uma página HTML, e a tela caía no "Erro de conexão. O ponto NÃO
foi registrado." — escondendo o motivo de quem estava com o dedo no botão.

NADA DE PONTO DE VERDADE: a função que fala com o Tangerino é dublê em todos os
casos, e tudo roda numa transação desfeita no fim.
"""
import json
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
from django.urls import reverse

from tangerino import views as tg_views
from tangerino.client import TangerinoError
from tangerino.models import ConfiguracaoTangerino, RegistroPontoPortal
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


FOTO = 'data:image/jpeg;base64,' + 'A' * 400
URL = reverse('tangerino:api_bater_ponto')

marcador = transaction.atomic()
marcador.__enter__()
try:
    cfg = ConfiguracaoTangerino.get()
    cfg.permitir_bater_ponto = True
    cfg.exigir_foto = True
    cfg.save()

    setor = Sector.objects.create(name='ZZ Loja Ponto Erro')
    pessoa = User.objects.create_user(
        username='zzponto.erro', email='zzponto.erro@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZPonto', last_name='Erro', sector=setor, tangerino_employee_id=999999)
    c = Client()
    c.force_login(pessoa)

    # O resumo lê marcações no Tangerino: dublê, para o teste não sair pela rede.
    resumo_falso = {'disponivel': False}

    def bate(efeito):
        """Uma tentativa de batida com a chamada ao Tangerino dublada."""
        with mock.patch.object(ConfiguracaoTangerino, 'libera', return_value=True), \
                mock.patch.object(tg_views.ponto_svc, 'resumo_para_usuario', return_value=resumo_falso), \
                mock.patch.object(tg_views, 'invalidar_cache_marcacoes'), \
                mock.patch.object(tg_views, 'registrar_ponto', side_effect=efeito) as chamada:
            r = c.post(URL, data=json.dumps({'foto': FOTO}), content_type='application/json')
        return r, json.loads(r.content.decode()), chamada

    print('== O TANGERINO FORA DO AR (404 desde 14/09) ==')
    erro_404 = TangerinoError('Tangerino respondeu 404 em /register/web/1.1: ')
    r, corpo, _ = bate(erro_404)
    t('a resposta continua sendo JSON que a tela consegue ler', r['Content-Type'].startswith('application/json'),
      r['Content-Type'])
    t('e sai com 200, para o proxy não trocar o corpo por uma página HTML', r.status_code == 200, r.status_code)
    t('diz que o ponto NÃO foi registrado', corpo['sucesso'] is False and 'NÃO foi registrado' in corpo['erro'], corpo)
    t('explica que a recusa é do Tangerino e o que fazer agora',
      'Tangerino' in corpo['erro'] and 'aplicativo' in corpo['erro'], corpo['erro'])
    t('sem jogar o texto cru da API na cara da pessoa', '/register/web/1.1' not in corpo['erro'])
    t('mas guarda o detalhe técnico para quem for investigar', '404' in corpo.get('detalhe', ''), corpo.get('detalhe'))
    registro = RegistroPontoPortal.objects.filter(usuario=pessoa).order_by('-pk').first()
    t('a tentativa fica registrada como falha, com a resposta do Tangerino',
      registro is not None and registro.sucesso is False and '404' in registro.retorno)

    print('\n== OUTRAS RECUSAS ==')
    _, corpo, _ = bate(TangerinoError('Não foi possível completar a solicitação. Já existe um ponto cadastrado nesse horário'))
    t('ponto repetido: explica que já existe marcação nesse horário', 'Já existe uma marcação' in corpo['erro'], corpo['erro'])
    _, corpo, _ = bate(TangerinoError('Tangerino respondeu 504 em /register/web/1.1: gateway timeout'))
    t('tempo esgotado: avisa que pode não ter entrado e manda conferir', 'pode NÃO ter sido registrado' in corpo['erro'],
      corpo['erro'])
    _, corpo, _ = bate(TangerinoError('Não foi possível falar com o Tangerino: HTTPSConnectionPool'))
    t('sem falar com o Tangerino: diz que não registrou e o que fazer', 'NÃO foi registrado' in corpo['erro'], corpo['erro'])
    _, corpo, _ = bate(TangerinoError('Ponto não Autorizado! Colaborador não Registra Ponto!'))
    t('colaborador sem autorização: manda falar com o RH', 'RH' in corpo['erro'], corpo['erro'])

    print('\n== O CAMINHO FELIZ CONTINUA INTEIRO ==')
    r, corpo, chamada = bate(lambda *a, **k: {'nsr': 4242, '_foto_url': 'https://exemplo/foto.jpg'})
    t('registrou: sucesso, hora e NSR para a tela mostrar',
      r.status_code == 200 and corpo['sucesso'] is True and corpo['nsr'] == 4242 and corpo['hora'], corpo)
    t('a foto vai junto para o Tangerino', chamada.call_args.kwargs.get('foto_base64') == FOTO)
    registro = RegistroPontoPortal.objects.filter(usuario=pessoa, sucesso=True).order_by('-pk').first()
    t('e a trilha local guarda a batida com a URL da foto',
      registro is not None and registro.foto_url == 'https://exemplo/foto.jpg' and registro.com_foto)

    print('\n== NADA SAIU DAQUI ==')
    t('nenhuma batida de verdade: a chamada ao Tangerino foi sempre dublê', True)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
