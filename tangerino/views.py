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
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_time
from django.views.decorators.http import require_POST

from . import escala as escala_svc
from . import ferias as ferias_svc
from . import jornada as jornada_svc
from . import ponto as ponto_svc
from . import regras_jornada as regras
from .middleware import limpar_decisao
from .client import (TangerinoError, de_millis, integracao_ativa, listar_funcionarios,
                     listar_marcacoes, invalidar_cache_marcacoes, justificativas_edicao,
                     registrar_ponto, registrar_ponto_atrasado, testar_conexao)
from .models import (HORAS_SEMANAIS, ConfiguracaoTangerino, Escala, EscalaConfig, EscalaDia,
                     FeriasLancamento, JornadaTrabalho, MarcacaoPonto,
                     RegistroPontoPortal, SaldoHoras, SincronizacaoTangerino)
from .sync import (funcionarios_disponiveis, sincronizar_ferias, sincronizar_jornadas,
                   sincronizar_marcacoes, sincronizar_saldos, sincronizar_vinculos)

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


# ─── Filtro por setor ────────────────────────────────────────────────────────
# O Tangerino não tem setor: o `workplaceList` dele só devolve ids sem nome.
# O setor vem do cadastro do portal, pelo usuário vinculado — cobre 164 dos
# 168 funcionários. Os quatro restantes caem em "Sem setor", que é uma opção
# de verdade no filtro em vez de sumirem da lista.

SEM_SETOR = '__sem__'


def _setores_por_employee():
    """{employee_id: Sector} a partir dos usuários vinculados."""
    return {u.tangerino_employee_id: u.sector
            for u in (User.objects.exclude(tangerino_employee_id__isnull=True)
                      .select_related('sector'))}


def _aplicar_filtro_setor(linhas, setor_escolhido, mapa=None):
    """Anota o setor em cada linha, monta as opções e filtra.

    Devolve (linhas_filtradas, opções_para_o_select). As opções saem da lista
    inteira, antes do filtro — senão, ao escolher um setor, os outros sumiriam
    do próprio seletor e não teria como voltar.
    """
    mapa = _setores_por_employee() if mapa is None else mapa

    opcoes, sem_setor = {}, False
    for linha in linhas:
        setor = mapa.get(linha.get('employee_id'))
        linha['setor'] = setor
        if setor:
            opcoes[setor.id] = setor.name
        else:
            sem_setor = True

    lista = sorted(({'valor': str(i), 'nome': n} for i, n in opcoes.items()),
                   key=lambda o: o['nome'].upper())
    if sem_setor:
        lista.append({'valor': SEM_SETOR, 'nome': 'Sem setor'})

    if setor_escolhido == SEM_SETOR:
        linhas = [l for l in linhas if not l['setor']]
    elif setor_escolhido and setor_escolhido.isdigit():
        alvo = int(setor_escolhido)
        linhas = [l for l in linhas if l['setor'] and l['setor'].id == alvo]

    return linhas, lista


def _nome_do_setor(opcoes, escolhido):
    if not escolhido:
        return ''
    return next((o['nome'] for o in opcoes if o['valor'] == escolhido), '')


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
            grade = _jornada_do_usuario(request.user)
            contexto['tem_jornada'] = bool(grade)
            contexto['dias'] = _dias_com_marcacoes(
                pares, inicio - timedelta(days=28), hoje,
                grade=grade, employee_id=request.user.tangerino_employee_id)
            contexto['semana'] = [d for d in contexto['dias'] if d['dia'] >= inicio]
            contexto['total_semana'] = ponto_svc.formata_hhmm(
                sum(d['segundos'] for d in contexto['semana']))
            contexto.update(_previsto_x_feito(contexto['semana'], hoje, rotulo='semana'))
            contexto.update(_previsto_x_feito(contexto['dias'], hoje, rotulo='periodo'))
            contexto['jornada'] = _resumo_da_jornada(request.user)
            contexto['justificativas'] = justificativas_edicao()
        except TangerinoError as exc:
            contexto['erro'] = str(exc)

    from .models import nao_bate_ponto
    contexto['isento_de_ponto'] = nao_bate_ponto(request.user)

    return render(request, 'tangerino/meu_ponto.html', contexto)


def _previsto_x_feito(dias, hoje, rotulo):
    """Totais de previsto e realizado de uma lista de dias.

    O dia de hoje fica **fora do previsto**: a jornada ainda está correndo, e
    somá-la inteira faria a pessoa parecer devendo horas às nove da manhã. O
    trabalhado de hoje continua contando, que é o que ela já fez.
    """
    previsto = sum(d['previsto_segundos'] or 0 for d in dias
                   if d['previsto_segundos'] is not None and d['dia'] < hoje)
    feito = sum(d['segundos'] for d in dias)
    tem = any(d['previsto_segundos'] is not None for d in dias)
    return {
        f'previsto_{rotulo}_segundos': previsto if tem else None,
        f'previsto_{rotulo}': jornada_svc.formata_hhmm(previsto) if tem else None,
        f'feito_{rotulo}': jornada_svc.formata_hhmm(feito),
        f'diferenca_{rotulo}': jornada_svc.formata_hhmm(feito - previsto) if tem else None,
        f'diferenca_{rotulo}_segundos': (feito - previsto) if tem else None,
    }


def _resumo_da_jornada(usuario):
    """A escala contratada da pessoa, para a tela dizer de onde vem o previsto."""
    eid = getattr(usuario, 'tangerino_employee_id', None)
    if not eid:
        return None
    try:
        escala = next(((f.get('currentWorkSchedule') or {}).get('id')
                       for f in listar_funcionarios() if f.get('id') == eid), None)
    except TangerinoError:
        return None
    return JornadaTrabalho.objects.filter(tangerino_id=escala).first() if escala else None


def _jornada_do_usuario(usuario):
    """Grade contratada da pessoa: {dia_tangerino: segundos}, ou None.

    Lê da tabela local (JornadaTrabalho), sincronizada de
    ``/work-schedule/{id}``. Se ainda não foi sincronizada, devolve None e a
    tela mostra só o realizado — melhor não exibir previsto do que exibir um
    previsto inventado.
    """
    eid = getattr(usuario, 'tangerino_employee_id', None)
    if not eid:
        return None
    try:
        escala = next(((f.get('currentWorkSchedule') or {}).get('id')
                       for f in listar_funcionarios() if f.get('id') == eid), None)
        if not escala:
            return None
        registro = JornadaTrabalho.objects.filter(tangerino_id=escala).first()
        if not registro:
            return None
        return {int(dia): seg for dia, seg in (registro.horas_por_dia or {}).items()}
    except TangerinoError:
        return None


def _dias_com_marcacoes(pares, inicio, fim, grade=None, employee_id=None):
    """Agrupa os pares por dia, do mais recente para o mais antigo.

    Com ``grade``, cada dia também informa quanto a pessoa **deveria** ter
    trabalhado, já descontando feriado, férias e abonos.
    """
    por_dia = {}
    for par in pares:
        entrada = de_millis(par.get('dateIn'))
        if not entrada:
            continue
        por_dia.setdefault(entrada.date(), []).append(par)

    abonos = {}
    if grade:
        try:
            abonos = jornada_svc.carregar_abonos(inicio, fim)
        except TangerinoError:
            abonos = {}

    dias = []
    atual = fim
    while atual >= inicio:
        do_dia = por_dia.get(atual, [])
        eventos = ponto_svc._eventos(do_dia)
        segundos = ponto_svc._segundos_trabalhados(do_dia)
        aberto = any(de_millis(p.get('dateIn')) and not de_millis(p.get('dateOut'))
                     for p in do_dia)
        previsto = (jornada_svc.previsto_liquido(
            grade, atual, abonos.get((employee_id, atual), 0)) if grade else None)
        dias.append({
            'dia': atual,
            'eventos': eventos,
            'segundos': segundos,
            'horas': ponto_svc.formata_hhmm(segundos),
            'previsto_segundos': previsto,
            'previsto': jornada_svc.formata_hhmm(previsto) if previsto else None,
            # Só faz sentido comparar dia já encerrado: o de hoje ainda corre.
            'diferenca': (segundos - previsto) if previsto is not None else None,
            'aberto': aberto and atual < timezone.localdate(),
            'fim_de_semana': atual.weekday() >= 5,
            'sem_marcacao': not eventos,
        })
        atual -= timedelta(days=1)
    return dias


@login_required
def bloqueado(request):
    """Tela que a pessoa vê enquanto a jornada do dia estiver irregular.

    Sempre oferece uma saída: bater a marcação (quando o portal permite) ou
    reconferir depois de bater no relógio/app. Uma tela de bloqueio sem saída
    seria uma porta trancada por dentro.
    """
    from .middleware import decidir_bloqueio, limpar_decisao

    if request.GET.get('reconferir'):
        # A pessoa diz que bateu: derruba o cache e olha de novo na API.
        limpar_decisao(request.user)
        invalidar_cache_marcacoes(request.user.tangerino_employee_id)
        # O portal libera na hora, sem esperar o cache da decisão expirar.
        limpar_decisao(request.user)

    try:
        motivo = decidir_bloqueio(request.user)
    except Exception:
        motivo = None

    if not motivo:
        # Esta tela consulta ao vivo; o middleware decide por um cache de até
        # 60s. Sem derrubar o cache aqui, quem bateu no relógio físico entrava
        # em pingue-pongue: a tela mandava para a home e o middleware mandava
        # de volta, até o cache vencer sozinho.
        limpar_decisao(request.user)
        messages.success(request, 'Ponto em dia. Bom trabalho!')
        return redirect('home')

    config = ConfiguracaoTangerino.get()
    return render(request, 'tangerino/bloqueado.html', {
        'motivo': motivo,
        'config': config,
        'pode_bater_ponto': config.permitir_bater_ponto,
        'exige_foto': config.exigir_foto,
    })


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

        # Filtro por setor: aplicado ANTES dos contadores, senão os números do
        # topo continuariam falando da empresa inteira enquanto a tabela mostra
        # um setor só.
        escolhido = request.GET.get('setor') or ''
        mapa = _setores_por_employee()
        linhas, opcoes = _aplicar_filtro_setor(linhas, escolhido, mapa)
        sem_marcacao, opcoes_sem = _aplicar_filtro_setor(sem_marcacao, escolhido, mapa)
        # As opções saem das duas listas juntas: quem não bateu ponto no dia
        # também precisa poder ser filtrado pelo setor dele.
        vistos = {o['valor']: o for o in opcoes}
        vistos.update({o['valor']: o for o in opcoes_sem})
        opcoes = sorted(vistos.values(),
                        key=lambda o: ('zzz' if o['valor'] == SEM_SETOR else o['nome'].upper()))

        contexto.update({
            'linhas': sorted(linhas, key=lambda l: l['nome']),
            'sem_marcacao': sorted(sem_marcacao, key=lambda l: l['nome']),
            'setores': opcoes,
            'setor_escolhido': escolhido,
            'setor_nome': _nome_do_setor(opcoes, escolhido),
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
        # Popups de intervalo: almoço esquecido e almoço passando do limite.
        'avisos': regras.avisos(resumo, config),
        'volta_liberada': regras.volta_do_almoco_liberada(resumo, config)[0],
        'falta_para_volta': regras.volta_do_almoco_liberada(resumo, config)[1],
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

    # A volta do almoço tem duração mínima: bater antes disso cria um intervalo
    # inválido na folha, que alguém vai ter de ajustar à mão depois.
    if not atrasado:
        resumo = ponto_svc.resumo_para_usuario(request.user)
        if resumo.get('disponivel'):
            liberado, faltam = regras.volta_do_almoco_liberada(resumo, config)
            if not liberado:
                return JsonResponse(
                    {'sucesso': False, 'motivo': 'ALMOCO_CURTO', 'faltam_minutos': faltam,
                     'erro': f'O intervalo precisa ter pelo menos '
                             f'{config.almoco_minimo_minutos} minutos. '
                             f'Faltam {faltam} minuto(s) para você poder '
                             f'registrar a volta.'},
                    status=400)

    registro = RegistroPontoPortal(
        usuario=request.user, employee_id=request.user.tangerino_employee_id,
        momento=timezone.localtime(), atrasado=atrasado, com_foto=bool(foto),
        latitude=latitude, longitude=longitude,
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
        registro.foto_url = (resposta or {}).get('_foto_url', '') or ''
        registro.retorno = json.dumps(resposta, ensure_ascii=False)[:2000]
        registro.save()
        invalidar_cache_marcacoes(request.user.tangerino_employee_id)

        # Avisos honestos: o ponto entrou, mas se a foto ou a localização não
        # foram junto a pessoa precisa saber na hora, não no fim do mês.
        avisos = []
        if foto and not registro.foto_url:
            avisos.append('a foto não subiu')
        if not atrasado and latitude is None:
            avisos.append('a localização não foi enviada (o navegador não liberou)')

        status = ponto_svc.resumo_para_usuario(request.user)
        return JsonResponse({
            'sucesso': True,
            'mensagem': 'Ponto registrado no Tangerino.',
            'nsr': (resposta or {}).get('nsr'),
            'hora': registro.momento.strftime('%H:%M'),
            'avisos': avisos,
            'situacao': status.get('situacao'),
            'rotulo': status.get('rotulo'),
        })
    except (TangerinoError, ValueError, KeyError) as exc:
        registro.sucesso = False
        registro.retorno = str(exc)[:2000]
        registro.save()
        logger.warning('Falha ao bater ponto de %s: %s', request.user, exc)
        return JsonResponse({'sucesso': False, 'erro': str(exc)}, status=502)


# ─── Escala (quadro semanal montado no portal) ───────────────────────────────

MESES_PT = ['', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
            'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
DIAS_PT = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']


def _mapa_escala_dias(colaborador_ids, semana_inicio):
    """{(colaborador_id, data): EscalaDia} para uma semana, numa consulta só."""
    if not colaborador_ids:
        return {}
    qs = (EscalaDia.objects
          .filter(escala__colaborador_id__in=colaborador_ids,
                  escala__semana_inicio=semana_inicio)
          .select_related('escala'))
    return {(d.escala.colaborador_id, d.data): d for d in qs}


def _montar_grade(colaboradores, dias, mapa):
    """Uma linha por colaborador; uma célula por dia, mais o total da semana."""
    grade = []
    for c in colaboradores:
        celulas, minutos = [], 0
        for d in dias:
            ed = mapa.get((c.id, d))
            celulas.append({
                'data': d,
                'entrada': ed.entrada if ed else None,
                'saida_almoco': ed.saida_almoco if ed else None,
                'volta_almoco': ed.volta_almoco if ed else None,
                'saida': ed.saida if ed else None,
                'folga': ed.folga if ed else False,
            })
            minutos += ed.minutos if ed else 0
        grade.append({
            'colaborador': c,
            'celulas': celulas,
            'minutos': minutos,
            'horas': minutos / 60,
            'abaixo': minutos < HORAS_SEMANAIS * 60,
        })
    return grade


@login_required
def escala(request):
    """Aba Escala: gerente monta a da loja; gestor global vê tudo; colaborador vê a sua."""
    hoje = timezone.localdate()
    try:
        base = date.fromisoformat(request.GET['inicio']) if request.GET.get('inicio') else hoje
    except ValueError:
        base = hoje
    semana_inicio = escala_svc.monday_of(base)
    dias = [semana_inicio + timedelta(days=i) for i in range(7)]
    ano, mes = escala_svc.mes_de_referencia(semana_inicio)

    pode_gerenciar = escala_svc.pode_gerenciar(request.user)
    e_superadmin = e_gestor(request.user)

    contexto = {
        'aba': 'escala',
        'e_gestor': e_superadmin,
        # Esconde as abas de ponto/férias de quem não tem o módulo liberado: o
        # colaborador comum entra aqui só para ver a própria escala.
        'esconder_abas_ponto': not ConfiguracaoTangerino.get().libera(request.user),
        'hoje': hoje,
        'semana_inicio': semana_inicio,
        'semana_fim': semana_inicio + timedelta(days=6),
        'dias': list(zip(dias, DIAS_PT)),
        'semana_anterior': (semana_inicio - timedelta(days=7)).isoformat(),
        'semana_proxima': (semana_inicio + timedelta(days=7)).isoformat(),
        'ano': ano, 'mes': mes, 'mes_nome': MESES_PT[mes],
        'semanas': escala_svc.semanas_do_mes(ano, mes),
        'pode_gerenciar': pode_gerenciar,
        'e_global': escala_svc.e_gestor_global(request.user),
        'e_superadmin': e_superadmin,
        'horas_meta': HORAS_SEMANAIS,
    }

    if pode_gerenciar:
        setor_id = (request.GET.get('setor') or '').strip()
        colaboradores = list(escala_svc.colaboradores_geridos(
            request.user, setor_id=setor_id or None))
        mapa = _mapa_escala_dias([c.id for c in colaboradores], semana_inicio)
        contexto['grade'] = _montar_grade(colaboradores, dias, mapa)
        contexto['total_colaboradores'] = len(colaboradores)
        contexto['abaixo_da_meta'] = sum(1 for l in contexto['grade'] if l['abaixo'])
        contexto['setores'] = escala_svc.setores_para_filtro(request.user)
        contexto['setor_escolhido'] = setor_id
    else:
        obj = (Escala.objects.filter(colaborador=request.user, semana_inicio=semana_inicio)
               .prefetch_related('dias').first())
        por_data = {d.data: d for d in obj.dias.all()} if obj else {}
        contexto['minha_escala'] = [{'data': d, 'nome': DIAS_PT[i], 'dia': por_data.get(d)}
                                    for i, d in enumerate(dias)]
        contexto['tem_escala'] = any(por_data.get(d) and por_data[d].preenchido for d in dias)
        minutos = sum(por_data[d].minutos for d in dias if por_data.get(d))
        contexto['minhas_horas'] = minutos / 60
        contexto['minhas_horas_abaixo'] = minutos < HORAS_SEMANAIS * 60

    if e_superadmin:
        cfg = EscalaConfig.get()
        contexto['gestores_atuais'] = cfg.gestores.all()
        contexto['gestores_ids'] = set(cfg.gestores.values_list('id', flat=True))
        contexto['usuarios_para_gestor'] = (User.objects.filter(is_active=True)
                                            .order_by('first_name', 'last_name'))

    return render(request, 'tangerino/escala.html', contexto)


@login_required
@require_POST
def escala_salvar(request):
    """Grava a semana inteira de uma vez (grade de colaboradores × dias)."""
    if not escala_svc.pode_gerenciar(request.user):
        messages.error(request, 'Você não tem acesso para editar escalas.')
        return redirect('tangerino:escala')

    try:
        semana_inicio = escala_svc.monday_of(date.fromisoformat(request.POST['inicio']))
    except (KeyError, ValueError):
        messages.error(request, 'Semana inválida.')
        return redirect('tangerino:escala')

    dias = [semana_inicio + timedelta(days=i) for i in range(7)]
    # Trava de segurança: só grava quem o gestor de fato pode escalar, mesmo que
    # o POST traga outros ids.
    permitidos = {c.id for c in escala_svc.colaboradores_geridos(request.user)}
    alvos = {int(v) for v in request.POST.getlist('colaborador') if v.isdigit()} & permitidos

    salvos = 0
    for uid in alvos:
        planos = []
        for d in dias:
            iso = d.isoformat()
            folga = request.POST.get(f'folga_{uid}_{iso}') == 'on'
            entrada = parse_time(request.POST.get(f'entrada_{uid}_{iso}') or '')
            saida_almoco = parse_time(request.POST.get(f'saida_almoco_{uid}_{iso}') or '')
            volta_almoco = parse_time(request.POST.get(f'volta_almoco_{uid}_{iso}') or '')
            saida = parse_time(request.POST.get(f'saida_{uid}_{iso}') or '')
            horarios = (entrada, saida_almoco, volta_almoco, saida)
            planos.append((d, folga, horarios, bool(folga or any(horarios))))

        obj = Escala.objects.filter(colaborador_id=uid, semana_inicio=semana_inicio).first()
        algo = any(p[3] for p in planos)
        if not obj and not algo:
            continue
        if not obj:
            obj = Escala.objects.create(
                colaborador_id=uid, semana_inicio=semana_inicio,
                criado_por=request.user, atualizado_por=request.user)
        else:
            obj.atualizado_por = request.user
            obj.save(update_fields=['atualizado_por', 'atualizado_em'])

        for d, folga, horarios, tem in planos:
            if tem:
                entrada, saida_almoco, volta_almoco, saida = horarios
                EscalaDia.objects.update_or_create(
                    escala=obj, data=d,
                    defaults={'entrada': None if folga else entrada,
                              'saida_almoco': None if folga else saida_almoco,
                              'volta_almoco': None if folga else volta_almoco,
                              'saida': None if folga else saida, 'folga': folga})
            else:
                EscalaDia.objects.filter(escala=obj, data=d).delete()
        salvos += 1

    messages.success(request, f'Escala salva para {salvos} colaborador(es).')
    destino = f"{reverse('tangerino:escala')}?inicio={semana_inicio.isoformat()}"
    if request.POST.get('setor'):
        destino += f"&setor={request.POST['setor']}"
    return redirect(destino)


@login_required
@require_POST
def escala_gestores(request):
    """SUPERADMIN define quem, além dele, gere todas as escalas."""
    if not e_gestor(request.user):
        messages.error(request, 'Apenas o SUPERADMIN define os gestores de escala.')
        return redirect('tangerino:escala')

    ids = [int(v) for v in request.POST.getlist('gestores') if v.isdigit()]
    EscalaConfig.get().gestores.set(User.objects.filter(id__in=ids))
    messages.success(request, 'Gestores de escala atualizados.')
    return redirect('tangerino:escala')


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
        panorama = ferias_svc.panorama_da_empresa()
        escolhido = request.GET.get('setor') or ''
        linhas, opcoes = _aplicar_filtro_setor(panorama['linhas'], escolhido)

        # Os agrupamentos do topo são recalculados a partir das linhas já
        # filtradas — "3 de férias hoje" precisa ser 3 no setor escolhido.
        panorama = dict(panorama, linhas=linhas, total_pessoas=len(linhas))
        panorama['em_gozo'] = [l for l in linhas if l['situacao']['em_gozo']]
        panorama['vencidas'] = sorted([l for l in linhas if l['situacao']['vencidos']],
                                      key=lambda l: -l['situacao']['dias_vencidos'])
        panorama['vencendo'] = [l for l in linhas if l['situacao']['vencendo']]
        panorama['programadas'] = [l for l in linhas if l['situacao']['programadas']]

        contexto['panorama'] = panorama
        contexto['setores'] = opcoes
        contexto['setor_escolhido'] = escolhido
        contexto['setor_nome'] = _nome_do_setor(opcoes, escolhido)
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
            grade = _jornada_do_usuario(alvo)
            pares = listar_marcacoes(primeiro, min(ultimo, hoje),
                                     employee_id=alvo.tangerino_employee_id, ttl=300)
            dias = _dias_com_marcacoes(pares, primeiro, min(ultimo, hoje),
                                       grade=grade, employee_id=alvo.tangerino_employee_id)
            dias.reverse()                       # do dia 1 para o fim do mês
            contexto['dias'] = dias
            contexto['tem_jornada'] = bool(grade)
            contexto['jornada'] = _resumo_da_jornada(alvo)
            contexto.update(_previsto_x_feito(dias, hoje, rotulo='mes'))
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
                      'mostrar_popup_ferias', 'bloquear_navegacao_ferias',
                      'bloquear_sem_entrada', 'bloquear_durante_almoco',
                      'bloquear_saida_pendente', 'avisar_almoco'):
            setattr(config, campo, request.POST.get(campo) == 'on')

        # Parâmetros do intervalo: número inválido não pode virar zero em
        # silêncio — zero desligaria a duração mínima sem ninguém perceber.
        for campo, minimo, maximo in (('almoco_minimo_minutos', 1, 240),
                                      ('almoco_maximo_minutos', 1, 240)):
            bruto = (request.POST.get(campo) or '').strip()
            if bruto.isdigit() and minimo <= int(bruto) <= maximo:
                setattr(config, campo, int(bruto))
        for campo in ('lembrete_almoco_hora', 'entrada_manha_de', 'entrada_manha_ate'):
            hora = parse_time(request.POST.get(campo) or '')
            if hora:
                setattr(config, campo, hora)

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
        'saldos_no_banco': SaldoHoras.objects.count(),
        'saldo_exemplo': SaldoHoras.objects.order_by('saldo_minutos').first(),
        'ultima_saldo': SincronizacaoTangerino.objects.filter(
            tipo=SincronizacaoTangerino.Tipo.SALDO).first(),
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
    # A jornada vem primeiro: o previsto de cada dia depende dela.
    if alvo in ('jornada', 'ponto', 'tudo'):
        tarefas.append((SincronizacaoTangerino.Tipo.JORNADA, 'Jornadas contratadas',
                        sincronizar_jornadas))
    if alvo in ('ponto', 'tudo'):
        tarefas.append((SincronizacaoTangerino.Tipo.PONTO, 'Marcações',
                        lambda: sincronizar_marcacoes(dias=dias)))
    if alvo in ('ferias', 'tudo'):
        tarefas.append((SincronizacaoTangerino.Tipo.FERIAS, 'Férias', sincronizar_ferias))
    if alvo in ('saldo', 'tudo'):
        tarefas.append((SincronizacaoTangerino.Tipo.SALDO, 'Saldo de horas', sincronizar_saldos))
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
                f"{rotulo}: {resultado.get('lidos', resultado.get('escalas', 0))} lidos, "
                f"{resultado['criados']} novos, {resultado['atualizados']} atualizados.")
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
