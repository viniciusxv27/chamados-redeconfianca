"""Quem enxerga o quê no módulo de cursos.

Separado das views porque o menu e o middleware também precisam da resposta.
"""
from .models import ConfiguracaoCursos


def config():
    return ConfiguracaoCursos.get()


def e_superadmin(user):
    return bool(user and user.is_authenticated
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def e_gestor(user, cfg=None):
    """Publica curso, escreve orientações e confere comprovante."""
    return (cfg or config()).e_gestor(user)


def no_escopo(user, cfg=None):
    """É cobrado pelos cursos."""
    return (cfg or config()).no_escopo(user)


def pode_ver(user, cfg=None):
    cfg = cfg or config()
    return e_gestor(user, cfg) or no_escopo(user, cfg)


def cursos_do_usuario(user, cfg=None):
    """Cursos publicados que valem para esta pessoa, do mais urgente ao resto."""
    from .models import Curso

    cfg = cfg or config()
    qs = Curso.objects.filter(publicado=True)
    if no_escopo(user, cfg):
        alcance = qs.filter(tipo=Curso.FOCO) | qs.filter(atribuicoes__colaborador=user)
    else:
        alcance = qs.filter(atribuicoes__colaborador=user)
    return alcance.distinct().order_by('prazo', 'id')


def pendencias(user, cfg=None):
    """Cursos que a pessoa ainda não comprovou. Recusado volta a pendurar."""
    from .models import Comprovante

    cursos = list(cursos_do_usuario(user, cfg))
    if not cursos:
        return []
    entregues = set(
        Comprovante.objects
        .filter(colaborador=user, curso__in=cursos,
                status__in=(Comprovante.PENDENTE, Comprovante.APROVADO))
        .values_list('curso_id', flat=True))
    return [c for c in cursos if c.id not in entregues]


def vencidos_sem_comprovante(user, cfg=None):
    from django.utils import timezone
    hoje = timezone.localdate()
    return [c for c in pendencias(user, cfg) if c.prazo < hoje]
