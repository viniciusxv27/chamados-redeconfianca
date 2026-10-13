"""A caixa de e-mail institucional de cada pessoa, atrelada ao usuário do portal."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class ContaEmail(models.Model):
    """Credencial da caixa no servidor de e-mail (poste.io).

    A senha fica cifrada (webmail/seguranca.py) e nunca volta para a tela.
    ``credencial_em`` muda a cada senha nova: é ela que invalida a conexão IMAP
    aberta com a senha antiga.
    """

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='conta_email',
        verbose_name='Usuário do portal')
    email = models.EmailField(verbose_name='E-mail institucional', db_index=True)
    senha_cifrada = models.TextField(verbose_name='Senha (cifrada)')
    nome_exibicao = models.CharField(max_length=150, blank=True, default='',
                                     verbose_name='Nome que aparece para quem recebe')
    assinatura = models.TextField(blank=True, default='', verbose_name='Assinatura (HTML)')
    credencial_em = models.DateTimeField(default=timezone.now, verbose_name='Senha gravada em')
    senha_recusada = models.BooleanField(
        default=False, verbose_name='Servidor recusou a senha',
        help_text='Marcado quando a senha foi trocada no servidor: a pessoa entra de novo.')
    ultimo_acesso = models.DateTimeField(null=True, blank=True, verbose_name='Último acesso')
    ultimo_erro = models.CharField(max_length=300, blank=True, default='', verbose_name='Último erro')
    criada_em = models.DateTimeField(auto_now_add=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Conta de e-mail'
        verbose_name_plural = 'Contas de e-mail'
        ordering = ['email']

    def __str__(self):
        return f'{self.email} ({self.usuario})'

    @property
    def remetente(self):
        nome = self.nome_exibicao or self.usuario.get_full_name() or ''
        return nome.strip()

    def definir_senha(self, senha):
        from .seguranca import cifrar
        self.senha_cifrada = cifrar(senha)
        self.credencial_em = timezone.now()
        self.senha_recusada = False
        self.ultimo_erro = ''

    def senha(self):
        from .seguranca import decifrar
        return decifrar(self.senha_cifrada)
