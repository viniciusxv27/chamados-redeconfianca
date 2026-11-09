"""Ponto (/ponto/) no assistente — leitura completa, com as regras do módulo.

O mesmo recorte de cada tela, pelos helpers do próprio app (tangerino/views.py,
tangerino/escala.py, tangerino/ajustes.py, solicitacoes_dp/permissoes.py):

- o próprio ponto (hoje, período, folha do mês, banco de horas, escala, férias,
  pedidos de ajuste e solicitações ao DP) para quem tem o módulo liberado;
- o ponto de outra pessoa, o painel da equipe e as férias da equipe só para quem
  administra o ponto (``e_gestor``: SUPERADMIN e ADMINISTRAÇÃO);
- o relatório do período para quem abre o relatório (``pode_ver_relatorio``);
- a escala dos colaboradores para quem monta escala (``escala.pode_gerenciar``);
- a fila de ajustes para o setor responsável e a caixa do DP para quem analisa.

SÓ LEITURA, de propósito: bater ponto (nunca disparado em produção), pedir,
aprovar ou recusar ajuste e mexer em solicitação ao DP continuam só na tela. As
chamadas ao Tangerino são as mesmas GETs que as telas fazem (com o timeout do
cliente); o relatório só completa o período na API quando é de uma pessoa.
"""
import calendar
import logging
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from .comum import Invalido, _bool, _cortar, _data, _int, _limite, _nome, _normal, _obj, _sim, _txt, _url

logger = logging.getLogger(__name__)
User = get_user_model()

MESES = ['', 'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
         'outubro', 'novembro', 'dezembro']
DIAS = ('seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom')
# A tela de ponto mostra quatro semanas; aqui o período pode ir a 62 dias (o
# pedaço que a API do Tangerino aceita numa consulta só).
MAX_DIAS_PERIODO = 62


# ─── Base ────────────────────────────────────────────────────────────────────

def _config():
    from tangerino.models import ConfiguracaoTangerino

    return ConfiguracaoTangerino.get()


def _exige_ponto(user):
    if not _config().libera_ponto(user):
        raise Invalido('O Ponto não está liberado para você no portal (quem libera é o RH/SUPERADMIN em '
                       '/ponto/configuracao/).')


def _exige_ferias(user):
    if not _config().libera_ferias(user):
        raise Invalido('As Férias não estão liberadas para você no portal agora.')


def _e_gestor(user):
    from tangerino.views import e_gestor

    return e_gestor(user)


def _d(dia):
    return f'{DIAS[dia.weekday()]} {dia:%d/%m/%Y}' if dia else '—'


def _hm(dt):
    try:
        return timezone.localtime(dt).strftime('%H:%M') if timezone.is_aware(dt) else dt.strftime('%H:%M')
    except Exception:  # noqa: BLE001
        return '—'


def _integracao():
    from tangerino.client import integracao_ativa

    if not integracao_ativa():
        raise Invalido('A integração com o Tangerino (Sólides) está desligada no portal: não há como ler o ponto agora.')


def _alvo(user, args, rotulo='o ponto'):
    """A pessoa consultada: o próprio usuário, ou outra se ele administra o ponto (como na tela)."""
    pid = args.get('usuario_id')
    if pid in (None, '', 0, '0'):
        return user
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        raise Invalido('usuario_id inválido: informe o número (id).') from None
    if pid == user.pk:
        return user
    if not _e_gestor(user):
        raise Invalido(f'Você só pode consultar {rotulo} de outra pessoa se administrar o ponto (SUPERADMIN ou '
                       'ADMINISTRAÇÃO). Aqui vai só o seu.')
    alvo = User.objects.filter(pk=pid, is_active=True).select_related('sector').first()
    if alvo is None:
        raise Invalido('Não achei colaborador ativo com esse id (use ponto_consultar o_que=pessoas).')
    return alvo


def _vinculado(alvo, eu):
    if not getattr(alvo, 'tangerino_employee_id', None):
        quem = 'Seu usuário' if alvo.pk == eu.pk else f'{_nome(alvo)}'
        raise Invalido(f'{quem} não está vinculado ao ponto (Tangerino/Sólides) — o vínculo é feito em /ponto/vinculos/.')


def _mes(args):
    hoje = timezone.localdate()
    try:
        ano = int(args.get('ano') or hoje.year)
        mes = int(args.get('mes') or hoje.month)
    except (TypeError, ValueError):
        raise Invalido('ano/mes inválidos.') from None
    if not 1 <= mes <= 12:
        raise Invalido('mes deve ser de 1 a 12.')
    return ano, mes


def _linha_dia(d):
    batidas = ' '.join(('↑' if e['tipo'] == 'ENTRADA' else '↓') + _hm(e['quando']) for e in d['eventos']) or 'sem batida'
    extra = []
    if d.get('previsto'):
        extra.append(f'previsto {d["previsto"]}')
    if d.get('diferenca') is not None and d['dia'] < timezone.localdate():
        from tangerino.jornada import formata_hhmm

        sinal = '+' if d['diferenca'] >= 0 else '-'
        extra.append(f'saldo {sinal}{formata_hhmm(abs(d["diferenca"]))}')
    if d.get('aberto'):
        extra.append('PONTO EM ABERTO (entrada sem saída)')
    return f'  {_d(d["dia"])}: {batidas} · trabalhado {d["horas"]}' + (' · ' + ' · '.join(extra) if extra else '')


def _dias(alvo, inicio, fim, ttl=120):
    from tangerino.client import listar_marcacoes
    from tangerino.views import _dias_com_marcacoes, _jornada_do_usuario

    grade = _jornada_do_usuario(alvo)
    pares = listar_marcacoes(inicio, fim, employee_id=alvo.tangerino_employee_id, ttl=ttl)
    dias = _dias_com_marcacoes(pares, inicio, fim, grade=grade, employee_id=alvo.tangerino_employee_id)
    return dias, pares, grade


def _totais(dias, rotulo):
    from tangerino.views import _previsto_x_feito

    t = _previsto_x_feito(dias, timezone.localdate(), rotulo=rotulo)
    texto = f'trabalhado {t["feito_" + rotulo]}'
    if t[f'previsto_{rotulo}'] is not None:
        sinal = '+' if (t[f'diferenca_{rotulo}_segundos'] or 0) >= 0 else '-'
        texto += (f' · previsto {t["previsto_" + rotulo]} (sem contar hoje) · diferença '
                  f'{sinal}{t["diferenca_" + rotulo].lstrip("-")}')
    else:
        texto += ' · sem jornada contratada sincronizada (só o realizado)'
    return texto


def _quem(alvo, eu):
    return 'Você' if alvo.pk == eu.pk else f'{_nome(alvo)} (id {alvo.pk})'


# ─── Leituras ────────────────────────────────────────────────────────────────

def _ler_hoje(user, args):
    from tangerino import ponto as ponto_svc
    from tangerino.client import TangerinoError, listar_marcacoes
    from tangerino.models import nao_bate_ponto

    _exige_ponto(user)
    alvo = _alvo(user, args)
    _vinculado(alvo, user)
    _integracao()
    hoje = timezone.localdate()
    try:
        pares = listar_marcacoes(hoje - timedelta(days=7), hoje, employee_id=alvo.tangerino_employee_id, ttl=120)
        st = ponto_svc.status_do_dia(alvo.tangerino_employee_id, dia=hoje, pares=pares)
        pend = ponto_svc.pendencias(alvo.tangerino_employee_id, pares=pares)
    except TangerinoError as exc:
        return f'O Tangerino não respondeu agora: {_cortar(str(exc), 200)}'
    linhas = [f'Ponto de hoje — {_quem(alvo, user)} — {_d(hoje)}']
    rotulo = st['rotulo'] if alvo.pk == user.pk else st['rotulo'].replace('Você ainda não bateu', 'Ainda não bateu')
    linhas.append(f'Situação: {rotulo} · trabalhado até agora {st["trabalhado_hhmm"]}')
    if st['eventos']:
        linhas.append('Batidas: ' + ', '.join(
            f'{"entrada" if e["tipo"] == "ENTRADA" else "saída"} {_hm(e["quando"])}' + (' (editada)' if e.get('editado') else '')
            for e in st['eventos']))
    linhas.append('Marcos: entrada ' + ('✓' if st['bateu_entrada'] else '—') + ' · saída p/ intervalo '
                  + ('✓' if st['saiu_almoco'] else '—') + ' · volta do intervalo ' + ('✓' if st['voltou_almoco'] else '—'))
    if pend:
        linhas.append(f'Ponto em aberto (entrada sem saída) em {len(pend)} dia(s) anterior(es): '
                      + ', '.join(_d(p['dia']) for p in pend[:10]))
    else:
        linhas.append('Sem ponto em aberto nos dias anteriores.')
    if nao_bate_ponto(alvo):
        linhas.append('Obs.: está no grupo de quem é dispensado de bater ponto.')
    linhas.append('Bater ponto e pedir ajuste são só pela tela: ' + _url('/ponto/'))
    return '\n'.join(linhas)


def _ler_periodo(user, args):
    from tangerino.client import TangerinoError

    _exige_ponto(user)
    alvo = _alvo(user, args)
    _vinculado(alvo, user)
    _integracao()
    hoje = timezone.localdate()
    fim = _data(args['ate'], 'ate') if args.get('ate') else hoje
    inicio = _data(args['de'], 'de') if args.get('de') else (hoje - timedelta(days=hoje.weekday()))
    fim = min(fim, hoje)
    if inicio > fim:
        inicio, fim = fim, inicio
    aviso = ''
    if (fim - inicio).days + 1 > MAX_DIAS_PERIODO:
        inicio = fim - timedelta(days=MAX_DIAS_PERIODO - 1)
        aviso = f' (encurtado para {MAX_DIAS_PERIODO} dias; para o mês inteiro use o_que=folha)'
    try:
        dias, _, _ = _dias(alvo, inicio, fim)
    except TangerinoError as exc:
        return f'O Tangerino não respondeu agora: {_cortar(str(exc), 200)}'
    dias.reverse()
    so = _normal(args.get('filtro'))
    linhas = [f'Ponto de {_quem(alvo, user)} — {inicio:%d/%m/%Y} a {fim:%d/%m/%Y}{aviso}',
              'Totais: ' + _totais(dias, 'periodo'),
              f'Dias com batida: {sum(1 for d in dias if d["segundos"] > 0)} · '
              f'em aberto: {sum(1 for d in dias if d["aberto"])}']
    mostrar = dias
    if so in ('aberto', 'abertos', 'em_aberto'):
        mostrar = [d for d in dias if d['aberto']]
    elif so in ('falta', 'faltas', 'sem_batida'):
        mostrar = [d for d in dias if d['sem_marcacao'] and d.get('previsto_segundos')]
    mostrar = [d for d in mostrar if not (d['sem_marcacao'] and d['fim_de_semana'] and not d.get('previsto_segundos'))]
    linhas.append('Dia a dia:')
    linhas += [_linha_dia(d) for d in mostrar[:_limite(args, 62, 62)]] or ['  nenhum dia neste recorte.']
    return '\n'.join(linhas)


def _ler_folha(user, args):
    from tangerino import ponto as ponto_svc
    from tangerino.client import TangerinoError
    from tangerino.views import _resumo_da_jornada

    _exige_ponto(user)
    alvo = _alvo(user, args, 'a folha')
    _vinculado(alvo, user)
    _integracao()
    ano, mes = _mes(args)
    hoje = timezone.localdate()
    primeiro = date(ano, mes, 1)
    ultimo = date(ano, mes, calendar.monthrange(ano, mes)[1])
    if primeiro > hoje:
        raise Invalido('Esse mês ainda não começou.')
    try:
        dias, _, grade = _dias(alvo, primeiro, min(ultimo, hoje), ttl=300)
        jornada = _resumo_da_jornada(alvo)
    except TangerinoError as exc:
        return f'O Tangerino não respondeu agora: {_cortar(str(exc), 200)}'
    dias.reverse()
    total = sum(d['segundos'] for d in dias)
    linhas = [f'Folha de ponto (ao vivo do Tangerino) — {_quem(alvo, user)} — {MESES[mes]}/{ano}',
              'Totais: ' + _totais(dias, 'mes') + f' · total {ponto_svc.formata_hhmm(total)}',
              f'Dias trabalhados: {sum(1 for d in dias if d["segundos"] > 0)} · em aberto: '
              f'{sum(1 for d in dias if d["aberto"])}']
    if jornada is not None:
        linhas.append(f'Jornada contratada: {jornada}')
    if _sim(args.get('detalhar'), True):
        linhas.append('Dia a dia:')
        linhas += [_linha_dia(d) for d in dias
                   if not (d['sem_marcacao'] and d['fim_de_semana'] and not d.get('previsto_segundos'))]
    linhas.append('Tela: ' + _url(f'/ponto/folhas/?ano={ano}&mes={mes}'
                                  + (f'&usuario={alvo.pk}' if alvo.pk != user.pk else '')))
    return '\n'.join(linhas)


def _ler_banco_horas(user, args):
    from tangerino.models import SaldoHoras

    _exige_ponto(user)
    if _sim(args.get('todos')):
        if not _e_gestor(user):
            raise Invalido('O banco de horas de todos é só para quem administra o ponto. Aqui vai só o seu.')
        qs = SaldoHoras.objects.select_related('usuario', 'usuario__sector')
        if args.get('setor_id'):
            qs = qs.filter(usuario__sector_id=args['setor_id'])
        itens = sorted(qs, key=lambda s: s.saldo_minutos)
        if not itens:
            return 'Nenhum saldo de banco de horas sincronizado ainda.'
        limite = _limite(args, 30, 200)
        devedores = [s for s in itens if s.devedor]
        linhas = [f'Banco de horas (sincronizado do Tangerino) — {len(itens)} pessoa(s), {len(devedores)} devendo horas. '
                  f'Período: {itens[0].periodo_inicio:%d/%m/%Y} a {itens[0].periodo_fim:%d/%m/%Y}.',
                  'Do mais devedor ao maior saldo:']
        linhas += [f'  {s.nome or _nome(s.usuario)}'
                   + (f' ({s.usuario.sector.name})' if s.usuario_id and s.usuario.sector_id else '')
                   + f': {s.saldo_hhmm}' for s in itens[:limite]]
        if len(itens) > limite:
            linhas.append(f'… e mais {len(itens) - limite}.')
        return '\n'.join(linhas)
    alvo = _alvo(user, args, 'o banco de horas')
    _vinculado(alvo, user)
    s = SaldoHoras.objects.filter(employee_id=alvo.tangerino_employee_id).first()
    if s is None:
        return f'Ainda não há saldo de banco de horas sincronizado para {_quem(alvo, user).lower() if alvo.pk == user.pk else _nome(alvo)}.'
    linhas = [f'Banco de horas de {_quem(alvo, user)}: {s.saldo_hhmm} '
              f'({"devendo horas" if s.devedor else "a favor"}) — calculado pelo Tangerino no período '
              f'{s.periodo_inicio:%d/%m/%Y} a {s.periodo_fim:%d/%m/%Y}, sincronizado em {timezone.localtime(s.sincronizado_em):%d/%m/%Y %H:%M}.']
    if s.tem_analise:
        linhas.append(f'Conta de conferência ({s.analise_inicio:%d/%m} a {s.analise_fim:%d/%m}): previsto {s.previsto_hhmm}, '
                      f'trabalhado {s.trabalhado_hhmm}, diferença {s.diferenca_hhmm}'
                      + (f', aproveitamento {s.aproveitamento}%' if s.aproveitamento is not None else '') + '.')
    return '\n'.join(linhas)


def _ler_ferias(user, args):
    from tangerino import ferias as ferias_svc
    from tangerino.client import TangerinoError

    _exige_ferias(user)
    if _sim(args.get('equipe')):
        if not _e_gestor(user):
            raise Invalido('As férias da equipe são só para quem administra o ponto. Aqui vão só as suas.')
        _integracao()
        try:
            p = ferias_svc.panorama_da_empresa()
        except TangerinoError as exc:
            return f'O Tangerino não respondeu agora: {_cortar(str(exc), 200)}'
        linhas_p = p['linhas']
        if args.get('setor_id'):
            from tangerino.views import _aplicar_filtro_setor

            linhas_p, _ = _aplicar_filtro_setor(linhas_p, str(args['setor_id']))
        limite = _limite(args, 25, 200)

        def bloco(titulo, itens, fmt):
            saida = [f'{titulo} ({len(itens)}):']
            saida += [f'  {fmt(l)}' for l in itens[:limite]] or ['  ninguém.']
            if len(itens) > limite:
                saida.append(f'  … e mais {len(itens) - limite}.')
            return saida

        em_gozo = [l for l in linhas_p if l['situacao']['em_gozo']]
        vencidas = sorted([l for l in linhas_p if l['situacao']['vencidos']], key=lambda l: -l['situacao']['dias_vencidos'])
        vencendo = [l for l in linhas_p if l['situacao']['vencendo']]
        programadas = [l for l in linhas_p if l['situacao']['programadas']]
        linhas = [f'Férias da equipe — {len(linhas_p)} pessoa(s) (hoje {timezone.localdate():%d/%m/%Y})']
        linhas += bloco('De férias agora', em_gozo,
                        lambda l: f'{l["nome"]}: volta em {l["situacao"]["volta_em"]:%d/%m/%Y}')
        linhas += bloco('Com dias VENCIDOS', vencidas, lambda l: f'{l["nome"]}: {l["situacao"]["dias_vencidos"]} dia(s)')
        linhas += bloco('Vencendo em breve', vencendo, lambda l: f'{l["nome"]}: ' + ', '.join(
            f'{v["saldo"]} dia(s) até {v["concessivo_fim"]:%d/%m/%Y}' for v in l['situacao']['vencendo']))
        linhas += bloco('Com férias marcadas', programadas, lambda l: f'{l["nome"]}: ' + ', '.join(
            f'{x["inicio"]:%d/%m}–{x["fim"]:%d/%m/%Y}' for x in l['situacao']['programadas'][:3]))
        return '\n'.join(linhas)

    alvo = _alvo(user, args, 'as férias')
    _vinculado(alvo, user)
    r = ferias_svc.situacao_do_usuario(alvo)
    if not r.get('disponivel'):
        return 'Não consegui consultar as férias no Tangerino agora — tente mais tarde.'
    linhas = [f'Férias de {_quem(alvo, user)} — saldo {r.get("saldo_total", 0)} dia(s)'
              + (f' · admissão {r["admissao"]:%d/%m/%Y}' if r.get('admissao') else '')]
    if r.get('dias_vencidos'):
        linhas.append(f'ATENÇÃO: {r["dias_vencidos"]} dia(s) VENCIDOS — precisam ser marcados.')
    for v in r.get('vencendo') or []:
        linhas.append(f'Vencendo: {v["saldo"]} dia(s) do período até {v["concessivo_fim"]:%d/%m/%Y} '
                      f'({v["dias_para_vencer"]} dia(s)).')
    if r.get('em_gozo'):
        linhas.append(f'De férias agora: volta em {r["volta_em"]:%d/%m/%Y} ({r["dias_restantes_gozo"]} dia(s) restantes).')
    for x in (r.get('programadas') or [])[:5]:
        linhas.append(f'Marcadas: {x["inicio"]:%d/%m/%Y} a {x["fim"]:%d/%m/%Y} ({x["status"]}).')
    passadas = r.get('passadas') or []
    if passadas:
        linhas.append('Últimas gozadas: ' + ', '.join(f'{x["inicio"]:%d/%m/%Y}–{x["fim"]:%d/%m/%Y}' for x in passadas[-3:]))
    periodos = [p for p in r.get('periodos') or [] if not p.get('sem_dados')]
    if periodos:
        linhas.append('Períodos aquisitivos:')
        for p in periodos[-4:]:
            linhas.append(f'  {p["inicio"]:%d/%m/%Y}–{p["fim"]:%d/%m/%Y}: direito {p["direito"]}, '
                          f'gozados {p["gozados"]}, saldo {p["saldo"]}, gozar até {p["concessivo_fim"]:%d/%m/%Y}'
                          + (' · VENCIDO' if p.get('vencido') else '')
                          + (' · em aquisição' if not p.get('completo') else ''))
    return '\n'.join(linhas)


def _ler_equipe(user, args):
    from tangerino import ponto as ponto_svc
    from tangerino.client import TangerinoError, listar_funcionarios
    from tangerino.views import _aplicar_filtro_setor, _setores_por_employee

    _exige_ponto(user)
    if not _e_gestor(user):
        raise Invalido('O ponto da equipe é só para quem administra o ponto (SUPERADMIN ou ADMINISTRAÇÃO).')
    _integracao()
    dia = _data(args['dia'], 'dia') if args.get('dia') else timezone.localdate()
    try:
        painel = ponto_svc.painel_da_empresa(dia=dia)
        funcionarios = {f['id']: f for f in listar_funcionarios()}
    except TangerinoError as exc:
        return f'O Tangerino não respondeu agora: {_cortar(str(exc), 200)}'
    vinculados = {u.tangerino_employee_id: u for u in User.objects.exclude(tangerino_employee_id__isnull=True)}

    def nome(eid):
        func = funcionarios.get(eid) or {}
        return func.get('name') or (vinculados[eid].full_name if eid in vinculados else f'ID {eid}')

    linhas_p = [{'employee_id': eid, 'nome': nome(eid), 'status': st} for eid, st in painel.items()]
    sem = [{'employee_id': f['id'], 'nome': f.get('name') or '', 'status': None}
           for f in funcionarios.values() if f['id'] not in painel]
    escolhido = str(args.get('setor_id') or '')
    mapa = _setores_por_employee()
    linhas_p, _ = _aplicar_filtro_setor(linhas_p, escolhido, mapa)
    sem, _ = _aplicar_filtro_setor(sem, escolhido, mapa)
    limite = _limite(args, 40, 300)
    por = {}
    for l in linhas_p:
        por.setdefault(l['status']['situacao'], []).append(l)
    rot = {'TRABALHANDO': 'Trabalhando', 'EM_INTERVALO': 'Em intervalo', 'ENCERRADO': 'Encerrado',
           'SEM_ENTRADA': 'Sem entrada'}
    linhas = [f'Ponto da equipe — {_d(dia)}' + (' · setor filtrado' if escolhido else ''),
              ' · '.join(f'{rot.get(k, k)}: {len(v)}' for k, v in sorted(por.items())) + f' · sem marcação: {len(sem)}']
    for chave in ('TRABALHANDO', 'EM_INTERVALO', 'ENCERRADO'):
        itens = sorted(por.get(chave, []), key=lambda l: l['nome'])
        if not itens:
            continue
        linhas.append(f'{rot[chave]}:')
        for l in itens[:limite]:
            st = l['status']
            setor = f' ({l["setor"].name})' if l.get('setor') else ''
            linhas.append(f'  {l["nome"]}{setor}: desde {_hm(st["primeira"]) if st["primeira"] else "—"}'
                          f' · trabalhado {st["trabalhado_hhmm"]}')
        if len(itens) > limite:
            linhas.append(f'  … e mais {len(itens) - limite}.')
    if sem:
        nomes = sorted(l['nome'] for l in sem)
        linhas.append(f'Sem marcação no dia ({len(nomes)}): ' + ', '.join(nomes[:limite])
                      + (f' … e mais {len(nomes) - limite}' if len(nomes) > limite else ''))
    return '\n'.join(linhas)


def _ler_relatorio(user, args):
    from tangerino import pendencias as pend_svc
    from tangerino import relatorio as rel_svc
    from tangerino.views import pode_ver_relatorio

    _exige_ponto(user)
    if not pode_ver_relatorio(user):
        raise Invalido('O relatório de ponto é só para quem administra o ponto ou a folha de ponto.')
    filtros = rel_svc.ler_filtros({'de': args.get('de') or '', 'ate': args.get('ate') or '',
                                   'setor': str(args.get('setor_id') or ''), 'usuario': str(args.get('usuario_id') or ''),
                                   'pendencias': '1' if _sim(args.get('so_pendencias')) else ''})
    # Buscar no Tangerino o que falta é uma chamada por pessoa: só para uma pessoa e
    # período curto. O resto lê o espelho local (a sincronização diária cobre 30 dias).
    buscar = bool(filtros['usuario']) and (filtros['ate'] - filtros['de']).days < MAX_DIAS_PERIODO
    aviso = {}
    linhas_r = pend_svc.linhas_do_periodo(
        filtros['de'], filtros['ate'], setor_id=filtros['setor'],
        usuarios=[filtros['usuario']] if filtros['usuario'] else None,
        apenas_com_pendencia=filtros['pendencias'], buscar=buscar, aviso=aviso)
    r = pend_svc.resumo(linhas_r)
    linhas = [f'Relatório de ponto — {filtros["de"]:%d/%m/%Y} a {filtros["ate"]:%d/%m/%Y}'
              + (' (encurtado para 1 ano)' if filtros['encurtado'] else '')
              + (' · só pendências' if filtros['pendencias'] else ''),
              f'{r["dias"]} linha(s) (pessoa × dia) · {r["pessoas"]} pessoa(s) · {r["com_pendencia"]} com pendência · '
              f'{r["sem_batida"]} sem nenhuma batida · previsto {r["previsto"]} · trabalhado {r["trabalhado"]} · '
              f'horas extras {r["horas_extras"]}']
    if not buscar:
        linhas.append('Lido do espelho local do ponto (a sincronização diária cobre os últimos 30 dias; dia que '
                      'ninguém buscou fica de fora). Filtre por uma pessoa (usuario_id) para completar no Tangerino.')
    if aviso.get('erro'):
        linhas.append('Aviso: ' + aviso['erro'])
    limite = _limite(args, 40, 300)
    mostrar = linhas_r if filtros['pendencias'] else ([l for l in linhas_r if l['tem_pendencia']] or linhas_r)
    if not filtros['pendencias'] and len(mostrar) < len(linhas_r):
        linhas.append('Abaixo, só as linhas com pendência (passe so_pendencias=false e um filtro para ver tudo):')
    for l in mostrar[:limite]:
        batidas = ' '.join(b for b in l['batidas'] if b) or 'sem batida'
        linhas.append(f'  {l["nome"]} · {l["loja"]} · {_d(l["data"])}: {batidas} · prev {l["previsto"]} · '
                      f'trab {l["trabalhado"]}' + (f' · {l["pendencia"]}' if l['pendencia'] else ''))
    if len(mostrar) > limite:
        linhas.append(f'… e mais {len(mostrar) - limite} linha(s). O Excel completo: ' + _url('/ponto/relatorio/'))
    return '\n'.join(linhas)


def _ler_escala(user, args):
    from tangerino import escala as escala_svc
    from tangerino.models import HORAS_SEMANAIS, Escala
    from tangerino.views import _mapa_escala_dias, _montar_grade

    base = _data(args['dia'], 'dia') if args.get('dia') else timezone.localdate()
    semana = escala_svc.monday_of(base)
    dias = [semana + timedelta(days=i) for i in range(7)]

    def cel(c):
        if c['tipo'] == 'FERIADO':
            return 'feriado'
        if c['folga']:
            return 'folga'
        if c['entrada'] and c['saida']:
            return f'{c["entrada"]:%H:%M}–{c["saida"]:%H:%M}'
        return '—'

    cabecalho = f'Escala da semana {semana:%d/%m} a {dias[-1]:%d/%m/%Y} (meta {HORAS_SEMANAIS}h/semana)'
    if escala_svc.pode_gerenciar(user) and not _sim(args.get('so_minha')):
        colaboradores = list(escala_svc.colaboradores_geridos(user, setor_id=args.get('setor_id')))
        grade = _montar_grade(colaboradores, dias, _mapa_escala_dias([c.id for c in colaboradores], semana))
        limite = _limite(args, 40, 300)
        abaixo = sum(1 for g in grade if g['abaixo'])
        linhas = [cabecalho + f' — {len(grade)} colaborador(es) que você escala, {abaixo} abaixo da meta']
        for g in grade[:limite]:
            c = g['colaborador']
            linhas.append(f'  {_nome(c)}' + (f' ({c.sector.name})' if c.sector_id else '') + ': '
                          + ' | '.join(f'{DIAS[i]} {cel(x)}' for i, x in enumerate(g['celulas']))
                          + f' · {g["horas"]:.1f}h' + (' · ABAIXO da meta' if g['abaixo'] else ''))
        if len(grade) > limite:
            linhas.append(f'… e mais {len(grade) - limite}.')
        return '\n'.join(linhas)
    obj = Escala.objects.filter(colaborador=user, semana_inicio=semana).prefetch_related('dias').first()
    por_data = {d.data: d for d in obj.dias.all()} if obj else {}
    if not any(por_data.get(d) and por_data[d].preenchido for d in dias):
        return cabecalho + ' — a sua ainda não foi montada pelo gerente.'
    minutos = sum(por_data[d].minutos for d in dias if por_data.get(d))
    linhas = [cabecalho + f' — a sua: {minutos / 60:.1f}h' + (' (abaixo da meta)' if minutos < HORAS_SEMANAIS * 60 else '')]
    for i, d in enumerate(dias):
        ed = por_data.get(d)
        if ed is None or not ed.preenchido:
            texto = '—'
        elif ed.feriado or ed.folga:
            texto = 'feriado' if ed.feriado else 'folga'
        else:
            partes = [('entrada', ed.entrada), ('intervalo', ed.saida_almoco), ('volta', ed.volta_almoco),
                      ('saída', ed.saida)]
            texto = ', '.join(f'{r} {h:%H:%M}' for r, h in partes if h) + f' ({ed.minutos / 60:.1f}h)'
        linhas.append(f'  {DIAS[i]} {d:%d/%m}: {texto}' + (f' · {ed.observacao}' if ed and ed.observacao else ''))
    return '\n'.join(linhas)


def _ler_ajustes(user, args):
    from tangerino import ajustes as ajustes_svc
    from tangerino.models import SolicitacaoAjustePonto as Sol

    config = _config()
    if not ajustes_svc.ligado(config):
        return 'O ajuste de ponto pelo portal está desligado (é ligado pelo SUPERADMIN em /ponto/configuracao/).'
    limite = _limite(args, 15, 100)

    def linha(s, com_quem=False):
        quem = f'{_nome(s.usuario)} · ' if com_quem else ''
        return (f'  #{s.pk} · {quem}{s.get_tipo_display()} em {_d(s.dia)}: {s.resumo} · {s.get_status_display()}'
                + (f' · motivo: {_cortar(s.motivo, 120)}' if s.motivo else '')
                + (f' · resposta: {_cortar(s.resposta, 120)}' if s.resposta else ''))

    if _sim(args.get('fila')):
        if not ajustes_svc.e_responsavel(user, config):
            raise Invalido('A fila de ajustes é do setor responsável pelo ponto. Aqui vão só os seus pedidos.')
        base = Sol.objects.select_related('usuario', 'decidido_por')
        pend = list(base.filter(status__in=[Sol.PENDENTE, Sol.ERRO]).order_by('criado_em')[:limite])
        hist = list(base.exclude(status__in=[Sol.PENDENTE, Sol.ERRO])[:limite])
        linhas = [f'Fila de ajustes de ponto — {len(pend)} aguardando:']
        linhas += [linha(s, True) for s in pend] or ['  nada pendente.']
        linhas.append('Decididos recentemente:')
        linhas += [linha(s, True) for s in hist] or ['  nenhum.']
        linhas.append('Aprovar ou recusar é só pela tela: ' + _url('/ponto/ajustes/'))
        return '\n'.join(linhas)
    meus = list(user.ajustes_de_ponto.select_related('decidido_por')[:limite])
    if not meus:
        return 'Você não tem pedidos de ajuste de ponto. Pedir ajuste é pela tela: ' + _url('/ponto/')
    return '\n'.join([f'Seus pedidos de ajuste de ponto ({len(meus)}):'] + [linha(s) for s in meus])


def _ler_solicitacoes(user, args):
    from solicitacoes_dp.models import Solicitacao as S
    from solicitacoes_dp.permissoes import e_analista

    limite = _limite(args, 15, 100)

    def linha(s, com_quem=False):
        quem = f'{_nome(s.colaborador)} · ' if com_quem else ''
        periodo = _d(s.data) + (f' a {_d(s.data_fim)}' if s.data_fim else '')
        return (f'  #{s.pk} · {quem}{s.get_tipo_display()}' + (f' ({s.get_especie_display()})' if s.especie else '')
                + f' · {periodo} · {s.get_status_display()}'
                + (' · lançado no ponto' if s.ajustado_no_ponto else '')
                + (f' · resposta do DP: {_cortar(s.resposta, 120)}' if s.resposta else ''))

    if _sim(args.get('fila')):
        if not e_analista(user):
            raise Invalido('A caixa do DP é para quem analisa as solicitações. Aqui vão só as suas.')
        base = S.objects.select_related('colaborador')
        filtro = _normal(args.get('status') or 'pendentes')
        if filtro == 'pendentes':
            lista = base.filter(status__in=(S.ENVIADA, S.EM_ANALISE)).order_by('enviada_em')
        elif filtro == 'devolvidas':
            lista = base.filter(status=S.DEVOLVIDA)
        elif filtro == 'lancar':
            lista = base.filter(tipo=S.AJUSTE, status=S.APROVADA, ajustado_no_ponto=False).order_by('aprovada_em')
        elif filtro == 'aprovadas':
            lista = base.filter(status=S.APROVADA).order_by('-aprovada_em')
        else:
            lista = base.order_by('-enviada_em')
        if args.get('busca'):
            q = str(args['busca']).strip()
            lista = lista.filter(Q(colaborador__first_name__icontains=q) | Q(colaborador__last_name__icontains=q))
        total = lista.count()
        linhas = [f'Caixa do DP — {filtro}: {total} solicitação(ões)']
        linhas += [linha(s, True) for s in lista[:limite]] or ['  nenhuma.']
        linhas.append('Analisar, aprovar e devolver é só pela tela: ' + _url('/ponto/dp/'))
        return '\n'.join(linhas)
    minhas = S.objects.filter(colaborador=user).order_by('-enviada_em')
    abertas = minhas.filter(status__in=S.ABERTOS).count()
    itens = list(minhas[:limite])
    if not itens:
        return 'Você não tem solicitações ao DP (ajuste de ponto, atestado, declaração). Nova: ' + _url('/ponto/solicitacoes/')
    return '\n'.join([f'Suas solicitações ao DP — {abertas} em aberto:'] + [linha(s) for s in itens])


def _ler_pessoas(user, args):
    _exige_ponto(user)
    if not _e_gestor(user):
        return (f'Você consulta só o próprio ponto: {_nome(user)} (id {user.pk})'
                + ('' if user.tangerino_employee_id else ' — ainda sem vínculo com o Tangerino') + '.')
    qs = (User.objects.filter(is_active=True, tangerino_employee_id__isnull=False)
          .select_related('sector').order_by('first_name', 'last_name'))
    if args.get('setor_id'):
        qs = qs.filter(sector_id=args['setor_id'])
    if args.get('busca'):
        for parte in str(args['busca']).split():
            qs = qs.filter(Q(first_name__icontains=parte) | Q(last_name__icontains=parte))
    limite = _limite(args, 30, 300)
    itens = list(qs[:limite + 1])
    linhas = ['Pessoas com ponto vinculado (use o id em usuario_id):']
    linhas += [f'  {_nome(u)} (id {u.pk})' + (f' · {u.sector.name}' if u.sector_id else '') for u in itens[:limite]]
    if len(itens) > limite:
        linhas.append('… há mais: refine com busca ou setor_id.')
    return '\n'.join(linhas) if itens else 'Ninguém com ponto vinculado nesse recorte.'


LEITURAS = {
    'hoje': _ler_hoje, 'periodo': _ler_periodo, 'folha': _ler_folha, 'banco_horas': _ler_banco_horas,
    'ferias': _ler_ferias, 'equipe': _ler_equipe, 'relatorio': _ler_relatorio, 'escala': _ler_escala,
    'ajustes': _ler_ajustes, 'solicitacoes': _ler_solicitacoes, 'pessoas': _ler_pessoas,
}
APELIDOS = {'dia': 'hoje', 'semana': 'periodo', 'batidas': 'periodo', 'marcacoes': 'periodo', 'mes': 'folha',
            'folhas': 'folha', 'saldo': 'banco_horas', 'banco': 'banco_horas', 'banco_de_horas': 'banco_horas',
            'ferias_equipe': 'ferias', 'ponto_equipe': 'equipe', 'pendencias': 'relatorio', 'ajuste': 'ajustes',
            'dp': 'solicitacoes', 'atestados': 'solicitacoes', 'solicitacao': 'solicitacoes'}


def _ponto_consultar(user, args):
    o_que = _normal(args.get('o_que') or 'hoje').replace(' ', '_').replace('-', '_')
    if o_que == 'ferias_equipe':
        args = dict(args, equipe=True)
    leitura = LEITURAS.get(APELIDOS.get(o_que, o_que))
    if leitura is None:
        raise Invalido('o_que inválido. Use: ' + ' | '.join(LEITURAS) + '.')
    return leitura(user, args)


TOOLS = {
    'ponto_consultar': {
        'fn': _ponto_consultar,
        'description': (
            'Ponto e férias (/ponto/, Tangerino/Sólides), SÓ LEITURA, com as permissões que o usuário tem na tela. '
            'o_que: hoje (situação e batidas do dia, ponto em aberto) | periodo (dia a dia com batidas, previsto x '
            'trabalhado e saldo; de/ate, até 62 dias; filtro=abertos|faltas) | folha (mês inteiro ao vivo; ano, mes) '
            '| banco_horas (saldo do Tangerino; todos=true para gestor) | ferias (saldo, vencidas, marcadas; '
            'equipe=true para gestor) | equipe (quem está trabalhando/intervalo/sem marcação no dia; gestor) | '
            'relatorio (pendências por pessoa e dia; de/ate/setor_id/usuario_id/so_pendencias; quem administra o '
            'ponto ou a folha) | escala (semana; gerente vê a equipe que escala) | ajustes (pedidos de ajuste; '
            'fila=true para o setor responsável) | solicitacoes (ajuste/atestado/declaração ao DP; fila=true para o '
            'DP) | pessoas (ids para usuario_id). Todos veem o próprio; usuario_id de outra pessoa só para quem '
            'administra o ponto (SUPERADMIN/ADMINISTRAÇÃO). Bater ponto, pedir/aprovar ajuste e responder ao DP '
            'são só pela tela.'),
        'input_schema': _obj(
            ['o_que'],
            o_que=_txt('hoje | periodo | folha | banco_horas | ferias | equipe | relatorio | escala | ajustes | '
                       'solicitacoes | pessoas.'),
            usuario_id=_int('Outra pessoa (só para quem administra o ponto); padrão: o próprio usuário.'),
            de=_txt('Início AAAA-MM-DD (periodo, relatorio).'), ate=_txt('Fim AAAA-MM-DD (periodo, relatorio).'),
            dia=_txt('Dia AAAA-MM-DD (equipe; escala usa a semana desse dia).'),
            ano=_int('Ano (folha).'), mes=_int('Mês 1–12 (folha).'),
            setor_id=_int('Filtrar por setor/loja (equipe, relatorio, escala, ferias equipe, banco_horas todos).'),
            filtro=_txt('periodo: abertos | faltas.'),
            so_pendencias=_bool('relatorio: só linhas com pendência.'),
            equipe=_bool('ferias: panorama da equipe (gestor).'), todos=_bool('banco_horas: todos (gestor).'),
            fila=_bool('ajustes/solicitacoes: a fila de quem decide.'),
            status=_txt('solicitacoes fila: pendentes | devolvidas | lancar | aprovadas | todas.'),
            so_minha=_bool('escala: só a minha, mesmo sendo gerente.'),
            detalhar=_bool('folha: listar dia a dia (padrão sim).'),
            busca=_txt('Parte do nome (pessoas, solicitacoes fila).'),
            limite=_int('Quantas linhas listar.')),
    },
}
