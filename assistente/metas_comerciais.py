"""Metas comerciais (/power-bi/metas/ e /power-bi/manage/metas/) no assistente.

Leitura: o mesmo recorte da tela de metas — consultor(a) vê as próprias metas e
as da loja, gerente vê a loja, os demais perfis veem a rede — pelas mesmas funções
de identificação do módulo power_bi. Gestão (só SUPERADMIN): competências
importadas, excluir e sincronizar com o MySQL do painel, pela própria view.
Importar a planilha precisa do arquivo: isso continua só na tela.

Não confundir com as metas do Impulso (/impulso/metas/), que são outra coisa.
"""
from collections import defaultdict
from decimal import Decimal

from .comum import (Invalido, _acao, _dh, _falha, _int, _inteiro, _limite, _nome, _normal, _obj, _sem_erro,
                    _txt, chamar_view)

ZERO = Decimal('0')


def _moeda(valor):
    texto = f'{valor:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')
    return f'R$ {texto}'


def _valor(pilar, total, fixa_em_quantidade):
    from power_bi import views as pv

    if fixa_em_quantidade and pv._is_fixa_pilar(pilar):
        return f'{total.normalize():f} (quantidade)'
    return _moeda(total)


def _competencias():
    from power_bi.models import GoalUpload

    return GoalUpload.objects.select_related('uploaded_by').order_by('-year', '-month', '-updated_at')


def _rotulo(c):
    return f'{c.month:02d}/{c.year}'


def _competencia(args):
    todas = _competencias()
    if args.get('ano') or args.get('mes'):
        ano, mes = _inteiro(args.get('ano'), 'ano'), _inteiro(args.get('mes'), 'mes')
        atual = todas.filter(year=ano, month=mes).first()
        if atual is None:
            disponiveis = ', '.join(_rotulo(c) for c in todas[:12]) or 'nenhuma'
            raise Invalido(f'Não há metas importadas para {mes:02d}/{ano}. Competências disponíveis: {disponiveis}.')
        return atual, todas
    return todas.first(), todas


def _somas(itens, chave):
    somas = defaultdict(lambda: ZERO)
    for e in itens:
        if e.goal_value is not None:
            somas[chave(e) or '—'] += e.goal_value
    return somas


def _metas_comerciais(user, args):
    from power_bi import views as pv
    from power_bi.models import GoalEntry

    atual, todas = _competencia(args)
    if atual is None:
        return 'Nenhuma competência de metas comerciais foi importada ainda (/power-bi/manage/metas/).'

    padrao = pv._is_standard_user(user)
    gerente = padrao and pv._is_gerentes_group_user(user)
    consultor = padrao and not gerente
    fixa_qtd = bool(atual.fixa_as_percentage)
    norm = pv._normalize_text
    CN, PDV = GoalEntry.SHEET_CN_REAL, GoalEntry.SHEET_PDV_REAL
    entradas = list(GoalEntry.objects.filter(upload=atual).order_by('sheet_type', 'store_name', 'pilar', 'user_name'))
    filtros = []

    # O mesmo recorte de goals_list_view (power_bi/views.py), sem renderizar a tela.
    if consultor:
        tokens = {norm(x) for x in (user.full_name, user.get_full_name(), user.first_name,
                                    user.last_name, user.username)} - {''}
        loja = pv._get_user_primary_store(user)
        cns = [e for e in entradas if e.sheet_type == CN and norm(e.user_name) in tokens]
        pdvs = [e for e in entradas if e.sheet_type == PDV and loja and pv._stores_match(norm(e.store_name), loja)]
        perfil = 'consultor(a) — suas metas e as da sua loja' + (f' ({loja})' if loja else '')
    elif gerente:
        loja = pv._get_user_primary_store(user)
        da_loja = [e for e in entradas if loja and pv._stores_match(norm(e.store_name), loja)]
        cns = [e for e in da_loja if e.sheet_type == CN]
        pdvs = [e for e in da_loja if e.sheet_type == PDV]
        perfil = 'gerente — metas da sua loja' + (f' ({loja})' if loja else ' (loja não identificada no cadastro)')
    else:
        base = entradas
        if args.get('loja'):
            alvo = norm(args['loja'])
            base = [e for e in base if pv._stores_match(norm(e.store_name), alvo)]
            filtros.append(f'loja "{args["loja"]}"')
        if args.get('pilar'):
            alvo = norm(args['pilar'])
            base = [e for e in base if norm(e.pilar) == alvo]
            filtros.append(f'pilar {args["pilar"]}')
        cns = [e for e in base if e.sheet_type == CN]
        pdvs = [e for e in base if e.sheet_type == PDV]
        perfil = 'visão da rede'
    if args.get('consultor') and not consultor:
        alvo = norm(args['consultor'])
        cns = [e for e in cns if alvo and alvo in norm(e.user_name)]
        filtros.append(f'consultor "{args["consultor"]}"')

    def entra_no_total(e):
        return e.goal_value is not None and not (fixa_qtd and pv._is_fixa_pilar(e.pilar))

    linhas = [f'Metas comerciais — competência {_rotulo(atual)} (importada em {_dh(atual.updated_at)})',
              f'Perfil: {perfil}' + (f' · filtros: {", ".join(filtros)}' if filtros else '')]
    if fixa_qtd:
        linhas.append('Nesta competência a FIXA é contada em quantidade e fica fora dos totais em R$.')
    if pdvs:
        linhas.append(f'Total das lojas (planilha PDV): {_moeda(sum((e.goal_value for e in pdvs if entra_no_total(e)), ZERO))}')
        linhas.append('Por pilar (lojas): ' + ' · '.join(
            f'{p}: {_valor(p, v, fixa_qtd)}' for p, v in sorted(_somas(pdvs, lambda e: e.pilar).items())))
    if cns:
        linhas.append(f'Total dos consultores (planilha CN): {_moeda(sum((e.goal_value for e in cns if entra_no_total(e)), ZERO))}')
        linhas.append('Por pilar (consultores): ' + ' · '.join(
            f'{p}: {_valor(p, v, fixa_qtd)}' for p, v in sorted(_somas(cns, lambda e: e.pilar).items())))
    if not cns and not pdvs:
        extra = (' Seu nome não apareceu na planilha dos consultores — confira se o cadastro está igual ao da planilha.'
                 if consultor else '')
        linhas.append('Nenhuma meta encontrada para esse recorte.' + extra)

    detalhe = _normal(args.get('detalhar'))
    limite = _limite(args, 15, 300)
    if pdvs and not consultor:
        lojas = sorted(_somas([e for e in pdvs if entra_no_total(e)], lambda e: e.store_name).items(),
                       key=lambda kv: kv[1], reverse=True)
        n = limite if detalhe == 'lojas' else min(limite, 10)
        linhas.append(('Lojas' if detalhe == 'lojas' else 'Maiores metas por loja') + f' ({min(n, len(lojas))} de {len(lojas)}):')
        linhas += [f'  {loja}: {_moeda(v)}' for loja, v in lojas[:n]]
    if cns:
        por_consultor = defaultdict(lambda: defaultdict(lambda: ZERO))
        for e in cns:
            if e.goal_value is not None:
                por_consultor[e.user_name or 'SEM CONSULTOR'][e.pilar or '—'] += e.goal_value
        ordem = sorted(por_consultor.items(), key=lambda kv: sum(
            v for p, v in kv[1].items() if not (fixa_qtd and pv._is_fixa_pilar(p))), reverse=True)
        n = limite if (detalhe == 'consultores' or gerente or consultor) else min(limite, 10)
        linhas.append(('Suas metas por pilar' if consultor else 'Consultores') + f' ({min(n, len(ordem))} de {len(ordem)}):')
        for nome, pilares in ordem[:n]:
            total = sum((v for p, v in pilares.items() if not (fixa_qtd and pv._is_fixa_pilar(p))), ZERO)
            partes = ' · '.join(f'{p} {_valor(p, v, fixa_qtd)}' for p, v in sorted(pilares.items()))
            linhas.append(f'  {nome}: {partes} · total {_moeda(total)}')
    outras = [_rotulo(c) for c in todas[:8] if c.pk != atual.pk]
    if outras:
        linhas.append('Outras competências: ' + ', '.join(outras) + ' (passe ano e mes).')
    return '\n'.join(linhas)


def _metas_comerciais_competencias(user, args):
    from power_bi import views as pv
    from power_bi.models import GoalEntry

    todas = list(_competencias()[:_limite(args, 12, 60)])
    if not todas:
        return 'Nenhuma competência de metas comerciais foi importada ainda.'
    admin = pv._is_superadmin(user)
    linhas = [f'Competências de metas comerciais ({len(todas)}):']
    for c in todas:
        if not admin:
            linhas.append(f'{_rotulo(c)}')
            continue
        cn = c.entries.filter(sheet_type=GoalEntry.SHEET_CN_REAL).count()
        pdv = c.entries.filter(sheet_type=GoalEntry.SHEET_PDV_REAL).count()
        linhas.append(f'competência #{c.pk} · {_rotulo(c)} · arquivo "{c.source_file_name or "—"}" · '
                      f'importada por {_nome(c.uploaded_by)} em {_dh(c.updated_at)} · {pdv} linha(s) de loja, '
                      f'{cn} de consultor' + (' · FIXA em quantidade' if c.fixa_as_percentage else ''))
    if admin:
        linhas.append('Importar uma planilha nova precisa do arquivo: só em /power-bi/manage/metas/.')
    return '\n'.join(linhas)


def _competencia_para_gestao(user, args):
    from power_bi import views as pv
    from power_bi.models import GoalUpload

    if not pv._is_superadmin(user):
        raise Invalido('Só SUPERADMIN gerencia as metas comerciais.')
    c = GoalUpload.objects.filter(pk=_inteiro(args.get('competencia_id'), 'competencia_id')).first()
    if c is None:
        raise Invalido('Competência não encontrada — veja metas_comerciais_competencias.')
    return c


def _previa_excluir(user, args):
    c = _competencia_para_gestao(user, args)
    return (f'Excluir a competência {_rotulo(c)} ({c.entries.count()} linha(s) de meta, arquivo '
            f'"{c.source_file_name or "—"}").\n'
            'O que já foi sincronizado no MySQL do painel e os ajustes de PCN/setor feitos na importação '
            'continuam como estão.\nNão dá para desfazer: para ter de volta, só importando a planilha de novo.'), {
        'competencia_id': c.pk}


def _exec_excluir(user, d):
    from power_bi import views as pv

    cid = d['competencia_id']
    r = chamar_view(pv.delete_goals_upload_view, user, f'/power-bi/manage/metas/{cid}/delete/', upload_id=cid)
    if not _sem_erro(r):
        return 'A competência não foi excluída: ' + _falha(r)
    return ' '.join(texto for _, texto in r['avisos']) or 'Competência excluída.'


def _previa_sincronizar(user, args):
    from power_bi.models import GoalEntry

    c = _competencia_para_gestao(user, args)
    cn = c.entries.filter(sheet_type=GoalEntry.SHEET_CN_REAL).count()
    pdv = c.entries.filter(sheet_type=GoalEntry.SHEET_PDV_REAL).count()
    return (f'Sincronizar a competência {_rotulo(c)} com o MySQL do painel ({pdv} linha(s) de loja e {cn} de '
            'consultor).\nAs linhas dessa competência nas tabelas metas, metas_cn e metas_gerente são apagadas e '
            'gravadas de novo.'), {'competencia_id': c.pk}


def _exec_sincronizar(user, d):
    from power_bi import views as pv

    cid = d['competencia_id']
    r = chamar_view(pv.sync_goals_upload_to_mysql_view, user, f'/power-bi/manage/metas/{cid}/sync-mysql/',
                    upload_id=cid)
    if not _sem_erro(r):
        return 'A sincronização não aconteceu: ' + _falha(r)
    return ' '.join(texto for _, texto in r['avisos']) or 'Sincronização concluída.'


TOOLS = {
    'metas_comerciais': {
        'fn': _metas_comerciais,
        'description': 'Metas comerciais do mês importadas no Power BI (/power-bi/metas/), no recorte que a tela '
                       'mostra para o perfil do usuário: consultor(a) vê as próprias metas e as da loja, gerente a '
                       'loja, os demais a rede. Totais e metas por pilar, por loja e por consultor. Não são as metas '
                       'do Impulso.',
        'input_schema': _obj(ano=_int('Ano da competência (padrão: a mais recente).'),
                             mes=_int('Mês da competência, 1 a 12.'),
                             loja=_txt('Filtrar uma loja (visão da rede).'),
                             pilar=_txt('Filtrar um pilar, ex.: MOVEL, FIXA, SEGURO (visão da rede).'),
                             consultor=_txt('Parte do nome de um consultor (gerente e visão da rede).'),
                             detalhar=_txt('lojas | consultores — a lista inteira em vez dos maiores.'),
                             limite=_int('Quantas linhas listar no detalhe.')),
    },
    'metas_comerciais_competencias': {
        'fn': _metas_comerciais_competencias,
        'description': 'Competências (meses) de metas comerciais já importadas; para SUPERADMIN, com id, arquivo, '
                       'quem importou e quantas linhas.',
        'input_schema': _obj(limite=_int('Quantas listar.')),
    },
    'metas_comerciais_excluir': _acao(
        _previa_excluir, _exec_excluir,
        'Só SUPERADMIN: exclui uma competência de metas comerciais importada.',
        _obj(['competencia_id'], competencia_id=_int('Id da competência (de metas_comerciais_competencias).'))),
    'metas_comerciais_sincronizar': _acao(
        _previa_sincronizar, _exec_sincronizar,
        'Só SUPERADMIN: sincroniza uma competência de metas comerciais com o MySQL do painel.',
        _obj(['competencia_id'], competencia_id=_int('Id da competência (de metas_comerciais_competencias).'))),
}
