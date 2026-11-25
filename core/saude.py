"""/saude/ — a conferência de saúde do contêiner — e o vigia das requisições presas.

``/saude/`` responde "ok" antes de qualquer outro middleware: sem sessão, banco, Redis nem
checagem de host. Antes a conferência abria "/", que passava por ~20 consultas de
middleware; com os workers ocupados ela estourava os 30 s, o contêiner era marcado doente e
reiniciado — e o reinício (migrate + collectstatic) tirava o portal do ar por um minuto e
meio (25/11/2026: quedas às 10:55, 11:05 e 11:19, com o banco parado nos três momentos).

O **vigia**: cada requisição em andamento fica anotada (thread, caminho, usuário, início).
Uma thread por worker olha a cada 15 s; a requisição que passa de ``PRESA_APOS`` segundos
tem a pilha (onde o código está parado naquele instante) escrita no log do servidor, uma
vez só. É o que faltava para saber o que segura o portal: a página que demora não chega ao
histórico de acessos se o worker morrer antes de terminar.
"""
import logging
import os
import sys
import threading
import time
import traceback

from django.http import HttpResponse

logger = logging.getLogger('core.saude')

PRESA_APOS = int(os.environ.get('RC_REQUISICAO_PRESA_APOS', '45'))
OLHAR_A_CADA = 15
# Caminhos que demoram de propósito (upload de vídeo, leitura do SAP...): não são "presos".
DEMORAM_DE_PROPOSITO = ('/sap/atualizar/', '/entrevista', '/drive/upload', '/media/')

_em_andamento = {}          # ident da thread → (início, método, caminho, usuário, já avisou)
_trava = threading.Lock()
_vigia = None


def _olhar():
    while True:
        time.sleep(OLHAR_A_CADA)
        try:
            agora = time.monotonic()
            with _trava:
                presas = [(ident, dados) for ident, dados in _em_andamento.items()
                          if not dados[4] and agora - dados[0] > PRESA_APOS]
                for ident, dados in presas:
                    _em_andamento[ident] = dados[:4] + (True,)
                ocupadas = len(_em_andamento)
            if not presas:
                continue
            quadros = sys._current_frames()
            for ident, (inicio, metodo, caminho, usuario, _avisou) in presas:
                quadro = quadros.get(ident)
                pilha = ''.join(traceback.format_stack(quadro)[-14:]) if quadro is not None else '(sem pilha)'
                logger.warning('REQUISIÇÃO PRESA há %ds (pid %s, %d em andamento neste worker): %s %s — %s\n%s',
                               int(agora - inicio), os.getpid(), ocupadas, metodo, caminho, usuario, pilha)
        except Exception:  # noqa: BLE001 — o vigia nunca derruba nada
            pass


def _garantir_vigia():
    global _vigia
    if _vigia is None or not _vigia.is_alive():
        with _trava:
            if _vigia is None or not _vigia.is_alive():
                _vigia = threading.Thread(target=_olhar, name='vigia-requisicoes', daemon=True)
                _vigia.start()


def em_andamento():
    """Fotografia das requisições deste worker: [(segundos, método, caminho, usuário)]."""
    agora = time.monotonic()
    with _trava:
        return sorted(((int(agora - d[0]), d[1], d[2], d[3]) for d in _em_andamento.values()), reverse=True)


class SaudeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == '/saude/':
            return HttpResponse('ok', content_type='text/plain')
        if request.path.startswith(DEMORAM_DE_PROPOSITO):
            return self.get_response(request)
        _garantir_vigia()
        ident = threading.get_ident()
        with _trava:
            _em_andamento[ident] = (time.monotonic(), request.method, request.path[:200], '', False)
        try:
            return self.get_response(request)
        finally:
            with _trava:
                _em_andamento.pop(ident, None)

    def process_view(self, request, *args, **kwargs):
        # Depois da autenticação, o usuário entra na anotação (para o log dizer de quem é).
        ident = threading.get_ident()
        usuario = getattr(getattr(request, 'user', None), 'username', '') or ''
        with _trava:
            dados = _em_andamento.get(ident)
            if dados is not None:
                _em_andamento[ident] = dados[:3] + (usuario,) + dados[4:]
        return None
