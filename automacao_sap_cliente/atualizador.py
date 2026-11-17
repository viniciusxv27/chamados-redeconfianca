"""Atualização automática da Automação SAP pelo portal (copiar para dentro do código do app).

Uso, logo no começo do app (antes de abrir a janela):

    from atualizador import atualizar_se_preciso
    atualizar_se_preciso(VERSAO, perguntar=lambda info: messagebox.askyesno(
        'Automação SAP', f"Versão {info['versao']} disponível. Atualizar agora?\\n\\n{info.get('notas') or ''}"))

O que faz:
1. Pergunta ao portal (GET /sap/automacao/api/versao/?atual=<VERSAO>, cabeçalho X-Chave-Automacao).
2. Se houver versão maior publicada: baixa o .zip, confere tamanho e sha256 e extrai numa pasta temporária.
3. Grava um .bat que espera este processo fechar, troca a pasta _internal e o .exe (com cópia de
   segurança e volta atrás se a cópia falhar), acrescenta arquivos NOVOS de config sem mexer nos que
   existem, NUNCA toca em logs, e abre o app de novo.
4. Fecha o app (os._exit) para o .bat poder trocar os arquivos.

Qualquer falha (sem internet, portal fora, chave errada, arquivo corrompido) só registra em
logs/erros.txt e deixa o app abrir na versão que está: atualizar nunca impede de trabalhar.
Só roda no .exe empacotado (PyInstaller) no Windows; rodando o .py em desenvolvimento, não faz nada.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

PORTAL = 'https://chamados.redeconfianca.com.br'
# A chave aparece em /sap/automacao/ para o SUPERADMIN. Pode ficar aqui ou em config/config.json ("chave_portal").
CHAVE = ''
TEMPO_LIMITE = 8          # segundos para perguntar a versão (não pode segurar a abertura do app)
TEMPO_DOWNLOAD = 300
NOME_EXE = 'AutomacaoSAP.exe'


def pasta_do_app():
    return os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))


def _registrar_erro(pasta, texto):
    try:
        os.makedirs(os.path.join(pasta, 'logs'), exist_ok=True)
        with open(os.path.join(pasta, 'logs', 'erros.txt'), 'a', encoding='utf-8') as f:
            f.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} [atualizacao] {texto}\n')
    except OSError:
        pass


def _config(pasta):
    try:
        with open(os.path.join(pasta, 'config', 'config.json'), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def versao_em_tupla(texto):
    try:
        return tuple(int(p) for p in str(texto or '').strip().lstrip('vV').split('.'))
    except ValueError:
        return ()


def consultar(versao_atual, portal=PORTAL, chave=CHAVE, abrir=urllib.request.urlopen):
    """Devolve o JSON do portal ({versao, atualizar, url, sha256, tamanho, notas}) ou levanta erro."""
    url = f'{portal.rstrip("/")}/sap/automacao/api/versao/?' + urllib.parse.urlencode({'atual': versao_atual})
    pedido = urllib.request.Request(url, headers={'X-Chave-Automacao': chave, 'User-Agent': 'AutomacaoSAP'})
    with abrir(pedido, timeout=TEMPO_LIMITE) as resp:
        return json.loads(resp.read().decode('utf-8'))


def baixar(info, destino, chave=CHAVE, abrir=urllib.request.urlopen):
    """Baixa o .zip para `destino`, conferindo tamanho e sha256."""
    if not str(info.get('url', '')).startswith(('https://', 'http://localhost', 'http://127.0.0.1')):
        raise ValueError('endereço da atualização sem https')
    pedido = urllib.request.Request(info['url'], headers={'X-Chave-Automacao': chave, 'User-Agent': 'AutomacaoSAP'})
    resumo, total = hashlib.sha256(), 0
    with abrir(pedido, timeout=TEMPO_DOWNLOAD) as resp, open(destino, 'wb') as saida:
        for bloco in iter(lambda: resp.read(1024 * 1024), b''):
            resumo.update(bloco)
            total += len(bloco)
            saida.write(bloco)
    if info.get('tamanho') and total != int(info['tamanho']):
        raise ValueError(f'download incompleto ({total} de {info["tamanho"]} bytes)')
    if resumo.hexdigest() != info.get('sha256'):
        raise ValueError('o arquivo baixado não confere (sha256 diferente)')
    return destino


def extrair(zip_path, destino):
    """Extrai o zip aceitando nomes com barra invertida e recusando caminho para fora. Devolve a pasta que tem o .exe."""
    destino = os.path.abspath(destino)
    with zipfile.ZipFile(zip_path) as zf:
        for item in zf.infolist():
            partes = [p for p in item.filename.replace('\\', '/').split('/') if p not in ('', '.')]
            if not partes or '..' in partes or ':' in partes[0]:
                continue
            alvo = os.path.abspath(os.path.join(destino, *partes))
            if not alvo.startswith(destino + os.sep):
                continue
            if item.filename.endswith(('/', '\\')):
                os.makedirs(alvo, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(alvo), exist_ok=True)
            with zf.open(item) as origem, open(alvo, 'wb') as saida:
                shutil.copyfileobj(origem, saida)
    for raiz, _pastas, arquivos in os.walk(destino):
        if any(a.lower() == NOME_EXE.lower() for a in arquivos):
            return raiz
    raise ValueError('o pacote não tem o AutomacaoSAP.exe')


def script_de_troca(novo, app, pid, backup):
    """O .bat que roda com o app fechado. robocopy: código < 8 é sucesso."""
    return f'''@echo off
chcp 65001 >nul
set "NOVO={novo}"
set "APP={app}"
set "BKP={backup}"
:espera
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul
if not errorlevel 1 (ping -n 2 127.0.0.1 >nul & goto espera)
robocopy "%APP%\\_internal" "%BKP%\\_internal" /MIR /R:2 /W:1 >nul
copy /Y "%APP%\\{NOME_EXE}" "%BKP%\\{NOME_EXE}" >nul
robocopy "%NOVO%\\_internal" "%APP%\\_internal" /MIR /R:3 /W:2 >nul
if errorlevel 8 goto volta
robocopy "%NOVO%" "%APP%" /XD "%NOVO%\\_internal" "%NOVO%\\config" "%NOVO%\\logs" /R:3 /W:2 >nul
if errorlevel 8 goto volta
if exist "%NOVO%\\config" robocopy "%NOVO%\\config" "%APP%\\config" /E /XC /XN /XO /R:2 /W:1 >nul
goto abre
:volta
echo %date% %time% [atualizacao] falhou ao copiar; voltando a versao anterior>> "%APP%\\logs\\erros.txt"
robocopy "%BKP%\\_internal" "%APP%\\_internal" /MIR /R:3 /W:2 >nul
copy /Y "%BKP%\\{NOME_EXE}" "%APP%\\{NOME_EXE}" >nul
:abre
start "" "%APP%\\{NOME_EXE}"
rmdir /S /Q "%NOVO%" >nul 2>&1
'''


def atualizar_se_preciso(versao_atual, perguntar=None, portal=None, chave=None):
    """Chame ao abrir o app. Volta normalmente se não há o que atualizar ou se algo falhar."""
    if not getattr(sys, 'frozen', False) or os.name != 'nt':
        return False
    app = pasta_do_app()
    cfg = _config(app)
    portal = portal or cfg.get('portal') or PORTAL
    chave = chave or cfg.get('chave_portal') or CHAVE
    try:
        info = consultar(versao_atual, portal, chave)
        if not info.get('atualizar') or versao_em_tupla(info.get('versao')) <= versao_em_tupla(versao_atual):
            return False
        if perguntar is not None and not perguntar(info):
            return False
        temp = tempfile.mkdtemp(prefix='AutomacaoSAP_atualizacao_')
        zip_path = baixar(info, os.path.join(temp, 'pacote.zip'), chave)
        novo = extrair(zip_path, os.path.join(temp, 'novo'))
        os.remove(zip_path)
        bat = os.path.join(temp, 'trocar.bat')
        with open(bat, 'w', encoding='utf-8') as f:
            f.write(script_de_troca(novo, app, os.getpid(), os.path.join(app, '_versao_anterior')))
        subprocess.Popen(['cmd', '/c', bat], cwd=temp, close_fds=True,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    except Exception as erro:  # noqa: BLE001 — atualizar nunca impede de abrir o app
        _registrar_erro(app, f'{type(erro).__name__}: {erro}')
        return False
    os._exit(0)
