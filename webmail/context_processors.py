"""A bolinha de não lidas ao lado de "E-mail" no menu lateral.

Roda em toda página, então não vai ao servidor de e-mail: entrega o número que
está no cache (webmail/views.py guarda) e diz se vale a página pedir um número
novo — só para quem tem caixa conectada e quando o guardado já venceu.
"""
from django.core.cache import cache

TEM_CONTA_SEGUNDOS = 600


def chave_tem_conta(user_id):
    return f'webmail:tem_conta:{user_id}'


def email_menu(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {}
    tem = cache.get(chave_tem_conta(user.pk))
    if tem is None:
        from .models import ContaEmail
        tem = ContaEmail.objects.filter(usuario_id=user.pk, senha_recusada=False).exists()
        cache.set(chave_tem_conta(user.pk), tem, TEM_CONTA_SEGUNDOS)
    if not tem:
        return {'email_menu': {'conectado': False}}
    from .views import chave_nao_lidas
    n = cache.get(chave_nao_lidas(user.pk))
    return {'email_menu': {'conectado': True, 'nao_lidas': n, 'atualizar': n is None}}
