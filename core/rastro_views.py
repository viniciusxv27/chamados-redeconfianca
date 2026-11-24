"""Rastro completo (/activity-history/rastro/): tudo o que cada pessoa fez num dia ou período,
com exportação em Excel/CSV por dia e por pessoas escolhidas. Regras em ``core/rastro.py``."""
import csv
import hashlib

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import caches
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render

from . import historico, rastro
from .historico_views import _negar

User = get_user_model()
POR_PAGINA = 300
CACHE_SEGUNDOS = 300


def _parametros(request):
    de, ate = rastro.periodo(request.GET)
    ids = rastro.ids_de_usuarios(request.GET)
    escolhidas = [f for f in request.GET.getlist('fonte') if f in dict(rastro.FONTES)]
    fontes = escolhidas or [c for c, _r in rastro.FONTES]
    sem_auto = request.GET.get('auto') == '0'
    busca = (request.GET.get('q') or '').strip()
    return de, ate, ids, fontes, sem_auto, busca


def _eventos(de, ate, ids, fontes):
    """A varredura leva alguns segundos: guarda o resultado por 5 min (memória do processo)."""
    chave = 'rastro:' + hashlib.sha1(f'{de}|{ate}|{ids}|{sorted(fontes)}'.encode()).hexdigest()
    cache = caches['local'] if 'local' in settings.CACHES else caches['default']
    guardado = cache.get(chave)
    if guardado is None:
        eventos, _usuarios, avisos = rastro.montar(de, ate, ids, fontes)
        guardado = (eventos, avisos)
        cache.set(chave, guardado, CACHE_SEGUNDOS)
    return guardado


def _filtrar(eventos, sem_auto, busca):
    if sem_auto:
        eventos = [e for e in eventos if not e['automatico']]
    if busca:
        b = busca.lower()
        eventos = [e for e in eventos if b in e['pessoa'].lower() or b in e['o_que'].lower()
                   or b in e['detalhe'].lower() or b in e['modulo'].lower()]
    return eventos


@login_required
def rastro_atividades(request):
    if not historico.pode_ver_historico(request.user):
        return _negar(request)
    de, ate, ids, fontes, sem_auto, busca = _parametros(request)
    eventos, avisos = _eventos(de, ate, ids, fontes)
    eventos = _filtrar(eventos, sem_auto, busca)
    resumo = rastro.resumo_por_pessoa(eventos, None)
    pagina = Paginator(eventos, POR_PAGINA).get_page(request.GET.get('page'))

    parametros = request.GET.copy()
    parametros.pop('page', None)
    return render(request, 'core/activity_history_rastro.html', {
        'pagina': pagina, 'resumo': resumo, 'avisos': avisos, 'total': len(eventos),
        'de': de, 'ate': ate, 'mesmo_dia': de == ate, 'ids': ids, 'fontes': fontes, 'sem_auto': sem_auto,
        'busca': busca, 'todas_fontes': rastro.FONTES, 'max_dias': rastro.MAX_DIAS,
        'pessoas': User.objects.order_by('-is_active', 'first_name', 'last_name').only(
            'id', 'first_name', 'last_name', 'email', 'is_active'),
        'qs': parametros.urlencode(),
        'contagem_fontes': {c: sum(1 for e in eventos if e['fonte'] == c) for c, _r in rastro.FONTES},
    })


@login_required
def rastro_exportar(request):
    if not historico.pode_ver_historico(request.user):
        return _negar(request)
    de, ate, ids, fontes, sem_auto, busca = _parametros(request)
    eventos, avisos = _eventos(de, ate, ids, fontes)
    eventos = _filtrar(eventos, sem_auto, busca)
    nome = f'rastro_{de:%Y%m%d}' + ('' if de == ate else f'_a_{ate:%Y%m%d}') + (f'_{len(ids)}pessoas' if ids else '')

    if request.GET.get('formato') == 'csv':
        resposta = HttpResponse(content_type='text/csv; charset=utf-8')
        resposta['Content-Disposition'] = f'attachment; filename="{nome}.csv"'
        resposta.write('﻿')  # Excel reconhece UTF-8
        escritor = csv.writer(resposta, delimiter=';')
        escritor.writerow(rastro.COLUNAS_EXPORTACAO)
        escritor.writerows(rastro.linhas_para_exportar(eventos))
        return resposta

    conteudo = rastro.exportar_xlsx(eventos, rastro.resumo_por_pessoa(eventos, None), de, ate, avisos)
    resposta = HttpResponse(conteudo, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resposta['Content-Disposition'] = f'attachment; filename="{nome}.xlsx"'
    return resposta
