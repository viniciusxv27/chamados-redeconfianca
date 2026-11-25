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
from django.utils.cache import patch_vary_headers
from django.views.decorators.gzip import gzip_page

from . import estoque as est
from .mysql import SapIndisponivel
from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

from .permissions import pode_escolher_gestores, pode_ver_estoque, pode_ver_estoque_lp, so_estoque


def so_quem_ve_estoque(view):
    """Quem vê a Visão SAP, ou quem foi liberado só para o estoque (sap.estoque)."""
    @wraps(view)
    def _view(request, *args, **kwargs):
        if not pode_ver_estoque(request.user):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'ok': False, 'erro': 'Sem acesso ao estoque SAP.'}, status=403)
            messages.error(request, 'O estoque SAP é restrito: peça a liberação ao SUPERADMIN.')
            return redirect('home')
        return view(request, *args, **kwargs)
    return _view



def so_superadmin_lp(view):
    """A aba Estoque LP (26/11/2026): só o SUPERADMIN. O ``lp=True`` vai para a view."""
    @wraps(view)
    def _view(request, *args, **kwargs):
        if not pode_ver_estoque_lp(request.user):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.headers.get('X-Estoque-Parcial'):
                return JsonResponse({'ok': False, 'erro': 'O estoque LP é só do SUPERADMIN.'}, status=403)
            messages.error(request, 'O estoque LP é só do SUPERADMIN.')
            return redirect('auditoria_sap:estoque')
        return view(request, *args, lp=True, **kwargs)
    return _view


logger = logging.getLogger(__name__)

# Panorama (24/11/2026, "mais intuitiva e dinâmica, com melhor visualização"): a abertura
# vira uma aba — números, estoque por loja em barras, saúde da reposição, tempo parado e categorias.
ABAS = (
    ('inicio', 'Panorama', 'fa-chart-pie'),
    ('disponivel', 'Disponível', 'fa-boxes-stacked'),
    ('transito', 'Em trânsito', 'fa-truck-fast'),
    ('reposicao', 'Reposição', 'fa-cart-plus'),
    ('detalhado', 'Detalhado', 'fa-list'),
    ('produtos', 'Produtos', 'fa-tags'),
)
# LP: só o saldo da MB52 — sem vendas no Vivo GO (sem reposição) e sem seriais na IQ09 (sem Detalhado).
ABAS_LP = tuple(a for a in ABAS if a[0] not in ('reposicao', 'detalhado'))
SITUACOES_REPOSICAO = (
    ('', 'Todas'), ('critico', 'Críticos'), ('ruptura', 'Em ruptura'),
    ('insuficiente', 'Insuficiente'), ('ok', 'Coberto'),
)
# A cor e a explicação de cada situação (barra da saúde, chips e selos).
SAUDE = (
    ('critico', 'Crítico', '#EF4444', 'zerado e vende 5+ por mês'),
    ('ruptura', 'Em ruptura', '#EC4899', 'zerado, mas vende'),
    ('insuficiente', 'Insuficiente', '#F97316', 'não cobre 30 dias'),
    ('ok', 'Coberto', '#22C55E', 'cobre o mês'),
)
# Há quanto tempo a unidade livre não se mexe (última movimentação da IQ09): o que encalhou.
IDADES = (
    ('ate30', 'Até 30 dias', 0, 30, '#22C55E'),
    ('31a60', '31 a 60 dias', 31, 60, '#84CC16'),
    ('61a90', '61 a 90 dias', 61, 90, '#F59E0B'),
    ('mais90', 'Mais de 90 dias', 91, None, '#EF4444'),
)
FILTROS_DE_TELA = ('aba', 'q', 'categoria', 'serial', 'loja', 'pagina', 'situacao', 'deposito', 'fabricante',
                   'ordem', 'formato', 'idade')
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


def _filtros(request, foto, abas=ABAS):
    g = request.GET
    centros = est.centros_da_foto(foto)
    loja = (g.get('loja') or '').strip().upper()
    if loja == 'TODAS':
        loja = ''
    elif loja not in centros:
        loja = '' if 'loja' in g else _loja_padrao(request.user)
        if loja not in centros:
            loja = ''
    aba = g.get('aba') if g.get('aba') in {a for a, _r, _i in abas} else ''
    if not aba:
        # Abriu sem escolher nada: o Panorama. Com filtro (busca, loja...), a tabela.
        aba = 'disponivel' if any(g.get(k) for k in FILTROS_DE_TELA) else 'inicio'
    if aba == 'inicio' and any((g.get(k) or '').strip() for k in ('q', 'categoria', 'serial')):
        aba = 'disponivel'               # buscou um produto no Panorama: mostra onde tem
    idade = g.get('idade') if g.get('idade') in {i[0] for i in IDADES} else ''
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
        'idade': idade,
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
        uf = est.uf_do_centro(centro) or 'Outras'
        grupos.setdefault(uf, []).append({'centro': centro, 'nome': est.nome_do_centro(foto, centro),
                                          'cidade': loja.cidade if loja else ''})
    return sorted(grupos.items(), key=lambda par: ('ES', 'RJ', 'SP').index(par[0]) if par[0] in ('ES', 'RJ', 'SP') else 9)


def _reposicao_da_loja(foto, loja):
    return est.reposicao(foto, loja or None) if foto['vendas'] is not None else []


def _resumo_da_loja(foto, loja, repos=None):
    """Os quatro números do topo: livre, em trânsito, rupturas e sugestão (da loja ou da rede)."""
    centros = set([loja] if loja else est.centros_da_foto(foto))
    livre = sum(deps.get(est.DEPOSITO_VENDA, (0, 0))[0] for (c, _m), deps in foto['saldos'].items() if c in centros)
    transito = sum(q for (c, _m), q in foto['transito'].items() if c in centros)
    if repos is None:
        repos = _reposicao_da_loja(foto, loja)
    return {
        'livre': int(livre),
        'transito': int(transito),
        'ruptura': sum(1 for r in repos if r['situacao'] in ('ruptura', 'critico')),
        'criticos': sum(1 for r in repos if r['situacao'] == 'critico'),
        'sugestao': sum(r['sugestao'] for r in repos),
    }


# Visão inicial (25/11/2026): cartões das categorias, com nome e ícone, para escolher por onde começar.
ICONES_DAS_CATEGORIAS = {
    'smartphone': ('fa-mobile-screen-button', '#2563EB'), 'games': ('fa-gamepad', '#7C3AED'),
    'audio': ('fa-headphones', '#DB2777'), 'bem estar': ('fa-heart-pulse', '#E11D48'),
    'box e modem e fwt': ('fa-wifi', '#0891B2'), 'casa inteligente': ('fa-house', '#059669'),
    'demais eletronicos': ('fa-plug', '#EA580C'), 'essenciais': ('fa-battery-full', '#16A34A'),
    'notebook e tablets': ('fa-laptop', '#4F46E5'), 'tv': ('fa-tv', '#0F766E'),
    'outros': ('fa-box', '#64748B'), 'sem categoria': ('fa-circle-question', '#94A3B8'),
}


def icone_da_categoria(nome):
    """(ícone, cor) da categoria; aceita plural e variação de grafia ("Smartphones", "Games e Consoles")."""
    alvo = ' '.join(est._sem_acento(nome).split())
    if alvo in ICONES_DAS_CATEGORIAS:
        return ICONES_DAS_CATEGORIAS[alvo]
    for chave, valor in ICONES_DAS_CATEGORIAS.items():
        if alvo.rstrip('s') == chave.rstrip('s') or alvo.startswith(chave.split()[0].rstrip('s')):
            return valor
    return ('fa-tag', '#0EA5E9')


def faixa_de_idade(dias):
    if dias is None:
        return ''
    for chave, _rotulo, de, ate, _cor in IDADES:
        if dias >= de and (ate is None or dias <= ate):
            return chave
    return ''


def _contar_situacoes(linhas):
    contagem = {chave: 0 for chave, *_r in SAUDE}
    for linha in linhas:
        contagem[linha['situacao']] = contagem.get(linha['situacao'], 0) + 1
    return contagem


def _fatias(itens, total):
    """Põe ``pct`` (0–100) em cada item; a soma fecha em 100 quando há total."""
    for item in itens:
        item['pct'] = round(item['qtd'] * 100 / total, 1) if total else 0
    return itens


def _panorama(foto, loja, repos):
    """O que a abertura desenha: estoque por loja, saúde da reposição, tempo parado e os mais urgentes."""
    livre, transito = {}, {}
    for (centro, _m), deps in foto['saldos'].items():
        livre[centro] = livre.get(centro, 0) + deps.get(est.DEPOSITO_VENDA, (0, 0))[0]
    for (centro, _m), qtd in foto['transito'].items():
        transito[centro] = transito.get(centro, 0) + qtd
    lojas = []
    for centro in est.centros_da_foto(foto):
        info = est.POR_CENTRO.get(centro)
        lojas.append({'centro': centro, 'nome': est.nome_do_centro(foto, centro), 'uf': est.uf_do_centro(centro),
                      'cidade': info.cidade if info else '', 'cor': cor_da_loja(centro),
                      'livre': int(livre.get(centro, 0)), 'transito': int(transito.get(centro, 0)),
                      'minha': centro == loja})
    lojas.sort(key=lambda l: (-l['livre'], -l['transito'], l['nome']))
    maior = max((l['livre'] + l['transito'] for l in lojas), default=0) or 1
    for l in lojas:
        l['pct_livre'] = round(l['livre'] * 100 / maior, 1)
        l['pct_transito'] = round(l['transito'] * 100 / maior, 1)

    contagem = _contar_situacoes(repos)
    saude = _fatias([{'chave': chave, 'rotulo': rotulo, 'cor': cor, 'dica': dica, 'qtd': contagem.get(chave, 0)}
                     for chave, rotulo, cor, dica in SAUDE], sum(contagem.values()))

    hoje, por_faixa, sem_data = foto['hoje'], {}, 0
    for (_mat, _num, centro, dep, tipo, _s, _su, quando) in foto['unidades']:
        if dep != est.DEPOSITO_VENDA or tipo != '01' or (loja and centro != loja):
            continue
        faixa = faixa_de_idade(est.dias_desde(quando, hoje))
        if faixa:
            por_faixa[faixa] = por_faixa.get(faixa, 0) + 1
        else:
            sem_data += 1
    idades = _fatias([{'chave': chave, 'rotulo': rotulo, 'cor': cor, 'qtd': por_faixa.get(chave, 0)}
                      for chave, rotulo, _de, _ate, cor in IDADES], sum(por_faixa.values()))
    maior_idade = max((i['qtd'] for i in idades), default=0) or 1
    for i in idades:
        i['barra'] = round(i['qtd'] * 100 / maior_idade, 1)

    urgentes = [r for r in repos if r['situacao'] in ('critico', 'ruptura')][:6]
    for r in urgentes:
        r['cor'] = cor_da_loja(r['centro'])
        if r['buscar_em']:
            r['buscar_em']['cor'] = cor_da_loja(r['buscar_em']['centro'])
    return {'lojas': lojas, 'saude': saude, 'saude_total': sum(contagem.values()), 'idades': idades,
            'idades_total': sum(por_faixa.values()), 'sem_data': sem_data, 'urgentes': urgentes,
            'parados': por_faixa.get('mais90', 0)}


def _cartoes_das_categorias(foto, loja):
    """Um cartão por categoria: ícone, cor, unidades livres na loja escolhida e na rede, e quantos produtos."""
    livre_rede, livre_loja, produtos = {}, {}, {}
    for (centro, material), deps in foto['saldos'].items():
        qtd = deps.get(est.DEPOSITO_VENDA, (0, 0))[0]
        if not qtd:
            continue
        cat = est.produto(foto, material)['categoria']
        livre_rede[cat] = livre_rede.get(cat, 0) + qtd
        produtos.setdefault(cat, set()).add(material)
        if loja and centro == loja:
            livre_loja[cat] = livre_loja.get(cat, 0) + qtd
    cartoes = []
    for nome in est.categorias(foto):
        icone, cor = icone_da_categoria(nome)
        cartoes.append({'nome': nome, 'icone': icone, 'cor': cor, 'rede': int(livre_rede.get(nome, 0)),
                        'loja': int(livre_loja.get(nome, 0)), 'produtos': len(produtos.get(nome, ()))})
    # A barra do cartão: a fatia da categoria no estoque (da loja escolhida, ou da rede).
    chave = 'loja' if loja else 'rede'
    total = sum(c[chave] for c in cartoes) or 1
    for c in cartoes:
        c['pct'] = round(c[chave] * 100 / total, 1)
    # Quem tem mais estoque primeiro; "Sem categoria" sempre no fim.
    cartoes.sort(key=lambda c: (c['nome'] == 'Sem categoria', c['rede'] == 0, -c[chave], est._sem_acento(c['nome'])))
    return cartoes


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
@so_quem_ve_estoque
def estoque(request, lp=False):
    abas = ABAS_LP if lp else ABAS
    aba_do_topo = 'estoque_lp' if lp else 'estoque'
    try:
        foto = est.fotografia(lp=lp)
    except SapIndisponivel as exc:
        logger.warning('Estoque SAP indisponível', exc_info=True)
        return render(request, 'auditoria_sap/estoque.html', {
            'aba': aba_do_topo, 'erro': str(exc), 'abas': abas, 'so_estoque': so_estoque(request.user),
            'lp': lp, 'pode_escolher_gestores': pode_escolher_gestores(request.user)})

    filtros = _filtros(request, foto, abas)
    aba = filtros['aba']
    inicial = aba == 'inicio'
    repos = _reposicao_da_loja(foto, filtros['loja'])
    contexto = {
        'inicial': inicial,
        'aba': aba_do_topo,
        'abas': abas,
        'lp': lp,
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
        'minha_loja': '' if lp else _loja_padrao(request.user),
        'categorias': est.categorias(foto),
        'resumo': _resumo_da_loja(foto, filtros['loja'], repos),
        'sem_vendas': foto['vendas'] is None and not lp,
        'pode_escolher_gestores': pode_escolher_gestores(request.user),
        'so_estoque': so_estoque(request.user),
        'q_base': _query(filtros, aba='', situacao='', deposito='', fabricante='', idade=''),
        'q_atual': _query(filtros),
    }
    if inicial:
        contexto['cartoes'] = _cartoes_das_categorias(foto, filtros['loja'])
        contexto['panorama'] = _panorama(foto, filtros['loja'], repos)
        return _responder(request, contexto)
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
        todas = est.reposicao(foto, filtros['loja'] or None, busca=filtros['q'], categoria=filtros['categoria'])
        contagem = _contar_situacoes(todas)
        linhas = [l for l in todas if not filtros['situacao'] or l['situacao'] == filtros['situacao']]
        for linha in linhas:
            linha['cor'] = cor_da_loja(linha['centro'])
            if linha['buscar_em']:
                linha['buscar_em']['cor'] = cor_da_loja(linha['buscar_em']['centro'])
        contexto.update(pagina=Paginator(linhas, LINHAS_POR_PAGINA).get_page(request.GET.get('pagina')),
                        total_linhas=len(linhas),
                        situacoes=[(chave, rotulo, len(todas) if not chave else contagem.get(chave, 0),
                                    next((c for k, _r, c, _d in SAUDE if k == chave), ''))
                                   for chave, rotulo in SITUACOES_REPOSICAO],
                        sugestao_filtrada=sum(l['sugestao'] for l in linhas))
    elif aba == 'detalhado':
        unidades = _detalhado(foto, filtros)
        if request.GET.get('formato') == 'csv':
            return _csv_detalhado(foto, unidades)
        depositos = sorted({u[3] for u in foto['unidades']} - {''})
        contexto.update(pagina=Paginator(unidades, UNIDADES_POR_PAGINA).get_page(request.GET.get('pagina')),
                        total_linhas=len(unidades), idades=IDADES,
                        depositos=[(d, est.DEPOSITOS.get(d, d)) for d in depositos] + [('TRANSITO', 'Em trânsito')])
    else:
        linhas = est.catalogo(foto, busca=filtros['q'], categoria=filtros['categoria'],
                              fabricante=filtros['fabricante'])
        contexto.update(pagina=Paginator(linhas, LINHAS_POR_PAGINA).get_page(request.GET.get('pagina')),
                        total_linhas=len(linhas), fabricantes=est.fabricantes(foto))
    return _responder(request, contexto)


def _responder(request, contexto):
    """A página inteira, ou só o miolo quando a própria tela pede (trocar aba/filtro sem recarregar)."""
    if request.headers.get('X-Estoque-Parcial'):
        resposta = render(request, 'auditoria_sap/_estoque_conteudo.html', contexto)
        resposta['X-Estoque-Parcial'] = '1'
    else:
        resposta = render(request, 'auditoria_sap/estoque.html', contexto)
    patch_vary_headers(resposta, ('X-Estoque-Parcial',))
    return resposta


def _detalhado(foto, filtros):
    unidades = est.unidades_de(foto, centro=filtros['loja'] or None, modo='todas', serial=filtros['serial'])
    if filtros['deposito'] == 'TRANSITO':
        unidades = [u for u in unidades if u['tipo'] == est.TIPO_TRANSITO]
    elif filtros['deposito']:
        unidades = [u for u in unidades if u['deposito'] == filtros['deposito']]
    if filtros['idade']:
        unidades = [u for u in unidades if faixa_de_idade(u['dias']) == filtros['idade']]
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


def _nome_para_preco(nome):
    """'iPhone 17 256GB (Demo)' → 'iphone 17 256gb': sem acento, caixa, parênteses e espaços repetidos."""
    import re
    texto = est._sem_acento(re.sub(r'\([^)]*\)', ' ', nome or ''))
    return ' '.join(re.sub(r'[^a-z0-9+]+', ' ', texto).split())


def _indice_por_nome():
    """{nome normalizado: preço} da mesma tabela — a reserva quando o material não está nos SKUs."""
    indice = _indice_de_precos()
    if _PRECOS.get('por_nome_de') is not indice:
        por_nome = {}
        for preco in indice.values():
            por_nome.setdefault(_nome_para_preco(preco['nome']), preco)
        _PRECOS.update(por_nome=por_nome, por_nome_de=indice)
    return _PRECOS['por_nome']


def preco_do_material(material, nome=''):
    """Preço pelo SKU (material) na tabela de valores de /vendas/precos/; sem SKU, pelo nome do produto (25/11/2026)."""
    try:
        preco = _indice_de_precos().get((material or '').upper())
        if preco is None and nome:
            achado = _indice_por_nome().get(_nome_para_preco(nome))
            if achado is not None:
                preco = {**achado, 'pelo_nome': True}
        return preco
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
@so_quem_ve_estoque
def estoque_produto(request, lp=False):
    """A gaveta do produto: preço (base, PIX/Vivo Pay, 12x, 21x, por plano), Renova e onde tem."""
    try:
        foto = est.fotografia(lp=lp)
    except SapIndisponivel as exc:
        return JsonResponse({'ok': False, 'erro': str(exc)}, status=503)
    material = (request.GET.get('material') or '').strip().upper()
    minha = (request.GET.get('minha') or '').strip().upper()
    if not material:
        return JsonResponse({'ok': False, 'erro': 'Produto inválido.'}, status=400)
    p = est.produto(foto, material)
    preco = preco_do_material(material, p['nome'])
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
@so_quem_ve_estoque
def estoque_sugestoes(request, lp=False):
    """O campo Produto se autopreenche (25/11/2026): até 12 produtos que batem com o que foi digitado.

    Um por nome (as cores do mesmo aparelho viram uma sugestão só), quem tem mais estoque primeiro."""
    termo = (request.GET.get('q') or '').strip()[:80]
    if len(termo) < 2:
        return JsonResponse({'ok': True, 'sugestoes': []})
    try:
        foto = est.fotografia(lp=lp)
    except SapIndisponivel:
        return JsonResponse({'ok': True, 'sugestoes': []})
    categoria = (request.GET.get('categoria') or '').strip()
    vistos, saida = {}, []
    for linha in est.catalogo(foto, busca=termo, categoria=categoria):
        p = linha['produto']
        chave = est._sem_acento(p['nome'])
        if chave in vistos:
            item = vistos[chave]
            item['livre'] += int(linha['livre'])
            if p['cor'] and p['cor'] not in item['cores']:
                item['cores'].append(p['cor'])
            continue
        if len(saida) >= 12:
            continue
        item = {'nome': p['nome'], 'categoria': p['categoria'], 'fabricante': p['fabricante'],
                'cores': [p['cor']] if p['cor'] else [], 'livre': int(linha['livre'])}
        vistos[chave] = item
        saida.append(item)
    return JsonResponse({'ok': True, 'sugestoes': saida})


@login_required
@so_quem_ve_estoque
def estoque_unidades(request, lp=False):
    """A gaveta: as unidades de uma loja e um material (disponível ou em trânsito)."""
    try:
        foto = est.fotografia(lp=lp)
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
        'preco': preco_do_material(material, p['nome']),
        'renova': _renova_para_json(p.get('renova'), preco_do_material(material, p['nome'])),
        'quantidade': int(qtd),
        'sem_serial': max(0, int(qtd) - len(unidades)),
        'unidades': [{
            'serial': u['serial'], 'deposito': u['deposito'], 'deposito_nome': u['deposito_nome'],
            'status': ' '.join(x for x in (u['status_sistema'], u['status_usuario']) if x),
            'desde': u['desde'].strftime('%d/%m/%Y') if u['desde'] else '', 'dias': u['dias'],
        } for u in unidades],
    })


# ── Estoque LP (26/11/2026): a mesma tela e as mesmas gavetas, com a fotografia só do LP ──
estoque_lp = gzip_page(login_required(so_superadmin_lp(estoque.__wrapped__.__wrapped__.__wrapped__)))
estoque_lp_produto = login_required(so_superadmin_lp(estoque_produto.__wrapped__.__wrapped__))
estoque_lp_sugestoes = login_required(so_superadmin_lp(estoque_sugestoes.__wrapped__.__wrapped__))
estoque_lp_unidades = login_required(so_superadmin_lp(estoque_unidades.__wrapped__.__wrapped__))
