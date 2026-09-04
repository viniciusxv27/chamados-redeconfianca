"""Banco de Talentos: importar currículo, ler o PDF, procurar por vaga.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
from datetime import timedelta

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

from communications.models import CommunicationGroup
from curriculos.busca import procurar, separar_intencao
from curriculos.entrevistas import _nome_do_tema, ficha_da_entrevista
from curriculos.extrator import achar_cargos, achar_endereco, achar_nome, extrair
from curriculos.integracao import extrair_token, url_da_entrevista
from curriculos.models import ConfiguracaoCurriculos, Curriculo
from curriculos.pdf_de_teste import CURRICULO_EXEMPLO, pdf_com_texto
from curriculos.texto import normalizar
from reunioes.models import Reuniao
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
    print('== LER O PDF ==')
    lido = extrair(pdf_com_texto(CURRICULO_EXEMPLO),
                   cidades_conhecidas={'Viana', 'Serra', 'Vila Velha'})
    t('o PDF é legível', lido['legivel'])
    t('acha o nome', lido['nome'] == 'Maria Silva Santos', lido['nome'])
    t('acha o endereço', 'Palmeiras' in lido['endereco'], lido['endereco'])
    t('acha a cidade', lido['cidade'] == 'Viana', lido['cidade'])
    t('acha o bairro', lido['bairro'] == 'Centro', lido['bairro'])
    t('acha o telefone', '99876' in lido['telefone'], lido['telefone'])
    t('acha o e-mail', lido['email'] == 'maria.silva@exemplo.com', lido['email'])
    t('acha a experiência', 'Loja Central' in lido['experiencia'])
    t('a experiência para na próxima seção',
      'Ensino medio' not in lido['experiencia'])
    t('acha os cargos', 'vendedora' in lido['cargos'] and
      'operadora de caixa' in lido['cargos'], lido['cargos'])

    print('\n== O QUE NÃO DÁ PARA LER NÃO É INVENTADO ==')
    vazio = extrair(b'nao sou um pdf')
    t('arquivo ilegível não quebra', vazio['legivel'] is False)
    t('e volta tudo vazio', not vazio['nome'] and not vazio['endereco'])
    t('nome só com uma palavra não é aceito', achar_nome('Maria\nOBJETIVO') == '')
    t('título de seção não vira nome',
      achar_nome('CURRICULO\nDADOS PESSOAIS\nJoao Pedro Lima') == 'Joao Pedro Lima')

    print('\n== CIDADE COM UF ==')
    for texto, esperado in [('Vila Velha/ES', 'Vila Velha'), ('Serra - ES', 'Serra'),
                            ('Bairro: Centro\nViana/ES', 'Viana')]:
        t(f'{texto!r} -> {esperado}', achar_endereco(texto)[1] == esperado,
          achar_endereco(texto)[1])

    print('\n== SEPARAR FUNÇÃO DE LUGAR ==')
    lugares = {'Loja Viana', 'Viana', 'Loja Norte Sul', 'Norte Sul', 'Serra'}
    funcoes, locais = separar_intencao('vendedor para loja de viana', lugares)
    t('"vendedor" é função', funcoes == ['vendedor'], funcoes)
    t('"viana" é lugar', 'viana' in locais, locais)
    t('"loja" e "para" não viram termo', 'loja' not in funcoes and 'para' not in funcoes)

    funcoes, locais = separar_intencao('caixa norte sul', lugares)
    t('lugar de duas palavras é reconhecido', 'norte sul' in locais, locais)
    t('e a função sobra certa', funcoes == ['caixa'], funcoes)

    funcoes, locais = separar_intencao('gerente para loja de guarapari', lugares)
    t('lugar desconhecido depois de "loja" ainda é lugar',
      'guarapari' in locais, (funcoes, locais))

    print('\n== O BANCO E A BUSCA ==')
    area = Sector.objects.create(name='Loja ZZ Viana')
    chefe = User.objects.create_user(
        username='bt.chefe', email='bt.chefe@exemplo-teste.local',
        password='S3nha!teste', sector=area, first_name='Chefe', last_name='RH',
        is_superuser=True, is_staff=True, hierarchy='SUPERADMIN')

    def cadastrar(nome, cidade, cargos, experiencia='', situacao=Curriculo.Situacao.NOVO):
        return Curriculo.objects.create(
            nome=nome, cidade=cidade, cargos='\n'.join(cargos),
            experiencia=experiencia, situacao=situacao, enviado_por=chefe)

    maria = cadastrar('ZZ Maria Silva', 'Viana', ['vendedora', 'operadora de caixa'],
                      'Vendedora na Loja Central de Viana por dois anos.')
    joao = cadastrar('ZZ Joao Souza', 'Serra', ['consultor de vendas'],
                     'Consultor de vendas em telefonia.')
    ana = cadastrar('ZZ Ana Lima', 'Viana', ['estoquista'], 'Estoque e reposição.')
    pedro = cadastrar('ZZ Pedro Rocha', 'Viana', ['vendedor'],
                      'Vendedor de loja.', situacao=Curriculo.Situacao.CONTRATADO)

    t('o blob de busca é montado sozinho', 'viana' in maria.busca and 'vendedora' in maria.busca)
    t('e é sem acento', normalizar('José da Conceição') == 'jose da conceicao')

    r = procurar('vendedor para loja de viana', lugares_conhecidos=lugares | {'Serra'})
    nomes = [x['curriculo'].nome for x in r]
    t('acha alguém', bool(r), nomes)
    t('a vendedora de Viana vem primeiro', nomes and nomes[0] == 'ZZ Maria Silva', nomes)
    t('o consultor de vendas também entra (sinônimo)', 'ZZ Joao Souza' in nomes, nomes)
    t('o estoquista de Viana entra por causa do lugar', 'ZZ Ana Lima' in nomes, nomes)
    t('quem já foi contratado fica de fora', 'ZZ Pedro Rocha' not in nomes, nomes)
    t('a busca explica o porquê',
      any('já foi vendedor' in m or 'mora em viana' in m
          for x in r for m in x['motivos']), [x['motivos'] for x in r])

    t('quem casa função E lugar ganha de quem casa só um',
      next(x['nota'] for x in r if x['curriculo'].nome == 'ZZ Maria Silva')
      > next(x['nota'] for x in r if x['curriculo'].nome == 'ZZ Ana Lima'))

    # O banco de dev tem currículos de verdade importados pelo RH, então as
    # asserções olham só para os ZZ criados aqui — o que o teste quer provar é
    # o ranqueamento, não o tamanho do banco.
    def meus(resultados):
        return [x['curriculo'].nome for x in resultados
                if x['curriculo'].nome.startswith('ZZ ')]

    r = procurar('estoquista', lugares_conhecidos=lugares)
    t('busca só por função funciona', meus(r) == ['ZZ Ana Lima'], meus(r))

    r = procurar('', lugares_conhecidos=lugares)
    t('busca vazia devolve o banco disponível', len(meus(r)) == 3, meus(r))
    t('e sem o contratado', 'ZZ Pedro Rocha' not in meus(r))

    print('\n== A TELA ==')
    c = Client(); c.force_login(chefe)
    html = c.get('/talentos/').content.decode()
    t('o banco abre', 'Banco de Talentos' in html)
    t('tem a busca por vaga', 'vendedor para loja de viana' in html)
    t('tem a importação de PDF', 'name="curriculos"' in html)

    html = c.get('/talentos/?q=vendedor para loja de viana').content.decode()
    t('a busca mostra o resultado', 'ZZ Maria Silva' in html)
    t('e mostra como entendeu a frase', 'função:' in html and 'lugar:' in html)
    t('o contratado não aparece', 'ZZ Pedro Rocha' not in html)

    print('\n== IMPORTAR PELO PORTAL ==')
    from django.core.files.uploadedfile import SimpleUploadedFile
    antes = Curriculo.objects.count()
    r = c.post('/talentos/importar/', {
        'curriculos': SimpleUploadedFile('cv.pdf', pdf_com_texto(CURRICULO_EXEMPLO),
                                         'application/pdf')}, follow=True)
    t('o currículo entra no banco', Curriculo.objects.count() == antes + 1)
    novo = Curriculo.objects.order_by('-id').first()
    t('já com o nome lido', novo.nome == 'Maria Silva Santos', novo.nome)
    t('e a cidade lida', novo.cidade == 'Viana', novo.cidade)
    t('e já aparece na busca',
      novo.id in [x['curriculo'].id
                  for x in procurar('vendedora viana', lugares_conhecidos=lugares)])

    r = c.post('/talentos/importar/', {
        'curriculos': SimpleUploadedFile('foto.png', b'\x89PNG', 'image/png')}, follow=True)
    t('arquivo que não é PDF é recusado', 'só PDF' in r.content.decode())

    print('\n== CONTRATADO SAI DA BUSCA ==')
    r = c.post(f'/talentos/{maria.id}/atualizar/',
               {'situacao': Curriculo.Situacao.CONTRATADO}, follow=True)
    maria.refresh_from_db()
    t('a situação muda', maria.situacao == Curriculo.Situacao.CONTRATADO)
    t('registra a data', maria.contratado_em == timezone.localdate())
    t('e quem marcou', maria.contratado_por_id == chefe.id)
    t('some da busca',
      'ZZ Maria Silva' not in [x['curriculo'].nome
                               for x in procurar('vendedor viana', lugares_conhecidos=lugares)])
    t('a tela avisa que saiu das buscas', 'sai das buscas' in r.content.decode())

    print('\n== QUEM PODE ==')
    comum = User.objects.create_user(
        username='bt.comum', email='bt.comum@exemplo-teste.local',
        password='S3nha!teste', sector=area, first_name='Comum', last_name='RH',
        hierarchy='PADRAO')
    cc = Client(); cc.force_login(comum)
    r = cc.get('/talentos/', follow=True)
    t('quem não é do RH não entra', bool(r.redirect_chain), r.redirect_chain)
    t('e a tela explica', 'restrito ao RH' in r.content.decode())

    grupo = CommunicationGroup.objects.create(name='ZZ RH Talentos', created_by=chefe)
    cfg = ConfiguracaoCurriculos.get()
    cfg.grupos.add(grupo)
    comum.communication_groups.add(grupo)
    r = cc.get('/talentos/', follow=True)
    t('o grupo liberado entra', not r.redirect_chain, r.redirect_chain)

    r = cc.post(f'/talentos/{ana.id}/excluir/', follow=True)
    t('só SUPERADMIN exclui', Curriculo.objects.filter(id=ana.id).exists())

    print('\n== ENTREVISTA VIRA FICHA ==')
    reuniao = Reuniao.objects.create(
        titulo='Entrevista — Carla Mendes', inicio=timezone.now() + timedelta(days=1),
        organizador=chefe, tipo=Reuniao.ENTREVISTA)
    ficha = ficha_da_entrevista(reuniao, autor=chefe)
    t('a entrevista abre ficha no banco', ficha is not None)
    t('o nome sai do tema', ficha and ficha.nome == 'Carla Mendes', ficha.nome if ficha else '')
    t('nasce em entrevista', ficha and ficha.situacao == Curriculo.Situacao.ENTREVISTA)
    t('e fica ligada à reunião', ficha and ficha.reuniao_id == reuniao.id)

    de_novo = ficha_da_entrevista(reuniao, autor=chefe)
    t('remarcar não cria segunda ficha', de_novo.id == ficha.id)
    t('e o banco tem uma só', Curriculo.objects.filter(reuniao=reuniao).count() == 1)

    comum_reuniao = Reuniao.objects.create(
        titulo='ZZ Alinhamento', inicio=timezone.now() + timedelta(days=1),
        organizador=chefe)
    t('reunião comum não abre ficha', ficha_da_entrevista(comum_reuniao) is None)

    for tema, esperado in [('Entrevista — Ana Paula', 'Ana Paula'),
                           ('Entrevista com Joao', 'Joao'),
                           ('Entrevista: Maria', 'Maria'),
                           ('Conversa com o time', 'Conversa com o time')]:
        t(f'{tema!r} -> {esperado!r}', _nome_do_tema(tema) == esperado, _nome_do_tema(tema))

    print('\n== O FORMULÁRIO DA REUNIÃO ==')
    html = c.get('/reunioes/nova/').content.decode()
    t('dá para escolher Entrevista', 'value="ENTREVISTA"' in html)
    t('com campo de currículo', 'name="curriculo"' in html)
    t('e o formulário aceita arquivo', 'multipart/form-data' in html)
    t('o bloco só aparece em entrevista', 'rnBlocoEntrevista' in html)

    print('\n== LINK DA IA DO RH ==')
    cfg.url_sistema_perfil = 'https://perfil.exemplo.local/'
    cfg.save()
    t('monta o link público', url_da_entrevista('abc123') ==
      'https://perfil.exemplo.local/e/abc123', url_da_entrevista('abc123'))
    t('sem token não monta link', url_da_entrevista('') == '')
    t('aceita o link inteiro colado',
      extrair_token('https://perfil.exemplo.local/e/tok123?x=1') == 'tok123',
      extrair_token('https://perfil.exemplo.local/e/tok123?x=1'))
    t('aceita o token puro', extrair_token('tok123') == 'tok123')

    ficha.entrevista_token = 'tok123'
    ficha.save()
    html = c.get(f'/talentos/{ficha.id}/').content.decode()
    t('a ficha mostra o link da entrevista', 'perfil.exemplo.local/e/tok123' in html)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
