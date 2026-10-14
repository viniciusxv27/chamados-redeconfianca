"""Cartões: a IA insiste até identificar o gasto — em imagem ou em PDF.

Pedido (29/09/2026), sobre `/cartoes/api/gasto/`: "quando a IA não conseguir
processar, faça a tentativa novamente até identificar, seja PDF ou Imagem que o
usuário mandar, deve identificar o valor da compra, categoria e descrição".

O que este teste cobre:

- resposta incompleta não encerra o assunto: a IA é chamada de novo, dizendo o
  que faltou, e o que cada rodada trouxe é aproveitado;
- erro passageiro (timeout, 500) também vira nova tentativa;
- a insistência sobe de modelo e a última rodada pede o essencial;
- modelo que não existe na conta cai no reserva, sem perder a rodada;
- esgotadas as rodadas, volta o que deu com a lista do que faltou;
- PDF é reconhecido pelo conteúdo (o WhatsApp manda `octet-stream`), vira
  texto + página em PNG, e as duas coisas vão para a IA;
- a API aceita o PDF, guarda a primeira página como comprovante e abre o
  chamado com valor, categoria e descrição;
- sem identificar o valor nem depois de tudo, o chamado ainda é aberto com
  "valor a confirmar" — não se perde o lançamento.

A OpenAI é dublada (nenhuma chamada real). Transação desfeita no fim.
"""
import io
import json
import os
import sys
from types import SimpleNamespace
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cart'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cart-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from cartoes import ai, views as cviews
from cartoes.models import Cartao, Gasto
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


# ── dublê da OpenAI ────────────────────────────────────────────────────────
class ClienteFalso:
    """Responde o roteiro, em ordem. Exceção no roteiro = erro naquela rodada."""

    def __init__(self, roteiro):
        self.roteiro = list(roteiro)
        self.pedidos = []

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.pedidos.append(kwargs)
        resposta = self.roteiro.pop(0) if self.roteiro else {}
        if isinstance(resposta, Exception):
            raise resposta
        conteudo = resposta if isinstance(resposta, str) else json.dumps(resposta)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=conteudo))])


class NotFoundError(Exception):
    """Mesmo nome da exceção da OpenAI para modelo inexistente."""


def analisar(roteiro, **kwargs):
    cliente = ClienteFalso(roteiro)
    with mock.patch('openai.OpenAI', return_value=cliente), \
         mock.patch.object(ai, 'ESPERA_ENTRE_RODADAS', 0), \
         mock.patch.object(settings, 'OPENAI_API_KEY', 'sk-zz-teste'):
        dados = ai.analyze_expense(**kwargs)
    return dados, cliente


def texto_dos_pedidos(cliente, indice):
    partes = cliente.pedidos[indice]['messages'][1]['content']
    return ' '.join(p.get('text', '') for p in partes if p.get('type') == 'text')


def imagens_do_pedido(cliente, indice):
    partes = cliente.pedidos[indice]['messages'][1]['content']
    return [p for p in partes if p.get('type') == 'image_url']


# ── material de teste ──────────────────────────────────────────────────────
def pdf_com_texto(texto='CUPOM FISCAL  TOTAL R$ 128,90  POSTO CENTRO'):
    """PDF mínimo, de verdade, com texto extraível."""
    fluxo = f'BT /F1 12 Tf 20 150 Td ({texto}) Tj ET'.encode('latin-1')
    objetos = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R '
        b'/Resources << /Font << /F1 5 0 R >> >> >>',
        b'<< /Length ' + str(len(fluxo)).encode() + b' >>\nstream\n' + fluxo + b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    saida = bytearray(b'%PDF-1.4\n')
    posicoes = []
    for numero, corpo in enumerate(objetos, start=1):
        posicoes.append(len(saida))
        saida += f'{numero} 0 obj\n'.encode() + corpo + b'\nendobj\n'
    inicio_xref = len(saida)
    saida += f'xref\n0 {len(objetos) + 1}\n'.encode() + b'0000000000 65535 f \n'
    for posicao in posicoes:
        saida += f'{posicao:010d} 00000 n \n'.encode()
    saida += (f'trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n'
              f'{inicio_xref}\n%%EOF\n').encode()
    return bytes(saida)


def pdf_escaneado():
    """PDF que é só imagem — como um comprovante fotografado e convertido."""
    from PIL import Image
    saida = io.BytesIO()
    Image.new('RGB', (600, 400), 'white').save(saida, 'PDF')
    return saida.getvalue()


def imagem_jpeg():
    from PIL import Image
    saida = io.BytesIO()
    Image.new('RGB', (400, 300), 'white').save(saida, 'JPEG')
    return saida.getvalue()


COMPLETO = {'estabelecimento': 'Posto Centro', 'valor': 128.9, 'data': '2026-09-28',
            'categoria': 'Combustível', 'descricao': 'Abastecimento do carro da loja.',
            'confianca': 'alta', 'observacoes': ''}
SEM_VALOR = dict(COMPLETO, valor='', observacoes='valor ilegível')
SO_CATEGORIA = {'estabelecimento': '', 'valor': '', 'categoria': 'Alimentação',
                'descricao': '', 'observacoes': 'foto escura'}

print('== A IA INSISTE ATÉ IDENTIFICAR ==')
dados, cliente = analisar([COMPLETO], image_bytes=imagem_jpeg(), mime='image/jpeg')
t('acertando de primeira, não repete', len(cliente.pedidos) == 1 and dados['tentativas'] == 1,
  (len(cliente.pedidos), dados.get('tentativas')))
t('e devolve valor, categoria e descrição',
  str(dados['valor']) == '128.9' and dados['categoria'] == 'Combustível' and dados['descricao'],
  dados)
t('sem nada faltando', 'faltou' not in dados, dados.get('faltou'))

dados, cliente = analisar([SEM_VALOR, COMPLETO], image_bytes=imagem_jpeg())
t('resposta sem valor vira outra tentativa', len(cliente.pedidos) == 2, len(cliente.pedidos))
t('e o valor aparece na segunda', str(dados['valor']) == '128.9' and dados['tentativas'] == 2, dados)
t('a segunda tentativa diz o que faltou', 'valor' in texto_dos_pedidos(cliente, 1)
  and 'tentativa anterior' in texto_dos_pedidos(cliente, 1).lower(),
  texto_dos_pedidos(cliente, 1)[:160])

dados, cliente = analisar([RuntimeError('timeout falando com a OpenAI'), COMPLETO],
                          image_bytes=imagem_jpeg())
t('erro passageiro também vira nova tentativa',
  len(cliente.pedidos) == 2 and str(dados['valor']) == '128.9', dados)
t('e o erro não sobra na resposta', 'error' not in dados, dados.get('error'))

dados, cliente = analisar([SO_CATEGORIA, dict(COMPLETO, categoria='')], image_bytes=imagem_jpeg())
t('o que uma rodada achou não se perde na seguinte',
  dados['categoria'] == 'Alimentação' and str(dados['valor']) == '128.9', dados)

dados, cliente = analisar([SEM_VALOR, SEM_VALOR, SEM_VALOR, SEM_VALOR],
                          image_bytes=imagem_jpeg())
t('insiste até o fim das rodadas', len(cliente.pedidos) == len(ai.RODADAS), len(cliente.pedidos))
t('sobe de modelo no caminho',
  cliente.pedidos[0]['model'] == 'gpt-4o-mini' and cliente.pedidos[-1]['model'] == 'gpt-4o',
  [p['model'] for p in cliente.pedidos])
t('a última rodada pede o essencial',
  'última tentativa' in texto_dos_pedidos(cliente, len(ai.RODADAS) - 1).lower(),
  texto_dos_pedidos(cliente, len(ai.RODADAS) - 1)[:120])
t('e o que sobrou sem identificar vem dito', dados.get('faltou') == ['valor'], dados.get('faltou'))
t('com o resto aproveitado', dados['categoria'] == 'Combustível' and dados['descricao'])

dados, cliente = analisar([NotFoundError('The model `gpt-4o` does not exist'), COMPLETO],
                          image_bytes=imagem_jpeg())
t('modelo que não existe na conta cai no reserva',
  len(cliente.pedidos) == 2 and cliente.pedidos[1]['model'] == ai.MODELO_RESERVA,
  [p['model'] for p in cliente.pedidos])

dados, cliente = analisar([RuntimeError('500'), RuntimeError('500'),
                           RuntimeError('500'), RuntimeError('500')],
                          image_bytes=imagem_jpeg())
t('falhando tudo, avisa o erro e quantas tentativas foram',
  dados.get('error') and dados.get('tentativas') == len(ai.RODADAS), dados)

vazio = ai.analyze_expense()
t('sem comprovante e sem texto, o recado é claro',
  'foto' in vazio.get('error', '').lower() and 'pdf' in vazio.get('error', '').lower(), vazio)
with mock.patch.object(settings, 'OPENAI_API_KEY', ''):
    sem_chave = ai.analyze_expense(manual_text='almoço 30 reais')
t('sem chave, também', 'OPENAI_API_KEY' in sem_chave.get('error', ''), sem_chave)

print('\n== PDF É LIDO COMO PDF ==')
pdf = pdf_com_texto()
t('reconhece pelo conteúdo, mesmo com o tipo errado',
  ai.e_pdf(pdf, 'application/octet-stream') and ai.e_pdf(pdf, '') and not ai.e_pdf(imagem_jpeg(), ''))
t('o texto do PDF sai', 'TOTAL R$ 128,90' in ai.texto_do_pdf(pdf), ai.texto_do_pdf(pdf)[:80])
paginas = ai.paginas_do_pdf(pdf)
t('e a página vira PNG', len(paginas) == 1 and paginas[0][:4] == b'\x89PNG', len(paginas))

anexo = ai.preparar_anexo(pdf, 'application/pdf')
t('o anexo de PDF leva texto e imagem',
  anexo['tipo'] == 'pdf' and anexo['texto'] and len(anexo['imagens']) == 1, anexo['tipo'])

dados, cliente = analisar([COMPLETO], image_bytes=pdf, mime='application/octet-stream')
t('a IA recebe o texto do PDF', 'TOTAL R$ 128,90' in texto_dos_pedidos(cliente, 0))
t('e a página como imagem', len(imagens_do_pedido(cliente, 0)) == 1
  and imagens_do_pedido(cliente, 0)[0]['image_url']['url'].startswith('data:image/png;base64,'))
t('e o resultado diz que veio de PDF', dados.get('anexo') == 'pdf', dados.get('anexo'))

escaneado = pdf_escaneado()
anexo = ai.preparar_anexo(escaneado, 'application/pdf')
t('PDF escaneado (sem texto) ainda manda a página',
  anexo['tipo'] == 'pdf' and not anexo['texto'] and len(anexo['imagens']) == 1, anexo)

anexo = ai.preparar_anexo(imagem_jpeg(), 'image/jpeg')
t('imagem continua indo direto', anexo['tipo'] == 'imagem' and len(anexo['imagens']) == 1)
t('e formato exótico é convertido',
  ai.preparar_anexo(imagem_jpeg(), 'image/bmp')['imagens'][0][1] == 'image/png')

print('\n== O DOWNLOAD ACEITA PDF ==')


class RespostaFalsa:
    def __init__(self, dados, ctype):
        self.dados = dados
        self.headers = {'Content-Type': ctype}

    def read(self, n=None):
        return self.dados

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def baixar(dados, ctype):
    with mock.patch.object(cviews, 'urlopen', return_value=RespostaFalsa(dados, ctype)):
        return cviews._baixar_comprovante('https://exemplo.local/comprovante')


conteudo, mime = baixar(pdf, 'application/pdf')
t('PDF anunciado como PDF passa', conteudo == pdf and mime == 'application/pdf', mime)
conteudo, mime = baixar(pdf, 'application/octet-stream')
t('e PDF anunciado como octet-stream também',
  conteudo == pdf and mime == 'application/pdf', mime)
conteudo, mime = baixar(imagem_jpeg(), 'image/jpeg')
t('imagem continua passando', conteudo and mime == 'image/jpeg', mime)
conteudo, mime = baixar(b'<html>erro</html>', 'text/html')
t('página HTML não vira comprovante', conteudo is None and mime is None, (conteudo, mime))

from tickets.models import Category
print('\n== PELA API ==')
marcador = transaction.atomic()
marcador.__enter__()
try:
    # Comprovantes do gasto são tabela nova: a migração entra aqui e sai no rollback.
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    _executor = MigrationExecutor(connection)
    # Até a última migração do app (nunca um número fixo: com uma migração
    # mais nova aplicada, mirar a antiga desfaria a nova durante o teste).
    _ultima = _executor.loader.graph.leaf_nodes('cartoes')
    if _executor.migration_plan(_ultima):
        _executor.migrate(_ultima)
    # Nada vai para o S3: os arquivos do teste ficam em memória.
    from django.core.files.storage import InMemoryStorage
    from cartoes.models import GastoComprovante
    from tickets.models import TicketAttachment
    _memoria = InMemoryStorage()
    for _campo in (GastoComprovante._meta.get_field('arquivo'), Gasto._meta.get_field('foto'),
                   TicketAttachment._meta.get_field('file')):
        _campo.storage = _memoria
    loja = Sector.objects.create(name='ZZ Loja dos Cartões')
    dono = User.objects.create_user(
        username='zzc.dono', email='zzc.dono@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZC', last_name='Dono', sector=loja, phone='(27) 99999-1234')
    cartao = Cartao.objects.create(apelido='ZZ Cartão', first4='1234', last4='4321',
                                   validade_mes=12, validade_ano=2030, bandeira='VISA',
                                   responsavel=dono, ativo=True)
    token = 'zz-token-do-teste'
    c = Client()

    def chamar(payload, roteiro, comprovante=None, mime='application/pdf'):
        cliente = ClienteFalso(roteiro)
        with mock.patch('openai.OpenAI', return_value=cliente), \
             mock.patch.object(ai, 'ESPERA_ENTRE_RODADAS', 0), \
             mock.patch.object(settings, 'OPENAI_API_KEY', 'sk-zz-teste'), \
             mock.patch.object(settings, 'CARTOES_API_TOKEN', token, create=True), \
             mock.patch.object(cviews, '_baixar_comprovante',
                               return_value=(comprovante, mime) if comprovante else (None, None)), \
             mock.patch.object(cviews, 'abrir_chamado_do_gasto', return_value=None):
            r = c.post('/cartoes/api/gasto/', json.dumps(payload),
                       content_type='application/json',
                       HTTP_AUTHORIZATION=f'Bearer {token}')
        try:
            return r.status_code, r.json(), cliente
        except Exception:
            return r.status_code, {}, cliente

    status, resposta, cliente = chamar(
        {'telefone': '27999991234', 'foto_url': 'https://exemplo.local/nota.pdf'},
        [SEM_VALOR, COMPLETO], comprovante=pdf)
    if status == 401:
        print('  (a API exige outro token neste ambiente: o resto do bloco não roda)')
    else:
        t('a API aceita o comprovante em PDF', status == 200 and resposta.get('success'),
          (status, resposta))
        t('insistindo até achar o valor', resposta.get('ia_tentativas') == 2,
          resposta.get('ia_tentativas'))
        t('o gasto sai com o valor identificado',
          float(resposta.get('valor') or 0) == 128.9, resposta.get('valor'))
        gasto = Gasto.objects.filter(id=resposta.get('gasto_id')).first()
        t('com categoria', gasto and gasto.categoria_gasto == 'Combustível',
          gasto and gasto.categoria_gasto)
        t('e descrição', gasto and 'Abastecimento' in gasto.descricao, gasto and gasto.descricao)
        t('o comprovante guardado é a página do PDF em imagem',
          gasto and gasto.foto and gasto.foto.name.endswith('.png'), gasto and str(gasto.foto))
        t('e a resposta diz que veio um PDF', resposta.get('comprovante') == 'pdf',
          resposta.get('comprovante'))
        if gasto and gasto.foto:
            gasto.foto.delete(save=False)         # o MinIO é compartilhado

        status, resposta, _ = chamar(
            {'telefone': '27999991234', 'foto_url': 'https://exemplo.local/nota.pdf'},
            [SEM_VALOR, SEM_VALOR, SEM_VALOR, SEM_VALOR], comprovante=pdf)
        t('sem identificar o valor, o chamado ainda é aberto',
          status == 200 and resposta.get('success') and float(resposta.get('valor') or -1) == 0,
          (status, resposta.get('valor')))
        t('avisando que o valor ficou a confirmar', 'confirmar' in (resposta.get('aviso') or ''),
          resposta.get('aviso'))
        t('e dizendo o que a IA não conseguiu', resposta.get('ia_faltou') == ['valor'],
          resposta.get('ia_faltou'))
        gasto = Gasto.objects.filter(id=resposta.get('gasto_id')).first()
        if gasto and gasto.foto:
            gasto.foto.delete(save=False)
    print('\n== PELA TELA (o mesmo motor) ==')
    from django.core.files.uploadedfile import SimpleUploadedFile

    dono.is_superuser = True
    dono.save(update_fields=['is_superuser'])
    ct = Client()
    ct.force_login(dono)

    cliente = ClienteFalso([COMPLETO])
    with mock.patch('openai.OpenAI', return_value=cliente), \
         mock.patch.object(ai, 'ESPERA_ENTRE_RODADAS', 0), \
         mock.patch.object(settings, 'OPENAI_API_KEY', 'sk-zz-teste'):
        r = ct.post(f'/cartoes/{cartao.pk}/gasto/analisar/',
                    {'foto': SimpleUploadedFile('nota.pdf', pdf, content_type='application/pdf')})
    t('a tela analisa PDF', r.status_code == 200 and r.json().get('anexo') == 'pdf',
      (r.status_code, r.content[:120]))
    t('mandando o texto do PDF para a IA',
      'TOTAL R$ 128,90' in texto_dos_pedidos(cliente, 0))

    r = ct.post(f'/cartoes/{cartao.pk}/gasto/', {
        'valor': '128,74', 'descricao': 'ZZ gasto de teste', 'estabelecimento': 'ZZ Posto',
        'categoria_gasto': 'Combustível', 'data_gasto': '2026-09-28',
        'foto': SimpleUploadedFile('nota.pdf', pdf, content_type='application/pdf'),
    }, follow=True)
    salvo = Gasto.objects.filter(descricao='ZZ gasto de teste').first()
    t('e guarda o PDF como a primeira página em imagem',
      salvo and salvo.foto and salvo.foto.name.endswith('.png'), salvo and str(salvo.foto))

    print('\n== VÁRIOS COMPROVANTES DO MESMO GASTO ==')
    from io import BytesIO
    from PIL import Image

    def png(cor):
        saida = BytesIO()
        Image.new('RGB', (60, 40), cor).save(saida, 'PNG')
        return saida.getvalue()

    dados, cliente = analisar([COMPLETO], anexos=[(png((200, 0, 0)), 'image/png'), (pdf, 'application/pdf')])
    pedido = texto_dos_pedidos(cliente, 0)
    imagens = [p for p in cliente.pedidos[0]['messages'][1]['content'] if p.get('type') == 'image_url']
    t('a IA recebe os dois comprovantes, cada um identificado', 'Comprovante 1:' in pedido
      and 'Comprovante 2:' in pedido and len(imagens) >= 2, pedido[:300])
    t('e a regra de que são do MESMO gasto (não somar o mesmo pagamento duas vezes)',
      'MESMO gasto' in pedido and 'não some o mesmo pagamento' in pedido)
    t('o texto do PDF vem marcado com o número do comprovante', '[Comprovante 2]' in pedido)
    t('a resposta diz quantos comprovantes leu', dados.get('comprovantes') == 2 and dados.get('anexo') == 'misto', dados)
    dados, cliente = analisar([COMPLETO], image_bytes=png((0, 0, 200)), mime='image/png')
    t('um comprovante só continua igual (sem a regra de vários)', 'MESMO gasto' not in texto_dos_pedidos(cliente, 0)
      and dados.get('comprovantes') == 1)

    cliente = ClienteFalso([COMPLETO])
    with mock.patch('openai.OpenAI', return_value=cliente), \
         mock.patch.object(ai, 'ESPERA_ENTRE_RODADAS', 0), \
         mock.patch.object(settings, 'OPENAI_API_KEY', 'sk-zz-teste'):
        r = ct.post(f'/cartoes/{cartao.pk}/gasto/analisar/', {'foto': [
            SimpleUploadedFile('nota.pdf', pdf, content_type='application/pdf'),
            SimpleUploadedFile('maquininha.png', png((0, 200, 0)), content_type='image/png')]})
    t('a tela manda os dois comprovantes para a IA', r.status_code == 200 and r.json().get('comprovantes') == 2,
      r.content[:200])

    r = ct.post(f'/cartoes/{cartao.pk}/gasto/', {
        'valor': '99,90', 'descricao': 'ZZ gasto com dois comprovantes', 'estabelecimento': 'ZZ Loja',
        'categoria_gasto': 'Material', 'data_gasto': '2026-10-10',
        'foto': [SimpleUploadedFile('maquininha.png', png((0, 200, 0)), content_type='image/png'),
                 SimpleUploadedFile('nota.pdf', pdf, content_type='application/pdf')],
    }, follow=True)
    duplo = Gasto.objects.filter(descricao='ZZ gasto com dois comprovantes').first()
    comps = list(duplo.comprovantes.all()) if duplo else []
    t('o gasto guarda os dois comprovantes, na ordem', [c.nome_original for c in comps] == ['maquininha.png', 'nota.pdf'],
      [c.nome_original for c in comps])
    t('o PDF fica como PDF (e o primeiro vira a foto do extrato)', comps and comps[1].e_pdf
      and comps[1].content_type == 'application/pdf' and duplo.foto)
    if duplo and duplo.ticket_id:
        anexos_chamado = sorted(TicketAttachment.objects.filter(ticket_id=duplo.ticket_id)
                                .values_list('original_filename', flat=True))
        t('o chamado leva os dois comprovantes', anexos_chamado == ['maquininha.png', 'nota.pdf'], anexos_chamado)
    else:
        print('  (categoria 99 de chamados não existe neste banco: anexos do chamado não conferidos)')
    html = ct.get(f'/cartoes/{cartao.pk}/').content.decode()
    t('o extrato mostra os comprovantes numerados', 'Abrir maquininha.png' in html and 'Abrir nota.pdf' in html)
    html = ct.get(f'/cartoes/{cartao.pk}/gasto/').content.decode()
    t('a tela aceita vários arquivos e tem "Adicionar outro comprovante"', 'multiple' in html
      and 'Adicionar outro comprovante' in html)

    print('\n== CNPJ/CPF DA LOJA ==')
    dados, _ = analisar([dict(COMPLETO, documento='11.222.333/0001-81')], manual_text='posto')
    t('a IA devolve o CNPJ do estabelecimento, formatado', dados.get('documento') == '11.222.333/0001-81', dados.get('documento'))
    dados, _ = analisar([dict(COMPLETO, documento='11222333000182')],
                        image_bytes=pdf_com_texto('POSTO CENTRO CNPJ 11.222.333/0001-81 TOTAL R$ 128,90'),
                        mime='application/pdf')
    t('CNPJ com dígito errado é descartado e sai do texto do PDF', dados.get('documento') == '11.222.333/0001-81',
      dados.get('documento'))
    dados, _ = analisar([dict(COMPLETO, documento='')],
                        image_bytes=pdf_com_texto('CPF DO CONSUMIDOR 529.982.247-25 TOTAL R$ 128,90'),
                        mime='application/pdf')
    t('o CPF do consumidor não vira documento da loja', dados.get('documento') == '', dados.get('documento'))
    t('pedimos o documento à IA (e avisamos para não usar o do consumidor)',
      'documento' in ai._USER_INSTRUCTION and 'CPF do consumidor' in ai._USER_INSTRUCTION)

    status, resposta, _ = chamar({'telefone': '27999991234', 'foto_url': 'https://exemplo.local/nota.pdf'},
                                 [dict(COMPLETO, documento='11.222.333/0001-81')], comprovante=pdf)
    if status != 401:
        g = Gasto.objects.filter(id=resposta.get('gasto_id')).first()
        t('pela API o gasto guarda o CNPJ lido', g and g.documento_estabelecimento == '11.222.333/0001-81',
          g and g.documento_estabelecimento)
        if Category.objects.filter(id=cviews.CARTAO_CATEGORY_ID).exists() and g:
            chamado = cviews.abrir_chamado_do_gasto(cartao, g, dono)
            t('o chamado traz o CNPJ/CPF da loja', 'CNPJ/CPF do estabelecimento: 11.222.333/0001-81' in chamado.description,
              chamado.description)
        else:
            print('  (categoria 99 de chamados não existe neste banco: texto do chamado não conferido)')

    r = ct.post(f'/cartoes/{cartao.pk}/gasto/', {'valor': '50,00', 'estabelecimento': 'Loja X', 'descricao': 'ZZ doc errado',
                                                'data_gasto': '2026-09-30', 'documento_estabelecimento': '11.111.111/1111-11'})
    t('na tela, CNPJ inválido é recusado com aviso', 'não é válido' in r.content.decode()
      and not Gasto.objects.filter(descricao='ZZ doc errado').exists())
    with mock.patch.object(cviews, 'abrir_chamado_do_gasto', return_value=None):
        ct.post(f'/cartoes/{cartao.pk}/gasto/', {'valor': '50,00', 'estabelecimento': 'Loja X', 'descricao': 'ZZ doc certo',
                                                'data_gasto': '2026-09-30', 'documento_estabelecimento': '11222333000181'})
    g = Gasto.objects.filter(descricao='ZZ doc certo').first()
    t('e o válido é gravado formatado', g and g.documento_estabelecimento == '11.222.333/0001-81', g and g.documento_estabelecimento)
    html = ct.get(f'/cartoes/{cartao.pk}/gasto/').content.decode()
    t('o formulário tem o campo do CNPJ/CPF', 'name="documento_estabelecimento"' in html)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado; nenhuma chamada real à OpenAI.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
