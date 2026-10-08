"""Telas da Visão SAP: a lista da auditoria e o painel administrativo."""
import logging
from datetime import datetime
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Abs, Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .espelho import sincronizar, ultima_sincronizacao
from .models import (LIMITE_VALOR_DESPREZIVEL, LinhaAuditoria, SincronizacaoAuditoria,
                     filtro_aguardando_leitura, filtro_resolvidas_pendentes, inicio_da_ultima_leitura,
                     q_valor_desprezivel)
from .mysql import SapIndisponivel
from .permissions import e_gestor as _e_gestor
from .permissions import (CHAVE_GESTOR, gestores_escolhidos, lojas_visiveis, normalizar_loja,
                          pode_escolher_gestores)
from .permissions import pode_ver as _pode_ver

logger = logging.getLogger(__name__)
ZERO = Decimal('0.00')
POR_PAGINA = 50

SITUACOES = (('abertas', 'Só as abertas'), ('pendentes', 'Resolvidas, mas ainda pendentes'),
             ('aguardando', 'Resolvidas, aguardando a próxima leitura'), ('resolvidas', 'Só as resolvidas'),
             ('todas', 'Todas'))
PRESENCAS = (('na', 'Ainda na auditoria'), ('sairam', 'Já saíram do SAP'), ('todas', 'Todas'))
ORDENS = (
    ('recente', 'Venda mais recente'),
    ('antiga', 'Venda mais antiga'),
    ('diferenca', 'Maior diferença de valor'),
    ('loja', 'Loja'),
    ('tipo', 'Tipo do erro'),
)
COLUNAS_DE_BUSCA = ('id_venda', 'nome_cliente', 'documento_vivogo', 'serial_vivogo',
                    'serial_sap', 'produto_vivogo', 'produto_sap', 'num_fat_sap',
                    'sku_vivogo', 'ordem_sap', 'pdv')
COMPARACOES = (('documento', 'status_documento'), ('produto', 'status_produto'),
               ('valor', 'status_valor'), ('loja', 'status_loja'), ('data', 'status_data'))


def so_auditoria(view):
    """Trava de tela: quem não pode ver a auditoria não entra nem pela URL."""
    @wraps(view)
    def _view(request, *args, **kwargs):
        if not _pode_ver(request.user):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'ok': False, 'erro': 'Sem acesso à Visão SAP.'}, status=403)
            messages.error(request, 'A Visão SAP é restrita à administração.')
            return redirect('home')
        return view(request, *args, **kwargs)
    return _view


def pdvs_visiveis(user):
    """None quando a pessoa vê a rede; senão os PDVs do espelho que são dela.

    O SAP escreve a loja do seu jeito ("GLÓRIA", "ITACIBA") e o cadastro do
    portal do dele ("Loja Glória", "Loja Itacibá"): a comparação é sem "Loja",
    sem acento e em maiúsculas. Loja sem nenhuma linha no espelho dá lista
    vazia — e a tela fica vazia, não aberta.
    """
    lojas = lojas_visiveis(user)
    if lojas is None:
        return None
    todos = LinhaAuditoria.objects.exclude(pdv='').values_list('pdv', flat=True).distinct()
    return sorted(p for p in todos if normalizar_loja(p) in lojas)


def _data(texto):
    try:
        return datetime.strptime((texto or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def ler_filtros(request):
    """O que a tela está filtrando — os mesmos filtros na lista e no painel."""
    de, ate = _data(request.GET.get('de')), _data(request.GET.get('ate'))
    if de and ate and de > ate:
        de, ate = ate, de
    situacao = request.GET.get('situacao') or 'abertas'
    if situacao not in dict(SITUACOES):
        situacao = 'abertas'
    presenca = request.GET.get('presenca') or 'na'
    if presenca not in dict(PRESENCAS):
        presenca = 'na'
    ordem = request.GET.get('ordem') or 'recente'
    if ordem not in dict(ORDENS):
        ordem = 'recente'
    comparacao = request.GET.get('comparacao') or ''
    if comparacao not in dict(COMPARACOES):
        comparacao = ''
    return {
        'tipo': (request.GET.get('tipo') or '').strip(),
        'loja': (request.GET.get('loja') or '').strip(),
        'de': de, 'ate': ate,
        'situacao': situacao,
        'presenca': presenca,
        'comparacao': comparacao,
        'q': (request.GET.get('q') or '').strip(),
        'ordem': ordem,
    }


def filtrar(filtros, corte=None, pdvs=None):
    """A consulta do espelho já com os filtros da tela (e só as lojas de quem vê).

    Divergência de valor abaixo de R$ 1 fica de fora de tudo (``q_valor_desprezivel``).
    """
    qs = LinhaAuditoria.objects.exclude(q_valor_desprezivel())
    if pdvs is not None:
        qs = qs.filter(pdv__in=pdvs)
    if filtros['presenca'] == 'na':
        qs = qs.filter(ativa=True)
    elif filtros['presenca'] == 'sairam':
        qs = qs.filter(ativa=False)
    if filtros['situacao'] == 'abertas':
        qs = qs.filter(resolvida=False)
    elif filtros['situacao'] == 'resolvidas':
        qs = qs.filter(resolvida=True)
    elif filtros['situacao'] == 'pendentes':
        qs = qs.filter(filtro_resolvidas_pendentes(corte if corte is not None else inicio_da_ultima_leitura()))
    elif filtros['situacao'] == 'aguardando':
        qs = qs.filter(filtro_aguardando_leitura(corte if corte is not None else inicio_da_ultima_leitura()))
    if filtros['tipo']:
        qs = qs.filter(tipo_erro=filtros['tipo'])
    if filtros['loja']:
        qs = qs.filter(pdv=filtros['loja'])
    if filtros['de']:
        qs = qs.filter(data_venda__gte=filtros['de'])
    if filtros['ate']:
        qs = qs.filter(data_venda__lte=filtros['ate'])
    if filtros['comparacao']:
        campo = dict(COMPARACOES)[filtros['comparacao']]
        # "Divergente" é o que interessa; OK e "não comparado" ficam de fora.
        qs = qs.filter(**{f'{campo}__icontains': 'DIVERG'})
        if filtros['comparacao'] == 'valor':
            # "Valor divergente" por centavos, em qualquer tipo de erro, não é o que se procura aqui.
            limite = LIMITE_VALOR_DESPREZIVEL
            qs = qs.exclude(diferenca_valor__gt=-limite, diferenca_valor__lt=limite)
    if filtros['q']:
        procura = Q()
        for coluna in COLUNAS_DE_BUSCA:
            procura |= Q(**{f'{coluna}__icontains': filtros['q']})
        qs = qs.filter(procura)
    return qs


def ordenar(qs, ordem):
    if ordem == 'antiga':
        return qs.order_by('data_venda', 'pdv', 'id_venda')
    if ordem == 'diferenca':
        return (qs.annotate(peso=Abs(Coalesce('diferenca_valor', Value(ZERO))))
                  .order_by('-peso', '-data_venda'))
    if ordem == 'loja':
        return qs.order_by('pdv', '-data_venda', 'id_venda')
    if ordem == 'tipo':
        return qs.order_by('tipo_erro', '-data_venda', 'id_venda')
    return qs.order_by('-data_venda', 'pdv', 'id_venda')


def _resumo(qs):
    """Os números do topo, numa consulta só."""
    dados = qs.aggregate(
        total=Count('id'),
        resolvidas=Count('id', filter=Q(resolvida=True)),
        em_risco=Coalesce(Sum(Abs(Coalesce('diferenca_valor', Value(ZERO)))),
                          Value(ZERO), output_field=DecimalField(max_digits=16, decimal_places=2)),
    )
    dados['abertas'] = dados['total'] - dados['resolvidas']
    dados['percentual'] = round(dados['resolvidas'] * 100 / dados['total']) if dados['total'] else 0
    return dados


def _contexto_comum(request, filtros, pdvs=None):
    """O que lista e painel mostram igual: filtros, listas de escolha, espelho."""
    visiveis = LinhaAuditoria.objects.filter(ativa=True).exclude(q_valor_desprezivel())
    if pdvs is not None:
        visiveis = visiveis.filter(pdv__in=pdvs)
    return {
        'filtros': filtros,
        'tipos': (visiveis.exclude(tipo_erro='').values_list('tipo_erro', flat=True)
                  .distinct().order_by('tipo_erro')),
        'lojas': (visiveis.exclude(pdv='').values_list('pdv', flat=True).distinct().order_by('pdv')),
        # O gerente vê só a loja dele: a tela diz isso em vez de oferecer um
        # filtro de loja com uma opção só.
        'so_da_loja': pdvs is not None,
        'loja_do_usuario': (request.user.sector.name if pdvs is not None and request.user.sector_id
                            else ''),
        'situacoes': SITUACOES,
        'presencas': PRESENCAS,
        'ordens': ORDENS,
        'comparacoes': COMPARACOES,
        'ultima': ultima_sincronizacao(),
        'e_gestor': _e_gestor(request.user),
        'pode_escolher_gestores': pode_escolher_gestores(request.user),
        'limite_desprezivel': LIMITE_VALOR_DESPREZIVEL,
        'tem_espelho': LinhaAuditoria.objects.exists(),
    }


@login_required
@so_auditoria
def lista(request):
    """A auditoria linha a linha, com filtro, ordem e o botão de resolver."""
    filtros = ler_filtros(request)
    pdvs = pdvs_visiveis(request.user)
    qs = ordenar(filtrar(filtros, pdvs=pdvs), filtros['ordem'])
    resumo = _resumo(qs)

    pagina = Paginator(qs.select_related('resolvida_por'), POR_PAGINA).get_page(request.GET.get('pagina'))
    corte = inicio_da_ultima_leitura()
    for linha in pagina:
        linha.estado = linha.estado_da_resolucao(corte)

    # A paginação precisa manter os filtros e trocar só a página.
    parametros = request.GET.copy()
    parametros.pop('pagina', None)

    contexto = _contexto_comum(request, filtros, pdvs)
    contexto.update({
        'pagina': pagina,
        'resumo': resumo,
        'aba': 'lista',
        'query': parametros.urlencode(),
    })
    return render(request, 'auditoria_sap/lista.html', contexto)


@login_required
@so_auditoria
def painel(request):
    """O painel: o que está aberto, o que foi tratado e se o tratamento pegou.

    O ciclo é: alguém marca "resolvido"; na leitura seguinte do SAP, se a linha
    sumiu, ela foi corrigida; se continua lá, entra em "Resolvidas, mas ainda
    pendentes" — alguém disse que resolveu e não resolveu. Até a leitura seguinte
    ela fica "aguardando". O painel mostra as três coisas — e quem tratou o quê.
    """
    from datetime import timedelta

    from django.db.models.functions import TruncDate

    from .models import MarcacaoAuditoria

    filtros = ler_filtros(request)
    pdvs = pdvs_visiveis(request.user)
    corte = inicio_da_ultima_leitura()
    hoje = timezone.localdate()
    # O painel conta todas as situações; o filtro de situação vale só para a lista.
    base = filtrar({**filtros, 'situacao': 'todas', 'presenca': 'na'}, corte, pdvs)
    todas_presencas = filtrar({**filtros, 'situacao': 'todas', 'presenca': 'todas'}, corte, pdvs)

    q_abertas = Q(resolvida=False)
    q_pendentes = filtro_resolvidas_pendentes(corte)
    q_aguardando = filtro_aguardando_leitura(corte)
    valor = Coalesce(Sum(Abs(Coalesce('diferenca_valor', Value(ZERO)))), Value(ZERO),
                     output_field=DecimalField(max_digits=16, decimal_places=2))
    valor_aberto = Coalesce(Sum(Abs(Coalesce('diferenca_valor', Value(ZERO))), filter=q_abertas), Value(ZERO),
                            output_field=DecimalField(max_digits=16, decimal_places=2))

    numeros = base.aggregate(total=Count('id'), abertas=Count('id', filter=q_abertas),
                             pendentes=Count('id', filter=q_pendentes),
                             aguardando=Count('id', filter=q_aguardando), valor_aberto=valor_aberto)
    corrigidas_qs = todas_presencas.filter(ativa=False)
    numeros['corrigidas'] = corrigidas_qs.count()
    numeros['corrigidas_marcadas'] = corrigidas_qs.filter(resolvida=True).count()
    tratadas = numeros['total'] - numeros['abertas']
    numeros['percentual'] = round(tratadas * 100 / numeros['total'], 1) if numeros['total'] else 0

    # Pendências por loja.
    por_loja = list(base.values('pdv')
                    .annotate(abertas=Count('id', filter=q_abertas), pendentes=Count('id', filter=q_pendentes),
                              aguardando=Count('id', filter=q_aguardando), valor=valor_aberto)
                    .filter(Q(abertas__gt=0) | Q(pendentes__gt=0) | Q(aguardando__gt=0))
                    .order_by('-pendentes', '-abertas', 'pdv'))
    for loja in por_loja:
        loja['total'] = loja['abertas'] + loja['pendentes']
    maior_loja = max((l['total'] for l in por_loja), default=0)

    # Por tipo de erro.
    por_tipo = list(base.values('tipo_erro')
                    .annotate(abertas=Count('id', filter=q_abertas), tratadas=Count('id', filter=Q(resolvida=True)),
                              valor=valor_aberto)
                    .order_by('-abertas', 'tipo_erro'))
    maior_tipo = max((t['abertas'] + t['tratadas'] for t in por_tipo), default=0)

    # Idade das abertas, pela data da venda.
    faixas = (('Até 7 dias', 0, 7), ('8 a 30 dias', 8, 30), ('31 a 60 dias', 31, 60), ('Mais de 60 dias', 61, None))
    abertas = base.filter(q_abertas)
    idade = []
    for rotulo, de, ate in faixas:
        qs = abertas.filter(data_venda__lte=hoje - timedelta(days=de))
        if ate is not None:
            qs = qs.filter(data_venda__gte=hoje - timedelta(days=ate))
        idade.append({'rotulo': rotulo, 'n': qs.count()})
    sem_data = abertas.filter(data_venda__isnull=True).count()
    if sem_data:
        idade.append({'rotulo': 'Sem data', 'n': sem_data})
    maior_idade = max((i['n'] for i in idade), default=0)

    # O que foi marcado como resolvido, e o que aconteceu depois.
    resolvidas = list(todas_presencas.filter(resolvida=True).select_related('resolvida_por')
                      .order_by('-resolvida_em')[:40])
    for linha in resolvidas:
        linha.estado = 'corrigida' if not linha.ativa else linha.estado_da_resolucao(corte)

    # Quem tratou: resolvidas por pessoa e se o SAP confirmou.
    quem = list(todas_presencas.filter(resolvida=True, resolvida_por__isnull=False)
                .values('resolvida_por', 'resolvida_por__first_name', 'resolvida_por__last_name')
                .annotate(n=Count('id'), corrigidas=Count('id', filter=Q(ativa=False)),
                          pendentes=Count('id', filter=q_pendentes), aguardando=Count('id', filter=q_aguardando))
                .order_by('-n')[:15])

    # Ritmo: linhas marcadas como resolvidas por dia, nos últimos 30 dias.
    inicio_ritmo = hoje - timedelta(days=29)
    marcadas = dict(MarcacaoAuditoria.objects.filter(resolvida=True, linha__in=todas_presencas,
                                                     quando__date__gte=inicio_ritmo)
                    .annotate(dia=TruncDate('quando')).values('dia').annotate(n=Count('id'))
                    .values_list('dia', 'n'))
    ritmo = [{'dia': inicio_ritmo + timedelta(days=i), 'n': marcadas.get(inicio_ritmo + timedelta(days=i), 0)}
             for i in range(30)]
    maior_ritmo = max((d['n'] for d in ritmo), default=0)

    maiores = list(ordenar(base.filter(q_abertas), 'diferenca')[:10])

    resolvidas_pendentes = list(base.filter(q_pendentes).select_related('resolvida_por')
                                .order_by('pdv', '-resolvida_em')[:100])

    from .agendador import proxima_leitura
    from .models import AgendaLeituraSap

    agenda = AgendaLeituraSap.get()
    ultima_ok = SincronizacaoAuditoria.objects.filter(erro='').first()
    # Atrasada: passou do intervalo da leitura automática com folga de 1 h (ou de um dia, se desligada).
    tolerancia = timedelta(hours=(agenda.intervalo_horas + 1) if agenda.ativo else 24)
    leitura_atrasada = bool(ultima_ok and (timezone.now() - ultima_ok.quando) > tolerancia)

    parametros = request.GET.copy()
    for chave in ('situacao', 'presenca', 'loja', 'pagina'):
        parametros.pop(chave, None)

    contexto = _contexto_comum(request, filtros, pdvs)
    contexto.update({
        'numeros': numeros,
        'por_loja': por_loja,
        'maior_loja': maior_loja,
        'por_tipo': por_tipo,
        'maior_tipo': maior_tipo,
        'idade': idade,
        'maior_idade': maior_idade,
        'resolvidas': resolvidas,
        'quem': quem,
        'ritmo': ritmo,
        'maior_ritmo': maior_ritmo,
        'ritmo_total': sum(d['n'] for d in ritmo),
        'maiores': maiores,
        'resolvidas_pendentes': resolvidas_pendentes,
        'corte': corte,
        'ultima_ok': ultima_ok,
        'leitura_atrasada': leitura_atrasada,
        'agenda': agenda,
        'proxima': proxima_leitura(agenda),
        'aba': 'painel',
        # Para os links de detalhe: os filtros do painel, sem situação/loja (cada link põe a sua).
        'query': parametros.urlencode(),
    })
    return render(request, 'auditoria_sap/painel.html', contexto)


def _linha_visivel(user, linha_id):
    """A linha, se ela for de uma loja que a pessoa vê — senão 404, nem pela URL."""
    pdvs = pdvs_visiveis(user)
    qs = LinhaAuditoria.objects.all() if pdvs is None else LinhaAuditoria.objects.filter(pdv__in=pdvs)
    return get_object_or_404(qs, id=linha_id)


@login_required
@so_auditoria
def detalhe(request, linha_id):
    """A linha inteira, como veio do SAP, mais o histórico de quem marcou."""
    linha = _linha_visivel(request.user, linha_id)
    historico = [{
        'quem': m.usuario.full_name if m.usuario else 'Usuário removido',
        'resolvida': m.resolvida,
        'quando': timezone.localtime(m.quando).strftime('%d/%m/%Y %H:%M'),
        'observacao': m.observacao,
    } for m in linha.marcacoes.select_related('usuario')[:20]]
    return JsonResponse({
        'ok': True,
        'campos': [{'coluna': c, 'valor': v} for c, v in (linha.dados or {}).items()],
        'historico': historico,
        'resolvida': linha.resolvida,
        'observacao': linha.observacao,
        'ativa': linha.ativa,
    })


@login_required
@so_auditoria
def marcar(request, linha_id):
    """Marca (ou desmarca) a linha como resolvida, guardando quem foi."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'erro': 'Use POST.'}, status=405)
    linha = _linha_visivel(request.user, linha_id)
    resolvida = (request.POST.get('resolvida') or '').lower() in ('1', 'true', 'sim', 'on')
    marcacao = linha.marcar(request.user, resolvida, request.POST.get('observacao') or '')
    if resolvida:
        from .avisos import avisar_resolvida
        avisar_resolvida(linha, request.user)
    return JsonResponse({
        'ok': True,
        'resolvida': linha.resolvida,
        'quem': marcacao.usuario.full_name if marcacao.usuario else '',
        'quando': timezone.localtime(marcacao.quando).strftime('%d/%m/%Y %H:%M'),
        'observacao': linha.observacao,
    })


@login_required
@so_auditoria
def atualizar(request):
    """Relê a view do SAP. Demora ~30 s: é ação de botão, não de abrir tela."""
    if request.method != 'POST':
        return redirect('auditoria_sap:lista')
    if not _e_gestor(request.user):
        messages.error(request, 'Só a administração atualiza a auditoria do SAP.')
        return redirect('auditoria_sap:lista')
    try:
        resumo = sincronizar(por=request.user)
    except SapIndisponivel as exc:
        messages.error(request, f'Não deu para ler o SAP agora: {exc}')
        return redirect(request.POST.get('voltar') or 'auditoria_sap:lista')
    messages.success(request, (
        f"Auditoria atualizada: {resumo['total']} linhas — {resumo['novas']} novas, "
        f"{resumo['atualizadas']} alteradas e {resumo['sumiram']} que saíram "
        f"({resumo['segundos']:.0f} s)."))
    return redirect(request.POST.get('voltar') or 'auditoria_sap:lista')


@login_required
def gestores(request):
    """O SUPERADMIN escolhe quem gere a Visão SAP (e recebe aviso a cada linha resolvida)."""
    if not pode_escolher_gestores(request.user):
        messages.error(request, 'Só o SUPERADMIN escolhe quem gere a Visão SAP.')
        return redirect('auditoria_sap:lista')
    from django.contrib.auth import get_user_model

    from users.models import UserModuleAccess
    from .agendador import proxima_leitura
    from .models import AgendaLeituraSap

    User = get_user_model()
    agenda = AgendaLeituraSap.get()
    if request.method == 'POST' and request.POST.get('secao') == 'agenda':
        agenda.ativo = request.POST.get('ativo') == 'on'
        try:
            agenda.intervalo_horas = max(1, min(24, int(request.POST.get('intervalo_horas') or 3)))
        except ValueError:
            agenda.intervalo_horas = 3
        agenda.atualizado_por = request.user
        agenda.save()
        messages.success(request, f'Leitura automática do SAP a cada {agenda.intervalo_horas} h.' if agenda.ativo
                         else 'Leitura automática do SAP desligada: só pelo botão "Atualizar do SAP".')
        return redirect('auditoria_sap:gestores')
    atuais = set(gestores_escolhidos().values_list('pk', flat=True))
    if request.method == 'POST':
        escolhidos = {int(i) for i in request.POST.getlist('gestores') if str(i).isdigit()}
        escolhidos = set(User.objects.filter(pk__in=escolhidos, is_active=True).values_list('pk', flat=True))
        saem = atuais - escolhidos
        entram = escolhidos - atuais
        UserModuleAccess.objects.filter(user_id__in=saem, module_key=CHAVE_GESTOR).delete()
        for pk in entram:
            UserModuleAccess.objects.get_or_create(user_id=pk, module_key=CHAVE_GESTOR,
                                                   defaults={'granted_by': request.user})
        try:
            from core.models import SystemLog
            nomes = lambda ids: ', '.join(u.full_name for u in User.objects.filter(pk__in=ids)) or '—'
            SystemLog.objects.create(user=request.user, action_type='ADMIN_ACTION',
                                     description=(f'Gestores da Visão SAP — entraram: {nomes(entram)}; '
                                                  f'saíram: {nomes(saem)}')[:1000])
        except Exception:                                       # noqa: BLE001 — o log não segura a escolha
            logger.warning('Log da escolha de gestores do SAP não foi gravado', exc_info=True)
        messages.success(request, f'Gestores da Visão SAP salvos: {len(escolhidos)} pessoa(s).')
        return redirect('auditoria_sap:gestores')
    pessoas = list(User.objects.filter(is_active=True).select_related('sector').order_by('first_name', 'last_name'))
    pessoas.sort(key=lambda u: (u.pk not in atuais, (u.first_name or '').upper()))
    return render(request, 'auditoria_sap/gestores.html', {
        'pessoas': pessoas, 'atuais': atuais, 'aba': 'gestores',
        'agenda': agenda, 'proxima': proxima_leitura(agenda),
        'pode_escolher_gestores': True, 'e_gestor': True, 'ultima': ultima_sincronizacao(),
    })
