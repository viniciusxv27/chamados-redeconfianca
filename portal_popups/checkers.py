"""Registro de "checkers" para popups de tarefa externa.

Um popup com modo de conclusão EXTERNAL não é concluído por um clique, e sim
quando uma tarefa do sistema é cumprida (ex.: a Pesquisa de Clima é respondida).
Cada tarefa dessas registra aqui uma função `checker(user) -> bool` que devolve
True quando o usuário já cumpriu a tarefa.

Outros apps registram seus checkers no `ready()` do seu AppConfig, para que a
tela de gestão de popups ofereça a opção sem precisar mexer neste arquivo.
"""

_CHECKERS = {}


def register_popup_checker(key, label):
    """Registra uma função de verificação sob uma chave estável.

    Uso:
        @register_popup_checker('climate_survey', 'Pesquisa de Clima respondida')
        def _climate(user): ...
    """
    def decorator(func):
        _CHECKERS[key] = {'label': label, 'func': func}
        return func
    return decorator


def available_checkers():
    """Lista [(key, label)] para popular os choices na tela de gestão."""
    return [(key, data['label']) for key, data in sorted(_CHECKERS.items())]


def run_checker(key, user):
    """Executa o checker da chave.

    Fail-open por segurança: se o checker não estiver registrado (ex.: o app que
    o fornece não carregou) ou lançar erro, tratamos a tarefa como concluída. É
    preferível um popup obrigatório deixar de aparecer a prender TODOS os
    usuários no portal por uma falha de infraestrutura — mesmo princípio do gate
    anterior ("nunca prende o usuário no portal")."""
    entry = _CHECKERS.get(key)
    if not entry:
        return True
    try:
        return bool(entry['func'](user))
    except Exception:
        return True
