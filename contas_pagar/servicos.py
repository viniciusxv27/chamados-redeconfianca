"""Cola entre as regras, o Postgres do portal e o MySQL do histórico."""
from datetime import date

from django.db import transaction
from django.utils import timezone

from . import mysql, regras
from .models import (Categoria, ConfiguracaoContasPagar, ContaBancaria, LogContasPagar, Rascunho, SaldoRascunho,
                     TituloRascunho)


def contexto_regras():
    """(bancos, categorias, config) como o carregar_ctx() antigo."""
    bancos = [b.como_dict() for b in ContaBancaria.objects.order_by('ordem', 'id')]
    cats = regras.mapa_categorias(Categoria.objects.values_list('tipo', 'categoria'))
    cfg = ConfiguracaoContasPagar.atual().como_dict()
    return bancos, cats, cfg


def _num(v):
    return float(v) if v is not None else 0.0


# ── Rascunho ───────────────────────────────────────────────────────────────
@transaction.atomic
def importar(user, nome, conteudo):
    """Lê a BASE VIVOGO e cria o rascunho (levanta ``regras.ArquivoInvalido``)."""
    linhas = regras.ler_arquivo(nome, conteudo)
    regras.validar_linhas(linhas)
    hoje = timezone.localdate()
    r = Rascunho.objects.create(user=user, arquivo=(nome or '')[:255], data_pagamento=hoje, venc_ini=hoje,
                                venc_fim=hoje, qtd_linhas=len(linhas))
    TituloRascunho.objects.bulk_create([
        TituloRascunho(rascunho=r, vencimento=regras.parse_data(row.get('Data de vencimento')),
                       valor=regras.dinheiro(regras.parse_num(row.get('Valor líquido'))), dados=row)
        for row in linhas], batch_size=500)
    aviso = ''
    try:  # pré-preenche os saldos (sem transferência) com o último dia publicado
        por_nome = {c.nome: c for c in ContaBancaria.objects.all()}
        novos = []
        for s in mysql.ultimos_saldos():
            conta = por_nome.get(s['banco'])
            if conta:
                novos.append(SaldoRascunho(rascunho=r, conta=conta, aplicacao=regras.dinheiro(s.get('aplicacao') or 0),
                                           garantida=regras.dinheiro(s.get('conta_garantida') or 0),
                                           saldo=regras.dinheiro(s.get('saldo') or 0)))
        SaldoRascunho.objects.bulk_create(novos)
    except mysql.ContasPagarIndisponivel as exc:
        aviso = f'Os saldos não vieram pré-preenchidos: {exc}'
    return r, aviso


def titulos_do_rascunho(r, ctx=None):
    bancos, cats, cfg = ctx or contexto_regras()
    return [regras.titulo_tratado(t.id, t.vencimento, t.valor, t.excluido, t.dados, bancos, cats, cfg)
            for t in r.titulos.all()]


def detalhe(r):
    """O que a tela de montagem precisa (JSON)."""
    ctx = contexto_regras()
    titulos = titulos_do_rascunho(r, ctx)
    for t in titulos:
        t['valor'] = float(t['valor'])
    bancos = [dict(b, nome=regras.nome_banco(b)) for b in ctx[0] if b['ativo']]
    saldos = [{'banco_id': s.conta_id, 'aplicacao': _num(s.aplicacao), 'garantida': _num(s.garantida),
               'saldo': _num(s.saldo), 'transferencia': _num(s.transferencia)} for s in r.saldos.all()]
    return {'upload': {'id': r.id, 'arquivo': r.arquivo, 'criado_em': timezone.localtime(r.criado_em).isoformat(),
                       'data_pagamento': r.data_pagamento.isoformat(), 'venc_ini': r.venc_ini.isoformat(),
                       'venc_fim': r.venc_fim.isoformat(), 'qtd_linhas': r.qtd_linhas},
            'titulos': titulos, 'saldos': saldos, 'bancos': bancos}


def _data(v, campo):
    d = regras.parse_data(v)
    if d is None:
        raise ValueError(f'Data inválida em {campo}.')
    return d


@transaction.atomic
def salvar(r, dados):
    """Grava datas, títulos desmarcados e saldos (o PUT /uploads/<id> antigo)."""
    campos = []
    for k, rotulo in (('data_pagamento', 'data de pagamento'), ('venc_ini', 'vencimento de'),
                      ('venc_fim', 'vencimento até')):
        if k in dados:
            setattr(r, k, _data(dados[k], rotulo))
            campos.append(k)
    if r.venc_ini > r.venc_fim:
        raise ValueError('O vencimento inicial está depois do final.')
    if campos:
        r.save(update_fields=campos)
    if 'excluidos' in dados:
        ids = {int(i) for i in dados.get('excluidos') or [] if str(i).lstrip('-').isdigit()}
        r.titulos.update(excluido=False)
        if ids:
            r.titulos.filter(id__in=ids).update(excluido=True)
    if 'saldos' in dados:
        validas = set(ContaBancaria.objects.values_list('id', flat=True))
        r.saldos.all().delete()
        vistos = set()
        novos = []
        for s in dados.get('saldos') or []:
            try:
                bid = int(s.get('banco_id'))
            except (TypeError, ValueError):
                continue
            if bid not in validas or bid in vistos:
                continue
            vistos.add(bid)
            novos.append(SaldoRascunho(rascunho=r, conta_id=bid,
                                       **{k: regras.dinheiro(regras.parse_num(s.get(k)))
                                          for k in ('aplicacao', 'garantida', 'saldo', 'transferencia')}))
        SaldoRascunho.objects.bulk_create(novos)


def calcular_publicacao(r, agora=None):
    """(selecionados, linhas de títulos, linhas de saldo) do rascunho, sem gravar nada."""
    ctx = contexto_regras()
    titulos = titulos_do_rascunho(r, ctx)
    sel = regras.selecionados(titulos, r.venc_ini, r.venc_fim)
    saldos = {s.conta_id: {'aplicacao': s.aplicacao, 'garantida': s.garantida, 'saldo': s.saldo,
                           'transferencia': s.transferencia} for s in r.saldos.all()}
    agora = agora or timezone.localtime().replace(tzinfo=None, microsecond=0)
    lt, ls = regras.linhas_para_publicar(r.data_pagamento, sel, ctx[0], saldos, agora)
    return sel, lt, ls


def publicar(r, user):
    """Grava o dia no MySQL (substituindo o que havia), registra no log e apaga o rascunho."""
    sel, lt, ls = calcular_publicacao(r)
    antes = mysql.gravar_dia(r.data_pagamento, lt, ls)
    valor = sum((t['valor'] for t in sel), regras.ZERO)
    venc = sorted(t['vencimento'] for t in sel if t['vencimento'])
    with transaction.atomic():
        LogContasPagar.objects.create(
            acao=LogContasPagar.PUBLICOU, user=user, data_pagamento=r.data_pagamento, qtd_titulos=len(sel),
            valor=valor, arquivo=r.arquivo[:255],
            detalhe={'venc_ini': r.venc_ini.isoformat(), 'venc_fim': r.venc_fim.isoformat(),
                     'vencimentos': [venc[0], venc[-1]] if venc else [],
                     'substituiu': {'qtd': int((antes or {}).get('qtd') or 0),
                                    'valor': float((antes or {}).get('valor') or 0)},
                     'saldos': [{'banco': l[1], 'pagamentos': float(l[7]), 'saldo_final': float(l[9])} for l in ls]})
        r.delete()  # o arquivo importado não fica no histórico
    return {'data_pagamento': r.data_pagamento.isoformat(), 'titulos': len(sel), 'valor': float(valor)}


def excluir_dia(dia, user):
    antes = mysql.excluir_dia(dia)
    LogContasPagar.objects.create(acao=LogContasPagar.EXCLUIU_DIA, user=user, data_pagamento=dia,
                                  qtd_titulos=int(antes.get('qtd') or 0), valor=regras.dinheiro(antes.get('valor') or 0),
                                  detalhe={'saldos': int(antes.get('saldos') or 0)})
    return antes


def sem_categoria():
    """Tipos (Agging) dos rascunhos que ainda não têm categoria: [(tipo, qtd)]."""
    bancos, cats, cfg = contexto_regras()
    faltando = {}
    for dados in TituloRascunho.objects.values_list('dados', flat=True).iterator():
        ag, _, _ = regras.tratar(dados, bancos, cats, cfg)
        if ag.strip().upper() not in cats:
            faltando[ag] = faltando.get(ag, 0) + 1
    return sorted(faltando.items(), key=lambda x: (-x[1], x[0]))


# ── Painel e histórico ─────────────────────────────────────────────────────
def painel(ini, fim):
    titulos, saldos = mysql.ler_periodo(ini, fim)
    bancos_cad = [b.como_dict() for b in ContaBancaria.objects.order_by('ordem', 'id')]
    return regras.montar_painel(ini, fim, titulos, saldos, bancos_cad)


def historico():
    saida = []
    for h in mysql.historico():
        saida.append({'data_pagamento': _como_data(h['data_pagamento']), 'qtd': int(h['qtd'] or 0),
                      'valor': regras.dinheiro(h['valor'] or 0), 'venc_ini': _como_data(h.get('venc_ini')),
                      'venc_fim': _como_data(h.get('venc_fim')), 'publicado_em': h.get('publicado_em')})
    return saida


def _como_data(v):
    if v is None or isinstance(v, date):
        return v
    return regras.parse_data(v)
