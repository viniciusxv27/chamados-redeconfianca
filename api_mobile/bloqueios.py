"""Os bloqueios do portal valem no app.

O portal web tranca a navegação por middleware (férias, jornada irregular no
ponto, curso Vivo vencido, trilha obrigatória). O app não passa por esses
redirecionamentos — a autenticação por token acontece depois dos middlewares —,
então perguntamos aos próprios middlewares: roda cada um com uma requisição de
mentira para a home e, se ele redirecionar, a pessoa está bloqueada. Assim a
regra (e as exceções de cada uma) continua morando num lugar só.
"""
import logging

from django.core.cache import caches
from django.test import RequestFactory

logger = logging.getLogger(__name__)

SEGUNDOS_CACHE = 60
_SEGUE = object()

# (tipo, título, mensagem, caminho da classe do middleware)
TRAVAS = (
    ('ferias', 'Você está de férias',
     'O portal fica fechado durante as férias. Aproveite o descanso!',
     'tangerino.middleware.BloqueioFeriasMiddleware'),
    ('jornada', 'Jornada pendente no ponto',
     'Há uma pendência na sua jornada de hoje. Resolva no portal para continuar.',
     'tangerino.middleware.BloqueioJornadaMiddleware'),
    ('curso', 'Curso obrigatório vencido',
     'Há um curso obrigatório vencido sem comprovante. Envie o comprovante no portal para liberar.',
     'cursos.middleware.BloqueioCursoMiddleware'),
    ('trilha', 'Trilha obrigatória pendente',
     'Conclua a trilha obrigatória no portal para liberar o acesso.',
     'knowledge_trails.bloqueio.BloqueioTrilhaMiddleware'),
)


def _classe(caminho):
    modulo, nome = caminho.rsplit('.', 1)
    return getattr(__import__(modulo, fromlist=[nome]), nome)


def bloqueio_atual(user, usar_cache=True):
    """{tipo, titulo, mensagem, caminho_web} do bloqueio que vale agora, ou None."""
    cache = caches['local']
    chave = f'api_mobile:bloqueio:{user.pk}'
    if usar_cache:
        guardado = cache.get(chave)
        if guardado is not None:
            return guardado or None

    resultado = {}
    for tipo, titulo, mensagem, caminho in TRAVAS:
        try:
            requisicao = RequestFactory().get('/')
            requisicao.user = user
            requisicao.session = {}
            resposta = _classe(caminho)(lambda r: _SEGUE)(requisicao)
        except Exception as exc:                       # mesma régua do portal: falha aberta
            logger.warning('Bloqueio %s ignorado no app por erro: %s', tipo, exc)
            continue
        if resposta is not _SEGUE and getattr(resposta, 'status_code', 0) in (301, 302):
            resultado = {'tipo': tipo, 'titulo': titulo, 'mensagem': mensagem,
                         'caminho_web': resposta.get('Location') or '/'}
            break
    cache.set(chave, resultado, SEGUNDOS_CACHE)
    return resultado or None
