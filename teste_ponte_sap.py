"""Ponte da Automação SAP de ponta a ponta, no Windows: servidor + WebSocket + um app Tk no lugar do AutomacaoSAP.

Roda com qualquer Python 3.11+ do Windows que tenha tkinter (não precisa do venv do portal):
    py teste_ponte_sap.py

Sobe a ponte numa porta de teste (--dev --sem-portal), conecta como a página do portal,
clica, digita, rola, responde a pergunta Sim/Não, redimensiona e confere que o app some da área
de trabalho enquanto está no portal e volta quando a página sai. Nada sai para a rede.
"""
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, 'ponte_sap'))
import imagem  # noqa: E402,F401
import janela  # noqa: E402
import ponte  # noqa: E402

PORTA = 47999
ORIGEM = 'http://localhost:8765'
PASTA = tempfile.mkdtemp(prefix='zz-ponte-')
ESTADO = os.path.join(PASTA, 'estado.json')
PYW = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
ok = falhas = 0


def t(nome, cond, extra=''):
    global ok, falhas
    ok, falhas = ok + bool(cond), falhas + (not cond)
    print(('  OK   ' if cond else '  FALHA ') + nome, '' if cond else extra)


def est():
    for _ in range(30):
        try:
            with open(ESTADO, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            time.sleep(.05)
    return {}


def esperar(cond, segundos=5):
    fim = time.time() + segundos
    while time.time() < fim:
        try:
            if cond():
                return True
        except (KeyError, TypeError, IndexError):
            pass
        time.sleep(.1)
    return False


def http(metodo, caminho, corpo=None, origem=ORIGEM):
    pedido = urllib.request.Request(f'http://127.0.0.1:{PORTA}{caminho}', method=metodo,
                                    data=json.dumps(corpo).encode() if corpo is not None else None,
                                    headers={'Origin': origem, 'Content-Type': 'application/json'} if origem else {})
    try:
        with urllib.request.urlopen(pedido, timeout=5) as r:
            return r.status, json.loads(r.read() or b'{}'), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}'), dict(e.headers)


class Cliente:
    """WebSocket do lado do navegador (quadros mascarados), só para o teste."""

    def __init__(self, origem=ORIGEM, token='t'):
        self.s = socket.create_connection(('127.0.0.1', PORTA), timeout=10)
        chave = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f'GET /ws?token={token} HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                        f'Sec-WebSocket-Key: {chave}\r\nSec-WebSocket-Version: 13\r\nOrigin: {origem}\r\n\r\n').encode())
        resposta = b''
        while b'\r\n\r\n' not in resposta:
            resposta += self.s.recv(1)
        self.status = resposta.split(b' ')[1].decode()
        self.camadas, self.layout, self.mensagens, self.faixas = {}, None, [], 0

    def _ler(self, n):
        dados = b''
        while len(dados) < n:
            pedaco = self.s.recv(n - len(dados))
            if not pedaco:
                raise ConnectionError
            dados += pedaco
        return dados

    def receber(self):
        a, b = self._ler(2)
        n = b & 0x7F
        if n == 126:
            n = struct.unpack('>H', self._ler(2))[0]
        elif n == 127:
            n = struct.unpack('>Q', self._ler(8))[0]
        dados = self._ler(n)
        op = a & 0x0F
        if op == 1:
            m = json.loads(dados)
            self.mensagens.append(m)
            if m.get('t') == 'layout':
                self.layout = m
        elif op == 2:
            tam = struct.unpack('>I', dados[:4])[0]
            cab = json.loads(dados[4:4 + tam])
            png = dados[4 + tam:]
            self.faixas += 1
            self.camadas.setdefault(cab['id'], {})[cab['y']] = (cab, png[:8] == b'\x89PNG\r\n\x1a\n')
        elif op == 8:
            raise ConnectionError('fechou')
        return op

    def bombear(self, segundos=.4):
        fim = time.time() + segundos
        self.s.settimeout(.1)
        while time.time() < fim:
            try:
                self.receber()
            except socket.timeout:
                pass
        self.s.settimeout(10)

    def enviar(self, obj):
        dados = json.dumps(obj).encode()
        mascara = os.urandom(4)
        n = len(dados)
        cab = struct.pack('>BB', 0x81, 0x80 | n) if n < 126 else struct.pack('>BBH', 0x81, 0x80 | 126, n)
        self.s.sendall(cab + mascara + bytes(c ^ mascara[i % 4] for i, c in enumerate(dados)))

    def principal(self):
        return next(c for c in self.layout['camadas'] if c['tipo'] == 'principal')

    def popups(self):
        return [c for c in self.layout['camadas'] if c['tipo'] == 'popup']

    def clicar(self, alvo, x, y):
        self.enviar({'t': 'mouse', 'acao': 'descer', 'alvo': alvo, 'x': x, 'y': y})
        self.enviar({'t': 'mouse', 'acao': 'subir', 'alvo': alvo, 'x': x, 'y': y})

    def fechar(self):
        self.s.close()


ponte_proc = subprocess.Popen([sys.executable, os.path.join(AQUI, 'ponte_sap', 'ponte.py'), '--porta', str(PORTA), '--dev',
                               '--sem-portal', '--config', os.path.join(PASTA, 'ponte.json'), '--sem-ninguem', '2',
                               '--comando', PYW, os.path.join(AQUI, 'ponte_sap', 'app_de_teste.py'), ESTADO],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    t('a ponte sobe', esperar(lambda: http('GET', '/estado')[0] == 200, 10))

    print('Segurança')
    status, corpo, cab = http('GET', '/estado', origem='https://site-qualquer.exemplo')
    t('página de outro site não conversa com a ponte', status == 403 and 'Access-Control-Allow-Origin' not in cab)
    status, corpo, cab = http('OPTIONS', '/estado')
    t('o navegador pode perguntar (CORS + rede privada)', status == 204 and cab.get('Access-Control-Allow-Origin') == ORIGEM
      and cab.get('Access-Control-Allow-Private-Network') == 'true')
    falso = Cliente(origem='https://site-qualquer.exemplo')
    t('WebSocket de outro site é recusado', falso.status == '403')
    falso.fechar()
    t('a ponte só escuta no próprio computador', ponte_proc.poll() is None)

    print('Estado e abrir o app')
    status, e, _ = http('GET', '/estado')
    t('estado: versão, PC, app ainda fechado', status == 200 and e['ponte'] == ponte.VERSAO and e['app']['instalado'] and not e['app']['rodando'], e)
    c = Cliente()
    t('o portal conecta', c.status == '101')
    c.bombear(.5)
    t('a ponte cumprimenta com o estado', c.mensagens and c.mensagens[0]['t'] == 'ola')
    c.enviar({'t': 'iniciar'})
    t('o app abre e a janela vem para o portal', esperar(lambda: (c.bombear(.3), c.layout and c.layout['aberto'])[1], 12))
    prin = c.principal()
    hwnd = prin['id']
    t('a janela principal some da área de trabalho (vai para fora da tela)', esperar(lambda: janela.retangulo(hwnd)[0] < ponte.LONGE, 4),
      janela.retangulo(hwnd))
    c.bombear(.8)
    faixas = c.camadas.get(hwnd, {})
    altura_coberta = sum(cab['a'] for cab, _png in faixas.values())
    t('a tela do app chega inteira, em faixas PNG', faixas and all(png for _c, png in faixas.values())
      and altura_coberta == prin['a'], (altura_coberta, prin['a']))

    print('Mexer no app pelo portal')
    t('o app de teste informa onde estão os botões', esperar(lambda: 'pos' in est(), 5))
    pos = est()['pos']

    def centro(nome):
        x, y, l, a = pos[nome]
        return x + l // 2, y + a // 2

    antes = c.faixas
    c.clicar(hwnd, *centro('Somar'))
    t('clique no botão', esperar(lambda: est()['cliques'] == 1))
    c.bombear(.6)
    t('e a tela atualizada chega (só o pedaço que mudou)', 0 < c.faixas - antes < 6, c.faixas - antes)
    c.clicar(hwnd, *centro('entrada'))
    time.sleep(.3)
    c.enviar({'t': 'texto', 'alvo': hwnd, 'texto': 'Transferência 2,5'})
    c.enviar({'t': 'tecla', 'alvo': hwnd, 'key': 'Enter', 'code': 'Enter'})
    t('digitar com acento e Enter', esperar(lambda: est()['entrada'] == 'Transferência 2,5' and est()['enter'] == 1), est())
    c.enviar({'t': 'tecla', 'alvo': hwnd, 'key': 'Backspace', 'code': 'Backspace'})
    t('Backspace', esperar(lambda: est()['entrada'] == 'Transferência 2,'), est().get('entrada'))
    c.enviar({'t': 'roda', 'alvo': hwnd, 'x': centro('lista')[0], 'y': centro('lista')[1], 'delta': -120})
    t('roda do mouse', esperar(lambda: est()['rolou'] >= 1))
    c.clicar(hwnd, *centro('lista'))
    t('escolher na lista', esperar(lambda: (est().get('lista') or '').startswith('Item')))
    c.enviar({'t': 'mouse', 'acao': 'descer', 'alvo': 999, 'x': 1, 'y': 1})
    t('janela que não é do app é ignorada', ponte_proc.poll() is None)

    print('Pergunta Sim/Não do Windows')
    c.clicar(hwnd, *centro('Perguntar'))
    t('a pergunta aparece como camada no portal', esperar(lambda: (c.bombear(.2), c.popups())[1], 6))
    pop = c.popups()[0]
    t('a pergunta também fica fora da área de trabalho', esperar(lambda: janela.retangulo(pop['id'])[0] < ponte.LONGE, 3),
      janela.retangulo(pop['id']))
    t('centralizada sobre o app no portal', 0 <= pop['x'] < prin['l'] and 0 <= pop['y'] < prin['a'], pop)
    t('com título e imagem', pop['titulo'] == 'Confirmação' and esperar(lambda: (c.bombear(.2), pop['id'] in c.camadas)[1], 3))
    botoes = {janela.titulo(h).replace('&', ''): janela.retangulo(h) for h in janela.filhos(pop['id']) if janela.classe(h) == 'Button'}
    px, py, _pl, _pa = janela.retangulo(pop['id'])
    bx, by, bl, ba = botoes.get('Sim') or botoes.get('Yes')
    c.clicar(pop['id'], bx - px + bl // 2, by - py + ba // 2)
    t('clicar em "Sim" na pergunta, pelo portal', esperar(lambda: est()['resposta'] is True), est().get('resposta'))
    t('a pergunta sai do portal', esperar(lambda: (c.bombear(.2), not c.popups())[1], 4))
    c.clicar(hwnd, *centro('Perguntar'))
    esperar(lambda: (c.bombear(.2), c.popups())[1], 6)
    c.enviar({'t': 'texto', 'alvo': c.popups()[0]['id'], 'texto': 'n'})
    t('a letra de atalho responde a pergunta ("N" de Não)', esperar(lambda: est()['resposta'] is False), est().get('resposta'))

    print('Tamanho da janela do portal')
    c.enviar({'t': 'tamanho', 'l': 1100, 'a': 700})
    t('o app acompanha o tamanho da janela do portal', esperar(lambda: (c.bombear(.2), c.principal()['l'] == 1100 and c.principal()['a'] == 700)[1], 5),
      c.principal())
    c.enviar({'t': 'tamanho', 'l': 50, 'a': 50})
    t('e não fica menor que o mínimo usável', esperar(lambda: (c.bombear(.2), (c.principal()['l'], c.principal()['a']) == (640, 420))[1], 3),
      c.principal())
    c.enviar({'t': 'tudo'})
    antes = c.faixas
    c.bombear(.6)
    t('"manda tudo de novo" (página recarregou o desenho)', c.faixas - antes >= c.principal()['a'] // 48)

    print('Outra aba e saída')
    c2 = Cliente()
    c2.bombear(.5)
    try:
        c.bombear(1)
    except ConnectionError:
        pass
    t('outra aba assume e a anterior é avisada e fechada', any(m.get('t') == 'assumido' for m in c.mensagens))
    _, e, _ = http('GET', '/estado')
    t('o estado diz que o app está no portal, em uso', e['app']['rodando'] and e['app']['no_portal'] and e['em_uso_por'] == 'teste', e)
    c2.fechar()
    c.fechar()
    t('sem ninguém no portal, o app volta para a área de trabalho', esperar(lambda: janela.retangulo(hwnd)[0] > ponte.LONGE, 8),
      janela.retangulo(hwnd))
    _, e, _ = http('GET', '/estado')
    t('e o estado mostra isso', not e['app']['no_portal'])
    c3 = Cliente()
    c3.bombear(.3)
    c3.enviar({'t': 'reiniciar'})
    t('reiniciar o app pelo portal', esperar(lambda: (c3.bombear(.3), c3.layout and c3.layout['aberto'] and c3.principal()['id'] != hwnd)[1], 15))
    c3.fechar()

    print('Pareamento e instalação')
    st, corpo, _ = http('POST', '/parear', {'chave': ''})
    t('pareamento sem chave é recusado', st == 400)
    st, corpo, _ = http('POST', '/parear', {'chave': 'abc123'})
    with open(os.path.join(PASTA, 'ponte.json'), encoding='utf-8') as f:
        salvo = json.load(f)
    t('a página do portal pareia a ponte (a chave fica guardada)', st == 200 and salvo.get('chave') == 'abc123' and 'sem_portal' not in salvo)
    t('pareamento de outro site é recusado', http('POST', '/parear', {'chave': 'x'}, origem='https://site-qualquer.exemplo')[0] == 403)
    print('Ponte que caiu com o app escondido')
    c4 = Cliente()
    c4.bombear(.3)
    c4.enviar({'t': 'iniciar'})
    esperar(lambda: (c4.bombear(.3), c4.layout and c4.layout['aberto'])[1], 12)
    h4 = c4.principal()['id']
    esperar(lambda: janela.retangulo(h4)[0] < ponte.LONGE, 4)
    ponte_proc.kill()
    ponte_proc.wait()
    t('a ponte caiu com o app fora da tela', janela.retangulo(h4)[0] < ponte.LONGE)
    ponte_proc = subprocess.Popen([sys.executable, os.path.join(AQUI, 'ponte_sap', 'ponte.py'), '--porta', str(PORTA), '--dev',
                                   '--sem-portal', '--config', os.path.join(PASTA, 'ponte.json'), '--sem-ninguem', '2',
                                   '--comando', PYW, os.path.join(AQUI, 'ponte_sap', 'app_de_teste.py'), ESTADO],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t('ao voltar, a ponte traz o app de volta para a área de trabalho', esperar(lambda: janela.retangulo(h4)[0] > ponte.LONGE, 12),
      janela.retangulo(h4))
finally:
    ponte_proc.kill()
    # O app de teste foi aberto pela ponte: sem ela, ele ficaria rodando sozinho.
    for h in janela.janelas_do_processo(janela.processos_por_nome('pythonw.exe')):
        if janela.titulo(h) == 'Automação SAP — teste':
            subprocess.run(['taskkill', '/PID', str(janela.pid_da_janela(h)), '/F'], capture_output=True)

print('Instalação a partir do pacote')
import io  # noqa: E402
import zipfile  # noqa: E402

pacote = io.BytesIO()
with zipfile.ZipFile(pacote, 'w') as z:
    z.writestr('AutomacaoSAP\\AutomacaoSAP.exe', b'MZ novo')
    z.writestr('AutomacaoSAP\\_internal\\base_library.zip', b'novo')
    z.writestr('AutomacaoSAP\\config\\config.json', b'{"do_pacote": true}')
destino = os.path.join(PASTA, 'AutomacaoSAP')
os.makedirs(os.path.join(destino, 'config'))
os.makedirs(os.path.join(destino, '_internal'))
with open(os.path.join(destino, 'config', 'config.json'), 'w') as f:
    f.write('{"da_loja": true}')
with open(os.path.join(destino, '_internal', 'velho.dll'), 'w') as f:
    f.write('x')
ponte.extrair_app(pacote.getvalue(), destino)
t('instala o .exe novo (zip com barras do Windows)', open(os.path.join(destino, 'AutomacaoSAP.exe'), 'rb').read() == b'MZ novo')
t('apaga o _internal velho', not os.path.exists(os.path.join(destino, '_internal', 'velho.dll'))
  and os.path.exists(os.path.join(destino, '_internal', 'base_library.zip')))
t('e não mexe na config da loja', json.load(open(os.path.join(destino, 'config', 'config.json'))) == {'da_loja': True})
ruim = io.BytesIO()
with zipfile.ZipFile(ruim, 'w') as z:
    z.writestr('AutomacaoSAP/AutomacaoSAP.exe', b'MZ')
    z.writestr('AutomacaoSAP/../../fora.txt', b'x')
try:
    ponte.extrair_app(ruim.getvalue(), os.path.join(PASTA, 'outro'))
    recusou = False
except RuntimeError:
    recusou = True
t('pacote com caminho para fora da pasta é recusado', recusou and not os.path.exists(os.path.join(PASTA, 'fora.txt')))
t('teclas do navegador viram teclas do Windows', ponte.vk_do_evento({'key': 'Enter'}) == (0x0D, False)
  and ponte.vk_do_evento({'key': 'F5'}) == (0x74, False) and ponte.vk_do_evento({'key': 'c', 'code': 'KeyC'}) == (ord('C'), True))

print(f'\n{ok} OK, {falhas} falha(s)')
sys.exit(1 if falhas else 0)
