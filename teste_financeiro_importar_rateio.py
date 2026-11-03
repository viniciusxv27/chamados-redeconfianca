"""Financeiro (03/11/2026): importar contas a pagar, PIS/Cofins no Financeiro e conta a pagar com rateio.

Cobre:
- importação do CSV do relatório: lê o cabeçalho, mapeia loja/forma/conta/parcela/plano, recusa o
  inválido, separa o que já foi pago, não duplica linha repetida nem ao subir de novo, respeita a loja;
- a tela: conferir não grava, confirmar grava; só quem lança conta a pagar importa;
- PIS/Cofins: fora do menu, aba no Financeiro, endereço antigo redireciona, quem só tem PIS/Cofins entra;
- "Lançar como Conta a Pagar": campos obrigatórios, conta única e rateio (um título por loja, soma tem de bater).

Roda num sqlite descartável; nada sai para a rede.
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

# Banco e arquivos descartáveis: a tabela das entregas e a coluna
# `exige_entrega` só existem no Postgres compartilhado depois do migrate, e o
# migrate é dele. O schema aqui sai direto dos models (`--run-syncdb`), como em
# qualquer outra sessão — nada toca o banco de verdade nem o MinIO.
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-fin-imp-'))
(PASTA / 'zz_fin_imp_settings.py').write_text(f"""
from redeconfianca.settings import *              # noqa: F401,F403

DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3',
                          'NAME': r'{PASTA}/banco.sqlite3'}}}}


class SemMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = SemMigrations()

USE_S3 = False
MEDIA_ROOT = r'{PASTA}/media'
MEDIA_URL = '/media/'
STORAGES = {{
    'default': {{'BACKEND': 'django.core.files.storage.FileSystemStorage'}},
    'staticfiles': {{'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
}}
DEBUG = False
OPENAI_API_KEY = ''
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
CACHES = {{
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fimp'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fimp-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_fin_imp_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

from datetime import date, timedelta
from decimal import Decimal as D

import requests
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from contagem_caixa.models import DocumentoPisCofins, FornecedorPisCofins
from financeiro.importacao_titulos import analisar, decodificar, importar, modelo_csv
from financeiro.models import AcessoFinanceiro, ContaBancaria, LogFinanceiro, PlanoConta, Titulo
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


def sem_rede(*a, **k):
    raise AssertionError('teste tentou sair para a rede')


for nome in ('get', 'post', 'request'):
    mock.patch.object(requests, nome, sem_rede).start()

gloria = Sector.objects.create(name='Loja Glória')
serra = Sector.objects.create(name='Loja Serra Sede')
norte = Sector.objects.create(name='Loja Norte Sul')
esc = Sector.objects.create(name='Financeiro')


def usuario(nome, hierarquia='PADRAO', setor=esc):
    u = User.objects.create_user(username=f'zzfi.{nome}', email=f'zzfi.{nome}@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZFI', last_name=nome, sector=setor, hierarchy=hierarquia)
    c = Client(); c.force_login(u)
    return u, c


chefe, cs = usuario('super', 'SUPERADMIN')
leitor, cl = usuario('leitor')
AcessoFinanceiro.objects.create(user=leitor, permissoes=['pagar.ver'], ativo=True)
limitado, cli = usuario('limitado')
ac = AcessoFinanceiro.objects.create(user=limitado, permissoes=['pagar.editar'], ativo=True)
ac.lojas.add(gloria)
adm, ca = usuario('adm', 'ADMIN')     # gestor do caixa: tem PIS/Cofins, não tem Financeiro
nada, cn = usuario('nada')
sicoob = ContaBancaria.objects.create(nome='SICOOB', banco_nome='SICOOB', agencia='301-0', conta='203409')

COL = 'Situação;Data de lançamento;Data de vencimento;Data de pagamento;Forma pagamento;Filial;CNPJ Raiz;Plano de Contas;Despesa;Obs. despesa;Centro de Custo;Status Pagamento Banco;Fornecedor;CNPJ/CPF Fornecedor;Crédito fornecedor;Vendedor;Nº do movimento;Nº do documento / Linha de pagamento;Nota fiscal;Observações;Motivo reversão;Data reversão;Usuário baixa/reversão;Usuário inserção;Débito;Valor total;Juros (%);Juros (R$);Descontos;Valor líquido;Valor Pago;'


def linha(sit='A Pagar', lanc='01/11/2026', venc='10/11/2026', pag='', forma='Boleto', filial='GLÓRIA', plano='ALUGUEL', desp='ALUGUEL',
          obs_d='', cc='DESPESAS OPERACIONAIS', forn='FORNECEDOR A', doc='28.152.650/0001-71', mov='100', num="'0430901", nf='0430901',
          obs='ALUGUEL DA LOJA', deb='Conta ( AG: 301 CC: 203409-3)', valor='5.500,00', pago='0,00'):
    return ';'.join([sit, lanc, venc, pag, forma, filial, '09.163.602', plano, desp, obs_d, cc, 'Nao Efetuado', forn, doc, '0,00', '', mov, num, nf,
                     obs, '', '', '', 'TAMIRES SILVA', deb, valor, '0,00', '0,00', '0,00', valor, pago, ''])


RELATORIO = '\n'.join(['Relatório de Contas a pagar e já pagas', '', 'Data de lançamento inícial : ;Data de vencimento inícial : 31/10/2026', '', COL,
    linha(),                                                                   # 1 aluguel na Glória
    linha(mov='101', forma='Itau -  9503', filial='SEDE ADMINISTRATIVA', plano='', desp='Compra', deb='Caixa', valor='188,24', obs='REEMBOLSO', doc='115.916.587-48', num=''),
    linha(mov='102', venc='05/11/2026', forma='PIX', filial='NORTE SUL', obs='NOTEBOOK - PARCELA 14 DE 15', valor='239,93', plano='OUTRAS DESPESAS', desp='MAQUINAS'),
    linha(mov='102', venc='05/12/2026', forma='PIX', filial='NORTE SUL', obs='NOTEBOOK - PARCELA 15 DE 15', valor='240,63', plano='OUTRAS DESPESAS', desp='MAQUINAS'),
    linha(mov='103', forma='Transferência', obs='GOOGLE DRIVE', valor='49,99'),
    linha(mov='103', forma='Transferência', obs='GOOGLE DRIVE', valor='49,99'),    # idêntica: entra uma
    linha(mov='104', sit='Paga', pag='02/11/2026', pago='70,00', valor='70,00'),    # já paga: fora
    linha(mov='105', venc='31/02/2026'),                                            # vencimento impossível
    linha(mov='106', valor='0,00'),                                                 # sem valor
    linha(mov='107', forn=''),                                                      # sem fornecedor
]) + '\n'

print('Análise do relatório')
a = analisar(RELATORIO)
t('separa novas, repetidas, pagas e recusadas', (len(a.novos), len(a.repetidas), len(a.pagas), len(a.rejeitadas)) == (5, 1, 1, 3),
  (len(a.novos), len(a.repetidas), len(a.pagas), a.rejeitadas))
t('nada foi gravado na análise', not Titulo.objects.exists() and not PlanoConta.objects.exists())
n = {x['numero_movimento'] + x['vencimento'].strftime('%m'): x for x in a.novos}
x = n['10011']
t('loja, conta, forma e documento', x['loja'] == gloria and x['conta_prevista'] == sicoob and x['forma'] == 'BOLETO'
  and x['pessoa_documento'] == '28152650000171' and x['numero_documento'] == '0430901' and x['valor'] == D('5500.00')
  and x['emissao'] == date(2026, 11, 1) and x['vencimento'] == date(2026, 11, 10), x)
x = n['10111']
t('"Itau - 9503" é cartão; SEDE entra sem loja; Caixa sem conta', x['forma'] == 'CARTAO' and x['loja'] is None and x['conta_prevista'] is None
  and 'Forma na planilha: Itau -  9503' in x['observacao'] and 'Filial: SEDE ADMINISTRATIVA' in x['observacao'] and x['pessoa_documento'] == '11591658748')
t('filial sem loja no portal fica no relatório', a.sem_loja == {'SEDE ADMINISTRATIVA': 1}, a.sem_loja)
t('parcelas lidas do texto e no mesmo grupo', (n['10211']['parcela'], n['10211']['parcelas']) == (14, 15) and n['10212']['parcela'] == 15
  and n['10211']['grupo'] == n['10212']['grupo'] and n['10011']['grupo'] != n['10211']['grupo'])
t('transferência vira TED', n['10311']['forma'] == 'TED')
t('observação guarda centro de custo, nota e quem lançou', 'Centro de custo: DESPESAS OPERACIONAIS' in n['10011']['observacao']
  and 'Lançado por: TAMIRES SILVA' in n['10011']['observacao'] and 'Nota fiscal: 0430901' in n['10011']['observacao'])
try:
    analisar('a;b;c\n1;2;3')
    t('arquivo fora do modelo é recusado', False)
except Exception as exc:
    t('arquivo fora do modelo é recusado', 'modelo' in str(exc), exc)
t('o arquivo em Latin-1 é lido', decodificar(RELATORIO.encode('latin-1')) == RELATORIO)
t('o modelo se lê com o próprio importador', len(analisar(decodificar(modelo_csv())).novos) == 1)
b = analisar(RELATORIO, lojas_permitidas={gloria.id})
t('quem só vê a Glória não importa outras filiais', {x['loja'] for x in b.novos} == {gloria} and len(b.rejeitadas) == 3 + 3, (len(b.novos), b.rejeitadas))

print('Tela de importar')
h = cs.get('/financeiro/pagar/').content.decode()
t('o botão Importar aparece em A pagar', '/financeiro/pagar/importar/' in h)
t('não aparece em A receber', '/financeiro/pagar/importar/' not in cs.get('/financeiro/receber/').content.decode())
t('só com pagar.editar', cl.get('/financeiro/pagar/importar/').status_code == 302 and cn.get('/financeiro/pagar/importar/').status_code == 302
  and 'Importar</a>' not in cl.get('/financeiro/pagar/').content.decode())
r = cs.get('/financeiro/pagar/importar/modelo.csv')
t('baixa o modelo', r.status_code == 200 and 'Nº do movimento'.encode('latin-1') in r.content)
r = cs.post('/financeiro/pagar/importar/', {'arquivo': SimpleUploadedFile('rel.csv', RELATORIO.encode('latin-1'), content_type='text/csv')})
h = r.content.decode()
t('conferir mostra o resumo e não grava', r.status_code == 200 and 'Importar 5' in h and 'GOOGLE' in h and not Titulo.objects.exists(), r.status_code)
r = cs.post('/financeiro/pagar/importar/', {'arquivo': SimpleUploadedFile('x.csv', b'a;b;c\n1;2;3')}, follow=True)
t('arquivo errado volta com aviso', 'modelo' in r.content.decode() and not Titulo.objects.exists())
r = cs.post('/financeiro/pagar/importar/', {'etapa': 'confirmar', 'nome': 'rel.csv', 'conteudo': RELATORIO})
titulos = Titulo.objects.filter(tipo='PAGAR')
t('confirmar grava as 5, em aberto e como importação', r.status_code == 302 and titulos.count() == 5
  and set(titulos.values_list('status', 'origem')) == {('ABERTO', 'IMPORTACAO')} and titulos.first().criado_por == chefe, titulos.count())
t('soma certa (a repetida entra uma vez)', sum(x.valor for x in titulos) == D('5500.00') + D('188.24') + D('239.93') + D('240.63') + D('49.99'))
tt = titulos.get(numero_movimento='100')
t('plano Grupo → Despesa', tt.plano and tt.plano.nome == 'ALUGUEL' and tt.plano.analitica and tt.plano.pai.nome == 'ALUGUEL' and not tt.plano.pai.analitica
  and tt.plano.pai.pai.codigo == '2' and tt.plano.tipo == 'DESPESA', tt.plano)
t('sem Plano de Contas vai para SEM GRUPO', titulos.get(numero_movimento='101').plano.pai.nome == 'SEM GRUPO')
t('fica na rastreabilidade', LogFinanceiro.objects.filter(acao='IMPORTAR', area='pagar').count() == 1)
planos = PlanoConta.objects.count()
tt.valor_baixado = D('0'); tt.descricao = 'EDITADA À MÃO'; tt.save()
cs.post('/financeiro/pagar/importar/', {'etapa': 'confirmar', 'nome': 'rel.csv', 'conteudo': RELATORIO})
t('subir de novo não duplica nem mexe no que foi editado', titulos.count() == 5 and titulos.get(numero_movimento='100').descricao == 'EDITADA À MÃO'
  and PlanoConta.objects.count() == planos and analisar(RELATORIO).ja_existem == 5)
h = cs.get('/financeiro/pagar/', {'situacao': 'todos'}).content.decode()
t('as contas aparecem em A pagar', 'FORNECEDOR A' in h)

print('PIS/Cofins no Financeiro')
h = cs.get('/financeiro/').content.decode()
t('saiu do menu lateral', 'data-rota="/contagem-caixa/pis-cofins/"' not in h and 'data-rota="/financeiro/pis-cofins/"' not in h)
t('virou aba do Financeiro', 'href="/financeiro/pis-cofins/"' in h and '<span>PIS/Cofins</span>' in h)
r = cs.get('/contagem-caixa/pis-cofins/?competencia=2026-10')
t('o endereço antigo redireciona com os filtros', r.status_code == 302 and r.url == '/financeiro/pis-cofins/?competencia=2026-10', getattr(r, 'url', ''))
r = cs.get('/financeiro/pis-cofins/')
h = r.content.decode()
t('a tela abre dentro do Financeiro', r.status_code == 200 and 'finAbasLista' in h and 'Lançar documento' in h)
t('tem a opção de conta a pagar com rateio', 'Lançar como Conta a Pagar' in h and 'name="rateio_valor_%d"' % gloria.id in h and 'pagar_vencimento' in h)
t('a aba saiu da Contagem de Caixa', 'href="/financeiro/pis-cofins/"' not in cs.get('/contagem-caixa/').content.decode().split('finAbas')[0]
  or '<i class="fas fa-file-invoice-dollar mr-1.5"></i>PIS/Cofins' not in cs.get('/contagem-caixa/').content.decode())
r = ca.get('/financeiro/')
t('quem só tem PIS/Cofins entra pelo Financeiro e cai nele', r.status_code == 302 and r.url == '/financeiro/pis-cofins/', getattr(r, 'url', r.status_code))
r = ca.get('/financeiro/pis-cofins/')
h = r.content.decode()
t('e não vê a opção de conta a pagar', r.status_code == 200 and 'id="pcPagar"' not in h and 'href="/financeiro/pagar/"' not in h)
t('sem acesso continua fora', cn.get('/financeiro/pis-cofins/').status_code == 302)

print('Lançar como Conta a Pagar')
FornecedorPisCofins.objects.create(cnpj='28152650000171', razao_social='ZZ FORNECEDOR LTDA', situacao='ATIVA')
hoje = timezone.localdate()
comp = hoje.strftime('%Y-%m')
num = iter(range(1000, 2000))


def lancar(cliente=None, **extra):
    d = {'tipo': 'NF', 'competencia': comp, 'cnpj': '28.152.650/0001-71', 'valor': '100,00', 'numero': str(next(num)),
         'descricao': 'Energia', 'voltar': '/financeiro/pis-cofins/',
         'arquivo': SimpleUploadedFile('nota.pdf', b'%PDF-1.4 zz', content_type='application/pdf')}
    d.update(extra)
    return (cliente or cs).post('/contagem-caixa/pis-cofins/lancar/', d, follow=True)


def novos():
    return Titulo.objects.filter(origem='PISCOFINS').order_by('id')


venc = (hoje + timedelta(days=10)).isoformat()
r = lancar()
t('sem marcar, só o documento', DocumentoPisCofins.objects.count() == 1 and not novos().exists())
t('volta para a tela no Financeiro', r.redirect_chain[-1][0].startswith('/financeiro/pis-cofins/'), r.redirect_chain)
for faltando, msg in (('pagar_vencimento', 'vencimento'), ('pagar_descricao', 'descrição'), ('pagar_forma', 'forma')):
    d = {'lancar_pagar': '1', 'pagar_vencimento': venc, 'pagar_descricao': 'Conta de luz', 'pagar_forma': 'BOLETO'}
    d[faltando] = ''
    antes = DocumentoPisCofins.objects.count()
    r = lancar(**d)
    t(f'sem {msg}: recusa e nem o documento entra', msg in r.content.decode() and DocumentoPisCofins.objects.count() == antes)
r = lancar(lancar_pagar='1', pagar_vencimento=venc, pagar_descricao='Conta de luz', pagar_forma='BOLETO', loja=str(gloria.id))
x = novos().first()
doc = DocumentoPisCofins.objects.order_by('-id').first()
t('conta única: um título na loja escolhida', novos().count() == 1 and x.loja == gloria and x.valor == D('100.00') and x.descricao == 'Conta de luz'
  and x.forma == 'BOLETO' and x.vencimento.isoformat() == venc and x.pessoa_documento == '28152650000171' and x.numero_documento == doc.numero
  and x.competencia == doc.competencia and x.status == 'ABERTO', x)
t('o título aponta para o arquivo do documento', x.anexo.name == doc.arquivo.name and x.anexo_nome == 'nota.pdf')
t('e fica na rastreabilidade', LogFinanceiro.objects.filter(objeto_id=str(x.pk), acao='CRIAR').exists())
Titulo.objects.filter(origem='PISCOFINS').delete()

base = {'lancar_pagar': '1', 'pagar_vencimento': venc, 'pagar_descricao': 'Aluguel rateado', 'pagar_forma': 'PIX', 'rateio': '1'}
r = lancar(**base, rateio_loja=[gloria.id, serra.id, norte.id], **{f'rateio_valor_{gloria.id}': '33,33', f'rateio_valor_{serra.id}': '33,33',
                                                                    f'rateio_valor_{norte.id}': '33,33'})
t('rateio que não bate é recusado (aviso e nada grava)', 'não bate' in r.content.decode() and not novos().exists())
antes = DocumentoPisCofins.objects.count()
r = lancar(**base, rateio_loja=[gloria.id, serra.id], **{f'rateio_valor_{gloria.id}': '100,00', f'rateio_valor_{serra.id}': '0'})
t('loja marcada sem valor é recusada', 'maior que zero' in r.content.decode() and DocumentoPisCofins.objects.count() == antes)
r = lancar(**base)
t('rateio sem loja é recusado', 'selecione as lojas' in r.content.decode() and not novos().exists())
r = lancar(**base, rateio_loja=[gloria.id, serra.id, norte.id], **{f'rateio_valor_{gloria.id}': '50,00', f'rateio_valor_{serra.id}': '33,33',
                                                                    f'rateio_valor_{norte.id}': '16,67'})
t('rateio certo: um título por loja, com o valor mudado à mão', {(x.loja_id, x.valor) for x in novos()} == {(gloria.id, D('50.00')), (serra.id, D('33.33')), (norte.id, D('16.67'))},
  list(novos().values_list('loja_id', 'valor')))
t('no mesmo grupo e sem virar "parcela"', novos().values('grupo').distinct().count() == 1 and all(x.parcelas == 1 for x in novos()))
t('a observação conta o rateio', 'Rateio de R$ 100.00 entre 3 lojas' in novos().first().observacao, novos().first().observacao)
h = cs.get(f'/financeiro/titulo/{novos().first().pk}/').content.decode()
t('no título aparecem as outras lojas', 'Loja Norte Sul' in h and 'Loja Serra Sede' in h)
t('mensagem conta as contas criadas', '3 conta(s) a pagar' in r.content.decode())
Titulo.objects.filter(origem='PISCOFINS').delete()

# Só a loja dele
AcessoFinanceiro.objects.filter(user=adm).delete()
ac2 = AcessoFinanceiro.objects.create(user=adm, permissoes=['pagar.editar'], ativo=True)
ac2.lojas.add(gloria)
ca = Client(); ca.force_login(User.objects.get(pk=adm.pk))
h = ca.get('/financeiro/pis-cofins/').content.decode()
t('o rateio lista só as lojas liberadas', f'rateio_valor_{gloria.id}"' in h and f'rateio_valor_{serra.id}"' not in h)
r = lancar(ca, **base, rateio_loja=[gloria.id, serra.id], **{f'rateio_valor_{gloria.id}': '50', f'rateio_valor_{serra.id}': '50'})
t('e recusa loja fora do acesso', not novos().exists() and 'selecione as lojas' in r.content.decode())
r = lancar(cn, lancar_pagar='1')
t('sem acesso ao PIS/Cofins não lança', not novos().exists())
ac2.delete()
ca = Client(); ca.force_login(User.objects.get(pk=adm.pk))
antes = DocumentoPisCofins.objects.count()
r = lancar(ca, lancar_pagar='1', pagar_vencimento=venc, pagar_descricao='x', pagar_forma='PIX')
t('sem pagar.editar: recusa a conta', 'não está liberado' in r.content.decode() and not novos().exists() and DocumentoPisCofins.objects.count() == antes)

print(f'\n{ok} OK, {fail} falha(s)')
