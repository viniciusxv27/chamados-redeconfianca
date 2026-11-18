"""Última etapa do instalador da Ponte (já com o Python do pacote, na conta da pessoa, sem administrador).

- abre a ponte junto com o Windows (HKCU\\...\\Run), sem janela (pythonw);
- registra o endereço ponte-sap:// — é com ele que a página do portal acorda a ponte;
- sobe a ponte agora e confere que ela respondeu.

    instalar.py            instala
    instalar.py --remover  tira o início automático e o endereço (o desinstalador chama)
Sai com 0 se deu certo; o texto para a pessoa fica em resultado.txt.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import winreg

PASTA = os.path.dirname(os.path.abspath(__file__))
PYTHONW = os.path.join(PASTA, 'runtime', 'pythonw.exe')
PONTE = os.path.join(PASTA, 'ponte.py')
CHAVE_RUN = r'Software\Microsoft\Windows\CurrentVersion\Run'
NOME = 'PonteAutomacaoSAP'
PROTOCOLO = r'Software\Classes\ponte-sap'
PORTA = 47861


def comando():
    return f'"{PYTHONW}" "{PONTE}"'


def registrar():
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN) as k:
        winreg.SetValueEx(k, NOME, 0, winreg.REG_SZ, comando())
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTOCOLO) as k:
        winreg.SetValueEx(k, '', 0, winreg.REG_SZ, 'URL:Ponte da Automação SAP')
        winreg.SetValueEx(k, 'URL Protocol', 0, winreg.REG_SZ, '')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTOCOLO + r'\shell\open\command') as k:
        # O endereço não passa nada para a ponte: só a acorda (se já estiver rodando, a porta ocupada faz a nova sair).
        winreg.SetValueEx(k, '', 0, winreg.REG_SZ, comando())


def remover():
    for caminho, valor in ((CHAVE_RUN, NOME),):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, caminho, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, valor)
        except OSError:
            pass
    for sub in (r'\shell\open\command', r'\shell\open', r'\shell', ''):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, PROTOCOLO + sub)
        except OSError:
            pass


def respondeu(segundos=20):
    fim = time.time() + segundos
    while time.time() < fim:
        try:
            pedido = urllib.request.Request(f'http://127.0.0.1:{PORTA}/estado', headers={'Origin': 'https://chamados.redeconfianca.com.br'})
            with urllib.request.urlopen(pedido, timeout=2) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError:
            return {'ponte': '', 'app': {}}        # respondeu (portal com outro endereço): está de pé
        except Exception:  # noqa: BLE001 — ainda subindo
            time.sleep(.5)
    return None


def escrever(texto):
    with open(os.path.join(PASTA, 'resultado.txt'), 'w', encoding='utf-16-le') as f:
        f.write('﻿' + texto)


def main():
    if '--remover' in sys.argv:
        remover()
        return 0
    try:
        registrar()
        subprocess.Popen([PYTHONW, PONTE], cwd=PASTA, creationflags=0x08000000)
        estado = respondeu()
        if not estado:
            escrever('A ponte foi instalada, mas não respondeu. Reinicie o computador e abra de novo a Automação SAP no portal.')
            return 1
        app = estado.get('app') or {}
        extra = ('O AutomacaoSAP deste computador foi encontrado.' if app.get('instalado')
                 else 'Abra a Automação SAP no portal: ela instala o app neste computador.')
        escrever(f'Pronto! A Ponte da Automação SAP {estado.get("ponte")} está rodando e abre sozinha com o Windows.\n\n{extra}\n\n'
                 'Volte ao portal (Visão SAP > Automação SAP) e clique em "Abrir aqui".')
        return 0
    except OSError as erro:
        escrever(f'Não deu para terminar a instalação: {erro}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
