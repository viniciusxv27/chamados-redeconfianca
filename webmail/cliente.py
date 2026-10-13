"""Conversa com o servidor de e-mail (poste.io): IMAP para ler, SMTP para enviar.

Abrir uma conexão IMAP custa um handshake TLS e um login (~0,5 s). Como cada
clique na caixa vira uma chamada, a conexão de cada pessoa fica aberta por
alguns minutos e é reaproveitada (``_SESSOES``), uma por processo. Uma trava
por sessão serializa as chamadas da mesma pessoa — o IMAP é uma conversa só.

Tudo que vai ao servidor passa por ``executar(conta, funcao)``: se a conexão
caiu no meio (servidor reiniciou, NAT esqueceu), ela é reaberta e a função
roda mais uma vez.
"""
import base64
import email as email_lib
import imaplib
import logging
import re
import smtplib
import socket
import ssl
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone as dt_timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesHeaderParser
from email.utils import format_datetime, formataddr, make_msgid, parsedate_to_datetime

from django.conf import settings
from django.utils import timezone

from . import conteudo
from .imap_parser import (chave_body, partes_da_estrutura, pastas_do_list, respostas_fetch,
                          utf7_decodificar, valores)
from .seguranca import (CertificadoNaoConfere, conferir_pino, contexto_pinado,
                        contexto_verificado)

logger = logging.getLogger(__name__)

TIMEOUT = 20
OCIOSA = 240            # segundos sem uso até a conexão ser fechada
CONFERIR_APOS = 25      # sem uso há mais que isso, um NOOP confirma que está viva
POR_PAGINA = 50
LIMITE_INLINE = 4 * 1024 * 1024      # imagens cid: embutidas na leitura
LIMITE_ENVIO = 25 * 1024 * 1024      # anexos de um envio


class ErroEmail(Exception):
    status = 400


class SenhaRecusada(ErroEmail):
    status = 401


class ServidorFora(ErroEmail):
    status = 503


class NaoEncontrada(ErroEmail):
    status = 404


def _host_imap():
    return settings.WEBMAIL_IMAP_HOST, settings.WEBMAIL_IMAP_PORT


def _host_smtp():
    return settings.WEBMAIL_SMTP_HOST, settings.WEBMAIL_SMTP_PORT


# ── Conexão ───────────────────────────────────────────────────────────────

_USA_PINO = {}      # host → instante em que a verificação normal falhou


def _abrir_tls(abrir_verificado, abrir_pinado, sock_de, host):
    """Tenta o certificado válido; se não houver, só aceita o pinado."""
    pinado_desde = _USA_PINO.get(host)
    if not pinado_desde or time.time() - pinado_desde > 3600:
        try:
            con = abrir_verificado(contexto_verificado())
            _USA_PINO.pop(host, None)
            return con
        except ssl.SSLCertVerificationError:
            _USA_PINO[host] = time.time()
        except (OSError, socket.timeout) as exc:
            raise ServidorFora(f'Servidor de e-mail fora do ar ({exc.__class__.__name__}).') from exc
    ctx = contexto_pinado()
    if ctx is None:
        raise ServidorFora('O certificado do servidor de e-mail não é válido e o portal não tem '
                           'a impressão digital dele (WEBMAIL_TLS_SHA256).')
    try:
        con = abrir_pinado(ctx)
    except (OSError, socket.timeout) as exc:
        raise ServidorFora(f'Servidor de e-mail fora do ar ({exc.__class__.__name__}).') from exc
    try:
        conferir_pino(sock_de(con))
    except CertificadoNaoConfere as exc:
        _USA_PINO.pop(host, None)          # pode ter ganho o certificado válido
        try:
            sock_de(con).close()
        except OSError:
            pass
        raise ServidorFora(str(exc)) from exc
    return con


def _conectar_imap():
    host, porta = _host_imap()
    return _abrir_tls(
        lambda ctx: imaplib.IMAP4_SSL(host, porta, ssl_context=ctx, timeout=TIMEOUT),
        lambda ctx: imaplib.IMAP4_SSL(host, porta, ssl_context=ctx, timeout=TIMEOUT),
        lambda con: con.sock, host)


def _conectar_smtp():
    host, porta = _host_smtp()
    return _abrir_tls(
        lambda ctx: smtplib.SMTP_SSL(host, porta, context=ctx, timeout=TIMEOUT),
        lambda ctx: smtplib.SMTP_SSL(host, porta, context=ctx, timeout=TIMEOUT),
        lambda con: con.sock, host)


def _entrar_imap(con, email, senha):
    try:
        if 'AUTH=PLAIN' in con.capabilities:
            # PLAIN em UTF-8: o LOGIN do imaplib só aceita senha ASCII.
            con.authenticate('PLAIN', lambda _: f'\0{email}\0{senha}'.encode('utf-8'))
        else:
            con.login(email, senha)
    except imaplib.IMAP4.error as exc:
        raise SenhaRecusada('O servidor recusou o e-mail ou a senha.') from exc


def testar_login(email, senha):
    """Confere a credencial sem guardar nada. Usado na tela de entrar."""
    con = _conectar_imap()
    try:
        _entrar_imap(con, email, senha)
    finally:
        try:
            con.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def _q(nome):
    """Nome de pasta entre aspas, como o IMAP pede (o imaplib não põe)."""
    return '"' + nome.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _uids(lista):
    saida = []
    for u in lista or []:
        u = str(u).strip()
        if u.isdigit():
            saida.append(u)
    if not saida:
        raise ErroEmail('Nenhuma mensagem selecionada.')
    return ','.join(saida)


class Sessao:
    def __init__(self, conta):
        self.chave = (conta.pk, conta.credencial_em)
        self.email = conta.email
        self._senha = conta.senha()
        self.con = None
        self.pasta = None
        self.caps = set()
        self.usada = 0.0
        self.trava = threading.RLock()
        self._pastas = None
        self._pastas_em = 0.0

    def abrir(self):
        if not self._senha:
            raise SenhaRecusada('A senha guardada não pôde ser lida. Entre de novo.')
        con = _conectar_imap()
        _entrar_imap(con, self.email, self._senha)
        try:
            typ, dados = con.capability()
            self.caps = set((dados[0] or b'').decode().upper().split()) if typ == 'OK' else set()
        except imaplib.IMAP4.error:
            self.caps = set()
        self.con, self.pasta, self.usada = con, None, time.time()

    def fechar(self):
        con, self.con, self.pasta = self.con, None, None
        if con is not None:
            try:
                con.shutdown()
            except OSError:
                pass

    def garantir(self):
        if self.con is None:
            self.abrir()
            return
        if time.time() - self.usada > CONFERIR_APOS:
            try:
                self.con.noop()
            except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError):
                self.fechar()
                self.abrir()

    def selecionar(self, pasta, forcar=False):
        if self.pasta == pasta and not forcar:
            return
        typ, dados = self.con.select(_q(pasta))
        if typ != 'OK':
            self.pasta = None
            raise NaoEncontrada('Pasta não encontrada.')
        self.pasta = pasta

    def uid(self, comando, *args):
        typ, dados = self.con.uid(comando, *args)
        if typ != 'OK':
            raise ErroEmail(f'O servidor recusou {comando}: {(dados or [b""])[0]!r}')
        return dados


_SESSOES = {}
_TRAVA = threading.Lock()


def _sessao(conta):
    agora = time.time()
    with _TRAVA:
        for chave, s in list(_SESSOES.items()):
            if agora - s.usada > OCIOSA and s.trava.acquire(blocking=False):
                try:
                    s.fechar()
                    _SESSOES.pop(chave, None)
                finally:
                    s.trava.release()
        s = _SESSOES.get(conta.pk)
        if s is None or s.chave != (conta.pk, conta.credencial_em):
            if s is not None:
                s.fechar()
            s = Sessao(conta)
            _SESSOES[conta.pk] = s
        return s


def esquecer(conta_pk):
    with _TRAVA:
        s = _SESSOES.pop(conta_pk, None)
    if s is not None:
        s.fechar()


def executar(conta, funcao):
    """Roda ``funcao(sessao)`` com a conexão viva; reabre uma vez se ela cair."""
    s = _sessao(conta)
    with s.trava:
        for tentativa in (1, 2):
            try:
                s.garantir()
                return funcao(s)
            except (imaplib.IMAP4.abort, OSError, ssl.SSLError) as exc:
                s.fechar()
                if tentativa == 2:
                    raise ServidorFora('A conexão com o servidor de e-mail caiu.') from exc
            finally:
                s.usada = time.time()


# ── Pastas ────────────────────────────────────────────────────────────────

PAPEL_POR_FLAG = {'\\sent': 'enviados', '\\drafts': 'rascunhos', '\\trash': 'lixeira',
                  '\\junk': 'spam', '\\archive': 'arquivo'}
PAPEL_POR_NOME = {
    'sent': 'enviados', 'sent items': 'enviados', 'sent messages': 'enviados', 'enviados': 'enviados',
    'itens enviados': 'enviados', 'drafts': 'rascunhos', 'rascunhos': 'rascunhos',
    'trash': 'lixeira', 'deleted items': 'lixeira', 'deleted messages': 'lixeira', 'lixeira': 'lixeira',
    'itens excluídos': 'lixeira', 'junk': 'spam', 'spam': 'spam', 'junk e-mail': 'spam',
    'lixo eletrônico': 'spam', 'archive': 'arquivo', 'arquivo': 'arquivo', 'arquivos': 'arquivo',
}
ROTULO_PAPEL = {'entrada': 'Caixa de entrada', 'enviados': 'Enviados', 'rascunhos': 'Rascunhos',
                'lixeira': 'Lixeira', 'spam': 'Spam', 'arquivo': 'Arquivo'}
ORDEM_PAPEL = ['entrada', 'rascunhos', 'enviados', 'arquivo', 'spam', 'lixeira']
NOME_PADRAO = {'enviados': 'Sent', 'rascunhos': 'Drafts', 'lixeira': 'Trash', 'spam': 'Junk',
               'arquivo': 'Archive'}


def _listar_pastas(s, forcar=False):
    if s._pastas is not None and not forcar and time.time() - s._pastas_em < 60:
        return s._pastas
    typ, dados = s.con.list('""', '"*"')
    if typ != 'OK':
        raise ErroEmail('Não consegui listar as pastas.')
    pastas = []
    vistos = set()
    for p in pastas_do_list(dados):
        if '\\noselect' in p['flags'] or '\\nonexistent' in p['flags']:
            continue
        nome = p['nome']
        legivel = utf7_decodificar(nome)
        folha = legivel.split(p['delim'])[-1] if p['delim'] else legivel
        nivel = legivel.count(p['delim']) if p['delim'] else 0
        papel = 'entrada' if nome.upper() == 'INBOX' else None
        if not papel:
            papel = next((PAPEL_POR_FLAG[f] for f in p['flags'] if f in PAPEL_POR_FLAG), None)
        if not papel and nivel == 0:
            papel = PAPEL_POR_NOME.get(folha.strip().lower())
        if papel in vistos and papel != 'entrada':
            papel = None                     # dois "Enviados": o segundo é pasta comum
        if papel:
            vistos.add(papel)
        pastas.append({
            'id': nome,
            'nome': ROTULO_PAPEL.get(papel) or folha,
            'caminho': legivel,
            'nivel': nivel,
            'papel': papel or '',
        })

    def ordem(p):
        if p['papel'] in ORDEM_PAPEL:
            return (ORDEM_PAPEL.index(p['papel']), '')
        return (len(ORDEM_PAPEL), p['caminho'].lower())
    pastas.sort(key=ordem)
    s._pastas, s._pastas_em = pastas, time.time()
    return pastas


def pasta_do_papel(s, papel, criar=True):
    for p in _listar_pastas(s):
        if p['papel'] == papel:
            return p['id']
    if not criar:
        return None
    nome = NOME_PADRAO[papel]
    s.con.create(_q(nome))
    try:
        s.con.subscribe(_q(nome))
    except imaplib.IMAP4.error:
        pass
    _listar_pastas(s, forcar=True)
    return nome


def _status(s, nome, itens='(MESSAGES UNSEEN)'):
    typ, dados = s.con.status(_q(nome), itens)
    if typ != 'OK' or not dados:
        return {}
    vals = valores(dados)
    lista = next((v for v in vals if isinstance(v, list)), [])
    saida = {}
    for i in range(0, len(lista) - 1, 2):
        try:
            saida[str(lista[i]).upper()] = int(lista[i + 1])
        except (TypeError, ValueError):
            pass
    return saida


def pastas(s):
    saida = []
    for p in _listar_pastas(s):
        st = _status(s, p['id'])
        saida.append({**p, 'total': st.get('MESSAGES', 0), 'nao_lidas': st.get('UNSEEN', 0)})
    return saida


def criar_pasta(s, nome):
    from .imap_parser import utf7_codificar
    nome = (nome or '').strip().replace('"', '')[:80]
    if not nome:
        raise ErroEmail('Dê um nome à pasta.')
    bruto = utf7_codificar(nome)
    typ, _ = s.con.create(_q(bruto))
    if typ != 'OK':
        raise ErroEmail('O servidor não criou a pasta (o nome já existe?).')
    try:
        s.con.subscribe(_q(bruto))
    except imaplib.IMAP4.error:
        pass
    _listar_pastas(s, forcar=True)
    return bruto


# ── Lista de mensagens ────────────────────────────────────────────────────

_MESES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def _data_imap(d):
    return f'{d.day}-{_MESES[d.month - 1]}-{d.year}'


def _internaldate(texto):
    m = re.match(r'\s*(\d{1,2})-(\w{3})-(\d{4}) (\d\d):(\d\d):(\d\d) ([+-])(\d\d)(\d\d)', texto or '')
    if not m:
        return None
    dia, mes, ano, h, mi, se, sinal, oh, om = m.groups()
    try:
        tz = dt_timezone((1 if sinal == '+' else -1) * timedelta(hours=int(oh), minutes=int(om)))
        return datetime(int(ano), _MESES.index(mes.title()) + 1, int(dia), int(h), int(mi), int(se), tzinfo=tz)
    except ValueError:
        return None


def _data_da_mensagem(cab, interna):
    data = None
    if cab.get('Date'):
        try:
            data = parsedate_to_datetime(cab.get('Date'))
            if data.tzinfo is None:
                data = data.replace(tzinfo=dt_timezone.utc)
        except (TypeError, ValueError, IndexError):
            data = None
    return data or _internaldate(interna)


def _tem_anexo(estrutura):
    for p in partes_da_estrutura(estrutura):
        if p['disposicao'] == 'attachment':
            return True
        if p['nome'] and p['tipo'] not in ('text',) and p['disposicao'] != 'inline':
            return True
        if p['tipo'] == 'message':
            return True
    return False


def _previa(dados):
    valor = dados.get('PREVIEW')
    if valor is None and isinstance(dados.get('SNIPPET'), list) and len(dados['SNIPPET']) > 1:
        valor = dados['SNIPPET'][1]
    if isinstance(valor, bytes):
        valor = valor.decode('utf-8', 'replace')
    return re.sub(r'\s+', ' ', valor or '').strip()[:200]


def _itens_previa(s):
    if 'PREVIEW' in s.caps:
        return ' PREVIEW'
    if 'SNIPPET=FUZZY' in s.caps:
        return ' SNIPPET (FUZZY)'
    return ''


def _resumos(s, uids):
    """Cabeçalho e marcas de cada UID (sem baixar o corpo)."""
    if not uids:
        return []
    itens = ('(UID FLAGS INTERNALDATE RFC822.SIZE BODYSTRUCTURE '
             'BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE)]' + _itens_previa(s) + ')')
    dados = s.uid('FETCH', ','.join(uids), itens)
    parser = BytesHeaderParser()
    por_uid = {}
    for r in respostas_fetch(dados):
        uid = str(r.get('UID') or '')
        if not uid:
            continue
        cab = parser.parsebytes(chave_body(r) or b'')
        flags = {str(f).lower() for f in (r.get('FLAGS') or [])}
        data = _data_da_mensagem(cab, r.get('INTERNALDATE'))
        de = conteudo.enderecos(cab.get('From'))
        por_uid[uid] = {
            'uid': int(uid),
            'de': de[0] if de else {'nome': '', 'email': ''},
            'para': conteudo.enderecos(cab.get('To')),
            'assunto': conteudo.cabecalho(cab.get('Subject')) or '(sem assunto)',
            'data': data.isoformat() if data else None,
            'lida': '\\seen' in flags,
            'sinalizada': '\\flagged' in flags,
            'respondida': '\\answered' in flags,
            'rascunho': '\\draft' in flags,
            'tamanho': int(r.get('RFC822.SIZE') or 0),
            'anexo': _tem_anexo(r.get('BODYSTRUCTURE')),
            'previa': _previa(r),
        }
    return [por_uid[u] for u in uids if u in por_uid]


def _buscar(s, criterios, busca=''):
    """UIDs que batem, do mais recente para o mais antigo.

    Com SORT (o Dovecot tem), a ordem é a da chegada na caixa: uma mensagem
    antiga movida de pasta ganha UID novo e, ordenando só por UID, subiria
    para o topo como se tivesse acabado de chegar.
    """
    criterios = list(criterios or [])
    if busca:
        s.con.literal = busca.encode('utf-8')
        criterios.append('TEXT')
    elif not criterios:
        criterios = ['ALL']
    if 'SORT' in s.caps:
        dados = s.uid('SORT', '(REVERSE ARRIVAL)', 'UTF-8', *criterios)
        vistos, ordem = set(), []
        for u in (dados[0] or b'').split():
            if u.isdigit() and int(u) not in vistos:
                vistos.add(int(u))
                ordem.append(int(u))
        return ordem
    if busca:
        dados = s.uid('SEARCH', 'CHARSET', 'UTF-8', *criterios)
    else:
        dados = s.uid('SEARCH', *criterios)
    return sorted({int(u) for u in (dados[0] or b'').split() if u.isdigit()}, reverse=True)


def listar(s, pasta, pagina=1, busca='', filtro=''):
    s.selecionar(pasta)
    criterios = []
    if filtro == 'nao_lidas':
        criterios.append('UNSEEN')
    elif filtro == 'sinalizadas':
        criterios.append('FLAGGED')
    elif filtro == 'anexos':
        criterios += ['HEADER', 'Content-Type', 'multipart/mixed']
    uids = _buscar(s, criterios, (busca or '').strip()[:200])
    total = len(uids)
    pagina = max(1, pagina)
    fatia = [str(u) for u in uids[(pagina - 1) * POR_PAGINA: pagina * POR_PAGINA]]
    return {
        'pasta': pasta,
        'total': total,
        'pagina': pagina,
        'paginas': max(1, -(-total // POR_PAGINA)),
        'mensagens': _resumos(s, fatia),
    }


# ── Leitura ───────────────────────────────────────────────────────────────

def _estrutura_e_cabecalho(s, uid):
    dados = s.uid('FETCH', uid, '(UID FLAGS INTERNALDATE BODYSTRUCTURE BODY.PEEK[HEADER])')
    for r in respostas_fetch(dados):
        if str(r.get('UID')) == str(uid):
            return r
    raise NaoEncontrada('Essa mensagem não está mais nesta pasta.')


def _baixar_secoes(s, uid, secoes):
    if not secoes:
        return {}
    itens = '(UID ' + ' '.join(f'BODY.PEEK[{sec}]' for sec in secoes) + ')'
    saida = {}
    for r in respostas_fetch(s.uid('FETCH', uid, itens)):
        for chave, valor in r.items():
            m = re.match(r'BODY\[([\d.]+)\]', chave)
            if m:
                saida[m.group(1)] = valor if isinstance(valor, bytes) else (valor or '').encode('utf-8')
    return saida


def _nome_anexo(p):
    nome = conteudo.cabecalho(p['nome']) if p['nome'] else ''
    if nome:
        return nome
    ext = {'pdf': '.pdf', 'jpeg': '.jpg', 'png': '.png', 'gif': '.gif', 'rfc822': '.eml',
           'zip': '.zip', 'plain': '.txt', 'html': '.html', 'calendar': '.ics'}.get(p['subtipo'], '')
    return f'anexo-{p["secao"]}{ext}'


def ler(s, pasta, uid, imagens=False):
    s.selecionar(pasta)
    uid = str(int(uid))
    r = _estrutura_e_cabecalho(s, uid)
    cab = BytesHeaderParser().parsebytes(chave_body(r) or b'')
    partes = partes_da_estrutura(r.get('BODYSTRUCTURE'))

    def texto(sub):
        return next((p for p in partes if p['tipo'] == 'text' and p['subtipo'] == sub
                     and p['disposicao'] != 'attachment'), None)
    parte_html, parte_txt = texto('html'), texto('plain')
    cids_usados = [p for p in partes if p['tipo'] == 'image' and p['id']]
    secoes = [p['secao'] for p in (parte_html, parte_txt) if p]
    soma = 0
    for p in cids_usados:
        if soma + p['tamanho'] <= LIMITE_INLINE:
            secoes.append(p['secao'])
            soma += p['tamanho']
    corpos = _baixar_secoes(s, uid, secoes)

    cids = {}
    for p in cids_usados:
        if p['secao'] in corpos:
            dado = conteudo.decodificar_parte(corpos[p['secao']], p['encoding'])
            cids[p['id']] = f'data:{p["mime"]};base64,{base64.b64encode(dado).decode()}'

    texto_puro = ''
    if parte_txt:
        texto_puro = conteudo.texto_da_parte(
            conteudo.decodificar_parte(corpos.get(parte_txt['secao']), parte_txt['encoding']),
            parte_txt['charset'])
    remota = False
    referenciados = set()
    if parte_html:
        fonte = conteudo.texto_da_parte(
            conteudo.decodificar_parte(corpos.get(parte_html['secao']), parte_html['encoding']),
            parte_html['charset'])
        referenciados = {c.strip('<>') for c in re.findall(r'cid:([^"\'\s>)]+)', fonte, re.I)}
        miolo, remota = conteudo.limpar_html(fonte, cids=cids, imagens=imagens)
        if not texto_puro:
            texto_puro = conteudo.html_para_texto(miolo)
    else:
        miolo = conteudo.texto_para_html(texto_puro)

    anexos = []
    for p in partes:
        if p in (parte_html, parte_txt):
            continue
        # Imagem que aparece dentro do corpo (cid:) não é anexo para quem lê.
        if p['id'] and p['id'] in referenciados and p['disposicao'] != 'attachment':
            continue
        if p['tipo'] == 'multipart':
            continue
        if p['tipo'] == 'text' and not p['nome'] and p['disposicao'] != 'attachment':
            continue
        anexos.append({'secao': p['secao'], 'nome': _nome_anexo(p), 'mime': p['mime'],
                       'tamanho': p['tamanho']})

    flags = {str(f).lower() for f in (r.get('FLAGS') or [])}
    if '\\seen' not in flags:
        try:
            s.uid('STORE', uid, '+FLAGS.SILENT', '(\\Seen)')
        except ErroEmail:
            pass
    data = _data_da_mensagem(cab, r.get('INTERNALDATE'))
    return {
        'uid': int(uid),
        'pasta': pasta,
        'assunto': conteudo.cabecalho(cab.get('Subject')) or '(sem assunto)',
        'de': (conteudo.enderecos(cab.get('From')) or [{'nome': '', 'email': ''}])[0],
        'para': conteudo.enderecos(cab.get('To')),
        'cc': conteudo.enderecos(cab.get('Cc')),
        'responder_para': conteudo.enderecos(cab.get('Reply-To')),
        'data': data.isoformat() if data else None,
        'message_id': (cab.get('Message-ID') or '').strip(),
        'references': ' '.join((cab.get('References') or '').split()),
        'sinalizada': '\\flagged' in flags,
        'rascunho': '\\draft' in flags,
        'era_nao_lida': '\\seen' not in flags,
        'documento': conteudo.documento_do_iframe(miolo, imagens=imagens),
        'citacao': miolo,
        'texto': texto_puro,
        'imagens_bloqueadas': bool(remota and not imagens),
        'anexos': anexos,
    }


def anexo(s, pasta, uid, secao):
    s.selecionar(pasta)
    uid = str(int(uid))
    if not re.fullmatch(r'\d+(\.\d+)*', secao or ''):
        raise NaoEncontrada('Anexo não encontrado.')
    r = _estrutura_e_cabecalho(s, uid)
    parte = next((p for p in partes_da_estrutura(r.get('BODYSTRUCTURE')) if p['secao'] == secao), None)
    if parte is None:
        raise NaoEncontrada('Anexo não encontrado.')
    bruto = _baixar_secoes(s, uid, [secao]).get(secao, b'')
    return _nome_anexo(parte), parte['mime'], conteudo.decodificar_parte(bruto, parte['encoding'])


def original(s, pasta, uid):
    s.selecionar(pasta)
    uid = str(int(uid))
    for r in respostas_fetch(s.uid('FETCH', uid, '(UID BODY.PEEK[])')):
        corpo = chave_body(r)
        if isinstance(corpo, bytes):
            return corpo
    raise NaoEncontrada('Essa mensagem não está mais nesta pasta.')


# ── Ações ─────────────────────────────────────────────────────────────────

def _apagar(s, uids):
    s.uid('STORE', uids, '+FLAGS.SILENT', '(\\Deleted)')
    if 'UIDPLUS' in s.caps:
        s.uid('EXPUNGE', uids)
    else:
        s.con.expunge()


def _mover(s, uids, destino):
    if 'MOVE' in s.caps:
        s.uid('MOVE', uids, _q(destino))
    else:
        s.uid('COPY', uids, _q(destino))
        _apagar(s, uids)


def acao(s, pasta, uids, qual, destino=''):
    s.selecionar(pasta)
    uids = _uids(uids)
    marcas = {'lida': ('+', '\\Seen'), 'nao_lida': ('-', '\\Seen'),
              'sinalizar': ('+', '\\Flagged'), 'tirar_sinal': ('-', '\\Flagged')}
    if qual in marcas:
        sinal, flag = marcas[qual]
        s.uid('STORE', uids, f'{sinal}FLAGS.SILENT', f'({flag})')
        return 'ok'
    if qual == 'mover':
        if not destino or destino == pasta:
            raise ErroEmail('Escolha outra pasta.')
        if destino not in {p['id'] for p in _listar_pastas(s)}:
            raise NaoEncontrada('Pasta de destino não encontrada.')
        _mover(s, uids, destino)
        return 'movida'
    if qual in ('lixeira', 'spam', 'arquivo'):
        papel = qual
        alvo = pasta_do_papel(s, papel)
        if alvo == pasta and qual == 'lixeira':
            _apagar(s, uids)
            return 'apagada'
        if alvo == pasta:
            return 'ok'
        _mover(s, uids, alvo)
        return 'movida'
    if qual == 'nao_spam':
        _mover(s, uids, 'INBOX')
        return 'movida'
    if qual == 'apagar':
        _apagar(s, uids)
        return 'apagada'
    raise ErroEmail('Ação desconhecida.')


# ── Visão geral e novidades ───────────────────────────────────────────────

def _quota(s):
    if 'QUOTA' not in s.caps:
        return None
    try:
        typ, dados = s.con.getquotaroot('INBOX')
    except imaplib.IMAP4.error:
        return None
    if typ != 'OK':
        return None
    for linha in (dados[1] if len(dados) > 1 else []) or []:
        m = re.search(rb'STORAGE (\d+) (\d+)', linha if isinstance(linha, bytes) else b'')
        if m:
            usado, limite = int(m.group(1)) * 1024, int(m.group(2)) * 1024
            return {'usado': usado, 'limite': limite,
                    'pct': round(usado * 100 / limite, 1) if limite else 0}
    return None


def resumo(s):
    s.selecionar('INBOX', forcar=True)
    hoje = timezone.localdate()
    nao_lidas = _buscar(s, ['UNSEEN'])
    recebidas_hoje = _buscar(s, ['SINCE', _data_imap(hoje)])
    sinalizadas = _buscar(s, ['FLAGGED'])
    total = _status(s, 'INBOX').get('MESSAGES', 0)

    inicio = hoje - timedelta(days=13)
    ultimos = _buscar(s, ['SINCE', _data_imap(inicio)])[:3000]
    por_dia = Counter()
    if ultimos:
        for r in respostas_fetch(s.uid('FETCH', ','.join(map(str, ultimos)), '(UID INTERNALDATE)')):
            d = _internaldate(r.get('INTERNALDATE'))
            if d:
                por_dia[timezone.localtime(d).date()] += 1
    serie = [{'dia': (inicio + timedelta(days=i)).isoformat(),
              'total': por_dia.get(inicio + timedelta(days=i), 0)} for i in range(14)]

    remetentes = Counter()
    nomes = {}
    mes = _buscar(s, ['SINCE', _data_imap(hoje - timedelta(days=30))])[:400]
    if mes:
        parser = BytesHeaderParser()
        dados = s.uid('FETCH', ','.join(map(str, mes)), '(UID BODY.PEEK[HEADER.FIELDS (FROM)])')
        for r in respostas_fetch(dados):
            for e in conteudo.enderecos(parser.parsebytes(chave_body(r) or b'').get('From')):
                chave = e['email'].lower()
                if chave:
                    remetentes[chave] += 1
                    nomes.setdefault(chave, e['nome'])
    return {
        'total': total,
        'nao_lidas': len(nao_lidas),
        'hoje': len(recebidas_hoje),
        'sinalizadas': len(sinalizadas),
        'serie': serie,
        'remetentes': [{'email': e, 'nome': nomes.get(e) or '', 'total': n}
                       for e, n in remetentes.most_common(6)],
        'recentes': _resumos(s, [str(u) for u in nao_lidas[:6]]),
        'quota': _quota(s),
    }


def novidades(s):
    st = _status(s, 'INBOX', '(MESSAGES UNSEEN UIDNEXT)')
    return {'uidnext': st.get('UIDNEXT', 0), 'nao_lidas': st.get('UNSEEN', 0),
            'total': st.get('MESSAGES', 0)}


# ── Escrever ──────────────────────────────────────────────────────────────

def _lista_enderecos(texto):
    from email.utils import getaddresses
    saida = []
    for nome, email in getaddresses([texto or '']):
        email = (email or '').strip()
        if not email:
            continue
        if not re.fullmatch(r'[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+', email):
            raise ErroEmail(f'Endereço inválido: {email}')
        saida.append((nome.strip(), email))
    return saida


def montar(conta, para='', cc='', cco='', assunto='', html_corpo='', anexos=(),
           responde_a='', referencias='', rascunho=False):
    """A mensagem pronta (sem Cco no cabeçalho de quem recebe)."""
    destinos = {'para': _lista_enderecos(para), 'cc': _lista_enderecos(cc),
                'cco': _lista_enderecos(cco)}
    if not rascunho and not (destinos['para'] or destinos['cc'] or destinos['cco']):
        raise ErroEmail('Informe pelo menos um destinatário.')
    total = sum(len(a[2]) for a in anexos)
    if total > LIMITE_ENVIO:
        raise ErroEmail(f'Os anexos passam de {LIMITE_ENVIO // (1024 * 1024)} MB.')

    msg = EmailMessage()
    msg['From'] = formataddr((conta.remetente, conta.email)) if conta.remetente else conta.email
    if destinos['para']:
        msg['To'] = ', '.join(formataddr(x) for x in destinos['para'])
    if destinos['cc']:
        msg['Cc'] = ', '.join(formataddr(x) for x in destinos['cc'])
    msg['Subject'] = (assunto or '').strip()
    msg['Date'] = format_datetime(timezone.localtime())
    dominio = conta.email.split('@')[-1]
    msg['Message-ID'] = make_msgid(domain=dominio)
    if responde_a:
        msg['In-Reply-To'] = responde_a.strip()
        msg['References'] = ' '.join(filter(None, [referencias.strip(), responde_a.strip()]))
    msg['X-Mailer'] = 'Portal Rede Confiança'

    html_corpo = conteudo.limpar_html(html_corpo or '', imagens=True)[0]
    msg.set_content(conteudo.html_para_texto(html_corpo) or ' ')
    msg.add_alternative(
        '<!doctype html><html><body style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        f'color:#1f2937">{html_corpo}</body></html>', subtype='html')
    for nome, mime, dado in anexos:
        principal, _, sub = (mime or 'application/octet-stream').partition('/')
        msg.add_attachment(dado, maintype=principal or 'application',
                           subtype=sub or 'octet-stream', filename=nome)
    return msg, destinos


def _appenduid(dados):
    for linha in dados or []:
        m = re.search(rb'APPENDUID \d+ (\d+)', linha if isinstance(linha, bytes) else b'')
        if m:
            return int(m.group(1))
    return None


def _guardar(s, papel, msg, flags):
    pasta = pasta_do_papel(s, papel)
    typ, dados = s.con.append(_q(pasta), flags, imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    if typ != 'OK':
        raise ErroEmail('O servidor não guardou a cópia.')
    if s.pasta == pasta:
        s.pasta = None                     # a pasta aberta mudou: reabrir na próxima
    return pasta, _appenduid(dados)


def _smtp_entrar(con, email, senha):
    try:
        con.ehlo()
        try:
            con.login(email, senha)
        except UnicodeEncodeError:
            token = base64.b64encode(f'\0{email}\0{senha}'.encode('utf-8')).decode()
            codigo, resposta = con.docmd('AUTH', 'PLAIN ' + token)
            if codigo != 235:
                raise smtplib.SMTPAuthenticationError(codigo, resposta)
    except smtplib.SMTPAuthenticationError as exc:
        raise SenhaRecusada('O servidor de envio recusou o e-mail ou a senha.') from exc


def enviar(conta, msg, destinos):
    """Manda pelo SMTP. A cópia em Enviados é feita depois, pelo IMAP."""
    todos = [e for _n, e in destinos['para'] + destinos['cc'] + destinos['cco']]
    con = _conectar_smtp()
    try:
        _smtp_entrar(con, conta.email, conta.senha())
        recusados = con.send_message(msg, from_addr=conta.email, to_addrs=todos)
    except smtplib.SMTPRecipientsRefused as exc:
        raise ErroEmail('O servidor recusou: ' + ', '.join(exc.recipients)) from exc
    except smtplib.SMTPSenderRefused as exc:
        raise ErroEmail(f'O servidor recusou o remetente ({exc.smtp_code}).') from exc
    except smtplib.SMTPDataError as exc:
        raise ErroEmail(f'O servidor recusou a mensagem: {exc.smtp_error.decode(errors="replace")[:200]}') from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise ServidorFora(f'Falha no envio ({exc.__class__.__name__}).') from exc
    finally:
        try:
            con.quit()
        except (smtplib.SMTPException, OSError):
            pass
    return recusados or {}


def depois_do_envio(s, msg, destinos, rascunho_uid=None, respondida=None):
    """Cópia em Enviados (com o Cco), rascunho apagado e original marcado como respondido."""
    copia = msg
    if destinos['cco']:
        copia = email_lib.message_from_bytes(msg.as_bytes(), policy=policy.default)
        copia['Bcc'] = ', '.join(formataddr(x) for x in destinos['cco'])
    try:
        _guardar(s, 'enviados', copia, '(\\Seen)')
    except (ErroEmail, imaplib.IMAP4.error) as exc:
        logger.warning('E-mail enviado, mas a cópia em Enviados falhou: %s', exc)
    if rascunho_uid:
        try:
            s.selecionar(pasta_do_papel(s, 'rascunhos'), forcar=True)
            _apagar(s, str(int(rascunho_uid)))
        except (ErroEmail, ValueError, imaplib.IMAP4.error):
            pass
    if respondida:
        pasta, uid = respondida
        try:
            s.selecionar(pasta, forcar=True)
            s.uid('STORE', str(int(uid)), '+FLAGS.SILENT', '(\\Answered)')
        except (ErroEmail, ValueError, imaplib.IMAP4.error):
            pass


def salvar_rascunho(s, msg, rascunho_uid=None):
    pasta, novo = _guardar(s, 'rascunhos', msg, '(\\Draft \\Seen)')
    if rascunho_uid:
        try:
            s.selecionar(pasta, forcar=True)
            _apagar(s, str(int(rascunho_uid)))
        except (ErroEmail, ValueError, imaplib.IMAP4.error):
            pass
    return novo


def anexos_de(s, pasta, uid):
    """Os anexos de uma mensagem, para encaminhar junto."""
    s.selecionar(pasta)
    uid = str(int(uid))
    r = _estrutura_e_cabecalho(s, uid)
    partes = [p for p in partes_da_estrutura(r.get('BODYSTRUCTURE'))
              if p['disposicao'] == 'attachment' or (p['nome'] and p['tipo'] != 'text')]
    corpos = _baixar_secoes(s, uid, [p['secao'] for p in partes])
    return [(_nome_anexo(p), p['mime'], conteudo.decodificar_parte(corpos.get(p['secao']), p['encoding']))
            for p in partes if p['secao'] in corpos]
