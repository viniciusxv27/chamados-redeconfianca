"""Diz, em uma tela, se cada fonte do mapa está mesmo servindo mapa.

Existe porque a falha deste módulo é silenciosa: o provedor responde HTTP 200 e
uma **imagem escrita** ("Access blocked", "API KEY REQUIRED") no lugar do mapa.
Para o navegador, carregou. Para quem olha, o mapa está errado e ninguém sabe
por quê.

O truque da conferência é simples: pedir dois quadradinhos de lugares bem
distantes. Mapa de verdade devolve imagens diferentes; aviso devolve a mesma
imagem sempre.

    venv/bin/python manage.py conferir_mapa
"""
from django.core.management.base import BaseCommand

from maps import tiles

# Um pedaço de Minas e um do Pará: nada em comum entre os dois quadradinhos.
AQUI = (12, 1498, 2184)
LA = (12, 1320, 1900)

# As que já foram descartadas, para a conferência mostrar o porquê — e para se
# algum dia voltarem a servir.
DESCARTADAS = (
    ('OpenStreetMap', 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', 'image/png'),
    ('Carto Voyager',
     'https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png', 'image/png'),
)


class Command(BaseCommand):
    help = 'Confere se as fontes do mapa estão servindo mapa de verdade.'

    def add_arguments(self, parser):
        parser.add_argument('--todas', action='store_true',
                            help='Confere também as fontes descartadas.')

    def handle(self, *args, **opcoes):
        import requests

        em_uso = [(f'Em uso {i + 1}', modelo, tipo)
                  for i, (modelo, tipo) in enumerate(tiles.FONTES)]
        lista = em_uso + (list(DESCARTADAS) if opcoes['todas'] else [])

        for nome, modelo, tipo_padrao in lista:
            linhas = []
            imagens = []
            for z, x, y in (AQUI, LA):
                url = modelo.format(z=z, x=x, y=y)
                try:
                    r = requests.get(url, timeout=tiles.TEMPO_LIMITE,
                                     headers={'User-Agent': tiles.AGENTE})
                    tipo = (r.headers.get('Content-Type') or tipo_padrao).split(';')[0]
                    linhas.append(f'HTTP {r.status_code} · {tipo} · {len(r.content)} bytes')
                    imagens.append(r.content if r.status_code == 200 else None)
                except Exception as exc:                      # noqa: BLE001
                    linhas.append(f'falhou: {exc}')
                    imagens.append(None)

            self.stdout.write(self.style.MIGRATE_HEADING(f'\n{nome}'))
            self.stdout.write(f'  {modelo}')
            for linha in linhas:
                self.stdout.write(f'  {linha}')

            if not all(imagens):
                self.stdout.write(self.style.ERROR('  → não respondeu'))
            elif imagens[0] == imagens[1]:
                self.stdout.write(self.style.ERROR(
                    '  → MESMA imagem para lugares diferentes: isto é um aviso, não um mapa'))
            elif any(len(i) < tiles.MINIMO_DE_IMAGEM for i in imagens):
                self.stdout.write(self.style.WARNING(
                    '  → imagem pequena demais para ser mapa'))
            else:
                self.stdout.write(self.style.SUCCESS('  → servindo mapa'))
