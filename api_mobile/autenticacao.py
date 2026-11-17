"""Autenticação do app: `Authorization: Bearer <token>`.

Sem cookie, sem CSRF (o token não vai sozinho como o cookie vai). Sessão parada
há mais de DIAS_SEM_USO dias expira; usuário desativado perde o acesso na hora.
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework import authentication, exceptions

from .models import TokenMobile, hash_do_token

DIAS_SEM_USO = 60
# Gravar o último uso a cada requisição seria uma escrita por tela; de 5 em 5 min basta.
INTERVALO_ULTIMO_USO = timedelta(minutes=5)


class TokenMobileAuthentication(authentication.BaseAuthentication):
    keyword = 'Bearer'

    def authenticate(self, request):
        cabecalho = authentication.get_authorization_header(request).split()
        if not cabecalho or cabecalho[0].lower() != self.keyword.lower().encode():
            return None
        if len(cabecalho) != 2:
            raise exceptions.AuthenticationFailed('Cabeçalho de autorização inválido.')
        try:
            token = cabecalho[1].decode()
        except UnicodeError:
            raise exceptions.AuthenticationFailed('Token inválido.')

        registro = (TokenMobile.objects.select_related('user')
                    .filter(chave_hash=hash_do_token(token), revogado_em__isnull=True).first())
        if registro is None:
            raise exceptions.AuthenticationFailed('Sessão encerrada. Entre de novo.')
        agora = timezone.now()
        if registro.ultimo_uso and agora - registro.ultimo_uso > timedelta(days=DIAS_SEM_USO):
            registro.revogar()
            raise exceptions.AuthenticationFailed('Sessão expirada. Entre de novo.')
        if not registro.user.is_active:
            raise exceptions.AuthenticationFailed('Usuário desativado.')
        if registro.ultimo_uso is None or agora - registro.ultimo_uso > INTERVALO_ULTIMO_USO:
            TokenMobile.objects.filter(pk=registro.pk).update(ultimo_uso=agora)
        request.token_mobile = registro
        return registro.user, registro

    def authenticate_header(self, request):
        return self.keyword
