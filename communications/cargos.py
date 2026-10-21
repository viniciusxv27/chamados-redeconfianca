"""Grupo por cargo: quem tem um dos cargos do grupo entra sozinho; mudou de cargo, sai.

Só sai quem entrou pelo cargo (MembroPeloCargo). Quem o gestor marcou à mão
continua no grupo mesmo trocando de cargo — tirar essa pessoa é decisão dele.
Desligado (is_active=False) não entra, e quem entrou pelo cargo sai.
"""
import re
import unicodedata

from django.db import transaction


def normalizar(cargo):
    """Mesma chave para "Gerente de Loja", "GERENTE  DE LOJA " e "GERENTE DE LÔJA"."""
    texto = unicodedata.normalize('NFKD', str(cargo or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'\s+', ' ', texto).strip().upper()


def _grupos_por_cargo():
    from .models import CargoDoGrupo
    mapa = {}
    for grupo_id, cargo in CargoDoGrupo.objects.values_list('grupo_id', 'cargo'):
        mapa.setdefault(normalizar(cargo), set()).add(grupo_id)
    return mapa


def sincronizar_usuario(user):
    """Acerta os grupos de uma pessoa pelo cargo atual dela. Devolve (entrou, saiu) em ids de grupo."""
    from .models import CommunicationGroup, MembroPeloCargo
    chave = normalizar(user.job_title)
    devidos = _grupos_por_cargo().get(chave, set()) if (chave and user.is_active) else set()
    pelo_cargo = {m.grupo_id: m for m in MembroPeloCargo.objects.filter(user=user)}
    ja_membro = set(CommunicationGroup.members.through.objects.filter(user_id=user.pk)
                    .values_list('communicationgroup_id', flat=True)) if devidos else set()
    entrou, saiu = [], []
    for grupo_id in devidos:
        if grupo_id in pelo_cargo:
            if pelo_cargo[grupo_id].cargo != user.job_title:
                MembroPeloCargo.objects.filter(pk=pelo_cargo[grupo_id].pk).update(cargo=user.job_title or '')
            continue
        if grupo_id in ja_membro:  # já estava, posto à mão: continua sendo do gestor
            continue
        CommunicationGroup.members.through.objects.get_or_create(user_id=user.pk, communicationgroup_id=grupo_id)
        MembroPeloCargo.objects.create(grupo_id=grupo_id, user=user, cargo=user.job_title or '')
        entrou.append(grupo_id)
    for grupo_id, m in pelo_cargo.items():
        if grupo_id not in devidos:
            CommunicationGroup.members.through.objects.filter(user_id=user.pk, communicationgroup_id=grupo_id).delete()
            m.delete()
            saiu.append(grupo_id)
    return entrou, saiu


def sincronizar_grupo(grupo):
    """Depois de mudar os cargos do grupo: entra quem tem o cargo, sai quem entrou por cargo que não vale mais."""
    from users.models import User

    from .models import MembroPeloCargo
    chaves = {normalizar(c) for c in grupo.cargos.values_list('cargo', flat=True)}
    devidos = {}
    if chaves:
        for uid, cargo in User.objects.filter(is_active=True).exclude(job_title='').values_list('id', 'job_title'):
            if normalizar(cargo) in chaves:
                devidos[uid] = cargo
    pelo_cargo = {m.user_id: m for m in MembroPeloCargo.objects.filter(grupo=grupo)}
    membros = set(grupo.members.values_list('id', flat=True))
    novos = [uid for uid in devidos if uid not in membros]
    with transaction.atomic():
        grupo.members.add(*novos)
        MembroPeloCargo.objects.bulk_create([MembroPeloCargo(grupo=grupo, user_id=uid, cargo=devidos[uid]) for uid in novos])
        fora = [uid for uid in pelo_cargo if uid not in devidos]
        grupo.members.remove(*fora)
        MembroPeloCargo.objects.filter(grupo=grupo, user_id__in=fora).delete()
    return novos, fora


# ---------------------------------------------------------------------------
# Gatilho: qualquer save() do usuário que mude cargo ou situação (edição,
# importação, Quadro de HC...) acerta os grupos. update()/bulk_update passam por fora.
# ---------------------------------------------------------------------------
_NAO_CARREGADO = object()


def _guardar_cargo(sender, instance, **kwargs):
    # __dict__ e não o atributo: num .only() sem job_title, ler o campo faria uma consulta por linha.
    instance._cargo_grupos = (instance.__dict__.get('job_title', _NAO_CARREGADO),
                              instance.__dict__.get('is_active', _NAO_CARREGADO))


def _acertar_grupos(sender, instance, created, update_fields=None, raw=False, **kwargs):
    if raw:
        return
    if update_fields is not None and not ({'job_title', 'is_active'} & set(update_fields)):
        return
    antes = getattr(instance, '_cargo_grupos', (_NAO_CARREGADO, _NAO_CARREGADO))
    agora = (instance.__dict__.get('job_title', _NAO_CARREGADO), instance.__dict__.get('is_active', _NAO_CARREGADO))
    instance._cargo_grupos = agora
    if not created and antes == agora:
        return
    try:
        with transaction.atomic():  # falha aqui nunca derruba o save do usuário
            sincronizar_usuario(instance)
    except Exception:  # noqa: BLE001 — tabela ainda não migrada noutro servidor
        import logging
        logging.getLogger(__name__).exception('grupo por cargo: falhou para o usuário %s', instance.pk)


def conectar():
    from django.contrib.auth import get_user_model
    from django.db.models.signals import post_init, post_save
    User = get_user_model()
    post_init.connect(_guardar_cargo, sender=User, dispatch_uid='grupo_por_cargo_init')
    post_save.connect(_acertar_grupos, sender=User, dispatch_uid='grupo_por_cargo_save')
