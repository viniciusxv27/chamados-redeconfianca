"""Acha o gravador na rede da loja sem ninguém digitar IP, caminho RTSP ou número de câmeras.

Três procuras, da mais certeira para a mais bruta: a busca do próprio gravador (DHIP, porta 37810,
usada por Intelbras e Dahua e que traz o serial), o WS-Discovery do ONVIF, e a porta 554 na faixa da
rede. A senha nunca viaja aberta: quem só aceita Basic fica de fora, porque aqui se fala com todo
aparelho que responder na rede. Só biblioteca padrão, para funcionar antes de instalar qualquer coisa.
"""
import hashlib
import ipaddress
import json
import re
import socket
import struct
import uuid
from concurrent.futures import ThreadPoolExecutor

PORTA_RTSP = 554
MAX_CANAIS = 32
TEMPO = 2.0
# O padrão de Intelbras/Dahua primeiro. Os outros cobrem gravadores de outras famílias, como o Motorola.
CAMINHOS = (
    '/cam/realmonitor?channel={canal}&subtype=1',
    '/Streaming/Channels/{canal}02',
    '/h264/ch{canal}/sub/av_stream',
    '/chID={canal}&streamType=sub',
    '/live/ch{canal}_1',
    '/stream{canal}',
)


def _dhip_pacote():
    corpo = json.dumps({'method': 'DHDiscover.search', 'params': {'mac': '', 'uni': 1}}).encode()
    return struct.pack('<I', 0x20) + b'DHIP' + struct.pack('<IIIIII', 0, 0, len(corpo), 0, len(corpo), 0) + corpo


def ler_resposta_dhip(dados):
    """(ip, serial, modelo) de uma resposta da busca, ou None se não for uma resposta válida."""
    inicio = dados.find(b'{')
    if inicio < 0:
        return None
    try:
        info = json.loads(dados[inicio:].split(b'\x00')[0].decode('utf-8', 'ignore'))['params']['deviceInfo']
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(info, dict):
        return None
    ip = (info.get('IPv4Address') or {}).get('IPAddress') if isinstance(info.get('IPv4Address'), dict) else None
    return (ip, str(info.get('SerialNo') or ''), str(info.get('DeviceType') or '')) if ip else None


def _ouvir(sock, segundos, ao_receber):
    sock.settimeout(segundos)
    try:
        while True:
            dados, origem = sock.recvfrom(65535)
            ao_receber(dados, origem)
    except OSError:
        pass
    finally:
        sock.close()


def buscar_dhip(segundos=TEMPO):
    achados = {}

    def receber(dados, _origem):
        lido = ler_resposta_dhip(dados)
        if lido:
            achados[lido[0]] = {'serial': lido[1], 'modelo': lido[2]}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind(('', 0))
        s.sendto(_dhip_pacote(), ('239.255.255.251', 37810))
        s.sendto(_dhip_pacote(), ('255.255.255.255', 37810))
    except OSError:
        return achados
    _ouvir(s, segundos, receber)
    return achados


def ler_resposta_onvif(dados):
    return re.findall(r'https?://(\d+\.\d+\.\d+\.\d+)', dados.decode('utf-8', 'ignore'))


def buscar_onvif(segundos=TEMPO):
    achados = set()
    pedido = ('<?xml version="1.0"?><e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
              'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
              'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
              'xmlns:dn="http://www.onvif.org/ver10/network/wsdl"><e:Header>'
              f'<w:MessageID>uuid:{uuid.uuid4()}</w:MessageID>'
              '<w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
              '<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action></e:Header>'
              '<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body></e:Envelope>').encode()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(pedido, ('239.255.255.250', 3702))
    except OSError:
        return achados
    _ouvir(s, segundos, lambda dados, _o: achados.update(ler_resposta_onvif(dados)))
    return achados


def faixa_local():
    """As /24 onde este computador está. A rota padrão não manda pacote: o UDP só escolhe a interface."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
        s.close()
    except OSError:
        return []
    rede = ipaddress.ip_network(f'{ip}/24', strict=False)
    return [str(h) for h in rede.hosts() if str(h) != ip] if rede.is_private else []


def porta_aberta(ip, porta=PORTA_RTSP, tempo=0.4):
    try:
        with socket.create_connection((ip, porta), timeout=tempo):
            return True
    except OSError:
        return False


def varrer_porta(ips, porta=PORTA_RTSP):
    with ThreadPoolExecutor(max_workers=64) as grupo:
        return [ip for ip, aberta in zip(ips, grupo.map(lambda i: porta_aberta(i, porta), ips)) if aberta]


def _md5(texto):
    return hashlib.md5(texto.encode()).hexdigest()


def _pedido_rtsp(uri, cseq, autorizacao=''):
    extra = f'Authorization: {autorizacao}\r\n' if autorizacao else ''
    return f'DESCRIBE {uri} RTSP/1.0\r\nCSeq: {cseq}\r\nAccept: application/sdp\r\nUser-Agent: agente-cameras\r\n{extra}\r\n'.encode()


def _ler_cabecalho(sock):
    dados = b''
    while b'\r\n\r\n' not in dados and len(dados) < 16384:
        parte = sock.recv(4096)
        if not parte:
            break
        dados += parte
    return dados.decode('latin-1')


def _status(texto):
    achado = re.match(r'RTSP/1\.\d (\d{3})', texto)
    return int(achado.group(1)) if achado else 0


def testar_rtsp(ip, porta, caminho, usuario, senha, tempo=TEMPO):
    """200 abre, 401 senha recusada, 404 caminho errado, 0 não fala RTSP. -1: só aceita senha aberta (Basic)."""
    uri = f'rtsp://{ip}:{porta}{caminho}'
    try:
        with socket.create_connection((ip, porta), timeout=tempo) as sock:
            sock.settimeout(tempo)
            sock.sendall(_pedido_rtsp(uri, 1))
            resposta = _ler_cabecalho(sock)
            status = _status(resposta)
            if status != 401:
                return status
            digest = re.search(r'WWW-Authenticate:\s*Digest\s+([^\r\n]+)', resposta, flags=re.I)
            if not digest:
                return -1
            campos = dict(re.findall(r'(\w+)="?([^",]+)"?', digest.group(1)))
            ha1 = _md5(f"{usuario}:{campos.get('realm', '')}:{senha}")
            ha2 = _md5(f'DESCRIBE:{uri}')
            assinatura = _md5(f"{ha1}:{campos.get('nonce', '')}:{ha2}")
            autorizacao = (f'Digest username="{usuario}", realm="{campos.get("realm", "")}", '
                           f'nonce="{campos.get("nonce", "")}", uri="{uri}", response="{assinatura}"')
            sock.sendall(_pedido_rtsp(uri, 2, autorizacao))
            return _status(_ler_cabecalho(sock))
    except OSError:
        return 0


def contar_canais(ip, porta, caminho, usuario, senha, maximo=MAX_CANAIS):
    canais = []
    falhas = 0
    for numero in range(1, maximo + 1):
        if testar_rtsp(ip, porta, caminho.replace('{canal}', str(numero)), usuario, senha) == 200:
            canais.append(numero)
            falhas = 0
        else:
            falhas += 1
            if falhas >= 2:
                break
    return canais


def _candidatos(serial_esperado, ips_extra=()):
    """IPs em ordem de confiança, com o que se sabe de cada um. O serial igual ao do cadastro vem primeiro."""
    dhip = buscar_dhip()
    onvif = buscar_onvif()
    ordem = sorted(dhip, key=lambda ip: dhip[ip]['serial'].upper() != (serial_esperado or '').upper())
    ordem += [ip for ip in sorted(onvif) if ip not in ordem]
    ordem += [ip for ip in ips_extra if ip not in ordem]
    conhecidos = set(ordem)
    ordem += [ip for ip in varrer_porta(faixa_local()) if ip not in conhecidos]
    return ordem, dhip


def descobrir(usuario, senha, serial_esperado='', caminho_atual='', ips_extra=(), porta=PORTA_RTSP):
    """{'ip', 'porta', 'caminho', 'canais', 'serial', 'modelo'} ou {'erro': texto para a tela do portal}."""
    candidatos, dhip = _candidatos(serial_esperado, ips_extra)
    if not candidatos:
        return {'erro': 'Nenhum gravador encontrado: o computador precisa estar na mesma rede do gravador.'}
    caminhos = list(dict.fromkeys([c for c in (caminho_atual,) if c] + list(CAMINHOS)))
    recusou, so_basic = [], []
    for ip in candidatos:
        for caminho in caminhos:
            status = testar_rtsp(ip, porta, caminho.replace('{canal}', '1'), usuario, senha)
            if status == 200:
                info = dhip.get(ip, {})
                return {'ip': ip, 'porta': porta, 'caminho': caminho,
                        'canais': contar_canais(ip, porta, caminho, usuario, senha) or [1],
                        'serial': info.get('serial', ''), 'modelo': info.get('modelo', '')}
            if status == 401:
                recusou.append(ip)
                break
            if status == -1:
                so_basic.append(ip)
                break
    if recusou:
        return {'erro': f'O gravador em {recusou[0]} recusou o usuário e a senha do cadastro.'}
    if so_basic:
        return {'erro': f'O aparelho em {so_basic[0]} só aceita senha sem proteção; preencha o IP à mão se for o gravador.'}
    return {'erro': f'{len(candidatos)} aparelho(s) na rede, nenhum abriu o vídeo. Confira se o RTSP está ligado no gravador.'}
