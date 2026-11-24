"""Ponte da Automação SAP: o app roda no Windows da loja e aparece numa janela dentro do portal.

    pythonw ponte.py            (o instalador deixa isso abrindo junto com o Windows)

O AutomacaoSAP precisa do SAP GUI do próprio computador, então ele continua rodando aqui.
A ponte:
- abre o app (ou acha o que já está aberto) e tira as janelas dele da área de trabalho;
- manda para a página do portal só os pedaços da tela que mudaram;
- recebe da página o mouse e o teclado e entrega direto às janelas do app;
- quando ninguém está olhando pelo portal, devolve o app para a área de trabalho.

Só escuta em 127.0.0.1 (nada de fora do computador entra) e só conversa com a página do portal
(o navegador garante a origem). Cada sessão é conferida no portal: fica registrado quem usou.
Sem bibliotecas de fora: só a biblioteca padrão do Python.
"""
import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from logging.handlers import RotatingFileHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imagem  # noqa: E402
import janela  # noqa: E402

VERSAO = '1.1.0'
PORTA = 47861
PORTAL_PADRAO = 'https://chamados.redeconfianca.com.br'
EXE = 'AutomacaoSAP.exe'
FORA_X, FORA_Y = -25000, -25000             # onde as janelas do app ficam enquanto aparecem no portal
LONGE = -10000                              # janela com x menor que isso já está escondida
SEM_NINGUEM_SEGUNDOS = 15                   # sem ninguém no portal, o app volta para a área de trabalho
QUADROS_POR_SEGUNDO = 15
QUADROS_PARADO = 4                          # sem mexer e sem mudança: olha a tela menos vezes
PASTAS_DA_LOJA = ('config', 'logs')         # o que a instalação/atualização nunca apaga
CONFERIR_VERSAO_SEGUNDOS = 30 * 60          # de quanto em quanto tempo pergunta ao portal a versão publicada
GUID_WS = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

log = logging.getLogger('ponte')


def pasta_da_ponte():
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    pasta = os.path.join(base, 'PonteAutomacaoSAP')
    os.makedirs(pasta, exist_ok=True)
    return pasta


# ── Configuração ──────────────────────────────────────────────────────────────
class Config:
    def __init__(self, caminho, portal=None, dev=False):
        self.caminho, self.dev = caminho, dev
        self.sem_portal = False
        self.dados = {}
        try:
            with open(caminho, encoding='utf-8') as f:
                self.dados = json.load(f) or {}
        except (OSError, ValueError):
            pass
        if portal:
            self.dados['portal'] = portal.rstrip('/')
        self.dados.setdefault('portal', PORTAL_PADRAO)

    def salvar(self):
        temporario = self.caminho + '.tmp'
        with open(temporario, 'w', encoding='utf-8') as f:
            json.dump(self.dados, f, ensure_ascii=False, indent=2)
        os.replace(temporario, self.caminho)

    @property
    def portal(self):
        return self.dados['portal']

    @property
    def origem_portal(self):
        p = urllib.parse.urlsplit(self.portal)
        return f'{p.scheme}://{p.netloc}'

    def origem_permitida(self, origem):
        if not origem:
            return False
        if origem == self.origem_portal:
            return True
        # Só em teste (--dev): a página servida na própria máquina.
        return self.dev and urllib.parse.urlsplit(origem).hostname in ('localhost', '127.0.0.1')


# ── O aplicativo ──────────────────────────────────────────────────────────────
def _pastas_candidatas(config):
    base = pasta_da_ponte()
    lista = [config.dados.get('pasta_app'), os.path.join(base, 'AutomacaoSAP'), r'C:\AutomacaoSAP',
             os.path.join(os.path.expanduser('~'), 'Desktop', 'AutomacaoSAP'),
             os.path.join(os.path.expanduser('~'), 'Downloads', 'AutomacaoSAP')]
    return [p for p in lista if p]


class Aplicativo:
    """O AutomacaoSAP deste computador: onde está, se está aberto e as janelas dele."""

    def __init__(self, config, comando=None):
        self.config = config
        self.comando = comando          # teste: outro programa no lugar do .exe
        self.nome_exe = os.path.basename(comando[0]) if comando else EXE
        self.proc = None
        self.pid = None
        self.principal = None
        self.popups = {}                # hwnd → (x, y) relativo à área útil da principal
        self.escondido = False
        self.original = None            # (x, y, largura, altura) antes de esconder
        self.parar_vigia = None
        self.trava = threading.RLock()
        self.instalando = None          # {'etapa': ..., 'progresso': 0..100, 'erro': ...}
        self.publicada = None           # a versão publicada no portal, na última conferência

    # Onde está
    def pasta(self):
        for p in _pastas_candidatas(self.config):
            if os.path.isfile(os.path.join(p, EXE)):
                return p
        return None

    def instalado(self):
        return bool(self.comando) or self.pasta() is not None

    def versao_instalada(self):
        pasta = self.pasta()
        if not pasta:
            return None
        try:
            with open(os.path.join(pasta, 'versao_ponte.txt'), encoding='utf-8') as f:
                return f.read().strip() or None
        except OSError:
            return None

    # Processo
    def _pids(self):
        if self.proc is not None and self.proc.poll() is None:
            return [self.proc.pid]
        return janela.processos_por_nome(self.nome_exe)

    def rodando(self):
        return bool(self._pids())

    def iniciar(self):
        with self.trava:
            if self.rodando():
                return True
            if self.comando:
                self.proc = subprocess.Popen(self.comando)
            else:
                pasta = self.pasta()
                if not pasta:
                    return False
                self.proc = subprocess.Popen([os.path.join(pasta, EXE)], cwd=pasta)
            self.principal, self.popups = None, {}
            log.info('App aberto pela ponte (pid %s)', self.proc.pid)
            return True

    def encerrar(self, esperar=6):
        with self.trava:
            if self.principal and janela.user32.IsWindow(self.principal):
                janela.fechar(self.principal)
            fim = time.time() + esperar
            while time.time() < fim and self.rodando():
                time.sleep(.2)
            if self.proc is not None and self.proc.poll() is None:
                self.proc.kill()
            self.proc, self.principal, self.popups, self.escondido = None, None, {}, False
            self._parar_vigia()

    # Janelas
    def atualizar(self):
        """Acha a janela principal e as outras (perguntas, telas de apoio). Devolve se o app está aberto."""
        with self.trava:
            pids = self._pids()
            if not pids:
                self.principal, self.popups, self.pid = None, {}, None
                self._parar_vigia()
                return False
            if self.pid not in pids:
                self.pid = pids[0]
                self._parar_vigia()
            janelas = janela.janelas_do_processo(pids)
            if self.principal not in janelas:
                candidatas = [h for h in janelas if not janela.dono(h) and not janela.e_dialogo(h)] or janelas
                self.principal = max(candidatas, key=lambda h: janela.retangulo(h)[2] * janela.retangulo(h)[3], default=None)
                self.original = None
                if self.escondido and self.principal:
                    self._esconder_principal()
            if self.escondido:
                self._ligar_vigia()
            atuais = {}
            for h in janelas:
                if h == self.principal:
                    continue
                atuais[h] = self._posicao_do_popup(h)
            self.popups = atuais
            return self.principal is not None

    def _posicao_do_popup(self, h):
        """Onde a janela de apoio aparece no portal (relativo à área útil da principal)."""
        x, y, largura, altura = janela.retangulo(h)
        if self.escondido and self.principal:
            px, py, pl, pa = janela.area_cliente(self.principal)
            if x > LONGE:
                # O Windows trouxe para a tela (pergunta do Windows): leva para fora, centralizada na principal.
                rx, ry = max(0, (pl - largura) // 2), max(0, (pa - altura) // 3)
                janela.mover(h, px + rx, py + ry)
                return rx, ry
            return x - px, y - py
        return 0, 0

    def _ao_aparecer(self, hwnd):
        if not self.escondido or hwnd == self.principal or not self.principal:
            return
        x, _y, _l, _a = janela.retangulo(hwnd)
        if x > LONGE:
            self._posicao_do_popup(hwnd)

    def _ligar_vigia(self):
        if self.parar_vigia is None and self.pid:
            self.parar_vigia = threading.Event()
            janela.vigiar_janelas_novas(self.pid, self._ao_aparecer, self.parar_vigia)

    def _parar_vigia(self):
        if self.parar_vigia is not None:
            self.parar_vigia.set()
            self.parar_vigia = None

    def _esconder_principal(self):
        if self.original is None:
            self.original = janela.retangulo_bruto(self.principal)
        _x, _y, largura, altura = janela.retangulo_bruto(self.principal)
        janela.mover(self.principal, FORA_X, FORA_Y, largura, altura)
        janela.tirar_da_barra_de_tarefas(self.principal)

    def esconder(self):
        """O app sai da área de trabalho e passa a aparecer só no portal."""
        with self.trava:
            self.escondido = True
            if self.atualizar():
                self._esconder_principal()
                for h in list(self.popups):
                    self.popups[h] = self._posicao_do_popup(h)
                self._ligar_vigia()

    def mostrar_na_area_de_trabalho(self):
        with self.trava:
            self.escondido = False
            self._parar_vigia()
            if self.principal and janela.user32.IsWindow(self.principal):
                x, y, largura, altura = self.original or (120, 80, 1100, 720)
                if x < LONGE:
                    x, y = 120, 80
                janela.voltar_para_barra_de_tarefas(self.principal)
                janela.mover(self.principal, x, y, largura, altura)
                for h in list(self.popups):
                    _x, _y, l, a = janela.retangulo(h)
                    janela.voltar_para_barra_de_tarefas(h) if not janela.dono(h) else None
                    janela.mover(h, x + max(0, (largura - l) // 2), y + max(0, (altura - a) // 3))
            self.original = None

    def ficou_escondida(self):
        """Alguma janela do app está fora da tela sem a ponte estar mostrando no portal?"""
        with self.trava:
            if self.escondido or not self.atualizar():
                return False
            return any(janela.retangulo(h)[0] < LONGE for h in [self.principal, *self.popups])

    def redimensionar(self, largura, altura):
        """Deixa a área útil do app do tamanho da janela do portal."""
        with self.trava:
            if not self.principal:
                return
            largura, altura = max(640, min(int(largura), 3840)), max(420, min(int(altura), 2160))
            jx, jy, jl, ja = janela.retangulo_bruto(self.principal)
            _cx, _cy, cl, ca = janela.area_cliente(self.principal)
            if (cl, ca) == (largura, altura):
                return
            janela.mover(self.principal, FORA_X if self.escondido else jx, FORA_Y if self.escondido else jy,
                         largura + (jl - cl), altura + (ja - ca))

    def camadas(self):
        """[(id, 'principal'|'popup', x, y, largura, altura, título)] — x, y relativos à área útil da principal."""
        with self.trava:
            if not self.principal:
                return []
            _x, _y, l, a = janela.area_cliente(self.principal)
            saida = [(self.principal, 'principal', 0, 0, l, a, janela.titulo(self.principal))]
            for h, (rx, ry) in self.popups.items():
                _px, _py, pl, pa = janela.retangulo(h)
                saida.append((h, 'popup', rx, ry, pl, pa, janela.titulo(h)))
            return saida

    def ponto_na_tela(self, alvo, x, y):
        """Coordenadas de tela de um ponto (x, y) dentro da camada ``alvo``."""
        if alvo == self.principal:
            cx, cy, _l, _a = janela.area_cliente(alvo)
            return cx + x, cy + y
        rx, ry, _l, _a = janela.retangulo(alvo)
        return rx + x, ry + y

    def capturar(self, alvo):
        return janela.capturar(alvo, so_cliente=(alvo == self.principal))


# ── Instalar / atualizar o app a partir do portal ─────────────────────────────
def _pedir(url, chave, tempo=30):
    pedido = urllib.request.Request(url, headers={'X-Chave-Automacao': chave, 'User-Agent': f'PonteAutomacaoSAP/{VERSAO}'})
    return urllib.request.urlopen(pedido, timeout=tempo)


def versao_publicada(config, instalada=None):
    """O que o portal diz da versão publicada: {'versao', 'url', 'sha256', ...}. Levanta se não der."""
    chave = config.dados.get('chave')
    if not chave:
        raise RuntimeError('A ponte ainda não foi pareada com o portal.')
    with _pedir(f'{config.portal}/sap/automacao/api/versao/?atual={instalada or 0}', chave) as r:
        return json.loads(r.read().decode())


_trava_instalacao = threading.Lock()


def garantir_versao_atual(app, config):
    """Sempre a versão publicada mais atual (24/11/2026: "está usando versões anteriores").

    Antes a ponte só baixava o app quando alguém clicava em instalar e nunca mais olhava —
    e uma instalação antiga achada no computador (C:\\AutomacaoSAP, Área de Trabalho, sem
    versao_ponte.txt) era usada como estava. Agora, com o app fechado, versão instalada
    diferente da publicada (ou sem número) é trocada pela publicada, na mesma pasta, sem
    mexer em config/ e logs/. Com o app aberto não fecha nada: atualiza na próxima vez
    que ele for aberto pelo portal (ou no "Reiniciar"). Sem portal, sem chave ou fora do
    ar: segue com o que tem. Devolve a versão publicada (ou None).
    """
    if config.sem_portal or app.comando or not config.dados.get('chave'):
        return None
    if not _trava_instalacao.acquire(blocking=False):
        return None                     # já tem uma conferência/instalação em andamento
    try:
        instalada = app.versao_instalada()
        try:
            info = versao_publicada(config, instalada)
        except Exception as erro:  # noqa: BLE001 — portal fora do ar: segue com a instalada
            log.info('Não deu para conferir a versão publicada: %s', erro)
            return None
        publicada = info.get('versao')
        app.publicada = publicada
        if not publicada or (app.instalado() and instalada == publicada) or app.rodando():
            return publicada
        log.info('Atualizando o app: instalada %s, publicada %s', instalada or 'sem número', publicada)
        _instalar(app, config, info)
        return publicada
    finally:
        _trava_instalacao.release()


def instalar_app(app, config):
    """Baixa a versão publicada no portal e põe na pasta do app, sem mexer em config/ e logs/."""
    with _trava_instalacao:
        # A página e a ponte (ao conectar) podem pedir juntas: quem chegou depois pergunta de
        # novo ao portal e não baixa se a instalada já é a publicada agora.
        try:
            info = versao_publicada(config, app.versao_instalada())
        except Exception:  # noqa: BLE001 — o _instalar pergunta de novo e mostra o motivo
            info = None
        if info and info.get('versao') and app.instalado() and app.versao_instalada() == info['versao']:
            app.publicada = info['versao']
            app.instalando = {'etapa': f'Versão {info["versao"]} já instalada', 'progresso': 100, 'erro': ''}
            return
        _instalar(app, config, info)


def _instalar(app, config, info):
    estado = app.instalando = {'etapa': 'Perguntando ao portal a versão atual', 'progresso': 0, 'erro': ''}
    try:
        chave = config.dados.get('chave')
        if not chave:
            raise RuntimeError('A ponte ainda não foi pareada com o portal.')
        if info is None:
            info = versao_publicada(config)
        if not info.get('versao'):
            raise RuntimeError('Nenhuma versão publicada no portal.')
        estado['etapa'] = f'Baixando a versão {info["versao"]}'
        conteudo = io.BytesIO()
        with _pedir(info['url'], chave, tempo=300) as r:
            total = int(r.headers.get('Content-Length') or info.get('tamanho') or 0)
            while True:
                bloco = r.read(256 * 1024)
                if not bloco:
                    break
                conteudo.write(bloco)
                if total:
                    estado['progresso'] = min(95, int(conteudo.tell() * 95 / total))
        dados = conteudo.getvalue()
        if info.get('sha256') and hashlib.sha256(dados).hexdigest() != info['sha256']:
            raise RuntimeError('O arquivo baixado não confere com o publicado (sha256).')
        estado['etapa'] = 'Instalando'
        destino = app.pasta() or os.path.join(pasta_da_ponte(), 'AutomacaoSAP')
        extrair_app(dados, destino)
        with open(os.path.join(destino, 'versao_ponte.txt'), 'w', encoding='utf-8') as f:
            f.write(info['versao'])
        config.dados['pasta_app'] = destino
        config.salvar()
        estado.update(etapa=f'Versão {info["versao"]} instalada', progresso=100)
    except Exception as erro:  # noqa: BLE001 — rede, disco, zip: a página mostra o motivo
        log.warning('Instalação do app falhou: %s', erro)
        estado.update(erro=str(erro)[:300], etapa='Não deu para instalar')


def extrair_app(dados, destino):
    """O zip publicado traz a pasta AutomacaoSAP/ (ou os arquivos soltos). config/ e logs/ da loja ficam."""
    with zipfile.ZipFile(io.BytesIO(dados)) as z:
        nomes = [n.replace('\\', '/') for n in z.namelist()]
        prefixo = 'AutomacaoSAP/' if any(n.startswith('AutomacaoSAP/') for n in nomes) else ''
        if not any(n == prefixo + EXE for n in nomes):
            raise RuntimeError(f'O pacote não tem o {EXE}.')
        temporaria = tempfile.mkdtemp(prefix='ponte-sap-')
        try:
            for original, nome in zip(z.namelist(), nomes):
                if not nome.startswith(prefixo) or nome.endswith('/'):
                    continue
                relativo = nome[len(prefixo):]
                if relativo.split('/')[0] in PASTAS_DA_LOJA and os.path.exists(os.path.join(destino, relativo.split('/')[0])):
                    continue
                if '..' in relativo.split('/') or os.path.isabs(relativo):
                    raise RuntimeError('Pacote com caminho inválido.')
                alvo = os.path.join(temporaria, *relativo.split('/'))
                os.makedirs(os.path.dirname(alvo), exist_ok=True)
                with z.open(original) as origem, open(alvo, 'wb') as saida:
                    shutil.copyfileobj(origem, saida)
            os.makedirs(destino, exist_ok=True)
            for item in os.listdir(destino):
                if item in PASTAS_DA_LOJA:
                    continue
                caminho = os.path.join(destino, item)
                shutil.rmtree(caminho) if os.path.isdir(caminho) else os.remove(caminho)
            for item in os.listdir(temporaria):
                shutil.move(os.path.join(temporaria, item), os.path.join(destino, item))
        finally:
            shutil.rmtree(temporaria, ignore_errors=True)


def conferir_sessao(config, token):
    """O portal diz quem é (e se pode usar). Sem portal (teste --sem-portal): vale qualquer token."""
    if config.sem_portal:
        return {'ok': True, 'usuario': 'teste'}
    chave = config.dados.get('chave')
    if not chave:
        return {'ok': False, 'erro': 'A ponte ainda não foi pareada com o portal.'}
    url = f'{config.portal}/sap/automacao/api/ponte/conferir/?' + urllib.parse.urlencode(
        {'token': token, 'pc': os.environ.get('COMPUTERNAME', ''), 'ponte': VERSAO})
    try:
        with _pedir(url, chave, tempo=10) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as erro:
        try:
            return json.loads(erro.read().decode())
        except ValueError:
            return {'ok': False, 'erro': f'O portal recusou ({erro.code}).'}
    except (urllib.error.URLError, OSError, ValueError) as erro:
        return {'ok': False, 'erro': f'Sem contato com o portal: {type(erro).__name__}'}


# ── Teclado do navegador → teclas do Windows ─────────────────────────────────
TECLAS = {
    'Enter': 0x0D, 'Tab': 0x09, 'Escape': 0x1B, 'Backspace': 0x08, 'Delete': 0x2E, 'Insert': 0x2D,
    'ArrowLeft': 0x25, 'ArrowUp': 0x26, 'ArrowRight': 0x27, 'ArrowDown': 0x28, 'Home': 0x24, 'End': 0x23,
    'PageUp': 0x21, 'PageDown': 0x22, 'NumpadEnter': 0x0D, ' ': 0x20,
    **{f'F{n}': 0x6F + n for n in range(1, 13)},
}


def vk_do_evento(dados):
    """(vk, é texto?) da tecla vinda do navegador."""
    chave, codigo = dados.get('key', ''), dados.get('code', '')
    if chave in TECLAS:
        return TECLAS[chave], False
    if codigo == 'NumpadEnter':
        return 0x0D, False
    if codigo.startswith('Key') and len(codigo) == 4:
        return ord(codigo[3]), True
    if codigo.startswith('Digit') and len(codigo) == 6:
        return ord(codigo[5]), True
    return None, True


# ── WebSocket mínimo (RFC 6455) ───────────────────────────────────────────────
class Fechado(Exception):
    pass


async def ler_quadro(leitor):
    cabeca = await leitor.readexactly(2)
    opcode, tamanho = cabeca[0] & 0x0F, cabeca[1] & 0x7F
    mascarado = cabeca[1] & 0x80
    if tamanho == 126:
        tamanho = struct.unpack('>H', await leitor.readexactly(2))[0]
    elif tamanho == 127:
        tamanho = struct.unpack('>Q', await leitor.readexactly(8))[0]
    if tamanho > 1_000_000:
        raise Fechado('mensagem grande demais')
    mascara = await leitor.readexactly(4) if mascarado else b'\0\0\0\0'
    dados = bytearray(await leitor.readexactly(tamanho))
    for i in range(len(dados)):
        dados[i] ^= mascara[i % 4]
    return opcode, bytes(dados)


def montar_quadro(dados, opcode):
    n = len(dados)
    if n < 126:
        cabeca = struct.pack('>BB', 0x80 | opcode, n)
    elif n < 65536:
        cabeca = struct.pack('>BBH', 0x80 | opcode, 126, n)
    else:
        cabeca = struct.pack('>BBQ', 0x80 | opcode, 127, n)
    return cabeca + dados


class Conexao:
    def __init__(self, leitor, escritor):
        self.leitor, self.escritor = leitor, escritor
        self.trava = asyncio.Lock()
        self.aberta = True

    async def enviar(self, dados):
        if not self.aberta:
            return
        quadro = montar_quadro(dados if isinstance(dados, bytes) else json.dumps(dados).encode(), 2 if isinstance(dados, bytes) else 1)
        async with self.trava:
            self.escritor.write(quadro)
            await self.escritor.drain()

    async def receber(self):
        while True:
            opcode, dados = await ler_quadro(self.leitor)
            if opcode == 8:
                raise Fechado('fechou')
            if opcode == 9:
                async with self.trava:
                    self.escritor.write(montar_quadro(dados, 10))
                continue
            if opcode in (1, 2):
                return dados

    async def fechar(self, motivo=''):
        if self.aberta:
            self.aberta = False
            try:
                async with self.trava:
                    self.escritor.write(montar_quadro(struct.pack('>H', 1000) + motivo.encode()[:100], 8))
                    await self.escritor.drain()
            except (ConnectionError, OSError):
                pass
            self.escritor.close()


# ── O servidor ────────────────────────────────────────────────────────────────
class Ponte:
    def __init__(self, config, app):
        self.config, self.app = config, app
        self.visor = None               # a conexão do portal que está controlando o app
        self.sem_ninguem_desde = time.time()
        self.usuario = ''
        self.sem_ninguem_segundos = SEM_NINGUEM_SEGUNDOS

    # HTTP
    def _cors(self, origem):
        if not self.config.origem_permitida(origem):
            return {}
        return {'Access-Control-Allow-Origin': origem, 'Vary': 'Origin', 'Access-Control-Allow-Private-Network': 'true',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type',
                'Access-Control-Max-Age': '600'}

    async def _responder(self, escritor, status, corpo, cabecalhos):
        dados = json.dumps(corpo, ensure_ascii=False).encode()
        linhas = [f'HTTP/1.1 {status}', 'Content-Type: application/json; charset=utf-8', f'Content-Length: {len(dados)}',
                  'Cache-Control: no-store', 'Connection: close'] + [f'{k}: {v}' for k, v in cabecalhos.items()]
        escritor.write(('\r\n'.join(linhas) + '\r\n\r\n').encode() + dados)
        await escritor.drain()
        escritor.close()

    def estado(self):
        app = self.app
        return {
            'ponte': VERSAO, 'pc': os.environ.get('COMPUTERNAME', ''), 'pareada': bool(self.config.dados.get('chave')),
            'app': {'instalado': app.instalado(), 'pasta': app.pasta(), 'versao': app.versao_instalada(),
                    'publicada': app.publicada,
                    'rodando': app.rodando(), 'no_portal': app.escondido, 'instalando': app.instalando},
            'em_uso_por': self.usuario if self.visor else '',
        }

    async def atender(self, leitor, escritor):
        try:
            cabecalho = await asyncio.wait_for(leitor.readuntil(b'\r\n\r\n'), 10)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            escritor.close()
            return
        linhas = cabecalho.decode('latin-1').split('\r\n')
        try:
            metodo, alvo, _versao = linhas[0].split(' ', 2)
        except ValueError:
            escritor.close()
            return
        h = {}
        for linha in linhas[1:]:
            if ':' in linha:
                k, v = linha.split(':', 1)
                h[k.strip().lower()] = v.strip()
        origem = h.get('origin', '')
        caminho, _, consulta = alvo.partition('?')
        params = dict(urllib.parse.parse_qsl(consulta))
        cors = self._cors(origem)
        if not cors:
            await self._responder(escritor, '403 Forbidden', {'erro': 'Origem não permitida.'}, {})
            return
        if metodo == 'OPTIONS':
            await self._responder(escritor, '204 No Content', {}, cors)
            return
        corpo = {}
        if metodo == 'POST':
            n = min(int(h.get('content-length') or 0), 100_000)
            if n:
                try:
                    corpo = json.loads((await leitor.readexactly(n)).decode() or '{}')
                except (ValueError, asyncio.IncompleteReadError):
                    corpo = {}
        if caminho == '/ws' and h.get('upgrade', '').lower() == 'websocket':
            await self._websocket(leitor, escritor, h, params.get('token', ''))
            return
        loop = asyncio.get_running_loop()
        if caminho == '/estado' and metodo == 'GET':
            await self._responder(escritor, '200 OK', self.estado(), cors)
        elif caminho == '/parear' and metodo == 'POST':
            # A própria página do portal (origem conferida) entrega a chave do app.
            chave = str(corpo.get('chave') or '').strip()
            if not chave or len(chave) > 100:
                await self._responder(escritor, '400 Bad Request', {'erro': 'Chave inválida.'}, cors)
                return
            self.config.dados['chave'] = chave
            self.config.salvar()
            await self._responder(escritor, '200 OK', {'ok': True}, cors)
        elif caminho == '/app/instalar' and metodo == 'POST':
            if not (self.app.instalando and not self.app.instalando.get('erro') and self.app.instalando.get('progresso', 0) < 100):
                threading.Thread(target=instalar_app, args=(self.app, self.config), daemon=True).start()
            await self._responder(escritor, '200 OK', {'ok': True}, cors)
        elif caminho == '/app/mostrar' and metodo == 'POST':
            await loop.run_in_executor(None, self.app.mostrar_na_area_de_trabalho)
            await self._responder(escritor, '200 OK', {'ok': True}, cors)
        else:
            await self._responder(escritor, '404 Not Found', {'erro': 'Não existe.'}, cors)

    # WebSocket: a sessão do portal
    async def _websocket(self, leitor, escritor, h, token):
        chave = h.get('sec-websocket-key', '')
        aceite = base64.b64encode(hashlib.sha1((chave + GUID_WS).encode()).digest()).decode()
        escritor.write(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                        f'Sec-WebSocket-Accept: {aceite}\r\n\r\n').encode())
        await escritor.drain()
        conexao = Conexao(leitor, escritor)
        loop = asyncio.get_running_loop()
        conferido = await loop.run_in_executor(None, conferir_sessao, self.config, token)
        if not conferido.get('ok'):
            await conexao.enviar({'t': 'recusado', 'erro': conferido.get('erro') or 'Sessão recusada pelo portal.'})
            await conexao.fechar('recusado')
            return
        if self.visor is not None:
            await self.visor.enviar({'t': 'assumido', 'erro': 'O app foi aberto em outra aba ou outro navegador.'})
            await self.visor.fechar('assumido')
        self.visor, self.usuario = conexao, conferido.get('usuario', '')
        log.info('Portal conectado: %s', self.usuario)
        sessao = Sessao(self, conexao)
        try:
            await sessao.rodar()
        except (Fechado, asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            sessao.parar = True
            if self.visor is conexao:
                self.visor, self.usuario = None, ''
                self.sem_ninguem_desde = time.time()
            await conexao.fechar()
            log.info('Portal desconectado')

    async def zelador(self):
        """Sem ninguém olhando pelo portal por um tempo, o app volta para a área de trabalho.

        Também cobre a ponte que caiu (ou o computador que foi reiniciado com o app aberto): janela do
        app que ficou fora da tela, sem ninguém no portal, volta para a área de trabalho.
        """
        loop = asyncio.get_running_loop()
        conferida_em = 0
        while True:
            if time.time() - conferida_em > CONFERIR_VERSAO_SEGUNDOS:
                conferida_em = time.time()
                loop.run_in_executor(None, garantir_versao_atual, self.app, self.config)
            if self.visor is None and time.time() - self.sem_ninguem_desde > self.sem_ninguem_segundos:
                if self.app.escondido or await loop.run_in_executor(None, self.app.ficou_escondida):
                    await loop.run_in_executor(None, self.app.mostrar_na_area_de_trabalho)
            await asyncio.sleep(2)


class Sessao:
    """Uma página do portal olhando e mexendo no app."""

    def __init__(self, ponte, conexao):
        self.ponte, self.conexao, self.app = ponte, conexao, ponte.app
        self.diferencas = {}
        self.ultimo_layout = None
        self.ultimo_toque = time.time()
        self.parar = False
        self.pediu_tudo = True
        self.loop = asyncio.get_running_loop()

    async def rodar(self):
        # O portal chegou: confere a versão publicada (com o app fechado, já atualiza).
        self.loop.run_in_executor(None, garantir_versao_atual, self.app, self.ponte.config)
        await self.conexao.enviar({'t': 'ola', **self.ponte.estado()})
        recebendo = asyncio.create_task(self._receber())
        try:
            while not self.parar and not recebendo.done():
                inicio = time.time()
                await self._quadro()
                parado = time.time() - self.ultimo_toque > 3
                intervalo = 1 / (QUADROS_PARADO if parado else QUADROS_POR_SEGUNDO)
                await asyncio.sleep(max(0.005, intervalo - (time.time() - inicio)))
        finally:
            recebendo.cancel()
        if recebendo.done() and recebendo.exception():
            raise recebendo.exception()

    async def _quadro(self):
        app = self.app
        aberto = await self.loop.run_in_executor(None, app.atualizar)
        if aberto and not app.escondido:
            await self.loop.run_in_executor(None, app.esconder)
        camadas = await self.loop.run_in_executor(None, app.camadas) if aberto else []
        layout = {'t': 'layout', 'aberto': aberto, 'instalado': app.instalado(),
                  'camadas': [{'id': c[0], 'tipo': c[1], 'x': c[2], 'y': c[3], 'l': c[4], 'a': c[5], 'titulo': c[6]} for c in camadas]}
        if layout != self.ultimo_layout:
            self.ultimo_layout = layout
            await self.conexao.enviar(layout)
            ids = {c[0] for c in camadas}
            for velho in [k for k in self.diferencas if k not in ids]:
                del self.diferencas[velho]
        tudo, self.pediu_tudo = self.pediu_tudo, False
        for c in camadas:
            captura = await self.loop.run_in_executor(None, app.capturar, c[0])
            if not captura:
                continue
            dif = self.diferencas.setdefault(c[0], imagem.Diferenca())
            for y, altura, png in await self.loop.run_in_executor(None, dif.mudancas, *captura, tudo):
                cabeca = json.dumps({'id': c[0], 'y': y, 'l': captura[0], 'a': altura, 'L': captura[0], 'A': captura[1]}).encode()
                await self.conexao.enviar(struct.pack('>I', len(cabeca)) + cabeca + png)

    async def _receber(self):
        while True:
            bruto = await self.conexao.receber()
            try:
                dados = json.loads(bruto.decode())
            except ValueError:
                continue
            self.ultimo_toque = time.time()
            await self.loop.run_in_executor(None, self._tratar, dados)

    def _tratar(self, d):
        app, t = self.app, d.get('t')
        if t == 'iniciar':
            if not app.rodando():
                garantir_versao_atual(app, self.ponte.config)   # abre sempre a publicada mais atual
            app.iniciar()
        elif t == 'reiniciar':
            app.encerrar()
            garantir_versao_atual(app, self.ponte.config)
            app.iniciar()
        elif t == 'tudo':
            self.pediu_tudo = True
        elif t == 'tamanho':
            app.redimensionar(d.get('l', 0), d.get('a', 0))
        elif t in ('mouse', 'roda', 'tecla', 'texto'):
            alvo = int(d.get('alvo') or 0)
            if alvo != app.principal and alvo not in app.popups:
                return
            if t == 'mouse':
                sx, sy = app.ponto_na_tela(alvo, int(d.get('x', 0)), int(d.get('y', 0)))
                acao = d.get('acao', 'mover')
                if acao == 'descer':
                    janela.ativar(alvo)
                janela.mouse(alvo, sx, sy, acao, d.get('botao', 'esquerdo'), int(d.get('mods', 0)), bool(d.get('apertado')))
            elif t == 'roda':
                sx, sy = app.ponto_na_tela(alvo, int(d.get('x', 0)), int(d.get('y', 0)))
                janela.roda(alvo, sx, sy, int(d.get('delta', 0)))
            elif t == 'texto':
                janela.texto(alvo, str(d.get('texto', ''))[:2000])
            elif t == 'tecla':
                vk, _texto = vk_do_evento(d)
                if vk:
                    mods = int(d.get('mods', 0))
                    janela.tecla(alvo, vk, False, mods)
                    janela.tecla(alvo, vk, True, mods)


# ── Rodar ─────────────────────────────────────────────────────────────────────
async def principal(config, app, porta, sem_ninguem=SEM_NINGUEM_SEGUNDOS):
    ponte = Ponte(config, app)
    ponte.sem_ninguem_segundos = sem_ninguem
    servidor = await asyncio.start_server(ponte.atender, '127.0.0.1', porta, limit=1 << 20)
    log.info('Ponte %s ouvindo em 127.0.0.1:%s (portal %s)', VERSAO, porta, config.portal)
    asyncio.create_task(ponte.zelador())
    async with servidor:
        await servidor.serve_forever()


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description='Ponte da Automação SAP')
    p.add_argument('--portal', help='Endereço do portal (padrão: o configurado)')
    p.add_argument('--porta', type=int, default=PORTA)
    p.add_argument('--config', default=os.path.join(pasta_da_ponte(), 'ponte.json'))
    p.add_argument('--dev', action='store_true', help='Aceita páginas de localhost (teste)')
    p.add_argument('--sem-portal', action='store_true', help='Não confere a sessão no portal (teste)')
    p.add_argument('--comando', nargs='+', help='Teste: outro programa no lugar do AutomacaoSAP.exe')
    p.add_argument('--sem-ninguem', type=float, default=SEM_NINGUEM_SEGUNDOS,
                   help='Segundos sem o portal até o app voltar para a área de trabalho')
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', handlers=[
        RotatingFileHandler(os.path.join(pasta_da_ponte(), 'ponte.log'), maxBytes=500_000, backupCount=1, encoding='utf-8'),
        *([logging.StreamHandler()] if sys.stdout else [])])
    config = Config(a.config, a.portal, a.dev)
    config.sem_portal = a.sem_portal
    app = Aplicativo(config, a.comando)
    try:
        asyncio.run(principal(config, app, a.porta, a.sem_ninguem))
    except OSError as erro:
        # Porta ocupada: outra ponte já está rodando neste computador — tudo certo.
        log.info('Ponte não iniciada: %s', erro)


if __name__ == '__main__':
    main()
