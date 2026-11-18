"""Gera o PonteAutomacaoSAP.exe: Python oficial embarcado + os arquivos da ponte (sem bibliotecas de fora).

Roda em qualquer sistema com makensis (Mac: brew install nsis):
    python ponte_sap/construir_instalador.py [--saida dist/PonteAutomacaoSAP.exe]

Depois, em /sap/automacao/, o SUPERADMIN envia o .exe em "Ponte (instalador)".
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent / 'agente_cameras'))
from construir_instalador import PTH, PYTHON_SHA256, PYTHON_URL, baixar  # noqa: E402  (o mesmo Python das câmeras)

# A versão vem do texto (importar a ponte puxaria a API do Windows, e o construtor roda no Mac).
VERSAO = re.search(r"^VERSAO = '([\d.]+)'", (AQUI / 'ponte.py').read_text(encoding='utf-8'), re.M).group(1)

ARQUIVOS = ('ponte.py', 'janela.py', 'imagem.py', 'instalar.py')


def montar(pasta, cache):
    runtime = pasta / 'runtime'
    runtime.mkdir(parents=True)
    baixar(PYTHON_URL, cache / 'python-embed.zip', PYTHON_SHA256)
    with zipfile.ZipFile(cache / 'python-embed.zip') as z:
        z.extractall(runtime)
    (runtime / 'python312._pth').write_text(PTH, encoding='ascii')
    for nome in ARQUIVOS:
        shutil.copy(AQUI / nome, pasta / nome)


def conferir(pasta):
    obrigatorios = [pasta / 'runtime' / n for n in ('python.exe', 'pythonw.exe', 'python312.dll', 'python312.zip')]
    obrigatorios += [pasta / n for n in ARQUIVOS]
    faltando = [str(c.relative_to(pasta)) for c in obrigatorios if not c.exists()]
    if faltando:
        sys.exit(f'Faltam peças no pacote: {faltando}')


def main():
    a = argparse.ArgumentParser()
    a.add_argument('--saida', default=str(AQUI / 'dist' / 'PonteAutomacaoSAP.exe'))
    a.add_argument('--cache', default=str(AQUI.parent / 'agente_cameras' / 'dist' / 'cache'))
    args = a.parse_args()
    saida, cache = Path(args.saida).resolve(), Path(args.cache).resolve()
    saida.parent.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporaria:
        pacote = Path(temporaria) / 'pacote'
        pacote.mkdir()
        montar(pacote, cache)
        conferir(pacote)
        subprocess.run(['makensis', '-V2', '-INPUTCHARSET', 'UTF8', f'-DSAIDA={saida}', f'-DPACOTE={pacote}',
                        f'-DVERSAO={VERSAO}', str(AQUI / 'instalador.nsi')], check=True)
    print(f'{saida} ({saida.stat().st_size / 1024 / 1024:.1f} MB) — versão {VERSAO}')


if __name__ == '__main__':
    main()
