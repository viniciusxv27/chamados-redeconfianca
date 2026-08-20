"""Gera a análise da IA dos feedbacks que ainda não têm uma.

Serve para o acervo: feedbacks antigos, ou os que falharam quando a API estava
fora. Pode rodar quantas vezes quiser — só toca no que está sem análise.

    manage.py gerar_analises_feedback
    manage.py gerar_analises_feedback --refazer      # refaz todos
    manage.py gerar_analises_feedback --limite 20
"""
from django.core.management.base import BaseCommand

from impulso.ai import generate_feedback_summary
from impulso.models import ImpulsoFeedback


class Command(BaseCommand):
    help = 'Gera a análise e a nota da IA dos feedbacks do Impulso.'

    def add_arguments(self, parser):
        parser.add_argument('--refazer', action='store_true',
                            help='Refaz mesmo os que já têm análise.')
        parser.add_argument('--limite', type=int, default=0,
                            help='Processa no máximo N feedbacks.')

    def handle(self, *args, **opcoes):
        alvo = ImpulsoFeedback.objects.all()
        if not opcoes['refazer']:
            alvo = alvo.filter(ai_summary='')
        alvo = alvo.select_related('colaborador', 'gestor').order_by('-referencia_mes')
        if opcoes['limite']:
            alvo = alvo[:opcoes['limite']]

        total = alvo.count() if hasattr(alvo, 'count') else len(alvo)
        if not total:
            self.stdout.write(self.style.SUCCESS('Nada a gerar: todos já têm análise.'))
            return

        self.stdout.write(f'Gerando análise de {total} feedback(s)…')
        feitos = falhos = 0
        for fb in alvo:
            texto = generate_feedback_summary(fb, force=opcoes['refazer'])
            fb.refresh_from_db()
            if texto:
                feitos += 1
                self.stdout.write(self.style.SUCCESS(
                    f'  ok   #{fb.id} {fb.colaborador} {fb.referencia_mes:%m/%Y} '
                    f'— nota {fb.nota_ia if fb.nota_ia is not None else "—"}'))
            else:
                falhos += 1
                self.stdout.write(self.style.ERROR(
                    f'  falha #{fb.id} {fb.colaborador}: {fb.ai_summary_error[:110]}'))

        estilo = self.style.SUCCESS if not falhos else self.style.WARNING
        self.stdout.write(estilo(f'\n{feitos} gerada(s), {falhos} falha(s).'))
