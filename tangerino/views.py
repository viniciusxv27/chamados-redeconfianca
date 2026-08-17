"""Telas da integração com o Tangerino: ponto, férias e folhas sincronizadas."""
import calendar
import json
import logging
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import ferias as ferias_svc
from . import ponto as ponto_svc
from .client import (TangerinoError, de_millis, integracao_ativa, listar_funcionarios,
                     listar_marcacoes, invalidar_cache_marcacoes, justificativas_edicao,
                     registrar_ponto, registrar_ponto_atrasado, testar_conexao)
from .models import (ConfiguracaoTangerino, FeriasLancamento, MarcacaoPonto,
                     RegistroPontoPortal, SincronizacaoTangerino)
from .sync import (funcionarios_disponiveis, sincronizar_ferias, sincronizar_marcacoes,
                   sincronizar_vinculos)

logger = logging.getLogger(__name__)
User = get_user_model()


def e_gestor(user):
    """Quem enxerga o ponto/férias de todo mundo."""
    return bool(user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN')


def modulo_liberado(view_func):
    """Fecha a view para quem não está no grupo liberado.

    O módulo nasce restrito: só superusuários e membros do grupo configurado
    enxergam ponto e férias. Abrir para o portal inteiro é uma decisão que se
    toma na tela de configuração, não no código.
    """
    from functools import wraps

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not ConfiguracaoTangerino.get().libera(request.user):
            messages.error(request, 'O módulo de Ponto e Férias não está liberado para você.')
            return redirect('home')
        return view_func(request, *args, **kwargs)
    return _wrapped


def _ip(request):
    encaminhado = request.META.get('HTTP_X_FORWARDED_FOR')
    return (encaminhado.split(',')[0].strip() if encaminhado
            else request.META.get('REMOTE_ADDR'))


# ─── Ponto ───────────────────────────────────────────────────────────────────

@modulo_liberado
@login_required
def meu_ponto(request):
    """Painel de ponto do próprio colaborador."""
    hoje = timezone.localdate()
    inicio = hoje - timedelta(days=hoje.weekday())      # segunda desta semana
    config = ConfiguracaoTangerino.get()
    contexto = {
        'aba': 'ponto',
        'config': config,
        'pode_bater_ponto': config.permitir_bater_ponto,
        'integracao_ativa': integracao_ativa(),
        'vinculado': bool(request.user.tangerino_employee_id),
        'e_gestor': e_gestor(request.user),
        'hoje': hoje,
        'inicio_semana': inicio,
    }

    if contexto['vinculado'] and contexto['integracao_ativa']:
        try:
            pares = listar_marcacoes(inicio - timedelta(days=28), hoje,
                                     employee_id=request.user.tangerino_employee_id, ttl=120)
            status = ponto_svc.status_do_dia(
                request.user.tangerino_employee_id, dia=hoje, pares=pares)
            contexto['status'] = status
            contexto['marcos'] = [
                ('Entrada', status['bateu_entrada']),
                ('Saída para intervalo', status['saiu_almoco']),
                ('Volta do intervalo', status['voltou_almoco']),
            ]
            contexto['pendencias'] = ponto_svc.pendencias(
                request.user.tangerino_employee_id, pares=pares)
            contexto['dias'] = _dias_com_marcacoes(pares, inicio - timedelta(days=28), hoje)
            contexto['semana'] = [d for d in contexto['dias'] if d['dia'] >= inicio]
            contexto['total_semana'] = ponto_svc.formata_hhmm(
                sum(d['segundos'] for d in contexto['semana']))
            contexto['justificativas'] = justificativas_edicao()
        except TangerinoError as exc:
            contexto['erro'] = str(exc)

    return render(request, 'tangerino/meu_ponto.html', contexto)


def _dias_com_marcacoes(pares, inicio, fim):
    """Agrupa os pares por dia, do mais recente para o mais antigo."""
    por_dia = {}
    for par in pares:
        entrada = de_millis(par.get('dateIn'))
        if not entrada:
            continue
        por_dia.setdefault(entrada.date(), []).append(par)

    dias = []
    atual = fim
    while atual >= inicio:
        do_dia = por_dia.get(atual, [])
        eventos = ponto_svc._eventos(do_dia)
        segundos = ponto_svc._segundos_trabalhados(do_dia)
        aberto = any(de_millis(p.get('dateIn')) and not de_millis(p.get('dateOut'))
                     for p in do_dia)
        dias.append({
            'dia': atual,
            'eventos': eventos,
            'segundos': segundos,
            'horas': ponto_svc.formata_hhmm(segundos),
            'aberto': aberto and atual < timezone.localdate(),
            'fim_de_semana': atual.weekday() >= 5,
            'sem_marcacao': not eventos,
        })
        atual -= timedelta(days=1)
    return dias


@modulo_liberado
@login_required
def ponto_equipe(request):
    """Painel de ponto de todos — só para SuperAdmin."""
    if not e_gestor(request.user):
        messages.error(request, 'Apenas administradores veem o ponto de toda a equipe.')
        return redirect('tangerino:meu_ponto')

    try:
        dia = date.fromisoformat(request.GET.get('dia')) if request.GET.get('dia') else timezone.localdate()
    except ValueError:
        dia = timezone.localdate()

    contexto = {'aba': 'ponto_equipe', 'dia': dia, 'e_gestor': True, 'hoje': timezone.localdate()}
    try:
        painel = ponto_svc.painel_da_empresa(dia=dia)
        funcionarios = {f['id']: f for f in listar_funcionarios()}
        vinculados = {u.tangerino_employee_id: u for u in
                      User.objects.exclude(tangerino_employee_id__isnull=True)}

        linhas = []
        for eid, status in painel.items():
            func = funcionarios.get(eid) or {}
            linhas.append({
                'employee_id': eid,
                'nome': func.get('name') or (vinculados.get(eid).full_name if eid in vinculados else f'ID {eid}'),
                'usuario': vinculados.get(eid),
                'status': status,
            })
        # Quem não marcou nada no dia também precisa aparecer.
        sem_marcacao = [
            {'employee_id': f['id'], 'nome': f.get('name') or '', 'usuario': vinculados.get(f['id']),
             'status': None}
            for f in funcionarios.values() if f['id'] not in painel
        ]

        contexto.update({
            'linhas': sorted(linhas, key=lambda l: l['nome']),
            'sem_marcacao': sorted(sem_marcacao, key=lambda l: l['nome']),
            'total_trabalhando': sum(1 for l in linhas if l['status']['situacao'] == 'TRABALHANDO'),
            'total_intervalo': sum(1 for l in linhas if l['status']['situacao'] == 'EM_INTERVALO'),
            'total_encerrado': sum(1 for l in linhas if l['status']['situacao'] == 'ENCERRADO'),
        })
    except TangerinoError as exc:
        contexto['erro'] = str(exc)

    return render(request, 'tangerino/ponto_equipe.html', contexto)


@modulo_liberado
@login_required
def api_ponto_status(request):
    """JSON do widget da home. Sempre 200: a home não pode quebrar por causa disto."""
    resumo = ponto_svc.resumo_para_usuario(request.user)
    if not resumo.get('disponivel'):
        return JsonResponse({'disponivel': False, 'motivo': resumo.get('motivo')})

    config = ConfiguracaoTangerino.get()
    return JsonResponse({
        'disponivel': True,
        'pode_bater': config.permitir_bater_ponto,
        'situacao': resumo['situacao'],
        'rotulo': resumo['rotulo'],
        'bateu_entrada': resumo['bateu_entrada'],
        'saiu_almoco': resumo['saiu_almoco'],
        'voltou_almoco': resumo['voltou_almoco'],
        'dentro': resumo['dentro'],
        'proxima_acao': resumo['proxima_acao'],
        'desde': resumo['desde'].isoformat() if resumo['desde'] else None,
        'trabalhado_segundos': resumo['trabalhado_segundos'],
        'trabalhado_hhmm': resumo['trabalhado_hhmm'],
        'agora': timezone.localtime().isoformat(),
        'eventos': [{'tipo': e['tipo'], 'hora': e['quando'].strftime('%H:%M')}
                    for e in resumo['eventos']],
        'pendencias': [{'dia': p['dia'].strftime('%d/%m'),
                        'entrada': p['entrada'].strftime('%H:%M')}
                       for p in resumo['pendencias']],
        'url_ponto': '/ponto/',
    })


@modulo_liberado
@login_required
@require_POST
def api_bater_ponto(request):
    """Registra a marcação no Tangerino a partir do portal."""
    config = ConfiguracaoTangerino.get()
    if not config.permitir_bater_ponto:
        return JsonResponse(
            {'sucesso': False,
             'erro': 'O registro de ponto pelo portal está desligado. Use o app do Tangerino.'},
            status=403)
    if not request.user.tangerino_employee_id:
        return JsonResponse({'sucesso': False,
                             'erro': 'Seu usuário ainda não está vinculado ao Tangerino.'}, status=400)

    try:
        corpo = json.loads(request.body or '{}')
    except ValueError:
        corpo = {}

    latitude, longitude = corpo.get('latitude'), corpo.get('longitude')
    atrasado = bool(corpo.get('atrasado'))
    foto = corpo.get('foto') or ''

    if atrasado and not config.permitir_ponto_atrasado:
        return JsonResponse(
            {'sucesso': False, 'erro': 'A marcação retroativa pelo portal está desligada.'},
            status=403)
    # O Tangerino desta empresa recusa marcação web sem foto. Barrar aqui evita
    # uma ida à API só para receber a recusa de volta.
    if config.exigir_foto and not foto:
        return JsonResponse(
            {'sucesso': False,
             'erro': 'Esta empresa exige foto para registrar ponto pela web. '
                     'Autorize a câmera e tente de novo.'},
            status=400)

    registro = RegistroPontoPortal(
        usuario=request.user, employee_id=request.user.tangerino_employee_id,
        momento=timezone.localtime(), atrasado=atrasado, com_foto=bool(foto),
        ip=_ip(request), user_agent=request.META.get('HTTP_USER_AGENT', '')[:500])

    try:
        if atrasado:
            quando = timezone.localtime().fromisoformat(corpo['quando'])
            if timezone.is_naive(quando):
                quando = timezone.make_aware(quando)
            if quando > timezone.localtime():
                return JsonResponse({'sucesso': False,
                                     'erro': 'Não dá para registrar um ponto no futuro.'}, status=400)
            justificativa_id = int(corpo.get('justificativa_id') or 0)
            if not justificativa_id:
                return JsonResponse({'sucesso': False,
                                     'erro': 'Escolha a justificativa da marcação retroativa.'}, status=400)
            registro.momento = quando
            registro.justificativa = str(corpo.get('justificativa_texto', ''))[:200]
            resposta = registrar_ponto_atrasado(
                request.user.tangerino_employee_id, quando, justificativa_id,
                observacao=corpo.get('observacao', ''), foto_base64=foto)
        else:
            resposta = registrar_ponto(
                request.user.tangerino_employee_id,
                latitude=latitude, longitude=longitude,
                endereco=corpo.get('endereco', ''), foto_base64=foto)

        registro.sucesso = True
        registro.retorno = json.dumps(resposta, ensure_ascii=False)[:2000]
        registro.save()
        invalidar_cache_marcacoes(request.user.tangerino_employee_id)

        status = ponto_svc.resumo_para_usuario(request.user)
        return JsonResponse({
            'sucesso': True,
            'mensagem': 'Ponto registrado no Tangerino.',
            'nsr': (resposta or {}).get('nsr'),
            'hora': registro.momento.strftime('%H:%M'),
            'situacao': status.get('situacao'),
            'rotulo': status.get('rotulo'),
        })
    except (TangerinoError, ValueError, KeyError) as exc:
        registro.sucesso = False
        registro.retorno = str(exc)[:2000]
        registro.save()
        logger.warning('Falha ao bater ponto de %s: %s', request.user, exc)
        return JsonResponse({'sucesso': False, 'erro': str(exc)}, status=502)


# ─── Férias ──────────────────────────────────────────────────────────────────

@modulo_liberado
@login_required
def minhas_ferias(request):
    contexto = {
        'aba': 'ferias',
        'integracao_ativa': integracao_ativa(),
        'vinculado': bool(request.user.tangerino_employee_id),
        'e_gestor': e_gestor(request.user),
        'hoje': timezone.localdate(),
        'situacao': ferias_svc.situacao_do_usuario(request.user),
    }
    return render(request, 'tangerino/minhas_ferias.html', contexto)


@modulo_liberado
@login_required
def ferias_equipe(request):
    if not e_gestor(request.user):
        messages.error(request, 'Apenas administradores veem as férias de toda a equipe.')
        return redirect('tangerino:minhas_ferias')

    contexto = {'aba': 'ferias_equipe', 'e_gestor': True, 'hoje': timezone.localdate()}
    try:
        contexto['panorama'] = ferias_svc.panorama_da_empresa()
    except TangerinoError as exc:
        contexto['erro'] = str(exc)
    return render(request, 'tangerino/ferias_equipe.html', contexto)


@login_required
def em_ferias(request):
    """Tela mostrada a quem está de férias, no lugar do portal."""
    lanc = ferias_svc.esta_de_ferias(request.user)
    if not lanc:
        return redirect('home')
    return render(request, 'tangerino/em_ferias.html', {
        'ferias': lanc,
        'volta_em': lanc['fim'] + timedelta(days=1),
        'dias_restantes': (lanc['fim'] - timezone.localdate()).days + 1,
    })


@modulo_liberado
@login_required
def api_ferias_popup(request):
    """Conteúdo do popup de férias (JSON), consultado uma vez por dia."""
    dados = ferias_svc.situacao_do_usuario(request.user)
    if not dados.get('disponivel') or not dados.get('precisa_alertar'):
        return JsonResponse({'mostrar': False})

    def _lanc(l):
        return None if not l else {
            'inicio': l['inicio'].strftime('%d/%m/%Y'),
            'fim': l['fim'].strftime('%d/%m/%Y'),
            'dias': (l['fim'] - l['inicio']).days + 1,
            'status': l['status'],
        }

    return JsonResponse({
        'mostrar': True,
        'em_gozo': _lanc(dados['em_gozo']),
        'dias_restantes_gozo': dados['dias_restantes_gozo'],
        'volta_em': dados['volta_em'].strftime('%d/%m/%Y') if dados['volta_em'] else None,
        'proxima': _lanc(dados['proxima']),
        'dias_vencidos': dados['dias_vencidos'],
        'saldo_total': dados['saldo_total'],
        'vencendo': [{'fim': p['concessivo_fim'].strftime('%d/%m/%Y'),
                      'dias': p['dias_para_vencer'], 'saldo': p['saldo']}
                     for p in dados['vencendo']],
        'url': '/ferias/',
    })


# ─── Folhas sincronizadas ────────────────────────────────────────────────────

@modulo_liberado
@login_required
def folhas_sincronizadas(request):
    """Folha de ponto montada ao vivo a partir do Tangerino.

    Tela NOVA: a importação por PDF em /folha-ponto/ continua existindo e
    intocada. Aqui os dados vêm da API, então estão sempre atualizados.
    """
    hoje = timezone.localdate()
    try:
        ano = int(request.GET.get('ano') or hoje.year)
        mes = int(request.GET.get('mes') or hoje.month)
        if not 1 <= mes <= 12:
            raise ValueError
    except ValueError:
        ano, mes = hoje.year, hoje.month

    alvo = request.user
    if e_gestor(request.user) and request.GET.get('usuario'):
        alvo = User.objects.filter(pk=request.GET['usuario']).first() or request.user

    primeiro = date(ano, mes, 1)
    ultimo = date(ano, mes, calendar.monthrange(ano, mes)[1])

    contexto = {
        'aba': 'folhas',
        'integracao_ativa': integracao_ativa(),
        'vinculado': bool(alvo.tangerino_employee_id),
        'e_gestor': e_gestor(request.user),
        'alvo': alvo,
        'ano': ano, 'mes': mes,
        'primeiro': primeiro, 'ultimo': ultimo,
        # calendar.month_name segue o locale do processo (vem em inglês no
        # servidor), então os nomes são fixados aqui em pt-BR.
        'meses': list(enumerate(
            ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho',
             'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'], start=1)),
        'anos': list(range(hoje.year - 3, hoje.year + 1)),
        'hoje': hoje,
    }
    if e_gestor(request.user):
        contexto['pessoas'] = (User.objects.exclude(tangerino_employee_id__isnull=True)
                               .filter(is_active=True).order_by('first_name', 'last_name'))

    if contexto['vinculado'] and contexto['integracao_ativa']:
        try:
            pares = listar_marcacoes(primeiro, min(ultimo, hoje),
                                     employee_id=alvo.tangerino_employee_id, ttl=300)
            dias = _dias_com_marcacoes(pares, primeiro, min(ultimo, hoje))
            dias.reverse()                       # do dia 1 para o fim do mês
            contexto['dias'] = dias
            contexto['total_segundos'] = sum(d['segundos'] for d in dias)
            contexto['total_horas'] = ponto_svc.formata_hhmm(contexto['total_segundos'])
            contexto['dias_trabalhados'] = sum(1 for d in dias if d['segundos'] > 0)
            contexto['dias_em_aberto'] = [d for d in dias if d['aberto']]
            contexto['atualizado_em'] = timezone.localtime()
        except TangerinoError as exc:
            contexto['erro'] = str(exc)

    return render(request, 'tangerino/folhas_sincronizadas.html', contexto)


# ─── Administração do vínculo ────────────────────────────────────────────────

@modulo_liberado
@login_required
def vinculos(request):
    if not e_gestor(request.user):
        messages.error(request, 'Apenas administradores gerenciam a integração.')
        return redirect('home')

    ok, mensagem = testar_conexao() if integracao_ativa() else (False, 'Integração desligada.')
    sem_vinculo = (User.objects.filter(is_active=True, tangerino_employee_id__isnull=True)
                   .order_by('first_name', 'last_name'))
    contexto = {
        'aba': 'vinculos',
        'conexao_ok': ok, 'conexao_msg': mensagem,
        'integracao_ativa': integracao_ativa(),
        'sem_vinculo': sem_vinculo,
        'total_vinculados': User.objects.exclude(tangerino_employee_id__isnull=True).count(),
        'ultimas': SincronizacaoTangerino.objects.all()[:5],
        'e_gestor': True,
    }
    if ok:
        try:
            contexto['disponiveis'] = funcionarios_disponiveis()
        except TangerinoError as exc:
            contexto['conexao_msg'] = str(exc)
    return render(request, 'tangerino/vinculos.html', contexto)


@modulo_liberado
@login_required
@require_POST
def sincronizar(request):
    if not e_gestor(request.user):
        messages.error(request, 'Apenas administradores podem sincronizar.')
        return redirect('home')

    registro = SincronizacaoTangerino(executada_por=request.user)
    try:
        resultado = sincronizar_vinculos(revincular=request.POST.get('revincular') == 'on')
        registro.casados_cpf = resultado['casados_cpf']
        registro.casados_nome = resultado['casados_nome']
        registro.ja_vinculados = resultado['ja_vinculados']
        registro.sem_correspondencia = resultado['sem_correspondencia']
        registro.sucesso = True
        registro.save()
        messages.success(
            request,
            f"Sincronização concluída: {resultado['casados_cpf']} por CPF, "
            f"{resultado['casados_nome']} por nome, {resultado['ja_vinculados']} já vinculados. "
            f"{resultado['sem_correspondencia']} sem correspondência.")
    except TangerinoError as exc:
        registro.sucesso = False
        registro.detalhe = str(exc)[:2000]
        registro.save()
        messages.error(request, f'Falha na sincronização: {exc}')
    return redirect('tangerino:vinculos')


@modulo_liberado
@login_required
def configuracao(request):
    """Liga/desliga do módulo — só superusuário."""
    if not request.user.is_superuser:
        messages.error(request, 'Apenas superusuários alteram a configuração do módulo.')
        return redirect('tangerino:meu_ponto')

    from communications.models import CommunicationGroup
    config = ConfiguracaoTangerino.get()

    if request.method == 'POST':
        for campo in ('ativo', 'restrito_ao_grupo', 'permitir_bater_ponto', 'exigir_foto',
                      'permitir_ponto_atrasado', 'mostrar_widget_home',
                      'mostrar_popup_ferias', 'bloquear_navegacao_ferias'):
            setattr(config, campo, request.POST.get(campo) == 'on')
        grupo_id = request.POST.get('grupo') or ''
        config.grupo_id = int(grupo_id) if grupo_id.isdigit() else None
        config.atualizado_por = request.user
        config.save()
        messages.success(request, 'Configuração salva.')
        return redirect('tangerino:configuracao')

    contexto = {
        'aba': 'configuracao',
        'e_gestor': True,
        'config': config,
        'grupos': CommunicationGroup.objects.all().order_by('name'),
        'marcacoes_no_banco': MarcacaoPonto.objects.count(),
        'ferias_no_banco': FeriasLancamento.objects.count(),
        'ultima_ponto': SincronizacaoTangerino.objects.filter(
            tipo=SincronizacaoTangerino.Tipo.PONTO).first(),
        'ultima_ferias': SincronizacaoTangerino.objects.filter(
            tipo=SincronizacaoTangerino.Tipo.FERIAS).first(),
        'tentativas_ponto': RegistroPontoPortal.objects.all()[:10],
    }
    return render(request, 'tangerino/configuracao.html', contexto)


@modulo_liberado
@login_required
@require_POST
def sincronizar_dados(request):
    """Puxa marcações e/ou férias da API para as tabelas locais."""
    if not e_gestor(request.user):
        messages.error(request, 'Apenas administradores podem sincronizar.')
        return redirect('tangerino:meu_ponto')

    alvo = request.POST.get('alvo')
    try:
        dias = max(1, min(365, int(request.POST.get('dias') or 30)))
    except ValueError:
        dias = 30

    tarefas = []
    if alvo in ('ponto', 'tudo'):
        tarefas.append((SincronizacaoTangerino.Tipo.PONTO, 'Marcações',
                        lambda: sincronizar_marcacoes(dias=dias)))
    if alvo in ('ferias', 'tudo'):
        tarefas.append((SincronizacaoTangerino.Tipo.FERIAS, 'Férias', sincronizar_ferias))
    if not tarefas:
        messages.error(request, 'Escolha o que sincronizar.')
        return redirect('tangerino:configuracao')

    for tipo, rotulo, funcao in tarefas:
        registro = SincronizacaoTangerino(tipo=tipo, executada_por=request.user)
        try:
            resultado = funcao()
            registro.criados = resultado['criados']
            registro.atualizados = resultado['atualizados']
            registro.sucesso = True
            registro.save()
            messages.success(
                request,
                f"{rotulo}: {resultado['lidos']} lidos, {resultado['criados']} novos, "
                f"{resultado['atualizados']} atualizados.")
        except TangerinoError as exc:
            registro.sucesso = False
            registro.detalhe = str(exc)[:2000]
            registro.save()
            messages.error(request, f'{rotulo}: falha na sincronização — {exc}')

    return redirect('tangerino:configuracao')


@modulo_liberado
@login_required
@require_POST
def vincular_manual(request):
    if not e_gestor(request.user):
        messages.error(request, 'Apenas administradores podem vincular.')
        return redirect('home')

    usuario = User.objects.filter(pk=request.POST.get('user_id')).first()
    bruto = (request.POST.get('employee_id') or '').strip()
    if not usuario:
        messages.error(request, 'Usuário não encontrado.')
        return redirect('tangerino:vinculos')

    if not bruto:
        usuario.tangerino_employee_id = None
        usuario.tangerino_synced_at = None
        usuario.save(update_fields=['tangerino_employee_id', 'tangerino_synced_at'])
        messages.success(request, f'Vínculo de {usuario.full_name} removido.')
        return redirect('tangerino:vinculos')

    employee_id = int(bruto)
    ocupado = User.objects.filter(tangerino_employee_id=employee_id).exclude(pk=usuario.pk).first()
    if ocupado:
        messages.error(request, f'Esse funcionário já está vinculado a {ocupado.full_name}.')
        return redirect('tangerino:vinculos')

    usuario.tangerino_employee_id = employee_id
    usuario.tangerino_synced_at = timezone.now()
    usuario.save(update_fields=['tangerino_employee_id', 'tangerino_synced_at'])
    messages.success(request, f'{usuario.full_name} vinculado ao funcionário {employee_id}.')
    return redirect('tangerino:vinculos')
