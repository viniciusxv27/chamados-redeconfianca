from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from acess.importacao_legado import importar


class Command(BaseCommand):
    help = ('Importa a exportação PostgreSQL do Confiança Acess anterior (fichas, chamados, histórico e mensagens). '
            'Sem --aplicar só simula e desfaz.')

    def add_arguments(self, parser):
        parser.add_argument('arquivo')
        parser.add_argument('--aplicar', action='store_true', help='Grava de verdade (sem isso, simula).')
        parser.add_argument('--autor', default='', help='E-mail do usuário do portal que fica como autor da importação.')

    def handle(self, *args, **op):
        autor = None
        if op['autor']:
            autor = get_user_model().objects.filter(email__iexact=op['autor']).first()
            if not autor:
                raise CommandError(f'Usuário {op["autor"]} não encontrado.')
        imp = importar(op['arquivo'], autor=autor, aplicar=op['aplicar'])
        self.stdout.write(('GRAVADO' if op['aplicar'] else 'SIMULAÇÃO (nada gravado)') + ':')
        for k, v in sorted(imp.rel.items()):
            self.stdout.write(f'  {k}: {v}')
        if imp.lojas_sem_par:
            self.stdout.write('  lojas sem setor no portal: ' + ', '.join(f'{k} ({v})' for k, v in imp.lojas_sem_par.items()))
        for a in imp.avisos:
            self.stdout.write('  aviso: ' + a)
        self.stdout.write('  operadores do Acess anterior:')
        for o in imp.operadores():
            self.stdout.write(f"    {o['nome']} ({o['papel']}{', trata chamados' if o['trata_chamados'] else ''}) → "
                              f"{o['no_portal'] or 'sem usuário no portal'}{' · já tem acesso' if o['tem_acesso'] else ''}")
