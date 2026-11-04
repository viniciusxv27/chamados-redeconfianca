"""Última etapa do instalador do Windows, já com o Python do pacote e como administrador.

Troca o código do nome do arquivo pelo token da loja, confere as peças, cria a tarefa que roda ao
ligar o computador e confirma que o agente subiu. Cada passo também vai para o portal, na linha da
loja: quem instala nem sempre é quem acompanha. Sai com 0 se deu certo; o texto para a pessoa fica em
resultado.txt, que o instalador mostra ao terminar.
"""
import json
import os
import re
import subprocess
import sys
import time
from xml.sax.saxutils import escape

PASTA = os.path.dirname(os.path.abspath(__file__))
TAREFA = 'Agente de Cameras'
SEM_JANELA = 0x08000000
ESPERA_DO_AGENTE = 25
PADRAO_CODIGO = re.compile(r'-([A-Z0-9]{12})(?:\s*\(\d+\))?\.exe$', re.IGNORECASE)


class Falhou(Exception):
    pass


def codigo_do_arquivo(nome):
    """O navegador pode acrescentar " (1)" ao baixar de novo: o código continua valendo."""
    achado = PADRAO_CODIGO.search(nome or '')
    return achado.group(1).upper() if achado else ''


def xml_da_tarefa(pasta):
    python = os.path.join(pasta, 'runtime', 'python.exe')
    agente = os.path.join(pasta, 'agente.py')
    config = os.path.join(pasta, 'config.json')
    argumentos = f'"{agente}" "{config}"'
    # ExecutionTimeLimit PT0S: o padrão do Windows mata a tarefa depois de 3 dias.
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Le as cameras da loja e envia ao portal.</Description></RegistrationInfo>
  <Triggers><BootTrigger><Enabled>true</Enabled></BootTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <Enabled>true</Enabled>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(python)}</Command><Arguments>{escape(argumentos)}</Arguments><WorkingDirectory>{escape(pasta)}</WorkingDirectory></Exec></Actions>
</Task>
'''


def _rodar(*comando):
    return subprocess.run(comando, capture_output=True, text=True, errors='replace', creationflags=SEM_JANELA)


class Instalacao:
    def __init__(self, pasta=PASTA, rodar=_rodar, esperar=time.sleep, http=None):
        self.pasta, self.rodar, self.esperar = pasta, rodar, esperar
        if http is None:
            import requests
            http = requests
        self.http = http
        self.portal = self.token = ''

    def caminho(self, nome):
        return os.path.join(self.pasta, nome)

    def passo(self, texto):
        print(f'>> {texto}', flush=True)
        self.avisar(texto)

    def avisar(self, texto):
        if not self.token:
            return
        try:
            self.http.post(f'{self.portal}/cameras/agente/descoberta/', data={'erro': f'Instalador: {texto}'},
                           headers={'Authorization': f'Bearer {self.token}'}, timeout=15)
        except Exception:  # noqa: BLE001 — só um aviso: o erro de verdade aparece no próximo passo
            pass

    def _config_guardada(self):
        try:
            with open(self.caminho('config.json'), encoding='utf-8') as arquivo:
                return json.load(arquivo)
        except (OSError, ValueError):
            return {}

    def ativar(self, nome_do_arquivo):
        with open(self.caminho('portal.txt'), encoding='utf-8') as arquivo:
            self.portal = arquivo.read().strip().rstrip('/')
        codigo = codigo_do_arquivo(nome_do_arquivo)
        if not codigo:
            guardada = self._config_guardada()
            if guardada.get('token'):       # reinstalação com o arquivo renomeado: segue com a loja de antes
                self.token = guardada['token']
                return ''
            raise Falhou('Este instalador não é de nenhuma loja. Baixe pelo portal, em Câmeras > Configuração, sem renomear o arquivo.')
        try:
            resposta = self.http.post(f'{self.portal}/cameras/agente/ativar/', data={'codigo': codigo}, timeout=30)
        except Exception as erro:
            raise Falhou(f'Sem conexão com o portal ({self.portal}). Confira a internet deste computador e rode de novo.') from erro
        if resposta.status_code == 404:
            raise Falhou('O portal ainda não tem esta versão das câmeras. Avise quem cuida do portal.')
        if resposta.status_code != 200:
            raise Falhou('O código deste instalador venceu ou não vale mais. Baixe de novo pelo portal, em Câmeras > Configuração.')
        dados = resposta.json()
        self.token = dados['token']
        config = {**self._config_guardada(), 'portal': self.portal, 'token': self.token, 'analisar': True, 'modelo': 'yolox_tiny.onnx'}
        with open(self.caminho('config.json'), 'w', encoding='utf-8') as arquivo:
            json.dump(config, arquivo, indent=2)
        return dados.get('loja', '')

    def conferir_pecas(self):
        python = self.caminho(os.path.join('runtime', 'python.exe'))
        for modulo, nome in (('cv2', 'leitura de vídeo'), ('onnxruntime', 'análise de imagem'), ('numpy', 'cálculo'),
                             ('av', 'vídeo ao vivo'), ('aiortc', 'transmissão ao vivo')):
            r = self.rodar(python, '-c', f'import {modulo}')
            if r.returncode != 0:
                raise Falhou(f'A peça "{nome}" não abriu neste computador: {(r.stderr or "").strip()[-200:]}')

    def proteger_token(self):
        # Por SID: o nome do grupo de administradores muda com o idioma do Windows.
        self.rodar('icacls', self.caminho('config.json'), '/inheritance:r', '/grant:r', '*S-1-5-18:F', '*S-1-5-32-544:F')

    def liberar_firewall(self):
        # Sem isto o Firewall do Windows corta o vídeo direto para quem está na mesma rede. Falhar não impede o resto.
        python = self.caminho(os.path.join('runtime', 'python.exe'))
        self.rodar('netsh', 'advfirewall', 'firewall', 'delete', 'rule', f'name={TAREFA}')
        self.rodar('netsh', 'advfirewall', 'firewall', 'add', 'rule', f'name={TAREFA}', 'dir=in', 'action=allow',
                   f'program={python}', 'protocol=udp', 'enable=yes')

    def criar_tarefa(self):
        caminho = self.caminho('tarefa.xml')
        with open(caminho, 'w', encoding='utf-16') as arquivo:
            arquivo.write(xml_da_tarefa(self.pasta))
        r = self.rodar('schtasks', '/Create', '/F', '/TN', TAREFA, '/XML', caminho)
        if r.returncode != 0:
            raise Falhou(f'O Windows não criou a tarefa do agente: {(r.stderr or r.stdout or "").strip()[:200]}')
        self.rodar('schtasks', '/End', '/TN', TAREFA)
        r = self.rodar('schtasks', '/Run', '/TN', TAREFA)
        if r.returncode != 0:
            raise Falhou(f'O Windows não iniciou o agente: {(r.stderr or r.stdout or "").strip()[:200]}')

    def confirmar_agente(self):
        self.esperar(ESPERA_DO_AGENTE)
        r = self.rodar('schtasks', '/Query', '/TN', TAREFA, '/FO', 'LIST')
        # O texto da situação muda com o idioma; o que muda de verdade é o processo ter caído e deixado log.
        if r.returncode != 0:
            raise Falhou('A tarefa do agente não ficou registrada no Windows.')
        if not os.path.exists(self.caminho('agente.log')):
            raise Falhou('O agente não chegou a iniciar. Rode de novo como administrador.')

    def executar(self, nome_do_arquivo):
        loja = self.ativar(nome_do_arquivo)
        self.passo('conferindo as peças')
        self.conferir_pecas()
        self.proteger_token()
        self.liberar_firewall()
        self.passo('ligando o agente')
        self.criar_tarefa()
        self.confirmar_agente()
        return loja


def principal(argv, instalacao=None):
    instalacao = instalacao or Instalacao()
    try:
        loja = instalacao.executar(argv[1] if len(argv) > 1 else '')
    except Falhou as erro:
        mensagem, codigo = str(erro), 1
        instalacao.avisar(f'falhou: {mensagem}')
    except Exception as erro:  # noqa: BLE001 — a pessoa precisa ver o motivo, seja qual for
        mensagem, codigo = f'Erro inesperado: {type(erro).__name__}: {erro}', 1
        instalacao.avisar(f'falhou: {mensagem}')
    else:
        mensagem, codigo = 'Pronto' + (f': câmeras da loja {loja} ligadas' if loja else '') + '. As imagens aparecem no portal em alguns minutos.', 0
    with open(instalacao.caminho('resultado.txt'), 'w', encoding='utf-16') as arquivo:
        arquivo.write(mensagem)
    print(mensagem, flush=True)
    return codigo


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='cp1252', errors='replace')
    sys.exit(principal(sys.argv))
