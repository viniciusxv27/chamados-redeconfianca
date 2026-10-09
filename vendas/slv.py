"""SLV — as regras do Documento de Requisitos v1.0 (out/2026), num lugar só.

A tela calcula para mostrar na hora ([Desempenho.NF002]); o servidor calcula de
novo para gravar. Nenhum total que chega do navegador é aceito como verdade:
o que vale é sugerido (tabela) − Renova − Vivo+, a menos que o vendedor tenha
editado o valor final — e aí fica gravado quem editou e o que o sistema tinha
calculado ([Confiabilidade.NF002]).

Referências entre colchetes apontam para os requisitos do documento.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

ZERO = Decimal('0.00')
CENTAVO = Decimal('0.01')


# ── Documentos e telefones ──────────────────────────────────────────────────
def so_digitos(valor):
    return re.sub(r'\D', '', str(valor or ''))


def cpf_valido(valor):
    """[Início da venda.RF002] / [NF001]: 11 dígitos, com os verificadores certos."""
    d = so_digitos(valor)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for tamanho in (9, 10):
        soma = sum(int(d[i]) * (tamanho + 1 - i) for i in range(tamanho))
        digito = (soma * 10) % 11 % 10
        if digito != int(d[tamanho]):
            return False
    return True


def formatar_cpf(valor):
    d = so_digitos(valor)
    return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}' if len(d) == 11 else d


def telefone_valido(valor):
    """DDD (11–99) + 8 ou 9 dígitos."""
    d = so_digitos(valor)
    if d.startswith('55') and len(d) in (12, 13):
        d = d[2:]
    return len(d) in (10, 11) and 11 <= int(d[:2]) <= 99


def numero_fake(valor):
    """[Venda de serviços.RF001] Número fictício: 999999999, 9000000000 e parecidos.

    Fictício é o número cuja parte depois do DDD é toda igual (99999-9999,
    0000-0000) ou um dígito seguido só de zeros (9000-0000, 90000-0000) — ou que
    nem chega a ter o tamanho de um telefone.
    """
    d = so_digitos(valor)
    if not d:
        return False
    if d in ('999999999', '9000000000') or len(d) < 8:
        return True
    corpo = d[2:] if len(d) in (10, 11) else d
    return len(set(corpo)) == 1 or bool(re.fullmatch(r'\d0+', corpo))


def formatar_telefone(valor):
    d = so_digitos(valor)
    if len(d) == 11:
        return f'({d[:2]}) {d[2:7]}-{d[7:]}'
    if len(d) == 10:
        return f'({d[:2]}) {d[2:6]}-{d[6:]}'
    return d


# ── Dinheiro ────────────────────────────────────────────────────────────────
def dinheiro(valor):
    """'1.234,56', '1234.56', 1234.56 → Decimal('1234.56'); vazio/inválido → None."""
    if valor is None or valor == '':
        return None
    if isinstance(valor, Decimal):
        return valor.quantize(CENTAVO, ROUND_HALF_UP)
    if isinstance(valor, (int, float)):
        return Decimal(str(valor)).quantize(CENTAVO, ROUND_HALF_UP)
    texto = str(valor).replace('R$', '').replace(' ', '').strip()
    if ',' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    try:
        return Decimal(texto).quantize(CENTAVO, ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


# ── Tabela de produtos ──────────────────────────────────────────────────────
APARELHOS = ('SMARTPHONES', 'SMARTPHONES_Demo', 'WATCHES_PL', 'DEVICES_ESPECIAIS')
ESSENCIAIS = ('PRODUTOS',)
# Coluna da tabela de aparelhos que diz a condição em cada grupo de plano
# ("3x", "10x"…). "-" ou vazio = aparelho não sai naquele plano.
SEM_CONDICAO = ('', '-', '--', 'N/A', 'NA', 'NÃO', 'NAO', 'X', '0')


# Pela coluna "Categoria" das abas que misturam tudo (PRODUTOS B2B): acessório é essencial.
CATEGORIAS_APARELHO = ('smartphone', 'smartwatch', 'celular')
CATEGORIAS_ESSENCIAL = ('capa', 'cabo', 'carregador', 'película', 'pelicula', 'acessório', 'acessorio', 'chip')


def categoria_slv(item):
    """Aparelho, eletrônico ou essencial — [Venda de produtos.RF002] passo 1."""
    extra = item.extra or {}
    rotulo = str(extra.get('CATEGORIA') or extra.get('Categoria') or '').strip().lower()
    if item.categoria in APARELHOS or rotulo in CATEGORIAS_APARELHO:
        return 'aparelho'
    if item.categoria in ESSENCIAIS or any(rotulo.startswith(c) for c in CATEGORIAS_ESSENCIAL):
        return 'essencial'
    return 'eletronico'


def _texto_extra(item, *chaves):
    extra = item.extra or {}
    for chave in chaves:
        if chave in extra and str(extra[chave]).strip():
            return str(extra[chave]).strip()
    return ''


def preco_sugerido(item, segmentacao='', grupamento=''):
    """[Venda de produtos.RF002] O valor do produto conforme o plano do cliente.

    Devolve ``(valor ou None, regra)``. Eletrônico e essencial: o valor da
    tabela pelo SKU. Aparelho: cliente Pré (ou sem plano) paga o preço Pré da
    tabela; os demais, o preço com plano — e, se a coluna do grupo do plano do
    cliente diz que o aparelho não sai nele, não há valor (o vendedor preenche,
    [Apêndice A, item 8]).
    """
    if categoria_slv(item) != 'aparelho':
        return (item.valor, 'tabela pelo SKU') if item.valor is not None else (None, 'sem valor na tabela')
    tem_coluna_pre = any(k in (item.extra or {}) for k in ('PRÉ', 'PRE-PAGO', 'PRÉ-PAGO'))
    if segmentacao in ('PRE', '') and tem_coluna_pre:
        pre = dinheiro(_texto_extra(item, 'PRÉ', 'PRE-PAGO', 'PRÉ-PAGO'))
        if pre:
            return pre, 'preço Pré da tabela' if segmentacao == 'PRE' else 'preço Pré (cliente sem plano)'
        if segmentacao == 'PRE':
            return None, 'sem preço Pré na tabela'
    if grupamento:
        condicao = _texto_extra(item, grupamento, grupamento.upper())
        if (item.extra or {}).get(grupamento) is not None and condicao.upper() in SEM_CONDICAO:
            return None, f'não disponível no plano {grupamento}'
    if item.valor is None:
        return None, 'sem valor na tabela'
    return item.valor, 'preço com plano'


# ── Cálculo do item ─────────────────────────────────────────────────────────
def calcular_item(sugerido, renova_vini=ZERO, renova_alied=ZERO, vivo_mais_percentual=None, qtde=1):
    """[Venda de produtos.RF003/RF004/RF005] sugerido − Renova − Vivo+ (por unidade).

    O Vivo+ incide sobre o que sobra depois do Renova. Devolve
    ``{'desconto_vivo_mais', 'valor_calculado'}``.
    """
    base = (sugerido or ZERO) - (renova_vini or ZERO) - (renova_alied or ZERO)
    desconto = ZERO
    if vivo_mais_percentual:
        desconto = (max(base, ZERO) * vivo_mais_percentual / Decimal('100')).quantize(CENTAVO, ROUND_HALF_UP)
    return {'desconto_vivo_mais': desconto, 'valor_calculado': (base - desconto).quantize(CENTAVO, ROUND_HALF_UP)}


def delta(valor_novo, valor_anterior):
    """[Início da venda.RF005] Novo plano menos o que pagava; sem plano anterior, conta zero."""
    if valor_novo is None:
        return None
    return (valor_novo - (valor_anterior or ZERO)).quantize(CENTAVO, ROUND_HALF_UP)


def renova_existe(codigo):
    """Código do Renova Vini: 'RN-000123' (ou só o número) de um checklist do portal."""
    numero = so_digitos(codigo)
    if not numero:
        return None
    try:
        from renova.models import Renova
        return Renova.objects.filter(pk=int(numero)).first()
    except Exception:  # noqa: BLE001
        return None


def renova_codigo(codigo):
    numero = so_digitos(codigo)
    return f'RN-{int(numero):06d}' if numero else ''


# ── Histórico ([Confiabilidade.NF001]) ──────────────────────────────────────
def _nome(usuario):
    if not usuario:
        return ''
    return ((usuario.get_full_name() or '').strip() or usuario.username)[:200]


def registrar_alteracoes(tipo, objeto, mudancas, usuario, pdv='', rotulo=''):
    """``mudancas``: [(campo, rótulo, antes, depois)] — grava só o que mudou de fato."""
    from .models import RegistroAlteracao
    linhas = [RegistroAlteracao(tipo=tipo, objeto_id=getattr(objeto, 'pk', None),
                                objeto_rotulo=(rotulo or str(objeto))[:200], campo=campo, rotulo=rot,
                                antes='' if antes is None else str(antes), depois='' if depois is None else str(depois),
                                usuario=usuario, usuario_nome=_nome(usuario), pdv=(pdv or '')[:120])
              for campo, rot, antes, depois in mudancas
              if ('' if antes is None else str(antes)) != ('' if depois is None else str(depois))]
    RegistroAlteracao.objects.bulk_create(linhas)
    return len(linhas)


CAMPOS_CLIENTE = [('nome', 'Nome'), ('telefone', 'Telefone'), ('cep', 'CEP'), ('logradouro', 'Logradouro'),
                  ('numero', 'Número'), ('complemento', 'Complemento'), ('bairro', 'Bairro'),
                  ('cidade', 'Cidade'), ('uf', 'UF')]


def salvar_cliente(cpf, dados, usuario, pdv=''):
    """[Início da venda.RF002 (novo cliente) / RF003] Cria ou atualiza, guardando o anterior.

    Devolve ``(cliente, criado, quantos_campos_mudaram)``.
    """
    from .models import Cliente
    cpf = so_digitos(cpf)
    limpos = {}
    for campo, _ in CAMPOS_CLIENTE:
        if campo in dados:
            valor = str(dados.get(campo) or '').strip()
            if campo == 'telefone':
                valor = so_digitos(valor)[:13]
            elif campo == 'cep':
                valor = so_digitos(valor)[:8]
            elif campo == 'uf':
                valor = valor.upper()[:2]
            limpos[campo] = valor[:Cliente._meta.get_field(campo).max_length]
    with transaction.atomic():
        cliente = Cliente.objects.select_for_update().filter(cpf=cpf).first()
        if cliente is None:
            cliente = Cliente.objects.create(cpf=cpf, criado_por=usuario, **limpos)
            return cliente, True, 0
        mudancas = [(campo, rotulo, getattr(cliente, campo), limpos[campo])
                    for campo, rotulo in CAMPOS_CLIENTE if campo in limpos and getattr(cliente, campo) != limpos[campo]]
        if not mudancas:
            return cliente, False, 0                     # "Sem alteração": nada é gravado
        for campo, _r, _a, depois in mudancas:
            setattr(cliente, campo, depois)
        cliente.save()
        registrar_alteracoes('CLIENTE', cliente, mudancas, usuario, pdv=pdv, rotulo=f'{cliente.nome} ({cliente.cpf_formatado})')
        return cliente, False, len(mudancas)


# ── Pré-análise ([Início da venda.RF004]) ───────────────────────────────────
COMPRAS_NA_PRE_ANALISE = 5      # [Apêndice A, item 6] — a validar


def pre_analise(cliente):
    """Plano atual (ou o último) e as últimas compras de produto lançadas no portal."""
    from .models import VendaProduto, VendaServico
    plano = None
    if cliente.plano_nome or cliente.plano_id:
        plano = {'nome': cliente.plano.nome if cliente.plano else cliente.plano_nome,
                 'segmentacao': cliente.segmentacao, 'valor': cliente.valor_pago,
                 'fonte': 'Vivo GO' if cliente.origem == 'VIVOGO' and not cliente.plano_id else 'cadastro'}
    else:
        ultimo = (VendaServico.objects.filter(venda__cliente=cliente).exclude(plano=None)
                  .select_related('plano', 'venda').order_by('-venda__data_venda').first())
        if ultimo:
            plano = {'nome': ultimo.plano.nome, 'segmentacao': ultimo.plano.segmentacao,
                     'valor': ultimo.valor_plano, 'fonte': 'última venda'}
    compras = [{'data': p.venda.data_venda, 'produto': p.nome_produto, 'valor': p.valor_total, 'venda': p.venda_id}
               for p in VendaProduto.objects.filter(venda__cliente=cliente).select_related('venda')
               .order_by('-venda__data_venda')[:COMPRAS_NA_PRE_ANALISE]]
    # Compras de produto no Vivo GO (espelho no Postgres — vendas/vivogo.py).
    from datetime import datetime, time as hora

    from .models import CompraVivoGo
    for c in CompraVivoGo.objects.filter(cpf=cliente.cpf, tipo='P').order_by('-data_venda')[:COMPRAS_NA_PRE_ANALISE]:
        compras.append({'data': timezone.make_aware(datetime.combine(c.data_venda, hora(12))) if c.data_venda else None,
                        'produto': c.item, 'valor': c.receita or ZERO, 'venda': None, 'pdv': c.pdv})
    compras = sorted([c for c in compras if c['data']], key=lambda c: c['data'], reverse=True)[:COMPRAS_NA_PRE_ANALISE]
    return {'plano': plano, 'compras': compras, 'vazio': not plano and not compras}


# ── Gravar a venda ──────────────────────────────────────────────────────────
class VendaInvalida(Exception):
    def __init__(self, erros):
        super().__init__('; '.join(erros))
        self.erros = erros


def registrar_venda(dados, usuario, loja, vendedor=None):
    """Valida e grava a venda inteira (cliente, produtos, serviços). Tudo ou nada.

    ``dados`` é o JSON da tela. Erros vão juntos em ``VendaInvalida`` — o
    vendedor vê tudo que falta de uma vez, não um erro por envio.
    """
    from .models import ConfiguracaoVendas, ItemPreco, Plano, ServicoAdicional, Venda, VendaProduto, VendaServico

    erros = []
    vendedor = vendedor or usuario
    if loja is None:
        erros.append('Seu usuário não tem PDV vinculado: procure o administrador do portal.')

    # Cliente
    c = dados.get('cliente') or {}
    cpf = so_digitos(c.get('cpf'))
    if not cpf_valido(cpf):
        erros.append('CPF do cliente inválido.')
    elif not str(c.get('nome') or '').strip():
        erros.append('Informe o nome do cliente.')
    telefone = so_digitos(c.get('telefone'))

    produtos = [p for p in (dados.get('produtos') or []) if isinstance(p, dict)]
    servicos = [s for s in (dados.get('servicos') or []) if isinstance(s, dict)]
    if not produtos and not servicos:
        erros.append('Adicione ao menos um produto ou serviço.')

    # Pagamento ([Venda de produtos.RF004]) — só faz sentido com produto.
    forma = dados.get('forma_pagamento') or ''
    vivo_mais = bool(dados.get('vivo_mais')) and bool(produtos)
    if produtos and forma not in ('CARTAO', 'PIX'):
        erros.append('Escolha a forma de pagamento: Cartão ou Pix.')
    percentual = ConfiguracaoVendas.atual().vivo_mais_percentual if vivo_mais else None

    # Plano anterior ([Início da venda.RF005])
    pa = dados.get('plano_anterior') or {}
    plano_anterior = Plano.objects.filter(pk=pa.get('id')).first() if str(pa.get('id') or '').isdigit() else None
    valor_anterior = dinheiro(pa.get('valor'))
    if valor_anterior is None and plano_anterior:
        valor_anterior = plano_anterior.valor
    segmentacao_cliente = (plano_anterior.segmentacao if plano_anterior else (pa.get('segmentacao') or ''))

    # Produtos
    itens_produto = []
    for n, p in enumerate(produtos, start=1):
        item = ItemPreco.objects.filter(pk=p.get('preco_id')).first() if str(p.get('preco_id') or '').isdigit() else None
        nome = (item.nome if item else str(p.get('nome') or '')).strip()
        if not nome:
            erros.append(f'Produto {n}: escolha o produto.')
            continue
        qtde = max(int(p.get('qtde') or 1), 1)
        if item:
            sugerido, regra = preco_sugerido(item, segmentacao_cliente, p.get('grupamento') or '')
        else:
            sugerido, regra = None, 'produto fora da tabela'
        rv = bool(p.get('renova_vini'))
        ra = bool(p.get('renova_alied'))
        rv_valor = dinheiro(p.get('renova_vini_valor')) or ZERO if rv else ZERO
        ra_valor = dinheiro(p.get('renova_alied_valor')) or ZERO if ra else ZERO
        codigo = ''
        if rv:
            if not renova_existe(p.get('renova_codigo')):
                erros.append(f'{nome}: o Código do Renova Vini é obrigatório e precisa existir no portal (RN-000000).')
            codigo = renova_codigo(p.get('renova_codigo'))
            if rv_valor <= 0:
                erros.append(f'{nome}: informe o valor do Renova Vini.')
        if ra and ra_valor <= 0:
            erros.append(f'{nome}: informe o valor do Renova Alied.')
        informado = dinheiro(p.get('valor_sugerido'))
        if sugerido is None:
            if informado is None or informado <= 0:
                erros.append(f'{nome}: sem valor na tabela para este plano — informe o valor.')
                continue
            sugerido, regra = informado, f'{regra} (valor informado)'
        conta = calcular_item(sugerido, rv_valor, ra_valor, percentual)
        final = conta['valor_calculado']
        editado = False
        if p.get('valor_final') not in (None, ''):
            digitado = dinheiro(p.get('valor_final'))
            if digitado is None:
                erros.append(f'{nome}: valor final inválido.')
                continue
            editado = digitado != final
            final = digitado
        if final <= 0:
            erros.append(f'{nome}: o valor final ficou zerado ou negativo — corrija o item.')
        itens_produto.append(dict(
            item=item, nome=nome, qtde=qtde, sugerido=sugerido, regra=regra[:120], final=final, editado=editado,
            rv=rv, rv_valor=rv_valor, ra=ra, ra_valor=ra_valor, codigo=codigo, conta=conta,
            prateleira=bool(p.get('prateleira_infinita')), serial=str(p.get('serial') or '')[:120],
            categoria=categoria_slv(item) if item else str(p.get('categoria_slv') or 'eletronico')))

    # Serviços
    itens_servico = []
    tipos = dict(VendaServico.TIPOS)
    linhas_cliente = [so_digitos(x) for x in (dados.get('linhas') or []) if so_digitos(x)]
    if servicos and not linhas_cliente:
        erros.append('Informe a linha do cliente para o serviço.')
    for linha in linhas_cliente:
        if not numero_fake(linha) and not telefone_valido(linha):
            erros.append(f'Linha {linha}: use DDD + número.')
    fake = any(numero_fake(x) for x in linhas_cliente) or (bool(telefone) and numero_fake(telefone))
    for n, s in enumerate(servicos, start=1):
        tipo = s.get('tipo') or ''
        if tipo not in tipos:
            erros.append(f'Serviço {n}: escolha o tipo de serviço.')
            continue
        rotulo = tipos[tipo]
        reg = dict(tipo=tipo, rotulo=rotulo, plano=None, plano_anterior=None, segmentacao='', valor_anterior=None,
                   adicional=None, serial='', novo_titular_cpf='', novo_titular_nome='', numero_novo='',
                   ativacao=False, valor=ZERO)
        if tipo in ('ALTA', 'REATIVACAO', 'TROCA_PLANO', 'MIGRACAO', 'TROCA_SIMCARD', 'TROCA_TITULARIDADE'):
            plano = Plano.objects.filter(pk=s.get('plano_id')).first() if str(s.get('plano_id') or '').isdigit() else None
            if plano is None:
                erros.append(f'{rotulo}: escolha a segmentação e o plano.')
            elif tipo == 'REATIVACAO' and plano.segmentacao not in ('POS', 'CONTROLE'):
                erros.append('Reativação: só Pós ou Controle.')
            elif not plano.ativo:
                erros.append(f'{rotulo}: o plano {plano.nome} está inativo.')
            reg.update(plano=plano, segmentacao=plano.segmentacao if plano else '', valor=plano.valor if plano else ZERO)
        if tipo in ('TROCA_PLANO', 'MIGRACAO'):
            anterior = (Plano.objects.filter(pk=s.get('plano_anterior_id')).first()
                        if str(s.get('plano_anterior_id') or '').isdigit() else None) or plano_anterior
            if anterior is None and valor_anterior is None:
                erros.append(f'{rotulo}: informe o plano anterior do cliente.')
            reg.update(plano_anterior=anterior,
                       valor_anterior=(anterior.valor if anterior and anterior != plano_anterior else valor_anterior))
        elif tipo in ('ALTA', 'REATIVACAO'):
            reg.update(plano_anterior=plano_anterior, valor_anterior=valor_anterior)
        if tipo == 'TROCA_SIMCARD':
            serial = so_digitos(s.get('serial'))
            # [Apêndice A, item 9] O estoque de SimCard da loja ainda não está no portal:
            # confere o formato do ICCID (19–20 dígitos, começando por 89).
            if not re.fullmatch(r'89\d{17,18}', serial):
                erros.append('Troca de SimCard: informe o serial (ICCID) do SimCard — 19 ou 20 números começando por 89.')
            reg['serial'] = serial
        if tipo in ('SEGURO', 'SVA'):
            adicional = (ServicoAdicional.objects.filter(pk=s.get('servico_adicional_id'), tipo=tipo, ativo=True).first()
                         if str(s.get('servico_adicional_id') or '').isdigit() else None)
            if adicional is None:
                erros.append(f'{rotulo}: escolha o serviço contratado.')
            if not s.get('ativacao_confirmada'):
                erros.append(f'{rotulo}: confirme que o serviço foi ativado antes de concluir.')
            reg.update(adicional=adicional, ativacao=bool(s.get('ativacao_confirmada')),
                       valor=adicional.valor if adicional else ZERO)
        if tipo == 'TROCA_TITULARIDADE':
            novo = so_digitos(s.get('novo_titular_cpf'))
            if not cpf_valido(novo):
                erros.append('Troca de titularidade: CPF do novo titular inválido.')
            elif novo == cpf:
                erros.append('Troca de titularidade: o novo titular é o próprio cliente.')
            if not str(s.get('novo_titular_nome') or '').strip():
                erros.append('Troca de titularidade: informe o nome do novo titular.')
            reg.update(novo_titular_cpf=novo, novo_titular_nome=str(s.get('novo_titular_nome') or '').strip()[:200])
        if tipo == 'TROCA_NUMERO':
            novo = so_digitos(s.get('numero_novo'))
            if not telefone_valido(novo):
                erros.append('Troca de número: o número novo precisa de DDD + número.')
            elif linhas_cliente and novo == linhas_cliente[0]:
                erros.append('Troca de número: o número novo é igual ao atual.')
            reg['numero_novo'] = novo
        reg['delta'] = delta(reg['valor'], reg['valor_anterior']) if reg['plano'] else None
        itens_servico.append(reg)

    if erros:
        raise VendaInvalida(erros)

    with transaction.atomic():
        cliente, _criado, _ = salvar_cliente(cpf, {k: c.get(k) for k, _r in CAMPOS_CLIENTE if k in c},
                                             usuario, pdv=loja.name if loja else '')
        venda = Venda.objects.create(
            loja=loja, pdv_nome=loja.name if loja else '', vendedor=vendedor, created_by=usuario,
            data_venda=timezone.now(), cliente=cliente, cliente_nome=cliente.nome, cliente_cpf=formatar_cpf(cpf),
            cliente_telefone=cliente.telefone,
            tipo_venda=' + '.join(x for x in ('Produto' if itens_produto else '', 'Serviço' if itens_servico else '') if x),
            forma_pagamento=forma if produtos else '', vivo_mais=vivo_mais, vivo_mais_percentual=percentual,
            plano_anterior=plano_anterior, plano_anterior_nome=(plano_anterior.nome if plano_anterior else str(pa.get('nome') or ''))[:200],
            segmentacao_anterior=segmentacao_cliente[:10], valor_anterior=valor_anterior, numero_fake=fake,
            observacao=str(dados.get('observacao') or '').strip()[:2000],
        )
        for i in itens_produto:
            item = i['item']
            VendaProduto.objects.create(
                venda=venda, preco=item, nome_produto=i['nome'][:200], qtde=i['qtde'], serial=i['serial'],
                categoria=(item.categoria if item else '')[:120], tipo_produto=i['categoria'],
                sku=((item.cod_sap or item.cod_sistema or (item.extra or {}).get('ID DPGC', '')) if item else '')[:60],
                plano=(plano_anterior.nome if plano_anterior else '')[:200],
                categoria_slv=i['categoria'], prateleira_infinita=i['prateleira'], valor_sugerido=i['sugerido'],
                regra_preco=i['regra'], renova_vini=i['rv'], renova_vini_valor=i['rv_valor'], renova_codigo=i['codigo'],
                renova_alied=i['ra'], renova_alied_valor=i['ra_valor'],
                desconto_vivo_mais=i['conta']['desconto_vivo_mais'], valor_calculado=i['conta']['valor_calculado'],
                valor_venda=i['final'], valor_editado=i['editado'], editado_por=usuario if i['editado'] else None)
        ultimo_plano = None
        for s in itens_servico:
            VendaServico.objects.create(
                venda=venda, servico=s['rotulo'], tipo_servico=s['tipo'], segmentacao=s['segmentacao'],
                plano=s['plano'], plano_novo=(s['plano'].nome if s['plano'] else (s['adicional'].nome if s['adicional'] else ''))[:200],
                tipo_plano=s['segmentacao'], plano_anterior=s['plano_anterior'],
                plano_anterior_nome=(s['plano_anterior'].nome if s['plano_anterior'] else venda.plano_anterior_nome)[:200],
                valor_anterior=s['valor_anterior'], valor_plano=s['valor'], delta=s['delta'],
                linhas=linhas_cliente, numero_acesso=(linhas_cliente[0] if linhas_cliente else '')[:40],
                numero_fake=fake, serial_simcard=s['serial'], servico_adicional=s['adicional'],
                ativacao_confirmada=s['ativacao'], novo_titular_cpf=s['novo_titular_cpf'],
                novo_titular_nome=s['novo_titular_nome'],
                numero_anterior=(linhas_cliente[0] if s['tipo'] == 'TROCA_NUMERO' and linhas_cliente else ''),
                numero_novo=s['numero_novo'])
            if s['plano'] and s['tipo'] != 'TROCA_TITULARIDADE':
                ultimo_plano = s['plano']
            if s['tipo'] == 'TROCA_NUMERO':
                # [Venda de serviços.RF010] cadastro atualizado, mantendo o histórico.
                salvar_cliente(cpf, {'telefone': s['numero_novo']}, usuario, pdv=venda.pdv_nome)
        if ultimo_plano:
            cliente.plano, cliente.plano_nome = ultimo_plano, ultimo_plano.nome
            cliente.segmentacao, cliente.valor_pago = ultimo_plano.segmentacao, ultimo_plano.valor
            cliente.save(update_fields=['plano', 'plano_nome', 'segmentacao', 'valor_pago', 'atualizado_em'])
    return venda
