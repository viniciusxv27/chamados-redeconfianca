"""Edição e exclusão de venda pelo SUPERADMIN (11/11/2026).

Só o SUPERADMIN mexe numa venda já lançada, e tudo vai para o histórico
(``RegistroAlteracao``): cada campo que mudou vira uma linha com o antes e o
depois em texto de gente (R$, nomes no lugar de ids), quem fez, o PDV da venda,
o motivo (obrigatório) e o número da venda — que fica mesmo depois de ela ser
excluída. As linhas de uma mesma edição têm o mesmo ``lote``.

Regras que valem na edição (as mesmas do lançamento, em ``slv``):

- linha que não mudou não é conferida de novo (venda antiga com dado fora da
  regra de hoje não trava a correção de outro campo);
- produto: o sistema recalcula ``desconto_vivo_mais`` e ``valor_calculado``
  (sugerido − Renova − Vivo+). O valor final acompanha o recálculo quando não
  tinha sido editado à mão e ninguém mexeu nele no formulário; se foi digitado,
  vale o digitado e fica marcado quem editou. O desconto no Pix da venda é
  repartido de novo entre os itens (``slv.aplicar_desconto_pix``);
- pagamentos: lidos de novo por ``slv.ler_pagamentos`` (fecham com o total dos
  produtos) e o resumo da venda (forma única ou MISTO, NSU e parcelas do 1º
  cartão) sai deles, como no lançamento;
- serviço: delta = valor do plano − valor anterior; trocar o plano faz o valor
  seguir o do plano novo, a menos que o valor também tenha sido mudado à mão;
- a venda só se exclui inteira, com a fotografia completa (cabeçalho, produtos,
  serviços e pagamentos, em JSON) gravada antes. Venda ligada à conciliação de
  cartões do Financeiro não se exclui: o par se solta lá primeiro.
"""
import json
import re
import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import slv
from .slv import ZERO, VendaInvalida, _brl

TIPOS_VENDA = ('VENDA', 'VENDA_PRODUTO', 'VENDA_SERVICO', 'VENDA_PAGAMENTO', 'VENDA_EXCLUIDA')
PAGAMENTOS_EM_BRANCO = 3        # linhas a mais no formulário para acrescentar pagamento
MOTIVO_MINIMO = 5
MAX_PRODUTOS_NOVOS = 20      # inclusão pela edição (11/11/2026)
TIPOS_COM_PLANO = ('ALTA', 'REATIVACAO', 'TROCA_PLANO', 'MIGRACAO', 'TROCA_SIMCARD', 'TROCA_TITULARIDADE')


class VendaDesatualizada(VendaInvalida):
    """Outra pessoa gravou a venda depois que o formulário foi aberto."""


# ── Texto para o histórico ──────────────────────────────────────────────────
def _sim(valor):
    return 'Sim' if valor else 'Não'


def _r(valor):
    return '' if valor is None else _brl(valor)


def _data(valor):
    return timezone.localtime(valor).strftime('%d/%m/%Y %H:%M') if valor else ''


def _pessoa(user):
    return slv._nome(user) if user else ''


def versao(venda):
    """A marca da venda quando o formulário abriu: se mudar até o envio, a edição é recusada."""
    return venda.updated_at.isoformat() if venda.updated_at else ''


def pagamentos_atuais(venda):
    """As partes do pagamento como dicionários. Venda antiga, de antes de PagamentoVenda, vira uma parte só."""
    linhas = [{'forma': pg.forma, 'valor': pg.valor, 'nsu': pg.nsu or '', 'parcelas': pg.parcelas}
              for pg in venda.pagamentos.all().order_by('ordem', 'id')]
    if not linhas and venda.forma_pagamento in slv.FORMAS_VALIDAS and venda.pk:
        linhas = [{'forma': venda.forma_pagamento, 'valor': venda.total, 'nsu': venda.nsu or '', 'parcelas': venda.parcelas}]
    return linhas


def _normal(pg):
    forma = str(pg.get('forma') or '').upper()
    try:
        parcelas = int(pg.get('parcelas') or 0) or None
    except (TypeError, ValueError):
        parcelas = -1
    nsu = re.sub(r'[^0-9A-Za-z]', '', str(pg.get('nsu') or '')).upper()[:30]
    return (forma, slv.dinheiro(pg.get('valor')), nsu if forma in slv.FORMAS_COM_NSU else '',
            parcelas if forma == 'CARTAO' else None)


def _campo_dinheiro(valor):
    return '' if valor is None else f'{valor:.2f}'.replace('.', ',')


def _resumo_pagamentos(pagamentos):
    partes = []
    for pg in pagamentos:
        forma = pg['forma'] if isinstance(pg, dict) else pg.forma
        valor = pg['valor'] if isinstance(pg, dict) else pg.valor
        nsu = pg['nsu'] if isinstance(pg, dict) else pg.nsu
        parcelas = pg['parcelas'] if isinstance(pg, dict) else pg.parcelas
        from .models import FORMAS_PAGAMENTO
        texto = f'{dict(FORMAS_PAGAMENTO).get(forma, forma)} {_brl(valor)}'
        detalhes = [f'NSU {nsu}'] if nsu else []
        if parcelas:
            detalhes.append('à vista' if parcelas == 1 else f'{parcelas}x')
        partes.append(texto + (f' ({", ".join(detalhes)})' if detalhes else ''))
    return ' · '.join(partes) or '(sem pagamento)'


def _resumo_produto(p):
    serial = f' · {"IMEI" if p.categoria_slv == "aparelho" else "serial"} {p.serial}' if p.serial else ''
    return f'{p.nome_produto}{serial} · {_brl(p.valor_total)}'


def _resumo_servico(s):
    plano = s.plano.nome if s.plano_id and s.plano else s.plano_novo
    acesso = f' · {slv.formatar_telefone(s.numero_acesso)}' if s.numero_acesso else ''
    return f'{s.get_tipo_servico_display() or s.servico}{" · " + plano if plano else ""}{acesso} · {_brl(s.valor_plano)}'


# ── Fotografia (exclusão e item removido) ───────────────────────────────────
def _como_dict(obj):
    User = get_user_model()
    saida = {}
    for campo in obj._meta.concrete_fields:
        valor = getattr(obj, campo.attname)
        if isinstance(valor, Decimal):
            valor = str(valor)
        elif hasattr(valor, 'isoformat'):
            valor = valor.isoformat()
        saida[campo.attname] = valor
        if campo.is_relation and valor is not None:
            rel = getattr(obj, campo.name, None)
            # "__nome": "cliente_nome" e "plano_anterior_nome" já são campos da venda.
            saida[f'{campo.name}__nome'] = (_pessoa(rel) if campo.related_model is User else str(rel)) if rel else None
    return saida


def fotografia(venda):
    """A venda inteira em JSON (o que vai no histórico antes de ela ser excluída)."""
    dados = _como_dict(venda)
    dados['produtos'] = [_como_dict(p) for p in venda.produtos.all().order_by('id')]
    dados['servicos'] = [_como_dict(s) for s in venda.servicos.all().order_by('id')]
    dados['pagamentos'] = [_como_dict(pg) for pg in venda.pagamentos.all().order_by('ordem', 'id')]
    dados['total'] = str(venda.total)
    return dados


def _json(dados):
    return json.dumps(dados, ensure_ascii=False, sort_keys=True)


# ── Formulário ──────────────────────────────────────────────────────────────
def formulario(venda, post=None):
    """Os valores que a tela mostra: os da venda ou, se o envio voltou com erro, os enviados."""
    def valor(nome, padrao):
        if post is None:
            return padrao
        return post.get(nome, '')

    def marcado(nome, padrao):
        if post is None:
            return bool(padrao)
        return bool(post.get(nome))

    cab = {
        'data_venda': valor('data_venda', timezone.localtime(venda.data_venda).strftime('%Y-%m-%dT%H:%M')
                            if venda.data_venda else ''),
        'loja': valor('loja', str(venda.loja_id or '')),
        'vendedor': valor('vendedor', str(venda.vendedor_id or '')),
        'cliente_nome': valor('cliente_nome', venda.cliente_nome),
        'cliente_cpf': valor('cliente_cpf', venda.cliente_cpf),
        'cliente_telefone': valor('cliente_telefone', slv.formatar_telefone(venda.cliente_telefone)),
        'comprovante_fiscal': valor('comprovante_fiscal', venda.comprovante_fiscal),
        'estoque_avancado': marcado('estoque_avancado', venda.estoque_avancado),
        'observacao': valor('observacao', venda.observacao),
        'vivo_mais': marcado('vivo_mais', venda.vivo_mais),
        'desconto_pix': valor('desconto_pix', _campo_dinheiro(venda.desconto_pix) if venda.desconto_pix else ''),
        'motivo': valor('motivo', ''),
    }
    produtos = []
    for p in venda.produtos.all().order_by('id'):
        k = f'p{p.pk}_'
        produtos.append({
            'obj': p, 'id': p.pk, 'remover': marcado(k + 'remover', False),
            'nome': valor(k + 'nome', p.nome_produto), 'serial': valor(k + 'serial', p.serial),
            'prateleira': marcado(k + 'prateleira', p.prateleira_infinita),
            'sku': valor(k + 'sku', p.sku), 'cor': valor(k + 'cor', p.cor),
            'sugerido': valor(k + 'sugerido', _campo_dinheiro(p.valor_sugerido)),
            'rv': marcado(k + 'rv', p.renova_vini), 'rv_valor': valor(k + 'rv_valor', _campo_dinheiro(p.renova_vini_valor)),
            'rv_codigo': valor(k + 'rv_codigo', p.renova_codigo),
            'ra': marcado(k + 'ra', p.renova_alied), 'ra_valor': valor(k + 'ra_valor', _campo_dinheiro(p.renova_alied_valor)),
            'ra_pedido': valor(k + 'ra_pedido', p.renova_alied_pedido),
            'ras': marcado(k + 'ras', p.renova_assurant),
            'ras_valor': valor(k + 'ras_valor', _campo_dinheiro(p.renova_assurant_valor)),
            'ras_pedido': valor(k + 'ras_pedido', p.renova_assurant_pedido),
            'final': valor(k + 'final', _campo_dinheiro((p.valor_venda or ZERO) + (p.desconto_pix or ZERO))),
        })
    servicos = []
    for s in venda.servicos.all().select_related('plano', 'servico_adicional').order_by('id'):
        k = f's{s.pk}_'
        servicos.append({
            'obj': s, 'id': s.pk, 'remover': marcado(k + 'remover', False),
            'tipo': valor(k + 'tipo', s.tipo_servico), 'plano': valor(k + 'plano', str(s.plano_id or '')),
            'acesso': valor(k + 'acesso', slv.formatar_telefone(s.numero_acesso)),
            'ordem': valor(k + 'ordem', s.numero_ordem),
            'portabilidade': marcado(k + 'portabilidade', s.portabilidade),
            'operadora': valor(k + 'operadora', s.operadora_origem),
            'simcard': valor(k + 'simcard', s.serial_simcard),
            'valor': valor(k + 'valor', _campo_dinheiro(s.valor_plano)),
            'valor_anterior': valor(k + 'valor_anterior', _campo_dinheiro(s.valor_anterior)),
        })
    existentes = pagamentos_atuais(venda)
    pagamentos = []
    for n in range(len(existentes) + PAGAMENTOS_EM_BRANCO):
        pg = existentes[n] if n < len(existentes) else None
        k = f'pg{n}_'
        pagamentos.append({
            'n': n, 'existente': pg is not None,
            'forma': valor(k + 'forma', pg['forma'] if pg else ''),
            'valor': valor(k + 'valor', _campo_dinheiro(pg['valor']) if pg else ''),
            'nsu': valor(k + 'nsu', pg['nsu'] if pg else ''),
            'parcelas': valor(k + 'parcelas', str(pg['parcelas'] or '') if pg else ''),
        })
    return {'cab': cab, 'produtos': produtos, 'servicos': servicos, 'pagamentos': pagamentos,
            'total_pagamentos': len(pagamentos)}


def _iguais_dinheiro(texto, valor):
    return slv.dinheiro(texto) == (valor.quantize(slv.CENTAVO) if valor is not None else None)


# ── Edição ──────────────────────────────────────────────────────────────────
def editar_venda(pk, post, usuario):
    """Valida e grava a edição inteira (ou nada). Devolve quantas linhas foram para o histórico.

    ``post`` é o formulário de /vendas/<pk>/editar/. Erros vão juntos em ``VendaInvalida``;
    venda gravada por outra pessoa depois que a tela abriu dá ``VendaDesatualizada``.
    """
    from .models import (ConfiguracaoVendas, Cliente, PagamentoVenda, Plano, Venda, VendaProduto, VendaServico)
    from users.models import Sector
    User = get_user_model()

    with transaction.atomic():
        venda = (Venda.objects.select_for_update().select_related('loja', 'vendedor', 'cliente').filter(pk=pk).first())
        if venda is None:
            raise VendaInvalida(['Esta venda não existe mais (pode ter sido excluída).'])
        if (post.get('versao') or '') != versao(venda):
            raise VendaDesatualizada(['Esta venda foi alterada depois que você abriu a tela. '
                                      'Os dados abaixo são os de agora: refaça a edição.'])
        erros = []
        motivo = ' '.join(str(post.get('motivo') or '').split())[:2000]
        if len(motivo) < MOTIVO_MINIMO:
            erros.append('Diga o motivo da alteração (fica no histórico da venda).')

        # ── Cabeçalho ──
        mud_venda = []
        txt_data = str(post.get('data_venda') or '').strip()
        data_venda = venda.data_venda
        if txt_data != (timezone.localtime(venda.data_venda).strftime('%Y-%m-%dT%H:%M') if venda.data_venda else ''):
            data_venda = parse_datetime(txt_data) if txt_data else None
            if data_venda is None:
                erros.append('Data da venda inválida.')
            else:
                if timezone.is_naive(data_venda):
                    data_venda = timezone.make_aware(data_venda)
                if data_venda > timezone.now() + timedelta(minutes=5):
                    erros.append('A data da venda não pode estar no futuro.')
        loja = venda.loja
        if str(post.get('loja') or '') != str(venda.loja_id or ''):
            loja = Sector.objects.filter(pk=post.get('loja')).first() if str(post.get('loja') or '').isdigit() else None
            if loja is None:
                erros.append('Escolha a loja / PDV da venda.')
        vendedor = venda.vendedor
        if str(post.get('vendedor') or '') != str(venda.vendedor_id or ''):
            vendedor = (User.objects.filter(pk=post.get('vendedor')).first()
                        if str(post.get('vendedor') or '').isdigit() else None)
            if vendedor is None:
                erros.append('Escolha o vendedor da venda.')
        nome_cliente = ' '.join(str(post.get('cliente_nome') or '').split())[:200]
        if not nome_cliente:
            erros.append('Informe o nome do cliente.')
        cpf = slv.so_digitos(post.get('cliente_cpf'))
        cpf_mudou = cpf != slv.so_digitos(venda.cliente_cpf)
        if (cpf_mudou or (not venda.cliente_id and cpf)) and not slv.documento_valido(cpf):
            erros.append('CPF/CNPJ do cliente inválido.')
        telefone = slv.so_digitos(post.get('cliente_telefone'))[:13]
        if (telefone != slv.so_digitos(venda.cliente_telefone) and telefone
                and not slv.telefone_valido(telefone) and not slv.numero_fake(telefone)):
            erros.append('Telefone do cliente: use DDD + número.')
        comprovante = post.get('comprovante_fiscal') or venda.comprovante_fiscal
        if comprovante not in dict(Venda.COMPROVANTE_CHOICES):
            erros.append('Escolha o comprovante fiscal.')
        estoque = bool(post.get('estoque_avancado'))
        observacao = str(post.get('observacao') or '').strip()[:2000]

        # ── Produtos ──
        com_serial = slv.categorias_com_serial()
        produtos = list(venda.produtos.select_related('preco', 'editado_por').order_by('id'))
        removidos_p, mantidos_p = [], []
        for p in produtos:
            if post.get(f'p{p.pk}_remover'):
                removidos_p.append(p)
            else:
                mantidos_p.append(p)
        # Produtos novos (inclusão pela edição, 11/11/2026): np<k>_preco_id, np<k>_serial, …
        n_novos = int(post.get('novos_produtos') or 0) if str(post.get('novos_produtos') or '').isdigit() else 0
        brutos_novos = []
        for k_novo in range(min(n_novos, MAX_PRODUTOS_NOVOS)):
            pre = f'np{k_novo}_'
            if not str(post.get(pre + 'preco_id') or '').strip():
                continue
            brutos_novos.append({
                'preco_id': post.get(pre + 'preco_id'), 'imei': post.get(pre + 'serial'),
                'prateleira_infinita': bool(post.get(pre + 'prateleira')), 'valor_final': post.get(pre + 'valor_final'),
                # Item sem preço na tabela para a segmentação: o valor final digitado vira o sugerido.
                'valor_sugerido': post.get(pre + 'valor_final'), 'sku': post.get(pre + 'sku'),
            })
        vivo_mais = bool(post.get('vivo_mais')) and bool(mantidos_p or brutos_novos)
        percentual = None
        if vivo_mais:
            percentual = (venda.vivo_mais_percentual if venda.vivo_mais and venda.vivo_mais_percentual is not None
                          else ConfiguracaoVendas.atual().vivo_mais_percentual)
        itens = []
        imeis = {}
        for p in mantidos_p:
            k = f'p{p.pk}_'
            nome = ' '.join(str(post.get(k + 'nome') or '').split())[:200]
            categoria = p.categoria_slv or (slv.categoria_slv(p.preco) if p.preco else 'eletronico')
            serial = str(post.get(k + 'serial') or '').strip()[:120]
            if categoria == 'aparelho':
                serial = slv.so_digitos(serial)
            prateleira = bool(post.get(k + 'prateleira'))
            sugerido = slv.dinheiro(post.get(k + 'sugerido'))
            rv, ra, ras = bool(post.get(k + 'rv')), bool(post.get(k + 'ra')), bool(post.get(k + 'ras'))
            rv_valor = slv.dinheiro(post.get(k + 'rv_valor')) if rv else ZERO
            ra_valor = slv.dinheiro(post.get(k + 'ra_valor')) if ra else ZERO
            ras_valor = slv.dinheiro(post.get(k + 'ras_valor')) if ras else ZERO
            codigo = slv.renova_codigo(post.get(k + 'rv_codigo')) if rv else ''
            ra_pedido = str(post.get(k + 'ra_pedido') or '').strip()[:40] if ra else ''
            ras_pedido = str(post.get(k + 'ras_pedido') or '').strip()[:40] if ras else ''
            txt_final = post.get(k + 'final')
            bruto_antes = (p.valor_venda or ZERO) + (p.desconto_pix or ZERO)
            final_mexido = not _iguais_dinheiro(txt_final, bruto_antes)
            novo = (nome, serial, prateleira, str(post.get(k + 'sku') or '').strip()[:60],
                    str(post.get(k + 'cor') or '').strip()[:60], sugerido, rv, rv_valor, codigo, ra, ra_valor, ra_pedido,
                    ras, ras_valor, ras_pedido)
            antigo = (p.nome_produto, p.serial, p.prateleira_infinita, p.sku, p.cor, p.valor_sugerido,
                      p.renova_vini, p.renova_vini_valor if p.renova_vini else ZERO, p.renova_codigo if p.renova_vini else '',
                      p.renova_alied, p.renova_alied_valor if p.renova_alied else ZERO,
                      p.renova_alied_pedido if p.renova_alied else '',
                      p.renova_assurant, p.renova_assurant_valor if p.renova_assurant else ZERO,
                      p.renova_assurant_pedido if p.renova_assurant else '')
            mudou = novo != antigo or final_mexido
            rot = nome or p.nome_produto
            if mudou:
                if not nome:
                    erros.append(f'Produto {p.nome_produto}: informe o nome do produto.')
                if serial != p.serial or prateleira != p.prateleira_infinita:
                    if categoria == 'aparelho':
                        if prateleira and not serial:
                            pass
                        elif not slv.imei_valido(serial):
                            erros.append(f'{rot}: informe o IMEI do aparelho — 15 números (confira o dígito final) '
                                         'ou marque Prateleira infinita.')
                        else:
                            ja = (VendaProduto.objects.filter(serial=serial, categoria_slv='aparelho')
                                  .exclude(venda_id=venda.pk).select_related('venda').first())
                            if ja:
                                erros.append(f'{rot}: o IMEI {serial} já foi vendido na venda #{ja.venda_id} '
                                             f'({timezone.localtime(ja.venda.data_venda):%d/%m/%Y}).')
                    elif not serial and not prateleira and slv.exige_serial(p.preco, com_serial):
                        erros.append(f'{rot}: informe o serial do produto (está na caixa ou na etiqueta) '
                                     'ou marque Prateleira infinita.')
                if sugerido is None or sugerido <= 0:
                    erros.append(f'{rot}: informe o valor de produto (maior que zero).')
                for marcado_, valor_, nome_ in ((rv, rv_valor, 'Renova Vini'), (ra, ra_valor, 'Renova Alied'),
                                                (ras, ras_valor, 'Renova Assurant')):
                    if marcado_ and (valor_ is None or valor_ <= 0):
                        erros.append(f'{rot}: informe o valor do {nome_}.')
                if rv and (codigo != p.renova_codigo or not p.renova_vini) and not slv.renova_existe(codigo):
                    erros.append(f'{rot}: o Código do Renova Vini é obrigatório e precisa existir no portal (RN-000000).')
                if ra and not ra_pedido:
                    erros.append(f'{rot}: informe o número do pedido do Renova Alied.')
            if categoria == 'aparelho' and serial:
                if serial in imeis:
                    erros.append(f'{rot}: o IMEI {serial} está repetido nesta venda (cada aparelho tem o seu).')
                imeis[serial] = rot
            # Linha intocada e Vivo+ igual: o item fica como está (venda antiga não muda de valor sozinha).
            recalc = mudou or vivo_mais != venda.vivo_mais
            if not recalc:
                itens.append(dict(obj=p, nome=rot, qtde=p.qtde or 1, final=bruto_antes, bruto=bruto_antes,
                                  desconto_pix=ZERO, conta={'desconto_vivo_mais': p.desconto_vivo_mais,
                                                            'valor_calculado': p.valor_calculado},
                                  novo=antigo, mudou=False, recalc=False, final_mexido=False))
                continue
            conta = slv.calcular_item(sugerido or ZERO, rv_valor or ZERO, ra_valor or ZERO, percentual,
                                      renova_assurant=ras_valor or ZERO)
            if final_mexido or p.valor_editado:
                final = slv.dinheiro(txt_final) if final_mexido else bruto_antes
                if final is None:
                    erros.append(f'{rot}: valor final inválido.')
                    final = ZERO
            else:
                final = conta['valor_calculado']
            if final <= 0:
                erros.append(f'{rot}: o valor final ficou zerado ou negativo — corrija o item.')
            itens.append(dict(obj=p, nome=rot, qtde=p.qtde or 1, final=final, bruto=final, desconto_pix=ZERO,
                              conta=conta, novo=novo, mudou=mudou, recalc=True, final_mexido=final_mexido))

        # Os produtos incluídos passam pela MESMA regra do lançamento (slv.ler_produto): IMEI, serial,
        # preço da tabela pela segmentação do cliente, Vivo+. O IMEI de quem está sendo removido pode voltar.
        novos_p = []
        if brutos_novos:
            segmentacao = venda.segmentacao_anterior or (venda.plano_anterior.segmentacao if venda.plano_anterior_id else '')
            imeis_ficam = {i['novo'][1]: i['nome'] for i in itens
                           if i['obj'].categoria_slv == 'aparelho' and i['novo'][1]}
            for k_novo, bruto_novo in enumerate(brutos_novos, start=1):
                novo_item = slv.ler_produto(bruto_novo, f'novo {k_novo}', segmentacao_cliente=segmentacao,
                                            percentual=percentual, com_serial=com_serial, imeis_da_venda=imeis_ficam,
                                            erros=erros, ignorar_produtos=[p.pk for p in removidos_p])
                if novo_item is not None:
                    novos_p.append(novo_item)

        # Desconto no Pix (repartido de novo) e pagamentos — só com produto, como no lançamento.
        brutos = []
        for n in range(int(post.get('total_pagamentos') or 0) if str(post.get('total_pagamentos') or '').isdigit() else 0):
            forma = str(post.get(f'pg{n}_forma') or '').strip().upper()
            if forma:
                brutos.append({'forma': forma, 'valor': post.get(f'pg{n}_valor'), 'nsu': post.get(f'pg{n}_nsu'),
                               'parcelas': post.get(f'pg{n}_parcelas')})
        desconto_pix, pagamentos = ZERO, []
        atuais = pagamentos_atuais(venda)
        todos_itens = itens + novos_p
        if todos_itens:
            desconto_pix = slv.aplicar_desconto_pix({'desconto_pix': post.get('desconto_pix'), 'pagamentos': brutos},
                                                    todos_itens, erros)
            total_novo = sum((i['final'] * i['qtde'] for i in todos_itens), ZERO)
            total_antes = sum(((p.valor_venda or ZERO) * (p.qtde or 1) for p in produtos), ZERO)
            if total_novo == total_antes and [_normal(b) for b in brutos] == [_normal(a) for a in atuais]:
                pagamentos = atuais          # nada mudou: não confere de novo (venda antiga sem NSU continua editável)
            else:
                pagamentos = slv.ler_pagamentos({'pagamentos': brutos}, total_novo, erros)
        formas = {pg['forma'] for pg in pagamentos}
        forma = (formas.pop() if len(formas) == 1 else 'MISTO') if pagamentos else ''
        cartao = next((pg for pg in pagamentos if pg['forma'] in slv.FORMAS_COM_NSU), None)
        nsu, parcelas = (cartao['nsu'], cartao['parcelas']) if cartao else ('', None)

        # ── Serviços ──
        tipos = dict(VendaServico.TIPOS)
        operadoras = dict(VendaServico.OPERADORAS)
        servicos = list(venda.servicos.select_related('plano', 'servico_adicional').order_by('id'))
        removidos_s, novos_s = [], []
        for s in servicos:
            k = f's{s.pk}_'
            if post.get(k + 'remover'):
                removidos_s.append(s)
                continue
            tipo = str(post.get(k + 'tipo') or '')
            rot = tipos.get(tipo) or s.get_tipo_servico_display() or s.servico
            plano = s.plano
            if str(post.get(k + 'plano') or '') != str(s.plano_id or ''):
                plano = Plano.objects.filter(pk=post.get(k + 'plano')).first() if str(post.get(k + 'plano') or '').isdigit() else None
            if tipo not in TIPOS_COM_PLANO:
                plano = None if tipo in tipos else plano
            acesso = slv.so_digitos(post.get(k + 'acesso'))[:40]
            ordem = re.sub(r'\s+', '', str(post.get(k + 'ordem') or ''))[:40]
            portabilidade = bool(post.get(k + 'portabilidade')) and tipo == 'ALTA'
            operadora = str(post.get(k + 'operadora') or '').strip().upper() if portabilidade else ''
            simcard = slv.so_digitos(post.get(k + 'simcard')) if tipo == 'TROCA_SIMCARD' else ''
            txt_valor = post.get(k + 'valor')
            valor_mexido = not _iguais_dinheiro(txt_valor, s.valor_plano)
            plano_mudou = (plano.pk if plano else None) != s.plano_id
            if plano_mudou and plano is not None and not valor_mexido:
                valor = plano.valor
            else:
                valor = slv.dinheiro(txt_valor)
            txt_anterior = str(post.get(k + 'valor_anterior') or '').strip()
            valor_anterior = slv.dinheiro(txt_anterior) if txt_anterior else None
            fibra = plano is not None and plano.segmentacao == 'FIBRA'
            if not fibra:
                ordem = ''
            novo = (tipo, plano.pk if plano else None, acesso, ordem, portabilidade, operadora, simcard, valor, valor_anterior)
            antigo = (s.tipo_servico, s.plano_id, s.numero_acesso, s.numero_ordem, s.portabilidade, s.operadora_origem,
                      s.serial_simcard, s.valor_plano, s.valor_anterior)
            mudou = novo != antigo
            if mudou:
                if tipo not in tipos:
                    erros.append(f'Serviço {rot}: escolha o tipo de serviço.')
                if tipo in TIPOS_COM_PLANO:
                    if plano is None:
                        erros.append(f'{rot}: escolha o plano.')
                    elif plano_mudou and not plano.ativo:
                        erros.append(f'{rot}: o plano {plano.nome} está inativo.')
                    elif tipo == 'REATIVACAO' and plano.segmentacao not in ('POS', 'CONTROLE'):
                        erros.append('Reativação: só Pós ou Controle.')
                if fibra:
                    if not ordem:
                        erros.append(f'{rot}: venda de fibra — informe o número da ordem.')
                    elif not re.fullmatch(r'[0-9A-Za-z./-]{4,40}', ordem):
                        erros.append(f'{rot}: número da ordem inválido (use só letras e números).')
                if portabilidade and operadora not in operadoras:
                    erros.append(f'{rot}: é portabilidade — escolha a operadora de origem (Claro, Oi, Tim ou Outros).')
                if tipo == 'TROCA_SIMCARD' and not re.fullmatch(r'89\d{17,18}', simcard):
                    erros.append('Troca de SimCard: informe o serial (ICCID) do SimCard — 19 ou 20 números começando por 89.')
                if acesso != s.numero_acesso and acesso and not slv.telefone_valido(acesso) and not slv.numero_fake(acesso):
                    erros.append(f'{rot}: número de acesso — use DDD + número.')
                if valor is None or valor < 0:
                    erros.append(f'{rot}: valor do plano inválido.')
                if txt_anterior and (valor_anterior is None or valor_anterior < 0):
                    erros.append(f'{rot}: valor anterior inválido.')
            novos_s.append(dict(obj=s, tipo=tipo, rot=rot, plano=plano, acesso=acesso, ordem=ordem,
                                portabilidade=portabilidade, operadora=operadora if operadora in operadoras else '',
                                simcard=simcard, valor=valor, valor_anterior=valor_anterior, mudou=mudou))

        if not itens and not novos_p and not novos_s:
            erros.append('A venda ficaria sem produto e sem serviço — para isso, exclua a venda.')
        if erros:
            raise VendaInvalida(erros)

        # ── Gravar (tudo validado) ──
        lote = uuid.uuid4().hex
        pdv = venda.pdv_nome or (venda.loja.name if venda.loja else '')
        extra = {'venda_id': venda.pk, 'motivo': motivo, 'lote': lote}
        rotulo_venda = f'Venda #{venda.pk}'
        gravadas = 0
        tipos_d = dict(VendaServico.TIPOS)

        for p in removidos_p:
            gravadas += slv.registrar_alteracoes('VENDA_PRODUTO', p, [
                ('removido', 'Produto removido', _resumo_produto(p), ''),
                ('dados', 'Dados do item removido', _json(_como_dict(p)), '')],
                usuario, pdv=pdv, rotulo=f'{rotulo_venda} · produto: {p.nome_produto}', extra=extra)
            p.delete()
        for i in itens:
            p = i['obj']
            (nome, serial, prateleira, sku, cor, sugerido, rv, rv_valor, codigo, ra, ra_valor, ra_pedido,
             ras, ras_valor, ras_pedido) = i['novo']
            final = i['final']
            calculado = i['conta']['valor_calculado']
            editado = (i['bruto'] != calculado) if i['recalc'] else p.valor_editado
            if not i['recalc']:
                editado_por = p.editado_por
            elif editado:
                editado_por = usuario if (i['final_mexido'] or not p.valor_editado) else p.editado_por
            else:
                editado_por = None
            regra = p.regra_preco
            if sugerido != p.valor_sugerido:
                regra = 'valor ajustado na edição'
            mud = [
                ('nome_produto', 'Produto', p.nome_produto, nome),
                ('serial', 'IMEI' if (p.categoria_slv == 'aparelho') else 'Serial', p.serial, serial),
                ('prateleira_infinita', 'Prateleira infinita', _sim(p.prateleira_infinita), _sim(prateleira)),
                ('sku', 'SKU', p.sku, sku), ('cor', 'Cor', p.cor, cor),
                ('valor_sugerido', 'Valor de produto', _r(p.valor_sugerido), _r(sugerido)),
                ('regra_preco', 'Regra do preço', p.regra_preco, regra),
                ('renova_vini', 'Renova Vini', _sim(p.renova_vini), _sim(rv)),
                ('renova_vini_valor', 'Valor Renova Vini', _r(p.renova_vini_valor), _r(rv_valor)),
                ('renova_codigo', 'Código do Renova', p.renova_codigo, codigo),
                ('renova_alied', 'Renova Alied', _sim(p.renova_alied), _sim(ra)),
                ('renova_alied_valor', 'Valor Renova Alied', _r(p.renova_alied_valor), _r(ra_valor)),
                ('renova_alied_pedido', 'Pedido Renova Alied', p.renova_alied_pedido, ra_pedido),
                ('renova_assurant', 'Renova Assurant', _sim(p.renova_assurant), _sim(ras)),
                ('renova_assurant_valor', 'Valor Renova Assurant', _r(p.renova_assurant_valor), _r(ras_valor)),
                ('renova_assurant_pedido', 'Pedido Renova Assurant', p.renova_assurant_pedido, ras_pedido),
                ('desconto_vivo_mais', 'Desconto Vivo+', _r(p.desconto_vivo_mais), _r(i['conta']['desconto_vivo_mais'])),
                ('valor_calculado', 'Valor calculado pelo sistema', _r(p.valor_calculado), _r(calculado)),
                ('desconto_pix', 'Desconto no Pix', _r(p.desconto_pix), _r(i['desconto_pix'])),
                ('valor_venda', 'Valor final (un.)', _r(p.valor_venda), _r(final)),
                ('valor_editado', 'Valor final editado à mão', _sim(p.valor_editado), _sim(editado)),
            ]
            n = slv.registrar_alteracoes('VENDA_PRODUTO', p, mud, usuario, pdv=pdv,
                                         rotulo=f'{rotulo_venda} · produto: {nome}', extra=extra)
            if n:
                gravadas += n
                p.nome_produto, p.serial, p.prateleira_infinita, p.sku, p.cor = nome, serial, prateleira, sku, cor
                p.valor_sugerido, p.regra_preco = sugerido, regra[:120]
                p.renova_vini, p.renova_vini_valor, p.renova_codigo = rv, rv_valor, codigo
                p.renova_alied, p.renova_alied_valor, p.renova_alied_pedido = ra, ra_valor, ra_pedido
                p.renova_assurant, p.renova_assurant_valor, p.renova_assurant_pedido = ras, ras_valor, ras_pedido
                p.desconto_vivo_mais, p.valor_calculado = i['conta']['desconto_vivo_mais'], calculado
                p.desconto_pix, p.valor_venda = i['desconto_pix'], final
                p.valor_editado, p.editado_por = editado, editado_por
                p.save()

        resumo_antes, resumo_depois = _resumo_pagamentos(atuais), _resumo_pagamentos(pagamentos)
        if resumo_antes != resumo_depois:
            gravadas += slv.registrar_alteracoes('VENDA_PAGAMENTO', venda, [
                ('pagamentos', 'Pagamentos', resumo_antes, resumo_depois)], usuario, pdv=pdv,
                rotulo=f'{rotulo_venda} · pagamentos', extra=extra)
            venda.pagamentos.all().delete()
            for n, pg in enumerate(pagamentos, start=1):
                PagamentoVenda.objects.create(venda=venda, ordem=n, **pg)

        for i in novos_p:
            novo_obj = slv.criar_produto(venda, i, usuario, venda.plano_anterior_nome)
            gravadas += slv.registrar_alteracoes('VENDA_PRODUTO', novo_obj, [
                ('incluido', 'Produto incluído', '', _resumo_produto(novo_obj))],
                usuario, pdv=pdv, rotulo=f'{rotulo_venda} · produto: {novo_obj.nome_produto}', extra=extra)
        for s in removidos_s:
            gravadas += slv.registrar_alteracoes('VENDA_SERVICO', s, [
                ('removido', 'Serviço removido', _resumo_servico(s), ''),
                ('dados', 'Dados do item removido', _json(_como_dict(s)), '')],
                usuario, pdv=pdv, rotulo=f'{rotulo_venda} · serviço: {s.get_tipo_servico_display() or s.servico}',
                extra=extra)
            s.delete()
        fake_servicos = False
        for d in novos_s:
            s = d['obj']
            plano = d['plano']
            linhas = list(s.linhas or [])
            if d['acesso'] != s.numero_acesso:
                linhas = ([d['acesso']] if d['acesso'] else []) + [x for x in linhas if x not in (s.numero_acesso, d['acesso'])]
            fake = any(slv.numero_fake(x) for x in linhas) or (bool(d['acesso']) and slv.numero_fake(d['acesso']))
            if not d['mudou']:
                fake_servicos = fake_servicos or s.numero_fake
                continue
            fake_servicos = fake_servicos or fake
            delta_novo = slv.delta(d['valor'], d['valor_anterior']) if plano else None
            mud = [
                ('tipo_servico', 'Tipo de serviço', tipos_d.get(s.tipo_servico, s.servico), tipos_d.get(d['tipo'])),
                ('plano', 'Plano', s.plano.nome if s.plano else '', plano.nome if plano else ''),
                ('numero_acesso', 'Número de acesso', slv.formatar_telefone(s.numero_acesso),
                 slv.formatar_telefone(d['acesso'])),
                ('numero_ordem', 'Número da ordem (fibra)', s.numero_ordem, d['ordem']),
                ('portabilidade', 'Portabilidade', _sim(s.portabilidade), _sim(d['portabilidade'])),
                ('operadora_origem', 'Operadora de origem', dict(VendaServico.OPERADORAS).get(s.operadora_origem, ''),
                 dict(VendaServico.OPERADORAS).get(d['operadora'], '')),
                ('serial_simcard', 'Serial do SimCard', s.serial_simcard, d['simcard']),
                ('valor_plano', 'Valor do plano', _r(s.valor_plano), _r(d['valor'])),
                ('valor_anterior', 'Valor anterior', _r(s.valor_anterior), _r(d['valor_anterior'])),
                ('delta', 'Delta', _r(s.delta), _r(delta_novo)),
                ('numero_fake', 'Número fictício', _sim(s.numero_fake), _sim(fake)),
            ]
            n = slv.registrar_alteracoes('VENDA_SERVICO', s, mud, usuario, pdv=pdv,
                                         rotulo=f'{rotulo_venda} · serviço: {d["rot"]}', extra=extra)
            if n:
                gravadas += n
                s.tipo_servico, s.servico = d['tipo'], d['rot'][:200]
                if plano is not None:
                    s.plano, s.plano_novo = plano, plano.nome[:200]
                    s.segmentacao, s.tipo_plano = plano.segmentacao, plano.segmentacao
                elif s.plano_id:
                    s.plano, s.segmentacao, s.tipo_plano = None, '', ''
                    s.plano_novo = (s.servico_adicional.nome if s.servico_adicional else '')[:200]
                s.numero_acesso, s.linhas, s.numero_fake = d['acesso'], linhas, fake
                s.numero_ordem, s.portabilidade, s.operadora_origem = d['ordem'], d['portabilidade'], d['operadora']
                s.serial_simcard, s.valor_plano, s.valor_anterior, s.delta = d['simcard'], d['valor'], d['valor_anterior'], delta_novo
                s.save()

        # Cliente: trocar o CPF liga a venda ao cadastro desse CPF (ou cria um, como no lançamento).
        cliente = venda.cliente
        if (cpf_mudou or cliente is None) and cpf and slv.documento_valido(cpf):
            cliente = Cliente.objects.filter(cpf=cpf).first()
            if cliente is None:
                cliente, _criado, _ = slv.salvar_cliente(cpf, {'nome': nome_cliente, 'telefone': telefone}, usuario, pdv=pdv)
        tipo_venda = venda.tipo_venda
        tinha = (bool(produtos), bool(servicos))
        fica = (bool(itens or novos_p), bool(novos_s))
        if tinha != fica:
            tipo_venda = ' + '.join(x for x in ('Produto' if (itens or novos_p) else '', 'Serviço' if novos_s else '') if x)
        cpf_gravado = slv.formatar_cpf(cpf) if cpf_mudou else venda.cliente_cpf
        if removidos_s or any(d['mudou'] for d in novos_s) or slv.so_digitos(venda.cliente_telefone) != telefone:
            numero_fake_venda = fake_servicos or (bool(telefone) and slv.numero_fake(telefone))
        else:
            numero_fake_venda = venda.numero_fake
        comprovantes = dict(Venda.COMPROVANTE_CHOICES)
        formas_d = dict(Venda._meta.get_field('forma_pagamento').choices)
        mud_venda = [
            ('data_venda', 'Data da venda', _data(venda.data_venda), _data(data_venda)),
            ('loja', 'Loja / PDV', venda.loja.name if venda.loja else venda.pdv_nome, loja.name if loja else ''),
            ('vendedor', 'Vendedor', _pessoa(venda.vendedor), _pessoa(vendedor)),
            ('cliente_nome', 'Cliente', venda.cliente_nome, nome_cliente),
            ('cliente_cpf', 'CPF/CNPJ do cliente', venda.cliente_cpf, cpf_gravado),
            ('cliente', 'Cadastro do cliente', str(venda.cliente) if venda.cliente else '', str(cliente) if cliente else ''),
            ('cliente_telefone', 'Telefone do cliente', slv.formatar_telefone(venda.cliente_telefone),
             slv.formatar_telefone(telefone)),
            ('tipo_venda', 'Tipo de venda', venda.tipo_venda, tipo_venda),
            ('comprovante_fiscal', 'Comprovante fiscal', comprovantes.get(venda.comprovante_fiscal, venda.comprovante_fiscal),
             comprovantes.get(comprovante, comprovante)),
            ('estoque_avancado', 'Estoque avançado', _sim(venda.estoque_avancado), _sim(estoque)),
            ('observacao', 'Observação', venda.observacao, observacao),
            ('vivo_mais', 'Pago pelo Vivo+', _sim(venda.vivo_mais), _sim(vivo_mais)),
            ('vivo_mais_percentual', 'Percentual Vivo+',
             f'{venda.vivo_mais_percentual}%' if venda.vivo_mais_percentual is not None else '',
             f'{percentual}%' if percentual is not None else ''),
            ('desconto_pix', 'Desconto no Pix', _r(venda.desconto_pix), _r(desconto_pix)),
            ('forma_pagamento', 'Forma de pagamento', formas_d.get(venda.forma_pagamento, venda.forma_pagamento),
             formas_d.get(forma, forma)),
            ('nsu', 'NSU do cartão', venda.nsu, nsu),
            ('parcelas', 'Parcelas no cartão', venda.parcelas or '', parcelas or ''),
            ('numero_fake', 'Tem número fictício', _sim(venda.numero_fake), _sim(numero_fake_venda)),
        ]
        gravadas += slv.registrar_alteracoes('VENDA', venda, mud_venda, usuario, pdv=pdv, rotulo=rotulo_venda, extra=extra)
        if not gravadas:
            return 0
        if loja is not None and loja.pk != venda.loja_id:
            venda.pdv_nome = loja.name[:120]
        venda.data_venda, venda.loja, venda.vendedor = data_venda, loja, vendedor
        venda.cliente, venda.cliente_nome, venda.cliente_cpf = cliente, nome_cliente, cpf_gravado
        venda.cliente_telefone, venda.tipo_venda, venda.comprovante_fiscal = telefone, tipo_venda[:80], comprovante
        venda.estoque_avancado, venda.observacao = estoque, observacao
        venda.vivo_mais, venda.vivo_mais_percentual, venda.desconto_pix = vivo_mais, percentual, desconto_pix
        venda.forma_pagamento, venda.nsu, venda.parcelas, venda.numero_fake = forma, nsu, parcelas, numero_fake_venda
        venda.save()      # muda o updated_at: um formulário aberto antes desta edição passa a ser recusado
        return gravadas


# ── Exclusão ────────────────────────────────────────────────────────────────
def ligacoes_que_impedem(venda):
    """O que, fora de Vendas, aponta para a venda e não pode perder o par sem alguém decidir.

    Hoje: a conciliação de cartões do Financeiro (RecebivelCartao.venda). Os filhos da venda
    (produtos, serviços, pagamentos) vão junto com ela e ficam na fotografia.
    """
    avisos = []
    try:
        from financeiro.models import RecebivelCartao
        recebiveis = list(RecebivelCartao.objects.filter(venda=venda).values_list('nsu', 'status'))
    except Exception:  # noqa: BLE001 — Financeiro fora deste servidor: nada a conferir
        recebiveis = []
    if recebiveis:
        nsus = ', '.join(n for n, _s in recebiveis[:5])
        avisos.append(f'A venda está ligada a {len(recebiveis)} registro(s) da adquirente na conciliação de cartões '
                      f'(NSU {nsus}). Solte o par em Financeiro › Cartões antes de excluir.')
    return avisos


def excluir_venda(pk, usuario, motivo, versao_vista=None):
    """Grava a fotografia completa no histórico e apaga a venda (produtos, serviços e pagamentos juntos)."""
    from .models import Venda
    motivo = ' '.join(str(motivo or '').split())[:2000]
    if len(motivo) < MOTIVO_MINIMO:
        raise VendaInvalida(['Diga o motivo da exclusão (fica no histórico).'])
    with transaction.atomic():
        venda = Venda.objects.select_for_update().select_related('loja', 'vendedor', 'created_by', 'cliente').filter(pk=pk).first()
        if venda is None:
            raise VendaInvalida(['Esta venda não existe mais.'])
        if versao_vista is not None and versao_vista != versao(venda):
            raise VendaDesatualizada(['Esta venda foi alterada depois que você abriu a tela. Confira e tente de novo.'])
        avisos = ligacoes_que_impedem(venda)
        if avisos:
            raise VendaInvalida(avisos)
        foto = fotografia(venda)
        pdv = venda.pdv_nome or (venda.loja.name if venda.loja else '')
        resumo = (f'{venda.cliente_nome or "sem cliente"} · {_data(venda.data_venda)} · {pdv or "sem PDV"} · '
                  f'{len(foto["produtos"])} produto(s), {len(foto["servicos"])} serviço(s) · total {_brl(venda.total)}')
        slv.registrar_alteracoes('VENDA_EXCLUIDA', venda, [
            ('resumo', 'Venda excluída', resumo, ''),
            ('fotografia', 'Fotografia da venda', _json(foto), '')],
            usuario, pdv=pdv, rotulo=f'Venda #{venda.pk} — {venda.cliente_nome or "s/ cliente"}'[:200],
            extra={'venda_id': venda.pk, 'motivo': motivo, 'lote': uuid.uuid4().hex})
        venda.delete()
        return foto
