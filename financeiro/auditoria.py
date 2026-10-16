"""Rastreabilidade: todo movimento do Financeiro deixa um registro.

Quem (o usuário e, congelados na hora, nome e setor), o quê (ação, objeto,
valor, loja), quando (data e hora), de onde (IP, navegador/dispositivo e a tela)
e o antes/depois dos campos que mudaram. Os registros não se editam nem se
apagam pelo portal.
"""
import logging
import uuid
from datetime import date, datetime
from decimal import Decimal

from django.db import models, transaction
from django.db.models.fields.files import FieldFile

from .models import LogFinanceiro

logger = logging.getLogger('financeiro.auditoria')


def ip_do_request(request):
    if request is None:
        return None
    encaminhado = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    ip = encaminhado or request.META.get('HTTP_X_REAL_IP') or request.META.get('REMOTE_ADDR') or ''
    return ip or None


def _valor_json(valor):
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    if isinstance(valor, models.Model):
        return str(valor)
    if isinstance(valor, uuid.UUID):
        return str(valor)
    if isinstance(valor, FieldFile):
        return valor.name or ''
    return valor


def foto(obj, campos=None):
    """Os campos do objeto num dict que cabe em JSON (FK vira o texto dela)."""
    if obj is None:
        return None
    dados = {}
    for campo in obj._meta.concrete_fields:
        if campos and campo.name not in campos:
            continue
        if campo.name in ('criado_em', 'atualizado_em'):
            continue
        if campo.is_relation:
            alvo = getattr(obj, campo.name, None)
            dados[campo.name] = str(alvo) if alvo is not None else None
        else:
            dados[campo.name] = _valor_json(getattr(obj, campo.attname))
    return dados


def diferenca(antes, depois):
    """Só o que mudou: ({campo: antes}, {campo: depois})."""
    if not antes or not depois:
        return antes, depois
    mudou = [k for k in depois if antes.get(k) != depois.get(k)]
    return {k: antes.get(k) for k in mudou}, {k: depois.get(k) for k in mudou}


def registrar(request, acao, descricao, area='', obj=None, valor=None, loja=None, antes=None, depois=None,
              usuario=None):
    """Grava o registro. Nunca derruba a operação: falha vira log de erro."""
    usuario = usuario or (getattr(request, 'user', None) if request is not None else None)
    if usuario is not None and not getattr(usuario, 'is_authenticated', False):
        usuario = None
    try:
        setor = getattr(usuario, 'sector', None) if usuario else None
        with transaction.atomic():  # savepoint: um erro aqui não envenena a operação
            LogFinanceiro.objects.create(
                usuario=usuario,
                usuario_nome=((usuario.get_full_name() or usuario.email or usuario.username) if usuario else 'sistema')[:200],
                usuario_setor=(setor.name if setor else '')[:120],
                acao=acao, area=area,
                objeto_tipo=obj._meta.model_name if obj is not None else '',
                objeto_id=str(obj.pk) if obj is not None and obj.pk else '',
                descricao=descricao[:300], valor=valor,
                loja=loja if loja is not None else getattr(obj, 'loja', None) if obj is not None else None,
                antes=antes, depois=depois,
                ip=ip_do_request(request),
                dispositivo=(request.META.get('HTTP_USER_AGENT', '') if request is not None else '')[:255],
                rota=(request.path if request is not None else '')[:255],
            )
    except Exception:  # noqa: BLE001
        logger.exception('Falha ao gravar a auditoria do Financeiro: %s', descricao)
