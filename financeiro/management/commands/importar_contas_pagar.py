from django.core.management.base import BaseCommand, CommandError

from financeiro.importacao_titulos import ArquivoInvalido, analisar, decodificar, importar


class Command(BaseCommand):
    help = 'Importa contas a pagar do CSV do relatório. Sem --confirmar, só mostra o que entraria.'

    def add_arguments(self, parser):
        parser.add_argument('arquivo')
        parser.add_argument('--confirmar', action='store_true')

    def handle(self, arquivo, confirmar, **opcoes):
        with open(arquivo, 'rb') as f:
            texto = decodificar(f.read())
        try:
            a = analisar(texto)
        except ArquivoInvalido as exc:
            raise CommandError(str(exc))
        self.stdout.write(f'Novas: {len(a.novos)} (R$ {a.total}) · já existem: {a.ja_existem} · repetidas: {len(a.repetidas)} · '
                          f'já pagas: {len(a.pagas)} · recusadas: {len(a.rejeitadas)} · contas novas no plano: {len(a.planos_novos)}')
        self.stdout.write(f'Filial sem loja no portal: {dict(a.sem_loja)}')
        for r in a.repetidas:
            self.stdout.write(f'  repetida: linha {r[0]} · {r[1]} · {r[2]:%d/%m/%Y} · R$ {r[3]}')
        for r in a.rejeitadas:
            self.stdout.write(f'  recusada: linha {r[0]} · {r[1]}')
        if confirmar:
            self.stdout.write(self.style.SUCCESS(f'Importadas: {importar(a, nome_arquivo=arquivo.rsplit("/", 1)[-1])}'))
        else:
            self.stdout.write('Nada gravado (use --confirmar).')
