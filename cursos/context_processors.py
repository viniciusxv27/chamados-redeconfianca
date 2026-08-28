"""Dados do módulo de cursos que o menu e a home precisam."""
from .permissions import ConfiguracaoCursos, e_gestor, pendencias, pode_ver


def cursos_menu(request):
    user = getattr(request, 'user', None)
    vazio = {'pode_ver_cursos': False, 'is_gestor_cursos': False, 'cursos_pendentes': 0}
    if not (user and user.is_authenticated):
        return vazio
    try:
        cfg = ConfiguracaoCursos.get()
        if not pode_ver(user, cfg):
            return vazio
        return {
            'pode_ver_cursos': True,
            'is_gestor_cursos': e_gestor(user, cfg),
            'cursos_pendentes': len(pendencias(user, cfg)),
        }
    except Exception:                       # nunca derruba o menu do portal
        return vazio
