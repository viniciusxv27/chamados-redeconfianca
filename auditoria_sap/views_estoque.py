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
LINHAS_POR_PAGINA = 80
UNIDADES_POR_PAGINA = 100


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

    if aba in ('disponivel', 'transito'):
        colunas, linhas = _matriz(foto, filtros, aba)
        pagina = Paginator(linhas, LINHAS_POR_PAGINA).get_page(request.GET.get('pagina'))
        contexto.update(colunas=colunas, pagina=pagina, total_linhas=len(linhas),
                        total_unidades=int(sum(l['total'] for l in linhas)))
    elif aba == 'reposicao':
        linhas = est.reposicao(foto, filtros['loja'] or None, busca=filtros['q'],
                               categoria=filtros['categoria'], status=filtros['situacao'])
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
        'km': round(km) if km is not None else None,
        'produto': {k: p[k] for k in ('nome', 'cor', 'material', 'categoria', 'descricao_sap')},
        'quantidade': int(qtd),
        'sem_serial': max(0, int(qtd) - len(unidades)),
        'unidades': [{
            'serial': u['serial'], 'deposito': u['deposito'], 'deposito_nome': u['deposito_nome'],
            'status': ' '.join(x for x in (u['status_sistema'], u['status_usuario']) if x),
            'desde': u['desde'].strftime('%d/%m/%Y') if u['desde'] else '', 'dias': u['dias'],
        } for u in unidades],
    })
