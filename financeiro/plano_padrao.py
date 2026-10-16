"""Um plano de contas de partida para uma rede de lojas de telefonia.

Só é criado quando alguém aperta "Criar plano padrão" com o plano vazio — e
depois cada conta se edita, desativa ou ganha irmãs na tela.
"""
from django.db import transaction

from . import auditoria
from .models import PlanoConta

# (código, nome); o tipo vem da raiz (1 receita, 2 despesa); sintética = tem filhas.
PADRAO = [
    ('1', 'Receitas'),
    ('1.1', 'Receita de vendas'),
    ('1.1.01', 'Venda de aparelhos'),
    ('1.1.02', 'Venda de acessórios e eletrônicos'),
    ('1.1.03', 'Comissões da operadora (ativações e serviços)'),
    ('1.1.04', 'Serviços (Vivo+, seguros, Renova)'),
    ('1.2', 'Outras receitas'),
    ('1.2.01', 'Rendimentos financeiros'),
    ('1.2.02', 'Reembolsos e ressarcimentos'),
    ('1.2.99', 'Outras receitas'),
    ('2', 'Despesas'),
    ('2.1', 'Pessoal'),
    ('2.1.01', 'Salários'),
    ('2.1.02', 'Comissões e premiações'),
    ('2.1.03', 'Encargos (INSS, FGTS)'),
    ('2.1.04', 'Benefícios (VT, VR, plano de saúde)'),
    ('2.1.05', 'Rescisões e férias'),
    ('2.1.06', 'Pró-labore'),
    ('2.2', 'Ocupação'),
    ('2.2.01', 'Aluguel'),
    ('2.2.02', 'Condomínio e IPTU'),
    ('2.2.03', 'Energia elétrica'),
    ('2.2.04', 'Água'),
    ('2.2.05', 'Internet e telefonia'),
    ('2.2.06', 'Manutenção, limpeza e segurança'),
    ('2.3', 'Mercadorias'),
    ('2.3.01', 'Compra de aparelhos'),
    ('2.3.02', 'Compra de acessórios e eletrônicos'),
    ('2.3.03', 'Fretes'),
    ('2.4', 'Administrativas'),
    ('2.4.01', 'Contabilidade'),
    ('2.4.02', 'Sistemas e softwares'),
    ('2.4.03', 'Material de escritório e consumo'),
    ('2.4.04', 'Viagens e deslocamento'),
    ('2.4.05', 'Marketing'),
    ('2.4.06', 'Jurídico'),
    ('2.5', 'Financeiras'),
    ('2.5.01', 'Tarifas bancárias'),
    ('2.5.02', 'Taxas de cartão (MDR)'),
    ('2.5.03', 'Juros e multas pagos'),
    ('2.5.04', 'IOF'),
    ('2.6', 'Impostos e taxas'),
    ('2.6.01', 'Simples Nacional (DAS)'),
    ('2.6.02', 'PIS'),
    ('2.6.03', 'Cofins'),
    ('2.6.04', 'ICMS'),
    ('2.6.05', 'IRPJ e CSLL'),
    ('2.6.06', 'ISS'),
    ('2.6.07', 'Taxas e alvarás'),
    ('2.9', 'Outras despesas'),
    ('2.9.99', 'Outras despesas'),
]


@transaction.atomic
def criar_plano_padrao(usuario=None, request=None):
    if PlanoConta.objects.exists():
        return 0
    codigos = {c for c, _n in PADRAO}
    criadas = {}
    for codigo, nome in PADRAO:
        pai = criadas.get(codigo.rsplit('.', 1)[0]) if '.' in codigo else None
        tem_filhas = any(c.startswith(codigo + '.') for c in codigos)
        criadas[codigo] = PlanoConta.objects.create(
            codigo=codigo, nome=nome, tipo='RECEITA' if codigo.startswith('1') else 'DESPESA',
            pai=pai, analitica=not tem_filhas)
    auditoria.registrar(request, 'CRIAR', f'Criou o plano de contas padrão ({len(criadas)} contas)', area='plano',
                        usuario=usuario)
    return len(criadas)
