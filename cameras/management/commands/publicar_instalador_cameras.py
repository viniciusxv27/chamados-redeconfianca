import hashlib
import re
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from cameras.models import ConfigCameras


def versao_do_codigo():
    """A versão escrita em agente_cameras/agente.py (é a que vai dentro do .exe gerado a partir dele)."""
    caminho = Path(settings.BASE_DIR) / 'agente_cameras' / 'agente.py'
    achado = re.search(r"^VERSAO = '([0-9.]+)'", caminho.read_text(encoding='utf-8'), re.M) if caminho.exists() else None
    return achado.group(1) if achado else ''


def impressao_digital(caminho):
    resumo = hashlib.sha256()
    with open(caminho, 'rb') as arquivo:
        for pedaco in iter(lambda: arquivo.read(1024 * 1024), b''):
            resumo.update(pedaco)
    return resumo.hexdigest()


class Command(BaseCommand):
    help = ('Publica o AgenteCameras.exe no armazenamento, para o botão de /cameras/configuracao/ entregar e para a '
            'atualização automática das lojas (versão + SHA-256, que o agente confere antes de instalar).')

    def add_arguments(self, parser):
        parser.add_argument('caminho')
        parser.add_argument('--versao', default='', help='Versão do agente dentro do .exe (padrão: a de agente_cameras/agente.py).')

    def handle(self, *args, **opcoes):
        caminho = Path(opcoes['caminho'])
        if not caminho.is_file() or caminho.read_bytes()[:2] != b'MZ':
            raise CommandError(f'{caminho} não é um .exe do Windows.')
        versao = opcoes['versao'] or versao_do_codigo()
        if not re.fullmatch(r'\d+(\.\d+){1,2}', versao or ''):
            raise CommandError('Não achei a versão do agente: passe --versao 3.1')
        config = ConfigCameras.atual()
        anterior = config.instalador_arquivo
        sha256, tamanho = impressao_digital(caminho), caminho.stat().st_size
        with open(caminho, 'rb') as arquivo:
            nome = default_storage.save(f'cameras/instalador/AgenteCameras-{versao}-{timezone.now():%Y%m%d%H%M%S}.exe', File(arquivo))
        ConfigCameras.objects.filter(pk=config.pk).update(instalador_arquivo=nome, instalador_em=timezone.now(), instalador_versao=versao,
                                                          instalador_sha256=sha256, instalador_tamanho=tamanho)
        if anterior and anterior != nome:
            default_storage.delete(anterior)
        self.stdout.write(f'Publicado: {nome} · versão {versao} · {tamanho / 1024 / 1024:.0f} MB · sha256 {sha256}')
