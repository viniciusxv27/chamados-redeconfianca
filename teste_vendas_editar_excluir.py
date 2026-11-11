"""Vendas (/vendas/): o SUPERADMIN edita e exclui venda, tudo no histórico (11/11/2026).

Cobre: edição de cabeçalho, produto, serviço e pagamento com o antes/depois e o motivo
em cada linha do histórico; motivo obrigatório; erros de validação (IMEI, fibra sem ordem,
portabilidade sem operadora, valor negativo, pagamento que não fecha); recálculo (valor
final, Vivo+, desconto no Pix, delta, forma MISTO); trava de concorrência; quem não é
SUPERADMIN (nem o gestor com todas as funções) não edita, não exclui e não vê os botões;
exclusão com a fotografia completa antes de apagar; venda conciliada no Financeiro não se
exclui; a tela /vendas/historico/; comentários de template. Banco descartável; nada sai.
"""
import os
import pathlib
import re
import sys
import tempfile

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-vendas-editar-'))
(PASTA / 'zz_vendas_editar_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-vendas-ed'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-vendas-ed-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_vendas_editar_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import json  # noqa: E402
from datetime import date  # noqa: E402
from decimal import Decimal as D  # noqa: E402

from django.contrib.auth import get_user_model  # noqa: E402

from financeiro.models import RecebivelCartao  # noqa: E402
from users.models import Sector  # noqa: E402
from vendas import edicao, slv  # noqa: E402
from vendas.models import (AcessoVendas, Cliente, ConfiguracaoVendas, PagamentoVenda, Plano,  # noqa: E402
                           RegistroAlteracao, Venda, VendaProduto, VendaServico)
from vendas.permissions import CHAVES  # noqa: E402

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


def imei(n):
    base = f'3599{n:010d}'
    soma = 0
    for posicao, digito in enumerate(int(c) for c in base):
        if posicao % 2:
            digito *= 2
            if digito > 9:
                digito -= 9
        soma += digito
    return base + str((10 - soma % 10) % 10)


def gerar_cpf(base9):
    d = [int(c) for c in base9]
    for tamanho in (9, 10):
        soma = sum(d[i] * (tamanho + 1 - i) for i in range(tamanho))
        d.append((soma * 10) % 11 % 10)
    return ''.join(map(str, d))


CPF, CPF2 = gerar_cpf('529982247'), gerar_cpf('111444777')

loja = Sector.objects.create(name='Loja ZZ Edição')
loja2 = Sector.objects.create(name='Loja ZZ Outra')
admin = User.objects.create_user(username='zz.ed.adm', email='zz.ed.adm@exemplo-teste.local', password='S3nha!teste',
                                 first_name='Ana', last_name='Super', hierarchy='SUPERADMIN', sector=loja)
gestor = User.objects.create_user(username='zz.ed.gestor', email='zz.ed.gestor@exemplo-teste.local', password='S3nha!teste',
                                  first_name='Gil', last_name='Gestor', hierarchy='PADRAO', sector=loja)
vend = User.objects.create_user(username='zz.ed.vend', email='zz.ed.vend@exemplo-teste.local', password='S3nha!teste',
                                first_name='Beto', last_name='Vendedor', hierarchy='PADRAO', sector=loja)
AcessoVendas.objects.create(user=gestor, permissoes=sorted(CHAVES))      # todas as funções do módulo
AcessoVendas.objects.create(user=vend, permissoes=['modulo.ver', 'vendas.lancar'])
ca, cg, cv = Client(), Client(), Client()
ca.force_login(admin)
cg.force_login(gestor)
cv.force_login(vend)

pos = Plano.objects.create(nome='ZZ Pós 50GB', segmentacao='POS', valor=D('149.90'))
ctrl = Plano.objects.create(nome='ZZ Controle', segmentacao='CONTROLE', valor=D('79.90'))
fibra = Plano.objects.create(nome='ZZ Fibra 500', segmentacao='FIBRA', valor=D('119.90'))
config = ConfiguracaoVendas.atual()
config.vivo_mais_percentual = D('10')
config.save()


def lancar(dados, cliente=ca):
    r = cliente.post('/vendas/nova/', json.dumps(dados), content_type='application/json')
    return r.json()


def post_de(venda, **mudar):
    """O formulário de edição como a tela mandaria (valores atuais) com as mudanças pedidas."""
    f = edicao.formulario(venda)
    dados = {'versao': edicao.versao(venda), 'total_pagamentos': str(f['total_pagamentos']), 'motivo': ''}
    for k, v in f['cab'].items():
        if isinstance(v, bool):
            if v:
                dados[k] = '1'
        else:
            dados[k] = v
    for p in f['produtos']:
        for k, v in p.items():
            if k in ('obj', 'id', 'remover'):
                continue
            if isinstance(v, bool):
                if v:
                    dados[f'p{p["id"]}_{k}'] = '1'
            else:
                dados[f'p{p["id"]}_{k}'] = v
    for s in f['servicos']:
        for k, v in s.items():
            if k in ('obj', 'id', 'remover'):
                continue
            if isinstance(v, bool):
                if v:
                    dados[f's{s["id"]}_{k}'] = '1'
            else:
                dados[f's{s["id"]}_{k}'] = v
    for pg in f['pagamentos']:
        for k in ('forma', 'valor', 'nsu', 'parcelas'):
            dados[f'pg{pg["n"]}_{k}'] = pg[k]
    for k, v in mudar.items():
        if v is None:
            dados.pop(k, None)
        else:
            dados[k] = v
    return dados


def editar(venda, cliente=ca, **mudar):
    venda.refresh_from_db()
    return cliente.post(f'/vendas/{venda.pk}/editar/', post_de(venda, **mudar))


print('== LANÇAMENTO (caminho real) ==')
j = lancar({'cliente': {'cpf': CPF, 'nome': 'Zz Cliente Um', 'telefone': '(27) 99876-5432'}, 'linhas': ['27998765432'],
            'produtos': [{'nome': 'ZZ Phone', 'categoria_slv': 'aparelho', 'imei': imei(1), 'valor_sugerido': '1000'},
                         {'nome': 'ZZ Capa', 'categoria_slv': 'essencial', 'valor_sugerido': '50'}],
            'pagamentos': [{'forma': 'CARTAO', 'valor': '1050', 'nsu': '123456', 'parcelas': 2}],
            'servicos': [{'tipo': 'ALTA', 'plano_id': pos.id}]})
t('venda lançada pelo /vendas/nova/', j.get('ok'), j)
venda = Venda.objects.get(pk=j['id'])
aparelho = venda.produtos.get(nome_produto='ZZ Phone')
capa = venda.produtos.get(nome_produto='ZZ Capa')
alta = venda.servicos.get()
j2 = lancar({'cliente': {'cpf': CPF2, 'nome': 'Zz Cliente Dois'},
             'produtos': [{'nome': 'ZZ Phone B', 'categoria_slv': 'aparelho', 'imei': imei(2), 'valor_sugerido': '900'}],
             'pagamentos': [{'forma': 'PIX', 'valor': '900'}]})
t('segunda venda lançada', j2.get('ok'), j2)
venda_b = Venda.objects.get(pk=j2['id'])

print('== QUEM NÃO É SUPERADMIN ==')
for nome, cli in (('gestor com todas as funções', cg), ('vendedor', cv)):
    r = cli.get(f'/vendas/{venda.pk}/editar/')
    t(f'{nome}: GET da edição barrado', r.status_code == 302, r.status_code)
    r = cli.post(f'/vendas/{venda.pk}/editar/', post_de(venda, cliente_nome='Hack', motivo='tentativa indevida'))
    venda.refresh_from_db()
    t(f'{nome}: POST da edição barrado e nada muda', r.status_code == 302 and venda.cliente_nome == 'Zz Cliente Um')
    r = cli.post(f'/vendas/{venda.pk}/excluir/', {'motivo': 'tentativa indevida', 'confirmo': '1'})
    t(f'{nome}: exclusão barrada', r.status_code == 302 and Venda.objects.filter(pk=venda.pk).exists())
    r = cli.get('/vendas/historico/')
    t(f'{nome}: /vendas/historico/ barrado', r.status_code == 302)
html_g = cg.get(f'/vendas/{venda.pk}/').content.decode()
t('gestor: detalhe sem os botões Editar/Excluir', 'data-acao-editar' not in html_g and 'data-acao-excluir' not in html_g
  and f'/vendas/{venda.pk}/excluir/' not in html_g)
t('gestor (rede.ver) vê o histórico desta venda', 'data-historico-venda' in html_g)
html_v = cv.get(f'/vendas/{venda.pk}/').content.decode()
t('vendedor: sem botões e sem o histórico', 'data-acao-editar' not in html_v and 'data-historico-venda' not in html_v)
t('nada foi para o histórico com as tentativas', not RegistroAlteracao.objects.filter(venda_id=venda.pk).exists())

print('== SUPERADMIN: TELA ==')
html_a = ca.get(f'/vendas/{venda.pk}/').content.decode()
t('detalhe com Editar, Excluir e Histórico', 'data-acao-editar' in html_a and 'data-acao-excluir' in html_a
  and 'data-historico-venda' in html_a and 'Nenhuma alteração depois do lançamento' in html_a)
r = ca.get(f'/vendas/{venda.pk}/editar/')
html_e = r.content.decode()
t('formulário de edição abre', r.status_code == 200 and 'name="versao"' in html_e and f'p{aparelho.pk}_serial' in html_e
  and f's{alta.pk}_plano' in html_e and 'pg0_forma' in html_e and 'name="motivo"' in html_e)
t('aba Histórico no menu do SUPERADMIN', '/vendas/historico/' in html_e)

print('== MOTIVO E VALIDAÇÃO ==')
r = editar(venda, cliente_nome='Zz Cliente Novo', motivo='')
venda.refresh_from_db()
t('sem motivo: recusa e nada muda', r.status_code == 200 and 'motivo da alteração' in r.content.decode()
  and venda.cliente_nome == 'Zz Cliente Um' and not RegistroAlteracao.objects.filter(venda_id=venda.pk).exists())
t('erro devolve o que foi digitado', 'value="Zz Cliente Novo"' in r.content.decode())
r = editar(venda, motivo='teste de validação', **{f'p{aparelho.pk}_serial': '490154203237519'})
t('IMEI com dígito errado recusado', 'informe o IMEI' in r.content.decode())
r = editar(venda, motivo='teste de validação', **{f'p{aparelho.pk}_serial': imei(2)})
t('IMEI já vendido em outra venda recusado', f'já foi vendido na venda #{venda_b.pk}' in r.content.decode())
r = editar(venda, motivo='teste de validação', **{f's{alta.pk}_plano': str(fibra.pk)})
t('fibra sem número da ordem recusada', 'informe o número da ordem' in r.content.decode())
r = editar(venda, motivo='teste de validação', **{f's{alta.pk}_portabilidade': '1'})
t('portabilidade sem operadora recusada', 'operadora de origem' in r.content.decode())
r = editar(venda, motivo='teste de validação', **{f's{alta.pk}_valor': '-5'})
t('valor do plano negativo recusado', 'valor do plano inválido' in r.content.decode())
r = editar(venda, motivo='teste de validação', **{f'p{capa.pk}_sugerido': '60'})
t('pagamento que não fecha com o novo total recusado', 'Os pagamentos somam' in r.content.decode())
r = editar(venda, motivo='teste de validação', cliente_cpf='123.456.789-00')
t('CPF inválido recusado', 'CPF/CNPJ do cliente inválido' in r.content.decode())
r = editar(venda, motivo='teste de validação', **{f'p{aparelho.pk}_remover': '1', f'p{capa.pk}_remover': '1',
                                                 f's{alta.pk}_remover': '1'})
t('não deixa a venda sem item (manda excluir)', 'exclua a venda' in r.content.decode())
venda.refresh_from_db()
t('nenhum erro gravou nada', not RegistroAlteracao.objects.filter(venda_id=venda.pk).exists()
  and venda.produtos.count() == 2 and venda.servicos.count() == 1)

print('== EDIÇÃO: CABEÇALHO, PRODUTO, SERVIÇO, PAGAMENTO ==')
motivo = 'Cliente pediu correção do nome; capa era a premium'
r = editar(venda, motivo=motivo, cliente_nome='Zz Cliente Um Corrigido', observacao='conferido', loja=str(loja2.pk),
           vendedor=str(vend.pk), comprovante_fiscal='NFE', estoque_avancado='1',
           **{f'p{capa.pk}_sugerido': '60,00', 'pg0_valor': '1060,00', f's{alta.pk}_acesso': '(27) 99777-1234',
              f'p{aparelho.pk}_serial': imei(3)})
t('edição gravada (redireciona para a venda)', r.status_code == 302 and r.url == f'/vendas/{venda.pk}/', r.status_code)
venda.refresh_from_db()
capa.refresh_from_db()
aparelho.refresh_from_db()
alta.refresh_from_db()
t('cabeçalho mudou', venda.cliente_nome == 'Zz Cliente Um Corrigido' and venda.loja == loja2 and venda.pdv_nome == loja2.name
  and venda.vendedor == vend and venda.comprovante_fiscal == 'NFE' and venda.estoque_avancado and venda.observacao == 'conferido')
t('capa: valor final seguiu o recálculo (não estava editado)', capa.valor_sugerido == D('60.00') and capa.valor_venda == D('60.00')
  and capa.valor_calculado == D('60.00') and not capa.valor_editado, (capa.valor_venda, capa.valor_calculado))
t('IMEI trocado', aparelho.serial == imei(3))
t('serviço: número de acesso e linhas', alta.numero_acesso == '27997771234' and alta.linhas[0] == '27997771234'
  and '27998765432' not in alta.linhas, alta.linhas)
pg = venda.pagamentos.get()
t('pagamento: valor novo', pg.valor == D('1060.00') and pg.nsu == '123456' and pg.parcelas == 2 and venda.forma_pagamento == 'CARTAO')
regs = RegistroAlteracao.objects.filter(venda_id=venda.pk)
t('todas as linhas com motivo, usuário e o mesmo lote', regs.exists() and all(h.motivo == motivo and h.usuario == admin for h in regs)
  and regs.values('lote').distinct().count() == 1)
h = regs.get(tipo='VENDA', campo='cliente_nome')
t('cabeçalho: antes e depois', h.antes == 'Zz Cliente Um' and h.depois == 'Zz Cliente Um Corrigido' and h.objeto_id == venda.pk)
h = regs.get(tipo='VENDA', campo='loja')
t('loja pelo nome', h.antes == loja.name and h.depois == loja2.name)
h = regs.get(tipo='VENDA', campo='vendedor')
t('vendedor pelo nome', h.antes == 'Ana Super' and h.depois == 'Beto Vendedor', (h.antes, h.depois))
h = regs.get(tipo='VENDA', campo='comprovante_fiscal')
t('comprovante pelo rótulo', h.antes == 'NFC-e (Cupom Fiscal)' and h.depois == 'NF-e (DANFE)')
h = regs.get(tipo='VENDA_PRODUTO', objeto_id=capa.pk, campo='valor_sugerido')
t('produto: R$ no antes e depois', h.antes == 'R$ 50,00' and h.depois == 'R$ 60,00' and 'ZZ Capa' in h.objeto_rotulo)
t('produto: valor final também no histórico', regs.filter(tipo='VENDA_PRODUTO', objeto_id=capa.pk, campo='valor_venda',
                                                           depois='R$ 60,00').exists())
t('IMEI no histórico', regs.filter(tipo='VENDA_PRODUTO', objeto_id=aparelho.pk, campo='serial', antes=imei(1), depois=imei(3)).exists())
h = regs.get(tipo='VENDA_SERVICO', campo='numero_acesso')
t('serviço: telefone formatado', h.antes == '(27) 99876-5432' and h.depois == '(27) 99777-1234', (h.antes, h.depois))
h = regs.get(tipo='VENDA_PAGAMENTO')
t('pagamento: resumo legível', h.antes == 'Cartão de crédito R$ 1.050,00 (NSU 123456, 2x)'
  and h.depois == 'Cartão de crédito R$ 1.060,00 (NSU 123456, 2x)', (h.antes, h.depois))
t('produto sem mudança não aparece', not regs.filter(tipo='VENDA_PRODUTO', objeto_id=aparelho.pk, campo='valor_venda').exists())
html_a = ca.get(f'/vendas/{venda.pk}/').content.decode()
t('detalhe mostra o histórico com o motivo', 'Cliente pediu correção do nome' in html_a and 'R$ 50,00' in html_a and 'Editada' in html_a)
t('gestor também vê', 'Cliente pediu correção do nome' in cg.get(f'/vendas/{venda.pk}/').content.decode())

print('== CONCORRÊNCIA ==')
velho = post_de(venda, motivo='edição com tela velha', observacao='tela velha')
r = editar(venda, motivo='outra pessoa mexeu antes', observacao='primeira')
t('primeira edição passa', r.status_code == 302)
antes_n = RegistroAlteracao.objects.filter(venda_id=venda.pk).count()
r = ca.post(f'/vendas/{venda.pk}/editar/', velho, follow=True)
venda.refresh_from_db()
t('formulário aberto antes é recusado', venda.observacao == 'primeira' and 'foi alterada depois que você abriu a tela' in r.content.decode()
  and RegistroAlteracao.objects.filter(venda_id=venda.pk).count() == antes_n)

print('== RECÁLCULO: VIVO+, PIX, MISTO, REMOVER, PLANO ==')
r = editar(venda, motivo='remover a capa lançada por engano', **{f'p{capa.pk}_remover': '1', 'pg0_valor': '1000'})
t('capa removida', r.status_code == 302 and not VendaProduto.objects.filter(pk=capa.pk).exists())
t('remoção no histórico com resumo e dados', RegistroAlteracao.objects.filter(venda_id=venda.pk, tipo='VENDA_PRODUTO', campo='removido',
                                                                            objeto_id=capa.pk).exists()
  and json.loads(RegistroAlteracao.objects.get(venda_id=venda.pk, campo='dados', objeto_id=capa.pk).antes)['nome_produto'] == 'ZZ Capa')
r = editar(venda, motivo='cliente pagou metade no Pix', **{'pg0_forma': 'PIX', 'pg0_valor': '500', 'pg0_nsu': '', 'pg0_parcelas': '',
                                                          'pg1_forma': 'CARTAO', 'pg1_valor': '500', 'pg1_nsu': '777888',
                                                          'pg1_parcelas': '3'})
venda.refresh_from_db()
t('dois pagamentos: forma MISTO, NSU/parcelas do cartão', r.status_code == 302 and venda.forma_pagamento == 'MISTO'
  and venda.nsu == '777888' and venda.parcelas == 3 and venda.pagamentos.count() == 2, (venda.forma_pagamento, venda.nsu))
r = editar(venda, motivo='desconto no pix esquecido', desconto_pix='100', pg0_valor='400')
venda.refresh_from_db()
aparelho.refresh_from_db()
t('desconto no Pix repartido no item', r.status_code == 302 and venda.desconto_pix == D('100.00') and aparelho.desconto_pix == D('100.00')
  and aparelho.valor_venda == D('900.00'), (venda.desconto_pix, aparelho.valor_venda))
r = editar(venda, motivo='cliente pagou pelo Vivo+', vivo_mais='1', pg0_valor='310', pg1_valor='500')
venda.refresh_from_db()
aparelho.refresh_from_db()
# 1000 − Vivo+ 10% = 900; − Pix 100 = 800 (310 + 500 ≠ 800 → recusa); manda 300 + 500
t('Vivo+ muda o total: pagamento que não fecha recusado', not venda.vivo_mais and 'Os pagamentos somam' in r.content.decode())
r = editar(venda, motivo='cliente pagou pelo Vivo+', vivo_mais='1', pg0_valor='300', pg1_valor='500')
venda.refresh_from_db()
aparelho.refresh_from_db()
t('Vivo+ recalcula o item', r.status_code == 302 and venda.vivo_mais and venda.vivo_mais_percentual == D('10.00')
  and aparelho.desconto_vivo_mais == D('100.00') and aparelho.valor_calculado == D('900.00') and aparelho.valor_venda == D('800.00'),
  (aparelho.desconto_vivo_mais, aparelho.valor_venda))
r = editar(venda, motivo='valor combinado com o gerente', **{f'p{aparelho.pk}_final': '880', 'pg0_valor': '280'})
aparelho.refresh_from_db()
t('valor final digitado: editado à mão por quem editou', r.status_code == 302 and aparelho.valor_editado
  and aparelho.editado_por == admin and aparelho.valor_venda == D('780.00') and aparelho.valor_calculado == D('900.00'),
  (aparelho.valor_venda, aparelho.valor_editado))
r = editar(venda, motivo='plano era controle', **{f's{alta.pk}_plano': str(ctrl.pk)})
alta.refresh_from_db()
t('plano trocado: valor segue o plano e delta recalculado', r.status_code == 302 and alta.plano == ctrl
  and alta.valor_plano == D('79.90') and alta.delta == D('79.90') and alta.segmentacao == 'CONTROLE', (alta.valor_plano, alta.delta))
r = editar(venda, motivo='fibra na mesma venda', **{f's{alta.pk}_plano': str(fibra.pk), f's{alta.pk}_ordem': 'ORD-12345',
                                                    f's{alta.pk}_valor_anterior': '50'})
alta.refresh_from_db()
t('fibra com ordem e valor anterior: delta = plano − anterior', r.status_code == 302 and alta.numero_ordem == 'ORD-12345'
  and alta.delta == D('69.90'), (alta.numero_ordem, alta.delta))
r = editar(venda, motivo='era portabilidade da Tim', **{f's{alta.pk}_plano': str(pos.pk), f's{alta.pk}_portabilidade': '1',
                                                        f's{alta.pk}_operadora': 'TIM'})
alta.refresh_from_db()
t('portabilidade com operadora; ordem some fora da fibra', r.status_code == 302 and alta.portabilidade
  and alta.operadora_origem == 'TIM' and alta.numero_ordem == '')
t('operadora pelo nome no histórico', RegistroAlteracao.objects.filter(venda_id=venda.pk, campo='operadora_origem', depois='Tim').exists())
r = editar(venda, motivo='CPF digitado errado', cliente_cpf=CPF2)
venda.refresh_from_db()
t('CPF trocado liga ao cadastro desse CPF', r.status_code == 302 and venda.cliente == Cliente.objects.get(cpf=CPF2)
  and RegistroAlteracao.objects.filter(venda_id=venda.pk, campo='cliente').exists())
r = editar(venda, motivo='nada de verdade')
t('enviar sem mudar nada não grava', r.status_code == 302 and not RegistroAlteracao.objects.filter(motivo='nada de verdade').exists())

print('== EXCLUSÃO ==')
r = ca.post(f'/vendas/{venda_b.pk}/excluir/', {'motivo': '', 'confirmo': '1'})
t('sem motivo não exclui', Venda.objects.filter(pk=venda_b.pk).exists())
r = ca.post(f'/vendas/{venda_b.pk}/excluir/', {'motivo': 'venda duplicada'})
t('sem a confirmação não exclui', Venda.objects.filter(pk=venda_b.pk).exists())
rec = RecebivelCartao.objects.create(data_venda=date.today(), nsu='999111', valor_bruto=D('900'), identificador='zz-ed-1',
                                     venda=venda_b, status='CONCILIADO')
r = ca.post(f'/vendas/{venda_b.pk}/excluir/', {'motivo': 'venda duplicada', 'confirmo': '1'}, follow=True)
t('venda conciliada no Financeiro não se exclui (com o aviso)', Venda.objects.filter(pk=venda_b.pk).exists()
  and 'Financeiro › Cartões' in r.content.decode())
t('o aviso aparece no detalhe', 'Solte o par em Financeiro' in ca.get(f'/vendas/{venda_b.pk}/').content.decode())
rec.venda, rec.status = None, 'PENDENTE'
rec.save()
ids_p = list(venda_b.produtos.values_list('pk', flat=True))
r = ca.post(f'/vendas/{venda_b.pk}/excluir/', {'motivo': 'venda lançada em duplicidade', 'confirmo': '1',
                                                 'versao': edicao.versao(venda_b)})
t('exclusão vai para o histórico geral', r.status_code == 302 and r.url == '/vendas/historico/')
t('venda e filhos apagados', not Venda.objects.filter(pk=venda_b.pk).exists() and not VendaProduto.objects.filter(pk__in=ids_p).exists()
  and not PagamentoVenda.objects.filter(venda_id=venda_b.pk).exists())
foto_h = RegistroAlteracao.objects.get(venda_id=venda_b.pk, tipo='VENDA_EXCLUIDA', campo='fotografia')
foto = json.loads(foto_h.antes)
t('fotografia completa no histórico', foto['id'] == venda_b.pk and foto['cliente_nome'] == 'Zz Cliente Dois'
  and foto['produtos'][0]['serial'] == imei(2) and foto['pagamentos'][0]['forma'] == 'PIX' and foto['total'] == '900.00'
  and foto['vendedor__nome'] == 'Ana Super' and foto['cliente__nome'].startswith('Zz Cliente Dois (') and foto_h.motivo == 'venda lançada em duplicidade' and foto_h.usuario == admin,
  {k: foto.get(k) for k in ('id', 'cliente_nome', 'total', 'vendedor__nome')})
t('resumo legível da exclusão', RegistroAlteracao.objects.filter(venda_id=venda_b.pk, campo='resumo', antes__contains='Zz Cliente Dois').exists())
j3 = lancar({'cliente': {'cpf': CPF2, 'nome': 'Zz Cliente Dois'},
             'produtos': [{'nome': 'ZZ Phone B', 'categoria_slv': 'aparelho', 'imei': imei(2), 'valor_sugerido': '900'}],
             'pagamentos': [{'forma': 'PIX', 'valor': '900'}]})
t('o IMEI da venda excluída pode ser lançado de novo', j3.get('ok'), j3)
venda_c = Venda.objects.get(pk=j3['id'])
r = ca.post(f'/vendas/{venda_c.pk}/excluir/', {'motivo': 'teste de tela velha', 'confirmo': '1', 'versao': 'velha'})
t('exclusão com tela velha recusada', Venda.objects.filter(pk=venda_c.pk).exists())

print('== HISTÓRICO GERAL ==')
html_h = ca.get('/vendas/historico/').content.decode()
t('lista edições e exclusões', 'Excluída' in html_h and 'Ana Super' in html_h and 'Editada' in html_h and 'venda lançada em duplicidade' in html_h
  and f'Venda #{venda_b.pk}' in html_h and 'Ver a venda como estava' in html_h and imei(2) in html_h)
html_h = ca.get(f'/vendas/historico/?acao=exclusao').content.decode()
t('filtro: só exclusões', 'Excluída' in html_h and 'Editada' not in html_h)
html_h = ca.get(f'/vendas/historico/?venda={venda.pk}&acao=edicao').content.decode()
t('filtro: venda e só edições', 'Editada' in html_h and 'Excluída' not in html_h and 'Cliente pediu correção do nome' in html_h)
html_h = ca.get('/vendas/historico/?usuario=ninguem-assim').content.decode()
t('filtro sem resultado', 'Nenhuma edição ou exclusão de venda com esses filtros' in html_h)
html_p = ca.get('/vendas/parametros/').content.decode()
t('parametrização não mistura edição de venda', 'Cliente pediu correção do nome' not in html_p)
r = ca.get(f'/vendas/{venda_b.pk}/editar/')
t('venda excluída: edição dá 404', r.status_code == 404)

print('== INCLUIR PRODUTO PELA EDIÇÃO ==')
from vendas.models import ItemPreco  # noqa: E402
tab_capa = ItemPreco.objects.create(categoria='PRODUTOS', nome='ZZ Capa Tabela', valor=D('59.90'), cod_sap='ZZCAPAT')
tab_phone = ItemPreco.objects.create(categoria='SMARTPHONES', nome='ZZ Phone Tabela', valor=D('1999.00'))
tab_sem = ItemPreco.objects.create(categoria='PRODUTOS', nome='ZZ Película Sem Preço', valor=None)
jd = lancar({'cliente': {'cpf': CPF, 'nome': 'Zz Cliente Um'},
             'produtos': [{'nome': 'ZZ Phone D', 'categoria_slv': 'aparelho', 'imei': imei(10), 'valor_sugerido': '900'}],
             'pagamentos': [{'forma': 'PIX', 'valor': '900'}]}, cliente=cv)
t('vendedor lança a própria venda', jd.get('ok'), jd)
venda_d = Venda.objects.get(pk=jd['id'])
phone_d = venda_d.produtos.get()
html_vd = cv.get(f'/vendas/{venda_d.pk}/').content.decode()
t('vendedor vê o histórico da própria venda (sem botões)', 'data-historico-venda' in html_vd
  and 'data-acao-editar' not in html_vd)
vend2 = User.objects.create_user(username='zz.ed.vend2', email='zz.ed.vend2@exemplo-teste.local', password='S3nha!teste',
                                 first_name='Caio', last_name='Outro', hierarchy='PADRAO', sector=loja)
AcessoVendas.objects.create(user=vend2, permissoes=['modulo.ver', 'vendas.lancar'])
cv2 = Client()
cv2.force_login(vend2)
t('outro vendedor não vê o histórico dessa venda', 'data-historico-venda' not in cv2.get(f'/vendas/{venda_d.pk}/').content.decode())
html_ed = ca.get(f'/vendas/{venda_d.pk}/editar/').content.decode()
t('tela de edição tem a busca para incluir produto', 'id="vdIncluir"' in html_ed and 'name="novos_produtos"' in html_ed)
r = editar(venda_d, motivo='esqueceu a capa', novos_produtos='1', np0_preco_id=str(tab_capa.pk))
t('incluir sem ajustar o pagamento: recusa', 'Os pagamentos somam' in r.content.decode() and venda_d.produtos.count() == 1)
r = editar(venda_d, motivo='esqueceu a capa', novos_produtos='1', np0_preco_id=str(tab_capa.pk), np0_serial='SN-77',
           np0_valor_final='55')
html_err = r.content.decode()
t('erro devolve a linha nova com o digitado', 'id="vdNovosDigitados"' in html_err and f'"preco_id": {tab_capa.pk}' in html_err
  and '"serial": "SN-77"' in html_err and '"valor_final": "55"' in html_err)
jb = ca.get(f'/vendas/precos/buscar/?ids={tab_capa.pk}').json()
t('busca por id (usada para refazer a linha) devolve o item', [x['id'] for x in jb['results']] == [tab_capa.pk])
r = editar(venda_d, motivo='esqueceu a capa', novos_produtos='1', np0_preco_id=str(tab_capa.pk), pg0_valor='959,90')
nova = venda_d.produtos.filter(preco=tab_capa).first()
t('capa da tabela incluída com o preço da tabela', r.status_code == 302 and nova is not None
  and nova.valor_venda == D('59.90') and nova.categoria_slv == 'essencial' and nova.nome_produto == 'ZZ Capa Tabela',
  getattr(nova, 'valor_venda', None))
h = RegistroAlteracao.objects.filter(venda_id=venda_d.pk, tipo='VENDA_PRODUTO', campo='incluido', objeto_id=getattr(nova, 'pk', 0)).first()
t('inclusão no histórico com motivo e resumo', h is not None and h.motivo == 'esqueceu a capa' and 'ZZ Capa Tabela' in h.depois
  and h.usuario == admin and h.antes == '')
t('detalhe mostra "Produto incluído"', 'Produto incluído' in ca.get(f'/vendas/{venda_d.pk}/').content.decode())
r = editar(venda_d, motivo='trocar aparelho', novos_produtos='1', np0_preco_id=str(tab_phone.pk), np0_serial='123')
t('aparelho novo com IMEI inválido recusado', 'informe o IMEI' in r.content.decode())
r = editar(venda_d, motivo='trocar aparelho', novos_produtos='1', np0_preco_id=str(tab_phone.pk), np0_serial=imei(10))
t('IMEI que já está na venda recusado', 'repetido nesta venda' in r.content.decode())
r = editar(venda_d, motivo='trocar aparelho', novos_produtos='1', np0_preco_id=str(tab_phone.pk), np0_serial=imei(3))
t('IMEI vendido em outra venda recusado', f'já foi vendido na venda #{venda.pk}' in r.content.decode())
t('nenhuma recusa gravou', venda_d.produtos.count() == 2)
r = editar(venda_d, motivo='aparelho errado: era o da tabela', novos_produtos='1', np0_preco_id=str(tab_phone.pk),
           np0_serial=imei(10), pg0_valor='2058,90', **{f'p{phone_d.pk}_remover': '1'})
venda_d.refresh_from_db()
novo_phone = venda_d.produtos.filter(preco=tab_phone).first()
t('trocar o aparelho mantendo o IMEI (remove um, inclui outro)', r.status_code == 302 and novo_phone is not None
  and novo_phone.serial == imei(10) and not VendaProduto.objects.filter(pk=phone_d.pk).exists()
  and venda_d.produtos.count() == 2)
r = editar(venda_d, motivo='película sem preço', novos_produtos='1', np0_preco_id=str(tab_sem.pk), pg0_valor='2058,90')
t('item sem preço e sem valor: recusa', 'informe o valor' in r.content.decode())
r = editar(venda_d, motivo='película sem preço', novos_produtos='1', np0_preco_id=str(tab_sem.pk), np0_valor_final='30',
           pg0_valor='2088,90')
pel = venda_d.produtos.filter(preco=tab_sem).first()
t('item sem preço com o valor digitado', r.status_code == 302 and pel is not None and pel.valor_venda == D('30.00'))
venda_d.refresh_from_db()
t('total e tipo da venda seguem os itens', venda_d.pagamentos.get().valor == D('2088.90'))

print('== ADMIN DO DJANGO SÓ LEITURA ==')
from django.contrib import admin as dj_admin  # noqa: E402
from django.test import RequestFactory  # noqa: E402
req = RequestFactory().get('/admin/')
admin.is_superuser = admin.is_staff = True
req.user = admin
va = dj_admin.site._registry[Venda]
t('admin: não adiciona, não altera, não exclui venda', not va.has_add_permission(req) and not va.has_change_permission(req, venda_d)
  and not va.has_delete_permission(req, venda_d))
t('admin: inlines também só leitura', all(not i.has_change_permission(req, venda_d) and not i.has_delete_permission(req, venda_d)
                                         and not i.has_add_permission(req, venda_d) for i in va.get_inline_instances(req, venda_d))
  and va.has_view_permission(req, venda_d))

print('== TEMPLATES ==')
pastas = pathlib.Path(__file__).resolve().parent / 'templates' / 'vendas'
multilinha = []
for nome in ('venda_detail.html', 'venda_editar.html', 'historico.html', '_historico_lote.html', '_base_vendas.html'):
    texto = (pastas / nome).read_text()
    for m in re.finditer(r'\{#(.*?)#\}', texto, re.S):
        if '\n' in m.group(1):
            multilinha.append(nome)
    if texto.count('{#') != texto.count('#}'):
        multilinha.append(nome + ' (desparelhado)')
t('nenhum {# #} em várias linhas', not multilinha, multilinha)
paginas = [ca.get(f'/vendas/{venda.pk}/').content.decode(), ca.get(f'/vendas/{venda.pk}/editar/').content.decode(),
           ca.get('/vendas/historico/').content.decode()]
t('nenhum comentário vazando na tela', all('{#' not in p and '#}' not in p and '{% comment' not in p for p in paginas))

print(f'\n{ok} ok, {fail} falha(s)')
sys.exit(1 if fail else 0)
