"""Registra o checker de popup fornecido pelo app de documentos.

Importado no `ready()` do DocumentosConfig, para que o sistema de popups
(portal_popups) saiba quando um usuário ainda tem documentos pendentes de
assinatura — exibindo o lembrete até que todas as assinaturas estejam em dia.
"""
from portal_popups.checkers import register_popup_checker


@register_popup_checker('documentos_pendentes', 'Documentos: todas as assinaturas em dia')
def no_pending_documents(user):
    """True quando o usuário NÃO tem documentos pendentes de assinatura.

    O popup (modo "Tarefa do sistema") é tratado como concluído quando este
    checker retorna True — ou seja, o lembrete some assim que a pessoa assina
    todos os documentos atribuídos a ela.
    """
    from .models import DocumentSignature
    return not DocumentSignature.objects.filter(
        user=user, signed_at__isnull=True
    ).exists()
