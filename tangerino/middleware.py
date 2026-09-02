"""Bloqueio de navegação para quem está de férias.

Quem está em período de férias aprovado não navega pelo portal: cai numa tela
explicando até quando. O bloqueio é deliberadamente **frouxo em caso de erro** —
se a API do Tangerino estiver fora, ninguém é trancado do lado de fora.
"""
import logging

from django.shortcuts import redirect
from django.urls import reverse

from .ferias import esta_de_ferias
from tangerino.agendador import disparar_se_esta_na_hora


logger = logging.getLogger(__name__)

# Caminhos que continuam abertos: sair da conta, a própria tela de férias, o
# painel de férias (a pessoa pode querer conferir as datas), arquivos estáticos
# e as APIs internas que a tela de bloqueio usa.
LIBERADOS = (
    '/logout', '/login', '/static/', '/media/', '/admin/',
    '/ferias/', '/sw.js', '/OneSignalSDKWorker.js',
)


class BloqueioFeriasMiddleware:
    """Redireciona colaboradores de férias para a tela de aviso."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, 'user', None)
        if usuario is None or not usuario.is_authenticated:
            return self.get_response(request)

        # Sem cron em produção, quem acorda a sincronização diária é a primeira
        # visita depois da hora marcada. Sai daqui em microssegundos: o
        # agendador só consulta o banco uma vez por minuto por worker, e o
        # trabalho de verdade acontece numa thread.
        disparar_se_esta_na_hora()

        caminho = request.path
        if any(caminho.startswith(p) for p in LIBERADOS):
            return self.get_response(request)

        # Administradores continuam entrando: quem cuida do portal pode precisar
        # trabalhar mesmo estando de férias no sistema de ponto.
        if usuario.is_superuser or getattr(usuario, 'hierarchy', '') == 'SUPERADMIN':
            return self.get_response(request)

        try:
            # O bloqueio é opcional e vale apenas para quem tem o módulo
            # liberado — quem está fora do grupo nem sabe que ele existe.
            from .models import ConfiguracaoTangerino
            config = ConfiguracaoTangerino.get()
            if not (config.bloquear_navegacao_ferias and config.libera(usuario)):
                return self.get_response(request)

            if esta_de_ferias(usuario):
                return redirect(reverse('tangerino:em_ferias'))
        except Exception as exc:                     # nunca derruba a navegação
            logger.warning('Bloqueio de férias ignorado por erro: %s', exc)

        return self.get_response(request)


# ─────────────────────────────────────────────────────────────────────────────
# Bloqueio por jornada: entrada não batida, almoço em andamento, saída em aberto
# ─────────────────────────────────────────────────────────────────────────────

# Além dos caminhos de sempre, a tela de bloqueio precisa das APIs de ponto —
# é por elas que a pessoa registra a marcação que a destrava.
LIBERADOS_JORNADA = LIBERADOS + (
    '/ponto/bloqueado', '/api/tangerino/ponto/',
)

# `/ponto/` é a tela onde a marcação é batida — bloqueá-la fazia o botão
# "Registrar a volta do intervalo" devolver a pessoa para a tela de bloqueio,
# que era o efeito de "fica recarregando a página".
#
# A liberação é por igualdade exata, e não por prefixo: `/ponto/equipe/`,
# `/ponto/folhas/` e `/ponto/configuracao/` continuam trancados, senão o
# bloqueio viraria um convite a passear pelo módulo inteiro.
EXATOS_JORNADA = ('/ponto/',)

# Quanto tempo a decisão fica em memória do processo. Sem isto, cada clique no
# portal viraria uma consulta ao Tangerino. Um minuto é curto o bastante para a
# liberação parecer imediata — e a marcação pelo portal limpa o cache na hora.
CACHE_DECISAO_SEGUNDOS = 60


def _cache():
    from django.core.cache import caches
    return caches['local']


def chave_da_decisao(user):
    from django.utils import timezone
    return f'tangerino:jornada:{user.pk}:{timezone.localdate()}'


def limpar_decisao(user):
    """Chamado depois de bater ponto, para o portal liberar na hora."""
    try:
        _cache().delete(chave_da_decisao(user))
    except Exception:                                  # cache indisponível
        pass


def _trabalha_hoje(user):
    """A pessoa tem jornada prevista para hoje?

    Quem está na escala "não registra ponto", de folga ou de férias não pode
    ser travado por não ter batido um ponto que ninguém espera dela.
    """
    from django.utils import timezone
    from .jornada import previsto_no_dia
    from .models import JornadaTrabalho

    chave = f'tangerino:trabalha:{user.tangerino_employee_id}:{timezone.localdate()}'
    guardado = _cache().get(chave)
    if guardado is not None:
        return guardado

    from .client import listar_funcionarios
    escala = next((( f.get('currentWorkSchedule') or {}).get('id')
                   for f in listar_funcionarios()
                   if f.get('id') == user.tangerino_employee_id), None)
    jornada = JornadaTrabalho.objects.filter(tangerino_id=escala).first() if escala else None
    grade = {int(d): s for d, s in (jornada.horas_por_dia or {}).items()} if jornada else {}
    resposta = bool(previsto_no_dia(grade, timezone.localdate()))

    _cache().set(chave, resposta, 60 * 60)
    return resposta


def decidir_bloqueio(user):
    """O bloqueio que se aplica a esta pessoa agora, ou None."""
    from . import ponto as ponto_svc
    from .models import ConfiguracaoTangerino
    from .regras_jornada import bloqueio

    from .models import nao_bate_ponto

    # Quem não bate ponto não pode ser trancado por causa de ponto.
    if nao_bate_ponto(user):
        return None

    config = ConfiguracaoTangerino.get()
    regras_ligadas = (config.bloquear_sem_entrada or config.bloquear_durante_almoco
                      or config.bloquear_saida_pendente)
    if not regras_ligadas or not config.libera(user):
        return None
    if not getattr(user, 'tangerino_employee_id', None):
        return None
    if not _trabalha_hoje(user):
        return None

    resumo = ponto_svc.resumo_para_usuario(user)
    if not resumo.get('disponivel'):                   # API fora: não tranca
        return None
    return bloqueio(resumo, resumo.get('pendencias') or [], config)


class BloqueioJornadaMiddleware:
    """Tranca o portal enquanto a jornada do dia estiver irregular.

    Falha aberta em qualquer erro: o preço de deixar de aplicar a regra é bem
    menor que o de trancar a empresa inteira do lado de fora por um timeout.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, 'user', None)
        if usuario is None or not usuario.is_authenticated:
            return self.get_response(request)

        if (request.path in EXATOS_JORNADA
                or any(request.path.startswith(p) for p in LIBERADOS_JORNADA)):
            return self.get_response(request)

        # Quem administra o portal não pode ficar preso do lado de fora — é
        # quem desliga a regra se ela sair errada.
        if usuario.is_superuser or getattr(usuario, 'hierarchy', '') == 'SUPERADMIN':
            return self.get_response(request)

        try:
            chave = chave_da_decisao(usuario)
            decisao = _cache().get(chave)
            if decisao is None:
                decisao = decidir_bloqueio(usuario) or {}
                _cache().set(chave, decisao, CACHE_DECISAO_SEGUNDOS)
            if decisao:
                return redirect(reverse('tangerino:bloqueado'))
        except Exception as exc:                       # nunca derruba a navegação
            logger.warning('Bloqueio de jornada ignorado por erro: %s', exc)

        return self.get_response(request)
