"""Ferramentas do assistente — sempre escopadas ao PRÓPRIO usuário.

Cada função recebe (user, args) e lê apenas registros daquele usuário (filtro por
FK), então o assistente nunca alcança dado de terceiros — a segurança é do
servidor, não do prompt. Toda leitura é defensiva: erro/módulo ausente vira um
aviso curto, sem derrubar a conversa.

Adicionar uma ferramenta = uma entrada em `TOOLS` com schema + função.
"""
import json
import logging

from django.db.models import Q

logger = logging.getLogger(__name__)


def _fmt_data(d, fmt='%d/%m/%Y'):
    try:
        return d.strftime(fmt)
    except Exception:
        return '—'


def _fmt_hora(d):
    try:
        return d.strftime('%H:%M')
    except Exception:
        return '—'


def _nome(u):
    if not u:
        return '—'
    return getattr(u, 'full_name', '') or getattr(u, 'email', '') or '—'


# ─── Implementações (cada uma só toca dados do próprio usuário) ───────────────

def _meu_perfil(user, args):
    setor = getattr(getattr(user, 'sector', None), 'name', None) or 'sem setor'
    linhas = [
        f'Nome: {user.full_name}',
        f'E-mail: {user.email}',
        f'Cargo: {getattr(user, "job_title", "") or "—"}',
        f'Setor principal: {setor}',
        f'Hierarquia: {user.get_hierarchy_display()}',
        f'PDV: {getattr(user, "pdv", "") or "—"}',
        f'Admissão: {_fmt_data(getattr(user, "admission_date", None))}',
        f'Telefone: {getattr(user, "phone", "") or "—"}',
    ]
    # Saldo de Confianças (C$) — o "dinheiro" interno do portal.
    saldo = getattr(user, 'balance_cs', None)
    if saldo is not None:
        linhas.append(f'Saldo de Confianças: C$ {saldo}')
    # Tempo de casa, a partir da admissão.
    adm = getattr(user, 'admission_date', None)
    if adm:
        try:
            import datetime
            dias = (datetime.date.today() - adm).days
            anos, resto = divmod(dias, 365)
            meses = resto // 30
            partes = ([f'{anos} ano(s)'] if anos else []) + ([f'{meses} mês(es)'] if meses else [])
            linhas.append('Tempo de casa: ' + (' e '.join(partes) or f'{dias} dia(s)'))
        except Exception:  # noqa: BLE001
            pass
    # Setores vinculados e grupos — dizem a que áreas a pessoa pertence.
    try:
        setores = list(user.sectors.values_list('name', flat=True))
        if setores:
            linhas.append('Setores vinculados: ' + ', '.join(setores))
    except Exception:  # noqa: BLE001
        pass
    try:
        grupos = list(user.communication_groups.values_list('name', flat=True))
        if grupos:
            linhas.append('Grupos: ' + ', '.join(grupos))
    except Exception:  # noqa: BLE001
        pass
    return '\n'.join(linhas)


def _meus_chamados(user, args):
    from tickets.models import Ticket
    qs = Ticket.objects.filter(Q(created_by=user) | Q(assigned_to=user)).select_related('category', 'sector')
    status = (args.get('status') or '').strip()
    if status:
        qs = qs.filter(status__iexact=status)
    qs = qs.order_by('-created_at')[:int(args.get('limite') or 10)]
    itens = list(qs)
    if not itens:
        return 'Nenhum chamado encontrado para você' + (f' com status {status}.' if status else '.')
    linhas = [f'Total listado: {len(itens)}']
    for t in itens:
        linhas.append(
            f'#{t.id} · {t.title} · status={t.get_status_display()} · '
            f'categoria={getattr(t.category, "name", "—")} · aberto em {_fmt_data(t.created_at)}')
    return '\n'.join(linhas)


def _minhas_folhas_ponto(user, args):
    from folhaponto.models import FolhaPonto
    qs = FolhaPonto.objects.filter(user=user)
    ano = args.get('ano')
    if ano:
        qs = qs.filter(year=ano)
    qs = qs.order_by('-year', '-month')[:int(args.get('limite') or 6)]
    itens = list(qs)
    if not itens:
        return 'Nenhuma folha de ponto encontrada para você.'
    linhas = []
    for f in itens:
        assinada = 'assinada' if getattr(f, 'signed_at', None) else 'não assinada'
        linhas.append(f'{f.month:02d}/{f.year} · saldo {f.total_saldo or "—"} · {assinada}')
    return '\n'.join(linhas)


def _meus_contracheques(user, args):
    from contracheque.models import Payslip
    qs = Payslip.objects.filter(user=user)
    ano = args.get('ano')
    if ano:
        qs = qs.filter(year=ano)
    qs = qs.order_by('-year', '-month')[:int(args.get('limite') or 6)]
    itens = list(qs)
    if not itens:
        return 'Nenhum contracheque encontrado para você.'
    linhas = []
    for p in itens:
        assinado = 'assinado' if getattr(p, 'signed_at', None) else 'não assinado'
        linhas.append(f'{p.month:02d}/{p.year} · líquido R$ {p.net_pay} · {assinado}')
    return '\n'.join(linhas)


def _meus_cursos(user, args):
    from cursos.permissions import cursos_do_usuario, pendencias
    cursos = list(cursos_do_usuario(user))
    if not cursos:
        return 'Você não tem cursos atribuídos.'
    pend_ids = {c.id for c in pendencias(user)}
    linhas = []
    for c in cursos:
        estado = 'PENDENTE' if c.id in pend_ids else 'entregue'
        linhas.append(f'{c.titulo} · prazo {_fmt_data(c.prazo)} · {estado}')
    return '\n'.join(linhas)


def _meus_documentos(user, args):
    from documentos.models import DocumentSignature
    qs = (DocumentSignature.objects.filter(user=user)
          .select_related('document').order_by('-created_at')[:int(args.get('limite') or 15)])
    itens = list(qs)
    if not itens:
        return 'Nenhum documento atribuído a você.'
    linhas = []
    for s in itens:
        estado = f'assinado em {_fmt_data(s.signed_at)}' if s.signed_at else 'PENDENTE de assinatura'
        titulo = getattr(s.document, 'title', 'Documento')
        linhas.append(f'{titulo} · {estado}')
    return '\n'.join(linhas)


def _meu_ponto(user, args):
    from tangerino.ponto import resumo_para_usuario
    r = resumo_para_usuario(user)
    if not r.get('disponivel'):
        if r.get('motivo') == 'sem_vinculo':
            return 'Seu usuário não está vinculado ao ponto (Tangerino).'
        return 'Não consegui consultar o ponto agora — tente mais tarde.'
    linhas = [
        f'Hoje ({_fmt_data(r.get("dia"))}): {r.get("rotulo", "—")}.',
        f'Trabalhado até agora: {r.get("trabalhado_hhmm", "—")}.',
    ]
    if r.get('primeira'):
        linhas.append(f'Primeira marcação {_fmt_hora(r.get("primeira"))} · '
                      f'última {_fmt_hora(r.get("ultima"))}.')
    pend = r.get('pendencias') or []
    if pend:
        linhas.append(f'Atenção: {len(pend)} dia(s) com ponto em aberto (esquecido):')
        for p in pend[:5]:
            linhas.append(f'  {_fmt_data(p.get("dia"))} — entrada sem saída')
    else:
        linhas.append('Sem pendências de ponto nos últimos dias.')
    return '\n'.join(linhas)


def _minhas_ferias(user, args):
    from tangerino.ferias import situacao_do_usuario
    r = situacao_do_usuario(user)
    if not r.get('disponivel'):
        if r.get('motivo') == 'sem_vinculo':
            return 'Seu usuário não está vinculado às férias (Tangerino).'
        return 'Não consegui consultar as férias agora — tente mais tarde.'
    linhas = [f'Saldo de férias: {r.get("saldo_total", 0)} dia(s).']
    if r.get('dias_vencidos'):
        linhas.append(f'⚠ {r["dias_vencidos"]} dia(s) VENCIDOS — precisam ser marcados.')
    if r.get('em_gozo'):
        linhas.append(f'Você está de férias agora — volta em {_fmt_data(r.get("volta_em"))} '
                      f'({r.get("dias_restantes_gozo", 0)} dia(s) restantes).')
    prox = r.get('proxima')
    if prox:
        linhas.append(f'Próximas férias marcadas: {_fmt_data(prox.get("inicio"))} '
                      f'a {_fmt_data(prox.get("fim"))}.')
    if not r.get('dias_vencidos') and not r.get('em_gozo') and not prox:
        linhas.append('Nada vencido e nenhuma férias marcada no momento.')
    return '\n'.join(linhas)


def _minhas_metas(user, args):
    from django.db.models import Q
    from impulso.models import Meta
    linhas = []

    # Faixa/pontuação do mês — só faz sentido para quem participa do Impulso.
    try:
        from impulso.utils import is_impulso_member, faixa_info
        if is_impulso_member(user):
            from impulso.scoring import calcular_pontuacao
            p = calcular_pontuacao(user)
            linhas.append(f'Impulso do mês: {p["percentual"]}% · faixa '
                          f'{faixa_info(p["faixa"])["label"]} · '
                          f'{p["total"]}/{p["aplicavel"]} pontos.')
    except Exception as exc:  # noqa: BLE001 — a pontuação é um extra, não trava a lista
        logger.debug('Pontuação Impulso indisponível para %s: %s', user, exc)

    qs = (Meta.objects.filter(Q(colaborador=user) | Q(participantes=user))
          .select_related('gestor').distinct())
    status = (args.get('status') or '').strip()
    if status:
        qs = qs.filter(status__iexact=status)
    elif not args.get('incluir_concluidas'):
        qs = qs.exclude(status=Meta.Status.CONCLUIDA)
    itens = list(qs.order_by('prazo')[:int(args.get('limite') or 15)])

    if not itens:
        linhas.append(f'Nenhuma meta com status {status}.' if status
                      else 'Nenhuma meta em aberto atribuída a você.')
        return '\n'.join(linhas)

    linhas.append(f'Metas ({len(itens)}):')
    for m in itens:
        extra = '' if m.aprovacao == Meta.Aprovacao.APROVADA else f' · {m.get_aprovacao_display()}'
        if m.nota_qualidade is not None or m.nota_prazo is not None:
            extra += f' · notas Q{m.nota_qualidade if m.nota_qualidade is not None else "—"}' \
                     f'/P{m.nota_prazo if m.nota_prazo is not None else "—"}'
        linhas.append(f'{m.titulo} · {m.get_status_display()} · prazo {_fmt_data(m.prazo)} · '
                      f'gestor {_nome(m.gestor)}{extra}')
    return '\n'.join(linhas)


# Nível numérico do Drive → rótulo legível (ver drive/permissions.py::ORDEM).
_DRIVE_NIVEIS = {1: 'Visualizar', 2: 'Download', 3: 'Upload',
                 4: 'Editar', 5: 'Excluir', 6: 'Administrar'}


def _meu_drive(user, args):
    from drive.permissions import sectors_visible, level_for_folder
    limite = int(args.get('limite') or 20)
    linhas = []

    mapeamentos = sectors_visible(user)   # já é escopado ao usuário no servidor
    if mapeamentos:
        linhas.append(f'Pastas do Drive que você acessa ({len(mapeamentos)}):')
        for m in mapeamentos[:limite]:
            nivel = level_for_folder(user, m)
            linhas.append(f'{m.sector.name} · acesso {_DRIVE_NIVEIS.get(nivel, "—")}')
    else:
        linhas.append('Você ainda não tem pastas liberadas no Drive.')

    favs = list(user.drive_favoritos.all()[:10])
    if favs:
        linhas.append('')
        linhas.append(f'Favoritos ({len(favs)}):')
        for f in favs:
            linhas.append(f'★ {f.file_name or f.file_id}')

    logs = list(user.drive_logs.all()[:8])
    if logs:
        linhas.append('')
        linhas.append('Sua atividade recente no Drive:')
        for l in logs:
            linhas.append(f'{_fmt_data(l.criado_em)} · {l.get_acao_display()} · {l.file_name or "—"}')
    return '\n'.join(linhas)


def _minhas_confiancas(user, args):
    from prizes.models import CSTransaction
    saldo = getattr(user, 'balance_cs', None)
    linhas = [f'Saldo de Confianças (C$): {saldo if saldo is not None else "—"}']
    itens = list(CSTransaction.objects.filter(user=user)
                 .order_by('-created_at')[:int(args.get('limite') or 10)])
    if itens:
        linhas.append(f'Últimos movimentos ({len(itens)}):')
        for t in itens:
            sinal = ('+' if t.transaction_type in ('CREDIT', 'REFUND')
                     else '-' if t.transaction_type in ('DEBIT', 'REDEMPTION') else '')
            linhas.append(f'{_fmt_data(t.created_at)} · {t.get_transaction_type_display()} · '
                          f'{sinal}C$ {t.amount} · {t.description} · {t.get_status_display()}')
    else:
        linhas.append('Nenhum movimento de C$ registrado.')
    return '\n'.join(linhas)


def _meus_elogios(user, args):
    from compliments.models import Compliment
    recebidos = Compliment.objects.filter(to_user=user, is_active=True).select_related('from_user')
    total_receb = recebidos.count()
    total_dados = Compliment.objects.filter(from_user=user, is_active=True).count()
    linhas = [f'Elogios recebidos: {total_receb} · elogios que você deu: {total_dados}']
    recentes = list(recebidos.order_by('-created_at')[:int(args.get('limite') or 8)])
    if recentes:
        linhas.append('Recebidos recentemente:')
        for c in recentes:
            linhas.append(f'{_fmt_data(c.created_at)} · {c.rating}★ · de {_nome(c.from_user)} · '
                          f'{(c.comment or "")[:140]}')
    return '\n'.join(linhas)


def _minhas_notificacoes(user, args):
    from notifications.models import UserNotification
    qs = UserNotification.objects.filter(user=user).select_related('notification')
    nao_lidas = qs.filter(is_read=False).count()
    recentes = list(qs.order_by('-created_at')[:int(args.get('limite') or 10)])
    linhas = [f'Notificações não lidas: {nao_lidas}']
    if recentes:
        linhas.append('Recentes:')
        for n in recentes:
            estado = 'não lida' if not n.is_read else 'lida'
            linhas.append(f'{_fmt_data(n.created_at)} · {getattr(n.notification, "title", "—")} · {estado}')
    else:
        linhas.append('Nenhuma notificação.')
    return '\n'.join(linhas)


def _meus_feedbacks(user, args):
    from impulso.models import ImpulsoFeedback
    itens = list(ImpulsoFeedback.objects.filter(colaborador=user)
                 .select_related('gestor').order_by('-referencia_mes')[:int(args.get('limite') or 6)])
    if not itens:
        return 'Você ainda não recebeu feedbacks no Impulso.'
    linhas = [f'Feedbacks recebidos ({len(itens)}):']
    for f in itens:
        nota = f' · nota IA {f.nota_ia}' if f.nota_ia is not None else ''
        linhas.append(f'{_fmt_data(f.referencia_mes, "%m/%Y")} · de {_nome(f.gestor)}{nota}')
        if f.pontos_fortes:
            linhas.append(f'   Fortes: {f.pontos_fortes[:160]}')
        if f.pontos_melhoria:
            linhas.append(f'   A melhorar: {f.pontos_melhoria[:160]}')
    return '\n'.join(linhas)


def _minhas_trilhas(user, args):
    from knowledge_trails.models import TrailProgress
    itens = list(TrailProgress.objects.filter(user=user)
                 .select_related('trail').order_by('status')[:int(args.get('limite') or 15)])
    if not itens:
        return 'Você não está em nenhuma trilha de conhecimento.'
    linhas = ['Suas trilhas:']
    for p in itens:
        linhas.append(f'{p.trail.title} · {p.get_status_display()} · {p.total_points_earned} pts')
    return '\n'.join(linhas)


def _resumo_geral(user, args):
    """Panorama do que está em aberto para o usuário, cruzando vários módulos.

    Cada linha é defensiva: se um módulo falhar, some daquela linha em vez de
    derrubar o resumo inteiro. É o atalho para 'o que eu preciso resolver?'.
    """
    linhas = [f'Panorama de {_nome(user)}:']

    def _tenta(rotulo, fn):
        try:
            linhas.append(f'· {rotulo}: {fn()}')
        except Exception as exc:  # noqa: BLE001
            logger.debug('resumo_geral/%s falhou: %s', rotulo, exc)

    def _cursos():
        from cursos.permissions import pendencias
        return f'{len(list(pendencias(user)))} curso(s) pendente(s)'

    def _docs():
        from documentos.models import DocumentSignature
        return (f'{DocumentSignature.objects.filter(user=user, signed_at__isnull=True).count()} '
                'documento(s) a assinar')

    def _metas():
        from impulso.models import Meta
        n = (Meta.objects.filter(Q(colaborador=user) | Q(participantes=user))
             .exclude(status=Meta.Status.CONCLUIDA).distinct().count())
        return f'{n} meta(s) em aberto'

    def _notif():
        from notifications.models import UserNotification
        return f'{UserNotification.objects.filter(user=user, is_read=False).count()} notificação(ões) não lida(s)'

    def _cs():
        return f'C$ {getattr(user, "balance_cs", "—")}'

    def _ponto():
        from tangerino.ponto import resumo_para_usuario
        r = resumo_para_usuario(user)
        if not r.get('disponivel'):
            return 'sem vínculo de ponto' if r.get('motivo') == 'sem_vinculo' else 'indisponível agora'
        pend = len(r.get('pendencias') or [])
        extra = f', {pend} dia(s) com ponto em aberto' if pend else ''
        return f'{r.get("rotulo", "—")} (trabalhado {r.get("trabalhado_hhmm", "—")}){extra}'

    def _ferias():
        from tangerino.ferias import situacao_do_usuario
        r = situacao_do_usuario(user)
        if not r.get('disponivel'):
            return 'sem vínculo' if r.get('motivo') == 'sem_vinculo' else 'indisponível agora'
        venc = f', {r["dias_vencidos"]} vencido(s)' if r.get('dias_vencidos') else ''
        return f'{r.get("saldo_total", 0)} dia(s) de saldo{venc}'

    _tenta('Cursos', _cursos)
    _tenta('Documentos', _docs)
    _tenta('Metas', _metas)
    _tenta('Ponto hoje', _ponto)
    _tenta('Férias', _ferias)
    _tenta('Notificações', _notif)
    _tenta('Confianças', _cs)
    return '\n'.join(linhas)


# ─── Resultados comerciais (Parciais Vivo) — SÓ SUPERADMIN ───────────────────
# Dado global da rede (não é dado "do usuário"), então fica atrás do mesmo
# portão do menu: apenas SUPERADMIN. As abas do painel são endpoints livres
# (sem login); o assistente lê server-side e resume. Trocar a URL: PARCIAIS_URL.

import re as _re  # usado só aqui, para limpar o HTML da aba Resultados

# aba lógica → (caminho, precisa de scope?, tipo). data_ref entra em todas.
_PARCIAIS_ABAS = {
    'resultados':   ('/parciais/indicadores/resultados-fragment', True, 'html'),
    'meta_dia':     ('/parciais/indicadores/meta-dia-dados', True, 'json'),
    'banda_larga':  ('/parciais/indicadores/banda-larga-dados', True, 'json'),
    'dias_zerados': ('/parciais/indicadores/dias-zerados-dados', True, 'json'),
    'bsc_d0':       ('/api/dados-bsc-d0', True, 'json'),
    'ppl':          ('/parciais/indicadores/ppl-dados', False, 'json'),
}
_PARCIAIS_MICRO = {
    'coordenacao': '/api/dados-microindicadores-coordenacao',
    'loja': '/api/dados-microindicadores-loja',
    'cn': '/api/dados-microindicadores-cn',
}
_PARCIAIS_ABAS_VALIDAS = (['resumo'] + list(_PARCIAIS_ABAS) + ['microindicadores'])
_PARCIAIS_LIMITE = 15000   # teto de caracteres por aba, para não estourar o contexto


def _e_superadmin(user):
    return bool(user and getattr(user, 'is_authenticated', False)
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def _parciais_get(path, params, espera_json=True):
    """GET no painel Parciais. Devolve (dados|texto) ou levanta para o chamador."""
    import requests
    from django.conf import settings
    base = (getattr(settings, 'PARCIAIS_URL', '') or '').rstrip('/')
    if not base:
        raise RuntimeError('PARCIAIS_URL não configurada.')
    r = requests.get(base + path, params=params, timeout=20)
    r.raise_for_status()
    return r.json() if espera_json else r.text


def _truncar(texto, limite=_PARCIAIS_LIMITE):
    texto = texto or ''
    if len(texto) <= limite:
        return texto
    return texto[:limite] + f'\n… [cortado em {limite} caracteres — peça uma aba específica ' \
                            f'ou um scope de loja para ver o restante]'


def _parciais_html_para_texto(html):
    html = _re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', html)
    html = _re.sub(r'(?s)<[^>]+>', ' ', html)
    return _re.sub(r'[ \t]+', ' ', _re.sub(r'\n\s*\n\s*\n+', '\n\n', html)).strip()


def _resumo_meta_dia(dados):
    """Formata a aba Meta Dia como a 'Visão geral do dia' do painel."""
    linhas = []
    st = (dados.get('_status') or {})
    cab = f'{dados.get("scope_label", "Rede")} · {dados.get("data", "")}'
    if st.get('updated_at'):
        cab += f' · atualizado {st["updated_at"]}'
    linhas.append(cab)
    linhas.append(f'{dados.get("atingidos", 0)}/{dados.get("total_pilares", 0)} pilares na meta')
    for l in dados.get('linhas', []):
        alvo = '✓' if l.get('atingido') else ' '
        linhas.append(f'[{alvo}] {l.get("pilar")}: {l.get("realizado")} de {l.get("meta")} '
                      f'({l.get("atingimento")}) · faltam {l.get("faltando")}')
    return '\n'.join(linhas)


def _resultados_comerciais(user, args):
    if not _e_superadmin(user):
        return 'Os resultados comerciais da rede são restritos a SUPERADMIN.'

    import datetime
    aba = (args.get('aba') or 'resumo').strip().lower().replace(' ', '_').replace('-', '_')
    if aba not in _PARCIAIS_ABAS_VALIDAS:
        return (f'Aba desconhecida: {aba}. Abas: ' + ', '.join(_PARCIAIS_ABAS_VALIDAS) + '.')
    scope = (args.get('scope') or 'rede').strip() or 'rede'
    data_ref = (args.get('data_ref') or '').strip()
    if not _re.match(r'^\d{4}-\d{2}-\d{2}$', data_ref):
        data_ref = datetime.date.today().isoformat()

    def p_scope():
        return {'scope': scope, 'data_ref': data_ref}

    try:
        if aba == 'resumo':
            meta = _parciais_get('/parciais/indicadores/meta-dia-dados', p_scope())
            return 'RESULTADOS DO DIA (Parciais Vivo)\n' + _resumo_meta_dia(meta)

        if aba == 'microindicadores':
            incluir_cn = bool(args.get('incluir_cn'))
            partes = ['MICROINDICADORES (documentos por produto) — período do mês']
            for chave in (['coordenacao', 'loja'] + (['cn'] if incluir_cn else [])):
                d = _parciais_get(_PARCIAIS_MICRO[chave], p_scope())
                partes.append(f'\n— por {chave} —\n' + json.dumps(d.get('data', d), ensure_ascii=False))
            return _truncar('\n'.join(partes))

        caminho, usa_scope, tipo = _PARCIAIS_ABAS[aba]
        params = p_scope() if usa_scope else {'data_ref': data_ref, 'modo': 'mes'}
        if tipo == 'html':
            texto = _parciais_html_para_texto(_parciais_get(caminho, params, espera_json=False))
            return _truncar(f'ABA RESULTADOS · {scope} · {data_ref}\n{texto}')
        dados = _parciais_get(caminho, params)
        return _truncar(f'ABA {aba.upper()} · {scope} · {data_ref}\n'
                        + json.dumps(dados, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 — vira aviso curto, não derruba a conversa
        logger.warning('Parciais (%s) falhou: %s', aba, exc)
        return ('Não consegui consultar o painel de resultados comerciais agora. '
                'Confirme se o painel está no ar e tente de novo.')


# ─── Registro ────────────────────────────────────────────────────────────────

TOOLS = {
    'meu_perfil': {
        'fn': _meu_perfil,
        'description': 'Dados cadastrais do próprio usuário (nome, cargo, setor, hierarquia, admissão).',
        'input_schema': {'type': 'object', 'properties': {}, 'required': []},
    },
    'meus_chamados': {
        'fn': _meus_chamados,
        'description': 'Chamados (tickets) abertos por ou atribuídos ao usuário. Filtro de status opcional.',
        'input_schema': {'type': 'object', 'properties': {
            'status': {'type': 'string', 'description': 'Filtro opcional de status (ex.: ABERTO, EM_ANDAMENTO, RESOLVIDO).'},
            'limite': {'type': 'integer', 'description': 'Quantos listar (padrão 10).'}}, 'required': []},
    },
    'minhas_folhas_ponto': {
        'fn': _minhas_folhas_ponto,
        'description': 'Folhas de ponto do usuário (mês/ano, saldo, se está assinada).',
        'input_schema': {'type': 'object', 'properties': {
            'ano': {'type': 'integer'}, 'limite': {'type': 'integer'}}, 'required': []},
    },
    'meus_contracheques': {
        'fn': _meus_contracheques,
        'description': 'Contracheques do usuário (mês/ano, valor líquido, se está assinado).',
        'input_schema': {'type': 'object', 'properties': {
            'ano': {'type': 'integer'}, 'limite': {'type': 'integer'}}, 'required': []},
    },
    'meus_cursos': {
        'fn': _meus_cursos,
        'description': 'Cursos atribuídos ao usuário e se estão pendentes ou entregues.',
        'input_schema': {'type': 'object', 'properties': {}, 'required': []},
    },
    'meus_documentos': {
        'fn': _meus_documentos,
        'description': 'Documentos atribuídos ao usuário para assinatura e o status de cada um.',
        'input_schema': {'type': 'object', 'properties': {
            'limite': {'type': 'integer'}}, 'required': []},
    },
    'meu_ponto': {
        'fn': _meu_ponto,
        'description': 'Situação do ponto de hoje (Tangerino): se bateu a entrada, horas '
                       'trabalhadas até agora e dias anteriores com ponto em aberto (esquecido).',
        'input_schema': {'type': 'object', 'properties': {}, 'required': []},
    },
    'minhas_ferias': {
        'fn': _minhas_ferias,
        'description': 'Férias do usuário (Tangerino): saldo, dias vencidos, se está de férias '
                       'agora e as próximas férias já marcadas.',
        'input_schema': {'type': 'object', 'properties': {}, 'required': []},
    },
    'minhas_metas': {
        'fn': _minhas_metas,
        'description': 'Metas do Impulso atribuídas ao usuário (status no Kanban, prazo, '
                       'aprovação e nota do gestor) e a faixa/pontuação do mês.',
        'input_schema': {'type': 'object', 'properties': {
            'status': {'type': 'string', 'description': 'Filtro opcional: A_FAZER, EM_ANDAMENTO, ENTREGUE, CONCLUIDA.'},
            'incluir_concluidas': {'type': 'boolean', 'description': 'Incluir metas já concluídas (padrão não).'},
            'limite': {'type': 'integer'}}, 'required': []},
    },
    'meu_drive': {
        'fn': _meu_drive,
        'description': 'Drive do usuário: pastas/setores que ele pode acessar (com o nível de '
                       'permissão), favoritos e a própria atividade recente.',
        'input_schema': {'type': 'object', 'properties': {
            'limite': {'type': 'integer'}}, 'required': []},
    },
    'resumo_geral': {
        'fn': _resumo_geral,
        'description': 'Panorama do usuário cruzando módulos: cursos pendentes, documentos a '
                       'assinar, metas em aberto, ponto de hoje, saldo de férias, notificações '
                       'não lidas e saldo de Confianças. Use para "o que eu preciso resolver?".',
        'input_schema': {'type': 'object', 'properties': {}, 'required': []},
    },
    'minhas_confiancas': {
        'fn': _minhas_confiancas,
        'description': 'Saldo de Confianças (C$) do usuário e os últimos movimentos (créditos, '
                       'débitos, resgates) com data e status.',
        'input_schema': {'type': 'object', 'properties': {
            'limite': {'type': 'integer'}}, 'required': []},
    },
    'meus_elogios': {
        'fn': _meus_elogios,
        'description': 'Elogios do usuário no portal: quantos recebeu e deu, e os últimos '
                       'recebidos (estrelas, de quem, comentário).',
        'input_schema': {'type': 'object', 'properties': {
            'limite': {'type': 'integer'}}, 'required': []},
    },
    'minhas_notificacoes': {
        'fn': _minhas_notificacoes,
        'description': 'Notificações do usuário: quantas não lidas e as mais recentes.',
        'input_schema': {'type': 'object', 'properties': {
            'limite': {'type': 'integer'}}, 'required': []},
    },
    'meus_feedbacks': {
        'fn': _meus_feedbacks,
        'description': 'Feedbacks mensais que o usuário recebeu no Impulso (pontos fortes, '
                       'pontos a melhorar, nota da IA), do mais recente ao mais antigo.',
        'input_schema': {'type': 'object', 'properties': {
            'limite': {'type': 'integer'}}, 'required': []},
    },
    'minhas_trilhas': {
        'fn': _minhas_trilhas,
        'description': 'Trilhas de conhecimento do usuário e o progresso em cada uma '
                       '(status e pontos conquistados).',
        'input_schema': {'type': 'object', 'properties': {
            'limite': {'type': 'integer'}}, 'required': []},
    },
    'resultados_comerciais': {
        'fn': _resultados_comerciais,
        'description': (
            'Resultados comerciais da rede (painel Parciais Vivo) — SÓ para SUPERADMIN. '
            'Lê ao vivo qualquer aba do painel e traz os números. Use o parâmetro "aba": '
            '"resumo" (visão geral do dia por pilar: móvel, fixa, smartphone, eletrônicos, '
            'essenciais, seguro, sva — realizado x meta e atingimento), "resultados" '
            '(ranking completo por loja em cada pilar), "meta_dia", "banda_larga" '
            '(vendas acelerando/desafio), "microindicadores" (documentos por produto, por '
            'coordenação/loja/consultor), "bsc_d0" (ranking BSC por PDV), "ppl" e '
            '"dias_zerados". Para uma loja específica, passe "scope" com o nome/identificador '
            'da loja; para outro dia, "data_ref" no formato AAAA-MM-DD (padrão: hoje).'),
        'input_schema': {'type': 'object', 'properties': {
            'aba': {'type': 'string', 'description': 'resumo | resultados | meta_dia | banda_larga | microindicadores | bsc_d0 | ppl | dias_zerados (padrão: resumo).'},
            'scope': {'type': 'string', 'description': 'Escopo: "rede" (padrão) ou o nome/identificador de uma loja.'},
            'data_ref': {'type': 'string', 'description': 'Dia de referência AAAA-MM-DD (padrão: hoje).'},
            'incluir_cn': {'type': 'boolean', 'description': 'Em microindicadores, incluir também o detalhe por consultor (CN).'}},
            'required': []},
    },
}


def tools_schema():
    """Lista no formato que a API do Claude espera."""
    return [{'name': nome, 'description': d['description'], 'input_schema': d['input_schema']}
            for nome, d in TOOLS.items()]


def executar(nome, args, user):
    """Roda a ferramenta escopada ao usuário. Nunca levanta — devolve texto."""
    tool = TOOLS.get(nome)
    if not tool:
        return f'Ferramenta desconhecida: {nome}.'
    try:
        return tool['fn'](user, args or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning('Ferramenta %s falhou: %s', nome, exc)
        return f'Não consegui obter estes dados agora ({nome}).'
