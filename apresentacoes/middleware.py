"""Deixa as telas do portal abrirem num iframe do próprio portal durante a captura.

O "Gerar apresentação de um módulo" fotografa as telas no navegador de quem
pediu, abrindo cada uma num iframe invisível. O portal responde
`X-Frame-Options: DENY` (padrão do Django), o que bloqueia até o iframe da
mesma origem. Aqui a resposta vira SAMEORIGIN só quando a URL traz
`?captura_apresentacao=1` e quem pede usa o módulo — outra origem continua sem
conseguir emoldurar o portal.

Fica DEPOIS do XFrameOptionsMiddleware na lista: a resposta passa por aqui
antes, e o dele não sobrescreve um cabeçalho já definido.
"""


class CapturaDeTelaMiddleware:
    PARAMETRO = 'captura_apresentacao'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.method == 'GET' and request.GET.get(self.PARAMETRO) == '1':
            user = getattr(request, 'user', None)
            if user is not None and user.is_authenticated:
                from .permissoes import pode_usar
                if pode_usar(user):
                    response['X-Frame-Options'] = 'SAMEORIGIN'
        return response
