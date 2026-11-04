"""Gera o AgenteCameras.exe: um arquivo só, com Python, bibliotecas e modelo, sem depender de nada.

Roda em qualquer sistema com makensis (no Mac: brew install nsis), porque só baixa as peças prontas
do Windows, confere e empacota.

    python agente_cameras/construir_instalador.py [--portal https://...] [--saida dist/AgenteCameras.exe]

Depois publique para o botão do portal: python manage.py publicar_instalador_cameras dist/AgenteCameras.exe
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PYTHON_URL = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip'
PYTHON_SHA256 = '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'
MODELO_URL = 'https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx'
MODELO_SHA256 = '427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7'
PORTAL_PADRAO = 'https://chamados.redeconfianca.com.br'
ARQUIVOS = ('agente.py', 'ao_vivo.py', 'descoberta.py', 'detector.py', 'regras.py', 'instalar.py')
# python.exe e python312.zip ficam em runtime; ".." é a pasta do agente. Com este arquivo o Python
# não acrescenta a pasta do script sozinho: sem o "..", "from descoberta import" falharia no Windows.
PTH = 'python312.zip\n.\nLib\\site-packages\n..\nimport site\n'


def baixar(url, destino, sha256):
    if not destino.exists():
        print(f'Baixando {url}')
        with urllib.request.urlopen(url) as resposta, open(destino, 'wb') as arquivo:
            shutil.copyfileobj(resposta, arquivo)
    achado = hashlib.sha256(destino.read_bytes()).hexdigest()
    if achado != sha256:
        destino.unlink()
        sys.exit(f'{destino.name}: o arquivo baixado não confere ({achado}).')


def rodas_do_windows(destino):
    subprocess.run([sys.executable, '-m', 'pip', 'download', '--quiet', '--dest', str(destino), '--platform', 'win_amd64',
                    '--python-version', '3.12', '--implementation', 'cp', '--only-binary=:all:',
                    '-r', str(AQUI / 'requirements.txt')], check=True)
    # msvcp140 e companhia: o Windows limpo não tem, e sem elas o OpenCV e o ONNX não abrem.
    subprocess.run([sys.executable, '-m', 'pip', 'download', '--quiet', '--no-deps', '--dest', str(destino), '--platform', 'win_amd64',
                    '--python-version', '3.12', '--implementation', 'cp', '--only-binary=:all:', 'msvc-runtime'], check=True)


def montar(pasta, cache, portal):
    runtime = pasta / 'runtime'
    pacotes = runtime / 'Lib' / 'site-packages'
    pacotes.mkdir(parents=True)
    baixar(PYTHON_URL, cache / 'python-embed.zip', PYTHON_SHA256)
    with zipfile.ZipFile(cache / 'python-embed.zip') as z:
        z.extractall(runtime)
    (runtime / 'python312._pth').write_text(PTH, encoding='ascii')
    rodas = cache / 'rodas'
    rodas.mkdir(exist_ok=True)
    rodas_do_windows(rodas)
    for roda in sorted(rodas.glob('*.whl')):
        with zipfile.ZipFile(roda) as z:
            if roda.name.startswith('msvc_runtime'):
                # Ao lado do python.exe, onde o Windows procura primeiro.
                for nome in z.namelist():
                    if nome.lower().endswith('.dll'):
                        (runtime / Path(nome).name).write_bytes(z.read(nome))
            else:
                z.extractall(pacotes)
    baixar(MODELO_URL, cache / 'yolox_tiny.onnx', MODELO_SHA256)
    shutil.copy(cache / 'yolox_tiny.onnx', pasta / 'yolox_tiny.onnx')
    for nome in ARQUIVOS:
        shutil.copy(AQUI / nome, pasta / nome)
    (pasta / 'portal.txt').write_text(portal + '\n', encoding='utf-8')


def conferir(pasta):
    """O que dá para provar sem ter um Windows aqui: as peças estão onde o Python vai procurar."""
    runtime = pasta / 'runtime'
    pacotes = runtime / 'Lib' / 'site-packages'
    obrigatorios = [runtime / n for n in ('python.exe', 'python312.dll', 'python312.zip', 'python312._pth', 'vcruntime140.dll', 'msvcp140.dll')]
    obrigatorios += [pacotes / 'requests' / '__init__.py', pacotes / 'cv2' / '__init__.py', pacotes / 'numpy' / '__init__.py',
                     pacotes / 'onnxruntime' / '__init__.py', pacotes / 'certifi' / 'cacert.pem', pacotes / 'aiortc' / '__init__.py',
                     pacotes / 'av' / '__init__.py', pasta / 'yolox_tiny.onnx', pasta / 'agente.py', pasta / 'ao_vivo.py']
    faltando = [str(c.relative_to(pasta)) for c in obrigatorios if not c.exists()]
    if faltando:
        sys.exit(f'Faltam peças no pacote: {faltando}')
    for pyd in list((pacotes / 'cv2').glob('*.pyd')) + list((pacotes / 'numpy').rglob('_multiarray_umath*.pyd')):
        if pyd.read_bytes()[:2] != b'MZ':
            sys.exit(f'{pyd.name} não é um binário do Windows.')
    if not list((pacotes / 'cv2').glob('*.pyd')):
        sys.exit('O OpenCV do pacote não é do Windows.')


def main():
    argumentos = argparse.ArgumentParser()
    argumentos.add_argument('--portal', default=PORTAL_PADRAO)
    argumentos.add_argument('--saida', default=str(AQUI / 'dist' / 'AgenteCameras.exe'))
    argumentos.add_argument('--cache', default=str(AQUI / 'dist' / 'cache'))
    a = argumentos.parse_args()
    saida, cache = Path(a.saida).resolve(), Path(a.cache).resolve()
    saida.parent.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporaria:
        pacote = Path(temporaria) / 'pacote'
        pacote.mkdir()
        montar(pacote, cache, a.portal)
        conferir(pacote)
        subprocess.run(['makensis', '-V2', '-INPUTCHARSET', 'UTF8', f'-DSAIDA={saida}', f'-DPACOTE={pacote}', str(AQUI / 'instalador.nsi')], check=True)
    print(f'{saida} ({saida.stat().st_size / 1024 / 1024:.0f} MB)')


if __name__ == '__main__':
    main()
