"""Tela de estoque da Visão SAP (23/11/2026) — a regra mora em ``auditoria_sap/estoque.py``.

Uma tela só, com a loja escolhida no topo ("Minha loja", que já abre na loja de
quem entra) e cinco abas: Disponível, Em trânsito, Reposição, Detalhado e
Produtos. Clicar numa quantidade abre a gaveta com as unidades (IMEI/serial,
depósito, desde quando, dias) — é o serial que a loja pede na transferência.

Estoque não tem cliente nem documento: quem abre a Visão SAP (a administração e
o gerente de loja) vê o estoque da rede inteira — é o que precisa para achar a
peça na loja vizinha.
"""
import csv
import logging

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.gzip import gzip_page

from . import estoque as est
from .mysql import SapIndisponivel
from .permissions import pode_escolher_gestores
from .views import so_auditoria

logger = logging.getLogger(__name__)

ABAS = (
    ('disponivel', 'Disponível', 'fa-boxes-stacked'),
    ('transito', 'Em trânsito', 'fa-truck-fast'),
    ('reposicao', 'Reposição', 'fa-cart-plus'),
    ('detalhado', 'Detalhado', 'fa-list'),
    ('produtos', 'Produtos', 'fa-tags'),
)
SITUACOES_REPOSICAO = (
    ('', 'Todas'), ('critico', 'Críticos'), ('ruptura', 'Em ruptura'),
    ('insuficiente', 'Insuficiente'), ('ok', 'Coberto'),
)
# A tabela rola dentro do cartão (cabeçalho e produto fixos), então cabe muito mais
# numa página; ordenar e filtrar é na hora, no navegador, sobre o que está na tela.
LINHAS_POR_PAGINA = 600
UNIDADES_POR_PAGINA = 500
# Uma cor por loja (na ordem de ``LOJAS``): o nome pintado no cabeçalho, nos chips e na gaveta.
CORES_DAS_LOJAS = (
    ('#0EA5E9', '#E0F2FE'), ('#8B5CF6', '#EDE9FE'), ('#F97316', '#FFEDD5'), ('#10B981', '#D1FAE5'),
    ('#EC4899', '#FCE7F3'), ('#EAB308', '#FEF9C3'), ('#6366F1', '#E0E7FF'), ('#14B8A6', '#CCFBF1'),
    ('#EF4444', '#FEE2E2'), ('#84CC16', '#ECFCCB'), ('#06B6D4', '#CFFAFE'), ('#D946EF', '#FAE8FF'),
)


def cor_da_loja(centro):
    ordem = [loja.centro for loja in est.LOJAS]
    i = ordem.index(centro) if centro in ordem else sum(map(ord, centro or 'x'))
    forte, clara = CORES_DAS_LOJAS[i % len(CORES_DAS_LOJAS)]
    return {'forte': forte, 'clara': clara}


def faixa_de_distancia(km):
    """perto (até 30 km), media (até 120 km) ou longe — a cor do chip da distância."""
    if km is None:
        return ''
    return 'perto' if km <= 30 else ('media' if km <= 120 else 'longe')


def _loja_padrao(user):
    """A loja de quem entra (setor principal), para abrir já nela."""
    setor = getattr(user, 'sector', None)
    return est.centro_da_loja(setor.name) if setor else ''


def _filtros(request, foto):
    g = request.GET
    centros = est.centros_da_foto(foto)
    loja = (g.get('loja') or '').strip().upper()
    if loja == 'TODAS':
        loja = ''
    elif loja not in centros:
        loja = '' if 'loja' in g else _loja_padrao(request.user)
        if loja not in centros:
            loja = ''
    aba = g.get('aba') if g.get('aba') in {a for a, _r, _i in ABAS} else 'disponivel'
    return {
        'aba': aba,
        'loja': loja,
        'q': (g.get('q') or '').strip()[:80],
        'categoria': (g.get('categoria') or '').strip(),
        'serial': (g.get('serial') or '').strip()[:40],
        'situacao': g.get('situacao') if g.get('situacao') in {s for s, _r in SITUACOES_REPOSICAO} else '',
        'deposito': (g.get('deposito') or '').strip().upper(),
        'fabricante': (g.get('fabricante') or '').strip(),
        'ordem': 'rede' if g.get('ordem') == 'rede' else 'nome',
    }


def _query(filtros, **troca):
    from urllib.parse import urlencode
    f = {**filtros, **troca}
    f['loja'] = f['loja'] or 'todas'
    return urlencode({k: v for k, v in f.items() if v and not (k == 'ordem' and v == 'nome')})


def _lojas_para_escolher(foto):
    """As lojas do seletor, por estado (ES, RJ, SP)."""
    grupos = {}
    for centro in est.centros_da_foto(foto):
        loja = est.POR_CENTRO.get(centro)
        uf = loja.uf if loja else 'Outras'
        grupos.setdefault(uf, []).append({'centro': centro, 'nome': est.nome_do_centro(foto, centro),
                                          'cidade': loja.cidade if loja else ''})
    return sorted(grupos.items(), key=lambda par: ('ES', 'RJ', 'SP').index(par[0]) if par[0] in ('ES', 'RJ', 'SP') else 9)


def _resumo_da_loja(foto, loja):
    """Os quatro números do topo: livre, em trânsito, rupturas e sugestão (da loja ou da rede)."""
    centros = [loja] if loja else est.centros_da_foto(foto)
    livre = sum(deps.get(est.DEPOSITO_VENDA, (0, 0))[0] for (c, _m), deps in foto['saldos'].items() if c in centros)
    transito = sum(q for (c, _m), q in foto['transito'].items() if c in centros)
    repos = est.reposicao(foto, loja or None) if foto['vendas'] is not None else []
    return {
        'livre': int(livre),
        'transito': int(transito),
        'ruptura': sum(1 for r in repos if r['situacao'] in ('ruptura', 'critico')),
        'criticos': sum(1 for r in repos if r['situacao'] == 'critico'),
        'sugestao': sum(r['sugestao'] for r in repos),
    }


def _matriz(foto, filtros, modo):
    colunas = est.colunas_da_matriz(foto, filtros['loja'])
    for c in colunas:
        c['cor'] = cor_da_loja(c['centro'])
        c['faixa'] = faixa_de_distancia(c['km'])
    linhas = est.matriz(foto, colunas, modo=modo, busca=filtros['q'], categoria=filtros['categoria'])
    if filtros['ordem'] == 'rede':
        linhas.sort(key=lambda l: (-l['rede'], l['produto']['nome']))
    else:
        linhas.sort(key=lambda l: (l['produto']['nome'].lower(), l['produto']['cor']))
    if modo == 'disponivel' and filtros['loja']:
        # Zerado na loja escolhida: diz onde está a mais perto (mesmo fora das seis colunas).
        for linha in linhas:
            if not linha['qtds'][0]:
                linha['mais_perto'] = est.mais_perto_com_saldo(foto, filtros['loja'], linha['produto']['material'])
    return colunas, linhas


# A matriz da rede passa de 2 MB de HTML e vira ~100 KB comprimida (o portal não
# comprime as respostas por padrão).
@gzip_page
@login_required
@so_auditoria
def estoque(request):
    try:
        foto = est.fotografia()
    except SapIndisponivel as exc:
        logger.warning('Estoque SAP indisponível', exc_info=True)
        return render(request, 'auditoria_sap/estoque.html', {
            'aba': 'estoque', 'erro': str(exc), 'abas': ABAS,
            'pode_escolher_gestores': pode_escolher_gestores(request.user)})

    filtros = _filtros(request, foto)
    aba = filtros['aba']
    contexto = {
        'aba': 'estoque',
        'abas': ABAS,
        'f': filtros,
        'foto': foto,
        'lojas': _lojas_para_escolher(foto),
        'loja_nome': est.nome_do_centro(foto, filtros['loja']) if filtros['loja'] else '',
        'loja_info': est.POR_CENTRO.get(filtros['loja']),
        'loja_cor': cor_da_loja(filtros['loja']) if filtros['loja'] else None,
        'vizinhas': [{'centro': c, 'nome': est.nome_do_centro(foto, c), 'km': km, 'faixa': faixa_de_distancia(km),
                      'cor': cor_da_loja(c)}
                     for c, km in est.vizinhas(filtros['loja'], centros=est.centros_da_foto(foto))]
                    if filtros['loja'] else [],
        'minha_loja': _loja_padrao(request.user),
        'categorias': est.categorias(foto),
        'resumo': _resumo_da_loja(foto, filtros['loja']),
        'sem_vendas': foto['vendas'] is None,
        'pode_escolher_gestores': pode_escolher_gestores(request.user),
        'q_base': _query(filtros, aba='', situacao='', deposito='', fabricante=''),
        'q_atual': _query(filtros),
    }
    if filtros['serial']:
        contexto['achados'] = est.unidades_de(foto, modo='todas', serial=filtros['serial'])[:30]
        for u in contexto['achados']:
            u['cor'] = cor_da_loja(u['centro'])
            u['produto'] = est.produto(foto, u['material'])

    if aba in ('disponivel', 'transito'):
        colunas, linhas = _matriz(foto, filtros, aba)
        pagina = Paginator(linhas, LINHAS_POR_PAGINA).get_page(request.GET.get('pagina'))
        contexto.update(colunas=colunas, pagina=pagina, total_linhas=len(linhas),
                        total_unidades=int(sum(l['total'] for l in linhas)))
    elif aba == 'reposicao':
        linhas = est.reposicao(foto, filtros['loja'] or None, busca=filtros['q'],
                               categoria=filtros['categoria'], status=filtros['situacao'])
        for linha in linhas:
            linha['cor'] = cor_da_loja(linha['centro'])
            if linha['buscar_em']:
                linha['buscar_em']['cor'] = cor_da_loja(linha['buscar_em']['centro'])
        contexto.update(pagina=Paginator(linhas, LINHAS_POR_PAGINA).get_page(request.GET.get('pagina')),
                        total_linhas=len(linhas), situacoes=SITUACOES_REPOSICAO,
                        sugestao_filtrada=sum(l['sugestao'] for l in linhas))
    elif aba == 'detalhado':
        unidades = _detalhado(foto, filtros)
        if request.GET.get('formato') == 'csv':
            return _csv_detalhado(foto, unidades)
        depositos = sorted({u[3] for u in foto['unidades']} - {''})
        contexto.update(pagina=Paginator(unidades, UNIDADES_POR_PAGINA).get_page(request.GET.get('pagina')),
                        total_linhas=len(unidades),
                        depositos=[(d, est.DEPOSITOS.get(d, d)) for d in depositos] + [('TRANSITO', 'Em trânsito')])
    else:
        linhas = est.catalogo(foto, busca=filtros['q'], categoria=filtros['categoria'],
                              fabricante=filtros['fabricante'])
        contexto.update(pagina=Paginator(linhas, LINHAS_POR_PAGINA).get_page(request.GET.get('pagina')),
                        total_linhas=len(linhas), fabricantes=est.fabricantes(foto))
    return render(request, 'auditoria_sap/estoque.html', contexto)


def _detalhado(foto, filtros):
    unidades = est.unidades_de(foto, centro=filtros['loja'] or None, modo='todas', serial=filtros['serial'])
    if filtros['deposito'] == 'TRANSITO':
        unidades = [u for u in unidades if u['tipo'] == est.TIPO_TRANSITO]
    elif filtros['deposito']:
        unidades = [u for u in unidades if u['deposito'] == filtros['deposito']]
    if filtros['q'] or filtros['categoria']:
        cache_bate = {}
        saida = []
        for u in unidades:
            m = u['material']
            if m not in cache_bate:
                p = est.produto(foto, m)
                cache_bate[m] = (est.bate_busca(foto, m, filtros['q'])
                                 and (not filtros['categoria'] or p['categoria'] == filtros['categoria']))
            if cache_bate[m]:
                saida.append(u)
        unidades = saida
    nomes = {}
    for u in unidades:
        if u['material'] not in nomes:
            nomes[u['material']] = est.produto(foto, u['material'])
        u['produto'] = nomes[u['material']]
    unidades.sort(key=lambda u: (u['loja'], u['produto']['nome'], -(u['dias'] or 0)))
    for u in unidades:
        u['cor'] = cor_da_loja(u['centro'])
    return unidades


def _csv_detalhado(foto, unidades):
    resposta = HttpResponse(content_type='text/csv; charset=utf-8')
    resposta['Content-Disposition'] = (
        f'attachment; filename="estoque_detalhado_{timezone.localdate():%Y%m%d}.csv"')
    resposta.write('﻿')
    w = csv.writer(resposta, delimiter=';')
    w.writerow(['Centro', 'Loja', 'Produto', 'Cor', 'Material', 'Descrição SAP', 'IMEI / serial', 'Depósito',
                'Tipo de estoque', 'Status sistema', 'Status usuário', 'Última movimentação', 'Dias'])
    for u in unidades:
        p = u['produto']
        w.writerow([u['centro'], u['loja'], p['nome'], p['cor'], u['material'], p['descricao_sap'], u['serial'],
                    f"{u['deposito']} ({u['deposito_nome']})", u['tipo_nome'], u['status_sistema'],
                    u['status_usuario'], u['desde'].strftime('%d/%m/%Y') if u['desde'] else '',
                    '' if u['dias'] is None else u['dias']])
    return resposta


# ---------------------------------------------------------------------------
# Preço e Renova (24/11/2026): "faltou adicionar o valor do produto, quando clicar em cima,
# e se tiver desconto do Renova, avisar".
# ---------------------------------------------------------------------------
GRUPO_DE_REFERENCIA = 'PRÉ'        # o preço "de vitrine" é o do bloco PRÉ da tabela da Vivo
_PRECOS = {}


def _dinheiro(valor):
    try:
        texto = str(valor).strip()
        if not texto or texto == '-':
            return None
        return round(float(texto.replace(',', '.')), 2)
    except (TypeError, ValueError):
        return None


def _indice_de_precos():
    """{SKU/material: preço} da tabela de preços importada em /vendas/precos/ (vendas.ItemPreco).

    O SKU da tabela é o material do SAP (um por cor, em ``extra['skus']``). Guardado em
    memória até a tabela mudar (nova importação ou edição)."""
    from django.db.models import Count, Max
    from vendas.models import ItemPreco

    ativos = ItemPreco.objects.filter(ativo=True)
    marca = tuple(ativos.aggregate(n=Count('id'), i=Max('importado_em'), u=Max('updated_at')).values())
    if _PRECOS.get('marca') == marca:
        return _PRECOS['indice']
    indice = {}
    for item in ativos.only('id', 'nome', 'categoria', 'cod_sap', 'valor', 'extra', 'importado_em'):
        extra = item.extra or {}
        grupos = []
        for grupo, valores in (extra.get('tabela') or {}).items():
            if not isinstance(valores, dict):
                continue
            preco = _dinheiro(valores.get('Preço'))
            if preco is None:
                continue
            t12, t21 = _dinheiro(valores.get('12x')), _dinheiro(valores.get('21x'))
            grupos.append({'grupo': grupo, 'preco': preco, 'pix': _dinheiro(valores.get('PIX e Vivo Pay')),
                           'parcela12': round(t12 / 12, 2) if t12 else None,
                           'parcela21': round(t21 / 21, 2) if t21 else None, 'total21': t21})
        base = next((g for g in grupos if g['grupo'].upper() == GRUPO_DE_REFERENCIA), grupos[0] if grupos else None)
        valor = float(item.valor) if item.valor is not None else (base['preco'] if base else None)
        if valor is None:
            continue
        preco = {
            'nome': item.nome, 'tabela': item.categoria, 'valor': valor,
            'pix': base['pix'] if base else None,
            'parcela12': base['parcela12'] if base else None,
            'parcela21': base['parcela21'] if base else None,
            'total21': base['total21'] if base else None,
            'grupo': base['grupo'] if base else '',
            'grupos': grupos,
            'portfolio': extra.get('PORTFÓLIO') or '',
            'atualizado': item.importado_em.strftime('%d/%m/%Y') if item.importado_em else '',
        }
        skus = [item.cod_sap] + [s.get('sku') for s in (extra.get('skus') or []) if isinstance(s, dict)]
        for sku in skus:
            if sku:
                indice.setdefault(str(sku).strip().upper(), preco)
    _PRECOS.clear()
    _PRECOS.update(marca=marca, indice=indice)
    return indice


def preco_do_material(material):
    try:
        return _indice_de_precos().get((material or '').upper())
    except Exception:                                            # noqa: BLE001 — sem preço, a gaveta abre igual
        logger.warning('Estoque SAP: tabela de preços não lida', exc_info=True)
        return None


def _renova_para_json(renova, preco):
    if not renova:
        return None
    valor = preco['valor'] if preco else None
    return {
        'desconto': renova['desconto'],
        'ate': renova['fim'].strftime('%d/%m/%Y') if hasattr(renova['fim'], 'strftime') else str(renova['fim']),
        'modelo': renova['modelo'],
        'com_renova': round(valor - renova['desconto'], 2) if valor else None,
    }


@login_required
@so_auditoria
def estoque_produto(request):
    """A gaveta do produto: preço (base, PIX/Vivo Pay, 12x, 21x, por plano), Renova e onde tem."""
    try:
        foto = est.fotografia()
    except SapIndisponivel as exc:
        return JsonResponse({'ok': False, 'erro': str(exc)}, status=503)
    material = (request.GET.get('material') or '').strip().upper()
    minha = (request.GET.get('minha') or '').strip().upper()
    if not material:
        return JsonResponse({'ok': False, 'erro': 'Produto inválido.'}, status=400)
    p = est.produto(foto, material)
    preco = preco_do_material(material)
    lojas = est.lojas_com_o_produto(foto, material, minha)
    for loja in lojas:
        loja['cor'] = cor_da_loja(loja['centro'])
    return JsonResponse({
        'ok': True,
        'produto': {k: p[k] for k in ('nome', 'cor', 'material', 'categoria', 'subcategoria', 'fabricante',
                                      'descricao_sap')},
        'preco': preco,
        'renova': _renova_para_json(p.get('renova'), preco),
        'lojas': lojas,
        'livre_rede': sum(l['livre'] for l in lojas),
        'transito_rede': sum(l['transito'] for l in lojas),
        'minha': est.nome_do_centro(foto, minha) if minha in est.centros_da_foto(foto) else '',
    })


@login_required
@so_auditoria
def estoque_unidades(request):
    """A gaveta: as unidades de uma loja e um material (disponível ou em trânsito)."""
    try:
        foto = est.fotografia()
    except SapIndisponivel as exc:
        return JsonResponse({'ok': False, 'erro': str(exc)}, status=503)
    centro = (request.GET.get('centro') or '').strip().upper()
    material = (request.GET.get('material') or '').strip().upper()
    modo = 'transito' if request.GET.get('modo') == 'transito' else 'disponivel'
    minha = (request.GET.get('minha') or '').strip().upper()
    if centro not in est.centros_da_foto(foto) or not material:
        return JsonResponse({'ok': False, 'erro': 'Loja ou produto inválido.'}, status=400)
    unidades = est.unidades_de(foto, centro, material, modo=modo)
    qtd = est.em_transito(foto, centro, material) if modo == 'transito' else est.livre_para_venda(foto, centro, material)
    km = None
    if minha and minha != centro:
        km = est.distancia_km(est.POR_CENTRO.get(minha), est.POR_CENTRO.get(centro))
    p = est.produto(foto, material)
    return JsonResponse({
        'ok': True,
        'modo': modo,
        'loja': est.nome_do_centro(foto, centro),
        'centro': centro,
        'cor': cor_da_loja(centro),
        'km': round(km) if km is not None else None,
        'produto': {k: p[k] for k in ('nome', 'cor', 'material', 'categoria', 'descricao_sap')},
        'preco': preco_do_material(material),
        'renova': _renova_para_json(p.get('renova'), preco_do_material(material)),
        'quantidade': int(qtd),
        'sem_serial': max(0, int(qtd) - len(unidades)),
        'unidades': [{
            'serial': u['serial'], 'deposito': u['deposito'], 'deposito_nome': u['deposito_nome'],
            'status': ' '.join(x for x in (u['status_sistema'], u['status_usuario']) if x),
            'desde': u['desde'].strftime('%d/%m/%Y') if u['desde'] else '', 'dias': u['dias'],
        } for u in unidades],
    })
