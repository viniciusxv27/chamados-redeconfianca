"""Módulo Comissão no app — o /users/commission/ no celular.

Tudo vem das mesmas funções do portal (``users/commission_views.py``): a planilha
do OneDrive com o cache local + versão no Redis (``download_excel_file``), as
metas por pilar, o IQ e o ``process_*_commission_data``. Aqui só se escolhe,
como cada visão da web escolhe, qual aba e qual nome procurar — e se monta o
JSON enxuto que o app desenha.

Regras (de onde veio cada uma):

- entrar: ``pode_ver_comissionamento`` (users/commission_views.py:3074), a porta
  da view web e o gate ``comissionamento`` de users/module_access.py:GATES;
- sem versão liberada para o perfil: a web manda para a home
  (commission_views.py:3113); aqui volta ``liberado: false`` sem número nenhum;
- referência (mês/ano/fase): ``resolve_commission_reference_from_request`` —
  quem não está no público da versão cai na liberada mais recente;
- ver outra pessoa: o mesmo recorte de cada visão web (ver ``_pode_ver_alvo``);
- histórico: ``commission_historico`` (linhas, ``_escopo`` e ``_montar``);
- médias da projeção: só quem passa no ``_gate_projecao`` (module_access.py:267),
  que é o que mostra "Médias de Comissão" no menu.
"""
import logging
from types import SimpleNamespace

from django.db.models import Q
from django.http import Http404
from django.urls import path
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from users import commission_views as cv
from users.models import User

from ..autenticacao import TokenMobileAuthentication
from . import permissao

logger = logging.getLogger(__name__)


def pode_entrar(user):
    # users/commission_views.py:3074 — a mesma função que a view web chama.
    return cv.pode_ver_comissionamento(user)


MODULO = {'rotulo': 'Comissão', 'descricao': 'Sua comissão do mês, pilar a pilar, e o histórico',
          'icone': 'cash-outline', 'cor': '#16A34A', 'pode_entrar': pode_entrar}

PAPEIS = {
    'superadmin': 'Superadmin', 'coordenador': 'Coordenador', 'gerente': 'Gerente',
    'recepcionista': 'Recepcionista', 'cn': 'Consultor', 'aparte': 'A parte',
}
FASES = {'antes': 'Antes da contestação', 'pos': 'Pós contestação'}

# Pilar da planilha → chaves dos dicionários de valores do process_commission_data.
# Eletrônicos e Essenciais vêm em A/B e a web soma as duas metades (commission.html).
_VALORES_DO_PILAR = [
    ('movel', ('movel',), 'movel'),
    ('fixa', ('fixa',), 'fixa'),
    ('smartphone', ('smartphone',), 'smartphone'),
    ('eletronicos', ('eletronicos_a', 'eletronicos_b'), 'eletronicos'),
    ('essenciais', ('essenciais_a', 'essenciais_b'), 'essenciais'),
    ('seguro', ('seguro',), 'seguro'),
    ('sva', ('sva',), 'sva'),
]


# ── Papel e referência ───────────────────────────────────────────────────────

def papel_de(user):
    """O papel na ordem do commission_view (commission_views.py:3106-3129):
    superadmin → "A parte" → coordenador → gerente → recepcionista → CN."""
    papel = cv.get_user_role(user)
    if papel != 'superadmin' and cv.is_user_aparte(user):
        return 'aparte'
    return papel


def _referencia(user, params):
    """Mês/ano/fase pedidos, filtrados pelo que a pessoa pode ver (a função da web)."""
    get = {}
    for chave_app, chave_web in (('ano', 'year'), ('mes', 'month'), ('fase', 'phase')):
        valor = (params.get(chave_app) or '').strip()
        if valor:
            get[chave_web] = valor
    return cv.resolve_commission_reference_from_request(SimpleNamespace(user=user, GET=get))


def _opcoes_de_referencia(user, ref):
    from users.commission_liberacao import versoes_liberadas
    opcoes, vistos = [], set()
    for v in versoes_liberadas(user):
        fase = v.contestacao_phase or 'pos'
        if (v.year, v.month, fase) in vistos:
            continue
        vistos.add((v.year, v.month, fase))
        opcoes.append({'ano': v.year, 'mes': v.month, 'fase': fase,
                       'rotulo': f"{cv.MONTH_NAMES_PT.get(v.month, v.month)}/{v.year}",
                       'fase_rotulo': FASES.get(fase, fase)})
    if not opcoes:   # nenhuma versão cadastrada: a web mostra só a referência padrão
        opcoes.append({'ano': ref['year'], 'mes': ref['month'], 'fase': ref['phase'],
                       'rotulo': f"{cv.MONTH_NAMES_PT.get(ref['month'], ref['month'])}/{ref['year']}",
                       'fase_rotulo': FASES.get(ref['phase'], ref['phase'])})
    return opcoes


# ── Quem cada um pode abrir (o seletor de pessoa da web) ─────────────────────

def _item(u, papel_rotulo=''):
    return {'id': u.id, 'nome': u.get_full_name() or u.email,
            'setor': u.sector.name if u.sector_id else None, 'papel': papel_rotulo}


def _pessoas_do_superadmin():
    """O diretório do superadmin (commission_views.py:3253-3283)."""
    gerentes = cv.get_group_members_by_name('GERENTES')
    coordenadores = cv.get_group_members_by_name('COORDENADORES')
    recepcionistas = cv.get_group_members_by_name('RECEPCIONISTAS')
    aparte = cv.get_aparte_commission_users()
    excluidos = set(gerentes.values_list('id', flat=True))
    excluidos.update(coordenadores.values_list('id', flat=True))
    excluidos.update(recepcionistas.values_list('id', flat=True))
    excluidos.update(u.id for u in aparte)
    cns = (User.objects.filter(hierarchy='PADRAO', is_active=True)
           .exclude(id__in=excluidos).select_related('sector'))
    catalogo = [('Coordenador', coordenadores.select_related('sector')),
                ('Gerente', gerentes.select_related('sector')),
                ('Consultor', cns), ('Recepcionista', recepcionistas.select_related('sector')),
                ('A parte', aparte)]
    pessoas = [_item(u, rotulo) for rotulo, membros in catalogo for u in membros]
    return sorted(pessoas, key=lambda p: p['nome'].lower())


def _pessoas_do_coordenador(user, urls, ref):
    """As lojas e os PADRÃO dos setores do coordenador (commission_views.py:3633-3780).

    A web lista estas pessoas e abre qualquer id pela URL; aqui o id precisa
    estar na lista — o que o coordenador vê na tela é o recorte dele.
    """
    pessoas = {}
    primeiro = user.first_name.strip().upper() if user.first_name else ''
    lojas = cv.fetch_lojas_por_coordenador(primeiro, excel_url=urls['comissao'],
                                           cache_suffix=ref['cache_suffix'])
    pdvs = [loja['pdv'].upper() for loja in lojas]
    todos = cv.fetch_all_users_from_sheet(cv.SHEET_GERENTE, excel_url=urls['comissao'],
                                          cache_suffix=ref['cache_suffix'])

    def primeiro_nome(nome):
        texto = str(nome or '').strip().upper()
        return texto.split()[0] if texto and texto != 'NAN' else ''

    for linha in (todos.get('users') or []) if todos.get('success') else []:
        dados = linha['data']
        pdv = str(dados.get('PDV', '')).strip().upper()
        coord = primeiro_nome(dados.get('COORDENADOR (A)', ''))
        if not ((primeiro and coord and coord == primeiro) or pdv in pdvs):
            continue
        nome = str(dados.get('NOME') or '').strip()
        partes = nome.split()
        gerente = None
        if len(partes) >= 2:
            gerente = User.objects.filter(first_name__iexact=partes[0], last_name__icontains=partes[-1],
                                          is_active=True).first()
        if not gerente and nome:
            gerente = User.objects.filter(first_name__iexact=partes[0] if partes else nome,
                                          is_active=True).first()
        if gerente:
            pessoas.setdefault(gerente.id, _item(gerente, 'Gerente'))
            if gerente.sector_id:
                cns = (User.objects.filter(sector=gerente.sector, is_active=True)
                       .exclude(groups__name__in=['Gerente', 'Gerentes', 'Coordenador', 'Coordenadores'])
                       .select_related('sector'))
                for u in cns:
                    pessoas.setdefault(u.id, _item(u, 'Consultor'))

    setores = set()
    if user.sector_id:
        setores.add(user.sector_id)
    setores.update(user.sectors.values_list('id', flat=True))
    if setores:
        membros = (User.objects.filter(hierarchy='PADRAO', is_active=True)
                   .filter(Q(sector_id__in=setores) | Q(sectors__in=setores)).distinct()
                   .select_related('sector'))
        for u in membros:
            pessoas.setdefault(u.id, _item(u, 'Consultor'))
    pessoas.pop(user.id, None)
    return sorted(pessoas.values(), key=lambda p: p['nome'].lower())


def pessoas_visiveis(user, papel, urls, ref):
    if papel == 'superadmin':
        return _pessoas_do_superadmin()
    if papel == 'coordenador':
        return _pessoas_do_coordenador(user, urls, ref)
    if papel == 'gerente':
        # commission_views.py:3521 — get_all_cns_for_gerente (PADRÃO da loja).
        return [_item(u, 'Consultor') for u in cv.get_all_cns_for_gerente(user).select_related('sector')]
    return []


def _pode_ver_alvo(user, papel, alvo, urls, ref):
    """O id pedido está no recorte de quem pede?

    - superadmin: qualquer ativo (commission_views.py:3163);
    - gerente: mesma loja (commission_views.py:3527, ``viewing_user.sector != user.sector``);
    - coordenador: quem a tela dele lista (``_pessoas_do_coordenador``);
    - demais: só o próprio.
    """
    if alvo.id == user.id:
        return True
    if papel == 'superadmin':
        return True
    if papel == 'gerente':
        return bool(user.sector_id) and alvo.sector_id == user.sector_id
    if papel == 'coordenador':
        return any(p['id'] == alvo.id for p in _pessoas_do_coordenador(user, urls, ref))
    return False


# ── O cálculo (as mesmas chamadas de cada visão web) ─────────────────────────

def _nome(u):
    return u.get_full_name() or u.first_name


def _setor(u):
    return u.sector.name if getattr(u, 'sector', None) else None


def _planilha(user, papel, alvo, urls, ref):
    """Devolve ``(tipo, result, processed)`` como a visão web daquele caso faz."""
    suf = ref['cache_suffix']
    proprio = alvo.id == user.id

    if cv.is_user_aparte(alvo) and (proprio or papel == 'superadmin'):
        # commission_views.py:3014 — build_aparte_commission_data (usuário vê o seu; superadmin, qualquer um).
        dados = cv.build_aparte_commission_data(alvo, ref['year'], ref['month'], ref['phase'])
        return 'aparte', {'success': True}, dados

    if proprio and papel == 'coordenador':
        # commission_views.py:3698-3712 e 3800-3812: aba de coordenador pelo PRIMEIRO NOME.
        primeiro = user.first_name.strip().upper() if user.first_name else ''
        result = cv.fetch_excel_data(cv.SHEET_COORDENADOR, primeiro, excel_url=urls['comissao'], cache_suffix=suf)
        if not result.get('success'):
            return 'coordenador', result, None
        metas = cv.fetch_metas_por_pilar_coordenador(_nome(user), urls=urls, cache_suffix=suf)
        return 'coordenador', result, cv.process_coordenador_commission_data(result['data'], metas)

    if proprio and papel == 'recepcionista':
        # commission_views.py:3339-3410.
        nome = _nome(user)
        result = cv.fetch_excel_data(cv.SHEET_RECEPCIONISTA, nome, excel_url=urls['comissao'], cache_suffix=suf)
        if not result.get('success'):
            return 'recepcionista', result, None
        metas = cv.fetch_metas_por_pilar(nome, False, _setor(user), urls=urls, cache_suffix=suf)
        iq = cv.fetch_iq_data(nome, excel_url=urls['comissao'], cache_suffix=suf)
        processed = cv.process_commission_data(result['data'], False, metas, iq)
        processed['info']['cargo'] = 'Recepcionista'
        processed['info']['pdv'] = _setor(user)
        processed['info']['coordenador'] = result['data'].get('GERENTE PDV')
        processed['comissoes']['total'] = cv.safe_float(result['data'].get('COMISSÃO') or result['data'].get('COMISSAO'))
        processed['totais']['premiacao_cargo'] = cv.safe_float(
            result['data'].get('PREMIAÇÃO') or result['data'].get('PREMIACAO'))
        return 'recepcionista', result, processed

    # Daqui para baixo: a própria planilha de CN/gerente, ou a de outra pessoa.
    alvo_gerente = cv.is_user_gerente(alvo)
    alvo_coord = alvo_recep = False
    if proprio and papel == 'gerente':
        alvo_gerente = True                                 # commission_views.py:3540
    elif not proprio and papel == 'superadmin':
        # commission_views.py:3168-3183: o superadmin acha a aba pelo papel do alvo.
        alvo_coord = not alvo_gerente and cv.is_user_coordenador(alvo)
        alvo_recep = not alvo_gerente and not alvo_coord and cv.is_user_recepcionista(alvo)

    if alvo_gerente:
        aba, busca = cv.SHEET_GERENTE, _nome(alvo)
    elif alvo_coord:
        aba, busca = cv.SHEET_COORDENADOR, alvo.first_name or alvo.get_full_name() or ''
    elif alvo_recep:
        aba, busca = cv.SHEET_RECEPCIONISTA, _nome(alvo)
    else:
        aba, busca = cv.SHEET_CN, _nome(alvo)
    tipo = 'gerente' if alvo_gerente else ('coordenador' if alvo_coord else 'cn')

    result = cv.fetch_excel_data(aba, busca, excel_url=urls['comissao'], cache_suffix=suf)
    if not result.get('success'):
        return tipo, result, None
    if alvo_coord:
        metas = cv.fetch_metas_por_pilar_coordenador(busca, urls=urls, cache_suffix=suf)
        return tipo, result, cv.process_coordenador_commission_data(result['data'], metas)
    metas = cv.fetch_metas_por_pilar(busca, alvo_gerente, _setor(alvo), urls=urls, cache_suffix=suf)
    iq = cv.fetch_iq_data(busca, excel_url=urls['comissao'], cache_suffix=suf)
    return tipo, result, cv.process_commission_data(result['data'], alvo_gerente, metas, iq)


def _f(valor):
    try:
        return round(float(valor or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _serializar_planilha(processed, meses, papel_tela):
    com = processed.get('comissoes') or {}
    bonus = processed.get('bonus') or {}
    alto = processed.get('alto_desempenho') or {}
    hunter = processed.get('hunter') or {}
    totais = processed.get('totais') or {}
    info = processed.get('info') or {}
    pilares = []
    for i, p in enumerate(processed.get('pilares') or []):
        chave, partes, simples = _VALORES_DO_PILAR[i] if i < len(_VALORES_DO_PILAR) else (str(i), (), '')
        comissao = sum(_f(com.get(k)) for k in partes)
        individual = sum(_f(alto.get(k)) for k in partes)
        coletiva = _f(bonus.get(simples))
        h = _f(hunter.get(simples))
        pilares.append({
            'chave': chave,
            'nome': p.get('nome'),
            'icone': p.get('icon'),
            'cor': p.get('color'),
            'atingimentos': [
                {'mes': meses.get('mes_pct_3'), 'pct': _f(p.get('pct_3'))},
                {'mes': meses.get('mes_pct_2'), 'pct': _f(p.get('pct_2'))},
                {'mes': meses.get('mes_pct_1'), 'pct': _f(p.get('pct_1'))},
            ],
            'media': _f(p.get('media')),
            'carteira': _f(p.get('carteira')) if 'carteira' in p else None,
            'carteira_sem_dado': bool(p.get('carteira_sem_dado')),
            'hunter_id': str(p.get('hunter') or p.get('habilitado') or '') or None,
            'meta_total': _f(p.get('total')), 'pago': _f(p.get('pago')), 'exclusao': _f(p.get('exclusao')),
            'comissao': comissao, 'premiacao_individual': individual, 'premiacao_coletiva': coletiva,
            'hunter': h,
            'iq_pct': _f(p.get('iq_pct')) if p.get('iq_pct') is not None else None,
            'total': _f(p.get('soma_com_iq', comissao + individual + coletiva + h)),
        })
    acel = processed.get('aceleradores') or {}
    desc = processed.get('descontos') or {}
    return {
        'tipo': 'planilha',
        'nome': info.get('nome') or '',
        'cargo': info.get('cargo') or '',
        'pdv': info.get('pdv') or '',
        'coordenador': info.get('coordenador') or '',
        'coordenador_rotulo': 'Gerente' if papel_tela == 'recepcionista' else 'Coordenador(a)',
        'total': _f(totais.get('remuneracao_final')),
        'comissao_total': _f(com.get('total')),
        'premiacao_total': _f(totais.get('premiacao_cargo')),
        'total_pilares': _f(totais.get('total_pilares_com_iq')),
        'aceleradores': {'seis_pilares': _f(acel.get('seis_pilares')), 'campanha': _f(acel.get('campanha'))},
        'descontos': {'advertencia': _f(desc.get('advertencia')), 'excedente': _f(desc.get('excedente')),
                      'price': _f(desc.get('price')), 'total': _f(desc.get('total'))},
        'composicao': [{'rotulo': r, 'valor': _f(v)} for r, v in zip(
            ((processed.get('charts') or {}).get('composicao') or {}).get('labels') or [],
            ((processed.get('charts') or {}).get('composicao') or {}).get('data') or [])],
        'pilares': pilares,
    }


def _serializar_aparte(dados):
    calc = dados.get('calc') or {}
    return {
        'tipo': 'aparte',
        'total': _f(calc.get('total')),
        'salario_base': _f(dados.get('base_salary')),
        'tem_configuracao': dados.get('config') is not None,
        'pilares': [{
            'chave': r.get('key'), 'nome': r.get('label'),
            'meta': _f(r.get('meta')), 'realizado': _f(r.get('proj')),
            'quantidade': r.get('key') == 'fixa',
            'atingimento': round(float(r.get('attainment') or 0) * 100, 2),
            'fator': round(float(r.get('commission_rate') or 0) * 100, 2),
            'comissao': _f(r.get('commission_value')),
        } for r in calc.get('rows') or []],
    }


def calcular_comissao(user, papel, alvo, urls, ref):
    """O que a tela web mostraria — dict pronto para o app, ou ``(None, erro)``."""
    tipo, result, processed = _planilha(user, papel, alvo, urls, ref)
    if processed is None:
        return None, result.get('error') or 'Seus dados não foram encontrados na planilha deste mês.'
    if tipo == 'aparte':
        return _serializar_aparte(processed), None
    meses = cv.get_month_names(ref['year'], ref['month'])
    return _serializar_planilha(processed, meses, tipo), None


# ── Endpoints ────────────────────────────────────────────────────────────────

def _alvo_pedido(request, user, papel, urls, ref):
    bruto = (request.query_params.get('usuario') or '').strip()
    if not bruto:
        return None
    try:
        alvo = User.objects.select_related('sector').get(pk=int(bruto), is_active=True)
    except (ValueError, User.DoesNotExist):
        raise Http404('Pessoa não encontrada.')
    if not _pode_ver_alvo(user, papel, alvo, urls, ref):
        raise Http404('Pessoa não encontrada.')
    return alvo


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('comissao')])
def resumo(request):
    from users.commission_liberacao import sem_versao_liberada
    from users.module_access import _gate_projecao

    user = request.user
    papel = papel_de(user)
    base = {
        'papel': papel, 'papel_rotulo': PAPEIS.get(papel, papel),
        'pode_ver_outros': papel in ('superadmin', 'coordenador', 'gerente'),
        'pode_projecao': bool(_gate_projecao(user)),
    }
    if sem_versao_liberada(user):
        # commission_views.py:3113 — a web volta para a home com este aviso.
        return Response({**base, 'liberado': False, 'referencia': None, 'opcoes': [], 'alvo': None,
                         'vendo_outro': False, 'precisa_escolher': False, 'dados': None, 'erro': None,
                         'aviso': 'Ainda não há versão do comissionamento liberada para o seu perfil. '
                                  'Assim que for liberada, ela aparece aqui.'})

    ref = _referencia(user, request.query_params)
    urls = cv.get_excel_urls(ref['year'], ref['month'], ref.get('phase'))
    alvo = _alvo_pedido(request, user, papel, urls, ref)
    referencia = {'ano': ref['year'], 'mes': ref['month'], 'fase': ref['phase'],
                  'rotulo': f"{cv.MONTH_NAMES_PT.get(ref['month'], ref['month'])}/{ref['year']}",
                  'fase_rotulo': FASES.get(ref['phase'], ref['phase']),
                  'selecao_invalida': bool(ref['invalid_selection'])}
    saida = {**base, 'liberado': True, 'aviso': None, 'referencia': referencia,
             'opcoes': _opcoes_de_referencia(user, ref), 'alvo': None, 'vendo_outro': False,
             'precisa_escolher': False, 'dados': None, 'erro': None}

    if alvo is None and papel == 'superadmin':
        # A web do superadmin abre no diretório, não numa planilha própria.
        saida['precisa_escolher'] = True
        return Response(saida)
    alvo = alvo or user
    saida['alvo'] = _item(alvo, '')
    saida['vendo_outro'] = alvo.id != user.id
    try:
        dados, erro = calcular_comissao(user, papel, alvo, urls, ref)
    except Exception:                                   # noqa: BLE001 — planilha fora do ar
        logger.exception('Comissão (app): falha ao calcular para %s (alvo %s)', user.pk, alvo.pk)
        dados, erro = None, 'Não foi possível ler a planilha agora. Tente de novo em instantes.'
    saida['dados'], saida['erro'] = dados, erro
    return Response(saida)


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('comissao')])
def pessoas(request):
    """Quem a pessoa pode abrir no seletor (vazio para CN, recepcionista e "A parte")."""
    from users.commission_liberacao import sem_versao_liberada

    user = request.user
    papel = papel_de(user)
    if sem_versao_liberada(user) or papel not in ('superadmin', 'coordenador', 'gerente'):
        return Response({'resultados': []})
    ref = _referencia(user, request.query_params)
    urls = cv.get_excel_urls(ref['year'], ref['month'], ref.get('phase'))
    return Response({'resultados': pessoas_visiveis(user, papel, urls, ref)})


def _historico_visivel(user, papel, linhas):
    """Tira as linhas de versões que a pessoa não pode ver.

    A tela web do histórico (commission_historico.py) não confere o público da
    versão; a tela do mês confere (``versoes_liberadas``). No app vale a regra
    do mês: prévia restrita a gerentes não aparece para o consultor.
    """
    if papel == 'superadmin':
        return linhas
    from users.commission_liberacao import versoes_liberadas
    visiveis = {(v.year, v.month, v.contestacao_phase or 'pos') for v in versoes_liberadas(user)}
    return [lin for lin in linhas if (lin['ano'], lin['mes'], 'antes' if lin['previa'] else 'pos') in visiveis]


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('comissao')])
def historico(request):
    """Meses anteriores: o que cada mês pagou (commission_historico.commission_history_view)."""
    from users import commission_historico as ch

    user = request.user
    papel = cv.get_user_role(user)          # o histórico web usa o papel puro (sem "A parte")
    linhas = ch._escopo(user, papel, ch.linhas_do_historico(ver_rascunho=papel == 'superadmin'))
    linhas = _historico_visivel(user, papel, linhas)
    escopo = request.query_params.get('escopo') or 'eu'
    tem_equipe = papel in ('superadmin', 'coordenador', 'gerente')
    if escopo != 'equipe' or not tem_equipe:
        escopo = 'eu'
        linhas = ch._escopo(user, 'cn', linhas)         # "só a própria linha", a regra do CN
    dados = ch._montar(linhas)

    def total(t):
        return {k: _f(t.get(k)) for k, _r in ch.PARTES} | {'recebido': _f(t.get('recebido'))}

    meses = [{
        'chave': m['chave'], 'rotulo': m['rotulo'], 'total': total(m['total']),
        'previa': m['previa'], 'rascunho': m['rascunho'], 'pct': m['pct'],
        'pessoas': len({lin['pessoa'] for lin in m['linhas']}),
        'linhas': [{'pessoa': lin['pessoa'], 'cargo': ch.CARGOS.get(lin['cargo'], lin['cargo']),
                    'loja': lin['loja_rotulo'], 'fase': lin['fase'], 'recebido': _f(lin['recebido']),
                    'partes': {k: _f(lin[k]) for k, _r in ch.PARTES}}
                   for lin in m['linhas']] if escopo == 'eu' else [],
    } for m in reversed(dados['meses'])]
    grade = [{'pessoa': g['pessoa'], 'cargo': ch.CARGOS.get(g['cargo'], g['cargo']), 'loja': g['loja'],
              'total': _f(g['total']), 'meses_com_valor': g['meses_com_valor'],
              'media': _f(g['total'] / g['meses_com_valor']) if g['meses_com_valor'] else 0.0}
             for g in dados['grade']] if escopo == 'equipe' else []
    melhor = dados['melhor_mes']
    return Response({
        'escopo': escopo, 'tem_equipe': tem_equipe,
        'partes': [{'chave': k, 'rotulo': r} for k, r in ch.PARTES],
        'total': total(dados['total']), 'media_mes': _f(dados['media_mes']),
        'melhor_mes': {'rotulo': melhor['rotulo'], 'recebido': _f(melhor['total']['recebido'])} if melhor else None,
        'n_pessoas': dados['n_pessoas'],
        'meses': meses, 'pessoas': grade,
    })


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('comissao')])
def projecao(request):
    """Médias de comissão pela projeção do simulador (users/commission_projection_views.py).

    O dataset da rede é o do portal (cache do dia, recálculo em segundo plano):
    esta chamada nunca roda o motor para a rede inteira.
    """
    from simulator.averages import ROLE_LABELS, ROLE_ORDER, get_projection_dataset, summarize
    from users.commission_projection_views import _scope_rows
    from users.module_access import _gate_projecao

    user = request.user
    if not _gate_projecao(user):
        raise PermissionDenied('As médias de comissão não estão liberadas para você.')
    papel = cv.get_user_role(user)
    dataset = get_projection_dataset()
    linhas, referencia = _scope_rows(user, papel, dataset['rows'])
    gerado = dataset['generated_at']

    def resumo_(s):
        return {'quantos': s['count'], 'total': _f(s['total']), 'media': _f(s['average']),
                'maior': _f(s['max']), 'menor': _f(s['min'])}

    contagem = {}
    for lin in linhas:
        contagem[lin['role_key']] = contagem.get(lin['role_key'], 0) + 1
    return Response({
        'status': 'calculando' if dataset['status'] == 'building' else 'pronto',
        'desatualizado': bool(dataset['is_stale']),
        'gerado_em': gerado.isoformat() if gerado else None,
        'referencia': (f"{cv.MONTH_NAMES_PT.get(gerado.month, gerado.month)}/{gerado.year}" if gerado else ''),
        'resumo': resumo_(summarize(linhas)),
        'referencia_loja': resumo_(summarize(referencia)) if referencia else None,
        'papeis': [{'chave': k, 'rotulo': ROLE_LABELS[k], 'quantos': contagem[k]}
                   for k in ROLE_ORDER if contagem.get(k)],
        'linhas': [{'id': lin['id'], 'nome': lin['name'], 'papel': lin['role_key'], 'papel_rotulo': lin['role_label'],
                    'setor': lin['sector'] or None, 'coordenador': lin['coordinator'] or None,
                    'ganho': _f(lin['gain']), 'tem_dado': bool(lin['has_data'])}
                   for lin in sorted(linhas, key=lambda x: -(x['gain'] or 0))],
    })


urlpatterns = [
    path('', resumo, name='comissao_resumo'),
    path('pessoas/', pessoas, name='comissao_pessoas'),
    path('historico/', historico, name='comissao_historico'),
    path('projecao/', projecao, name='comissao_projecao'),
]
