"""Esvazia o carrinho de contestação de todo mundo (o rascunho guardado no servidor).

    python manage.py limpar_carrinhos_contestacao            # só mostra quantos
    python manage.py limpar_carrinhos_contestacao --apagar   # apaga

O navegador também guarda o carrinho (localStorage + IndexedDB) e o mandaria de
volta ao abrir a tela: por isso exclusion_list.html tem CARRINHO_LIMPO_EM — ao
mudar essa data, cada navegador apaga o carrinho local na próxima abertura.
Rodar este comando depois que a versão nova estiver no ar pega quem abriu a
tela antes dela.

Os ARQUIVOS das evidências ficam: a contestação enviada aponta para o mesmo
arquivo do rascunho, e apagá-lo tiraria a evidência de contestação já enviada.
"""
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction

from contestacao.models import ContestationCartDraft


class Command(BaseCommand):
    help = 'Esvazia o carrinho de contestação de todos os usuários (rascunhos do servidor).'

    def add_arguments(self, parser):
        parser.add_argument('--apagar', action='store_true', help='Apaga de verdade (sem isto, só conta).')

    def handle(self, *args, **opts):
        qs = ContestationCartDraft.objects.all()
        por_pessoa = Counter(qs.values_list('user__email', flat=True))
        self.stdout.write(f'{sum(por_pessoa.values())} item(ns) no carrinho de {len(por_pessoa)} pessoa(s).')
        if not opts['apagar']:
            self.stdout.write('Nada apagado (use --apagar).')
            return
        with transaction.atomic():
            apagados, _ = qs.delete()
        self.stdout.write(self.style.SUCCESS(f'{apagados} item(ns) apagado(s).'))
