"""Usuário do portal inativado → aviso no Acess (desligamento imediato ou agendado).

Qualquer save() que mude is_active passa por aqui (edição, importação, Quadro de HC...).
"""
import logging

from django.db import transaction

_SEM_VALOR = object()


def _guardar(sender, instance, **kwargs):
    # __dict__ e não o atributo: num .only() sem is_active, ler o campo faria uma consulta por linha.
    instance._acess_ativo_antes = instance.__dict__.get('is_active', _SEM_VALOR)


def _conferir(sender, instance, created, update_fields=None, raw=False, **kwargs):
    if raw or created:
        return
    if update_fields is not None and 'is_active' not in set(update_fields):
        return
    antes = getattr(instance, '_acess_ativo_antes', _SEM_VALOR)
    agora = instance.__dict__.get('is_active', _SEM_VALOR)
    instance._acess_ativo_antes = agora
    if antes is _SEM_VALOR or agora is _SEM_VALOR or antes == agora:
        return
    try:
        with transaction.atomic():  # falha aqui nunca derruba o save do usuário
            from . import servicos
            if antes and not agora:
                servicos.avisar_inativacao(instance)
            elif agora and not antes:
                servicos.descartar_avisos_do_reativado(instance)
    except Exception:  # noqa: BLE001 — tabela ainda não migrada noutro servidor
        logging.getLogger(__name__).exception('Acess: aviso de inativação falhou para o usuário %s', instance.pk)


def conectar():
    from django.contrib.auth import get_user_model
    from django.db.models.signals import post_init, post_save
    User = get_user_model()
    post_init.connect(_guardar, sender=User, dispatch_uid='acess_inativacao_init')
    post_save.connect(_conferir, sender=User, dispatch_uid='acess_inativacao_save')
