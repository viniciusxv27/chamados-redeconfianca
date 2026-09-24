"""Visão SAP: espelho do MySQL, tela da auditoria, marcação e painel.

Pedido: "Crie uma nova visão 'visão SAP', onde deve conectar a um banco mysql
(aquelas credenciais que já existem, porém o banco é 'SAP') e acessar a view
'vw_auditoria_visao_geral' — deve ser uma visão podendo marcar se aquela linha
está resolvida ou não, dinâmica, responsiva e moderna, mostrando quem marcou
como resolvida ou não, tendo uma dashboard administrativa pra ver, com filtros
para conseguir filtrar essas linhas".

O que este teste cobre:

- o endereço do banco SAP saindo da credencial que já existe (mesmo host e
  usuário, só o banco muda) — sem nada de credencial escrito no código;
- o espelho: linha nova, linha que mudou, linha que sumiu, linha que voltou, e
  o "resolvida" do portal sobrevivendo a tudo isso;
- quem vê a tela e quem pode mandar atualizar;
- a lista com os filtros (tipo, loja, período, situação, divergência, busca),
  a ordem e a paginação;
- marcar e reabrir guardando quem foi, quando e a observação;
- a ficha da linha com todas as colunas e o histórico;
- o painel: números, por tipo, por loja, por dia, quem resolveu e as maiores
  diferenças.

O MySQL do SAP é um dublê: nenhuma conexão sai daqui. Tudo roda numa transação
desfeita no fim.
"""
import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sap-teste'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sap-teste-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from auditoria_sap import mysql
from auditoria_sap.espelho import sincronizar
from auditoria_sap.models import (LinhaAuditoria, MarcacaoAuditoria,
                                  SincronizacaoAuditoria, chave_de)
from auditoria_sap.permissions import e_gestor, pode_ver
from users.models import Sector

User = get_user_model()
D = Decimal
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


HOJE = date(2026, 9, 22)


def linha_sap(**troca):
    """Uma linha da view, no formato que o MySQL devolve."""
    base = {
        'TIPO_ERRO': 'SEM NF', 'DATA_VENDA': HOJE, 'PDV': 'ICONHA',
        'ID_VENDA': '2728717', 'NOME_CLIENTE': 'CLIENTE DE TESTE',
        'DOCUMENTO_VIVOGO': '085.114.656-26', 'DOCUMENTO_SAP': '085.114.656-26',
        'PRODUTO_VIVOGO': 'Película Fosca', 'PRODUTO_SAP': 'PELICULA FOSCA',
        'SKU_VIVOGO': '22023374', 'COD_MATERIAL_SAP': '22023374',
        'SERIAL_VIVOGO': '2202337410035656', 'SERIAL_SAP': '220233741035656',
        'ORDEM_SAP': 'PADR', 'TIPO_FATURAMENTO_SAP': 'ZFAN', 'ORDEM_ZVNB': 'NÃO',
        'VENDA_MANUAL_ZLVC': 'NÃO', 'NUM_FAT_SAP': '9113964600',
        'STATUS_NF': 'VALIDANDO', 'STATUS_NF_AUDITORIA': 'DIVERGENTE',
        'VALOR_SAP': D('89.00'), 'VALOR_VIVO_GO': D('88.99'),
        'DIFERENCA_VALOR': D('0.010000000000000000000000000000'),
        'DIFERENCA': 'VIVO GO R$ 0,01 ABAIXO DO SAP',
        'SITUACAO_VALOR': 'FALTAM R$ 0,01 NO VIVO GO',
        'LOJA_VIVOGO': 'ICONHA', 'LOJA_SAP': 'LOJA ICONHA', 'CENTRO_SAP': 'C001',
        'DATA_VENDA_VIVOGO': HOJE, 'DATA_FATURA_SAP': HOJE,
        'STATUS_DOCUMENTO': 'OK', 'STATUS_PRODUTO': 'DIVERGENTE', 'STATUS_VALOR': 'DIVERGENTE',
        'STATUS_LOJA': 'OK', 'STATUS_DATA': 'OK', 'DIFERENCA_DIAS': 0,
        'DIFERENCA_EXPLICITA': 'SEM NF | PRODUTO DIFERENTE: VIVOGO = Película Fosca / SAP = PELICULA FOSCA',
        'PONTUACAO_INDICIOS': 3, 'INDICIOS_LOCALIZACAO': 'CPF=OK | SKU=OK | DATA=OK',
    }
    base.update(troca)
    return base


marcador = transaction.atomic()
marcador.__enter__()
try:
    print('== O ENDEREÇO DO BANCO SAP ==')
    with mock.patch.dict(os.environ, {'SAP_MYSQL_URL': '', 'MYSQL_URI': '',
                                      'SISTEMA_PERFIL_MYSQL_URL': 'mysql://zz:segredo@painel.exemplo:3306/iadorh'},
                         clear=False):
        endereco = mysql.endereco()
    t('reaproveita a credencial que já existe, trocando só o banco',
      endereco == 'mysql://zz:segredo@painel.exemplo:3306/SAP', endereco)

    with mock.patch.dict(os.environ, {'SAP_MYSQL_URL': 'mysql://a:b@outro:3307/SAP'}, clear=False):
        t('e dá para apontar para outro servidor sem mexer no código',
          mysql.endereco() == 'mysql://a:b@outro:3307/SAP', mysql.endereco())

    with mock.patch.dict(os.environ, {'SAP_MYSQL_URL': '', 'MYSQL_URI': '',
                                      'SISTEMA_PERFIL_MYSQL_URL': ''}, clear=False):
        t('sem credencial nenhuma, o endereço é vazio', mysql.endereco() == '')
        try:
            mysql.conexao()
            deu = 'conectou'
        except mysql.SapIndisponivel as exc:
            deu = str(exc)
        t('e conectar avisa o que falta', 'SAP_MYSQL_URL' in deu, deu)

    t('a view lida é a do pedido', mysql.VISAO == 'vw_auditoria_visao_geral' and mysql.BANCO == 'SAP')
    t('nenhuma credencial mora no código',
      'senha' not in open('auditoria_sap/mysql.py').read().lower().split('password')[0].split('#')[0]
      and 'redeconfianca' not in open('auditoria_sap/mysql.py').read())

    print('\n== O ESPELHO ==')
    linhas = [linha_sap(),
              linha_sap(TIPO_ERRO='VENDA NÃO ENCONTRADA NO VIVO GO', ID_VENDA='', PDV='GLÓRIA',
                        NOME_CLIENTE='OUTRO CLIENTE', VALOR_VIVO_GO=None, DIFERENCA_VALOR=None,
                        STATUS_VALOR='NÃO COMPARADO', SITUACAO_VALOR='VENDA NÃO ENCONTRADA NO VIVO'),
              linha_sap(ID_VENDA='2728999', DATA_VENDA=HOJE - timedelta(days=3), PDV='PIUMA',
                        VALOR_SAP=D('500.00'), VALOR_VIVO_GO=D('450.00'), DIFERENCA_VALOR=D('50.00'))]

    with mock.patch.object(mysql, 'ler_visao_geral', return_value=linhas):
        resumo = sincronizar()
    t('a primeira leitura traz as linhas', resumo['novas'] == 3 and resumo['total'] == 3, resumo)
    t('e fica registrada com o tempo', SincronizacaoAuditoria.objects.first().total == 3
      and SincronizacaoAuditoria.objects.first().segundos >= 0)

    uma = LinhaAuditoria.objects.get(chave=chave_de(linhas[0]))
    t('os campos vêm convertidos', (uma.tipo_erro == 'SEM NF' and uma.data_venda == HOJE
                                    and uma.valor_sap == D('89.00') and uma.diferenca_valor == D('0.01')
                                    and uma.pontuacao_indicios == 3),
      (uma.tipo_erro, uma.data_venda, uma.valor_sap, uma.diferenca_valor))
    t('a linha inteira fica guardada para a ficha',
      uma.dados.get('CENTRO_SAP') == 'C001' and uma.dados.get('DATA_FATURA_SAP') == '22/09/2026',
      list(uma.dados.items())[:3])
    t('a diferença aparece em reais, sempre positiva', uma.diferenca_em_reais == D('0.01'))
    t('nasce aberta e ativa', not uma.resolvida and uma.ativa)

    chaves = [chave_de(l) for l in linhas]
    with mock.patch.object(mysql, 'ler_visao_geral', return_value=linhas):
        resumo = sincronizar()
    t('ler de novo não duplica nada',
      resumo['novas'] == 0 and LinhaAuditoria.objects.filter(chave__in=chaves).count() == 3, resumo)
    t('e só o que veio na leitura fica ativo',
      LinhaAuditoria.objects.filter(ativa=True).count() == 3,
      LinhaAuditoria.objects.filter(ativa=True).count())

    print('\n-- o que o portal marcou não se perde --')
    chefe = User.objects.create_user(
        username='zz.sap.chefe', email='zz.sap.chefe@exemplo-teste.local',
        password='S3nha!teste', first_name='Zz', last_name='Chefe',
        hierarchy='SUPERADMIN', is_staff=True, is_superuser=True)
    uma.marcar(chefe, True, 'nota emitida na mão')
    with mock.patch.object(mysql, 'ler_visao_geral', return_value=linhas):
        sincronizar()
    uma.refresh_from_db()
    t('a linha continua resolvida depois da sincronização', uma.resolvida)
    t('e guarda quem marcou', uma.resolvida_por_id == chefe.id and uma.observacao == 'nota emitida na mão')

    print('\n-- linha que some e linha que volta --')
    with mock.patch.object(mysql, 'ler_visao_geral', return_value=linhas[:2]):
        resumo = sincronizar()
    sumida = LinhaAuditoria.objects.get(chave=chave_de(linhas[2]))
    t('a que saiu da auditoria não é apagada, fica inativa',
      resumo['sumiram'] == 1 and not sumida.ativa and sumida.sumiu_em is not None, resumo)
    t('e as outras continuam ativas', LinhaAuditoria.objects.filter(ativa=True).count() == 2)

    with mock.patch.object(mysql, 'ler_visao_geral', return_value=linhas):
        sincronizar()
    sumida.refresh_from_db()
    t('voltou a aparecer, volta a ficar ativa', sumida.ativa and sumida.sumiu_em is None)

    print('\n-- valor que muda é outra linha --')
    mudada = dict(linhas[2]); mudada['VALOR_SAP'] = D('600.00')
    with mock.patch.object(mysql, 'ler_visao_geral', return_value=[linhas[0], linhas[1], mudada]):
        resumo = sincronizar()
    t('a divergência com outro valor entra como linha nova',
      resumo['novas'] == 1 and resumo['sumiram'] == 1, resumo)

    print('\n-- SAP fora do ar --')
    antes = LinhaAuditoria.objects.count()
    with mock.patch.object(mysql, 'ler_visao_geral', side_effect=mysql.SapIndisponivel('sem rota para o host')):
        try:
            sincronizar(por=chefe)
            quebrou = False
        except mysql.SapIndisponivel:
            quebrou = True
    t('a falha sobe para a tela avisar', quebrou)
    t('e o espelho não é apagado', LinhaAuditoria.objects.count() == antes)
    ultima = SincronizacaoAuditoria.objects.first()
    t('a falha fica registrada', 'sem rota' in ultima.erro and not ultima.deu_certo, ultima.erro)

    print('\n== QUEM VÊ ==')
    setor = Sector.objects.create(name='ZZ Setor SAP')
    comum = User.objects.create_user(
        username='zz.sap.comum', email='zz.sap.comum@exemplo-teste.local',
        password='S3nha!teste', first_name='Zz', last_name='Comum',
        hierarchy='PADRAO', sector=setor)
    t('SUPERADMIN vê e atualiza', pode_ver(chefe) and e_gestor(chefe))
    t('colaborador comum não vê', not pode_ver(comum) and not e_gestor(comum))

    from users.module_access import set_user_modules
    set_user_modules(comum, ['sap'], granted_by=chefe)
    comum = User.objects.get(pk=comum.pk)
    t('com a liberação individual, passa a ver', pode_ver(comum))
    t('mas continua sem poder atualizar o espelho', not e_gestor(comum))

    print('\n== A TELA DA AUDITORIA ==')
    c = Client(); c.force_login(chefe)
    r = c.get('/sap/')
    t('a auditoria abre', r.status_code == 200, r.status_code)
    html = r.content.decode()
    t('mostra a linha da venda', '2728717' in html and 'Cliente De Teste' in html.title() or 'CLIENTE DE TESTE' in html)
    t('mostra o tipo do erro', 'Sem Nf' in html or 'SEM NF' in html.upper())
    t('e o que está diferente', 'PRODUTO DIFERENTE' in html)
    t('tem o filtro de tipo, loja, situação e busca',
      'name="tipo"' in html and 'name="loja"' in html and 'name="situacao"' in html and 'name="q"' in html)
    t('tem o filtro de período', 'name="de"' in html and 'name="ate"' in html)
    t('e a ordem', 'name="ordem"' in html)
    t('o resumo conta as abertas', r.context['resumo']['total'] == r.context['resumo']['abertas'] + r.context['resumo']['resolvidas'])

    visiveis = {l.id_venda for l in r.context['pagina']}
    t('por padrão mostra só as abertas (a resolvida sai)', '2728717' not in visiveis, visiveis)

    r = c.get('/sap/?situacao=todas')
    t('pedindo todas, a resolvida volta', any(l.resolvida for l in r.context['pagina']))

    r = c.get('/sap/?situacao=todas&tipo=VENDA N%C3%83O ENCONTRADA NO VIVO GO')
    t('o filtro por tipo recorta',
      all(l.tipo_erro == 'VENDA NÃO ENCONTRADA NO VIVO GO' for l in r.context['pagina'])
      and len(r.context['pagina']) == 1, [l.tipo_erro for l in r.context['pagina']])

    r = c.get('/sap/?situacao=todas&loja=PIUMA')
    t('o filtro por loja também', all(l.pdv == 'PIUMA' for l in r.context['pagina'])
      and len(r.context['pagina']) >= 1)

    r = c.get('/sap/?situacao=todas&q=2728999')
    t('a busca acha pela venda', len(r.context['pagina']) == 1
      and r.context['pagina'][0].id_venda == '2728999')
    r = c.get('/sap/?situacao=todas&q=OUTRO CLIENTE')
    t('e pelo nome do cliente', len(r.context['pagina']) == 1)

    r = c.get(f'/sap/?situacao=todas&de={HOJE:%Y-%m-%d}')
    t('o período corta o que é mais antigo',
      all(l.data_venda >= HOJE for l in r.context['pagina']))

    r = c.get('/sap/?situacao=todas&comparacao=valor')
    t('dá para ver só o que diverge no valor',
      all('DIVERG' in l.status_valor for l in r.context['pagina']))

    r = c.get('/sap/?situacao=todas&presenca=sairam')
    t('e só o que já saiu do SAP', all(not l.ativa for l in r.context['pagina'])
      and len(r.context['pagina']) >= 1)

    r = c.get('/sap/?situacao=todas&ordem=diferenca')
    valores = [abs(l.diferenca_valor or D('0')) for l in r.context['pagina']]
    t('a ordem por diferença traz a maior primeiro', valores == sorted(valores, reverse=True), valores)

    r = c.get('/sap/')
    t('a tela diz de quando são os dados', 'Dados de' in r.content.decode())
    t('e oferece atualizar para quem pode', 'Atualizar do SAP' in r.content.decode())

    cc = Client(); cc.force_login(comum)
    t('quem só tem a liberação de ver também abre', cc.get('/sap/').status_code == 200)
    t('mas não vê o botão de atualizar', 'Atualizar do SAP' not in cc.get('/sap/').content.decode())
    r = cc.post('/sap/atualizar/', follow=True)
    t('e um POST direto não atualiza', 'Só a administração' in r.content.decode())

    outro = User.objects.create_user(
        username='zz.sap.fora', email='zz.sap.fora@exemplo-teste.local',
        password='S3nha!teste', first_name='Zz', last_name='Fora',
        hierarchy='PADRAO', sector=setor)
    cf = Client(); cf.force_login(outro)
    r = cf.get('/sap/', follow=True)
    t('quem não tem acesso é barrado', 'restrita à administração' in r.content.decode())

    print('\n== MARCAR COMO RESOLVIDA ==')
    alvo = LinhaAuditoria.objects.filter(resolvida=False, ativa=True).first()
    r = c.post(f'/sap/linha/{alvo.id}/marcar/', {'resolvida': '1', 'observacao': 'ajustado no SAP'})
    dados = r.json()
    alvo.refresh_from_db()
    t('marcar responde com quem e quando', dados['ok'] and dados['resolvida']
      and dados['quem'] == chefe.full_name and dados['quando'], dados)
    t('a linha fica resolvida', alvo.resolvida and alvo.resolvida_por_id == chefe.id)
    t('a observação fica guardada', alvo.observacao == 'ajustado no SAP')
    t('e a marcação entra no histórico',
      alvo.marcacoes.filter(resolvida=True, usuario=chefe).exists())

    r = c.post(f'/sap/linha/{alvo.id}/marcar/', {'resolvida': '0'})
    alvo.refresh_from_db()
    t('reabrir também guarda quem foi', not alvo.resolvida and alvo.resolvida_por_id == chefe.id)
    t('e o histórico tem as duas passagens', alvo.marcacoes.count() == 2)

    r = c.get(f'/sap/linha/{alvo.id}/')
    ficha = r.json()
    t('a ficha traz a linha inteira', len(ficha['campos']) >= 35, len(ficha['campos']))
    t('com as colunas do SAP', any(x['coluna'] == 'INDICIOS_LOCALIZACAO' for x in ficha['campos']))
    t('e o histórico de quem marcou',
      len(ficha['historico']) == 2 and ficha['historico'][0]['quem'] == chefe.full_name,
      ficha['historico'])

    t('quem não tem acesso não marca',
      cf.post(f'/sap/linha/{alvo.id}/marcar/', {'resolvida': '1'}).status_code in (302, 403))

    print('\n== O PAINEL ==')
    r = c.get('/sap/painel/?situacao=todas')
    t('o painel abre', r.status_code == 200, r.status_code)
    html = r.content.decode()
    contexto = r.context
    t('conta as linhas do filtro (as que ainda estão no SAP)',
      contexto['resumo']['total'] == LinhaAuditoria.objects.filter(ativa=True).count(),
      contexto['resumo']['total'])
    todas = c.get('/sap/painel/?situacao=todas&presenca=todas')
    t('e, pedindo tudo, conta também as que saíram',
      todas.context['resumo']['total'] == LinhaAuditoria.objects.count(),
      todas.context['resumo']['total'])
    t('separa por tipo de erro', len(contexto['por_tipo']) >= 2, contexto['por_tipo'])
    t('e por loja', len(contexto['por_loja']) >= 2)
    t('tem o dia a dia', len(contexto['por_dia']) >= 1)
    t('mostra quem resolveu', any(q['usuario'] == chefe.id for q in contexto['quem']), contexto['quem'])
    t('e as últimas marcações', len(contexto['ultimas']) >= 2)
    t('lista as maiores diferenças', len(contexto['maiores']) >= 1)
    t('diz quantas resolvidas continuam aparecendo', 'Resolvidas que voltaram' in html)
    t('e quantas saíram do SAP', 'Saíram do SAP' in html)
    t('o painel tem os mesmos filtros da lista',
      'name="tipo"' in html and 'name="loja"' in html and 'name="q"' in html)
    t('e diz de quando é a última leitura', 'Última leitura do SAP' in html)

    print('\n== A TELA É DE VERDADE ==')
    for nome, pedaco in (('a tabela vira cartão no celular', 'lg:grid-cols-12'),
                         ('o filtro recarrega sozinho', 'sap-auto'),
                         ('marcar não recarrega a página', 'function (d)'),
                         ('a ficha abre por AJAX', 'sapFicha')):
        t(nome, pedaco in c.get('/sap/').content.decode())
    t('nenhum comentário de template vazou na tela',
      '{#' not in c.get('/sap/').content.decode() and '{#' not in html)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco; nenhuma conexão saiu para o SAP.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
