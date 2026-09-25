"""Leva para o MinIO os áudios de feedback que ficaram no disco.

Antes da correção do storage, o áudio do /feedback era gravado no disco do
container (a pasta de onde o gunicorn subiu), enquanto o banco guardava o
caminho e o portal procurava o arquivo no MinIO. Este comando procura cada
áudio que falta no bucket, acha o arquivo no disco e manda para lá com o mesmo
caminho — o que já estiver no MinIO ele nem toca.

    python manage.py audios_para_o_s3            # só relatório
    python manage.py audios_para_o_s3 --aplicar  # sobe o que achar

Rodar duas vezes não faz mal: o que já subiu aparece como "no MinIO".
"""
import os

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand

from feedback.models import Feedback


class Command(BaseCommand):
    help = 'Sobe para o MinIO os áudios de feedback que ficaram no disco do servidor.'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true',
                            help='Sobe de verdade (sem isso, só mostra o que faria).')

    def _candidatos(self, nome):
        """Onde o arquivo pode ter ficado, na ordem em que faz sentido procurar."""
        lugares = [
            os.path.join(str(getattr(settings, 'MEDIA_ROOT', '') or ''), nome),
            os.path.join(str(settings.BASE_DIR), nome),
            os.path.join(os.getcwd(), nome),
            nome,
        ]
        vistos = []
        for caminho in lugares:
            if caminho and caminho not in vistos:
                vistos.append(caminho)
        return vistos

    def handle(self, *args, **opcoes):
        aplicar = opcoes['aplicar']
        no_minio = subidos = perdidos = 0

        qs = (Feedback.objects.exclude(audio_file='').exclude(audio_file=None)
              .order_by('id'))
        for fb in qs:
            nome = fb.audio_file.name
            storage = fb.audio_file.storage
            try:
                if storage.exists(nome):
                    no_minio += 1
                    continue
            except Exception as exc:                              # noqa: BLE001
                self.stderr.write(f'#{fb.id} {nome}: não deu para conferir no MinIO ({exc})')
                continue

            origem = next((c for c in self._candidatos(nome) if os.path.isfile(c)), None)
            if not origem:
                perdidos += 1
                self.stdout.write(self.style.WARNING(f'#{fb.id} sem o arquivo em lugar nenhum: {nome}'))
                continue

            tamanho = os.path.getsize(origem)
            if not aplicar:
                subidos += 1
                self.stdout.write(f'#{fb.id} subiria {nome} ({tamanho} bytes) de {origem}')
                continue

            with open(origem, 'rb') as arq:
                gravado = storage.save(nome, File(arq))
            if gravado != nome:
                # file_overwrite=False renomeia se já existir; aqui não existia,
                # mas se acontecer o banco precisa apontar para o nome novo.
                fb.audio_file.name = gravado
                fb.save(update_fields=['audio_file'])
            subidos += 1
            self.stdout.write(self.style.SUCCESS(f'#{fb.id} {gravado} ({tamanho} bytes) → MinIO'))

        resumo = (f'{no_minio} já no MinIO · {subidos} '
                  f'{"subidos" if aplicar else "prontos para subir"} · {perdidos} sem arquivo')
        self.stdout.write(self.style.SUCCESS(resumo) if not perdidos else self.style.WARNING(resumo))
        if perdidos and not aplicar:
            self.stdout.write(
                'Os "sem arquivo" só existem se o container onde foram gravados ainda estiver de pé; '
                'rode este comando lá dentro, na mesma pasta em que o gunicorn roda.')
