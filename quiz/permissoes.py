"""Quem faz o quê no Quiz.

- Jogar: só quem foi selecionado para a sala — e só nas salas dele.
- Criar e administrar (quizzes, perguntas, salas, resultados e relatórios):
  SUPERADMIN e quem ele liberar na tela do usuário (/users/manage/users/<id>/edit/),
  com a chave ``quiz.gestao``.
- Conduzir a partida (iniciar, avançar, encerrar): o responsável pela sala e o
  SUPERADMIN — dois gestores apertando botões na mesma sala atrapalhariam o jogo.
"""


def e_superadmin(user):
    return bool(user and getattr(user, 'is_authenticated', False)
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def pode_gerenciar(user):
    """Cria quizzes e salas, vê resultados e relatórios."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    if e_superadmin(user):
        return True
    from users.module_access import user_has_module
    return user_has_module(user, 'quiz.gestao')


def pode_conduzir(user, sala):
    """Aperta os botões da partida desta sala."""
    return pode_gerenciar(user) and (e_superadmin(user) or sala.responsavel_id == user.id)


def participacao(user, sala):
    """A vaga desta pessoa na sala, ou None se ela não foi chamada."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return None
    return sala.participantes.filter(user=user).first()
