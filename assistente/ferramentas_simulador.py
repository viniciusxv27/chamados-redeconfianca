"""Simulador de comissionamento (/simulator/) no assistente — leitura, com as regras da tela.

Quem a pessoa pode consultar é exatamente a lista do seletor de
``simulator.views.simulator_dashboard``: SUPERADMIN escolhe qualquer um (A parte,
coordenador, sniper, gerente, consultor); o coordenador, a si e quem está nas
lojas da carteira dele; o gerente, quem é da loja dele; o "A parte", só a si; o
consultor, a si e os consultores da loja dele. O cálculo é o mesmo motor da tela
(``compute_*_simulation`` dentro de ``realized_prefetch``), uma pessoa por
chamada — o MySQL de vendas é remoto e lento, então nada aqui varre a rede.

A tabela da rede (/simulator/tabela/) só é lida do cache que a própria tela
monta em segundo plano; o assistente não dispara esse recálculo (167 pessoas ×
2 cálculos). O recorte da tabela é o mesmo da tela (``_scope_rows``).
Fatores, lojas do coordenador e snipers (telas de admin) continuam só na tela.
"""
import logging

from django.contrib.auth import get_user_model
from django.utils import timezone

from .comum import Invalido, _bool, _int, _limite, _nome, _normal, _obj, _txt, _url

logger = logging.getLogger(__name__)
User = get_user_model()

ROTULO_PAPEL = {'consultor': 'Consultor', 'gerente': 'Gerente', 'coordenador': 'Coordenador',
                'aparte': 'A parte', 'superadmin': 'SUPERADMIN'}


def _moeda(v):
    try:
        texto = f'{float(v or 0):,.2f}'
    except (TypeError, ValueError):
        return '—'
    return 'R$ ' + texto.replace(',', '_').replace('.', ',').replace('_', '.')


def _num(v):
    try:
        return f'{float(v or 0):,.0f}'.replace(',', '.')
    except (TypeError, ValueError):
        return '—'


def _pct(v):
    try:
        return f'{float(v or 0) * 100:.1f}%'.replace('.', ',')
    except (TypeError, ValueError):
        return '—'


def _rotulo_pessoa(u, papel):
    return f'{u.get_full_name() or u.email} (id {u.pk}, {ROTULO_PAPEL.get(papel, papel)})'


def _alvos(user):
    """(papel do usuário, [(pessoa, papel do cálculo)], alvo padrão) — o seletor da tela, sem renderizar."""
    from simulator import services as sv
    from simulator import views as simv

    papel = sv.get_user_role(user)
    if papel == sv.ROLE_SUPERADMIN:
        coords = sv.get_all_coordinators()
        aparte = sv.get_all_aparte_users()
        ids_coord, ids_aparte = {u.id for u in coords}, {u.id for u in aparte}
        lista = ([(u, sv.ROLE_APART) for u in aparte]
                 + [(u, sv.ROLE_COORDENADOR) for u in coords if u.id not in ids_aparte]
                 + [(u, sv.ROLE_COORDENADOR) for u in sv.get_all_snipers() if u.id not in ids_coord]
                 + [(u, sv.ROLE_GERENTE) for u in sv.get_all_gerentes() if u.id not in ids_aparte]
                 + [(u, sv.ROLE_CONSULTOR) for u in sv.get_all_consultors() if u.id not in ids_aparte])
        return papel, lista, None
    if papel == sv.ROLE_COORDENADOR:
        setores = sv.get_coordinator_sectors(user)
        qs = (User.objects.filter(is_active=True, sector__in=setores)
              .exclude(id__in=sv.get_simulator_excluded_user_ids()).exclude(id=user.id)
              .order_by('first_name', 'last_name'))
        return papel, [(user, sv.ROLE_COORDENADOR)] + [(u, sv.get_user_role(u)) for u in qs], (user, sv.ROLE_COORDENADOR)
    if papel == sv.ROLE_GERENTE:
        lista = [(u, sv.get_user_role(u)) for u in simv.get_sector_users(user)]
        if not any(u.pk == user.pk for u, _ in lista):
            lista.insert(0, (user, sv.ROLE_GERENTE))
        return papel, lista, (user, sv.ROLE_GERENTE)
    if papel == sv.ROLE_APART:
        return papel, [(user, sv.ROLE_APART)], (user, sv.ROLE_APART)
    lista = [(u, sv.ROLE_CONSULTOR) for u in simv.get_sector_consultors(user)]
    if not any(u.pk == user.pk for u, _ in lista):
        lista.insert(0, (user, sv.ROLE_CONSULTOR))
    return papel, lista, (user, sv.ROLE_CONSULTOR)


def _escolher(user, args):
    from simulator import services as sv

    papel, lista, padrao = _alvos(user)
    pid = args.get('usuario_id')
    if pid in (None, '', 0, '0'):
        busca = _normal(args.get('busca'))
        if busca:
            achados = [(u, r) for u, r in lista if all(p in _normal(u.get_full_name()) for p in busca.split())]
            if len(achados) == 1:
                return papel, achados[0]
            if not achados:
                raise Invalido(f'Ninguém com "{args["busca"]}" entre as pessoas que você pode ver no simulador '
                               '(simulador_consultar o_que=pessoas).')
            raise Invalido('Mais de uma pessoa com esse nome: ' + '; '.join(_rotulo_pessoa(u, r) for u, r in achados[:10])
                           + '. Passe usuario_id.')
        if padrao is None:
            raise Invalido('Como SUPERADMIN, escolha de quem é o comissionamento: passe usuario_id ou busca '
                           '(simulador_consultar o_que=pessoas lista todos).')
        return papel, padrao
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        raise Invalido('usuario_id inválido: informe o número (id).') from None
    for u, r in lista:
        if u.pk == pid:
            if papel == sv.ROLE_SUPERADMIN:
                # Como na tela: o papel do cálculo é o da pessoa (sniper calcula como coordenador).
                r = sv.ROLE_COORDENADOR if sv.is_sniper_user(u) else sv.get_user_role(u)
            return papel, (u, r)
    raise Invalido('Essa pessoa não está entre as que você pode ver no simulador (a tela mostra só a sua loja/'
                   'carteira, conforme o seu papel). Veja simulador_consultar o_que=pessoas.')


def _hunter(args):
    niveis = args.get('hunter') or {}
    if not isinstance(niveis, dict):
        return {}
    saida = {}
    for k, v in niveis.items():
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n in (2, 3):
            saida[_normal(k)] = n
    return saida


def _calcular(alvo, papel_calc, visao, hunter, acelerador):
    from simulator import services as sv
    from simulator.sql_realizado import realized_prefetch

    with realized_prefetch():
        if papel_calc == sv.ROLE_APART:
            from users.models import AParteCommissionConfig

            config = AParteCommissionConfig.objects.filter(user=alvo).first()
            return sv.compute_aparte_simulation(alvo, config, hunter, view_mode=visao)
        dados = sv.get_factor_set(papel_calc).data
        if papel_calc == sv.ROLE_CONSULTOR:
            return sv.compute_consultor_simulation(alvo, dados, hunter, view_mode=visao,
                                                   acelerador_microindicadores=acelerador)
        if papel_calc == sv.ROLE_GERENTE:
            return sv.compute_gerente_simulation(alvo, dados, hunter, view_mode=visao)
        if papel_calc == sv.ROLE_COORDENADOR:
            return sv.compute_coordenador_simulation(alvo, dados, hunter, view_mode=visao)
    return None


def _formatar(sim, alvo, papel_calc, visao, dias):
    passados, total = dias
    linhas = [f'Comissionamento — {_rotulo_pessoa(alvo, papel_calc)} — visão {visao.upper()} '
              f'({timezone.localdate():%m/%Y}; {passados} de {total} dias úteis passados; realizado até ontem)']
    if sim.get('pdv') or sim.get('coordinator'):
        linhas.append('Loja: ' + (sim.get('pdv') or '—') + (f' · coordenação: {sim["coordinator"]}' if sim.get('coordinator') else ''))
    if sim.get('is_sniper'):
        linhas.append(f'Sniper: recebe uma fração do coordenador {sim.get("sniper_coordinator") or "—"}.')
    if sim.get('is_gerente_operacional'):
        linhas.append(f'Gerente operacional: {sim.get("percentual_operacional_pct")}% do gerente de vendas '
                      f'{sim.get("gerente_de_vendas_base") or "—"}.')
    if sim.get('base_salary'):
        linhas.append(f'Salário base (A parte): {_moeda(sim["base_salary"])} — atingimento medido na rede.')
    restantes = max(total - passados, 0)
    linhas.append('Por pilar (meta · realizado/projetado · atingimento · ganho):')
    for r in sim.get('rows') or []:
        fixa = r.get('key') == 'fixa'
        feito = r.get('quantity') if fixa and r.get('quantity') is not None else r.get('proj')
        meta = r.get('meta') or 0
        valor = (lambda v: _num(v) + ' un.') if fixa else _moeda
        ganho = r.get('total_with_pdv')
        if ganho is None:
            ganho = r.get('commission_value')
        extra = ''
        if restantes and meta and (feito or 0) < meta:
            por_dia = (meta - (feito or 0)) / restantes
            extra = ' · falta ' + (f'{por_dia:.1f}'.replace('.', ',') + ' un.' if fixa else _moeda(por_dia)) + '/dia útil'
        linhas.append(f'  {r.get("label") or r.get("key")}: meta {valor(meta)} · {valor(feito)} · '
                      f'{_pct(r.get("attainment"))} · ganho {_moeda(ganho)}{extra}')
    t = sim.get('totals') or {}
    partes = []
    for chave, rotulo in (('total_with_pdv', 'pilares'), ('total_commission', 'comissão'), ('hunter2', 'Hunter II'),
                          ('hunter3', 'Hunter III'), ('bonus_6_7', 'bônus 6/7'),
                          ('acelerador_microindicadores', 'acelerador B2B')):
        if t.get(chave):
            partes.append(f'{rotulo} {_moeda(t[chave])}')
    linhas.append(f'GANHO TOTAL: {_moeda(t.get("ganho_total"))}' + (f' ({", ".join(partes)})' if partes else ''))
    return '\n'.join(linhas)


def _ler_comissao(user, args):
    from simulator import services as sv

    _, (alvo, papel_calc) = _escolher(user, args)
    if papel_calc not in (sv.ROLE_CONSULTOR, sv.ROLE_GERENTE, sv.ROLE_COORDENADOR, sv.ROLE_APART):
        return f'{_nome(alvo)} não tem comissionamento no simulador (papel {ROTULO_PAPEL.get(papel_calc, papel_calc)}).'
    visao = _normal(args.get('visao') or 'projecao')
    if visao not in (sv.VIEW_PROJECAO, sv.VIEW_REALIZADO):
        raise Invalido('visao deve ser projecao ou realizado (o modo Simulador, com valores digitados, é só na tela).')
    acelerador = bool(args.get('acelerador_microindicadores')) and papel_calc == sv.ROLE_CONSULTOR
    sim = _calcular(alvo, papel_calc, visao, _hunter(args), acelerador)
    if not sim:
        return 'O simulador não devolveu cálculo para essa pessoa.'
    if sim.get('error'):
        return f'O simulador avisou: {sim["error"]}'
    texto = _formatar(sim, alvo, papel_calc, visao, sv.get_business_days_info())
    return texto + '\nTela: ' + _url(f'/simulator/?user_id={alvo.pk}&view={visao}')


def _ler_pessoas(user, args):
    papel, lista, _ = _alvos(user)
    busca = _normal(args.get('busca'))
    if busca:
        lista = [(u, r) for u, r in lista if all(p in _normal(u.get_full_name()) for p in busca.split())]
    limite = _limite(args, 40, 400)
    linhas = [f'Seu papel no simulador: {ROTULO_PAPEL.get(papel, papel)}. Pessoas que você pode consultar '
              f'({len(lista)}):']
    linhas += [f'  {_rotulo_pessoa(u, r)}' for u, r in lista[:limite]] or ['  ninguém nesse recorte.']
    if len(lista) > limite:
        linhas.append('… há mais: refine com busca.')
    return '\n'.join(linhas)


def _ler_tabela(user, args):
    from django.core.cache import cache

    from simulator.tabela import DATASET_KEY, resumir
    from users.commission_projection_views import _scope_rows
    from users.commission_views import get_user_role as papel_comissao
    from users.commission_views import pode_ver_comissionamento

    if not pode_ver_comissionamento(user):
        raise Invalido('A tabela da rede fica com o comissionamento: gerentes, coordenadores, SUPERADMIN e quem tem a '
                       'liberação do módulo.')
    dataset = cache.get(DATASET_KEY)
    if not dataset:
        return ('A tabela da rede ainda não está pronta no cache (ela é montada em segundo plano quando alguém abre '
                + _url('/simulator/tabela/') + '). Abra a tela e pergunte de novo em alguns minutos.')
    rows, _ = _scope_rows(user, papel_comissao(user), dataset['rows'])
    if args.get('setor'):
        alvo = _normal(args['setor'])
        rows = [r for r in rows if alvo in _normal(r.get('sector'))]
    if args.get('busca'):
        alvo = _normal(args['busca'])
        rows = [r for r in rows if alvo in _normal(r.get('name'))]
    if args.get('papel'):
        alvo = _normal(args['papel'])
        rows = [r for r in rows if alvo in (_normal(r.get('role_key')), _normal(r.get('role_label')))]
    s = resumir(rows)
    gerado = dataset.get('generated_at')
    vencido = dataset.get('built_on') != timezone.localdate()
    linhas = [f'Tabela da rede (realizado × projeção) — gerada em '
              + (f'{timezone.localtime(gerado):%d/%m/%Y %H:%M}' if gerado else '—') + (' (de outro dia)' if vencido else ''),
              f'{s["pessoas"]} pessoa(s), {s["com_dados"]} com dados · realizado {_moeda(s["realizado"])} · projeção '
              f'{_moeda(s["projecao"])} · diferença {_moeda(s["diferenca"])} · média projetada {_moeda(s["media_projecao"])}']
    ordem = sorted(rows, key=lambda r: r.get('projecao') or 0, reverse=True)
    limite = _limite(args, 25, 300)
    for r in ordem[:limite]:
        linhas.append(f'  {r["name"]} (id {r["id"]}, {r.get("role_label")}, {r.get("sector") or "—"}): realizado '
                      f'{_moeda(r["realizado"])} · projeção {_moeda(r["projecao"])} · dif. {_moeda(r["diferenca"])}'
                      + (f' · {r["motivo"]}' if r.get('motivo') else '') + (f' · erro: {r["erro"]}' if r.get('erro') else ''))
    if len(ordem) > limite:
        linhas.append(f'… e mais {len(ordem) - limite}.')
    return '\n'.join(linhas)


LEITURAS = {'comissao': _ler_comissao, 'pessoas': _ler_pessoas, 'tabela': _ler_tabela}
APELIDOS = {'comissionamento': 'comissao', 'simulacao': 'comissao', 'meu': 'comissao', 'pessoa': 'comissao',
            'projecao': 'comissao', 'realizado': 'comissao', 'rede': 'tabela', 'tabela_rede': 'tabela'}


def _simulador_consultar(user, args):
    o_que = _normal(args.get('o_que') or 'comissao').replace(' ', '_').replace('-', '_')
    if o_que in ('projecao', 'realizado') and not args.get('visao'):
        args = dict(args, visao=o_que)
    leitura = LEITURAS.get(APELIDOS.get(o_que, o_que))
    if leitura is None:
        raise Invalido('o_que inválido. Use: ' + ' | '.join(LEITURAS) + '.')
    return leitura(user, args)


TOOLS = {
    'simulador_consultar': {
        'fn': _simulador_consultar,
        'description': (
            'Simulador de comissionamento (/simulator/), com as permissões da tela. o_que: comissao (meta, realizado/'
            'projetado, atingimento e ganho por pilar, Hunter, bônus 6/7 e ganho total; visao=projecao (padrão) ou '
            'realizado; usuario_id ou busca para outra pessoa) | pessoas (quem ele pode consultar: SUPERADMIN todos; '
            'coordenador a carteira; gerente a loja; consultor os consultores da loja; A parte só ele) | tabela '
            '(realizado × projeção de cada um com o motivo da diferença, do cache da tela; filtros setor, busca, '
            'papel). Um cálculo por pessoa por chamada — o realizado vem do MySQL de vendas (até ontem).'),
        'input_schema': _obj(
            ['o_que'],
            o_que=_txt('comissao | pessoas | tabela.'),
            usuario_id=_int('De quem (id de o_que=pessoas). Padrão: o próprio usuário.'),
            busca=_txt('Parte do nome da pessoa.'),
            visao=_txt('projecao | realizado.'),
            hunter={'type': 'object', 'description': 'Nível Hunter por pilar (2 ou 3), ex.: {"movel": 2}.'},
            acelerador_microindicadores=_bool('Consultor: somar o acelerador B2B (+10%), como o checkbox da tela.'),
            setor=_txt('tabela: filtrar loja.'), papel=_txt('tabela: consultor | gerente | coordenador | aparte.'),
            limite=_int('Quantas linhas listar.')),
    },
}
