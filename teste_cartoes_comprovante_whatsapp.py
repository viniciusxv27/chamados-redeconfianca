"""/cartoes/api/gasto/: comprovante que não chega não pode virar gasto "manual" calado.

Chamado #8232 (11/11/2026): a foto da DANFE foi mandada pelo WhatsApp, o download
falhou sem deixar rastro e o gasto saiu só com o texto da mensagem — valor R$ 0 e
"Pádua" (a cidade da loja) como estabelecimento. Agora:
- o motivo da falha fica no gasto (ia_dados.comprovante_perdido), no chamado e na resposta;
- o estabelecimento "adivinhado" do texto não é gravado quando a foto se perdeu;
- o comprovante pode vir no próprio corpo (foto_base64), sem depender de link;
- o download tenta de novo uma vez e aceita até 20 MB.

Nada sai daqui: IA e abertura do chamado são dublês; a rede é dublê. Transação desfeita no fim.
"""
import base64
import json
import os
import sys
from unittest import mock
from urllib.error import URLError

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
# Cache em memória: o `default` é o Redis remoto do dev.
import redeconfianca.settings as _cfg  # noqa: E402
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'} for n in _cfg.CACHES}
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.files.storage import InMemoryStorage
from django.db import transaction
from django.test import Client
from django.test.utils import override_settings

from cartoes import views as cartoes_views
from cartoes.models import Cartao, Gasto

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


TOKEN = 'token-de-teste-cartoes'
TELEFONE = '(00) 90000-0202'
TEXTO = 'Material para manutenção loja de Pádua. Tinta, rodapé, PU e ferramenta. Parcelado em 3x.'
IA_SO_TEXTO = {'valor': '', 'estabelecimento': 'Pádua', 'categoria': 'Manutenção', 'data': '',
               'descricao': 'Material para manutenção', 'faltou': ['valor'], 'tentativas': 4}
IA_COM_FOTO = {'valor': '947,63', 'estabelecimento': 'Blanc De Padua Materiais De Construcao Ltda',
               'documento': '02.804.127/0001-99', 'categoria': 'Manutenção', 'data': '2026-11-11',
               'descricao': 'Rodapé, esmalte, PU', 'tentativas': 1}
JPEG = b'\xff\xd8\xff\xe0' + b'0' * 64


class Resposta:
    def __init__(self, corpo, tipo='image/jpeg'):
        self._corpo, self.headers = corpo, {'Content-Type': tipo}

    def read(self, n=-1):
        return self._corpo if n < 0 else self._corpo[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


marcador = transaction.atomic()
marcador.__enter__()
try:
    assert not [1 for f in User.objects.filter(is_active=True).exclude(phone='').values_list('phone', flat=True)
                if cartoes_views._norm_br_phone(f) == '00900000202'], 'telefone do teste já usado no banco'
    dono = User.objects.create_user(username='zzcomp.dono', email='zzcomp.dono@exemplo-teste.local',
                                    password='S3nha!teste', first_name='ZZ', last_name='Comprovante', phone=TELEFONE)
    Cartao.objects.create(first4='5555', last4='3925', responsavel=dono, validade_mes=12, validade_ano=2030,
                          bandeira='MASTERCARD')
    c = Client()
    memoria = InMemoryStorage()

    def lancar(**dados):
        corpo = {'telefone': TELEFONE, 'descricao': TEXTO}
        corpo.update(dados)
        return c.post('/cartoes/api/gasto/', data=json.dumps(corpo), content_type='application/json',
                      HTTP_AUTHORIZATION=f'Bearer {TOKEN}')

    with override_settings(CARTOES_API_TOKEN=TOKEN), \
            mock.patch.object(cartoes_views, 'abrir_chamado_do_gasto', return_value=None), \
            mock.patch.object(Gasto._meta.get_field('foto'), 'storage', memoria):

        print('== FOTO MANDADA, DOWNLOAD FALHOU ==')
        with mock.patch.object(cartoes_views, 'urlopen', side_effect=URLError('timed out')) as rede, \
                mock.patch.object(cartoes_views, 'analyze_expense', return_value=IA_SO_TEXTO):
            r = lancar(foto_url='https://mmg.exemplo/comprovante.jpg')
        j = r.json()
        g = Gasto.objects.get(pk=j['gasto_id'])
        t('tenta baixar de novo (2 tentativas)', rede.call_count == 2, rede.call_count)
        t('a resposta avisa que o comprovante não chegou', 'não foi recebido' in (j.get('aviso_comprovante') or ''), j)
        t('o motivo fica guardado no gasto', 'timed out' in (g.ia_dados or {}).get('comprovante_perdido', ''), g.ia_dados)
        t('o chamado/descrição avisa para mandar de novo', 'Comprovante enviado, mas não recebido' in g.descricao, g.descricao)
        t('"Pádua" (palpite do texto) não vira estabelecimento', g.estabelecimento == '', g.estabelecimento)
        t('continua com valor a confirmar', g.valor == 0 and '[Valor a confirmar]' in g.descricao)

        print('\n== O LINK DEVOLVE UMA PÁGINA (NÃO A FOTO) ==')
        with mock.patch.object(cartoes_views, 'urlopen', return_value=Resposta(b'<html>erro</html>', 'text/html')), \
                mock.patch.object(cartoes_views, 'analyze_expense', return_value=IA_SO_TEXTO):
            j = lancar(foto_url='https://mmg.exemplo/x').json()
        t('o motivo diz o que veio', 'text/html' in (j.get('aviso_comprovante') or ''), j.get('aviso_comprovante'))

        print('\n== FOTO BAIXADA NORMALMENTE ==')
        with mock.patch.object(cartoes_views, 'urlopen', return_value=Resposta(JPEG)), \
                mock.patch.object(cartoes_views, 'analyze_expense', return_value=IA_COM_FOTO) as ia:
            j = lancar(foto_url='https://mmg.exemplo/ok.jpg').json()
        g = Gasto.objects.get(pk=j['gasto_id'])
        t('a IA recebe a imagem', ia.call_args.kwargs.get('image_bytes') == JPEG)
        t('gasto com foto, valor e estabelecimento da nota', g.origem == 'FOTO' and str(g.valor) == '947.63'
          and g.estabelecimento.startswith('Blanc'), (g.origem, g.valor, g.estabelecimento))
        t('sem aviso de comprovante perdido', j.get('aviso_comprovante') is None and 'comprovante_perdido' not in g.ia_dados)

        print('\n== FOTO NO PRÓPRIO CORPO (BASE64) ==')
        with mock.patch.object(cartoes_views, 'urlopen', side_effect=AssertionError('não devia baixar')), \
                mock.patch.object(cartoes_views, 'analyze_expense', return_value=IA_COM_FOTO) as ia:
            j = lancar(foto_base64='data:image/jpeg;base64,' + base64.b64encode(JPEG).decode()).json()
        g = Gasto.objects.get(pk=j['gasto_id'])
        t('base64 (data URI) chega à IA sem link', ia.call_args.kwargs.get('image_bytes') == JPEG and g.origem == 'FOTO',
          (g.origem, j))
        with mock.patch.object(cartoes_views, 'analyze_expense', return_value=IA_COM_FOTO) as ia:
            j = lancar(foto_base64=base64.b64encode(b'%PDF-1.4 zz').decode()).json()
        t('base64 puro de PDF é reconhecido como PDF', ia.call_args.kwargs.get('mime') == 'application/pdf', ia.call_args.kwargs.get('mime'))
        with mock.patch.object(cartoes_views, 'analyze_expense', return_value=IA_SO_TEXTO):
            j = lancar(foto_base64='%%%não é base64%%%').json()
        t('base64 quebrado vira aviso, não silêncio', j.get('aviso_comprovante') is not None, j)

        print('\n== SÓ TEXTO (NINGUÉM MANDOU FOTO) ==')
        with mock.patch.object(cartoes_views, 'analyze_expense', return_value=IA_SO_TEXTO):
            j = lancar().json()
        g = Gasto.objects.get(pk=j['gasto_id'])
        t('sem foto mandada não há aviso de comprovante perdido', j.get('aviso_comprovante') is None
          and 'Comprovante enviado' not in g.descricao and g.origem == 'MANUAL')

        print('\n== LIMITE DE TAMANHO ==')
        grande = Resposta(b'\xff\xd8' + b'0' * (20 * 1024 * 1024 + 10))
        motivos = []
        with mock.patch.object(cartoes_views, 'urlopen', return_value=grande):
            b, m = cartoes_views._baixar_comprovante('https://mmg.exemplo/grande.jpg', motivos)
        t('acima de 20 MB recusa e diz o motivo', b is None and '20 MB' in (motivos[-1] if motivos else ''), motivos)
        medio = Resposta(b'\xff\xd8' + b'0' * (12 * 1024 * 1024))
        with mock.patch.object(cartoes_views, 'urlopen', return_value=medio):
            b, m = cartoes_views._baixar_comprovante('https://mmg.exemplo/medio.jpg')
        t('foto de 12 MB (antes recusada no limite de 10) passa', b is not None)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
