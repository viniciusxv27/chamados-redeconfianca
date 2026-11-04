from pathlib import Path

from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from cameras.models import ConfigCameras


class Command(BaseCommand):
    help = 'Publica o AgenteCameras.exe no armazenamento, para o botão de /cameras/configuracao/ entregar.'

    def add_arguments(self, parser):
        parser.add_argument('caminho')

    def handle(self, *args, **opcoes):
        caminho = Path(opcoes['caminho'])
        if not caminho.is_file() or caminho.read_bytes()[:2] != b'MZ':
            raise CommandError(f'{caminho} não é um .exe do Windows.')
        config = ConfigCameras.atual()
        anterior = config.instalador_arquivo
        with open(caminho, 'rb') as arquivo:
            nome = default_storage.save(f'cameras/instalador/AgenteCameras-{timezone.now():%Y%m%d%H%M%S}.exe', File(arquivo))
        ConfigCameras.objects.filter(pk=config.pk).update(instalador_arquivo=nome, instalador_em=timezone.now())
        if anterior and anterior != nome:
            default_storage.delete(anterior)
        self.stdout.write(f'Publicado: {nome} ({caminho.stat().st_size / 1024 / 1024:.0f} MB)')
