"""Os 8 requisitos do caminho do consultor até Pleno — de onde vem cada número e quando está cumprido.

| Requisito  | Como é medido                                                   | De onde vem                                   |
|------------|-----------------------------------------------------------------|-----------------------------------------------|
| Tempo      | 12 meses como Jr (admissão ou última promoção; férias, licença  | ``User.admission_date`` / ``PerfilCarreira``  |
|            | e INSS contam — por isso é só o calendário)                     |                                               |
| Resultado  | média dos últimos 6 meses em 100% ou mais nos 7 pilares         | lançado pela Inteligência (SUPERADMIN)        |
| Satisfação | nota de 4,2 ou mais na pesquisa pós-venda                        | lançado (SUPERADMIN)                          |
| Qualidade  | até 5% de erro de lançamento nos últimos 3 meses (D-1)           | auditoria D-1 (MySQL), ou lançado             |
| Assiduidade| absenteísmo de até 2% das horas previstas em 3 meses             | ponto do Tangerino (``tangerino.pendencias``) |
| Conduta    | nenhuma advertência escrita nos últimos 3 meses                  | ``Advertencia``                               |
| Formação   | as trilhas escolhidas pelo SUPERADMIN, concluídas                | ``knowledge_trails.TrailProgress``            |
| Loja apta  | rentabilidade final da loja acima de 20% no trimestre            | ``LojaApta`` (o consultor não vê o número)    |

Cada requisito sai como um dicionário com ``situacao`` em ``ok`` (cumprido), ``falta`` (ainda não) ou
``aguardando`` (sem dado ainda — não conta contra, mas também não conclui).
"""
import logging
from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone

from .models import Advertencia, ConfigCarreira, LojaApta, PedidoPromocao, PerfilCarreira

logger = logging.getLogger(__name__)

MESES_COMO_JR = 12
RESULTADO_MINIMO = Decimal('100')
SATISFACAO_MINIMA = Decimal('4.2')
ERRO_MAXIMO = Decimal('5')
ABSENTEISMO_MAXIMO = Decimal('2')
JANELA_DIAS = 90                    # "últimos 3 meses" / "ciclo de 3 meses"
NAO_AGORA_ESPERA_DIAS = 90          # depois de um "ainda não", o próximo pedido só sai no trimestre seguinte
TEMPO_CACHE = 6 * 3600
TOLERANCIA_DIA = 10 * 60            # CLT: até 10 min de variação no dia não é atraso
OK, FALTA, AGUARDANDO = 'ok', 'falta', 'aguardando'


def e_superadmin(user):
    return bool(user and getattr(user, 'is_authenticated', False)
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def e_do_comercial(user):
    """Consultor do comercial: usuário Padrão ativo, lotado numa loja (setor principal)."""
    return bool(user and getattr(user, 'is_authenticated', False) and user.is_active
                and getattr(user, 'hierarchy', '') == 'PADRAO'
                and getattr(user, 'sector_id', None) and 'loja' in (user.sector.name or '').lower())


CHAVE_LIBERADO = 'carreira:liberado:v1'


def liberado():
    """A chave "liberado para o comercial" (o menu pergunta em toda página: fica 1 min em cache)."""
    valor = cache.get(CHAVE_LIBERADO)
    if valor is None:
        valor = bool(ConfigCarreira.objects.filter(pk=1, liberado=True).exists())
        cache.set(CHAVE_LIBERADO, valor, 60)
    return valor


def pode_ver_modulo(user):
    """SUPERADMIN sempre; o comercial só depois que o SUPERADMIN libera."""
    if e_superadmin(user):
        return True
    if not user or getattr(user, 'hierarchy', '') != 'PADRAO':
        return False
    return liberado() and e_do_comercial(user)


def perfil_de(user):
    perfil, _ = PerfilCarreira.objects.get_or_create(user=user)
    return perfil


def _meses_entre(inicio, fim):
    meses = (fim.year - inicio.year) * 12 + (fim.month - inicio.month)
    if fim.day < inicio.day:
        meses -= 1
    return max(0, meses)


def _mais_meses(dia, meses):
    ano, mes = divmod(dia.month - 1 + meses, 12)
    ano, mes = dia.year + ano, mes + 1
    import calendar
    return date(ano, mes, min(dia.day, calendar.monthrange(ano, mes)[1]))


def _fmt_pct(valor, casas=1):
    return f'{Decimal(valor):.{casas}f}'.replace('.', ',') + '%'


def _fmt_data(dia):
    return dia.strftime('%d/%m/%Y') if dia else ''


# ---------------------------------------------------------------------------
# Cada requisito
# ---------------------------------------------------------------------------
def _tempo(user, perfil, hoje):
    inicio = max([d for d in (user.admission_date, perfil.ultima_promocao) if d], default=None)
    base = {'chave': 'tempo', 'titulo': '12 meses como Jr', 'icone': 'fa-hourglass-half', 'cor': '#6366F1',
            'como': 'Data de admissão ou da última promoção; férias, licença e INSS contam.', 'onde': 'Portal (RH)'}
    if not inicio:
        return {**base, 'situacao': AGUARDANDO, 'progresso': 0, 'valor': 'Sem data de admissão no cadastro',
                'dica': 'Peça ao RH para conferir sua data de admissão.'}
    meses = _meses_entre(inicio, hoje)
    pronto_em = _mais_meses(inicio, MESES_COMO_JR)
    ok = meses >= MESES_COMO_JR
    return {**base, 'situacao': OK if ok else FALTA, 'progresso': min(1, meses / MESES_COMO_JR),
            'valor': (f'{meses} meses ({MESES_COMO_JR} exigidos)' if ok else f'{meses} de {MESES_COMO_JR} meses'),
            'dica': (f'Desde {_fmt_data(inicio)}.' if ok
                     else f'Faltam {MESES_COMO_JR - meses} {"mês" if MESES_COMO_JR - meses == 1 else "meses"} — completa em {_fmt_data(pronto_em)}.')}


def _resultado(user, perfil, hoje):
    base = {'chave': 'resultado', 'titulo': 'Resultado', 'icone': 'fa-chart-line', 'cor': '#0EA5E9',
            'como': 'Média dos últimos 6 meses em 100% ou mais nos 7 pilares, no comissionamento pós-contestação.',
            'onde': 'Portal (Inteligência)'}
    if perfil.resultado_media is not None:
        media, pilares = perfil.resultado_media, []
        ref = f' ({perfil.resultado_referencia})' if perfil.resultado_referencia else ''
        origem = ' · lançado pelo SUPERADMIN'
    elif perfil.resultado_auto is not None:
        media, pilares = perfil.resultado_auto, perfil.resultado_auto_pilares or []
        ref = f' ({perfil.resultado_auto_meses})' if perfil.resultado_auto_meses else ''
        origem = ''
    else:
        return {**base, 'situacao': AGUARDANDO, 'progresso': 0,
                'valor': 'Aguardando o comissionamento pós-contestação',
                'dica': 'A média dos 7 pilares entra aqui assim que o comissionamento dos seus meses for fechado.'}
    ok = media >= RESULTADO_MINIMO
    return {**base, 'situacao': OK if ok else FALTA, 'progresso': float(min(1, media / RESULTADO_MINIMO)),
            'valor': f'Média de {_fmt_pct(media)}{ref}{origem}',
            'pilares': [{**p, 'ok': p['media'] >= float(RESULTADO_MINIMO)} for p in pilares],
            'dica': 'Meta batida nos 7 pilares. Continue assim!' if ok
            else f'Faltam {_fmt_pct(RESULTADO_MINIMO - media)} na média para chegar a 100%.'}


def _satisfacao(user, perfil, hoje):
    base = {'chave': 'satisfacao', 'titulo': 'Satisfação do cliente', 'icone': 'fa-face-smile', 'cor': '#F59E0B',
            'como': 'Nota de 4,2 ou mais na pesquisa pós-venda.', 'onde': 'Portal'}
    nota = perfil.satisfacao_nota
    if nota is None:
        return {**base, 'situacao': AGUARDANDO, 'progresso': 0, 'valor': 'Aguardando a nota da pesquisa',
                'dica': 'A nota da pesquisa pós-venda entra aqui quando for apurada.'}
    ok = nota >= SATISFACAO_MINIMA
    texto = f'{nota:.2f}'.replace('.', ',')
    return {**base, 'situacao': OK if ok else FALTA, 'progresso': float(min(1, nota / SATISFACAO_MINIMA)),
            'valor': f'Nota {texto}', 'dica': 'Seus clientes estão satisfeitos!' if ok
            else f'Faltam {(SATISFACAO_MINIMA - nota):.2f}'.replace('.', ',') + ' ponto(s) para 4,2.'}


def erro_d1(user, hoje):
    """(vendas, erros) do vendedor no D-1 dos últimos 90 dias, ou None se não deu para ler. Guardado 6 h."""
    chave = f'carreira:d1:{user.pk}:{hoje:%Y%m%d}'
    guardado = cache.get(chave)
    if guardado is not None:
        return tuple(guardado) if guardado else None
    resultado = None
    try:
        from auditoria_d1 import mysql as d1
        esc, params = d1.filtro_escopo()
        inicio, fim = hoje - timedelta(days=JANELA_DIAS), hoje - timedelta(days=1)
        nomes = [n for n in {(user.full_name or '').strip(), f'{user.first_name} {user.last_name}'.strip()} if n]
        for nome in nomes:
            linhas = d1.consultar(
                f"""SELECT COUNT(*) AS vendas, COALESCE(SUM(v.status = 'DIVERGENTE'), 0) AS erros
                    FROM vendas_servicos s
                    LEFT JOIN verificacao_vendas v ON v.id_servico = s.`ID_do_serviço`
                    WHERE s.`data_da_venda` BETWEEN %s AND %s AND s.`Nome_do_vendedor` = %s AND {esc}""",
                [inicio, fim, nome] + list(params))
            if linhas and int(linhas[0]['vendas'] or 0):
                resultado = (int(linhas[0]['vendas']), int(linhas[0]['erros'] or 0))
                break
    except Exception:  # noqa: BLE001 — sem o D-1, o requisito fica aguardando
        logger.warning('Carreira: D-1 não lido para %s', user.pk, exc_info=True)
        return None
    cache.set(chave, list(resultado) if resultado else [], TEMPO_CACHE)
    return resultado


def _qualidade(user, perfil, hoje):
    base = {'chave': 'qualidade', 'titulo': 'Qualidade', 'icone': 'fa-clipboard-check', 'cor': '#10B981',
            'como': 'Até 5% de erro de lançamento nos últimos 3 meses (D-1).', 'onde': 'Portal (Validação D-1)'}
    if perfil.qualidade_manual is not None:
        erro, origem = perfil.qualidade_manual, 'lançado pelo SUPERADMIN'
    else:
        d1 = erro_d1(user, hoje)
        if not d1:
            return {**base, 'situacao': AGUARDANDO, 'progresso': 0, 'valor': 'Sem vendas no D-1 nos últimos 3 meses',
                    'dica': 'O D-1 confere suas vendas todo dia; o percentual aparece aqui.'}
        vendas, erros = d1
        erro = Decimal(erros * 100) / Decimal(vendas)
        origem = f'{erros} erro(s) em {vendas} venda(s)'
    ok = erro <= ERRO_MAXIMO
    return {**base, 'situacao': OK if ok else FALTA,
            'progresso': 1 if ok else float(max(Decimal('0.05'), ERRO_MAXIMO / erro)),
            'valor': f'{_fmt_pct(erro)} de erro · {origem}',
            'dica': 'Lançamentos certinhos!' if ok else 'Capriche na conferência antes de lançar: o limite é 5%.'}


def absenteismo(user, hoje):
    """Absenteísmo dos últimos 90 dias: {'pct', 'dias_sem_batida', 'minutos_atraso'}, ou None.

    É a mesma leitura do relatório de ponto do DP (``tangerino.pendencias``): dia sem batida nenhuma conta inteiro;
    atraso e saída antes da hora contam o que passa da tolerância de 10 min; dia com batida esquecida fica fora
    (não é ausência); feriado, férias, atestado e folga chegam como abono e não entram.
    """
    if not getattr(user, 'tangerino_employee_id', None):
        return None
    chave = f'carreira:ponto:v2:{user.pk}:{hoje:%Y%m%d}'
    guardado = cache.get(chave)
    if guardado is not None:
        return ({**guardado, 'pct': Decimal(guardado['pct'])} if guardado else None)
    valor = None
    try:
        from tangerino import pendencias
        linhas = pendencias.linhas_do_periodo(hoje - timedelta(days=JANELA_DIAS), hoje - timedelta(days=1),
                                              usuarios=[user], buscar=False)
        conta = pendencias.absenteismo_das_linhas(linhas, TOLERANCIA_DIA).get(user.pk)
        if conta:
            valor = {'pct': conta['pct'], 'dias_sem_batida': conta['dias_sem_batida'],
                     'minutos_atraso': conta['minutos_atraso']}
    except Exception:  # noqa: BLE001 — sem o ponto, o requisito fica aguardando
        logger.warning('Carreira: ponto não lido para %s', user.pk, exc_info=True)
        return None
    cache.set(chave, {**valor, 'pct': str(valor['pct'])} if valor else {}, TEMPO_CACHE)
    return valor


def _assiduidade(user, perfil, hoje):
    base = {'chave': 'assiduidade', 'titulo': 'Assiduidade', 'icone': 'fa-calendar-check', 'cor': '#8B5CF6',
            'como': 'Absenteísmo de até 2% das horas previstas em 3 meses (cerca de 1 dia): faltas '
                    'injustificadas, atrasos e saídas antecipadas sem abono. Atestados e ausências legais não contam.',
            'onde': 'Portal (DP)'}
    detalhe = ''
    if perfil.assiduidade_manual is not None:
        valor, detalhe = perfil.assiduidade_manual, ' · lançado pelo SUPERADMIN'
    else:
        ponto = absenteismo(user, hoje)
        valor = ponto['pct'] if ponto else None
        if ponto:
            partes = []
            if ponto['dias_sem_batida']:
                partes.append(f"{ponto['dias_sem_batida']} dia(s) sem batida")
            if ponto['minutos_atraso']:
                partes.append(f"{ponto['minutos_atraso']} min de atraso/saída antes")
            detalhe = (' · ' + ', '.join(partes)) if partes else ''
    if valor is None:
        return {**base, 'situacao': AGUARDANDO, 'progresso': 0, 'valor': 'Sem ponto nos últimos 3 meses',
                'dica': 'O absenteísmo sai do ponto; aparece aqui assim que houver marcações.'}
    ok = valor <= ABSENTEISMO_MAXIMO
    return {**base, 'situacao': OK if ok else FALTA,
            'progresso': 1 if ok else float(max(Decimal('0.05'), ABSENTEISMO_MAXIMO / valor)),
            'valor': f'{_fmt_pct(valor)} das horas previstas{detalhe}', 'dica': 'Presença em dia!' if ok
            else 'Atrasos e saídas antes da hora somam: o limite é 2% em 3 meses.'}


def _conduta(user, perfil, hoje):
    base = {'chave': 'conduta', 'titulo': 'Conduta', 'icone': 'fa-shield-heart', 'cor': '#EF4444',
            'como': 'Nenhuma advertência escrita nos últimos 3 meses.', 'onde': 'Portal (RH)'}
    ultima = Advertencia.objects.filter(user=user, data__gt=hoje - timedelta(days=JANELA_DIAS)).order_by('-data').first()
    if ultima is None:
        return {**base, 'situacao': OK, 'progresso': 1, 'valor': 'Nenhuma advertência nos últimos 3 meses',
                'dica': 'Conduta exemplar!'}
    livre_em = ultima.data + timedelta(days=JANELA_DIAS)
    passados = (hoje - ultima.data).days
    return {**base, 'situacao': FALTA, 'progresso': max(0.05, passados / JANELA_DIAS),
            'valor': f'Advertência em {_fmt_data(ultima.data)}', 'dica': f'Libera em {_fmt_data(livre_em)}.'}


def _formacao(user, perfil, hoje, config):
    from knowledge_trails.models import TrailProgress
    base = {'chave': 'formacao', 'titulo': 'Formação', 'icone': 'fa-graduation-cap', 'cor': '#EC4899',
            'como': 'Trilha do consultor e formação de responsável de loja concluídas.', 'onde': 'Portal (trilha)'}
    trilhas = list(config.trilhas.all())
    if not trilhas:
        return {**base, 'situacao': AGUARDANDO, 'progresso': 0, 'valor': 'Trilhas ainda não definidas',
                'dica': 'As trilhas da formação aparecem aqui assim que forem escolhidas.', 'trilhas': []}
    feitas = set(TrailProgress.objects.filter(user=user, trail__in=trilhas, status='completed')
                 .values_list('trail_id', flat=True))
    itens = [{'id': t.pk, 'nome': str(getattr(t, 'title', t)), 'feita': t.pk in feitas} for t in trilhas]
    ok = len(feitas) == len(trilhas)
    return {**base, 'situacao': OK if ok else FALTA, 'progresso': len(feitas) / len(trilhas),
            'valor': f'{len(feitas)} de {len(trilhas)} trilha(s) concluída(s)', 'trilhas': itens,
            'dica': 'Formação completa!' if ok else 'Conclua as trilhas que faltam no módulo de Trilhas.'}


def trimestre_de(dia):
    return dia.year, (dia.month - 1) // 3 + 1


def _loja(user, perfil, hoje, mostrar_numero):
    base = {'chave': 'loja', 'titulo': 'Loja apta', 'icone': 'fa-store', 'cor': '#14B8A6',
            'como': 'Rentabilidade final da loja acima de 20%, validada por trimestre (o número fica com o comitê).',
            'onde': 'Gerente, no comitê'}
    registro = (LojaApta.objects.filter(setor_id=user.sector_id).order_by('-ano', '-trimestre').first()
                if user.sector_id else None)
    if registro is None:
        return {**base, 'situacao': AGUARDANDO, 'progresso': 0, 'valor': 'Aguardando o comitê do trimestre',
                'dica': 'O comitê valida a loja a cada trimestre.'}
    numero = (f' · rentabilidade {_fmt_pct(registro.rentabilidade)}'
              if mostrar_numero and registro.rentabilidade is not None else '')
    quando = f'{registro.trimestre}º tri/{registro.ano}'
    if registro.apta:
        return {**base, 'situacao': OK, 'progresso': 1, 'valor': f'Loja apta no {quando}{numero}',
                'dica': 'Sua loja está apta.'}
    return {**base, 'situacao': FALTA, 'progresso': 0.3, 'valor': f'Loja ainda não apta no {quando}{numero}',
            'dica': 'Em loja não apta, o caminho pode passar por mudar de loja — converse com seu gerente.'}


# ---------------------------------------------------------------------------
# A jornada inteira
# ---------------------------------------------------------------------------
def avaliar(user, hoje=None, para_superadmin=False):
    """A jornada da pessoa: os 8 requisitos, quantos cumpridos, nível e o pedido de promoção."""
    hoje = hoje or timezone.localdate()
    perfil = perfil_de(user)
    config = ConfigCarreira.atual()
    requisitos = [
        _tempo(user, perfil, hoje), _resultado(user, perfil, hoje), _satisfacao(user, perfil, hoje),
        _qualidade(user, perfil, hoje), _assiduidade(user, perfil, hoje), _conduta(user, perfil, hoje),
        _formacao(user, perfil, hoje, config), _loja(user, perfil, hoje, para_superadmin),
    ]
    for i, r in enumerate(requisitos, 1):
        r['numero'] = i
        r['progresso_pct'] = round(r['progresso'] * 100)
    cumpridos = sum(1 for r in requisitos if r['situacao'] == OK)
    pedido = PedidoPromocao.objects.filter(user=user).order_by('-criado_em').first()
    return {
        'perfil': perfil, 'requisitos': requisitos, 'cumpridos': cumpridos, 'total': len(requisitos),
        'percentual': round(cumpridos * 100 / len(requisitos)),
        'faltam': len(requisitos) - cumpridos,
        'xp': sum(round(r['progresso'] * 125) for r in requisitos),   # 8 x 125 = 1000 XP
        'xp_total': 125 * len(requisitos),
        'concluiu': cumpridos == len(requisitos),
        'pleno': perfil.nivel == PerfilCarreira.PLENO,
        'pedido': pedido,
        'hoje': hoje,
    }


def guardar_retrato(jornada):
    """Guarda o resumo no perfil (o painel do SUPERADMIN lê dele, sem refazer as contas de todo mundo)."""
    perfil = jornada['perfil']
    perfil.retrato = {'cumpridos': jornada['cumpridos'], 'total': jornada['total'],
                      'requisitos': {r['chave']: r['situacao'] for r in jornada['requisitos']}}
    perfil.retrato_em = timezone.now()
    PerfilCarreira.objects.filter(pk=perfil.pk).update(retrato=perfil.retrato, retrato_em=perfil.retrato_em)


def pedir_promocao_se_concluiu(user, jornada):
    """Concluiu os 8 e ainda é Jr: abre o pedido (uma vez) e avisa os SUPERADMIN. Devolve o pedido novo ou None."""
    if not jornada['concluiu'] or jornada['pleno']:
        return None
    ultimo = jornada['pedido']
    if ultimo is not None:
        if ultimo.situacao in (PedidoPromocao.ANALISE, PedidoPromocao.PROMOVIDO):
            return None
        if ultimo.decidido_em and timezone.now() - ultimo.decidido_em < timedelta(days=NAO_AGORA_ESPERA_DIAS):
            return None
    resumo = [{'chave': r['chave'], 'titulo': r['titulo'], 'valor': r['valor'], 'situacao': r['situacao']}
              for r in jornada['requisitos']]
    pedido = PedidoPromocao.objects.create(user=user, requisitos=resumo)
    jornada['pedido'] = pedido
    _avisar_superadmins(user, pedido)
    return pedido


def _avisar_superadmins(user, pedido):
    try:
        from django.contrib.auth import get_user_model
        from django.db.models import Q
        from django.urls import reverse

        from core.models import Notification
        url = reverse('carreira:pessoa', args=[user.pk])
        nome = user.full_name or user.get_username()
        for sa in get_user_model().objects.filter(is_active=True).filter(Q(is_superuser=True) | Q(hierarchy='SUPERADMIN')):
            Notification.objects.create(user=sa, title='Pedido de promoção a Pleno',
                                        message=f'{nome} cumpriu os 8 requisitos do Pleno. Analise e decida.',
                                        notification_type='SYSTEM', related_object_id=pedido.pk, related_url=url)
    except Exception:  # noqa: BLE001 — o aviso não segura o pedido
        logger.warning('Carreira: aviso do pedido %s não enviado', pedido.pk, exc_info=True)


def avisar_pessoa(user, titulo, mensagem):
    try:
        from django.urls import reverse

        from core.models import Notification
        Notification.objects.create(user=user, title=titulo, message=mensagem, notification_type='SYSTEM',
                                    related_url=reverse('carreira:jornada'))
    except Exception:  # noqa: BLE001
        logger.warning('Carreira: aviso para %s não enviado', user.pk, exc_info=True)
