"""Vendas: base de clientes do Vivo GO (/vendas/clientes/ e a busca do CPF na Nova venda).

Pedido: "ao buscar o CPF do cliente, use o VIVOGO_MYSQL_URL para ver se o cliente
existe, pré-preenchendo os dados; crie a aba /vendas/clientes com a base
refletida (importe no Postgres e vá adicionando de 5 em 5 minutos os novos)".

O MySQL aqui é um dublê que devolve linhas no formato das views
vendas_produtos_2026 / vendas_servicos_2026. O que este teste cobre:

- normalização (CPF formatado, linha, chave estável com linhas idênticas);
- incremental (só o novo, a partir da marca com folga) e completa (reconcilia);
- resumo do cliente; o cadastro do portal não é sobrescrito;
- MySQL fora do ar: registra o erro, não quebra;
- agendador: 5 minutos, um processo só, teste não dispara;
- Nova venda: CPF da base do Vivo GO pré-preenche nome, telefone e plano;
  pré-análise com as compras do Vivo GO; histórico rápido pelo espelho;
- aba Clientes: busca, filtros, ficha; recorte da loja para o vendedor.

Caches em memória, transação desfeita no fim.
"""
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-vg'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-vg-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from users.models import Sector
from vendas import vivogo
from vendas.models import Cliente, CompraVivoGo, Plano, SincronizacaoVivoGo

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


def gerar_cpf(base9):
    d = [int(c) for c in base9]
    for tamanho in (9, 10):
        d.append(sum(d[i] * (tamanho + 1 - i) for i in range(tamanho)) * 10 % 11 % 10)
    return ''.join(map(str, d))


def fmt(c):
    return f'{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}'


CPF_A, CPF_B, CPF_PORTAL = gerar_cpf('900111222'), gerar_cpf('900333444'), gerar_cpf('900555666')
PDV = 'ZZ PDV TESTE'


def prod(cpf, nome, id_venda, dia, item, receita, ins, pdv=PDV, linha='27 99876-5432'):
    return ('P', {'COORDENACAO': 'ZZ', 'PDV': pdv, 'ID_VENDA': id_venda, 'DATA_VENDA': dia, 'DATA_INSERCAO_VENDA': ins,
                  'TIPO_PRODUTO': 'Produto Vivo', 'NOME_PRODUTO': item.upper(), 'NOME_COMERCIAL_PRODUTO': item, 'SKU': 'ZZSKU',
                  'SUBCATEGORIA': 'Smartphone', 'IMEI': '35000000000000', 'QTD_VENDIDA': 1, 'RECEITA': D(receita),
                  'VENDEDOR': 'ZZ VENDEDORA', 'CPF_VENDEDOR': '000.000.000-00', 'CPF_CLIENTE': fmt(cpf),
                  'NOME_CLIENTE': nome, 'NUMERO_ACESSO': linha, 'PILAR': 'Smartphone'})


def serv(cpf, nome, id_venda, dia, servico, plano, receita, ins, pdv=PDV, linha='27 99876-5432', cancel=None):
    return ('S', {'COORDENACAO': 'ZZ', 'PDV': pdv, 'ID_VENDA': id_venda, 'DATA_VENDA': dia, 'DATA_INSERCAO_VENDA': ins,
                  'PLANO': plano, 'VENDEDOR': 'ZZ VENDEDORA', 'CPF_VENDEDOR': '000.000.000-00', 'CPF_CLIENTE': fmt(cpf),
                  'DOCUMENTO_CLIENTE': fmt(cpf), 'NUMERO_ACESSO': linha, 'NOME_CLIENTE': nome, 'RECEITA': D(receita),
                  'SERVICO': servico, 'PILAR': 'Móvel', 'STATUS': None, 'STATUS_BKO': 'Não avaliado',
                  'STATUS_SERVICO': 'Confirmado', 'DATA_CANCELAMENTO': cancel, 'OBSERVACAO': None, 'NOVA RECEITA': D(receita)})


class MySqlFalso:
    def __init__(self, linhas):
        self.linhas = linhas if isinstance(linhas, Exception) else list(linhas)
        self.pedidos = []

    def __call__(self, desde=None):
        self.pedidos.append(desde)
        if isinstance(self.linhas, Exception):
            raise self.linhas
        corte = timezone.localtime(desde).replace(tzinfo=None) if desde else None
        return [(tp, r) for tp, r in self.linhas if corte is None or r['DATA_INSERCAO_VENDA'] >= corte]


hoje = timezone.localdate()
base_ins = datetime(2026, 10, 9, 10, 0)
marcador = transaction.atomic()
marcador.__enter__()
try:
    # Estado de partida da sincronização: as linhas reais não entram no teste.
    SincronizacaoVivoGo.objects.all().delete()
    escopo = CompraVivoGo.objects.filter(cpf__in=[CPF_A, CPF_B, CPF_PORTAL])

    print('== NORMALIZAÇÃO ==')
    duas_iguais = [prod(CPF_A, 'ZZ ANA', '9001', date(2026, 9, 1), 'Capa ZZ', '50', base_ins)] * 2
    n = vivogo.normalizar(duas_iguais)
    t('CPF formatado vira só dígitos', n[0]['cpf'] == CPF_A and n[0]['numero_acesso'] == '27998765432')
    t('linhas idênticas têm chaves diferentes e estáveis', n[0]['chave'] != n[1]['chave']
      and [x['chave'] for x in vivogo.normalizar(duas_iguais)] == [x['chave'] for x in n])
    t('segmentação pelo nome do plano', [vivogo.segmentacao_do_plano(p) for p in
                                         ('VIVO CONTROLE 25GB', 'PRÉ TURBO', 'VIVO EMPRESAS 20GB', 'POS 50GB', '')]
      == ['CONTROLE', 'PRE', 'EMPRESAS', 'POS', ''])

    print('== CARGA ==')
    Cliente.objects.create(cpf=CPF_PORTAL, nome='Zz Nome do Portal', telefone='27911112222', origem='PORTAL')
    linhas = [
        prod(CPF_A, 'ZZ ANA SOUZA', '9001', date(2026, 9, 1), 'Galaxy ZZ', '1999.00', base_ins - timedelta(days=30)),
        serv(CPF_A, 'ZZ ANA SOUZA', '9002', date(2026, 10, 2), 'Alta Pós', 'ZZ PÓS 50GB', '149.90', base_ins - timedelta(days=7)),
        serv(CPF_B, 'ZZ BETO', '9003', date(2026, 10, 3), 'Alta Controle', 'ZZ CONTROLE 25GB', '79.90', base_ins, pdv='ZZ OUTRA LOJA',
             linha='27 3333-4444'),
        serv(CPF_PORTAL, 'NOME NO VIVOGO', '9004', date(2026, 10, 4), 'Troca', 'ZZ PÓS 50GB', '149.90', base_ins, linha='27 95555-6666'),
    ]
    mysql = MySqlFalso(linhas)
    resumo = vivogo.sincronizar(leitor=mysql, escopo=escopo, agora=timezone.now())
    t('primeira leitura é completa', mysql.pedidos == [None] and resumo.startswith('completa'), resumo)
    a = Cliente.objects.get(cpf=CPF_A)
    t('cliente do Vivo GO criado com o resumo', a.origem == 'VIVOGO' and a.nome == 'ZZ ANA SOUZA' and a.qtd_vendas == 2
      and a.total_gasto == D('2148.90') and a.primeira_compra == date(2026, 9, 1) and a.ultima_compra == date(2026, 10, 2)
      and a.pdv_ultimo == PDV, (a.qtd_vendas, a.total_gasto))
    t('telefone e plano pré-preenchidos', a.telefone == '27998765432' and a.plano_nome == 'ZZ PÓS 50GB'
      and a.segmentacao == 'POS' and a.valor_pago == D('149.90'))
    p = Cliente.objects.get(cpf=CPF_PORTAL)
    t('cadastro do portal não é sobrescrito', p.nome == 'Zz Nome do Portal' and p.telefone == '27911112222'
      and p.linha == '27955556666' and p.qtd_vendas == 1)
    sinc = SincronizacaoVivoGo.get()
    t('marca = maior DATA_INSERCAO lida', timezone.localtime(sinc.marca).replace(tzinfo=None) == base_ins)

    print('== INCREMENTAL ==')
    mysql.linhas.append(prod(CPF_B, 'ZZ BETO', '9005', date(2026, 10, 9), 'Fone ZZ', '299.00', base_ins + timedelta(minutes=3),
                             pdv='ZZ OUTRA LOJA', linha='27 3333-4444'))
    resumo = vivogo.sincronizar(leitor=mysql, escopo=escopo, agora=timezone.now())
    t('incremental pede a partir da marca − 2 h', mysql.pedidos[-1] == sinc.marca - timedelta(hours=2) and resumo.startswith('incremental'),
      (mysql.pedidos[-1], resumo))
    t('só a linha nova entra', '1 nova(s)' in resumo and CompraVivoGo.objects.filter(cpf=CPF_B).count() == 2, resumo)
    b = Cliente.objects.get(cpf=CPF_B)
    t('resumo do cliente atualizado', b.qtd_vendas == 2 and b.ultima_compra == date(2026, 10, 9))

    print('== COMPLETA ==')
    mysql.linhas = [l for l in mysql.linhas if l[1]['ID_VENDA'] != '9001']   # a venda 9001 sumiu do Vivo GO
    SincronizacaoVivoGo.objects.update(ultima_completa=timezone.now() - timedelta(days=2))
    resumo = vivogo.sincronizar(leitor=mysql, escopo=escopo, agora=timezone.now())
    a.refresh_from_db()
    t('completa diária reconcilia: o que saiu de lá sai daqui', resumo.startswith('completa') and '1 removida(s)' in resumo
      and a.qtd_vendas == 1 and a.total_gasto == D('149.90'), (resumo, a.qtd_vendas))

    print('== FALHA ==')
    try:
        vivogo.sincronizar(leitor=MySqlFalso(vivogo.VivoGoIndisponivel('fora do ar')), escopo=escopo)
        t('MySQL fora levanta VivoGoIndisponivel', False)
    except vivogo.VivoGoIndisponivel:
        t('MySQL fora levanta VivoGoIndisponivel', True)
    t('e o erro fica registrado', 'fora do ar' in SincronizacaoVivoGo.get().ultimo_erro)

    print('== AGENDADOR ==')
    t('processo de teste não dispara a leitura', vivogo.disparar_se_esta_na_hora() is False)

    print('== NOVA VENDA ==')
    loja = Sector.objects.create(name='Loja ZZ PDV Teste')
    admin = User.objects.create_user(username='zz.vg.adm', email='zz.vg.adm@exemplo-teste.local', password='S3nha!teste',
                                     first_name='Ana', last_name='Gestora', hierarchy='SUPERADMIN', is_superuser=True, sector=loja)
    vend = User.objects.create_user(username='zz.vg.vend', email='zz.vg.vend@exemplo-teste.local', password='S3nha!teste',
                                    first_name='Beto', last_name='Vendedor', hierarchy='PADRAO', sector=loja)
    ca, cv = Client(), Client()
    ca.force_login(admin)
    cv.force_login(vend)
    Plano.objects.create(nome='ZZ PÓS 50GB', segmentacao='POS', valor=D('149.90'))
    j = cv.get(f'/vendas/api/cliente/?cpf={fmt(CPF_A)}').json()
    c = j.get('cliente', {})
    t('CPF da base do Vivo GO é encontrado e pré-preenche', j['encontrado'] and c['nome'] == 'ZZ ANA SOUZA'
      and c['telefone'] == '27998765432' and c['origem'] == 'VIVOGO' and c['segmentacao'] == 'POS', j)
    t('plano do Vivo GO casa com o plano cadastrado de mesmo nome', c.get('plano_id') == Plano.objects.get(nome='ZZ PÓS 50GB').id)
    t('pré-análise traz o plano do Vivo GO', j['pre_analise']['plano']['fonte'] == 'Vivo GO')
    j = cv.get(f'/vendas/api/cliente/?cpf={CPF_B}').json()
    t('pré-análise com as compras de produto do Vivo GO', any(x['produto'] == 'Fone ZZ' and x['pdv'] == 'ZZ OUTRA LOJA'
                                                            for x in j['pre_analise']['compras']), j['pre_analise'])
    h = cv.get(f'/vendas/cliente/?cpf={CPF_B}').json()
    t('histórico do cliente sai do espelho (rápido)', h['encontrado'] and h['total'] == 2 and h['ultima'] == '09/10/2026', h)
    r = cv.get(f'/vendas/nova/?cpf={CPF_A}')
    t('Nova venda aceita ?cpf= (vindo da ficha)', r.status_code == 200 and 'doLink' in r.content.decode())

    print('== ABA CLIENTES ==')
    r = ca.get('/vendas/clientes/?q=ZZ')
    html = r.content.decode()
    t('admin vê a base com busca', r.status_code == 200 and 'ZZ ANA SOUZA' in html and 'ZZ BETO' in html and 'Zz Nome do Portal' in html)
    r = ca.get(f'/vendas/clientes/?q={CPF_B[:6]}')
    t('busca por CPF', 'ZZ BETO' in r.content.decode() and 'ZZ ANA SOUZA' not in r.content.decode())
    r = ca.get('/vendas/clientes/?q=33334444')
    t('busca pelo telefone/linha', 'ZZ BETO' in r.content.decode())
    r = ca.get('/vendas/clientes/?q=ZZ&situacao=recorrentes')
    t('filtro recorrentes', 'ZZ BETO' in r.content.decode() and 'ZZ ANA SOUZA' not in r.content.decode())
    r = ca.get('/vendas/clientes/?q=ZZ&situacao=portal')
    t('filtro cadastrados no portal', 'Zz Nome do Portal' in r.content.decode() and 'ZZ BETO' not in r.content.decode())
    r = cv.get('/vendas/clientes/?q=ZZ')
    html = r.content.decode()
    t('vendedor vê só os clientes da loja dele', 'ZZ ANA SOUZA' in html and 'ZZ BETO' not in html, 'recorte')
    r = cv.get(f'/vendas/clientes/{CPF_B}/')
    t('nem abre a ficha de cliente de outra loja', r.status_code == 404, r.status_code)
    r = cv.get(f'/vendas/clientes/{CPF_A}/')
    html = r.content.decode()
    t('ficha do cliente: histórico do Vivo GO e nova venda', r.status_code == 200 and 'Histórico no Vivo GO' in html
      and 'Alta Pós' in html and f'?cpf={CPF_A}' in html)
    r = cv.post('/vendas/clientes/sincronizar/')
    t('vendedor não força a sincronização', r.status_code == 404)
    r = ca.get('/vendas/')
    t('aba Clientes na navegação', 'fa-users mr-1.5"></i>Clientes' in r.content.decode())
finally:
    marcador.__exit__(Exception, Exception('rollback'), None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
