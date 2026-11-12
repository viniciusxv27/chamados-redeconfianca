"""Apaga alertas de câmera com fotos, vídeos e quadros no armazenamento.

    python manage.py limpar_alertas_cameras --abertos            # só os que ninguém tratou (NOVO e Em análise)
    python manage.py limpar_alertas_cameras --todos --loja 10
    python manage.py limpar_alertas_cameras --abertos --antes 2026-11-01 --confirmar

Sem --confirmar só mostra quantos seriam apagados. Alerta decidido (furto confirmado ou falso alarme)
ensina a análise: só sai com --todos.
"""
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from cameras.models import AlertaCamera
from cameras.servicos import apagar_alertas


class Command(BaseCommand):
    help = 'Apaga alertas de câmera (com a mídia). Sem --confirmar, só conta.'

    def add_arguments(self, parser):
        grupo = parser.add_mutually_exclusive_group(required=True)
        grupo.add_argument('--abertos', action='store_true', help='Só os não tratados (NOVO e Em análise).')
        grupo.add_argument('--todos', action='store_true', help='Inclusive os decididos (apaga o que a análise aprendeu deles).')
        parser.add_argument('--loja', type=int, help='Id do gravador.')
        parser.add_argument('--antes', help='Só os criados antes desta data (AAAA-MM-DD).')
        parser.add_argument('--confirmar', action='store_true')

    def handle(self, *args, **o):
        alvo = AlertaCamera.objects.all()
        if o['abertos']:
            alvo = alvo.filter(status__in=['NOVO', 'ANALISE'])
        if o['loja']:
            alvo = alvo.filter(gravador_id=o['loja'])
        if o['antes']:
            try:
                alvo = alvo.filter(criado_em__date__lt=datetime.strptime(o['antes'], '%Y-%m-%d').date())
            except ValueError as erro:
                raise CommandError('--antes no formato AAAA-MM-DD.') from erro
        resumo = ', '.join(f"{r['gravador__loja']}: {r['n']}" for r in alvo.values('gravador__loja').annotate(n=Count('id')).order_by('-n'))
        total = alvo.count()
        if not o['confirmar']:
            self.stdout.write(f'Seriam apagados {total} alerta(s){f" ({resumo})" if resumo else ""}. Rode com --confirmar.')
            return
        self.stdout.write(f'Apagados {apagar_alertas(alvo.order_by())} alerta(s){f" ({resumo})" if resumo else ""}.')
